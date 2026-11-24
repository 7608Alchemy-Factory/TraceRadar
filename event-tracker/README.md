# TraceRadar — local topic-tracking agent

A working basic framework: create a Topic, approve sources, run simulated evidence batches, inspect multiple Events, state history, notifications and ordered execution traces. No model key is needed in Mock mode.

Member D walkthrough and current branch audit (中文): [逐步开发与验收](docs/member-d-guide.zh-CN.md).
Current v0.2 UI, model configuration and correction workflow: [工作台操作指南](docs/workspace-v2.zh-CN.md).
Member D delivery and live replay repair results: [成员 D 交付总结](docs/member-d-delivery.zh-CN.md).
This branch also supports C's replay/RSS/manual connectors, domain-independent model proposals,
goal editing, manual evidence entry and audited review closure. Mock remains ExampleSoft-only.

## Start on Windows PowerShell

For normal local use on Windows, double-click **Start-TraceRadar.cmd** at the repository root.
It starts a hidden process independently of the chat terminal, serving the built UI and API
together at http://127.0.0.1:5173. API docs are at http://127.0.0.1:5173/docs.
Closing the launcher window does not stop the server; double-click **Stop-TraceRadar.cmd** to stop it.
After restarting Windows, run the start launcher again. Existing topics, database and model settings remain.
The launcher reuses an already running instance of this workspace and refuses to stop an unrelated port owner.
It creates no startup task or system service. Logs: `data/local-workspace.log`.

Backend dependencies and `frontend/dist` must exist (already prepared in the current workspace).
After editing frontend source, rebuild it with `npm run build` before using this launcher.
The two-terminal instructions below are the development alternative; stop the bundled server first
because both modes use port 5173.

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

## Frontend storyboard

Open [the screen and motion storyboard](docs/frontend-1124-storyboard.html) in a browser to walk through the screens and transitions. The top-right language and theme settings are saved in the current browser. Article and user-entered text retain their source language.

## Topic management

Use **New topic** to add one or more topic types, or enter a custom tag. Type filters on the homepage match tagged topics and show starter ideas for the built-in categories. Existing tags can be edited with the tracking goal.

Use **Delete topic** on a topic page. Deletion requires confirmation and removes the topic's events, runs and reviews while retaining shared evidence and sources. A topic with an active run cannot be deleted. Fixtures remain available to the API and automated tests.

## Live sources

Open a topic's **Sources** tab to set up a web search, a monitored webpage, or an RSS subscription. Select the registered source and save the selection before running a check. A webpage monitor saves an initial snapshot and records later changes.

**Search engine** shows the active search configuration and can test the saved engine from the backend. Auto mode uses Bocha or Tavily when configured, with key-free Bing and Google News available for public search. Set `SEARCH_PROVIDER` to `auto`, `bocha`, `tavily`, or `free` and provide the matching optional keys on the backend. See [the environment example](.env.example).

If Google News keyword RSS is unavailable, a new check tries the configured search engine or key-free Bing for the same keywords. **Run history** records the fallback or names the failed sources. Deploy the frontend build and FastAPI backend from the same revision. Restart the backend after upgrading so it loads the new search endpoints and connectors.

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

To generate the Hong Kong McDonald's benchmark report from `event-tracker/backend`:

```powershell
.\.venv\Scripts\python.exe ..\scripts\run_benchmark.py --provider mock
```

Each run writes `report.json` with processing outcomes and counts, plus `review.md` with stage-by-stage expected claims, actual Event facts/status, citation structure, and notification checks for human adjudication. Use `--cases mcd_hk_campaigns_v1 --provider qwen` to run the frozen MCD case with your configured model; `qwen` is the legacy provider identifier. Mock runs are pipeline checks, not semantic scores. Frozen input hashes are checked against the case manifest. The evaluation design for future live search is in `evaluation/ONLINE_EVALUATION_DESIGN.zh-CN.md`.

## Model setup

Open **模型配置**, enter your compatible HTTPS API base URL or local Ollama loopback URL and key, use **读取可用模型** to choose an actual API model ID, then save and test. Saving applies to later runs immediately. Alternatively copy `.env.example` to `.env`, fill the legacy `QWEN_API_KEY`, `QWEN_BASE_URL` and `QWEN_MODEL` names, and restart. The provider identifier qwen is retained for compatibility; the actual model comes from configuration. Missing configuration is rejected before queuing. Credentials remain in the backend; `.env` is ignored by Git.

Adapter follows [Alibaba Cloud Chat Completions](https://help.aliyun.com/en/model-studio/qwen-api-via-openai-chat-completions) and [structured output documentation](https://www.alibabacloud.com/help/en/model-studio/qwen-structured-output). JSON mode is followed by Pydantic validation and State Manager reference/ownership checks. Network/temporary failures have at most 2 attempts; authentication fails immediately. Invalid JSON/schema has at most one repair attempt. No Mock fallback occurs.

An explicit optional real-call check (may incur a provider charge), from backend:

```powershell
.\.venv\Scripts\python.exe ..\scripts\check_qwen.py --allow-paid-call
```

The configured DeepSeek service has been verified on LBJ/NBA replays with `deepseek-flash` (2026-11-17); see the delivery report for scope and limits. Qwen-specific live calls remain unverified. Ordinary tests, health checks and the default demo never call a paid model. Use **读取可用模型** in the settings dialog to obtain actual API model IDs; a service brand name is not a model ID.

## Module map

| Path | Responsibility |
|---|---|
| backend/app/domain.py | Shared Pydantic contracts and enums |
| backend/app/main.py | FastAPI endpoints and consistent errors |
| backend/app/repositories.py | SQLite schema, constraints, persistence and transactions |
| backend/app/connectors.py | Approved fixture/replay/RSS/manual sources (member C) |
| backend/app/llm.py | Provider interface, content-rule Mock, Qwen adapter |
| backend/app/services.py | State validation, atomic proposal submission and notification policy |
| backend/app/notification_policy.py | Domain-independent deterministic notification decisions |
| backend/app/orchestration.py | Single worker, workflow, deduplication, trace and error aggregation |
| frontend/src | Typed HTTP client and local dashboard |
| fixtures/cybersecurity | Runtime evidence inputs |
| evaluation/expected | Test-only expectations |

See `docs/decisions.md`, `data_contracts.md`, `api_contracts.md`, `progress.md` and `acceptance.md` for boundaries and verified results. The source plans remain at repository root.

## Current limits

Local single user and manual execution. C's connectors support registered RSS feeds and offline replay, rather than general web search or arbitrary website crawling. No scheduling, authentication, cloud deployment or external notifications. Mock rules cover ExampleSoft only. Reviews support dismissal or explicit human acceptance/correction with a required audit note and complete state history. In **检查历史**, select a failed run and use **续跑未完成证据** to retry failed and unattempted items from its saved input snapshot. SQLite schema version 3 adds run input snapshots. Changed content at an existing source external ID requires a new revision ID. Evidence reference checks establish traceability, not semantic proof of truth. Generic model reasoning still requires live-provider evaluation. Model settings can be saved through the local workspace; keys are never returned and remain excluded from Git.

## Event radar, reports and translations

The homepage radar shows recent recorded events. Its dot positions represent display order and do not encode locations. The topic report uses the same stored update classifications as event milestones. Items awaiting verification are counted separately.

The interface language can request model translations for workspace text. Article translations are requested from the evidence dialog and labeled as a reference. These requests use the configured model provider and leave the source text intact.
