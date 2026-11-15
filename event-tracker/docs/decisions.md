# Implementation decisions — 2026-11-10

- The current repository is TraceRadar. Business code is isolated in `event-tracker/`, rather than the old LLM 7608 path. Existing plans and course files are preserved.
- Python 3.12, FastAPI/Pydantic, React/TypeScript/Vite, sqlite3, single-process threaded queue. No agent framework is needed for this explicit workflow.
- SQLite stores validated domain documents as JSON with indexed relational columns, foreign keys, unique constraints and schema version 1. SQL is confined to Repository. State Manager is the sole state update path. Request threads and worker use separate short-lived connections.
- The queue is local and non-durable. Startup marks unfinished runs `failed/interrupted`; replay uses persistent evidence and transition keys. Use exactly one API process and no hot reload for the demo.
- Immutable evidence: a changed body at an existing `(source_id, external_id)` is explicitly rejected as SOURCE_REVISION. A connector must supply a new revision ID. It never silently rewrites old claim provenance.
- The demo timeline uses publication time, validated against referenced evidence. Other event occurrence dates need a future structured extraction policy.
- Mock uses content rules limited to fictional ExampleSoft vulnerabilities. It is an engineering fixture demo, not a general relevance classifier or benchmark score.
- Notification policy is deterministic. Topic preferences are snapshotted when a run starts; changes apply to subsequent runs. No external delivery occurs.
- Qwen uses the documented OpenAI-compatible Chat Completions JSON mode with strict local Pydantic validation. Base URL/model/key remain blank until the user chooses a Model Studio region/workspace. No silent Mock fallback.
- Original architecture copied from `C:/Users/86195/Desktop/架构设计.md` on 2026-11-10 into `architecture-reference.md`. Confirmed implementation plan takes precedence.

Qwen references checked on 2026-11-10:
- https://help.aliyun.com/en/model-studio/qwen-api-via-openai-chat-completions
- https://www.alibabacloud.com/help/en/model-studio/qwen-structured-output

The registered fixture paths never use a caller-supplied filesystem path. Runtime imports no evaluation modules. This is application separation, not a hostile-code sandbox. Reference validation does not prove semantic truth; inspect each claim and its evidence.
