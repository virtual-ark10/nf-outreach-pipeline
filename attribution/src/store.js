import fs from 'node:fs';
import path from 'node:path';

/**
 * Zero-dependency JSON-file backed store. No native compile needed — works on
 * any Node version. Two collections persisted to disk:
 *   clicks  : every token issued (one per emailed lead) + first-click time.
 *   visits  : every site visit attributed to a lead (via the attribution cookie).
 *
 * If you later want SQLite/Postgres, just implement these same methods:
 *   addToken / lookupToken / recordClick / recordVisit / visitsForLead /
 *   allVisits / summary
 */
export class AttributionStore {
  constructor(filePath = './attribution.json') {
    this.file = filePath;
    this.data = { clicks: [], visits: [] };
    this._load();
  }

  _load() {
    if (fs.existsSync(this.file)) {
      try {
        const parsed = JSON.parse(fs.readFileSync(this.file, 'utf8'));
        this.data.clicks = parsed.clicks || [];
        this.data.visits = parsed.visits || [];
      } catch (e) {
        // Corrupt file — start fresh rather than crash the server.
      }
    }
  }

  _save() {
    // Write atomically (tmp + rename) so a crash mid-write can't corrupt data.
    const tmp = this.file + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(this.data, null, 2));
    fs.renameSync(tmp, this.file);
  }

  addToken(rec) {
    // Normalize camelCase input (from generateToken) into snake_case rows so
    // lookup/export/middleware all agree on field names.
    const row = {
      token: rec.token,
      lead_id: rec.leadId,
      campaign: rec.campaign,
      link_ref: rec.linkRef || null,
      dest: rec.dest || '/',
      created_at: rec.createdAt,
      expires_at: rec.expiresAt,
      first_click_at: null,
    };
    const i = this.data.clicks.findIndex((c) => c.token === row.token);
    if (i === -1) {
      this.data.clicks.push(row);
    } else {
      row.first_click_at = this.data.clicks[i].first_click_at;
      this.data.clicks[i] = row;
    }
    this._save();
  }

  lookupToken(token) {
    return this.data.clicks.find((c) => c.token === token) || null;
  }

  recordClick(token) {
    const c = this.data.clicks.find((x) => x.token === token);
    if (c && !c.first_click_at) {
      c.first_click_at = new Date().toISOString();
      this._save();
    }
  }

  recordVisit(leadId, campaign, token, visitPath, referrer, linkRef = null) {
    this.data.visits.push({
      id: this.data.visits.length ? this.data.visits[this.data.visits.length - 1].id + 1 : 1,
      lead_id: leadId,
      campaign: campaign || null,
      link_ref: linkRef || null,
      visited_at: new Date().toISOString(),
      path: visitPath || null,
      referrer: referrer || null,
    });
    this._save();
  }

  visitsForLead(leadId) {
    return this.data.visits
      .filter((v) => v.lead_id === leadId)
      .sort((a, b) => b.visited_at.localeCompare(a.visited_at));
  }

  allVisits() {
    return [...this.data.visits].sort((a, b) => b.visited_at.localeCompare(a.visited_at));
  }

  summary() {
    const byLead = new Map();
    for (const v of this.data.visits) {
      if (!byLead.has(v.lead_id)) byLead.set(v.lead_id, { lead_id: v.lead_id, visit_count: 0, first_visit: v.visited_at, last_visit: v.visited_at });
      const g = byLead.get(v.lead_id);
      g.visit_count++;
      if (v.visited_at < g.first_visit) g.first_visit = v.visited_at;
      if (v.visited_at > g.last_visit) g.last_visit = v.visited_at;
    }
    const clickSet = new Map();
    for (const c of this.data.clicks) clickSet.set(c.token, c);
    const rows = [];
    for (const g of byLead.values()) {
      const leadClicks = this.data.clicks.filter(
        (c) => this.data.visits.some((v) => v.lead_id === g.lead_id && v.token === c.token)
      );
      g.clicked = leadClicks.some((c) => c.first_click_at) ? 1 : (leadClicks.length ? 0 : (this.data.clicks.some((c) => c.lead_id === g.lead_id && c.first_click_at) ? 1 : 0));
      rows.push(g);
    }
    return rows.sort((a, b) => b.last_visit.localeCompare(a.last_visit));
  }

  close() {
    // JSON store is always flushed on write; nothing to close.
  }
}