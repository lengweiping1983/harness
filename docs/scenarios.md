# 场景迁移：办公 / 开发 / 设计

本文记录把 WorkBuddy 的三个典型场景——**定时任务（办公）、日常开发（开发）、网站设计（设计）**——迁移到 Harness 的分析与落地结果。

分析按 **REA** 三段展开：

- **R（Requirement）**：场景里用户真正要的结果是什么，哪些是硬要求。
- **E（Execution）**：要达成结果，运行时必须提供哪些能力。
- **A（Adaptation）**：Harness 已有什么、缺什么、怎么补，以及明确不做什麼。

总原则：**不重复已有功能**。Harness 的底层（工具、计划链、团队、技能、钩子、权限、记忆、验收）已经完备，缺的是场景入口层与几处表达力缺口，因此全部改动都落在缺口上。

---

## 〇、REA 证据：本地 WorkBuddy 的真实结构

下面三节的 R/E/A 以对本地 WorkBuddy（`resources/app.asar`，182 MB）的 REA 静态重建为事实基础，而不是只看界面推断。

- **内核同源**：应用内嵌 `genie/agent-harness/`，含 `bin/agent-harness.js`、`cli/dist-server/codebuddy.js` 与 `lib/skills/{handlers,rpcs,schemas,service}`。也就是说 WorkBuddy 与本次迁移的 harness **共用同一套 agent 内核**，差别在内核之上的产品外壳（技能、插件、连接器）。
- **能力以技能与工作流承载**：内置能力落在 `.../skills/<name>`（`miora-*` 系列、`mini-program-development`、`sites`、`skill-creator` 等）与 `cli/builtin/workflows/*.workflow.js`；办公能力「深度研究」就是 `deep-research.workflow.js`。
- **三场景的真实分工**（REA 产物中的标识符命中次数）：
  - 办公 → `automation`（192 次）：自动化与调度子系统。
  - 设计 → `ardot`（143 次，设计画布）+ `miora`（231 次，图像 / 视频生成）。
  - 开发 → 无独立引擎，直接由技能体系承载。
- **前端形态**：Rspack/Webpack 代码分割——`cli/dist/codebuddy-lite-wb.mjs` 为主包，`cli/dist/lazy-lite-wb/<id>.<hash>.mjs`（20 余个）加 `manifest.js` 为懒加载分块。场景与能力的切换靠分块懒加载完成，**不是三套独立页面**。

> **口径与局限（必须记录）**：REA 共编目 7190 个文件，受单文件 300 秒上限约束，`cli/dist/codebuddy-lite-wb.mjs` 超时中断，故上述结论取自已完成部分的结构事实，且 REA 自身标注了「图覆盖不完整，不可用事实必须显式存疑」。产物中未出现「定时任务 / 日常开发 / 网站设计」等中文文案串——REA 保留的是标识符而非任意 UI 文案，因此本节以结构与标识符命中为准；**界面层事实由三张场景截图提供**（三者为同一欢迎首页在「场景 → 能力」两组选中态下的样子）。

---

## 一、办公：定时任务

### R：用户要什么

在对话里用自然语言说"下周一早上提醒我……""每月 1 号汇总一次"，然后**忘掉它**——到点自动执行，结果回到对话里。硬要求：一次性与循环都要支持；循环要覆盖真实办公周期（每天 / 每周 / 每月 / 每年 / 每隔 N 分钟）；任务要能查、能改、能停；执行失败了不能静默。

### E：运行时必须提供

自然语言建任务 → 调度表达力 → 持久化与并发保护 → 到点驱动一次真实会话 → 结果回推 → 生命周期管理（查 / 改 / 停 / 删）→ 执行历史。

### A：Harness 的适配

| 环节 | 迁移前 | 迁移后 |
|---|---|---|
| 对话内创建 | 已有 `schedule_automation` | 不变 |
| 调度表达力 | 仅 once / interval / daily / weekly，**无每月每年** | **新增 `monthly`（可多月多日）与 `yearly`（可多月多日）** |
| 持久化与并发 | 已有：SQLite 租约、并发上限 3、失败 3 次自动停用、忙会话避让、错过的周期合并 | 不变 |
| 到点执行 | 已有：驱动一次完整 agent run，结果以 `source='automation'` 回到会话 | 不变 |
| 生命周期 | 对话内只能创建 / 列出 / 删除；改与暂停只能去设置页 | **新增 `update_automation`（改名称、正文、计划、启用状态）** |
| 历史与预览 | 已有：运行记录、未读标记、接下来 5 次触发预览 | 不变 |

落地点：

- `harness/app/schedules.py`：`validate_schedule` 接受 `monthly` / `yearly`；`_next_calendar_due` 按月推进日历，**当月不存在的日期（如 2 月 31 日）直接跳过**，沿用原有的夏令时处理（跳过不存在的本地时刻，歧义时刻只触发一次）。
- `harness/tools/builtin/automation_tools.py`：新增 `update_automation`；只改传入字段，其余沿用原值；**身份不匹配（其他对话的任务）不写入**。
- `harness/prompt/tool_policies.py`：新工具纳入同一条 `policy.automation` 策略。
- `web/src/components/overlays/AutomationsPage.tsx`、`web/src/lib/automations.ts`：编辑器新增每月 / 每年类型（多选日期、月份）与对应日程文案。
- 双语资源与 `docs/api.md` 同步。

明确不做：

- **不做进程外调度**（cron / launchd / 任务计划）。Harness 是本地框架，调度器随进程存活，这是设计约束而非缺陷；启动时也不补跑错过的窗口——周期任务合并为一次执行，是既有的、有意的语义。
- **不做连接器 / 专家绑定**。Harness 的自动化模型只绑定会话（会话自带工作目录），引入第二套绑定维度会与现有归属校验冲突。

---

## 二、开发：日常开发

### R：用户要什么

一句话进入编码会话，然后按项目习惯推进：上手文档要能照着跑起来、改动提交前要过一遍审查、技术文档要跟得上代码。硬要求：结论必须基于真实代码，不能凭记忆生成；产出要能直接交付。

### E：运行时必须提供

编码工具链 → 计划到执行的编排 → 事实取证（读文件 / 搜索 / 定义跳转 / git）→ 可复用的工作方法 → 交付。

### A：Harness 的适配

底层**全部已有**：`read/write/edit/glob/grep`、`bash/pwsh`、`terminal_*`、`lsp`、`run_code`、`todo_write`、`create_goal`、`exit_plan_mode`、`project_*` 任务板与证据化验收、`spawn_agent` 团队、三层记忆、钩子与权限、以及 `present` 交付。

缺的是**成品化的工作方法**——同一套动作每次都要模型临时拼。补法不是新造工具，而是把方法固化为**系统技能**（`~/.harness/skills/`），由 `skill(name=...)` 或 `/skill` 加载：

| 技能 | 覆盖 | 关键约束（写在技能里） |
|---|---|---|
| `project-onboarding` | README / 上手文档 | 先 `glob`/`grep`/`read` 摸真实结构；命令必须可验证；已有文档在其基础上修订，不另起炉灶 |
| `code-review` | 变更审查 | 范围取当前未提交改动；**分级输出**（阻断 / 建议 / 提示）；每条问题必须给出位置、原因、修法；推测须标注 |
| `tech-docs` | 接口 / 架构 / 操作 / 变更文档 | 事实来源是代码与协议文件；长文档先用 `todo_write` 列大纲，多模块可 `spawn_agent` 并行核实 |
| `website-design` | 网站设计（见下节） | — |

明确不做：

- **不新增"开发模式"入口**。CLI `--cwd <项目> "…"`、工作台、`/plan`、`/skill` 已经覆盖进入方式；再加一层模式只是换个名字。
- **不新增审查 / 文档工具**。取证能力（`lsp`、`grep`、`git`）与验收链路（`project_*`、`verification_policy`）已够用，重复造一套只会分散事实来源。

---

## 三、设计：网站设计

### R：用户要什么

要一个**能打开、视觉完整**的网站，而不是一堆代码片段。硬要求：先对齐需求与风格再写代码；单页与多页都要能预览；交付时能直接看到效果。

### E：运行时必须提供

需求澄清 → 信息架构 → 视觉决策 → 生成静态文件 → **预览** → 迭代 → 交付。

### A：Harness 的适配

| 环节 | 迁移前 | 迁移后 |
|---|---|---|
| 澄清 / 架构 / 视觉 | 靠模型临场发挥 | **固化为 `website-design` 技能**：先问清类型、受众、板块、风格；先定配色、字体层级、间距与圆角再写代码；响应式、中文排版、可访问性、零外网依赖写进红线 |
| 对话内视觉确认 | 已有 `show_widget`（HTML/SVG 沙箱渲染 + `mockup` 规范） | 技能里明确：首屏与核心板块先 `show_widget` 确认方向，再落成文件 |
| **文件预览** | `text/html` 被强制 `attachment` 下载，侧栏把 HTML 当源码显示 | **网页默认以渲染结果打开**，可切回源码 |
| **多文件站点预览** | 无 | **新增站点预览路由**，页面里的相对样式 / 脚本 / 图片按目录结构解析回工作区文件 |
| 交付 | 已有 `present` + 交付物卡片 | 不变 |

落地点：

- `harness/web/api/workspace.py`：
  - `/api/v1/file` 对 HTML 内联返回，并加 `Content-Security-Policy: sandbox allow-scripts`——**脚本能跑，但页面被剥夺同源身份，拿不到本机接口**。`download=1` 仍是附件，不带该头。
  - 新增 `/api/v1/preview/{folder}/{entry}`（工作区根用 `__root__`）。走路径形态而不是查询参数，是为了让页面里的 `styles.css` 解析成 `/api/v1/preview/site/styles.css` 而不是 `/api/v1/styles.css`。站点目录内的文件（含 css / js / 图片）都可取；**跳出工作区根一律 403**。
- `web/src/lib/fileLinks.ts`：`previewUrl()` 拼路径形态地址、`isHtmlPath()` 判定网页。
- `web/src/components/inspector/FilePreview.tsx`：网页默认渲染，复用"查看源代码 / 查看预览"切换。

明确不做：

- **不做 AI 矢量设计画布**。那是独立的产品形态（图层 / 组件 / 变量的文档模型 + 编辑器），不是"补个工具"能解决的，且与 Harness"代码即产物"的定位重复。
- **不做图片 / 视频素材生成**，也**不补二进制写文件**。`write` 只写 UTF-8 是既有约束；技能里改为用纯色块、渐变、SVG 与 `show_widget` 示意图替代位图素材——这条路径今天就能跑通，且不引入新的二进制面。
- **不做站点发布 / 在线链接**。涉及托管与公网暴露，是独立的基础设施议题。需要真实服务器行为时，技能引导用 `terminal_open` + `terminal_send` 起本地静态服务，工作台会把终端输出里的本地地址显示为预览入口。

---

## 四、开始页：场景入口

三个场景最后需要一个**用户看得见的入口**。WorkBuddy 的入口是**两级**的——一级场景（办公 / 开发 / 设计）配二级能力（定时任务、日常开发、网站设计等），两级共用一个输入框、一个模型选择器、一条工作空间与权限条，切换的只是被预置的能力提示与技能；能力本身又由技能与工作流承载（见第〇节）。Harness 按「每场景各取一个能力」的口径落三张卡，并在卡面保留这一两级语义。

落点开始页（空对话欢迎区）：`web/src/components/chat/ConversationScenarios.tsx` 在标题与输入框之间渲染三张场景卡，每张以**场景标签 + 能力名 + 一句话说明**呈现——「办公 · 定时任务」「开发 · 日常开发」「设计 · 网站设计」。点击沿用推荐任务的同一套规则：只填入输入框草稿（已有输入、附件或正在创建会话时不覆盖用户状态），由用户编辑后发送。

- 定时任务卡填入自然语言前缀（"帮我创建一个定时任务："），交给 `schedule_automation`；
- 日常开发卡填入 `/skill project-onboarding `，网站设计卡填入 `/skill website-design `，走服务端 `/skill` 斜杠命令把技能正文注入为有效输入——开始页因此同时成为系统技能的可发现入口，而不是平行于技能体系的另一套入口。

小窗口（高度 ≤ 640px）与推荐任务一样隐藏入口卡、直接打开输入区（`web/src/styles.css` 既有媒体查询扩展）。

---

## 五、验证

改动按仓库约定做了回归（不重复跑完整套，只跑相关域）：

- 后端：`tests/test_schedule_current_contract.py`（新增每月 / 每年的契约与持久化）、`tests/test_automation_tool.py`（`update_automation` 的归属校验与改计划后重算到期）、`tests/test_system_skills.py`（四个系统技能可被解析与注册）、`tests/test_workspace_html_preview.py`（沙箱头、相对资源解析、越界拒绝），以及自动化归属 / 租约 / 创建身份、提示词策略与资源一致性等既有回归。
- 前端：`web/src/lib/__tests__/fileLinks.test.ts`（预览地址与网页判定）、`presentationLanguage.test.tsx`（每月 / 每年日程文案双语）、`automationCreation.test.ts`、`conversationScenarios.test.tsx`（场景卡双语渲染、**场景标签渲染**、填草稿与防覆盖）、`web/tests/static-copy-smoke.mjs`（欢迎区场景卡文案纳入浏览器冒烟）。
- 同步与静态检查：`scripts/generate_i18n.py`、`scripts/generate_protocol.py`、`scripts/lint.py`、`npm --prefix web run typecheck`。新增路由会进入生成的 `docs/openapi-v1.json`。

## 六、边界一览

| 场景 | 这次补的 | 仍然不做（及原因） |
|---|---|---|
| 定时任务 | 每月 / 每年调度、对话内改任务 | 进程外调度与启动补跑（本地框架约束）；连接器 / 专家绑定（与会话归属模型冲突） |
| 日常开发 | 上手文档、代码审查、技术文档三个系统技能 | 开发模式入口（已有等价入口）；新的审查 / 文档工具（已有取证与验收链路） |
| 网站设计 | 网页预览与站点相对资源解析、`website-design` 技能 | 矢量设计画布（独立产品形态）；图片 / 视频生成与二进制写入（既有约束，技能内以 SVG 替代）；站点发布（独立基础设施议题） |
| 开始页 | 三个场景入口卡，卡面带**场景标签**（办公 / 开发 / 设计），填草稿，复用推荐任务与 `/skill` 规则 | 独立的模式切换页（开始页卡片已覆盖发现性，多一层模式只是换名字）；不铺 WorkBuddy 的完整二级能力列表（本次每场景只取一个能力，其余统一由技能体系承载，避免与技能目录重复） |
