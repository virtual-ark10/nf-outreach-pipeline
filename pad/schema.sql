-- ============================================================================
--  nf-outreach-pipeline — SQLite schema (node:sqlite, DatabaseSync)
-- ============================================================================
--  Replaces the previous JSON stores:
--    leads/data/crm.json        -> leads, lead_stage_events, emails, replies, events
--    data/drafts.json           -> drafts
--    data/sent-drafts.jsonl     -> emails (outbound) + drafts.status='sent'
--    data/webhooks.jsonl        -> events + emails.status/status_at + replies
--
--  Adapted for NewsletterFIT, not copied from a generic CRM:
--    * dropped     phone            — this pipeline is email-only outbound; nothing
--                                     ever calls a sponsor. `domain` covers website
--                                     as an identifier and `website` keeps the URL.
--    * niche    -> industry         — NF classifies sponsors by the vertical their
--                                     sponsored publications cover, not by a niche label.
--    * quality  -> priority         — one ordering field, usable for triage.
--    * + campaign, sponsored_pubs, recommended_pubs, angle, subscriber_range
--                                   — the actual product signals: which batch this
--                                     lead came from, where the brand was SEEN
--                                     sponsoring (the proof), which lookalikes we
--                                     pitched, the pitch angle and audience size.
--    * + stage_at_send              — the stage snapshotted when the mail went out,
--                                     so history cannot rewrite itself.
--    * + direction                  — emails holds outbound AND inbound.
--    * stages stay NF's own vocabulary (see STAGES in leads/server.cjs):
--                                     leads -> first_email -> follow_up_1..4 ->
--                                     replied / qualified / won / no / archived
--      (the generic new/contacted/replied/qualified/won/lost/archived maps onto it
--       as new=leads, contacted=first_email, lost=no)
--    * converted is kept OUT of stage: a sponsor can be marked converted (a deal
--      closed) without rewriting where the pipeline thinks it is.
--
--  Rules of this file:
--    * STRICT tables — a bad type is an error, not a silent coercion.
--    * every timestamp is an ISO-8601 UTC string (TEXT), sortable as text.
--    * JSON lives in TEXT columns and is queryable with json_extract().
--    * no field is ever hard-deleted: deleted_at / archived_at / status.
-- ============================================================================

-- ---------------------------------------------------------------- leads
CREATE TABLE IF NOT EXISTS leads (
  id                 TEXT PRIMARY KEY,              -- slug, e.g. 'mcalvany' (derived from company)
  company            TEXT NOT NULL,
  domain             TEXT,                          -- the Hunter key: always resolve this before enrich
  website            TEXT,
  contact_name       TEXT,
  contact_title      TEXT,                          -- drives the targeting rule (GTM > growth > ...)
  contact_role       TEXT,                          -- gtm|growth|partnerships|marketing|cro|founder|other
  email              TEXT,                          -- primary address (API field: contact_email)
  emails             TEXT,                          -- JSON array: every address we know (API: extra_emails)
  industry           TEXT,
  city               TEXT,
  region             TEXT,
  country            TEXT,
  source             TEXT,                          -- pad_batch|intake|manual|import|backfill
  stage              TEXT NOT NULL DEFAULT 'leads',
  stage_changed_at   TEXT,
  priority           TEXT,                          -- high|medium|low
  score              INTEGER,                       -- 0-100 from the sponsor export
  owner              TEXT,
  tags               TEXT,                          -- JSON array
  notes              TEXT,
  campaign           TEXT,                          -- outreach batch, e.g. 'sep2-2026'
  sponsored_pubs     TEXT,                          -- JSON array — where we SAW them sponsor (the proof)
  recommended_pubs   TEXT,                          -- JSON array — the lookalikes we pitch
  angle              TEXT,                          -- the pitch angle for this lead
  subscriber_range   TEXT,                          -- audience size of their placement, e.g. '33K'
  meta               TEXT,                          -- JSON escape hatch — anything not worth a column yet
  converted          INTEGER NOT NULL DEFAULT 0,    -- 0/1, separate from stage
  converted_at       TEXT,
  value_cents        INTEGER,                       -- deal value
  currency           TEXT DEFAULT 'USD',
  unsubscribed       INTEGER NOT NULL DEFAULT 0,
  bounced            INTEGER NOT NULL DEFAULT 0,
  first_contact_at   TEXT,
  last_contact_at    TEXT,
  next_follow_up_at  TEXT,                          -- drives the touch cadence
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL,
  archived_at        TEXT,
  deleted_at         TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS ix_leads_stage      ON leads(stage);
CREATE INDEX IF NOT EXISTS ix_leads_domain     ON leads(domain);
CREATE INDEX IF NOT EXISTS ix_leads_email      ON leads(email);
CREATE INDEX IF NOT EXISTS ix_leads_campaign   ON leads(campaign);
CREATE INDEX IF NOT EXISTS ix_leads_next_fu    ON leads(next_follow_up_at);
CREATE INDEX IF NOT EXISTS ix_leads_live       ON leads(deleted_at, archived_at);

-- ------------------------------------------------- lead_stage_events
-- The stage HISTORY, not just the current stage. Every transition, whoever made
-- it (a send, the sync, the UI, a cron job) and why.
CREATE TABLE IF NOT EXISTS lead_stage_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_id     TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
  from_stage  TEXT,
  to_stage    TEXT NOT NULL,
  at          TEXT NOT NULL,
  by          TEXT,                                 -- actor: crm|sync|send|pad|intake|system
  note        TEXT,
  source      TEXT                                  -- mechanism that caused it
) STRICT;

CREATE INDEX IF NOT EXISTS ix_stage_events_lead ON lead_stage_events(lead_id, at);

-- ---------------------------------------------------------------- emails
-- Outbound and inbound, one row per message. stage_at_send is the stage the lead
-- was in when this went out, frozen — so moving the lead later never rewrites it.
CREATE TABLE IF NOT EXISTS emails (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_id         TEXT REFERENCES leads(id) ON DELETE SET NULL,
  direction       TEXT NOT NULL DEFAULT 'outbound', -- outbound|inbound
  stage_at_send   TEXT,
  thread_id       TEXT,                             -- groups a first email with its follow-ups
  parent_email_id INTEGER REFERENCES emails(id) ON DELETE SET NULL,
  in_reply_to     TEXT,                             -- RFC Message-ID this one answers
  from_addr       TEXT,
  to_addr         TEXT,                             -- comma-separated; one row per send
  cc              TEXT,
  bcc             TEXT,
  reply_to        TEXT,
  subject         TEXT,
  body_text       TEXT,
  body_html       TEXT,
  template_id     TEXT,
  campaign        TEXT,
  resend_id       TEXT UNIQUE,                      -- Resend's id — the dedupe key
  status          TEXT,                             -- queued|sent|delivered|bounced|complained|failed
  status_at       TEXT,
  error           TEXT,
  sent_at         TEXT,
  created_at      TEXT NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS ix_emails_lead    ON emails(lead_id, sent_at);
CREATE INDEX IF NOT EXISTS ix_emails_thread  ON emails(thread_id);
CREATE INDEX IF NOT EXISTS ix_emails_parent  ON emails(parent_email_id);
CREATE INDEX IF NOT EXISTS ix_emails_campaign ON emails(campaign);

-- ---------------------------------------------------------------- replies
CREATE TABLE IF NOT EXISTS replies (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_id        TEXT REFERENCES leads(id) ON DELETE SET NULL,
  email_id       INTEGER REFERENCES emails(id) ON DELETE SET NULL,
  from_addr      TEXT,
  to_addr        TEXT,
  subject        TEXT,
  body_text      TEXT,
  body_html      TEXT,
  message_id     TEXT,                              -- RFC Message-ID of the reply
  in_reply_to    TEXT,
  received_at    TEXT,
  classification TEXT,                              -- interested|not_now|not_interested|unsubscribe|auto|unknown
  sentiment      TEXT,                              -- positive|neutral|negative
  is_read        INTEGER NOT NULL DEFAULT 0,
  starred        INTEGER NOT NULL DEFAULT 0,
  deleted_at     TEXT,                              -- the ✕ — soft delete, never a row loss
  raw            TEXT,                              -- the raw Resend payload, verbatim
  created_at     TEXT NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS ix_replies_lead      ON replies(lead_id, received_at);
CREATE INDEX IF NOT EXISTS ix_replies_inbox     ON replies(deleted_at, is_read);
CREATE UNIQUE INDEX IF NOT EXISTS ux_replies_message ON replies(message_id) WHERE message_id IS NOT NULL;

-- ---------------------------------------------------------------- drafts
-- status: draft | sent | discarded. A sent draft is kept (with its resend_id and
-- the email row it produced) instead of being spliced out of a JSON array.
CREATE TABLE IF NOT EXISTS drafts (
  id           TEXT PRIMARY KEY,
  lead_id      TEXT REFERENCES leads(id) ON DELETE SET NULL,
  company      TEXT,
  from_addr    TEXT,
  to_addr      TEXT,                                -- comma-separated, as stored before
  cc           TEXT,
  reply_to     TEXT,
  subject      TEXT,
  body_text    TEXT,
  body_html    TEXT,
  status       TEXT NOT NULL DEFAULT 'draft',
  resend_id    TEXT,
  send_error   TEXT,
  campaign     TEXT,
  meta         TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT,
  sent_at      TEXT,
  discarded_at TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS ix_drafts_status ON drafts(status, created_at);
CREATE INDEX IF NOT EXISTS ix_drafts_lead   ON drafts(lead_id);

-- ---------------------------------------------------------------- events
-- Generic audit trail for everything that is not a stage transition or a message:
-- lead created, note added, draft edited/discarded, webhook received, click, sync.
CREATE TABLE IF NOT EXISTS events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  entity    TEXT NOT NULL,                          -- lead|draft|email|reply|webhook|system
  entity_id TEXT,
  type      TEXT NOT NULL,                          -- created|note|draft_sent|webhook|click|sync|...
  payload   TEXT,                                   -- JSON
  at        TEXT NOT NULL,
  actor     TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS ix_events_entity ON events(entity, entity_id, at);
CREATE INDEX IF NOT EXISTS ix_events_type   ON events(type, at);

-- ---------------------------------------------------------------- engagement
-- Opens and clicks, exactly as Resend reports them. Both are produced by the
-- tracking subdomain (analytics.newsletterfit.com, a CNAME to Resend's tracking
-- infrastructure): Resend rewrites every link in the HTML body through it and
-- embeds a 1x1 pixel, so the recipient's mail client is what fires the event —
-- there is nothing for this app to serve and nothing to fake.
--
-- One row per event, never a counter on its own: the counters are derived from
-- these rows, so a wrong number can always be traced back to an event. Resend's
-- webhook payload carries no event id, so `dedupe` is derived from the parts that
-- are stable across a retry (kind + message + click timestamp or event
-- created_at + link); the unique index turns a duplicate delivery into a no-op.
--
-- Deliberately separate from emails.status: an open is NOT a delivery state, and
-- letting one overwrite 'delivered' would collapse the funnel.
CREATE TABLE IF NOT EXISTS email_engagements (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  resend_id  TEXT,                                  -- emails.resend_id (may not be matched)
  email_id   INTEGER REFERENCES emails(id) ON DELETE SET NULL,
  lead_id    TEXT REFERENCES leads(id) ON DELETE SET NULL,
  kind       TEXT NOT NULL,                         -- open | click
  url        TEXT,                                  -- the clicked link; NULL for opens
  link_host  TEXT,                                  -- hostname, for grouping links
  user_agent TEXT,
  ip         TEXT,
  at         TEXT NOT NULL,                         -- when it happened (provider time)
  dedupe     TEXT,
  created_at TEXT NOT NULL
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS ux_eng_dedupe ON email_engagements(dedupe) WHERE dedupe IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_eng_resend ON email_engagements(resend_id);
CREATE INDEX IF NOT EXISTS ix_eng_lead   ON email_engagements(lead_id, at);
CREATE INDEX IF NOT EXISTS ix_eng_kind   ON email_engagements(kind, at);
CREATE INDEX IF NOT EXISTS ix_eng_host   ON email_engagements(kind, link_host);

-- ---------------------------------------------------------------- triggers
-- Safety net: stage_changed_at can never drift from stage, even for a hand-run
-- UPDATE in sqlite3. The application still writes the stage event itself, because
-- only it knows the actor and the note. Not recursive: the inner UPDATE does not
-- list `stage` in its SET clause.
CREATE TRIGGER IF NOT EXISTS trg_leads_stage_changed_at
AFTER UPDATE OF stage ON leads
WHEN new.stage IS NOT old.stage
BEGIN
  UPDATE leads SET stage_changed_at = new.updated_at WHERE id = new.id;
END;

-- Every lead starts with its own history: the transition into its first stage.
CREATE TRIGGER IF NOT EXISTS trg_leads_birth_stage_event
AFTER INSERT ON leads
BEGIN
  INSERT INTO lead_stage_events (lead_id, from_stage, to_stage, at, by, note, source)
  VALUES (new.id, NULL, new.stage, new.created_at, 'system', 'lead created', new.source);
  UPDATE leads SET stage_changed_at = new.created_at WHERE id = new.id;
END;

-- ---------------------------------------------------------------- views
-- Per lead: where it stands and how it is actually going.
DROP VIEW IF EXISTS v_lead_pipeline;
CREATE VIEW v_lead_pipeline AS
SELECT
  l.id,
  l.company,
  l.domain,
  l.stage,
  l.converted,
  l.priority,
  l.score,
  l.campaign,
  l.converted_at,
  l.value_cents,
  l.currency,
  (SELECT COUNT(*) FROM emails  e WHERE e.lead_id = l.id AND e.direction = 'outbound') AS emails_sent,
  (SELECT COUNT(*) FROM replies r WHERE r.lead_id = l.id AND r.deleted_at IS NULL)     AS replies,
  (SELECT MAX(r.received_at) FROM replies r WHERE r.lead_id = l.id AND r.deleted_at IS NULL) AS last_reply_at,
  l.first_contact_at,
  l.last_contact_at,
  l.next_follow_up_at,
  CASE WHEN l.last_contact_at IS NULL THEN NULL
       ELSE CAST(julianday('now') - julianday(l.last_contact_at) AS INTEGER) END AS days_since_contact,
  l.created_at,
  l.updated_at
FROM leads l
WHERE l.deleted_at IS NULL AND l.archived_at IS NULL;

-- One chronological stream: mail out, mail in, stage moves.
DROP VIEW IF EXISTS v_lead_timeline;
CREATE VIEW v_lead_timeline AS
  SELECT
    e.lead_id,
    'email'              AS kind,
    'email:' || e.id     AS ref,
    COALESCE(e.sent_at, e.created_at) AS at,
    e.subject            AS summary,
    e.direction || COALESCE(' [' || e.status || ']', '') || COALESCE(' @' || e.stage_at_send, '') AS detail
  FROM emails e
  WHERE e.lead_id IS NOT NULL
UNION ALL
  SELECT
    r.lead_id,
    'reply',
    'reply:' || r.id,
    COALESCE(r.received_at, r.created_at),
    r.subject,
    COALESCE(r.classification, 'unclassified') || COALESCE(' / ' || r.sentiment, '')
  FROM replies r
  WHERE r.lead_id IS NOT NULL AND r.deleted_at IS NULL
UNION ALL
  SELECT
    s.lead_id,
    'stage',
    'stage:' || s.id,
    s.at,
    COALESCE(s.from_stage, '(new)') || ' -> ' || s.to_stage,
    COALESCE(s.note, s.source, '') || COALESCE(' by ' || s.by, '')
  FROM lead_stage_events s
UNION ALL
  -- Opens and clicks belong on the timeline too: they are activity on the lead,
  -- from the track side rather than the mail side.
  SELECT
    g.lead_id,
    g.kind,
    'eng:' || g.id,
    g.at,
    COALESCE(g.link_host, 'email'),
    CASE WHEN g.kind = 'click' THEN 'clicked ' || COALESCE(g.url, 'a link')
         ELSE 'opened the email' END
  FROM email_engagements g
  WHERE g.lead_id IS NOT NULL;

-- NF-specific: who is due a touch right now (the day 3 / 7 / 14 cadence).
DROP VIEW IF EXISTS v_followups_due;
CREATE VIEW v_followups_due AS
SELECT
  p.*,
  CAST(julianday('now') - julianday(p.next_follow_up_at) AS INTEGER) AS days_overdue
FROM v_lead_pipeline p
WHERE p.next_follow_up_at IS NOT NULL
  AND p.next_follow_up_at <= strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
  AND p.stage NOT IN ('replied', 'won', 'no', 'archived', 'qualified');

-- ---------------------------------------------------------------- views: engagement
-- The three questions the Tracking tab asks, answered from the rows themselves
-- (never from a stored counter): how much engagement per day, which links earn
-- the clicks, and how a single lead is engaging.
DROP VIEW IF EXISTS v_engagement_daily;
CREATE VIEW v_engagement_daily AS
SELECT substr(at, 1, 10) AS day,
       kind,
       COUNT(*)                        AS n,
       COUNT(DISTINCT resend_id)       AS messages,
       COUNT(DISTINCT lead_id)         AS leads
FROM email_engagements
GROUP BY day, kind;

DROP VIEW IF EXISTS v_top_links;
CREATE VIEW v_top_links AS
SELECT COALESCE(link_host, '(unknown)') AS host,
       url,
       COUNT(*)                AS clicks,
       COUNT(DISTINCT lead_id) AS leads,
       MIN(at)                 AS first_at,
       MAX(at)                 AS last_at
FROM email_engagements
WHERE kind = 'click'
GROUP BY url
ORDER BY clicks DESC;

DROP VIEW IF EXISTS v_engagement_by_lead;
CREATE VIEW v_engagement_by_lead AS
SELECT lead_id,
       SUM(CASE WHEN kind = 'open'  THEN 1 ELSE 0 END) AS opens,
       SUM(CASE WHEN kind = 'click' THEN 1 ELSE 0 END) AS email_clicks,
       MIN(CASE WHEN kind = 'open'  THEN at END)       AS first_open_at,
       MAX(CASE WHEN kind = 'open'  THEN at END)       AS last_open_at,
       MIN(CASE WHEN kind = 'click' THEN at END)       AS first_click_at,
       MAX(CASE WHEN kind = 'click' THEN at END)       AS last_click_at
FROM email_engagements
WHERE lead_id IS NOT NULL
GROUP BY lead_id;

-- Per message: what the Sent tab shows beside a mail ("opened 3x, clicked once").
DROP VIEW IF EXISTS v_email_engagement;
CREATE VIEW v_email_engagement AS
SELECT resend_id,
       SUM(CASE WHEN kind = 'open'  THEN 1 ELSE 0 END) AS opens,
       SUM(CASE WHEN kind = 'click' THEN 1 ELSE 0 END) AS clicks,
       MIN(CASE WHEN kind = 'open'  THEN at END)       AS first_open_at,
       MAX(CASE WHEN kind = 'open'  THEN at END)       AS last_open_at,
       MIN(CASE WHEN kind = 'click' THEN at END)       AS first_click_at,
       MAX(CASE WHEN kind = 'click' THEN at END)       AS last_click_at
FROM email_engagements
WHERE resend_id IS NOT NULL
GROUP BY resend_id;
