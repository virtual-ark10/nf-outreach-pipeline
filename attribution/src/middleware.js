import { resolveToken } from './token.js';

const ATTRIBUTION_COOKIE = 'nf_lead';

/** Tiny cookie parser so we stay dependency-light (or you can use cookie-parser). */
export function parseCookies(header) {
  const out = {};
  if (!header) return out;
  for (const part of String(header).split(';')) {
    const i = part.indexOf('=');
    if (i === -1) continue;
    const k = part.slice(0, i).trim();
    const v = part.slice(i + 1).trim();
    out[k] = decodeURIComponent(v);
  }
  return out;
}

/**
 * Express router wiring for the email click endpoint.
 *
 * Flow when a lead clicks an emailed link:
 *   1. GET /api/click?lt=<TOKEN>&dest=/pricing&utm_...
 *   2. token looked up -> which lead + campaign
 *   3. first click recorded
 *   4. attribution cookie set (repeat visits over next N days also count)
 *   5. 302 redirect to destination, carrying UTM params through so GA4
 *      on the landing page tags the visit to the campaign.
 */
export function clickRouter(store, opts = {}) {
  const {
    cookieMaxDays = 30,
    cookieDomain = undefined,
    path = '/api/click',
    // Optional array of allowed destination domains, e.g. ["migma.io","newsletterfit.com"].
    // When set, a click that resolves to a destination outside the allowlist is
    // refused rather than redirected — defence-in-depth against open-redirect abuse.
    allowedDestinations = null,
  } = opts;
  return (req, res) => {
    const token = req.query.lt;
    const rec = resolveToken(store, token);
    if (!rec) return res.status(410).send('Link invalid or expired.');

    store.recordClick(token);

    const cookieVal = encodeURIComponent(
      JSON.stringify({ leadId: rec.lead_id, campaign: rec.campaign, token })
    );
    let setCookie = `${ATTRIBUTION_COOKIE}=${cookieVal}; Max-Age=${cookieMaxDays * 86400}; Path=/; HttpOnly; SameSite=Lax`;
    if (cookieDomain) setCookie += `; Domain=${cookieDomain}`;
    res.append('Set-Cookie', setCookie);

    // Resolve the destination from the SERVER-SIDE token record (never trust a
    // dest= query param for redirects). Fall back to query dest only for
    // forward-compat with links that still carry one.
    const recDest = rec.dest && rec.dest !== 'null' ? rec.dest : null;
    const dest = recDest || req.query.dest || '/';

    if (allowedDestinations) {
      let host = dest;
      try { host = new URL(dest, `${req.protocol}://${req.get('host')}`).host; } catch (e) {}
      const ok = allowedDestinations.some((d) => {
        const dd = String(d).toLowerCase();
        const h = host.toLowerCase();
        return h === dd || h.endsWith('.' + dd);
      });
      if (!ok) return res.status(403).send('Redirect not permitted.');
    }

    // Build redirect target, carrying utm_* params through so GA4 on the
    // landing page still tags the visit to the campaign.
    const base = `${req.protocol}://${req.get('host')}`;
    const redirect = new URL(dest, base);
    for (const [k, v] of Object.entries(req.query)) {
      if (k.startsWith('utm_')) redirect.searchParams.set(k, v);
    }
    return res.redirect(302, redirect.toString());
  };
}

/**
 * Site-wide middleware: run on page views (after Express static/Astro handling).
 * If an attribution cookie is present, it records that this specific lead
 * visited this page right now — that's what turns "they clicked once" into
 * "they're actually browsing the site over several days."
 */
export function trackVisitMiddleware(store) {
  return (req, res, next) => {
    const cookies = parseCookies(req.headers.cookie);
    const raw = cookies[ATTRIBUTION_COOKIE];
    if (raw) {
      try {
        const { leadId, campaign, token } = JSON.parse(raw);
        let linkRef = null;
        if (token) {
          const tok = store.lookupToken(token);
          linkRef = tok ? tok.link_ref : null;
        }
        if (leadId) {
          store.recordVisit(
            leadId,
            campaign || null,
            token || null,
            req.originalUrl || req.url,
            req.get('referrer') || '',
            linkRef
          );
        }
      } catch (e) {
        // Bad/forged cookie — ignore it, don't crash the request.
      }
    }
    next();
  };
}