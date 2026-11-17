# 成员 D 工作交付总结

日期：2026-11-17。交付分支：`feat/member-d-workspace-1117`。
开发基线：成员 C 的 `feat/data-pipeline-1116`，提交 `8e476ba`。本分支保留 B 的基础框架和 C 的数据管线，下面说明 D 的增量贡献。

## 1. 本人负责与完成的工作

成员 D 的职责是 Event Logic / Frontend：事件关联、状态更新、通知逻辑及用户工作台。

| 工作 | 本分支完成内容 |
|---|---|
| 通用事件逻辑 | 移除 ExampleSoft 专属模型提示与补丁限制，按用户目标判断相关性、已有/新事件、传闻、确认和冲突 |
| 状态更新 Harness | 模型提供结构化决策与新增 supported_claims；确定性代码保留原事实/时间线，逐字追加新事实并引用输入发布时间；最终仍经 StateManager 校验与原子提交 |
| 可靠模型接入 | 配置与连接测试、读取真实模型 ID、DeepSeek 非思考 JSON 输出、有限修复、明确区分模型名/认证/余额/网络错误；配置错误停止剩余批次 |
| 通知 | 重大进展即时、普通更新汇总、低优先级仅记录；遵守 digest_only 偏好；重复回放不重复提交/提醒 |
| 人工审核 | 保留原状态关闭待办，或采纳证据、替换指定旧事实；完整保存修改前后状态、依据及独立人工 Run/Trace |
| 工作台 UI | 重做导航、排版和信息层级；概览、事件档案、数字证据引用、证据库、审核、来源管理、运行历史、目标编辑、手动投喂 |
| 回放操作 | LBJ/NBA 独立工作区、三阶段截止时间、选择对应语料；只读取所选已批准来源，排除无关 RSS、模拟批次和重复注册 |
| 本地运行 | Windows 一键启动/停止；同一后台进程在 5173 提供 UI 与 API；脱离临时聊天终端；日志和数据库保留 |
| 评测交接 | 导出运行记录和独立空白标注模板，保留已有标注，不向 Runtime 提供 Ground Truth |
| 验证和文档 | 增加回归测试、更新 OpenAPI/契约、使用说明和本交付总结 |

不是本人的新增贡献：原来的 FastAPI/SQLite/Worker 骨架、ExampleSoft 三批模拟素材、C 的 RSS/Replay/Manual 连接器与多层去重、LBJ 30 条及 NBA 18 条语料。本分支是在这些基础上接入和完善 D 模块。

## 2. 本次“全部失败”的原因与修复

### 模型 ID 错误

本机配置把服务品牌名 `Deepseek` 当成了 API 模型 ID。服务端返回 HTTP 400，并明确给出支持的模型名。
通过实际 `/models` 返回结果，更正为 `deepseek-flash`，保留本地 Key。界面新增“读取可用模型”，不再靠手填品牌名猜测。
参考：[DeepSeek 模型列表](https://api-docs.deepseek.com/api/list-models/)、[JSON 输出](https://api-docs.deepseek.com/guides/json_mode/)。

### 回放混入不兼容来源

部分主题同时批准了 RSS、ExampleSoft 模拟源及多份回放源。`all` 批次不适用于 ExampleSoft 的 `1/2/3/empty` 连接器，导致来源错误及重复输入。
新增 RunInput.source_ids，仅允许选择当前主题已批准来源；UI 自动隔离选定回放语料。当前两个故障主题已恢复为对应单一回放来源。
原始失败 Run 和旧来源证据保留；证据库默认显示当前回放来源，可勾选查看全部历史。

### 大模型输出与严格状态契约不一致

此前模型需要同时输出完整历史、key_facts 与 supported_claims，容易产生改写旧事实、两份新事实表述不一致等问题。
现在使用 `assessment-v4`：supported_claims 为新增事实唯一来源，代码组装完整状态和时间线，再交给原 StateManager 验证。
未知证据 ID、跨 Topic、错误版本等仍会被拒绝；不会用 Mock 冒充成功，也不把失败新闻改成“不相关”。
Trace 记录 `state_assembly=evidence_delta_v1`；模型输出仍不合法时，最多进行一次带明确契约反馈的修复。

## 3. 已实际验证的结果

模型：本机 DeepSeek API 的 `deepseek-flash`。使用现有故障 Topic 恢复回放，三个阶段均最终完成。

| 场景 | 当前回放来源输入 | 最终状态 | 事件数 | 当前来源处理状态 |
|---|---:|---|---:|---|
| LeBron James 转会追踪 | 30 条 | completed，0 errors | 1 | 29 completed，1 review |
| NBA 多事件追踪 | 18 条 | completed，0 errors | 7 | 18 completed |

再次运行相同最终批次：LBJ 跳过 30 条，NBA 跳过 18 条；两者均新增 0 次状态变化、0 条通知、0 个错误。

其他验证：64 项后端测试通过；TypeScript 检查与生产构建通过。测试覆盖状态/证据守卫、原子回滚、主题隔离、版本、人工修正、模型列表及错误分类、模型增量状态组装和回放来源限制。

这些结果证明当前接入、恢复和工程流程可运行。它们不是从全新空白状态独立开展的正式 Benchmark，也不能当作相关性、事件归属或事实准确率。正式指标需要 A 的人工标签与独立实验。

## 4. 文件与接口接缝

- `backend/app/llm.py`：通用 Prompt、增量组装、模型错误与修复。
- `backend/app/services.py`：状态校验及人工修正。
- `backend/app/notification_policy.py`：确定性通知规则。
- `backend/app/model_settings.py`：本地配置与模型发现。
- `backend/app/repositories.py`：审核事务、证据查询、运行来源快照。
- `frontend/src/`：工作台、事件详情、审核、模型配置与回放操作。
- `scripts/local_workspace.py`、`local-server.ps1`：独立本地服务；仓库根目录 `.cmd` 为启动入口。
- `scripts/export_pilot.py`：实验输出与标注模板导出。

API 增量：模型设置/发现/测试、全部证据查询、审核 accept/dismiss、语料目录、RunInput.source_ids。
Run 支持 `provider=human` 和 `trigger_type=human_review`，供人工审核审计；数据库使用已有的增量 schema version 2。

## 5. 其他成员如何运行

```powershell
# 仓库根目录，首次安装
cd event-tracker\backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
cd ..\frontend
npm ci
npm run build
```

回到仓库根目录双击 `Start-TraceRadar.cmd`，打开 `http://127.0.0.1:5173`。停止使用 `Stop-TraceRadar.cmd`。
模型配置位于左下角：填写自己服务的 API 地址、Key，读取并选择可用模型，保存和测试连接。

API Key、`.env`、数据库、日志、PID 文件、虚拟环境、node_modules、dist 和本地实验数据均不提交。新克隆的项目不会带入个人 Topic 或凭据，需要自行创建演示/回放工作区。

## 6. 后续分工

- D：根据真实错例继续调整事件行为和交互，协助标注。
- A：核验语料、制定事件边界与标签、计算五项指标；不直接采用本次事件数作为正确答案。
- B：审阅新增契约和事务、按 C→D 的依赖顺序集成；定时调度、部署另行推进。
- C：继续检查来源、语料真实性、去重误删及召回。

站外通知、定时调度、人工新建事件、正式模型准确率报告仍未完成，不能算作本次交付功能。
