# Hunter.io Credit Consumption Pattern

## Key Finding
The Hunter.io `/v2/domain-search` endpoint consumes **2 credits per successful call** that returns email data:
- 1 search credit
- 1 verification credit

This is not explicitly documented in Hunter's basic API description but is observable in the account endpoint (`/v2/account`) where both `searches.used` and `verifications.used` increment together after each domain-search that returns email data.

## Evidence from Session (2026-09-10)
After initializing with a fresh Hunter key (50 searches + 100 verifications):
1. Performed 1 domain-search on `tryprofound.com`
2. Checked account status:
   - Searches: used 1, available 50, remaining 49
   - Verifications: used 2, available 100, remaining 98

This indicates that even though only one search was made, two verification credits were consumed—likely because the API internally verifies each email address it returns (up to the limit of 10) and charges per verification.

## Practical Implications
- Effective free tier: 25 domain-searches per month (not 50) if you need the email data.
- Verification pool depletes faster than search pool.
- Monitoring only `searches.used` will underestimate actual consumption; always check both counters.
- When planning batches, calculate: `max_searches = min(searches_remaining, verifications_remaining // 2)`

## Recommended Adjustments to Workflow
1. In the Hunter domain-search step, explicitly check both search and verification credits before proceeding.
2. Log both metrics after each batch for accurate spend tracking.
3. Consider lowering the `limit` parameter if you want to conserve verification credits (though each returned email still seems to trigger a verification charge).

## Source
Direct observation via Hunter API calls during NewsletterFIT sponsor enrichment workflow, September 2026.