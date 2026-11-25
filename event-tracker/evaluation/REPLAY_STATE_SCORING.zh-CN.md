# 固定回放：Event 整体状态基础评分

运行 `scripts/run_benchmark.py` 后，每个案例目录会多出 `state_score.json`、`state_score.md` 和 `state_adjudication.template.json`。输入仍是冻结的 `case.json`、fixture 和 `ground_truth.json`；评分在 Agent 运行结束后进行。MCD 的 `state_facets.offer_status` 无论是否为空，**本项都不评分**；本项只看 `stage_expectations.events[].lifecycle_status`。

评分器先用证据 ID 匹配实际 Event 和预期 `event_key`。只有双方都唯一时自动对齐；共享证据对应多个 Event、合并或拆分、未关联证据时，输出候选 ID 供人工匹配。`current_status` 是自由文本，自动判对仅限与预期状态值完全相同；不同措辞不自动判错，标为 `review_needed`。如果 Event 缺失，记 `event_missing`，不再额外记一次状态错误。

每阶段状态与**状态转移**分别计数。例如 MCD 的 `weekly_rewards` 在 s1 为 `active`、s2 改为 `expired`，s3/s4 仍为 `expired`；s2 只产生一次预期状态转移。若 s2 的实际 Event 没有改变且该阶段有时间触发里程碑，脚本只给出 `time_update_gap` **可能原因**，不会自动认定是模型错误或功能缺口。人工根据运行 trace 裁定后，才可填 `feature_gap`。

需要复核时，把 `state_adjudication.template.json` 复制为 `state_adjudication.json`：

- `event_matches`：将 `actual_event_id` 改成真实 Event ID；确认没有对应 Event 时保留 `null`。一篇证据涉及多个 Event 时必须分别确认。
- `status_reviews`：将 `verdict` 改成 `correct`、`incorrect`、`feature_gap` 或 `unclear`，并写 `note`。`feature_gap` 用于确认的时间驱动能力缺口；不能因为状态文字不同就自动填 `incorrect`。

然后执行：

```powershell
backend/.venv/Scripts/python.exe scripts/score_replay_state.py evaluation/reports/benchmark-local/<本轮目录>/report.json --adjudication evaluation/reports/benchmark-local/<本轮目录>/state_adjudication.json
```

输出仍在同一目录。`status_accuracy` 只以已裁定的 `correct` 与 `incorrect` 为分母；有未解决的歧义、使用 mock、或 ground truth 仍是 draft 时为 `null`。`feature_gap`、缺失 Event 与未裁定项独立报告。若原始报告记录的 ground truth 哈希与当前文件不同，脚本拒绝评分，避免将旧输出配上新版答案。

这只是五项 Proposal 指标中 **Event State Update Accuracy 的整体生命周期部分**。Claim 内容、Event 边界、内部优惠状态、通知质量及语义引用仍未自动评分；完整评测不能以本脚本的单项数字替代。
