# 模型供应商实现审查

## 缓存用量报告

触发条件是供应商桥接返回正 `cacheRead`：`model-runtime/bridge.mjs` 的完成事件原先只传 `cache_read_tokens`，遗漏 `cache_reported_input_tokens`；`provider_runtime.provider_stream` 经 `TokenUsage.to_dict` 将缺失覆盖保留为缺失，而 `request_traces._usage_totals` 把它当作零，造成有读取量却被判不一致，前端随后隐藏比例。桥接现在同步传递覆盖输入，归档和单请求展示按正读取证据逐请求确定缺失分母，保留显式矛盾和未知零报告。回归为 `test_cache_usage_reporting.py`、`RuntimeBridgeTests.test_messages_and_responses_streams_map_text_tools_and_usage`、`model-runtime/tests/transcript.test.mjs`、`promptCache.test.ts` 和 `context-statistics-language-smoke.mjs`；产品规则统一见 [架构与模块](architecture.md)。这些回归使用隔离数据与回环接口，不代表在线供应商验收。

## 核对依据与调用链

通用目录以 DeepSeek Harness `packages/llm/llm-pi-ai/src/index.ts` 的 `catalogProviderIds()` 注册入口、`provider-data.generated.ts` 的认证字段及其固定供应商库目录为依据；专用入口为 `llm-deepseek` 与 `llm-deepseek-account`。不读取 research 目录。

实际消费者为：`ModelConnections` → `/connection` → `ProviderConnections.resolve` → `OpenRouterAdapter.stream` → `provider_runtime.provider_stream` → 发行版 `bridge.mjs` → 原生供应商 API → Harness 模型事件。手工指定通用协议时使用对应 API 工厂。模型身份同时进入 `model_catalog`、会话覆盖、智能体模板、子智能体目录及压缩调用。

下表记录随固定运行库交付的目录和分发路径；它是源码核对，不代表所有供应商已在线验收。API Key 一列同时允许该供应商已有的环境认证；云认证保持原生含义。

| 供应商 | 目录中的请求协议 | 认证 | 最终分发 |
|---|---|---|---|
| `deepseek-official` | anthropic-messages | API Key／环境 | DeepSeek 专用认证与 Messages |
| `deepseek-account` | anthropic-messages | 、OAuth | DeepSeek 专用认证与 Messages |
| `amazon-bedrock` | bedrock-converse-stream | API Key／环境 | 原生 provider.streamSimple |
| `ant-ling` | openai-completions | API Key／环境 | 原生 provider.streamSimple |
| `anthropic` | anthropic-messages | API Key／环境、OAuth | 原生 provider.streamSimple |
| `azure` | azure-openai-responses, openai-completions | API Key／环境 | 原生 provider.streamSimple |
| `baseten` | openai-completions | API Key／环境 | 原生 provider.streamSimple |
| `cerebras` | openai-completions | API Key／环境 | 原生 provider.streamSimple |
| `cloudflare-ai-gateway` | anthropic-messages, openai-completions, openai-responses | API Key／环境 | 原生 provider.streamSimple |
| `cloudflare-workers-ai` | openai-completions | API Key／环境 | 原生 provider.streamSimple |
| `deepseek` | openai-completions | API Key／环境 | 原生 provider.streamSimple |
| `fireworks` | anthropic-messages, openai-completions | API Key／环境 | 原生 provider.streamSimple |
| `github-copilot` | anthropic-messages, openai-completions, openai-responses | API Key／环境、OAuth | 原生 provider.streamSimple |
| `google` | google-generative-ai | API Key／环境 | 原生 provider.streamSimple |
| `google-vertex` | google-vertex | API Key／环境 | 原生 provider.streamSimple |
| `groq` | openai-completions | API Key／环境 | 原生 provider.streamSimple |
| `huggingface` | openai-completions | API Key／环境 | 原生 provider.streamSimple |
| `kimi-coding` | openai-completions | API Key／环境、OAuth | 原生 provider.streamSimple |
| `meta` | openai-responses | API Key／环境、OAuth | 原生 provider.streamSimple |
| `minimax` | anthropic-messages | API Key／环境 | 原生 provider.streamSimple |
| `minimax-cn` | anthropic-messages | API Key／环境 | 原生 provider.streamSimple |
| `mistral` | mistral-conversations | API Key／环境 | 原生 provider.streamSimple |
| `moonshotai` | openai-completions | API Key／环境 | 原生 provider.streamSimple |
| `moonshotai-cn` | openai-completions | API Key／环境 | 原生 provider.streamSimple |
| `nvidia` | openai-completions | API Key／环境 | 原生 provider.streamSimple |
| `openai` | openai-responses | API Key／环境、OAuth | 原生 provider.streamSimple |
| `openai-codex` | openai-codex-responses | 、OAuth | 界面合并至 Codex 帐号池；订阅 Responses |
| `opencode` | anthropic-messages, google-generative-ai, openai-completions, openai-responses | API Key／环境 | 原生 provider.streamSimple |
| `opencode-go` | anthropic-messages, google-generative-ai, openai-completions, openai-responses | API Key／环境 | 原生 provider.streamSimple |
| `openrouter` | anthropic-messages, openai-completions | API Key／环境、OAuth | 原生 provider.streamSimple |
| `qwen-token-plan` | openai-completions | API Key／环境 | 原生 provider.streamSimple |
| `qwen-token-plan-cn` | openai-completions | API Key／环境 | 原生 provider.streamSimple |
| `qwen-token-plan-individual` | openai-completions | API Key／环境 | 原生 provider.streamSimple |
| `radius` | pi-messages | API Key／环境、OAuth | 原生 provider.streamSimple |
| `together` | openai-completions | API Key／环境 | 原生 provider.streamSimple |
| `typesafe` | 目录无聊天模型 | API Key／环境 | 原生 provider.streamSimple |
| `vercel-ai-gateway` | anthropic-messages | API Key／环境 | 原生 provider.streamSimple |
| `xai` | openai-responses | API Key／环境、OAuth | 原生 provider.streamSimple |
| `xiaomi` | openai-completions | API Key／环境 | 原生 provider.streamSimple |
| `xiaomi-token-plan-ams` | openai-completions | API Key／环境 | 原生 provider.streamSimple |
| `xiaomi-token-plan-cn` | openai-completions | API Key／环境 | 原生 provider.streamSimple |
| `xiaomi-token-plan-sgp` | openai-completions | API Key／环境 | 原生 provider.streamSimple |
| `zai` | openai-completions | API Key／环境 | 原生 provider.streamSimple |
| `zai-coding-cn` | openai-completions | API Key／环境 | 原生 provider.streamSimple |
| `opencode-zen` | 专用 OpenAI 请求映射 | 多 API Key | OpenRouterAdapter 专用入口 |
| `opencode-local` | 现有本机 OpenCode 传输 | 本机资料 | OpenRouterAdapter 专用入口 |
| `custom` | Completions、Responses、Messages | 多 API Key | 对应协议工厂 |

## 已修正问题与回归

- 手动模型配置曾在 `ConnectionEditor.addManualModel` 仅接收模型 ID，新增后才能展开下方列表补填参数；目录标题、数量与按钮也分散占行。当前由 `ProviderModelEditor.tsx` 提供添加前的完整草稿与共享编辑字段，`ModelConnections` 保留接口草稿归属、分页和重复准入；工具栏集中排列，已选模型默认收起参数。`provider-manual-model-smoke.mjs` 使用真实设置页核对添加前完整参数、容量拒绝、重复 ID／双击、输入法、连续添加重置与焦点、保存正文及重开读取，并在英中英、明暗与窄宽屏切换中保留同一份草稿。`provider-add-flow-smoke.mjs` 保留对应参数保存断言，既有目录／保存／布局场景同步限定已选模型作用域。本轮验证范围仅限此手动配置场景及必要的资源同步、类型和生产构建；其他业务、在线账号、桌面和完整浏览器套件不在范围内。

- 没有已配置模型但进程带有单模型 Key 时，`web/models.py:model_catalog` 原先将 `config.model` 合成为 OpenRouter 条目；未装配提供商目录时还会把 `model_routes` 与当前模型合成为目录，导致设置空态下仍出现保底模型。现移除这两条合成路径、`ModelSpec`／`_synthetic` 及专用双语说明，目录继续投影已配置提供商和已登录订阅账号。`test_model_catalog.py` 经真实 `/api/v1/models` 路由验证环境变量、旧默认与可见性设置、无提供商目录、提供商移除及重开、账号准入和去重，并保留正常模型身份、容量、思考档位、默认选择与当前隐藏模型仍可见的行为；读取保持原配置和文件字节。`test_catalog_authoring_image_language.py` 的英中英回归改为核对空目录，图片与插件错误的语言断言继续保留。当前目录规则见 [模型提供商](model-providers.md)。

- 本地语义 worker 在启动退出或响应 EOF 时，`memory_semantic_worker.SemanticWorker` 原先立即调用 `_failure()`，异步 `_drain` 可能尚未读到 stderr；随后 `search` 虽执行 `_dispose`，对外错误已冻结为空诊断。现先等待所属进程和日志收尾，再构造同一 `memory/semantic-worker` 错误。`test_crash_diagnostic_waits_for_owned_log_drain_before_reporting` 用日志释放屏障覆盖启动和请求阶段，核对原始中文诊断、错误码及进程释放；相同回归对修改前实现的两个阶段均失败。这说明诊断丢失已修复，不能据此将未复现的历史子进程退出归为某个环境原因。服务生命周期规则见 [扩展机制](extensions.md#可选本地语义记忆候选)。
- Key 池构造原先直接调用 `_load()`，后续入口才由 `_transaction` 取得旁路锁，首次读取可能与另一实例的 `Path.replace` 发布竞争。构造现使用同一 `state_lock`；`test_key_pool_transactions.py` 以发布与读取屏障验证构造不能提前读取，并保留跨进程失败标记合并和保存失败后的重试。原先各处 Windows 锁适配在加锁前为初始空文件写占位字节，多个首次调用可在未取得锁时争用该字节；当前统一由 `core/file_lock.py` 直接锁定空区域，`test_file_lock_resources.py` 核对空文件互斥、跨进程竞争、原字节保持及释放后重入。产品规则见 [存储](storage.md)。
- 目录 HTTP 夹具只捕获断管和连接重置，Windows 写入抛出 `ConnectionAbortedError [WinError 10053]` 时不会设置断开事件，导致 `closed.wait(2)` 失败。当前响应头与正文共用 `ConnectionError` 边界，受控 10053 回归核对断开事件和单次请求。另一独立缺陷是 `catalog_fetch.read_catalog` 使用 `subprocess.run(timeout=...)` 仅终止首进程，读取器后代继续持有匿名输出与 HTTP 连接，Windows 的超时收尾仍等待管道 EOF。当前复用所属进程组／Job Object 和匿名输入，整个组退出并排空输出后才返回；凭据不进入参数，输出仍有界，回收未确认保留原异常并通过 `ProcessCleanupError` 阻止工具降级。`test_catalog_total_deadline.py` 覆盖启动预算、持续响应、真实派生读取器、取消、读取上限和停止失败；原先数百毫秒启动假设不再作为 HTTP 业务前提。
- `owned_process.spawn_owned` 与 `conpty_process.spawn_conpty` 原先将原生创建包装为子 Task，创建自行取消或循环同时取消创建者与子 Task 时，会在已取消结果上无限重试，也可能丢失线程迟到交付的句柄。当前共享 `create_owned`，保留上下文的执行器 Future 不进入循环的 Task 取消快照；自行取消原样传播，观察者取消继续等待唯一创建与所属回收。`test_process_creation_cleanup.py` 用独立解释器的外部截止时间覆盖这两个入口的自取消和整体取消快照，并保留迟到句柄及重复取消屏障回归。

- 本机 OpenCode 的系统上下文曾由 `895ffe052` 引入，`302afaad8` 清理时移除。此次对照同一 `https://opencode.ai/inference/openai/v1` 地址，原生请求和保留完整原生请求体的 Bun 转发均成功；删除上下文返回 `FreeUsageLimitError`。恢复上下文后，当前 Harness 请求中的 `spawn_agent` 仍触发相同拒绝；仅将该协议名映射为原生 `subagent` 即成功，其余工具定义无需移除。现恢复显式上下文与容量计入，并在请求、历史及响应之间双向映射名称，调用身份、参数和权限仍归 Harness。设置入口同时拒绝缺失资料后的 HTTP 静默回退；准备流程固定 `PWD`、私有服务与临时数据，避免钩子落在错误项目及常驻端口冲突。`test_opencode_local.py` 覆盖真实供应商分发、协议往返、分片工具调用、名称冲突、英中英消费、拒绝原文及进程回收。macOS 的 OpenCode 2.1.4 已通过 `deepseek-v4.1-flash-free` 设置链路在线文件读写、历史续接及另一对话隔离；`big-pickle` 也通过单接口在线检查。该实测不证明供应商识别规则完整、免费配额永久可用或其他操作系统已经验收。产品配置与生命周期统一见 [本机 OpenCode](opencode-local.md)。
- 本机转发曾丢弃 stderr，并将超时、断流、非法元数据及非零退出统一转为不可重试的 `llm/opencode-local-runtime`。本机历史追踪中，一次无模型事件的请求约 60 秒失败，另一次交付部分事件后失败；旧记录缺少底层诊断，不能追认具体网络原因。当前 `opencode_local.mjs` 通过独立错误管道输出固定分类，`OpenCodeLocalTransport.post_stream` 有界排空并保留阶段、字节数与已确认退出码，瞬时网络故障交回适配器的既有重试边界。`test_opencode_local.py` 经真实子进程及适配器验证无内容重试、部分内容不重放、不完整工具不交付、英中英资源消费及超限诊断；`model-runtime/tests/opencode-local.test.mjs` 直接运行实际转发脚本，验证请求映射、空闲重置及故障协议。未知退出及损坏诊断仍不可重试；此修复不保证远端服务不再断流，也不改写历史失败或重放既有工具。

- 生成配置原先暴露单次思考／总输出字符限制，并在普通 SSE、原生桥接与订阅工作器中执行；普通传输还会因有效进展超时停止，工具循环和收尾纠正也有固定次数终止。现移除对应配置、环境入口、SettingsPatch 字段、实时限制快照及运行拦截，仅保留默认无限的步骤／Token 预算及默认 180 秒网络超时。`test_generation_policy.py` 核对默认值、协议字段、心跳流、重复并行读取与多次收尾纠正；`test_adapter_stream_cleanup.py` 保留网络超时和全部所属流收尾，`test_plan_completion.py` 继续以显式步骤预算验证未审批时不可写入。设置页公共输入焦点改为边界内单层描边；本机页面读取确认三项设置及焦点无阴影、无外圈。

- 轮次接收 `length`／`max_tokens` 截断响应后，原先由 `Agent._prompt` 统计正文为空或与上一段相同的次数，累计三次便抛出 Harness 自有的 `llm/output-continuation-stalled`，原生供应商仅返回思考时也会命中。现移除该固定次数终止及其计数状态，继续保留各段响应、续写关联和主动停止。`test_output_continuation.py` 核对五段相同正文／原生思考截断后仍能完成，以及多次续写后用户取消仍结束唯一轮次；旧错误卡片保留历史诊断呈现。
- 输出能力未知而上下文较大时，`provider_runtime.provider_stream` 的 `normalize_request` 会用上下文余量生成请求预算；`bridge.mjs` 原先将其直接传给 `streamSimple`，覆盖模型的输出能力／回退值。pi-ai 的 `buildBaseOptions` 只按上下文收敛显式预算，因而可能发送 `max_completion_tokens: 984198`。DeepSeek Harness 的 `llm-pi-ai/src/adapter.ts` 与 `catalog.ts` 区分模型输出能力和请求默认预算；当前桥接补充请求预算与模型输出能力取最小值，保留较小业务预算。进一步核对可视化长参数后，未知能力且未设置预算的 Chat Completions 请求改为省略上限，避免把运行库占位 8192 当作生成限制；已知能力继续收敛，显式预算保持原意。`test_output_budget_cannot_expand_model_output_capacity` 通过发行桥接与回环 HTTP 核对未知能力、大请求预算、已知输出能力和较小显式预算，确认日志与实际发送的上限一致；未据此宣称在线供应商验收。
- Qwen Token Plan CN 的 `deepseek-v4.1-flash-1017` 继承了推理能力，但不在固定目录中且缺少 `supportsDeveloperRole: false`。`bridge.mjs` 将系统提示交给 pi-ai 后，Completions 的默认检测把推理模型提示转成 `developer`，造成供应商 400 拒绝。旧 Python 转换层一直使用 `system`；DeepSeek Harness 的 `llm-pi-ai/src/context.ts` 同样将开发者消息映射到系统消息，`catalog.ts` 的 `resolveModelCompat` 提供接口／模型级逐字段覆盖。桥接现在为三个 Qwen Token Plan 入口提供系统角色默认值，保留目录及显式配置的覆盖顺序。`test_qwen_snapshot_instruction_role_reaches_actual_request` 通过发行桥接与回环 HTTP 核对新快照、已有缓存、无缓存、原生／显式 Completions、接口／模型覆盖和其他供应商，并确认日志请求体与实际发送内容一致；不代表在线模型验收。
- 相同接口的多个模型原先各自拥有 Key；现在接口共享状态，`test_provider_connections` 核对跨模型失败标记与显式重置。
- 混合目录不能套用首模型的地址；接口留空地址时保留模型自己的地址与 API，显式覆盖才应用接口配置。
- 环境字段必须同时进入认证解析与实际请求；隔离桥接检查覆盖 OpenAI、Azure、Google 和 AWS Profile，避免只保存表单。
- OAuth 与 Bedrock 的变量动态导入在独立包内不可用；构建器将这些入口改为静态可打包导入。发行资源通过生成同步检查，wheel／sdist 回归核对桥接资源字节。
- Responses 的空 session ID 不能作为 null 传给供应商库；桥接转换为缺省值。真实回环 HTTP 回归覆盖 Messages／Responses 文本、工具调用、用量及结束原因。
- 部分输出后的认证错误不能自动重放；隔离错误注入核对只尝试一次，以及未输出时仅在当前接口轮换并明确报告耗尽。
- 原生供应商桥接曾只按 HTTP 状态码判断瞬时故障，流已建立后以 200 返回 `Upstream idle timeout exceeded` 时会被标为永久失败，智能体因此以 `attempts=1` 结束本轮。`provider_runtime.bridge` 现在对有界的上游空闲超时、暂时不可用、过载和连接中断语义保留瞬时分类，再由接口 Key 池轮换；已交付内容仍不重放。`RuntimeBridgeTests.test_http_200_failure_event_classifies_idle_timeout_as_transient` 核对桥接错误分类，`RuntimeBridgeTests.test_http_200_upstream_idle_timeout_rotates_key` 核对第二把 Key 成功收尾。

- 模型批量操作原先替换整个列表；现在仅追加缺少项，保留手工编辑和稳定身份。`providerConnections.test.ts` 核对去重、次序与容量，浏览器添加流程核对搜索空态、批量选择和删除取消。
- 云环境认证模板原先包含空 Key；模板现在按实际认证类型创建 Key 列表，避免环境认证被无用字段拒绝。对应单测同时核对 API Key 模板仍为空值。
- 草稿接口变化后，旧模型探测结果可能继续应用；`ConnectionEditor` 将探测绑定地址、协议、认证和 Key 代次，取消只读等待。`provider-draft-discovery-smoke.mjs` 用受控迟到响应核对旧结果拒绝，并覆盖读取重试与语言／主题／宽度矩阵。
- 未提交的探测实现会把安装包成员拼回新目录，因而无法删除已下架模型；不支持列表请求的路径还会返回成功标记。现改为远端成功结果覆盖成员，缓存按接口与选择一起保存，失败／不完整响应保留缓存并明确标为回退。思考能力只由明确报告、同名元数据或同版本日期快照补充，不再匹配任意长前缀。`test_connection_model_discovery.py` 核对目录增减、空目录、持久缓存、回退和能力元数据；`provider-native-save-smoke.mjs` 通过真实隔离后端及回环供应商接口核对空白默认选择及显式添加各系列最新版、保存已有配置、获取后删除下架项、重开目录、新增模型思考等级及并发编辑。
- 配置保存已确认后模型菜单刷新失败，原先进入统一的保存失败分支；现在保留保存成功状态并独立说明菜单刷新失败，不重放配置写入。

- 设置页的“显示名称”同时用于供应商与模型，保存栏也留下不一致的内嵌底色；现在分别使用独立的提供商／模型名称资源，底栏与卡片同色、贴齐左右边界并保持按钮对齐。`provider-settings-review-smoke.mjs` 通过真实 SettingsDialog 的中英文、明暗及窄／宽屏矩阵，逐项核对标签、短说明和边界坐标，并生成截图供视觉复查。
- 设置中的配置卡片与模型显示／默认操作曾分离，同一个提供商重复显示提供商和接口名称。`SettingsDialog` 现在仅挂载 `ModelConnections`，由 `providerModelGroups` 按完整稳定身份归组，`ProviderModelPreferences` 将模型操作嵌入对应卡片；单接口不重复标题，多接口仍保留分组。`ModelPreferences` 沿用原保存动作，默认模型的未知结果与重试反馈在页首独立保留；模型配置区域中文统一为“提供商”，按钮与目录标题的同义英文同时统一，由 `test_i18n_terminology.py` 核对同文案一致性。`providerModelPreferences.test.tsx` 核对同名身份隔离、多接口、内部 ID 隐藏、显示状态与默认保存反馈的英中英消费，既有 `modelSaveLanguage.test.ts` 保留写入及回查行为回归。浏览器场景已同步分组断言与当前文案，本次未执行浏览器和桌面专项。
- 内部模型身份曾在权限与预算摘要及目录缺失的菜单回退中展示；展示入口统一解析模型名称，内部身份缺失目录时显示“模型不可用”，请求和保存身份不改写。同一浏览器回归核对权限页与缺失目录情形；`provider-model-labels-smoke.mjs` 核对默认模型和可见性仍保存原 ID。能力来源中的自有配置／订阅账号标识同样使用双语资源。

- 添加目录曾将订阅账号硬编码为置顶 ChatGPT，并把本机／Zen OpenCode 追加到目录末尾；界面现在使用稳定的供应商系列排序，OpenCode 相邻，OpenAI 后紧接 Codex 帐号池。排序复制输入列表，不改写实际配置顺序。`provider-choice-order-smoke.mjs` 核对中英文目录次序、搜索入口及账号池实际标题。
- 单接口的删除按钮与常驻“未保存”“尚未测试连接”占用多余空间；单接口隐藏删除动作，多接口的删除放在标题旁，仍可实际移除指定接口。保存仅在进行中显示过程反馈，已配置状态不包含未实现的测试说明。同一浏览器回归核对增删、标题对齐及取消期间无写入。

- 未填写 API Key 即获取模型会将不完整草稿交给严格配置校验，返回笼统的格式错误。界面在获取模型和保存前校验凭据，错误按当前语言显示，保留草稿和原模型。提供商入口使用“选择提供商”，说明对应接口／认证／模型管理；额外接口入口收进“其他接口设置”，常规表单不再常驻添加动作。`provider-native-save-smoke.mjs` 使用隔离真实后端，核对 OpenAI 默认不勾选及显式添加各系列最新版、缺少凭据时不发送请求、配置保存与遮罩凭据再次保存；专项矩阵核对中英文、明暗及窄屏。

- 模型按钮曾以单独的配置模式替代实际模型名，并提供额外的恢复动作；现已清理该显示分支、空值提交入口及对应资源。默认模型直接显示并勾选，默认变化同步更新；手选只提交当前对话具体模型，重复选择不写入。输入区与菜单不再因 ChatGPT／API 模型前缀不同而丢弃手选结果，与服务端显式模型优先的解析保持一致。`model-default-selection-smoke.mjs` 保留键盘／输入法、草稿、实际请求证据、拒绝与未知写入、A→B→A 所有权及语言／主题／宽度／字体矩阵；服务端清空覆盖的协议回归继续保留。

- 新模型手工配置或刷新目录报告了思考档位后，模型目录曾依赖内置条目的 reasoning 标志而隐藏档位；现明确模型级映射与缓存能力都能进入菜单，`RuntimeBridgeTests.test_new_cached_model_reasoning_reaches_the_actual_request` 使用真实桥接和回环 SSE 核对 high 实际发送，未知档位仍保持未知。
- 获取目录尚未结束时可保存旧选择，迟到结果也可能覆盖其他接口及本接口后续编辑；现同步准入锁阻止保存，成功结果基于最新草稿合并，只更新当前接口。真实后端浏览器场景用受控读取屏障核对保存禁用、名称／模型选项编辑保留及遮罩凭据再次保存。

- 聚合目录按每个系列默认选最新仍可能产生数百项选择，已选配置逐条挂载使编辑表单过长。新增接口现在从空选择开始，`latestProviderModels` 仅由显式批量按钮调用；`ConnectionEditor` 为候选列表和已选配置分别搜索及分页，批量操作依据完整集合。`provider-large-catalog-smoke.mjs` 核对 396 项目录、243 项旧选择、跨页批量操作、搜索与排序、手工参数、取消、保存回读及英中英／明暗／窄宽屏矩阵；既有真实后端回归继续验证目录刷新、能力与凭据保存。

## 待验证或尚未完整迁移

在线供应商、OAuth 实际登录与刷新、真实云凭据及各操作系统安装包尚未逐项实机验收。DeepSeek 已具备专用账号认证、Messages 请求、目录和最低版本检查；源实现的 Files API 持久文件引用、图片卸载及历史内系统／工具更新等扩展尚未完整迁移，不能据此宣称 DeepSeek 专用能力全部等价。TypeSafe 的源目录没有聊天模型，不能将该供应商模板存在视为聊天调用通过。
