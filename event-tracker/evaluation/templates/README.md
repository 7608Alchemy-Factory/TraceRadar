# Ground truth v2.0 template

`ground_truth.template.json` is a **shape example**, not a scoreable case. Copy it beside a `case.json`; replace all placeholders. The agent must never read ground truth. Stable `item_id` values map to fixture `external_id`, not runtime UUIDs.

## Minimal rules

| Field | Meaning |
|---|---|
| `schema_version`, `case_id`, `annotation_status` | Format, link to case input, and `draft/adjudicated/frozen` review state. |
| `events[].event_key/boundary` | Stable event identity and what it covers. An article may support multiple events; multiple articles may support one event. |
| `evidence_labels[].item_id` | Exactly one label per input item. |
| `relevance` | `relevant/irrelevant/uncertain` relative to the Topic. Duplicates can still be relevant. |
| `event_keys` | Zero or more affected events. Empty for irrelevant items. |
| `duplicate_of` | Earlier item with the same substantive report, or null. |
| `provenance_status` | `verified/uncertain`. Uncertain date or historical version requires a `review_note`; do not invent certainty. |
| `scoring_exclusions` | Specific metrics excluded for this item, e.g. `timeliness` or `historical_fact_recall`; normally empty. |
| `claims[]` | Key semantic facts only. `supporting_item_ids` are acceptable evidence; `first_visible_stage_id` prevents future leakage. Source attribution belongs in `statement` when essential. |
| `stage_expectations[].events[]` | Events expected by a cutoff, with current state, required claims, and prohibited assertions. No separate count is needed. |
| `lifecycle_status` | `active/expired/end_unknown/not_applicable`, evaluated at that stage's `case.json` `as_of` timestamp, not at today's wall-clock time. For promotions, active means at least one confirmed current offer; expired means all tracked offers have known end dates that passed. Unknown end must not be guessed. A later offer can reactivate the same event. `not_applicable` is for topics such as vulnerabilities where offer expiry has no meaning. The current runtime uses `as_of` to filter visible evidence; it does not automatically update event status when an offer expires. |
| `state_facets` | Optional case-specific distinctions, e.g. individual offer periods or vulnerability patch availability versus customer deployment. They are judged semantically against current_status and facts, not expected as identical runtime JSON. |
| `milestones[]` | One meaningful change, possibly supported by several items. `trigger_type` is `evidence/time`; `expected_action` is `immediate/digest/record_only/none`. This avoids one alert per article. |

Optional `review_note` on any label records a human justification. No separate epistemic-status or per-item notification field: claims express attribution, and notifications are scored by milestone. Excluded or unimplemented capabilities remain visible in per-case error reports, not silently counted as agent mistakes. `annotation_status=frozen` requires an independent review and a recorded fixture/version hash in the experiment manifest.

## Runtime support as of the C/D development branch

The domain model can record relevance, one event association per assessment, facts with evidence IDs, transitions, notifications and ordered trace. It does **not** natively represent multiple event associations from one item, offer validity intervals, a reliable/unknown publication timestamp, or time-only expiration. These gold labels are retained as product requirements; report them as capability gaps until implemented. Current `EventState.current_status` and `key_facts` are free text, so `state_facets` need human checking or an explicit judge rubric.
