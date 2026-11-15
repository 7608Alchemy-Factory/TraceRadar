# Acceptance record — 2026-11-10, Asia/Hong_Kong

## Executed checks

- Backend: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider` → **13 passed** in 28.34s. One upstream Starlette/AnyIO deprecation warning; no test failures.
- Frontend: `npm run build` → TypeScript check passed, Vite production build passed (29 modules). Dependency install recorded `package-lock.json`; backend actual dependency snapshot recorded in `requirements.lock.txt`.
- Servers started successfully at 127.0.0.1:8000 and 127.0.0.1:5173. Browser displayed **API · ok**, confirming the Vite proxy reaches FastAPI.
- Real browser: created a topic, approved both fixture sources, ran batches 1/2/3 in sequence, opened Event A detail, inspected facts/timeline/evidence/history, closed detail and refreshed. Observed 2 events, 3 digest + 1 immediate, 2 reviews, A patched at v3. Recent run and source approvals survived refresh. Browser warn/error log query returned an empty list.
- API demo: `.venv/Scripts/python.exe ../scripts/demo.py` → PASS, 2 events / 4 transitions / 4 notifications after all three batches and replay; reproducible result exported to `evaluation/reports/mock-demo.json`.
- Tests verify business counts remain **2 events / 4 transitions / 4 notifications** after replay and independently for another Topic. They also cover unapproved source rejection, vendor-only execution, no configured Qwen run creation, invalid schema, 404, transaction rollback/retry, idempotency key conflict, active-run/source lock, restart persistence/interrupted marking, digest_only and low/record_only mapping, cross-topic targets, invalid evidence references, version mismatch, source failure vs empty batch, Qwen substitute schema repair/auth/timeout attempt bounds.

## Environment observations

Python 3.12.4, Node 24.22.1, npm 11.19.1. The restricted execution sandbox denied Python temporary-directory access and esbuild process spawning. Dependency installation, pytest and production build succeeded using approved execution outside that sandbox. These failures were environmental, not reported as application test failures.

## Boundaries

Mock validates the engineering workflow only. Qwen transport is implemented and substitute-tested, but **real model invocation is unverified**. No credentials were requested or embedded. No live sources, paid model checks, external messages, cloud services, deployment, Git commits or pushes were invoked. See README for known limits and repeatable startup/demo steps.
