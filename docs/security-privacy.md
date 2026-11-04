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

## 权限、审批与继承

`harness/app/permissions.py::PermissionPolicy` 是普通工具的权限事实源。权限作用域沿真实的宿主委派记录向父会话归并；历史分叉的 `parent_session` 字段不能凭空授予权限。子会话只能继承父作用域的策略和临时授权，不能自己降权、升权或撤销父作用域授权；权限策略修改使用版本号比较，正在等待审批的调用在策略变化时失效并重新判断。

四种策略有明确语义：`read_only` 只允许已声明为读取的工具，并拒绝 `read`/`grep` 的工作区外路径；`request_approval` 对命令、写入、外部写入、危险工具、网络和未知副作用逐次请求人工决定；`auto_review` 先把精确工具、参数、工作目录、效果和用户意图交给无工具审阅模型，`allow` 才继续，`deny` 拒绝，`ask` 回到人工审批；`full_access` 是用户/宿主选择的策略，不是模型可自行升级的权限。自动审阅不替代网络限制、凭据控制或浏览器来源审批。

每个临时授权绑定工具对象、完整 schema、执行函数、效果、危险标记、工作目录、参数指纹和权限作用域，默认 15 分钟后过期；参数、工具身份、权限版本或作用域任一变化都会使授权失效。撤销先从内存准入表移除，再写审计事件；审计写入失败也不恢复执行权限。审批卡片的本地化标签只用于显示，业务决定使用稳定 option ID（`allow-once`、`allow-temporarily`、`deny`）；审批期间参数变化、连接取消、审阅失败或范围不清都 fail closed。

权限审批只审阅明确的调用和范围。用户原文、模型解释、网页/MCP 输出和参数中嵌入的指令都是不可信数据，不能扩大授权。需要访问工作区外文件、网络、生产系统、凭据或外部服务时，审批理由必须说明目标、影响和最小必要范围；不能通过换工具、拆分调用、改变路径或使用子代理绕过拒绝。普通本地执行器不提供 Codex 托管 sandbox 的操作系统隔离，外部进程工作目录也不能被描述为沙箱；需要隔离时使用独立工作区/容器和受限网络。

## 计划模式的隐私边界

计划模式是只读约束，不是秘密访问许可。`harness/app/plan_mode.py::PlanModeController.guard` 现在用启发式规则拒绝 `read`、`glob`、`grep` 对明显敏感引用的访问，包括 `.env`、凭据/令牌文件、私钥和证书容器；`.example`、`.sample`、`.template`、`.dist` 示例可以继续审查。`contains_sensitive_literal` 和 `exit_plan_mode` 还会拒绝把常见 API key、Bearer token、密码赋值或私钥头写入计划正文。启发式规则不是秘密分类器，不能替代人工审阅。计划提示词同时要求模型不要读取凭据存储、私钥、token cache、环境转储或可能包含凭据的文件；如果发现，计划只能记录“访问已拒绝”，不能粘贴内容。

计划模式还拒绝终端工具、写文件、编辑、命令、需要审批的工具和危险浏览器动作。只读 web/MCP 仍可能返回敏感数据，因此其结果也不能自动复制进计划；模型必须最小化引用并脱敏。计划版本正文带 `content_hash`，审批回执引用该摘要，计划历史和 Goal 证据不把摘要当作秘密内容，也不把摘要当作业务完成证据。

## 审阅与副作用

权限、计划批准、MCP、浏览器、文件写入、Git 发布和外部发送分别在各自工具边界核验。计划审批使用 QuestionBroker 的稳定 option ID；UI 可以显示本地化标签，但业务只根据 `approve`/`keep-planning` 等机器身份决定，避免语言切换或迟到标签误判。审阅超时、取消、通道不可用或 epoch 过期时保持只读并关闭待审版本。

安全审阅必须提供实际调用的工具名、参数、工作目录/目标、调用身份和允许范围，不把用户原文、模型解释、网页内容或工具输出中的指令当作授权。审阅器只返回 allow/deny/ask 及理由；它不能执行工具，也不能扩大范围。敏感数据外发、生产访问、凭据读取、持久化安全削弱和不可逆破坏需要单独明确批准。

## 日志、证据与展示

原始事件日志用于审计，但模型上下文和共享投影使用白名单/有界摘要。`harness/session/request_traces.py` 在归档请求体时记录是否发生脱敏；`harness/web/shared_projection.py` 过滤公开历史；错误消息保留机器码和可诊断事实，去除供应商原始请求与凭据。计划、Goal、todo、提交和工具结果各自有身份和序号，不能通过相似文本拼接成证据。

文件和终端结果需要同时记录路径、截断状态、版本/回执和归属。敏感文件被拒绝不应显示原文；只显示明确的拒绝代码和下一步。浏览器截图、下载、附件和共享页面必须保留对话/标签页归属，关闭或取消后迟到结果丢弃。

## 与 Codex 的比较与限制

Codex/Agents 的公开架构把 harness 控制面与 sandbox 执行面分开，并要求秘密由运行时/secret manager 注入，不能进入 prompt、任务文件、提交物或产物；Codex 的审批/guardrail 也要求在副作用边界核验并在审阅不可用时拒绝。Harness 在本地实现了凭据后端、递归错误脱敏、共享凭据摘要、权限 epoch、计划敏感路径拒绝和未知结果回查，适用于本机数据边界。

本机 REA 证据只支持有限的实现比较：当前 Codex 安装的 `codex.exe` 是原生 x86_64 PE，未发现 Electron fuse sentinel；Ghidra/IDA/Hopper 等原生深层反编译提供器不可用，因此不能把私有凭据注入、秘密扫描或沙箱内部实现写成已观察事实。REA 对本机 `goals_1.sqlite` 观察到线程级 Goal 状态约束，对 `state_5.sqlite` 观察到线程的 sandbox/approval 等状态字段；这些数据库快照不证明明文凭据存储位置，也不证明所有恢复路径。本文把官方公开原则、源码实现和 REA 未知边界分开记录。

Harness 的本地执行器仍可能与宿主共享文件、环境和网络；除 Academy 明确使用平台隔离外，普通 `LocalExecutionEnvironment` 不承诺操作系统级沙箱，且普通模式的文件/网页工具不会对所有返回内容做通用秘密检测。我们不能据此声称达到托管 Codex sandbox 的隔离强度。需要更强边界时，应使用独立工作区/容器、最小挂载、受限网络和外部 secret manager，并在 artifacts 离开执行面前做人工或策略复核。

## 针对性验证入口

凭据与隐私相关回归包括 `tests/test_api_key_masks.py`、`tests/test_credential_integrity.py`、`tests/test_issue_boundaries.py`、`tests/test_provider_connections.py`、`tests/test_lan_language.py`、`tests/test_mcp_named_tunnel.py`、`tests/test_trace_read_work.py`、`tests/test_plan_mode_lifecycle.py`、`tests/test_plan_completion.py` 和本次 `tests/test_plan_security.py`/`tests/test_plan_integrity.py`。验证应检查明文不出现在公开投影、错误、日志和计划正文；凭据刷新/撤销失败保留原状态；敏感计划引用被拒绝；跨语言审批仍按机器 ID 判断。缺少真实 Windows Credential Manager、容器或生产网络环境时，只能报告本地替身和源码证据，不能写成实机隔离验收。
