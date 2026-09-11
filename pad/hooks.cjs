'use strict';
// ============================================================================
//  hooks.cjs — the rule table: what happens AFTER something has already happened.
//
//  One table, shared by the pad and the leads engine, because they run one store:
//  whoever fires the event, the same rule reacts. Every rule gets
//  ({ event, payload, store }) and may add a note, set a flag, record a reason or
//  schedule the next touch. The store marks the event processed afterwards
//  (db.processEvents), claiming and applying in one transaction.
//
//  THE INVARIANT THAT KEEPS THIS SAFE: a rule may never move a stage.
//
//  The stage machine lives in pipeline.cjs. It is the single writer of `stage`, it
//  commits a transition together with its history row, it protects terminal stages,
//  and its sends are idempotent through emails.resend_id UNIQUE. The `store` facade
//  a rule receives has NO setStage — a rule cannot move a stage even by accident.
//
//  This is the one part of the upstream hooks.cjs that could not come across as it
//  was: the original called store.setStage (or advanceStage) from four rules, which
//  makes the rule table a SECOND writer of the stage machine. Two writers of one
//  stage machine means a send advances a lead twice, or a reply races a send for the
//  same row. So the shape came across (one row per reaction, drained from a queue,
//  durable, replayable) and the stage moves stayed where the transaction is.
//
//  Brand-agnostic: stage keys and labels come from the store and config, nothing
//  here knows a product name.
// ============================================================================

function buildRules(store) {
  // Two kinds of rule, and the difference matters for the timeline:
  //   note()   — this is NEW information, so it writes one line on the lead's story.
  //   report() — the fact is already recorded by the domain layer that caused it
  //              (pipeline.cjs writes the send, the reply row and the stage move in
  //              one transaction), so the rule only reports what it did. Writing a
  //              second line for it would double the timeline, not enrich it.
  const action = (leadId, detail, extra) =>
    leadId ? Object.assign({ lead_id: leadId, note: detail }, extra || {}) : null;
  const note = (leadId, detail, extra) => {
    if (!leadId) return null;
    store.logEvent({
      entity: 'lead', entity_id: leadId, type: 'rule',
      payload: Object.assign({ detail }, extra || {}), at: store.nowISO(), actor: 'hooks',
    });
    return { lead_id: leadId, note: detail };
  };
  const report = (leadId, detail, extra) => action(leadId, detail, extra);

  return {
    // A lead exists (discovery, form, manual add). Whoever created it set the stage;
    // this only makes sure the timeline starts with an entry of its own.
    'lead.created': ({ payload }) =>
      note(payload.leadId, 'lead in the pipeline', { source: payload.source || null }),

    // A draft was composed: nothing moves yet, but the lead's story shows the work in
    // progress, and the CRM can surface "draft sitting ready".
    'draft.created': ({ payload, store: s }) => {
      if (!payload.leadId) return null;
      const lead = s.one('SELECT id FROM leads WHERE id = ?', [payload.leadId]);
      if (!lead) return null;
      // Touching updated_at is deliberate: it is what "last activity" reads, and a
      // composed draft IS activity. It is not a stage change.
      s.run('UPDATE leads SET updated_at = ? WHERE id = ?', [s.nowISO(), payload.leadId]);
      return note(payload.leadId, 'draft composed' + (payload.subject ? ': ' + payload.subject : ''),
        { draft_id: payload.draftId || null });
    },

    // The send already moved the cadence — pipeline.cjs did that atomically with the
    // mail row and wrote the audit entry. The rule reports the reaction; it must not
    // schedule or advance, or the cadence would be written twice.
    'email.sent': ({ payload }) =>
      report(payload.leadId, payload.first ? 'first email sent' : 'follow-up sent',
        { resend_id: payload.resendId || null, stage: payload.stage || null }),

    // An inbound reply already put the lead in the reply stage (pipeline.cjs, with its
    // history row). Reports only, for the same reason.
    'reply.received': ({ payload }) =>
      report(payload.leadId, 'reply received',
        { message_id: payload.messageId || null, classification: payload.classification || null }),

    // Redraft feedback: keep it, count it, and let whoever writes drafts read it back
    // from GET /api/redraft-guidance instead of the reason being silently dropped.
    'draft.redraft_requested': ({ payload }) =>
      note(payload.leadId, 'redraft: ' + (payload.reason || 'no reason given'),
        { draft_id: payload.draftId || null, reason: payload.reason || null }),

    'draft.discarded': ({ payload }) =>
      note(payload.leadId, 'draft discarded without sending' + (payload.reason ? ': ' + payload.reason : ''),
        { draft_id: payload.draftId || null, reason: payload.reason || null }),
  };
}

module.exports = { buildRules };
