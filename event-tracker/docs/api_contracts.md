# API contracts

Interactive API docs: http://127.0.0.1:8000/docs. Generated schema: `openapi.json`.
Responses directly contain domain data, except composite views below.

| Method | Path | Result |
|---|---|---|
| GET | /api/health | status, default_provider, data_mode; no model call |
| POST / GET | /api/topics | 201 Topic / Topic[] |
| GET / PATCH | /api/topics/{id} | {topic, approved_sources} / Topic |
| POST | /api/topics/{id}/source-recommendations | {recommendation_mode: topic_aware_v1, sources}; registers validated candidates |
| POST | /api/sources | Register fixture/replay/RSS/manual source; 201 Source (existing ID updates configuration) |
| PUT | /api/topics/{id}/sources | approved_source_ids → {approved_sources} |
| POST | /api/topics/{id}/runs | fixture_batch_id, provider, optional as_of/prefilter_enabled/near_duplicate_enabled/manual_items → 202 {run_id, status} |
| GET | /api/topics/{id}/runs | newest first, latest 30 Run records |
| GET | /api/runs/{id} | {run, steps, evidence}, with steps and fetched inputs in processing order |
| POST | /api/runs/{id}/resume | Resume a finished run with errors from its saved inputs, returning 202 {run_id, status} |
| GET | /api/topics/{id}/dashboard | topic, approved_sources, events, recent_runs, reviews, review_resolutions, notification_counts |
| POST | /api/topics/{id}/reviews/{evidence_id}/resolve | decision=dismiss or accept; required note; accept also requires event_id, expected_version, current_status, fact; optional superseded_facts and update_kind |
| GET | /api/topics/{id}/evidence | All ingested evidence with processing status, assessment and error, including excluded/duplicate inputs |
| GET | /api/replay/corpora | Bundled runtime input corpus counts and three suggested publication-time cutoffs; no labels |
| GET / PUT | /api/settings/model | Read non-secret model status / save base_url, model and optional api_key to local .env |
| POST | /api/settings/model/models | Discover actual model IDs using draft base_url and optional api_key; does not save credentials or call completions |
| POST | /api/settings/model/check | Explicit one-request model/schema check; no Topic/Event changes |

RunInput.source_ids optionally selects a nonempty subset of this Topic's approved sources.
The subset is validated before queuing and persisted in the Run snapshot/idempotency body.
Replay UI chooses one source for the selected corpus, excluding unrelated RSS/fixtures and
duplicate registrations. All historical runs/evidence remain available; the evidence library
defaults to currently approved sources and can include inactive sources explicitly.

Model discovery, connection checks and assessment use the same provider error codes.
`PROVIDER_TIMEOUT` covers request timeouts, `PROVIDER_NETWORK` covers network errors,
`PROVIDER_RATE_LIMIT` covers upstream HTTP 429, and `PROVIDER_SERVER_ERROR` covers upstream HTTP 5xx.
Both settings endpoints make one attempt and return HTTP 502 for these errors. Assessment retries
temporary failures within the configured attempt limit, then records `failed_retryable` and continues.
Authentication, invalid model, insufficient balance and rejected requests stop the batch. The run
report lists each remaining input as `not_attempted` with zero attempts and the blocking error.
Each report row includes `source_id`, `external_id`, `evidence_id`, `status`, `attempts`, `error`
and `event_outcomes`. Older runs may have no per-input report.

Resume creates a new run linked by `snapshot.resume_of`. It reuses the original fetched evidence,
replay cutoff and topic options, with the current provider configuration. Completed or reviewed
inputs are skipped, including those completed by a later run. Original sources must still be
approved. An optional `Idempotency-Key` can be used for repeated requests. If there are no eligible
inputs, the endpoint returns `NO_RESUMABLE_EVIDENCE` (409).
Structured output has at most two attempts, with exact contract feedback for repair. Model
supported_claims is the canonical new-fact delta; deterministic code copies prior state,
appends those exact claims, and uses the input title/publication timestamp for the new timeline
entry. Raw references are checked before assembly; unknown IDs are never silently discarded.
The application attaches the input evidence ID. If a model includes an ID, it must exactly match the input.
A `new_fact` response requires `update_kind`, an explicit `proposed_state.current_status` and
nonempty evidence-backed `supported_claims`. A `confirmation` or `no_change` carries no update
fields or new claims. Every `existing_event` decision must reference an Event supplied to the
model. State updates also require its exact version.
StateManager independently checks the complete assembled proposal before it is stored. Trace records
state_assembly=evidence_delta_v1. Current prompt version is `assessment-v7`.
An assessment may include multiple `event_decisions` under one `evidence_id`. The legacy
single-decision response is still accepted. `counts.committed` counts state transitions.
| GET | /api/events/{id} | event, history, evidence with relation, reviews |
| GET | /api/topics/{id}/notifications | Notification[], optional action filter |

Unknown resources return 404; request/schema errors 422; active-run/configuration-state conflicts 409. Error envelope:
```json
{"error":{"code":"NO_APPROVED_SOURCE","message":"Approve a source before running.","details":{},"run_id":null}}
```

Run provider defaults to backend config. Registered model providers: mock/qwen; qwen is the legacy identifier for the configured compatible JSON-mode service. RunInput never accepts model addresses or keys. Dedicated local settings endpoints accept them and never return the key or a key fragment; updates are blocked while any Run is active. Foreign browser origins cannot mutate model settings or trigger a connection check. Fixture batches: 1/2/3/empty; replay also supports all. Source registration validates corpus paths within fixtures and validates RSS endpoints. Missing config is rejected before creating a run. A run captures approved source configurations, tracking goal, preferences and replay/manual options. Source approvals and topic edits are blocked while pending/running. Goal edits apply to future runs without reprocessing historical evidence.

Review handling is topic-scoped and blocked during active runs. Dismiss retains evidence/assessment/relations and creates no transition/notification. Accept targets an existing Event in the same Topic, checks its version and exact superseded facts, creates a current-evidence-backed fact and timeline entry, and preserves the full before/after state. Run(trigger_type=human_review,provider=human), RunStep, transition, notification, processing completion and resolution are committed in one transaction. Failed commits roll back all writes. Repeating the same request returns the same resolution; a changed request returns 409. Resolved evidence stays processed on replay. actor=local_user identifies an unauthenticated local action, not a verified identity. Ordinary model proposals still cannot delete old facts.

Optional Idempotency-Key is scoped to topic. Same effective body returns the original run, different body returns 409. A distinct request while a topic is running returns TOPIC_RUN_ACTIVE. The worker processes queued runs sequentially; poll once per second until terminal. Reload retrieves the latest run. Unfinished runs fail explicitly at next startup.
