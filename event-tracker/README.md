# TraceRadar V2 使用手册

TraceRadar 是 COMP7608 Large Language Models 课程的本地新闻追踪工作台。它从批准的信息源收集报道，用模型识别独立事件和状态变化，保留证据、时间线、人工审核与运行记录。首页雷达展示近期事件和待执行的定时检查。

## 启动 V2

Windows 从仓库根目录安装依赖和构建前端。项目使用 Python 3.11+、Node.js 22+、FastAPI、React、Vite 和 SQLite。

```powershell
python -m venv .\event-tracker\backend\.venv
.\event-tracker\backend\.venv\Scripts\python.exe -m pip install -r .\event-tracker\backend\requirements.lock.txt
Push-Location .\event-tracker\frontend
npm ci
npm run build
Pop-Location
.\Start-TraceRadar-V2.cmd
```

打开 [工作台](http://127.0.0.1:8629/) 或 [交互式 API 文档](http://127.0.0.1:8629/docs)。再次打开时可以直接双击启动脚本。启动后可以关闭启动窗口，服务和定时检查会继续运行。运行 `Stop-TraceRadar-V2.cmd` 或重启电脑会停止后台服务。重启后再次运行启动脚本。

V2 数据库为 `event-tracker/data/frontend-v2-preview.db`，日志为同目录的 `local-workspace-8629.log`。原版启动脚本 `Start-TraceRadar.cmd` 仍可启动 5173 端口的独立工作区。修改前端后重新构建，修改后端后重启对应服务。

### 开发模式

从仓库根目录打开两个 PowerShell 窗口。Vite 的 `/api` 代理指向 8000，开发前端使用 5173。如果该端口正在运行原版工作区，先停止对应服务。

```powershell
# 窗口一
cd event-tracker\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 窗口二
cd event-tracker\frontend
npm ci
npm run dev
```

开发后端默认使用 `event-tracker/data/tracker.db`。本地工作区使用一个 API 进程和一个运行队列。

## 从创建主题到查看报告

1. 在首页新建主题，输入清晰的目标和对象，按优先级选最多三个分类。第一类决定胶卷封面。新建表单和主题页的「编辑标题」都可添加排除规则。首页按类型筛选后，可用「主题示例」预填完整表单，并查看测试备注。[主题示例与维护格式](docs/topic-examples.zh-CN.md)
2. 在「信息源」添加或选择 RSS、联网搜索、监测网页或手动录入，然后点击「保存批准来源」。网页监测第一次保存基线，之后记录变化。
3. 在主题概览点击「开始检查」。确认结果后，从事件档案查看当前结论、时间线、前后对比和支持证据。有冲突的判断会进入「待审核」。
4. 在「证据库」组合关键词与已处理、已排除、待审核、重复、未处理或失败等状态过滤。在「追踪报告」查看事件与进展分布，在「运行记录」查看来源、流程、失败原因和 Agent 调用记录。可从有错误的历史运行续跑未完成的证据。
5. 在侧栏可置顶或拖动主题顺序，首页胶卷同步更新。右上角切换界面语言、主题色和时区。模型参考翻译可通过工作台开关或证据详情请求，来源原文仍可查看。

### 选择检查方式

| 方式 | 适合场景 | 数据与设置 |
|---|---|---|
| 模拟来源 | 无 API Key 的管线验收 | 内置 ExampleSoft 规则与固定输入，适合验证读取、去重、状态流和审核 |
| 测试离线回放 | 课堂展示、重复测时间线 | 通过侧栏建立独立回放主题，选择语料和截止时间。LBJ、NBA、香港麦当劳等语料会按新闻发布时间分阶段读入 |
| 真实模型分析 | 新主题、公开消息、实况检查 | 配好模型和批准来源，手动发起。先验证一次结果，再设置定时 |
| Agent 模式 | 同一事件跨多轮演进、资料有冲突 | 在真实模型检查里启用。Agent 可按需查看当前主题的证据、评估指导和最多三条历史状态变化 |
| 定时更新模式 | 持续追踪有稳定来源的主题 | 使用同一真实模型和实时来源。可独立决定定时检查是否使用 Agent |

## 定时更新

主题概览的检查卡片提供「本次使用定时更新模式」，其下也可直接打开「管理更新频率」。保存前配置真实模型，并至少批准一个 RSS、搜索或监测网页来源。定时检查按**触发时的已批准实时来源**运行。

- **指定时间点**可设置一至三个不同的未来时间，每个时间点触发一次。
- **按固定频率重复**支持 1 分钟至 30 天，可立即开始或从指定未来时间开始。选择立即开始会排入一次检查，此后的计划按选定间隔推进。
- 时间按右上角时区输入和显示，数据库统一存 UTC。切换显示时区会换算同一时刻。
- 关闭浏览器后任务继续。服务停止期间的多个到期时间会在重启后合并为一次补查，未来时间点保留。已有同主题检查时会等待，该主题不会并行运行。缺少实时来源或模型配置会在主题状态中提示。
- 首页「待定时更新」和主题胶卷显示下一次检查，主题标题下方显示是否启用及下次时间。「运行记录」里的定时检查带有计划时间。关闭定时更新只取消后续计划，历史记录可继续查看。

## 站内提醒

在「信息源 → 提醒偏好」选策略。通知从已提交的状态变化生成。

| 偏好 | 工作台内的行为 |
|---|---|
| 重大进展即时提醒 | 重大进展显示站内横幅，并进入收件箱。常规进展进入更新汇总，次要变化保留在事件历史 |
| 全部进入更新汇总 | 启用后的所有已记录进展进入汇总，重大进展也按汇总展示 |

右上角铃铛显示全站未读数。主题概览的「变化与提醒」显示当前主题的提醒、结论和时间，待审核入口可单独进入。收件箱可筛选即时提醒与更新汇总，点开提醒查看相应事件并标记已读，也可将全站提醒一次性标为已读。已读状态保存在 SQLite。页面打开时约每 5 秒更新，页面关闭期间的提醒会留在收件箱。

## 配置模型与实时来源

在「模型配置」填写兼容 Chat Completions 的 HTTPS API 基础地址（或本机 Ollama 回环地址）、API Key 和模型 ID。可以先读取可用模型再保存并测试。API 中沿用 `qwen` 这一通用提供方标识，实际请求发送到已配置的模型服务。真实模型检查、Agent、翻译和定时检查会消耗该服务的额度。

搜索设置支持 `auto`、`bocha`、`tavily`、`free`。Google News 关键词 RSS 超时后可尝试相同关键词的搜索后备，具体结果见运行记录。实时来源可用性取决于本地网络和供应商。环境变量见 [.env.example](.env.example)。

模型、Bocha、Tavily 和网页抓取的 Jina API Key 用 AES-256-GCM 保存在 Git 忽略的 `.env` 中。Windows 用当前账号的 DPAPI 保护加密密钥。其他系统可从进程环境提供 API Key，或把稳定的 Base64 编码 32 字节随机密钥注入进程环境的 `TRACERADAR_SECRETS_KEY`，以启用加密保存。迁移到另一台 Windows 电脑时重新输入密钥。

## 共享主题配置

分支中的回放语料与默认目标随代码分发。用户添加的主题保存在其 SQLite 数据库中。以下命令从仓库根目录执行，导出时请选用队友实际使用的数据库文件。

```powershell
python .\event-tracker\scripts\topic_settings.py export --database .\event-tracker\data\tracker.db --output .\event-tracker\data\teammate-topics.json
python .\event-tracker\scripts\topic_settings.py import --database .\event-tracker\data\frontend-v2-preview.db --input .\event-tracker\data\teammate-topics.json --dry-run
python .\event-tracker\scripts\topic_settings.py import --database .\event-tracker\data\frontend-v2-preview.db --input .\event-tracker\data\teammate-topics.json
```

先启动目标工作区初始化数据库，导入后刷新。同 ID 主题保留本地设置。导出包括主题名称、目标、类型、排除规则、提醒偏好和批准来源。运行、证据、已读状态、定时计划和模型密钥留在原工作区。分享前检查自定义来源的 URL 或配置是否含私有访问参数。

## 验证与项目结构

```powershell
# 后端测试，从 event-tracker/backend
.\.venv\Scripts\python.exe -m pytest -q

# 前端，从 event-tracker/frontend
npm run check
npm run build
```

普通测试用模拟数据和模型传输，不需要调用付费服务。API 演示脚本 `scripts/demo.py` 在运行中的开发工作区创建模拟主题。基准脚本 `scripts/run_benchmark.py` 从冻结样本生成报告；`--provider mock` 检查管线，`--provider qwen` 使用配置的模型评估语义结果。分类型的手动、离线回放和定时验收见 [主题示例与测试规则](docs/topic-examples.zh-CN.md)。

| 路径 | 职责 |
|---|---|
| `backend/app/main.py` | FastAPI 路由和错误响应 |
| `backend/app/repositories.py` | SQLite、事件状态、收件箱和定时计划事务 |
| `backend/app/scheduler.py` | 将已到期的检查交给常规运行队列 |
| `backend/app/orchestration.py` | 来源读取、去重、分析、状态提交和运行记录 |
| `backend/app/notification_policy.py` | 即时提醒、汇总和仅记录策略 |
| `backend/app/connectors.py`、`web_connectors.py` | RSS、搜索、网页、离线回放与手动来源 |
| `backend/app/llm.py`、`secret_storage.py` | 模型与 Agent 工具、API Key 加密 |
| `frontend/src` | React 工作台、胶卷、雷达、提醒和计划编辑 |
| `fixtures`、`evaluation` | 回放与演示输入，以及独立评估材料 |

此工作区按本机单用户使用设计。站内提醒通过工作台显示。结论可追溯到证据，来源可信度与模型判断仍需要人工核验。
