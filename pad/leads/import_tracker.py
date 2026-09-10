#!/usr/bin/env python3
"""One-off migration: pull the old tracker rows into the CRM store.

This is the ONLY place the previous tracker format is understood. It is a
migration tool, not a sync: run it once (or again if a fresh copy of the old
export turns up) and then leave it alone. The CRM store is the source of truth
from here on.

Usage:
    python3 import_tracker.py                 # read the local export snapshot
    python3 import_tracker.py --csv path.csv  # read a specific CSV
    python3 import_tracker.py --url URL       # read a URL (CSV export)

Idempotent: leads are matched by company id and only missing fields are filled,
so re-running never duplicates or clobbers CRM edits.
"""
import argparse
import csv
import io
import json
import re
import shutil
import sys
import urllib.request
from datetime import datetime, timezone

STORE = '/home/boxed/nf-crm/data/crm.json'
DEFAULT_CSV = '/home/boxed/live_sheet.csv'
now = datetime.now(timezone.utc).isoformat()
EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')


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
    if re.search(r'founder|ceo|coo|owner|chief executive', t):
        return 'founder'
    return 'other'


def name_from_email(email):
    local = (email or '').split('@')[0]
    if not local or re.match(r'^(info|hello|contact|team|sales|admin|support|hi)$', local, re.I):
        return ''
    return ' '.join(p[:1].upper() + p[1:] for p in re.split(r'[._-]+', local) if p)


def load_rows(args):
    if args.url:
        with urllib.request.urlopen(args.url, timeout=30) as r:
            return r.read().decode('utf-8', 'replace')
    return open(args.csv or DEFAULT_CSV, encoding='utf-8').read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--csv')
    ap.add_argument('--url')
    args = ap.parse_args()

    try:
        text = load_rows(args)
    except Exception as e:
        sys.exit(f'could not read the export: {e}')

    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        sys.exit('no rows found')
    head = {h.strip().lower(): h for h in rows[0].keys()}

    def cell(r, name):
        col = head.get(name)
        return (r.get(col) or '').strip() if col else ''

    store = json.load(open(STORE))
    store.setdefault('leads', [])
    store.setdefault('activity', [])
    added, filled, skipped = [], [], 0

    for r in rows:
        company = cell(r, 'company name')
        found = EMAIL_RE.search(cell(r, 'contact emails'))
        email = found.group(0) if found else ''
        title = cell(r, 'title')
        first = cell(r, 'first email')
        fu1 = cell(r, 'follow-up 1')

        if not company and not email:
            skipped += 1
            continue
        if not company and email:                     # orphan contact row
            host = email.split('@')[1]
            for lead in store['leads']:
                if lead.get('domain') and host.endswith(lead['domain']):
                    if email.lower() not in [e.lower() for e in [lead.get('contact_email')] + (lead.get('extra_emails') or [])]:
                        lead.setdefault('extra_emails', []).append(email)
                        filled.append(f"{lead['company']} +{email}")
                    break
            continue

        lead = next((l for l in store['leads'] if l['id'] == kebab(company)), None)
        stage = 'follow_up_1' if fu1 else ('first_email' if first else 'leads')
        if lead is None:
            lead = {
                'id': kebab(company), 'company': company, 'domain': email.split('@')[1] if '@' in email else '',
                'contact_email': email, 'contact_name': name_from_email(email), 'contact_title': title,
                'contact_role': role_of(title), 'stage': stage, 'source': 'import',
                'notes': [], 'created_at': now, 'updated_at': now,
            }
            store['leads'].append(lead)
            added.append(company)
        else:
            changed = False
            for key, val in (('contact_email', email), ('contact_title', title)):
                if val and not lead.get(key):
                    lead[key] = val
                    changed = True
            if not lead.get('contact_name') and email:
                lead['contact_name'] = name_from_email(email)
                changed = True
            if title and lead.get('contact_role') in (None, '', 'other'):
                lead['contact_role'] = role_of(title)
                changed = True
            if stage != 'leads' and lead.get('stage') == 'leads':
                lead['stage'] = stage
                changed = True
            if changed:
                filled.append(company)
            lead['updated_at'] = now

        for label, subject in ((first, 'First Email'), (fu1, 'Follow-up 1')):
            if not label:
                continue
            already = any(a.get('lead_id') == lead['id'] and a.get('subject') == subject for a in store['activity'])
            if not already:
                store['activity'].append({
                    'ts': now, 'lead_id': lead['id'], 'kind': 'email_out', 'subject': subject,
                    'detail': 'carried over from the previous tracker (no send timestamp)', 'source': 'import',
                })

    shutil.copyfile(STORE, STORE + '.pre-import')
    json.dump(store, open(STORE, 'w'), indent=1)
    print('import complete')
    print('  new leads      :', len(added), (', '.join(added) if added else ''))
    print('  fields filled  :', len(filled), (', '.join(filled[:8]) if filled else ''))
    print('  blank rows skip:', skipped)
    print('  total leads    :', len(store['leads']))


if __name__ == '__main__':
    main()
