# Submission verification

The following traces were recorded during the `tot-1125` integration on 25 November 2026. The commands in the [root README](../../../README.md) reproduce the checks with bundled sample data.

| Check | Observed result | Trace |
| --- | --- | --- |
| ExampleSoft success and replay | Six HTTP API runs completed. The first three produced two events, four transitions, three digests and one immediate alert. The second pass committed zero changes. | [Mock demo and changed request](mock-demo-and-changed-request.json) |
| Changed request | The revised goal and exclusion were saved and returned by the topic API. The canned Mock provider was used for the demo only. | [Mock demo and changed request](mock-demo-and-changed-request.json) |
| Authentication failure | One attempt for MCD-04. The linked resume processed MCD-04 and the previously unattempted MCD-03. | [Fault and resume traces](failure-recovery.json) |
| Service failure | Two attempts for MCD-04. MCD-03 committed in the original run. Only MCD-04 ran on resume. | [Fault and resume traces](failure-recovery.json) |
| Reviewed search score | Applying the checked-in labels to the archived capture regenerated the published JSON score exactly. | [Frozen results](../results/freeze_v1) |

The two failure traces use HTTPX MockTransport and the real application pipeline with a temporary SQLite database. In both scenarios the resumed run used saved evidence, an identical resume request returned the same run, and the final notification count was two. The benchmark's human-reviewed model results and limitations are in the [evaluation report](../FINAL_REPORT_EVALUATION_DRAFT.md).
