# Code Agent 开发实施计划：Autonomous Event Tracking Agent 本地 MVP

日期：2026-11-09，Asia/Hong_Kong。
目标交付：本周，暂按 2026-11-15 为排期上限，具体截止时刻未指定。
本文是开发任务书；当前只交付计划，尚未创建业务工程。

## 1. 任务背景与执行原则

用户将先独立承担主要架构与集成开发，不依赖组员先提交模块。
开发本地网页，完成 Topic → Source 确认 → 手动 Run → Evidence 处理 → Event 状态更新 → 通知决策 → 页面与 Trace 查询。

参考资料：

- `C:/Users/86195/Desktop/架构设计.md`：原始架构蓝图。
- `C:/Users/86195/Desktop/LLM 7608/docs/phase0-plan.md`：本轮已确认的技术栈和排期。

用户明确决定优先于参考资料中的建议；本文新增的细节是本周实施默认方案，可在不改变用户目标的前提下按实际代码调整，并记录理由。

执行时先检查工作区及适用 AGENTS.md。当前目录包含课程资料与其他练习，不能清理、重构或覆盖这些内容。
建议业务工程新建在 `C:/Users/86195/Desktop/LLM 7608/event-tracker/`，下文所有工程路径相对该目录。
若已有该项目实现，先检查并增量修改，不重新覆盖。
这份任务书不自动要求创建新聊天、启动多个代理、提交 Git、推送代码或部署。

## 2. 范围与完成定义

### 用户已确认的基线

- 后端：Python、FastAPI、Pydantic。
- 前端：React、Vite、TypeScript。
- 数据库：SQLite；通过 Repository 访问。
- 编排：显式 Python Workflow，模块化单体。
- 本地、单用户、手动触发；通知先展示在网页。
- 模型服务暂定 Qwen，实际模型与接入方式待配置。

### 本周必须交付

1. 无需模型 Key，Mock 模式可以完成全部网页流程。
2. Topic 和批准的 Source 可创建、查询、保存。
3. Run 异步执行并可查询状态和有序步骤；不长时间占用触发请求。
4. 同一 Topic 可维护多个 Event，Evidence 不等于 Event。
5. 状态四字段、历史、通知决策、Trace 可查询且持久化。
6. 相同证据安全重跑；重启保留数据；错误不会伪装为成功。
7. Qwen 适配器、配置说明和可选真实调用检查。无凭证时准确报告“未验证真实调用”，继续交付 Mock 闭环。
8. README、固定 Demo、必要测试、验收记录。

### 后续阶段，不进入本周关键路径

真实网站抓取、定时监控、外部消息投递、多用户和登录、云部署、完整五项指标实现、Finance 第二案例、图谱、向量库、复杂 Agent Framework。
本周网页的“即时通知”表示立即出现在应用内，不表示邮件或手机消息已经发送。
Digest 是常规更新列表，不承诺定时发送或额外模型总结。

### 可调整默认值

首个 Demo 使用虚构软件的漏洞披露与修复，明确标注“模拟数据”；并非已获用户确认的真实业务领域。
Fixture 是证据输入；Mock 是模型实现。支持 `fixture + mock` 与 `fixture + qwen` 两种组合，不混淆。
Source 推荐先用后端注册的候选清单和静态理由，不宣称是模型发现或实时搜索。

## 3. 建议工程结构与工具

```text
event-tracker/
  backend/
    app/
      main.py
      api/                    # topics、sources、runs、events、notifications
      core/                   # settings、errors、logging
      domain/                 # Pydantic 契约、枚举、验证
      db/                     # SQLite 连接、DDL 初始化与版本记录
      repositories/           # SQL 与持久化接口
      services/               # 业务校验、State Manager、通知策略
      connectors/             # Connector 接口、FixtureConnector
      llm/                    # AssessmentProvider、MockProvider、QwenProvider
      orchestration/          # Run Manager、后台 worker、workflow
    tests/
    requirements.txt          # 或 pyproject.toml；选择一种，不同时维护两套
  frontend/
    src/
      pages/
      components/
      services/               # HTTP Client
      types/                  # 从契约维护/生成的类型
  fixtures/cybersecurity/     # 运行时可见的内容与来源，不含答案标签
  evaluation/
    expected/                # 仅测试/评测读取的预期结果
    reports/
  scripts/                    # 初始化、验证、导出等本地辅助命令
  docs/
    decisions.md
    data_contracts.md
    api_contracts.md
    progress.md
    acceptance.md
  .env.example
  .gitignore
  README.md
```

为一周范围优先使用 Python 标准库 sqlite3，SQL 留在 Repository 内，用事务与数据库约束保证一致性。
使用 pytest；前端保留 TypeScript 检查与构建，并用少量浏览器测试验证关键流程。
依赖选择兼容且环境支持的版本，安装后记录实际版本与锁文件；不凭空写“最新版本”。
后端默认 127.0.0.1:8000，前端默认 127.0.0.1:5173；冲突时配置端口并记录。
前端通过 Vite `/api` 代理访问后端，模型凭证只在后端读取。

## 4. 数据契约：编码前先落盘

所有模型字段写明类型、必填/可空、默认值、示例；ID 使用后端生成的稳定字符串；时间使用带时区 UTC。
不得让 API、模型输出、数据库和前端各自定义不一致的字段。

| 对象 | 最小字段与约束 |
| --- | --- |
| Topic | topic_id、title、description、status、notification_preferences、created_at、updated_at；status=draft/active |
| Source | source_id、name、type、endpoint、config；本周 type=fixture；endpoint 由注册表控制 |
| TopicSource | topic_id、source_id、approved_at；批准关系必须持久化 |
| Evidence | evidence_id、source_id、external_id、url、title、content、published_at、fetched_at、content_hash、canonical_evidence_id |
| TopicEvidence | topic_id、evidence_id、处理状态、assessment、error；相关性与处理结果属于 Topic，不全局共享 |
| Event | event_id、topic_id、title、state、version、created_at、updated_at |
| EventEvidence | event_id、evidence_id、relation；区分 supporting/conflicting/duplicate |
| EventState | current_status:string、timeline:TimelineItem[]、key_facts:Fact[]、sources:EvidenceID[] |
| TimelineItem | occurred_at、description、evidence_ids；不能用抓取时间伪造事件发生时间 |
| Fact | fact、evidence_ids |
| Assessment | evidence_id、is_relevant、association、novelty、supported_claims、conflicts、proposed_state、requires_review、reason_codes |
| Association | type=existing_event/new_event/unresolved；existing_event 必须提供本 Topic 的 event_id；new_event 提供标题和理由 |
| StateUpdateProposal | event_id 或新 Event 提议、expected_version、update_kind、完整候选 EventState、evidence_ids；由 State Manager 验证 |
| StateTransition | transition_id、event_id、run_id、before_state、after_state、evidence_ids、idempotency_key、committed_at |
| Notification | notification_id、topic_id、event_id、transition_id、priority、action、reason_codes、created_at；本周应用内记录无需伪造投递状态 |
| Run | run_id、topic_id、trigger_type、status、provider、fixture_batch_id、started_at、completed_at、counts、error |
| RunStep | step_id、run_id、sequence、step_name、evidence_id、status、attempt、input_refs、output_refs、decision、error、started_at、completed_at |

Run 状态采用 pending/running/completed/completed_with_errors/failed/cancelled；cancelled 保留枚举，本周不必做取消按钮。
RunStep 状态采用 pending/running/completed/skipped/failed。
EventState.sources 是 Evidence ID 引用，不是 Source ID 或 URL；通过 Evidence 再查 Source。
novelty 枚举为 new_fact/confirmation/conflict/no_change；update_kind 枚举为 event_created/fact_added/major_milestone/minor_update。update_kind 由模型提议、代码按规则校验后用于通知映射；本周补丁发布是 Demo 明确定义的 major_milestone，不自动将所有领域状态变化视为重大变化。
原始 Evidence 与派生状态分开；不要把状态同时维护在两处形成两个真相来源。
初次创建 Event 的 transition.before_state 可以为空，所有后续更新必须记录完整前后状态。

Schema 校验之外，State Manager 还必须校验：Evidence 存在且属于本次 Topic 可用输入；Event 属于该 Topic；全部关键事实和时间线项有有效 Evidence 引用；版本匹配；冲突未被静默覆盖。
结构和引用检查不能证明事实语义正确；保留 Claim→Evidence 内容供用户核对与后续评测，不能宣称已经自动证明真实性。

## 5. API 契约

响应数据直接按共享 Schema 输出；错误统一为：

```json
{"error":{"code":"NO_APPROVED_SOURCE","message":"Approve a source before running.","details":{},"run_id":null}}
```

FastAPI 默认请求校验错误也归一化；不泄露密钥或调用堆栈。

| 方法与路径 | 请求/响应要求 |
| --- | --- |
| GET /api/health | 简单健康状态；不能发起付费模型调用 |
| POST /api/topics | title、description、notification_preferences → 201 Topic，初始 draft |
| GET /api/topics | Topic 列表，按创建时间稳定排序 |
| GET /api/topics/{id} | Topic 与当前批准 Source 配置 |
| PATCH /api/topics/{id} | 修改标题、目标和通知偏好；不允许前端直接修改 EventState |
| POST /api/topics/{id}/source-recommendations | 返回注册 Fixture Source 清单、理由、recommendation_mode=static |
| PUT /api/topics/{id}/sources | approved_source_ids → 保存并返回当前关系；至少一个则 active，无则 draft |
| POST /api/topics/{id}/runs | fixture_batch_id、provider=mock/qwen → 202 {run_id,status} |
| GET /api/topics/{id}/runs | 最近运行，便于刷新网页后恢复展示 |
| GET /api/runs/{id} | Run、按 sequence 排序的步骤、错误和结果计数 |
| GET /api/topics/{id}/dashboard | Topic、Event 摘要、最近 Run、通知计数 |
| GET /api/events/{id} | EventState、Evidence、完整历史、待审核冲突 |
| GET /api/topics/{id}/notifications | 支持 action=immediate/digest/record_only 过滤 |

不存在的资源返回 404；非法状态/并行冲突返回 409；字段错误返回 422；上游调用失败写入 Run 并可查询。
run 请求可使用 Idempotency-Key：同一 Topic 同一请求键返回原 Run；同键不同请求体返回 409。
不同请求键但同一 Topic 已有 pending/running Run 时返回 409 TOPIC_RUN_ACTIVE。
Provider 默认来自后端配置；允许显式选择时仅接受已注册值，不接收前端传来的任意模型地址或凭证。
运行期间禁止改变批准 Source，返回 409；Run 保存批准关系及配置快照。
前端约每 1 秒查询运行，终态即停止，页面离开清理轮询；刷新可从最近 Run 恢复。

## 6. 工作流、事务与恢复

### 执行顺序

1. API 验证 Topic、批准关系、批次与 Provider 配置，创建 pending Run，返回 run_id。
2. 单进程后台 worker 顺序处理队列；本地启动仅一个 API worker，不使用开发热重载进行正式演示。
3. 加载 Topic 和 Source 配置快照，创建 ordered RunStep。
4. FixtureConnector 按 published_at、稳定 external_id 顺序读取本批次批准来源的证据。
5. 规范化文本、计算 hash、保存 Evidence 与 TopicEvidence。
6. 去重或已完成处理则记录 skipped；未完成/失败的证据允许后续 Run 重试。
7. 调用 AssessmentProvider；不相关则归档，不明确归属/冲突则记录待审核。
8. 验证更新提议。无实际状态变化时不创建 Transition 和通知。
9. 在短事务中创建/更新 Event、关联 Evidence、写 Transition、创建关联 Notification、标记 TopicEvidence 完成。
10. 写步骤结果和计数，汇总 Run 终态。

模型和 Connector 调用不得在 SQLite 写事务中等待。worker 使用独立连接；不要跨线程共享请求连接。
启用外键约束和有限数据库 busy timeout，正确关闭连接。

### 去重与幂等必须具体实现

- 来源条目使用 UNIQUE(source_id, external_id)，重复抓取更新获取信息而不重复新增条目。
- 以规范化 content_hash 确定 canonical Evidence，保留其他来源条目与指向关系。
- 精确内容重复只贡献一次事实，不使用模型“相似”判断代替基础去重。
- 内容不同的近似报道本周不保证自动合并；Assessment 必须判断是否有新事实，无变化不提交。
- TopicEvidence 保存处理结果；同一 Evidence 对不同 Topic 可以有不同判断。
- Transition 幂等键由 topic_id、canonical_evidence_id、处理策略版本派生；不能包含新 run_id，否则重跑失效。
- 一条 canonical Evidence 本周最多提交一个原子提议；需要多 Event 多提议是后续扩展。
- Notification 对 transition_id 建唯一约束，一次变化只记录一个最终动作。
- 状态、历史、通知与处理完成标记一起提交；Trace 写入失败不能使已提交证据再次改变状态。
- Run 和 Trace 重跑可以增加，Evidence、Event、Transition、Notification 的业务数量不能无故增加。

### 失败与中断

- Source 失败不能显示“没有新信息”；记录 source_error。
- Provider 超时或临时网络失败最多总计 2 次尝试，有限退避；认证失败不循环重试。
- JSON/Schema 错误最多修复 1 次，仍失败则不提交状态。
- 事务失败必须回滚，记录步骤错误；后续 Run 可重新处理。
- 无证据的新批次正常完成；所有抓取失败或无可用处理结果且发生错误则 failed；部分成功且部分失败则 completed_with_errors。
- 启动时将遗留 pending/running Run 标记 failed，原因 interrupted；本周不承诺断点自动恢复，用户重新 Run 安全重跑。
- Qwen 失败不得静默回退 Mock，不得将 Mock 结果标记为真实模型结果。

### 通知默认策略

使用确定性规则，避免再引入一个不稳定模型调用：event_created/fact_added 为 routine/digest；major_milestone 为 breaking/immediate；minor_update 为 low/record_only；无关与重复不产生新的状态通知。
importance 由校验后的更新类别映射；新增的机器标签需写进 Schema 与样例，不靠任意状态字符串包含关系判断。
Topic 偏好至少支持 standard 与 digest_only；后者将 breaking 降为 digest，保留 reason_codes。
低重要性但有实际状态变化时生成 low/record_only；Digest 展示数据库中 action=digest 的记录。

## 7. 固定案例与预期行为

使用虚构 ExampleSoft 软件和两个独立漏洞 A/B，避免引用未经核实的真实漏洞信息。
所有正文、时间、URL 和来源明确属于模拟输入；不要伪造在线抓取。
至少两个注册 Fixture Source，并验证未批准 Source 不进入执行路径。

| 批次 | 输入 | 预期结果（仅写入测试/评测侧） |
| --- | --- | --- |
| 1 | 漏洞 A 披露 | 创建 Event A，状态 disclosed，1 次 Transition，digest |
| 1 | 与目标无关的体育信息 | 判不相关，无 Event/Transition/Notification |
| 2 | A 的影响版本确认 | 更新 A，追加事实与时间线，1 次 Transition，digest |
| 2 | 相同正文的另一来源报道 | 指向 canonical Evidence，无新增状态变化或通知 |
| 2 | 独立漏洞 B 披露 | 创建 Event B，1 次 Transition，digest |
| 3 | A 的补丁发布 | 更新 A 为 patched，1 次 Transition，immediate |
| 3 | 与 A 已确认影响范围冲突的信息 | 待审核，不覆盖确认事实，不新增 Transition |
| 3 | 无法判断归属的模糊线索 | unresolved，保存 Evidence 与理由，不强行创建 Event |

标准偏好下，三个批次依次运行的基准：2 个 Event、4 个 Transition、4 个 Notification（3 digest、1 immediate）。
重跑相同批次后这些计数保持不变；刷新和重启后结果保持。
另用一个独立用例覆盖 low/record_only，以及 digest_only 偏好改变通知动作。
演示页面用“第一批信息/后续进展/补丁与冲突”展示批次，不需要暴露内部对象名。

MockProvider 使用独立、简单的内容规则实现上述语义，不能导入 expected 文件、按答案标签直接返回，或按 evidence_id 查表背答案。
Mock 检查验证工程链路，不代表模型能力或 Benchmark 成绩；Qwen 输出不强求每次完全一致，应另记模型结果。
Runtime 只接收运行时 Fixture 目录和显式 Context，不提供任意文件读取工具，不导入 evaluation，也不将预期结果加入 Prompt。
目录分离不是进程级沙箱；本地实现只承诺没有这条数据访问路径，不宣称能防御任意恶意本机代码。

## 8. Qwen 适配器

接口示意：`AssessmentProvider.assess(topic, current_events, evidence) -> Assessment`。
运行时上下文包含当前 Evidence、批准来源元数据、当前 EventState 和规则；不提供整份数据库、课程资料或评测答案。

配置至少包含：

```text
MODEL_PROVIDER=mock
QWEN_API_KEY=
QWEN_BASE_URL=
QWEN_MODEL=
MODEL_TIMEOUT_SECONDS=30
MODEL_MAX_ATTEMPTS=2
```

实际接入时先核对 Qwen 官方文档和用户可用服务，确认地域、地址、模型标识、鉴权与结构化输出能力，不凭空假定兼容端点。
密钥由用户在本机后端环境设置，不要求粘贴到聊天；`.env` 加入忽略，`.env.example` 只有占位值。
从最少字段 Prompt 开始，Pydantic 严格校验输出；处理 JSON 错误、引用错误和超时。
Run 记录 provider、model（可用时）、prompt_version、耗时、attempt；可获得时记录 token 用量，不记录凭证或模型私有思维链。
UI 清晰显示 Mock / Qwen 和模拟数据来源；配置缺失时直接返回可理解的错误，不创建永远 pending 的 Run。
真实调用检查为显式命令，普通测试与健康检查不得自动使用付费模型。

## 9. 分阶段任务包

严格按依赖实施，每包交付可检查结果，更新 docs/progress.md。日期是目标，不得因日期到来就跳过前一包的失败项。

### T0：工作区确认与契约（11/09）

- 检查已有文件、运行环境、适用指令；创建独立工程目录。
- 落盘 decisions、data_contracts、api_contracts；将原架构文档复制到工程 docs/architecture-reference.md，标注来源和日期，不修改桌面原件。
- 定义模型、错误、枚举、Provider 与 Connector 接口及样例。
- 验收：样例通过 Schema，跨 Topic Event ID 和无效证据引用可被拒绝；Phase 0 设计可直接用于编码。

### T1：可启动工程（11/10，完成 Phase 0）

- 创建 FastAPI、React/Vite/TS、配置、日志、健康检查、代理和依赖记录。
- 加 README 和 .env.example；提供安装、启动、检查命令。
- 验收：前端访问后端健康检查，前端类型检查和构建通过，无 Key 也能启动。

### T2：持久化与配置流程（11/11）

- 实现 SQLite 表、约束、版本化初始化，Topic/Source Repository 和 API。
- Fixture Source 注册幂等，不在每次启动时重建或清空数据。
- 实现 Topic 创建列表页、来源确认页和配置保存。
- 验收：未批准无法 Run；刷新/重启后 Topic 和批准关系保留。

### T3：Mock 工作流与一致性（11/12）

- 实现 worker、Run API、FixtureConnector、MockProvider、State Manager 和通知规则。
- 实现 Step Trace、事务、处理结果、数据库幂等约束、失败状态和启动中断处理。
- 三批案例从 API 跑通后再做页面美化。
- 验收：2 Event/4 Transition/4 Notification 基准通过；重跑不增业务数据；冲突不覆盖；事务回滚有效。

### T4：网页闭环（11/13）

- Topic Dashboard：目标、批准来源、Run 按钮、批次、模式、运行进度、Event 列表。
- Event 详情：四字段状态、时间线、证据、前后变化、待审核冲突。
- 通知区域：即时、Digest、仅记录。
- Run 详情：步骤顺序、结果、理由、耗时、错误；信息来自实际 API。
- 所有请求提供 loading/empty/error 状态；运行期间禁用重复触发；页面刷新可找到最近 Run。
- 验收：仅通过网页完成三批流程，页面无关键控制台错误。

### T5：Qwen（11/14）

- 实现配置、Provider 适配、Prompt、严格解析和有限重试。
- 用替身响应测试超时、非法 JSON、错误 ID；有本地凭证时执行一次小样本真实调用。
- 验收：页面明确显示实际模式；模型错误可查且不改变状态；无凭证时明确记录真实调用尚未验证。

### T6：验收与交付（11/15）

- 完成关键回归、浏览器演示、重启与安全重跑验证。
- 修复阻塞演示的问题，不在此时增加新依赖或新功能。
- 写 README、演示脚本、acceptance.md、已知限制，保留一份可复现的结果导出。
- 验收：按新环境安装说明启动后，能复现 Mock 基准和网页流程；真实 Qwen 状态如实记录。

## 10. 关键验证清单

测试聚焦行为与失败风险，不为每个 getter 或静态页面编写重复测试。

| 验证 | 必须观察到的结果 |
| --- | --- |
| 未批准来源 | 无法运行；伪造来源 ID 不会读取任意文件 |
| 标准三批案例 | 状态、关联、计数和通知映射正确 |
| 相同输入重跑 | Event/Transition/Notification 不增；Run/Trace 可增 |
| 不同 Topic 使用相同 Evidence | 处理状态独立，不因全局去重漏掉另一 Topic |
| 同 Topic 同时触发 | 一个被接受，另一个返回明确冲突或相同幂等 Run |
| 提交中途注入失败 | 无半条状态历史或漏关联通知，重试只提交一次 |
| Provider 非法输出/不存在 ID | 无状态提交，错误和验证结果可追溯 |
| 抓取失败与空批次 | 前者错误，后者正常，无混淆 |
| 冲突和 unresolved | 保留 Evidence 与理由，不覆盖或强行建 Event |
| 进程重启 | 数据保留，遗留运行明确 interrupted，可安全重新 Run |
| 网页操作 | 创建、批准、运行、查看状态/证据/通知/Trace 全部可用 |
| 前端检查 | 类型检查、构建、关键浏览器流程通过 |
| 凭证与答案隔离 | 前端构建/响应不含 Key，Runtime 不加载 expected 数据 |

README 至少给出 Windows PowerShell 环境下验证过的后端虚拟环境、依赖安装、数据库初始化、前后端启动、pytest、前端检查与构建命令。
不要填入未经执行验证的“全部通过”。无法执行的检查写清原因与剩余步骤。

## 11. 进度报告和范围收敛

每完成一个任务包记录：改动文件、实际验证命令与结果、剩余风险、下一包任务。
对用户先用短而简单的英文说明结果，再用中文解释关键点；代码之外的解释避免一次灌输太多概念。
只把真实阻塞项交给用户：凭证、无法访问的官方服务配置、需求冲突等。常规内部实现选择自行决定并记录。

落后于排期时优先砍：额外样式、模型生成 Source 推荐、复杂 Digest 文案、附加过滤器。
不能砍：批准 Source 限制、状态校验、数据持久化、幂等、Trace、错误透明和 Mock/Qwen 标识。
没有 Qwen 凭证不阻塞 Mock MVP；但必须在验收中把真实模型集成标为未验证，不能谎称整个模型环节已完成。

## 12. 可直接交给 Code Agent 的启动指令

> 阅读本文件、phase0-plan.md 和原始架构文档，检查工作区与适用 AGENTS.md。按 T0→T6 实施本地 MVP，默认业务目录为 event-tracker/，保留全部课程资料和现有练习。先完成并验证契约与可启动骨架，再推进 Mock API 闭环、网页和 Qwen。不要只再次返回计划；按任务包实际编码并执行必要检查，每包记录可验证结果。对于未确认的演示领域使用本文明确标注的虚构默认案例，不将其称为用户已确认需求。Qwen 凭证缺失时继续独立工作，准确报告实际调用状态。完成后给出启动方法、验收结果和剩余限制。
