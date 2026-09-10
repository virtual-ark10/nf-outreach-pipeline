import crypto from 'node:crypto';

/**
 * A per-lead token is a random code that maps to { leadId, campaign }.
 * It is baked into the link an outreach email carries, so a click on that
 * link unambiguously identifies WHICH lead clicked (and which campaign).
 */
export function generateToken(leadId, campaign = 'default', linkRef = null, dest = null, ttlDays = 30) {
  return {
    token: crypto.randomBytes(16).toString('hex'),
    leadId,
    campaign,
    /** Which specific link this token guards: e.g. "pub-migma", "article-recap-3".
        Lets you tell apart WHICH suggestion a lead clicked, not just which lead. */
    linkRef: linkRef || null,
    /** Where the click redirects to. Kept SERVER-SIDE (in the token record) rather
        than in the email link, so the link carries no external URL. */
    dest: dest || '/',
    createdAt: new Date().toISOString(),
    expiresAt: new Date(Date.now() + ttlDays * 86400000).toISOString(),
  };
}

/**
 * Build a click-tracking link for one lead.
 *
 * IMPORTANT (spam/security hardening): the destination is resolved on the
 * server from the token record; the link itself carries ONLY the token —
 * no external URL, no `dest=` parameter, and by default no UTM tags
 * (keeps emailed links short). This keeps the link looking like ordinary
 * click-tracking and avoids any open-redirect heuristics in email
 * clients / antivirus.
 *
 *  - baseUrl : your site origin, e.g. https://newsletterfit.com
 *  - tok     : the token RECORD (from generateToken), so its .dest is used
 *  - utm     : OPTIONAL object of utm_* params if you want GA4 campaign
 *              tagging in the emailed link; omit for token-only links.
 *
 * Result:
 *   https://newsletterfit.com/api/click?lt=<TOKEN>                       (default)
 *   https://newsletterfit.com/api/click?lt=<TOKEN>&utm_source=outreach   (when utm given)
 */
export function buildTokenLink(baseUrl, tok, utm = {}) {
  const url = new URL('/api/click', baseUrl);
  url.searchParams.set('lt', tok.token);
  for (const [k, v] of Object.entries(utm)) url.searchParams.set(k, v);
  return url.toString();
}

/**
 * Decode a token by looking it up in the store (see store.js).
 * Returns null if unknown or expired.
 */
export function resolveToken(store, token) {
  if (!token) return null;
  const rec = store.lookupToken(token);
  if (!rec) return null;
  if (Date.now() > Date.parse(rec.expires_at)) return null;
  return rec;
}