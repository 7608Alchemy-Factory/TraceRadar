# TraceRadar — local topic-tracking agent

A working basic framework: create a Topic, approve sources, run simulated evidence batches, inspect multiple Events, state history, notifications and ordered execution traces. No model key is needed in Mock mode.

## Start on Windows PowerShell

Prerequisites verified here: Python 3.12.4, Node 24.22.1, npm 11.19.1. Other compatible Python 3.11+ / Node 22+ environments have not been checked.

From the repository root, install the backend:

```powershell
cd event-tracker\backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

SQLite initializes automatically on startup at `event-tracker/data/tracker.db`. Keep one API process; omit `--reload` and `--workers`. No virtual-environment activation or PowerShell execution-policy changes are required.

In a second terminal, from the repository root:

```powershell
cd event-tracker\frontend
npm ci
npm run dev
```

Open http://127.0.0.1:5173. API documentation: http://127.0.0.1:8000/docs. Vite proxies `/api` to port 8000. If ports conflict, change the API port and proxy target together in `frontend/vite.config.ts`; change the Vite server port there as needed.

## Fixed demo

1. Create the default **ExampleSoft security** topic. All ExampleSoft content, URLs, dates and sources are fictional simulated input.
2. Check **both** source boxes and select **Save approved sources**.
3. Keep **Mock** selected. Run **第一批信息**, then **后续进展**, then **补丁与冲突**, waiting for each to complete.
4. Expect 2 events, 4 transitions, 3 digest notifications, 1 immediate notification and 2 review records. Event A becomes patched; conflicting and unclear reports never overwrite confirmed state.
5. Inspect Event A for the four state fields, raw evidence, duplicate/conflicting relations and before/after history. Expand Trace steps to see actual decisions, references, attempt and timestamps.
6. Run the same batches again. Event, transition and notification counts stay unchanged. Refresh or restart to verify persistence.

Notifications are in-app decisions only. Digest means a list of routine updates. No email, scheduled summary or phone delivery is implemented.

## Checks

Backend, from `event-tracker/backend`:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Frontend, from `event-tracker/frontend`:

```powershell
npm run check
npm run build
```

With the API running, from `event-tracker/backend`, execute the repeatable API demo and export an acceptance result (creates a new demo Topic):

```powershell
.\.venv\Scripts\python.exe ..\scripts\demo.py
```

Result: `evaluation/reports/mock-demo.json`. This script reads expected results; the agent runtime never imports evaluation data.

## Optional Qwen setup

Copy `.env.example` to `.env` under `event-tracker/`. Set `QWEN_API_KEY`, `QWEN_BASE_URL` and `QWEN_MODEL` locally. Select the HTTPS compatible-mode base URL and a JSON-mode-capable model from **your** Model Studio workspace/region; no address or model is guessed. Restart the backend and select Qwen in the UI. Missing configuration is rejected before a run is queued. Credentials stay in the backend; `.env` is ignored by Git.

Adapter follows [Alibaba Cloud Chat Completions](https://help.aliyun.com/en/model-studio/qwen-api-via-openai-chat-completions) and [structured output documentation](https://www.alibabacloud.com/help/en/model-studio/qwen-structured-output). JSON mode is followed by Pydantic validation and State Manager reference/ownership checks. Network/temporary failures have at most 2 attempts; authentication fails immediately. Invalid JSON/schema has at most one repair attempt. No Mock fallback occurs.

An explicit optional real-call check (may incur a provider charge), from backend:

```powershell
.\.venv\Scripts\python.exe ..\scripts\check_qwen.py --allow-paid-call
```

**Real Qwen calls have not been verified.** Ordinary tests, health checks and the default demo never call a paid model.

## Module map

| Path | Responsibility |
|---|---|
| backend/app/domain.py | Shared Pydantic contracts and enums |
| backend/app/main.py | FastAPI endpoints and consistent errors |
| backend/app/repositories.py | SQLite schema, constraints, persistence and transactions |
| backend/app/connectors.py | Approved registered fixture sources |
| backend/app/llm.py | Provider interface, content-rule Mock, Qwen adapter |
| backend/app/services.py | State validation, atomic proposal submission and notification policy |
| backend/app/orchestration.py | Single worker, workflow, deduplication, trace and error aggregation |
| frontend/src | Typed HTTP client and local dashboard |
| fixtures/cybersecurity | Runtime evidence inputs |
| evaluation/expected | Test-only expectations |

See `docs/decisions.md`, `data_contracts.md`, `api_contracts.md`, `progress.md` and `acceptance.md` for boundaries and verified results. The source plans remain at repository root.

## Current limits

Local single user, manual execution, registered fixtures only. No live website retrieval, scheduling, authentication, cloud deployment or external notifications. Mock rules cover this demo only. Review records are visible but cannot yet be resolved through the UI. Restart fails interrupted runs explicitly; run again to recover safely. Changed content at an existing source external ID requires a new revision ID. SQLite schema version 1 initializes idempotently; future schema changes need explicit migrations. Evidence reference checks establish traceability, not semantic proof of truth.
