# Execution ledger — pipeline-storage-transition

Plan: 2026-10-19-pipeline-storage-transition.md. Spec and plan approved by user.
Branch: codex/pipeline-storage-transition, base f6482dd.

- Baseline: 365 passed, 15 network tests deselected. Existing pytest cache warning.
- Ruling: local venv launcher references a missing Python; use system Python 3.14
  with PYTHONPATH pointing to src and existing .venv/Lib/site-packages. No installs.
  Disable pytest cache provider to avoid the existing cache directory error.
- Ruling: native execution without subagents, as explicitly agreed. Self-review
  at completion; no delegated review overriding that choice.
- Ruling: keep this small tracked ledger alongside the plan instead of using
  POSIX-only workspace helper scripts on Windows. No global Git/ACL changes.
- Preflight: task 1 lifecycle consumed by task 3; manager in task 2 consumed by
  task 3; task 4 removes Dataset only after pipeline integration. No conflicts.
- Task 1 RED: new start failure/cancellation test, 2 failed as expected: closed
  skipped and reason remained done. GREEN: 79 tests passed after protected startup.
- Commits were initially blocked by missing Git author identity; no author was invented.
- Task 2 complete: manager/settings tests RED on missing module, GREEN 21 passed.
- Task 3 complete: integration tests initially 13 failed; GREEN 115 tests including
  legacy Dataset behavior before removal. Fatal signal precedes new work, cleanup
  waits for workers and closes components in reverse order.
- Task 4 implemented: removed storage/exports/dataset arguments, replaced old backend
  tests with application component tests, migration guide and JSONL example.
  API removal tests RED 6 failed; GREEN 101 integration tests. No data files touched.
- Ruling: use separate focused test modules instead of growing test_crawl.py;
  existing tests retain their behavior outside removed storage API.
- Review fixes RED/GREEN: late run failures were unobserved on stream break;
  context-exit cancellation was swallowed. Three reproductions failed before fixes.
  Context exit now observes the run and preserves consumer failure/cancellation.
- Ruling: private manager.close returns list[BaseException] instead of list[Exception]
  to report cancellation from a component while still closing remaining owners.
  Two failing close-cancellation tests now pass; cancellation remains cancellation.
- Final review: self-review, no subagents per user choice. Examined lifecycle,
  failure priority, per-crawl state, removed API, and example thread writes.
- Final verification: 364 passed, 15 network tests deselected; asyncio debug
  enabled and warnings treated as errors. Command: system python -W error -m
  pytest -q -m 'not network' -p no:cacheprovider
  -o asyncio_default_fixture_loop_scope=function. Existing venv dependencies
  loaded via PYTHONPATH. Explicit fixture loop scope avoids an existing
  pytest-asyncio configuration deprecation; no project config changed.
- Ruff check and format --check: passed. Git diff --check: passed.
- Consumer status unchanged: only its pre-existing modified pyproject.toml.
- No delegated review, push, merge, release, DB access or migration.
- All implementation tasks complete; author identity was subsequently configured by the user.

- Follow-up: added examples/mongo.py with optional lazy PyMongo import,
  from_crawler settings, async ping/insert/close, and copied documents.
- Mongo example tests replace only the driver: no database or scraping run.
- Review fix: pipeline worker cancellation previously could finish silently
  or leave queued requests waiting. RED reproduced timeout; GREEN propagates
  cancellation through the fatal wakeup without counting it as a request error.
- Follow-up verification: 371 offline tests passed, 15 network deselected,
  warnings-as-errors and asyncio debug enabled; Ruff checks passed.
- Finalization: user-configured Git author verified; fresh strict suite
  371 passed, 15 deselected, Ruff and staged diff checks passed.
- Ruling: one atomic transition commit instead of reconstructing the planned
  intermediate commits, because identity was unavailable while implementing.
