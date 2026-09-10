#!/usr/bin/env python3
"""One-time (idempotent) migration into the Leads CRM store.

Brings in everything that lives outside the Google Sheet so the CRM is useful
from the first load:
  1. the pad's in-flight batch (xplor-pay, mcalvany, stacker, bolt-new, ground-news)
  2. today's intake candidates with the GTM/growth contacts chosen for them
  3. click activity from the attribution store, per lead

The sheet itself is imported through the running server (POST /api/import/sheet),
which also creates the six already-contacted leads and their touch history.
Safe to re-run: leads are matched by id and only missing fields are added.
"""
import csv
import json
import os
import re
import shutil
from datetime import datetime, timezone

CRM = '/home/boxed/nf-crm/data/crm.json'
PICKS = '/home/boxed/intake_contact_picks_2026-09-10.csv'
LEADS_CSV = '/srv/newsletterfit/reports/sponsor-outreach/sponsor-leads.csv'
ATTR = '/home/boxed/newsletterfit/attribution/attribution.json'
DRAFTS = '/home/boxed/resend-pad/data/drafts.json'
now = datetime.now(timezone.utc).isoformat()


def kebab(s):
    return re.sub(r'^-|-$', '', re.sub(r'[^a-z0-9]+', '-', str(s or '').lower()))


def role_of(title):
    t = (title or '').lower()
    if 'go-to-market' in t or re.search(r'\bgtm\b', t):
        return 'gtm'
    if 'growth' in t:
        return 'growth'
    if 'partnership' in t or 'business development' in t:
        return 'partnerships'
    if 'marketing' in t:
        return 'marketing'
    if any(w in t for w in ('founder', 'ceo', 'coo', 'owner')):
        return 'founder'
    return 'other'


def sponsor_rows():
    out = {}
    try:
        with open(LEADS_CSV, newline='') as f:
            for r in csv.DictReader(f):
                out[(r.get('sponsor') or '').strip().lower()] = r
    except Exception as e:
        print('  (sponsor export unreadable:', e, ')')
    return out


def load_store():
    try:
        with open(CRM) as f:
            s = json.load(f)
    except Exception:
        s = {'leads': [], 'activity': [], 'sync': {}}
    s.setdefault('leads', [])
    s.setdefault('activity', [])
    s.setdefault('sync', {})
    return s


def upsert(s, lead):
    cur = next((l for l in s['leads'] if l['id'] == lead['id']), None)
    if cur is None:
        s['leads'].append(lead)
        return 'added', lead
    changed = False
    for k, v in lead.items():
        if k in ('id', 'created_at'):
            continue
        if v in (None, '', [], {}):
            continue
        if not cur.get(k):
            cur[k] = v
            changed = True
    cur['updated_at'] = now
    return ('updated' if changed else 'kept'), cur


def main():
    os.makedirs(os.path.dirname(CRM), exist_ok=True)
    if os.path.exists(CRM):
        shutil.copyfile(CRM, CRM + '.bak-' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    s = load_store()
    sponsors = sponsor_rows()
    counts = {'added': 0, 'updated': 0, 'kept': 0}

    def add(lead, activity=None):
        act, obj = upsert(s, lead)
        counts[act] += 1
        if activity:
            for ev in activity:
                ev = dict(ev)
                if not any(a.get('lead_id') == obj['id'] and a.get('detail') == ev.get('detail') and a.get('kind') == ev.get('kind') for a in s['activity']):
                    s['activity'].append(dict(ev, ts=ev.get('ts') or now))
        return obj

    def corpus_fields(name):
        r = sponsors.get(name.lower())
        if not r:
            return {}
        return {
            'score': r.get('sponsorScore'),
            'quality': r.get('outreachQuality'),
            'placements': r.get('placements'),
            'subscriber_range': r.get('subscriberRange'),
            'sponsored_pubs': [x.strip() for x in (r.get('topSponsoredPublications') or '').replace(' and ', ', ').split(',') if x.strip()],
            'recommended_pubs': [x.strip() for x in (r.get('topRecommendedPublications') or '').split(';') if x.strip()],
            'angle': r.get('bestOutreachAngle'),
        }

    # ---- 1. the pad's in-flight batch -------------------------------------
    sent_ids = {}
    try:
        for line in open('/home/boxed/resend-pad/data/sent-drafts.jsonl'):
            line = line.strip()
            if not line:
                continue
            j = json.loads(line)
            sent_ids[(j.get('id') or kebab(j.get('company') or ''))] = j.get('sent_at') or j.get('created_at') or now
    except Exception:
        pass
    draft_ids = {}
    try:
        for d in json.load(open(DRAFTS)):
            draft_ids[d['id']] = d
    except Exception:
        pass

    batch = [
        ('xplor-pay', 'Xplor Pay', 'ankur.bhatt@xplorpay.com', '', ''),
        ('mcalvany', 'McAlvany Precious Metals', 'robert@mcalvany.com', 'Robert', ''),
        ('stacker', 'Stacker', 'dmouret@stacker.com', '', ''),
        ('bolt-new', 'Bolt.new', 'eric.simons@bolt.new', '', ''),
        ('ground-news', 'Ground News', 'courtney@ground.news', '', ''),
    ]
    for lid, company, email, cname, _ in batch:
        sent_at = sent_ids.get(lid)
        stage = 'first_email' if sent_at else 'leads'
        acts = []
        if sent_at:
            acts.append({'lead_id': lid, 'kind': 'email_out', 'subject': (draft_ids.get(lid, {}).get('subject') or 'First contact'),
                         'detail': 'sent via the pad', 'ts': sent_at, 'source': 'seed'})
        elif lid in draft_ids:
            acts.append({'lead_id': lid, 'kind': 'note', 'detail': 'draft ready in the Email Pad, not sent yet', 'source': 'seed'})
        add(dict({'id': lid, 'company': company, 'contact_email': email, 'contact_name': cname,
                  'contact_role': role_of(''), 'domain': email.split('@')[1] if '@' in email else '',
                  'stage': stage, 'source': 'pad_batch', 'campaign': 'sep2-2026',
                  'created_at': now, 'updated_at': now}, **corpus_fields(company)), acts)

    # ---- 2. today's intake candidates (GTM/growth picks) -------------------
    if os.path.exists(PICKS):
        with open(PICKS, newline='') as f:
            for r in csv.DictReader(f):
                company = (r.get('company') or '').strip()
                if not company:
                    continue
                lid = kebab(company)
                email = (r.get('email') or '').strip()
                contact = (r.get('chosen_contact') or '').strip()
                if not email or not contact:
                    add({'id': lid, 'company': company, 'stage': 'leads', 'source': 'intake',
                         'notes': [], 'created_at': now, 'updated_at': now,
                         'blocked': 'no contact found (Hunter has no people for this domain)'},
                        [{'lead_id': lid, 'kind': 'note', 'detail': 'candidate: no contact available yet - needs a manual/LinkedIn route', 'source': 'seed'}])
                    continue
                add(dict({'id': lid, 'company': company, 'contact_email': email, 'contact_name': contact,
                          'contact_title': r.get('title') or '', 'contact_role': role_of(r.get('title')),
                          'domain': email.split('@')[1] if '@' in email else '',
                          'stage': 'leads', 'source': 'intake', 'created_at': now, 'updated_at': now,
                          'pick_basis': r.get('rule_basis') or '', 'alternates': r.get('alternates') or ''},
                         **corpus_fields(company)),
                    [{'lead_id': lid, 'kind': 'note',
                      'detail': 'candidate with contact: %s (%s) - %s' % (contact, r.get('title') or '', r.get('rule_basis') or ''),
                      'source': 'seed'}])

    # ---- 3. click activity -------------------------------------------------
    clicks = 0
    try:
        ad = json.load(open(ATTR))
        alias = {'mcalvany-precious-metals': 'mcalvany'}
        for c in ad.get('clicks', []):
            lid = alias.get(c.get('lead_id'), c.get('lead_id'))
            if not lid or not any(l['id'] == lid for l in s['leads']):
                continue
            if not c.get('first_click_at'):
                continue          # minted token, never clicked
            s['activity'].append({'ts': c['first_click_at'], 'lead_id': lid, 'kind': 'click',
                                  'detail': 'clicked ' + (c.get('link_ref') or ''), 'source': 'attribution'})
            clicks += 1
    except Exception as e:
        print('  (attribution unreadable:', e, ')')

    with open(CRM, 'w') as f:
        json.dump(s, f, indent=1)
    print('wire-up complete')
    print('  leads added/updated/kept:', counts['added'], counts['updated'], counts['kept'])
    print('  total leads now:', len(s['leads']))
    print('  click events imported:', clicks)
    print('  lead ids:', ', '.join(sorted(l['id'] for l in s['leads'])))


if __name__ == '__main__':
    main()
