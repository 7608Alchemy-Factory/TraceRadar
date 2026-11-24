# 成员 D：现状、开发路线与逐步验收

后续更新：界面已重做，模型配置、三阶段回放准备、人工采纳/修正和实验导出已接入。当前操作以 [v0.2 工作台指南](workspace-v2.zh-CN.md) 为准；下文保存最初的分支核查与第一版交付背景。

核查日期：2026-11-17（香港）。这是本地实现说明，不代表团队已批准新产品决策。

## 1. 先理解你在做什么

架构设计 §12 把成员 D 定义为 Event Logic / Frontend：Event Association、State Update、Notification Logic、Dashboard。
Proposal §4–5 要求系统维护事件演化、保留证据与冲突历史，并输出可评测的状态与 Trace。
因此 D 的核心链路是：

```text
C 提供规范化 Evidence
  → D 判断相关性、关联已有 Event / 新建 Event / 暂不确定
  → D 生成完整状态提议与证据引用
  → StateManager 校验并通过 B 的 Repository 原子提交
  → 确定性通知策略
  → Dashboard 展示状态、证据、历史和人工审核
  → A 读取结构化产物进行评测
```

不要把一篇文章当成一个事件。例如“某球员转会谈判→达成协议→正式签约”通常是一条生命周期；另一名球员的独立交易可以是新事件。边界最终由模型判断和 A 的标注评测检验。

## 2. 两个远端分支实际完成了什么

核查的远端版本：

- main：`f472965fbc977813bfe4be3a8e429b245426abbf`（崔钦元提交的基础 MVP）。
- feat/data-pipeline-1116：`8e476ba3841b790a9cded590b57ab9c0cfcaa255`（Kystal 提交的数据管线）。
- C 分支相对 main 修改 13 个文件，新增约 1974 行、删除约 33 行；当时尚未合并。
- 本地开发分支：`feat/member-d-event-logic`，基于 C 分支，保留这次数据接入工作。

| 部分 | 已有功能 | 实际边界 |
|---|---|---|
| B 基础后端 | FastAPI、Topic 创建/修改、Source 审批、异步手动 Run、单 Worker、SQLite | 单用户本地 MVP，无定时调度 |
| B 持久化/Harness | Run 配置快照、有序步骤、错误记录、版本锁、原子状态/历史/通知提交、重复运行保护、重启中断标记 | 不是自动断点续跑 |
| 原有 D 占位逻辑 | Assessment 契约、ExampleSoft Mock、模型适配器、状态守卫、通知映射 | 模型提示词与重大进展校验限定 ExampleSoft 补丁 |
| 原有前端 | Topic、Source 勾选、批次 Run、事件四字段、证据、历史、通知、Trace、待审列表 | 目标编辑只有 API；无手动投喂和审核操作；来源类型只声明 fixture |
| C 连接器 | Fixture、Replay、RSS/Atom、Manual 路由，动态 RSS 候选与来源注册 | RSS 检索/订阅能力，不是任意网站爬虫或完整搜索引擎 |
| C 证据处理 | HTML 清理、URL 归一化、UTC 时间、hash/URL/近似去重、可选预筛选、错误隔离 | 近似去重阈值及预筛选召回仍需评测 |
| C 离线语料 | LBJ 30 条；NBA 多事件 18 条；as_of 截止回放；语料校验脚本 | 格式校验不能证明新闻真实性或标签正确 |
| A 评测 | 仓库已有 expected/demo.json 和 mock-demo.json 验收文件 | 不能视作完整 Event Evolution Benchmark 或五项模型指标 |

聊天里“真实模型跑通过”是队友的实验陈述；这个 checkout 中没有可据以复现该实验的已配置凭据或完整评测报告。本轮没有付费调用真实模型。

## 3. 你需要交付的 D 模块

| 交付项 | 验收标准 | 本轮状态 |
|---|---|---|
| 通用事件判断 Prompt | 使用 Topic 目标；解释事件边界；区分传闻/确认/冲突；返回现有 Assessment | 已实现 assessment-v2；语义效果待真实模型评测 |
| 状态更新校验 | 限定同 Topic；引用可用证据；保留既有事实与时间线；检查版本；新增事实有当前证据 | 已实现并回归 |
| 通用重大进展与通知 | 不依赖 ExampleSoft 字符串；重大进展改变状态；偏好可抑制即时通知；无变化不重复提醒 | 已实现并回归 |
| 编辑追踪目标 | 编辑标题/描述；运行时禁止修改；后续 Run 使用新目标；旧 Run 保留快照 | 已实现 |
| 新闻投喂与回放工作台 | 注册/批准来源、手动新闻表单、回放 all/as_of、模型选择、状态和 Trace 展示 | 已实现；复用 C 接口 |
| 审核处理 | 展示冲突依据；记录人工操作；保留原文、模型判断和历史 | 已实现“保留证据并关闭待办”；未实现人工采纳冲突以修改状态 |
| 文档/测试/交接 | 本说明、契约/OpenAPI、新回归用例、原演示仍能运行 | 已补齐 |

模型判断仍可能把不重要变化判成里程碑。校验器证明的是结构、引用和提交约束，不是语义正确性。新事件当前按 routine/digest 处理；若团队希望“新发现的重大事件”也即时通知，应和 A 确认标签规则后扩展契约。

## 4. 第一步：启动并复现已有闭环

本地目录 `D:\Hku\Comp7608\TraceRadar`，依赖已经安装在 backend/.venv 和 frontend/node_modules；不要把它们提交。

常规终端 A（从仓库根目录）：

```powershell
cd event-tracker\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

常规终端 B：

```powershell
cd event-tracker\frontend
npm run dev
```

打开 http://127.0.0.1:5173，API 文档 http://127.0.0.1:8000/docs。本轮环境 Node/npm 未配置到普通 PATH，验证时使用 Codex 自带 Node/npm；在自己的终端使用 npm 前先确认已安装并加入 PATH。

1. 创建默认 ExampleSoft security Topic。
2. 只批准 vendor 和 research 两个模拟来源；不要勾选 RSS。
3. 选择 Mock，依次运行批次 1、2、3，每次等待完成。
4. 应看到 2 个 Event、4 次状态变化、3 条 digest、1 条 immediate、2 条待审核。
5. Event A 为 patched；冲突不覆盖它。重复三批后状态变化与通知数量不增加。

这一步回答“框架是否仍可运行”，无需任何 API key。

## 5. 第二步：验收 D 新增的交互

1. 点“编辑追踪目标”，修改描述并保存，确认页面更新；已有历史保留。
2. 运行中编辑目标和审核操作应被禁止，直接调用 API 也应返回 409。
3. 展开“添加手动来源 / 回放语料 / RSS”，类型选“手动新闻”，填写未使用的来源 ID，例如 my_manual；添加后勾选并保存批准。
4. 展开“手动投喂一条新闻”，输入标题、正文、发布时间。ExampleSoft Mock 可使用 `SIMULATED: ExampleSoft vulnerability A editorial clarification.` 测试低优先级追加。
5. 在一个待审条目中填写关闭原因，点击“保留证据并关闭待办”。待审数量减少；人工处理历史增加；原 Event、历史、通知不变。
6. 再次回放原批次，该待审条目不应重新出现。重复完全相同的审核 API 请求也不增加记录。

审核关闭不是确认传闻为事实。如需纠正事实，当前应追加可验证证据进入正常判断管线；人工批准替换冲突事实还需要单独设计 correction 提议。

## 6. 第三步：真实模型 + C 的 LBJ 回放（尚待执行）

1. 使用团队已经选定的 OpenAI-compatible JSON-mode 模型服务，在工作台的“模型配置”中保存地址、模型名和密钥。Windows 后端会加密保存密钥。
2. 当前 provider 标识仍叫 qwen，以保持现有接口兼容；真实模型由后端配置决定。并非任意服务都一定兼容，需联调确认。
3. 重启后端。新建独立 Topic，例如“LeBron James 转会进展”，目标明确要求区分谈判、传闻、协议与正式签约。
4. 添加“LBJ · 30 条回放语料”，新 ID 如 lbj_replay；只批准该回放来源，使输入可重复。
5. 选“全部语料”，模型选“模型”，填写香港本地截止时间；UI 会转换 UTC。没有截止时间时 UI 拒绝启动回放。
6. 先观察 fixtures/lbj_2026_extended/all.json 的时间分布，再选 3–4 个递增 cutoff；每批检查相关性、事件归属、四字段状态、通知、证据和 Trace。
7. 对每个 cutoff 使用同一模型/配置重复实验，确认同一篇或转载不会重复提醒；查看被过滤/去重条目是否遗漏真正进展。
8. 再用 NBA 多事件语料创建另一个 Topic，验证独立交易分事件、同一交易后续进展继续关联原 Event。

Mock 不会理解 NBA。用 Mock 跑 LBJ 得到“无事件”不能判断数据管线故障。语料的新闻真实性/来源和事件标签还需要 C/A 人工核验，不能当作已经完成的真实新闻 Benchmark。

## 7. 第四步：与 A/B/C 按 4–5 天周期集成

下面是建议节奏，不是已承诺的团队排期：

| 顺序 | 你做什么 | 与谁对齐 | 产物 |
|---|---|---|---|
| 本轮 | 跑通本地 D MVP 与原 Mock 演示 | B | 可审阅 diff、测试、接口说明 |
| 下一个联调周期 | 同一模型按 cutoff 跑 LBJ / NBA，逐条检查错判 | C、A | 数据问题/归属问题/通知问题的清单 |
| 同一周期 | 选少量 Finance/Cybersecurity 样例，按 A 模板标注一部分 | A | 独立于 runtime 的标注，不把答案写进 Prompt |
| 再下一周期 | 根据错例修正 Prompt/规则，必要时做人工采纳冲突流程 | A、B | 原用例不退化的新集成版本 |
| 最后 | 收敛 Dashboard 演示、实验记录和报告 | 全组 | 可复现 Demo 与五项指标报告 |

B 负责最终集成顺序、定时调度和持久化基础；C 负责连接器、清洗、去重；A 负责 Benchmark 模板、Ground Truth、指标与实验分析。D 应重点交付事件行为和用户工作台，同时帮 A 标少量数据。

接口接缝：D 保持 Assessment 既有字段；新增 supported_claims/当前证据的提交约束；数据库仅新增 review_resolutions 表；dashboard 新增 review_resolutions 字段；新增审核关闭端点。B 需在集成时检查这些变化。

## 8. 验证和提交

```powershell
# backend/
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe ..\scripts\validate_corpus.py ..\fixtures\lbj_2026_extended ..\fixtures\nba_2026_multi_event
# frontend/
npm run check
npm run build
```

新增 test_member_d.py 使用可控输出测试体育/收购生命周期、冲突保护、重复运行、通知偏好、审核审计与锁、Prompt 请求格式及无依据更新的拒绝。它不是语义 Benchmark。原工作流/连接器测试继续保留。

本轮结果：后端 41 项通过（原有 22 项 + 新增 19 项）；前端 TypeScript/build 通过；30/18 条语料格式校验通过；真实浏览器已跑通三批模拟新闻、目标编辑、审核关闭、手动来源注册/批准、新闻投喂与 record_only 通知、事件详情。桌面和 390px 手机视口已检查，无横向溢出或页面脚本异常。依赖安装沿用原 lockfile，npm 报告 1 项 high 漏洞，本轮未升级依赖。

远端目前未改动。确认 diff 后可在仓库根目录执行：

```powershell
git status --short
git diff --check
git add event-tracker
git commit -m "feat(d): generalize event updates and add review workspace"
git push -u origin feat/member-d-event-logic
```

提交前核对本地 .env、数据库、依赖均被忽略。先让 B 审阅 D 相对 C 的增量；若 C 分支仍未合入 main，应先集成 C 或明确堆叠 PR 的 base，避免把 C 的代码误算成 D 的贡献。不要把未做的真实模型评测写成已完成。
