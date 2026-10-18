# Remaining framework scope — agreed 2026-10-19

The user approved discussing the remaining candidates together, then implementing
the agreed scope. Final decisions:

- Keep application-specific traversal limits in trading_platform. Do not add
  mark_incomplete, report_limit or Stats.incomplete_reasons to collector.
- Keep inprotect in the application; no bundled module or default hook.
- Add only Response.expect_status(status, *other_statuses). At least one integer
  status is required; return None on match. No implicit status rejection or ok
  property. Special statuses remain available to callbacks.
- Export ResponseStatusError from collector and collector.crawler. Its fields are
  status, expected_statuses (tuple) and url (original request URL, consistent with
  existing crawl diagnostics). The normal engine error policy handles it.

Implementation and verification:

- Tests first failed because ResponseStatusError was absent.
- Added the method and exception in crawler/response.py, public exports and docs.
- Covered accepted statuses including explicit 404, multiple statuses, mismatch
  details, missing arguments, opt-in behaviour and on_error/max_errors integration.
- Updated the exact-public-exports test for the new exception.
- No consumer, persistence, HTTP retry policy or engine changes.
