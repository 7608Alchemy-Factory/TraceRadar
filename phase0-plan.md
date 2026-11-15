# 本地网页 MVP：开发基线与 Phase 0 计划

记录日期：2026-11-09（Asia/Hong_Kong）

依据：用户本轮确认，以及 `C:/Users/86195/Desktop/架构设计.md`。
本文区分用户已确认的选择与建议默认值；不将架构文档中的候选方案自动视为已确认。

详细编码顺序、契约、恢复规则及验收见 [Code Agent 开发实施计划](code-agent-implementation-plan.md)。本文保留为基线摘要。

## 1. 已确认的开发基线

| 项目 | 决定 |
| --- | --- |
| 本版交付 | 本地网页完成主要操作流程 |
| 截止时间 | 本周；排期暂按 2026-11-15 前完成，具体交付时刻尚未指定 |
| 后端 | Python + FastAPI + Pydantic |
| 前端 | React + Vite + TypeScript |
| 数据库 | SQLite，通过 Repository 隔离数据库访问 |
| 工作流 | 显式 Python Workflow |
| 初期运行 | 本地、单用户、手动触发 |
| 通知 | 网页展示通知决策及 Digest；后续再扩展外部投递 |
| 模型服务 | 暂定 Qwen；具体模型、接入地址和预算待接入时确定 |
| 扩展依赖 | 当前不引入 Graphiti / LangGraph |

用户暂承担主要架构搭建；计划按一人完成关键路径安排，不依赖组员先交付模块。

## 2. 本周交付目标（建议验收范围）

本周目标包含 Phase 0 和随后最小 Mock 闭环，不能将整个星期都用于架构设计。

1. 创建 Topic，填写标题与追踪目标。
2. 展示候选 Source，用户确认后保存批准关系。
3. 手动触发 Run，接口快速返回 run_id，网页查询进度。
4. 读取已批准的 Fixture Source，处理固定 Evidence。
5. 判断相关性、关联或创建 Event、校验并提交 Event State。
6. 展示 Event 当前状态、时间线、证据和状态变化历史。
7. 展示 immediate / digest / record_only 决策。
8. 查询 Run Trace，能够追溯证据、判断和状态变化。
9. 重启后保留数据；同一批 Evidence 重跑不重复产生状态变化或通知。

建议本周先使用一个 Cybersecurity 漏洞披露与修复案例；这不是用户已经确认的领域选择。
Fixture 负责保证演示可重复。真实 Source 抓取、定时监控、在线部署与第二领域不进入本周关键路径。
Qwen 通过统一接口接入；Mock 演示与真实模型调用分别标明，不能把 Mock 结果宣称为真实模型输出。

## 3. Phase 0：最多 1–2 天

### 步骤 A：冻结数据契约

交付：共享 Pydantic Schema、JSON 示例、前端类型对应规则。

- Topic、Source、TopicSource、Evidence。
- Event、EventState、Assessment、StateUpdateProposal。
- StateTransition、NotificationDecision、Notification。
- Run、RunStep。

必须约定：ID、UTC 时间、必填字段与空值、状态枚举、错误格式、证据引用、幂等键作用范围。
EventState 保留 current_status、timeline、key_facts、sources 四个基础字段。
published_at 与 fetched_at 分开；sources 中的 Evidence 引用与 source_id 分开定义。

### 步骤 B：冻结模块接口和状态提交边界

交付：输入输出示例、错误与重试规则。

- Connector 只读取 Topic 已批准的 Source。
- Assessment 返回结构化提议，不直接写状态。
- State Manager 是唯一 Event State 提交入口。
- Notification 使用已经提交的 StateTransition。
- Orchestrator 负责步骤、状态、超时、有限重试和 Trace。
- Repository 负责数据库访问；状态提交与对应历史记录应在同一事务内保存。

### 步骤 C：搭建最小工程骨架

交付：backend、frontend、fixtures、evaluation、docs 目录及运行说明。

- 后端启动与 GET /api/health。
- 前端启动并访问健康检查。
- 配置、日志、公共错误、.env.example、依赖版本记录。
- Repository 和模型服务接口；Mock 实现可独立运行。
- Qwen 的具体 SDK 或协议在实际接入前核对官方文档，不预设接入地址或模型名。
- API Key 仅通过本地环境配置，不写入仓库或前端。

### 步骤 D：准备 Phase 1 联调输入

交付：最小 API 契约、固定样例和验收清单。

样例覆盖：新 Event、已有 Event 更新、重复证据、无关证据、冲突证据。
运行时输入与评测预期分离；Agent 不加载 Ground Truth。
确定性验收使用 Mock，Qwen 接入单独验证调用、输出校验和失败行为。

Phase 0 验收：工程能启动；Schema 示例可校验；接口边界明确；下一阶段无需重新发明数据格式。

## 4. 本周排期（建议，可随实际进度调整）

| 日期 | 主要工作 | 完成标志 |
| --- | --- | --- |
| 11/09 | 数据契约、业务规则、API 草案 | 字段和模块边界固定 |
| 11/10 | 工程骨架、配置、健康检查 | 前后端可启动并连通，Phase 0 完成 |
| 11/11 | Topic、Source、SQLite、Run 入口 | 创建与配置可以保存，能触发 Run |
| 11/12 | Fixture 工作流、状态提交、Trace | 后端 Mock 闭环完成 |
| 11/13 | Dashboard、Event 详情、通知展示 | 本地网页完整走通 |
| 11/14 | Qwen 适配与结构化输出校验 | 真实调用可验证；失败明确展示并保留 Trace |
| 11/15 | 重跑、重启、异常验证和演示整理 | 可重复演示并有运行说明 |

## 5. 尚待补充的具体参数

这些参数不阻塞 Phase 0：

- 首个 Demo 的具体 Topic、Source 与重要状态变化示例。
- Qwen 的具体模型标识、接入方式、调用预算与本地凭证配置。
- 本周交付的具体日期和时刻；当前只将周日作为排期上限。

本计划尚未实施业务工程；当前交付为开发决策记录和搭建计划。
