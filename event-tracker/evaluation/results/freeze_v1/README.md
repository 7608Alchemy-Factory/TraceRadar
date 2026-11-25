# Evaluation V1 published results

The report in `../../FINAL_REPORT_EVALUATION_DRAFT.md` summarizes the reviewed fixed-evidence runs and the search-only trial. The Word version is `../../TraceRadar_Evaluation.docx`.

- `fixed_replay_human_review.json`: human decisions on disputed fixed-evidence outputs.
- `fixed_replay_baseline.json`: the executed per-article baseline's Event-count results.
- `search_mcd_hk_capture_minimal.json`: the search candidates needed to recompute the published search score. It retains titles, URLs and at most 180 characters of captured content per item, rather than full article bodies.
- `search_mcd_hk_score.json`: the reviewed search score.

The complete local run traces remain outside Git because they include model requests, logs and third-party article content. The minimal search capture can be rescored with:

```text
python scripts/score_search_benchmark.py --capture evaluation/results/freeze_v1/search_mcd_hk_capture_minimal.json --labels evaluation/search_cases/mcd_hk_nov2026_search_v1_assistant_labels.json --output search_score_check.json
```
