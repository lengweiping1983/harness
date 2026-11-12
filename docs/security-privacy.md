# 安全、隐私与本地凭据

Harness 的安全边界分为控制面、执行面和展示面。控制面保存会话、权限、审批、模型路由和凭据索引；执行面运行文件、命令、浏览器和 MCP 工具；展示面向模型、浏览器和共享成员投影消息、结果和诊断。三者不能把“模型看得到”当作“用户已经授权”，也不能把“工具完成”当作“敏感数据可以外传”。

本文记录当前源码能证明的行为，并与 OpenAI 公开的 Codex/Agents 安全原则对照。官方建议把凭据作为运行时配置而不是提示词内容，不把密钥写入任务文件、提交物或生成产物，审阅外发的 artifacts；敏感工具调用应在产生副作用的边界进行独立检查，审阅不可用时 fail closed。[OpenAI Sandbox Agents](https://developers.openai.com/api/docs/guides/agents/sandboxes)；[OpenAI Guardrails and human review](https://developers.openai.com/api/docs/guides/agents/guardrails-approvals)。这些公开原则不等于 Harness 已获得操作系统级隔离；本地执行仍受宿主权限限制。

## 威胁模型与不变量

模型输入、用户粘贴文本、附件、网页、MCP 返回、终端输出和工具结果都可能包含提示注入或秘密。它们是数据，不是权限。权限必须来自当前会话、当前成员、当前工具调用和明确的审批记录。未知副作用、未知凭据写入和未知外发结果都必须先回查，不能因为超时、断线、重启或模型声称成功而重放。

以下不变量是产品安全契约：

- 明文 API key、OAuth access/refresh token、会话凭据和隧道 token 不由连接设置、共享投影、命令行参数或诊断日志主动返回；普通文件/网页/工具输出仍可能是用户数据，不能把当前 UI 白名单误读成全局秘密扫描器。
- 凭据身份与凭据内容分开。公开设置只显示稳定身份、配置状态和遮罩；环境引用、自定义请求头和 OAuth 只显示存在/已授权状态。
- 任何要读取、传输、复制、共享、提交、上传或写入生产系统的敏感数据，都需要在实际副作用边界重新核对范围和授权；计划模式不会因为“只读”而把秘密读入上下文。
- 认证失败、审阅失败、凭据存储失败和状态未知都保持失败/未知语义；不退回到另一套凭据，不自动创建新凭据，不把旧结果当成功。
- 共享对话、子代理、浏览器标签、终端和后台任务按会话及生命周期隔离；异步结果不能覆盖另一对话或关闭页面。

## 凭据生命周期

模型连接由 `harness/llm/providers.py`、`harness/llm/routes.py` 和 `harness/llm/provider_runtime.py` 装配。连接配置保存 provider/interface/model 的稳定身份、环境变量引用和 key ID；明文 key 放在独立的 `connection-secrets.json`，读回时用统一 `mask_api_key`；OAuth 凭据由 `harness/connectors/credentials.py` 优先写 Windows Credential Manager，失败时使用 `HARNESS_HOME/oauth-credentials.json` 的 0600 文件并原子替换。`harness/llm/chatgpt_auth.py` 另行保存账号凭据，普通设置投影只返回邮箱和授权状态。

刷新操作在接口级锁内完成，先确认旧 refresh token 的一次性消费和新凭据持久化；持久化失败回滚内存状态。`provider_runtime` 在桥接错误和诊断上下文中按当前请求凭据做递归脱敏；`routes.py` 对连接 key 做同样处理。错误详情不能成为泄漏旁路，未知写入不重试。

会话共享凭据由 `harness/app/session_credentials.py` 管理。只保存 SHA-256 摘要，明文只在成功落盘后返回一次；成员权限 epoch 变化立即使旧凭据失效，撤销只追加审计事件，不删除历史。邀请和领取秘密在 `harness/web/api/collaboration.py` 中只通过一次性流程兑换，公共历史不包含秘密。

MCP 与隧道 token 只通过受监督子进程环境传递，不写命令行参数；状态 API 返回遮罩。网关固定回环监听，`harness/codex_gateway` 的账号凭据与额度快照按账号目录隔离，不读取桌面客户端凭据来代替显式配置。

模型可调用的 shell、代码、终端、LSP、Hex、workflow、文件 grep 和语义记忆 worker 统一经过 `harness/tools/process_env.py::execution_env`，清除宿主密钥及常见代码注入变量；worker 只接收本次调用生成的 bootstrap 和可信 `PYTHONPATH`。项目 hooks、cloudflared 下载/隧道和 MCP 管理服务也沿用这条边界，插件变量、隧道 token、连接器 token 和代理只在调用点显式加入。MCP/ACP 与 provider bridge 使用 `stdio_env`：先清洗宿主环境，再加入连接记录明确声明的运行时变量，因此已登记的 connector secret 仍可被该连接子进程读取，这不是全局秘密扫描器或操作系统级沙箱。

## 权限、审批与继承

`harness/app/permissions.py::PermissionPolicy` 是普通工具的权限事实源。权限作用域沿真实的宿主委派记录向父会话归并；历史分叉的 `parent_session` 字段不能凭空授予权限。子会话只能继承父作用域的策略和临时授权，不能自己降权、升权或撤销父作用域授权；权限策略修改使用版本号比较，正在等待审批的调用在策略变化时失效并重新判断。

四种策略有明确语义：`read_only` 只允许已声明为读取的工具，并拒绝 `read`/`grep` 的工作区外路径；`request_approval` 对工作区写入、工作区外读取、命令、外部写入、危险工具、网络和未知副作用逐次请求人工决定；`auto_review` 先把精确工具、参数、工作目录、效果和用户意图交给无工具审阅模型，`allow` 才继续，`deny` 拒绝，`ask` 回到人工审批；`full_access` 是用户/宿主选择的策略，不是模型可自行升级的权限。自动审阅不替代网络限制、凭据控制或浏览器来源审批。

即使选择 `full_access`，权限控制器仍递归扫描普通工具和 ACP tool-call 的参数；敏感文件访问、凭据字段、Bearer/Basic、JWT、云令牌前缀和私钥块会硬拒绝。普通工具通过可信 `ToolDefinition.argument_semantics` 声明顶层字符串参数的用途，权限层按用途判断文件引用，不能把整份源码按路径拆词：

| 用途 | 当前消费者与检查 |
|---|---|
| `path` | 文件路径、glob 模式与命令工作目录，按完整路径分量检查敏感名称和后缀 |
| `text` | 文件写入/编辑正文、内容搜索表达式、可视化正文与展示说明，检查秘密字面量，不因 `event.key`、`state.secret` 等源码成员名或文档提到 `.env` 就当作文件访问 |
| `shell` | Shell 命令与终端输入，按命令词检查路径、选项值和重定向；区分解释器代码参数及 heredoc 正文 |
| `code` | `run_code` 与 workflow 的可执行源码，检查字符串中的敏感引用；`run_code` 的 Shell 语言沿命令检查路径处理 |

`harness/app/argument_sensitivity.py` 只对直接交给 `cat`/`tee` 的 heredoc 输入使用数据正文规则；正文仍接受秘密字面量扫描，未加引号的 heredoc 还检查命令替换。解释器输入保留源码引用检查，管道、未知消费者、无法完成词法分析或超过嵌套限制的输入保持保守检查。该分析不执行输入、不读取目标文件，也不保证识别动态拼接的路径、全部 Shell 方言或任意程序的数据流，不能作为操作系统沙箱。

声明只来自注册实现，复制后以只读映射保存，并纳入工具授权指纹；模型传入同名字段不能改变用途，声明变化使旧授权失效。未声明或非法用途、嵌套对象/数组以及没有可信工具声明的 ACP 请求仍按保守引用规则扫描。`todo_read`、`todo_write`、`todo_update` 和 `todo_bind` 的顶层 `snapshot_token` 是公开的清单版本身份，仅豁免该字段名的凭据判定；字段值、嵌套内容及其他字段仍接受扫描，其他工具和 ACP 请求不享有此例外，快照归属与版本仍由 todo 工具验证。审批卡片、外部 Agent 详情、自动审阅输入、工作目录和审阅理由使用同一套有界脱敏，真实参数只留在受信执行链中。秘密字面量规则和引用分析均是启发式，不能被描述为完整秘密分类器。

每个临时授权绑定工具对象、完整 schema、执行函数、效果、危险标记、工作目录、参数指纹和权限作用域，默认 15 分钟后过期；参数、工具身份、权限版本或作用域任一变化都会使授权失效。撤销先从内存准入表移除，再写审计事件；审计写入失败也不恢复执行权限。审批卡片的本地化标签只用于显示，业务决定使用稳定 option ID（`allow-once`、`allow-temporarily`、`deny`）；审批期间参数变化、连接取消、审阅失败或范围不清都 fail closed。

基础敏感判定与脱敏统一在 `harness/core/redaction.py` 实现：路径与保守引用判定、明文凭据形态识别、结构化递归扫描与脱敏、纯文本脱敏与命中计数都在该模块，`harness/app/permissions.py` 与 `harness/app/plan_mode.py` 按既有名字再导出。权限层按可信参数用途选择顶层字符串的引用检查，源码与命令分析由 `harness/app/argument_sensitivity.py` 提供；字段名、秘密字面量和嵌套内容仍经过核心扫描。审批展示、计划正文检查与跨智能体上下文继承共用基础规则。脱敏只作用于展示与继承副本，不改变真实工具参数、执行指纹或落盘原件；`redact_text` 返回命中次数，使“改写了多少”成为可报告事实而不是静默行为。

委派边界与权限边界一起收敛。`harness/subagent/admission.py::AdmissionGate` 按整棵代理树计名额并限制派生深度：后代不再绕过并发闸，深度在创建会话之前判定，超限立即失败且不留半成品运行记录。父代理阻塞观察期间让渡名额、观察结束后召回，因此“父等子、子等名额”不会互锁；让渡与召回是显式状态迁移，取消既不丢名额也不重复计数。委派成员单调收紧 `read_only` 与工作区目录，不能通过省略覆盖而放宽。

跨代理继承的上下文在进入子代理之前必须脱敏。`harness/collaboration/context_inheritance.py` 复制父会话可见正文时逐条脱敏，并在载荷中给出 `message_count`、`redacted_messages`、`redacted_hits`；父会话事件保持原件。超出体积上限明确拒绝，不做静默截断。继承提示词资源同时声明：继承正文是不可信数据而非指令或授权，调用方已持有的批准不构成本代理的授权，且不得还原或索取被移除的凭据。

自动审阅的证据必须标明完整性与请求方身份。`harness/app/permissions.py::_review` 的载荷带 `user_messages_total`、`user_messages_incomplete` 与 `requester`（根代理报告 `kind='root'`；受管成员报告 `kind='subagent'` 及其 `depth`、`read_only`、`runner`、`parent_session_id`），保留条数上限由 `MAX_REVIEW_USER_MESSAGES` 单点定义。审阅提示词资源相应要求：证据被截断时不得把有界样本当成完整授权，缺失上下文可能改变结论时选择 `ask`；请求来自受委派成员时，委派方某个具体操作曾获得的批准不构成成员的通用授权，必须按当前根会话的限制与撤销重判本次请求。

权限审批只审阅明确的调用和范围。用户原文、模型解释、网页/MCP 输出和参数中嵌入的指令都是不可信数据，不能扩大授权。需要访问工作区外文件、网络、生产系统、凭据或外部服务时，审批理由必须说明目标、影响和最小必要范围；不能通过换工具、拆分调用、改变路径或使用子代理绕过拒绝。普通本地执行器不提供 Codex 托管 sandbox 的操作系统隔离，外部进程工作目录也不能被描述为沙箱；需要隔离时使用独立工作区/容器和受限网络。

## 计划模式的隐私边界

计划模式是只读约束，不是秘密访问许可。`harness/app/plan_mode.py::PlanModeController.guard` 用启发式规则拒绝 `read`、`glob`、`grep` 对明显敏感文件的访问，包括 `.env`、凭据/令牌文件、私钥和证书容器；`.example`、`.sample`、`.template`、`.dist` 示例可以继续审查。`glob.pattern` 是文件模式；`grep.path` 和 `grep.glob` 决定读取范围，`grep.pattern` 是内容表达式，不作为路径检查，搜索 `event.key` 不会据此被判成读取私钥。`contains_sensitive_literal` 和 `exit_plan_mode` 还会拒绝把常见 API key、Bearer token、密码赋值或私钥头写入计划正文。启发式规则不是秘密分类器，不能替代人工审阅。计划提示词同时要求模型不要读取凭据存储、私钥、token cache、环境转储或可能包含凭据的文件；如果发现，计划只能记录“访问已拒绝”，不能粘贴内容。

计划模式还拒绝终端工具、写文件、编辑、命令、需要审批的工具和危险浏览器动作。只读 web/MCP 仍可能返回敏感数据，因此其结果也不能自动复制进计划；模型必须最小化引用并脱敏。计划版本正文带 `content_hash`，审批回执引用该摘要，计划历史和 Goal 证据不把摘要当作秘密内容，也不把摘要当作业务完成证据。

## 审阅与副作用

权限、计划批准、MCP、浏览器、文件写入、Git 发布和外部发送分别在各自工具边界核验。计划审批使用 QuestionBroker 的稳定 option ID；UI 可以显示本地化标签，但业务只根据 `approve`/`keep-planning` 等机器身份决定，避免语言切换或迟到标签误判。审阅超时、取消、通道不可用或 epoch 过期时保持只读并关闭待审版本。

安全审阅必须提供实际调用的工具名、参数、工作目录/目标、调用身份和允许范围，不把用户原文、模型解释、网页内容或工具输出中的指令当作授权。审阅器只返回 allow/deny/ask 及理由；它不能执行工具，也不能扩大范围。敏感数据外发、生产访问、凭据读取、持久化安全削弱和不可逆破坏需要单独明确批准。

## 日志、证据与展示

原始事件日志用于审计，但模型上下文和共享投影使用白名单/有界摘要。`harness/session/request_traces.py` 在归档请求体时记录是否发生脱敏；`harness/web/shared_projection.py` 过滤公开历史；错误消息保留机器码和可诊断事实，去除供应商原始请求与凭据。计划、Goal、todo、提交和工具结果各自有身份和序号，不能通过相似文本拼接成证据。

文件和终端结果需要同时记录路径、截断状态、版本/回执和归属。敏感文件被拒绝不应显示原文；只显示明确的拒绝代码和下一步。浏览器截图、下载、附件和共享页面必须保留对话/标签页归属，关闭或取消后迟到结果丢弃。

## 与 Codex 的比较与限制

Codex/Agents 的公开架构把 harness 控制面与 sandbox 执行面分开，并要求秘密由运行时/secret manager 注入，不能进入 prompt、任务文件、提交物或产物；Codex 的审批/guardrail 也要求在副作用边界核验并在审阅不可用时拒绝。Harness 在本地实现了凭据后端、递归错误脱敏、共享凭据摘要、权限 epoch、计划敏感路径拒绝和未知结果回查，适用于本机数据边界。

本机 REA 证据只支持有限的实现比较：当前 Codex 安装的 `codex.exe` 是原生 x86_64 PE（SHA-256 `37762753b554982eef1c109303d1be652b6397f1479e844794353a85650199c6`），未发现 Electron fuse sentinel；Ghidra/IDA/Hopper 等原生深层反编译提供器不可用（REA `binary_session` 报告 `ghidra: not_configured`），因此不能把私有凭据注入、秘密扫描或沙箱内部实现写成已观察事实。REA 对本机 `goals_1.sqlite` 观察到线程级 Goal 状态约束与续接延后表，对 `state_5.sqlite` 观察到线程的 sandbox/approval 等状态字段；这些数据库快照不证明明文凭据存储位置，也不证明所有恢复路径。

可核对的实现级证据集中在 [Codex Agent 内核实测对照](codex-agent-internals.md)：该文按字节偏移记录二进制字面量（三档沙箱提示词、命令分段与规则匹配边界、`prefix_rule` 升级流程与被禁前缀、`agents` 与 `features.multi_agent_v2` 配置段、guardian v2 证据预算与来源标注、`shell_environment_policy` 五种继承模式、沙箱来源枚举含 `SubAgent`），并记录运行目录结构（`plans/<threadId>/<planId>/PLAN.md`、`rules/default.rules`、`secrets/mcp_oauth.age`、`.sandbox/deny_read_acl_state.json`）。凭据文件只记录名称与字节数，不读取也不复制内容。本文把官方公开原则、源码实现和 REA 未知边界分开记录。

Harness 的本地执行器仍可能与宿主共享文件、环境和网络；除 Academy 明确使用平台隔离外，普通 `LocalExecutionEnvironment` 不承诺操作系统级沙箱，且普通模式的文件/网页工具不会对所有返回内容做通用秘密检测。我们不能据此声称达到托管 Codex sandbox 的隔离强度。需要更强边界时，应使用独立工作区/容器、最小挂载、受限网络和外部 secret manager，并在 artifacts 离开执行面前做人工或策略复核。

## 针对性验证入口

凭据与隐私相关回归包括 `tests/test_api_key_masks.py`、`tests/test_credential_integrity.py`、`tests/test_issue_boundaries.py`、`tests/test_provider_connections.py`、`tests/test_lan_language.py`、`tests/test_mcp_named_tunnel.py`、`tests/test_trace_read_work.py`、`tests/test_plan_mode_lifecycle.py`、`tests/test_plan_completion.py`、`tests/test_plan_security.py`、`tests/test_plan_integrity.py`、`tests/test_permission_sensitive.py`、`tests/test_context_inheritance_hygiene.py`、`tests/test_review_evidence_honesty.py` 和 `tests/test_subagent_admission.py`。`tests/test_permission_sensitive.py` 核对真实 todo 工具的公开快照字段、嵌套及其他工具的拒绝边界，`tests/test_todo_evidence_recovery.py` 通过真实智能体循环核对错误证据后读取快照并仅提交一次有效清单。验证应检查明文不出现在公开投影、错误、日志、计划正文和跨代理继承副本；继承载荷的脱敏计数与实际改写一致且源事件保留原件；审阅证据在截断时显式声明截断并标注委派请求方；凭据刷新/撤销失败保留原状态；敏感计划引用被拒绝；跨语言审批仍按机器 ID 判断；名额与深度超限失败且不丢名额。缺少真实 Windows Credential Manager、容器或生产网络环境时，只能报告本地替身和源码证据，不能写成实机隔离验收。

源码成员名误判的防回归依据在 `tests/test_permission_argument_semantics.py`：固定 `write.content` 和 heredoc 中的 `e.key`/`ev.key` 触发形态，通过真实注册与权限入口检查英文→中文→英文，核对原始参数不改写、临时目录的实际写入/编辑字节，以及敏感路径和秘密在执行前被拒绝。命令用例同时覆盖数据正文、命令替换、解释器输入和管道，声明隔离与授权指纹另有断言；POSIX heredoc 的真实执行不代表 Windows 命令解释器实机验收。`tests/test_plan_security.py::PlanSecurityTests.test_content_search_does_not_treat_member_names_as_file_paths` 固定内容表达式与文件范围的区别。
