// How to drop this into your existing Express / Astro+Express backend.

import express from 'express';
import {
  AttributionStore,
  clickRouter,
  trackVisitMiddleware,
} from 'nf-attribution';
// If using relative paths instead of the package name:
// import { AttributionStore, clickRouter, trackVisitMiddleware } from '../src/index.js';

const app = express();
const store = new AttributionStore('./attribution.db');
// Issue a token per lead when you send them (see the "creating links" section in README).
// store.addToken(generateToken('lead_123', 'campaign-1'));

// 1) The click endpoint your emailed links hit.
//    Example link: https://yoursite.com/api/click?lt=<TOKEN>
app.get('/api/click', clickRouter(store, { cookieMaxDays: 30 }));

// 2) Site-wide visit tracking. Put this AFTER your routing/handlers so page
//    views get attributed. Safe to run on every route.
app.use(trackVisitMiddleware(store));

// 3) (Optional) a tiny API to pull a lead's visits so you can feed your sheet.
app.get('/api/lead-visits', (req, res) => {
  const leadId = req.query.leadId;
  if (!leadId) return res.status(400).json({ error: 'missing leadId' });
  res.json({ leadId, visits: store.visitsForLead(leadId) });
});

app.listen(3000, () => console.log('attribution enabled on :3000'));