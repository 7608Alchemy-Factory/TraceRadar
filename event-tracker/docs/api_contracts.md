# API contracts

Interactive API docs: http://127.0.0.1:8000/docs. Generated schema: `openapi.json`.
Responses directly contain domain data, except composite views below.

| Method | Path | Result |
|---|---|---|
| GET | /api/health | status, default_provider, data_mode; no model call |
| POST / GET | /api/topics | 201 Topic / Topic[] |
| GET / PATCH | /api/topics/{id} | {topic, approved_sources} / Topic |
| POST | /api/topics/{id}/source-recommendations | {recommendation_mode: static, sources} |
| PUT | /api/topics/{id}/sources | approved_source_ids → {approved_sources} |
| POST | /api/topics/{id}/runs | fixture_batch_id, provider → 202 {run_id, status} |
| GET | /api/topics/{id}/runs | newest first, latest 30 Run records |
| GET | /api/runs/{id} | {run, steps}, ordered by sequence |
| GET | /api/topics/{id}/dashboard | topic, approved_sources, events, recent_runs, reviews, notification_counts |
| GET | /api/events/{id} | event, history, evidence with relation, reviews |
| GET | /api/topics/{id}/notifications | Notification[], optional action filter |

Unknown resources return 404; request/schema errors 422; active-run/configuration-state conflicts 409. Error envelope:
```json
{"error":{"code":"NO_APPROVED_SOURCE","message":"Approve a source before running.","details":{},"run_id":null}}
```

Run provider defaults to backend config. Registered providers: mock/qwen. Batches: 1/2/3/empty. No model address, key or arbitrary fixture path is accepted from the browser. Missing Qwen config is rejected before creating a run. A run captures approved source configurations and topic preferences. Source changes are blocked while pending/running.

Optional Idempotency-Key is scoped to topic. Same effective body returns the original run, different body returns 409. A distinct request while a topic is running returns TOPIC_RUN_ACTIVE. The worker processes queued runs sequentially; poll once per second until terminal. Reload retrieves the latest run. Unfinished runs fail explicitly at next startup.
