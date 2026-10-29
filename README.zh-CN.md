# 蜂伴 BeeGuide

面向养蜂新手的知识与养护 Agent。独立于此前 YOLO / DeepBee 项目，使用 LangGraph、FastAPI、Vue 3 / TypeScript 和持久化数据库。

核心流程：**咨询 → 读取档案与历史 → 必要追问 → 收集资料/天气 → 建议草稿 → 复杂场景审查与有限修改 → 确认保存 → 执行反馈。**

默认采用轻量多 Agent：协调员通过原生 tools/tool_calls 收集只读信息，养蜂顾问生成草稿，审查员检查复杂建议。简单知识问答跳过工具协调与审查；无密钥时运行明确标注的本地资料演示。三个角色共用一个模型，通过独立提示词、上下文、输出约束和权限分工。

## 先体验

Windows 双击 `Start-BeeGuide.cmd`，或在项目目录执行：

```powershell
.\run.ps1
```

打开 http://127.0.0.1:8008 。默认演示账号：`demo@beeguide.local`；第二个账号：`second@beeguide.local`。密码均为 `bee2026`。两者资料分开保存。

新电脑需 Python 3.12+ 和 Node.js 22，然后执行 `./run.ps1 -Setup -Build`。此脚本启动隐藏的本地服务，日志与 PID 保存在 data。`./stop.ps1` 只停止该脚本创建、并经路径和命令核对的进程。

若 PowerShell 策略不允许启动脚本，可在 backend 目录直接执行 `../.venv/Scripts/python.exe -m app.serve`，无需修改系统执行策略。

## 当前运行模式

| 模式 | 实际行为 |
|---|---|
| 未配置密钥 | **本地资料演示**：规则路由、真实 LangGraph、中断/恢复、本地词频检索、资料整理、真实持久化。没有大模型推理 |
| 配置文本模型，multi | 简单问题直接回答；复杂问题使用原生工具循环、顾问草稿、独立审查与最多一次修改；失败明确降级 |
| AGENT_ARCHITECTURE=single | 固定检索/天气流程基线；保留模型生成、追问与确认，用于对照 |
| 开启 Embedding | embedding API + BM25 + RRF；SQLite 用向量余弦计算，PostgreSQL 用 pgvector 查询 |

本地 TF-IDF 是词频向量，不是语义 embedding。2026-10-10 扩充到 33 个知识片段后，48 条内部检索题上 BM25、TF-IDF 和融合的平均 Recall@5 均为 0.9792；默认继续使用 TF-IDF。原 24 题单独为 0.9583，新增国内 24 题为 1.0，详见验证记录。可以通过 LOCAL_RETRIEVAL_STRATEGY 比较三种方案；内部小样本不能推断一般效果。页面、运行记录和评测不把本地演示说成真实模型。

## 配置模型

将 `.env.example` 复制为项目根目录 `.env`，在本机编辑以下配置，然后停止并重新启动后端：

```dotenv
MODEL_API_KEY=你的密钥
MODEL_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
MODEL_NAME=qwen-plus
EMBEDDING_ENABLED=false
AGENT_ARCHITECTURE=multi
```

模型名以你的服务账号实际可用型号为准。不要在聊天中发送密钥；`.env` 已忽略。默认不自动开启 embedding，以便明确控制调用用量。

本机已验证 DeepSeek 官方 https://api.deepseek.com/v1 与 deepseek-flash。DeepSeek 专用的 thinking 参数默认关闭，以便短结构化角色输出完整结果；其他兼容供应商不发送该参数。AGENT_MAX_TOOL_ROUNDS 默认 3，AGENT_MAX_TOOL_CALLS 默认 6；审查后最多修改一次，再审查一次。工具、模型或审查失败都记录降级，未通过审查的任务不提供保存入口。

开启语义检索需要设置 `EMBEDDING_ENABLED=true`、正确模型和维度。首次检索会为尚未索引的资料生成向量并缓存，属于真实 API 调用。更换模型会重新索引；更改 PostgreSQL 向量维度还需要相应 schema 迁移，不能只改环境变量。

所有聊天模型调用（路由、协调、顾问、审查）均经过上下文预算检查。`MODEL_CONTEXT_WINDOW` 默认 32768，`MODEL_MAX_OUTPUT_TOKENS` 默认 4096，另预留 `CONTEXT_SAFETY_TOKENS=1024`；应按供应商实际模型设置。路由最多输出 256，协调与审查最多 1800，并受全局输出上限约束。供应商分词器不统一，当前用 UTF-8 字节数加协议开销作保守估算，不等同于精确 Token 计数。

超预算时仅筛选发给模型的副本：移除历史助手回答和重复的当前问题，按当前问题相关性筛选历史任务，省略任务仍保留 ID、标题和完成状态；知识资料整条保留或省略，审查/修改所引用的完整资料不得丢弃。`CONTEXT_TOOL_RESULT_TOKENS` 默认 4096，限制单条工具消息；工具返回与请求的 ID 配对保持完整。问题、澄清、档案、过去用户提出的要求、草稿和审查意见不截断。必要内容仍无法容纳时，请求在 HTTP 调用前终止并走现有降级路径。原始数据库、图状态和完整资料不被裁剪。

运行记录中每次模型调用的 `usage.context_budget` 包含输入估算、输出预留和省略计数。此功能是确定性的上下文筛选，尚未实现自动摘要模型或全部个人记忆的语义检索；checkpoint 的保存策略也未在此次修改中调整。

## 三个演示场景

1. 点击“从零开始养蜂”，补充“杭州，准备开始”，确认保存任务。刷新页面，记录仍在。
2. 完善档案为“杭州 / 已有蜂群”，询问“明天适合检查蜂箱吗？”查看天气卡、资料和明确的适用条件。
3. 在养护计划中选择任务，安排日期、记录执行反馈。回到聊天输入“我做完了上次的准备，下一步是什么？”查看历史被读取。

打开“运行记录”可以看到工具、真实耗时、引用和调用用量。只有模型实际返回文本时才记录首 token 时间。

## 技术与目录

```text
backend/app/
  main.py        API、账号所有权、SSE、会话并发控制
  agent.py       LangGraph 节点、中断、草稿确认与保存
  collaboration.py 协调员只读工具注册、校验、去重、预算与失败处理
  rag.py         中文分词、BM25、TF-IDF / embedding、RRF
  llm.py         兼容模型协议、真实 JSON 流式解析
  weather.py     Open-Meteo 请求、时间和单位、失败状态
  models.py      SQLAlchemy 数据实体与 pgvector 列
  schemas.py     类型化请求、建议和任务结构
  auth.py        演示账号登录、密码派生与 JWT
backend/migrations/   Alembic 初始迁移
frontend/src/         Vue / TypeScript / Pinia 工作台
knowledge/seed.json   资料、来源、小节、地区和版本
knowledge/domestic.json 国内新增资料片段
knowledge/sources.json  国内原文来源登记与适用条件
eval/cases.json      原 24 检索题 + 12 规则路由题
eval/domestic.cases.json 国内新增 24 检索题
docs/                架构、阅读顺序、面试讲解和验证记录
```

代码走读见 `docs/CODE_TOUR.zh-CN.md`，多 Agent 设计见 `docs/MULTI_AGENT.zh-CN.md`，实测记录见 `docs/VERIFICATION.zh-CN.md`。

## 开发与验证

```powershell
# 后端，在 backend 目录
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8008
..\.venv\Scripts\python.exe -m pytest -q tests
# 前端，在 frontend 目录
npm ci
npm run dev
npm run build
# 项目根目录：不调用付费模型的检索/规则路由评测
.venv\Scripts\python.exe scripts\evaluate.py
# 显式允许付费模型：在独立临时数据库对照 single / multi
.venv\Scripts\python.exe scripts\evaluate_agents.py --live
```

开发前端在 http://127.0.0.1:5178 ，代理 API 到 8008。生产式本地启动由 FastAPI 直接提供打包后的静态前端，只需一个端口。

多 Agent 设计与学习顺序见 docs/MULTI_AGENT.zh-CN.md。端到端对照题位于 eval/agent.cases.json，真实结果保存在忽略的 eval/results.agents.local.json。四条自编题只验证功能、耗时和用量，不代表独立领域评测或多 Agent 准确率提升。

## PostgreSQL 和部署

默认 SQLite 支持本机直接运行，业务库与 LangGraph checkpoint 分别使用持久文件，均位于 data。

`compose.yaml` 提供 PostgreSQL + pgvector 和应用容器配置，使用同一个 PostgreSQL 存储业务及 LangGraph checkpoints；安装 Docker 后可以 `docker compose up --build`。当前电脑没有 Docker，容器与 PostgreSQL 路径尚未在本机联调，不能视为已验证部署成果。默认绑定 localhost，演示账号和配置需在面向真实用户前更换。

## 扩充知识

启动应用完成迁移后，可导入有权使用的 UTF-8 Markdown/文本：

```powershell
.venv\Scripts\python.exe scripts\import_document.py 资料.md --title 资料标题 --source 来源机构 --region 适用地区 --version 2026-10-06 --url https://真实原文地址
```

导入保留源信息，按段落切块，重复内容/版本不会重复添加。重启应用重建本地索引。扫描 PDF、自动爬取和知识管理后台尚未实现。

当前有 33 个种子知识片段：24 条来自 6 份已核对的国内政府公开或转载文章，7 条来自两份 MSU Extension 页面的中文整理，2 条明确标记的项目咨询/记录说明。国内新增资料保留原文标题、小节、日期、蜂种、地区和季节条件；较早地方文章只整理指定内容，不采纳全文中的用药或诊断结论。内容仍需本地专业复核，项目不宣称已实现病害诊断、精确图像计数或现场养护效果验证。

来源和覆盖范围见 `docs/KNOWLEDGE_CATALOG.zh-CN.md`，现有技术分工见 `docs/TECH_STACK.zh-CN.md`。修改国内资料时，在 domestic.json 关联 sources.json 中的 source_id；启动时校验片段 ID 唯一并幂等导入，不清空用户计划和反馈。
