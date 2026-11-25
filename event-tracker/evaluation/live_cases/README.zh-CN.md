# 联网 RSS 评测脚本用法

本目录的 `mcd_hk_example.json` 是**运行样例**，不是已冻结的 MCD 标准答案。运行前应复制为本轮案例，固定 `topic`、明确批准的 `approved_sources`、`window` 和 `max_items`。案例文件不含标准答案或已知 URL，不会把评测答案送给 Agent。

在仓库根目录运行：

```powershell
backend/.venv/Scripts/python.exe scripts/run_live_benchmark.py --case evaluation/live_cases/mcd_hk_example.json --provider qwen --repeats 3
```

需要先在根目录 `.env` 配好当前模型的 `QWEN_API_KEY`、`QWEN_BASE_URL`、`QWEN_MODEL`。只检查采集链路可用 `--provider mock`。每次运行创建独立数据库，结果写到已忽略的 `evaluation/reports/live-local/<case_id>-<capture_id>/`；真实 RSS 与模型调用会产生动态结果及 API 成本。脚本不会修改既有本地服务数据库。

每轮输出：

- `capture.json`：来源建议和已批准来源、请求和运行 trace、每条 RSS 原始输入与处理结果、证据判断、Event 和通知、代码与模型设置、抓取时间及摘要哈希。原始 feed XML 和目标网页全文当前无法从接口获取。
- `labels.template.json`：本轮抓到的候选清单。复制成 `labels.json`，人工给 `relevance` 填 `relevant`、`irrelevant`、`uncertain` 或 `unjudged`；可用 `event_keys` 和 `review_note` 记录归属与理由。`known_items` 应从**运行前独立列好的**关键进展清单填入，给每项稳定 `id` 和 `acceptable_urls`；Google News 跳转 URL 对不齐时，经人工核对后填 `matched_candidate_keys`。不要事后根据 Agent 结果补造已知项。

裁定后运行：

```powershell
backend/.venv/Scripts/python.exe scripts/score_live_benchmark.py --capture evaluation/reports/live-local/<本轮目录>/capture.json --labels evaluation/reports/live-local/<本轮目录>/labels.json
```

输出 `score.json` 和 `score.md`。相关性 precision、recall、F1 的分母只包含**时间窗内、已人工裁定且 Agent 有可观察相关性判断**的 RSS 候选；未裁定、难以判断、模型未判断的数量单列。已知进展覆盖只以预列清单为分母，不能称为全网召回。结构检查只验证事实引用 ID 存在、属于批准来源并且没有晚于截止时间；**不判断事实语义是否被原文支持**。

## 与评测设计的当前差距

当前分支实现的是批准 RSS 来源监测（可包含 Google News RSS 搜索 feed），不是通用网页搜索。没有独立查询列表、搜索预算和排名、目标文章全文抓取、原始 feed/HTTP 错误快照、页面版本与可靠发布时间判断。运行时只执行 `window.end` 上限，`window.start` 仅供评分分析；较早条目仍可能影响 Event。此时只能报告 RSS 候选池内的判断和预列关键进展覆盖，不能给网页全文检索或历史时间线的完整分数。

`Topic Evolution Accuracy`、`Event State Update Accuracy`、`Notification Quality` 和引用的语义支持仍需按 Event/claim/milestone 人工对齐，再把裁定表接入评分器；目前这些分数在 `score.json` 中是 `null`。公平 baseline、去重组件对照、跨次汇总也尚未实现。固定证据回放继续使用 `scripts/run_benchmark.py` 与 `scripts/build_review_sheet.py`。
