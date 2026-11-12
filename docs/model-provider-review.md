# 模型供应商实现审查

## 模型行操作与编辑保存入口

原 `providerModelGroups` 仅返回已保存配置与全局 `models` 缓存的交集，而 `ModelConnections` 打开时只读取接口、提供商模板与账号，不主动读取模型目录。缓存为空或缺少条目时，卡片仍按配置显示模型数量，模型行却全部或部分消失。新增回归先在原实现复现空缓存不读取目录、空目录隐藏模型的失败。当前管理行按配置枚举，按完整身份补充可用目录元数据；页面打开时读取 `/models`，读取失败仍发布已保存配置，保留行内编辑与删除，并提供只读重试。未进入可用目录的配置不补造运行时可选模型，其默认与显隐操作暂时禁用。`ModelConnections.test.tsx` 覆盖空缓存打开、目录失败及只读重试恢复，`providerModelPreferences.test.tsx` 覆盖全部配置行、已知容量、同名身份隔离和实际行内操作消费；接口与模型选择协议保持不变。

多个提供商或较长模型列表下，原 `ModelConnections` 将提供商编辑表单放在其他卡片之后，保存栏位于表单末尾，`sticky bottom-0` 无法在尚未滚到保存栏时提供入口。`ProviderModelPreferences` → `ModelPreferences` 只呈现显示开关与默认操作，单个模型的参数修改和移除必须先进入完整提供商编辑。

原删除确认在模型行左侧展开，提供商确认又位于整个模型列表之后，导致点击右侧垃圾桶后仍需横向移动或向下寻找。当前两类操作共用 `RemovalConfirmation`：模型确认位于当前行右侧，提供商确认位于标题操作下方、模型列表之前；取消在前、移除在最右侧，提交期间保留并禁用确认，失败不自动重放。当前模型行按完整提供商／接口／模型身份分发编辑与删除操作；单模型编辑复用 `ProviderModelFields`，只修改所属模型。提供商和模型编辑期间隐藏其他配置列表，表单顶部固定显示保存、取消、保存中及失败反馈；进入时聚焦首字段，退出后恢复来源入口。保存仍使用 `providerPayload`、当前 `revision` 和原 `PATCH /connection` 路径，不提交 Key 遮罩，不改变其他模型、接口及提供商顺序。双语文案从源资源生成至浏览器。

模型删除确认最初复用了提供商专用的 `ui.providers.confirmRemove`，导致操作只移除模型而按钮显示“移除提供商”。模型确认现使用独立的 `ui.providers.confirmRemoveModel`；行删除回归直接核对“移除模型 / Remove model”，同时拒绝出现提供商删除文案，避免只比较共用资源键而漏掉对象不一致。

`ModelConnections.test.tsx` 以页面钩子宿主和受控传输核对单模型完整参数保存、取消、顶部操作栏、焦点恢复、同名对象删除隔离、提供商确认位于标题内且先于模型列表、确认按钮顺序、提供商删除成功／失败及单次提交、非法参数、重复保存、失败草稿、英中英及关闭页面后的迟到回执。`providerModelPreferences.test.tsx` 的行操作用例通过实际呈现链核对完整身份及原位确认的资源消费。该验证不包含浏览器实际布局、明暗主题、不同窗口尺寸、生产构建或桌面实机验收；这些仍待对应专项。

## OpenRouter 目录外模型协议选择

触发条件是选择 `stealth/crouton-alpha` 等固定目录之外的聊天模型，接口保留原生协议，所选模型又没有缓存协议元数据。2026-11-05 核对随包目录确认 OpenRouter 同时包含 `anthropic-messages` 与 `openai-completions`，且没有 Crouton Alpha。原 `bridge.mjs` 仅检查接口声明、所选模型元数据和目录是否只有一种协议；因此在发出 HTTP 请求前抛出 `The model needs an explicit API protocol`，表现为 `status=0`。空缓存、缺少协议的匹配条目及混合缓存也会触发；仅剩其他模型的 Messages 缓存时，还会把其他模型的唯一协议误用于当前新模型。

已通过 [OpenRouter 模型页](https://openrouter.ai/stealth/crouton-alpha) 和公开 `GET /api/v1/models` 确认该模型存在，目录报告上下文为 1048576，支持工具调用。[官方 API 文档](https://openrouter.ai/docs/api_reference/overview) 提供统一 Chat Completions 入口。调用链为配置发现／保存 → `ProviderConnections.resolve` → `provider_runtime.provider_stream` → 随包 Node 桥接；发现路由原本已为新模型补充端点协议，缺陷位于运行时处理无该元数据的模型。当前桥接增加 OpenRouter 专用端点默认值，接口显式协议和匹配模型声明仍优先，不修改用户配置，也不向其他混合协议提供商扩展猜测规则。源与发行桥接由 `model-runtime/build.mjs` 同步生成。

`test_openrouter_protocol.py` 先在原发行桥接复现失败，再核对无缓存、空缓存、缺少协议、单一及混合缓存的真实请求路径、认证、模型身份、正文、工具调用、用量和终止原因；同时保留不默认填满输出能力的断言。另核对显式 Completions／Responses、缓存及内置 Messages 的优先级、其他混合提供商发送前拒绝，以及配置原字节和 Key 租约收尾。`test_openrouter_discovery_persists_protocol_for_a_new_chat_model` 通过真实发现路由、保存及重开核对协议和能力元数据。4 项针对性用例通过。未使用在线账号生成，未执行全量、生产 Web 构建、浏览器或桌面专项；公开目录读取成功不代表在线模型调用验收通过。

## 自定义网关模型失败与默认输出预算

对照 2026-11-05 17:00 前基线 `527b0a3`、最初报错时的 `c863d3f` 和本机既有请求归档，确认两版模型传输及随包桥接相同，同一 TokenRhythm 接口的 `egg-alpha` 在下午成功使用 `system`，晚间失败使用 `developer`。晚间省略推理强度的请求也仍是 `developer`，用户提供的 `trace_9e54c006-ea37-488b-ae5b-daa3decbcd90` 对应归档没有 `reasoning_effort`；因此不能只根据界面所选档位判断实际请求。对照相邻成功／失败样本，工具名称与参数 schema 相同，仅 `write` 说明不同，提示词和对话内容仍有变化。这些归档证实此前正常，并提供角色变化的明确证据，但不能单独证明远端 409 的全部原因。

角色变化来自 pi-ai 的 `model.reasoning && compat.supportsDeveloperRole` 分支：自定义模型一旦声明推理能力，运行库默认的 `supportsDeveloperRole: true` 就会将系统指令改为 `developer`，即使本次没有显式选择推理强度。这是能力配置触发的原有适配行为，并非 17:00 后提交引入了一套不同的模型传输。当前桥接为 Custom API 的 Chat Completions 提供 `supportsDeveloperRole: false` 默认值，恢复已成功使用的 `system`；目录、接口及模型的显式配置仍按原顺序覆盖，其他原生提供商不套用该默认值。`test_qwen_snapshot_instruction_role_reaches_actual_request` 已在修复前复现有／无目录、默认／high／max 时的角色误切，并覆盖修复后的实际请求、请求归档、显式覆盖及租约收尾。该代码回归使用隔离回环，不重放用户请求。

用户要求配置 `http://127.0.0.1:5273/` 后，已核对该实例的后端来自当前源码；20:24 与 20:27 的既有失败请求已经使用 `system`，其中省略推理参数的请求仍返回 409，说明角色修复不足以确认远端恢复。通过本地配置 API 显式保存 `supportsDeveloperRole: false` 和 OpenAI 推理参数格式，并在实际模型编辑页面将自定义推理等级恢复为继承；读回确认手工档位映射已移除、全局强度仍为默认，其他模型及凭据保留。使用同一接口和 Key 的单条固定用户消息，不含系统消息、工具或推理参数，直接流式请求仍返回 409／`MODEL_CALL_FAILED`，trace 为 `trace_7dc0b8d7-2859-4446-8c4f-36bdd1e6d0c6`。再以临时配置和固定 system／user 问答经过当前 Harness 桥接，确认实际请求无推理参数、工具及输出上限，仅发送一次，仍返回同一业务错误，trace 为 `trace_c7954d72-ae7e-4075-8125-29fe77d6d027`，Key 租约正常收尾。未向用户对话写入测试任务、未重放原任务，也未自动切换模型；本地配置已确认，提供商模型调用尚未恢复，具体上游原因仍需服务商依据 trace 核查。

2026-11-05 使用用户授权的 TokenRhythm 接口和同一 Key，仅发送固定的最小问答，不读取用户对话。目录明确报告 `deepseek-v4.1-flash-1017` 的输出能力为 384000、`egg-alpha` 为 128000。前者的 Chat Completions 流式请求成功；后者在 `max_completion_tokens` 为 128000、256 和省略时均返回 HTTP 409／`MODEL_CALL_FAILED`。移除 `store`、`stream_options` 的最简流式及非流式请求、改用 `max_tokens`、Responses 最小请求也返回相同错误。因此已排除 Harness 历史、工具定义和大输出上限是该错误的必要触发条件，但不能凭公开错误推断网关内部路由、账号权限或上游的具体故障。修复后的真实 Harness 分发再次确认 DeepSeek 未发送输出上限仍返回 `OK`，Egg 未发送输出上限仍失败，诊断保留 `provider_trace_id=trace_46efb38d-aaa5-4d9a-9e11-c9b40d50271b`。远端 Egg 的可用性仍需服务商核查，不能记录为修复通过。[平台接入文档](https://tokenrhythm.studio/docs/api-integration) 要求保留 `traceId` 排障；[错误码文档](https://tokenrhythm.studio/docs/errors) 未列出此 409 业务码，没有据此扩大自动重试的依据。

源码链路为 `provider_runtime.provider_stream` → 发行 `bridge.mjs` → pi-ai 请求与重试器 → SDK／fetch。原来的 `normalize_request` 在没有业务预算时直接选择模型输出能力，桥接又把它作为显式 `maxTokens` 传入；因此目录能力变成每次请求的输出上限。`extra_body` 在桥接最后合并，原先还能覆盖已收敛的预算。当前共享规范化与桥接将未指定预算保持为未指定，Chat Completions／Responses 在最终正文省略可选上限；显式预算及三种字段别名统一收敛，必填 Messages 容量保持。[Chat Completions 参数文档](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create) 明确 `max_completion_tokens` 是可选上限，包含可见输出及推理 Token，并非请求实际用量。

隔离回环还确认 pi-ai 的 `retryProviderRequest` 将所有 409 默认列为可重试，接口重试为 2 时，一次逻辑调用实际发送三次；OpenAI SDK 在提取嵌套 `error` 时会丢弃响应顶层的 `traceId`。`stream-observation.mjs` 现对已消费的错误正文最多额外保留 64 KiB，不改变原始正文；只对明确的 409／`MODEL_CALL_FAILED` 设置运行库不重试提示，并将顶层或嵌套诊断交回桥接。`_provider_failure` 校验、脱敏并归档 trace，分类为 `llm/provider-model-call-failed`；错误卡片、运行历史与恢复入口沿显式错误码消费双语提示，其他瞬时错误仍走原规则。

对应回归：`test_output_budget.py` 覆盖未设置预算、三种别名、输入预检、思考预算、直接 SDK 及发行桥接的 Responses／Messages 请求；`test_output_budget_cannot_expand_model_output_capacity` 覆盖缓存目录能力及显式预算。`test_model_call_failure_preserves_trace_without_rotating_or_disabling_keys` 通过真实发行桥接核对平铺／嵌套错误、英中英、只发送一次、Key 状态和归档；`test_model_call_failure_keeps_completed_tool_and_turn_evidence` 经真实智能体验证失败前工具只执行一次、轮次与 trace 保留。Node 回归核对原始字节、有界诊断、其他冲突／限流／服务故障的重试提示保持；`providerModelFailure.test.tsx` 和 `errorConvergence.test.tsx` 的对应错误码用例核对实际错误组件、实时／历史投影与英中英消费。仅执行这些针对性用例，未执行全量、生产 Web 构建、浏览器或桌面专项；运行库与双语生成产物已由源生成。

## 推理档位与参数格式

目录只用 `reasoningEfforts` 报告 `max`／`xhigh`、未附 `thinkingLevelMap`，且所选模型没有手工映射时，原桥接虽标记模型支持推理，却没有将档位列表转成运行库映射。pi-ai 的 `getSupportedThinkingLevels` 要求这两个档位有显式映射；真实发行桥接回环已复现选择 `max`／`xhigh` 都发送为 `high`，嵌套 `reasoning.effort` 同样受影响。桥接现仅按已声明列表构造映射，保留模型手工映射、明确禁用及未知能力边界；源与随包桥接由生成器同步。

另一个确认的差异是未选档位时，运行库的 OpenRouter 格式会生成 `reasoning.effort=none`，与“模型默认”不发送显式强度的语义不符。最终正文现在区分默认与显式 `off`，默认时移除运行库补出的两种强度字段，调用方显式扩展保持。`ProviderModelFields` 通过现有模型 `compat.thinkingFormat` 提供平铺、嵌套及继承选择，不丢弃其他兼容配置；`ModelSeat` 消费当前对话／全局选择并直接显示级别，保留待确认和不支持状态，单档位菜单仍提供选择。

`RuntimeBridgeTests.test_new_cached_model_reasoning_reaches_the_actual_request` 覆盖真实请求的列表档位、映射、两种格式、覆盖优先级、默认／关闭、显式扩展、禁用与未知能力，并核对配置原件及租约收尾。409 trace 回归同时核对两种格式均只发送所选模型一次，保留 trace、不轮换或停用 Key。`modelMenu.test.tsx` 与 `ProviderModelEditor.test.tsx` 定向核对实际组件的级别显示、对话继承、单档位、未确认状态、格式编辑和英中英资源消费。这里的回环和组件单测不能证明远端 `egg-alpha` 已恢复；未调用用户在线账号，未执行完整构建、浏览器或桌面专项。

## 流活动、超时与恢复诊断

`provider_runtime.bridge` 原先把读取超时与进程退出、NDJSON 解析错误一起改写为不可恢复的 `llm/provider-runtime`，丢弃原异常；`provider_stream` 又将归档中的错误类型、代码与首事件时间固定为空。已核对的失败记录在请求发出约 180.9 秒后结束，解析事件为零、恢复次数为零；旧记录没有底层错误证据，不能证明当时是本机网络中断还是上游等待。

另一可复现触发条件是上游持续发送 SSE 心跳或工具参数片段：`model-runtime/bridge.mjs` 原先只转发正文、思考和完整调用，后端会把尚无这些事件误判为无活动。现由 `stream-observation.mjs` 观察真实响应字节及原生供应商事件，通过桥接传递节流后的活动；只在完整调用时交付工具。读取超时独立分类为 `llm/timeout`，SDK 传输错误保留结构化身份；明确缺少终止帧与任意协议损坏分别分类。认证与证书拒绝、刷新结果未知及进程故障保持非恢复边界，归档保存实际错误及首事件时间。

`test_provider_stream_resilience.py` 经发行桥接与隔离回环接口验证 Completions／Messages／Responses 心跳及分片参数超过无活动阈值仍完成、无响应超时、断流后正文续接且已完成工具只执行一次、取消后的连接释放，以及英中英错误呈现、脱敏、协议／进程错误和未知刷新保护。`model-runtime/tests/stream-observation.test.mjs` 核对活动节流、字节与响应元数据、取消等待和 SDK 原始原因；其中活动回归在旧发行桥接上失败后再验证修复。取消回归也固定了 TransformStream 取消返回早于底层释放的问题，现使用显式读取器等待原流取消。这些证据不代表在线接口或跨平台安装包验收；产品行为统一见 [模型提供商](model-providers.md#执行预算与流监控)。

回环断连观察另有夹具问题：对端以 TCP reset 断开时，原 `respond` 在 `reader.read()` 抛出 `ConnectionResetError`，未设置断连事件，导致取消测试误报等待超时；连接收尾中的同类异常处理无法补回该事件。当前超时与取消夹具共用 `wait_for_peer_disconnect`，接受 EOF 和连接重置，其他读取异常保持原对象并且不发布断连成功。确定性分支回归与真实发行桥接的取消、无响应超时用例分别核对观察语义和实际资源释放。

## 缓存用量报告

触发条件是供应商桥接返回正 `cacheRead`：`model-runtime/bridge.mjs` 的完成事件原先只传 `cache_read_tokens`，遗漏 `cache_reported_input_tokens`；`provider_runtime.provider_stream` 经 `TokenUsage.to_dict` 将缺失覆盖保留为缺失，而 `request_traces._usage_totals` 把它当作零，造成有读取量却被判不一致，前端随后隐藏比例。桥接现在同步传递覆盖输入，归档和单请求展示按正读取证据逐请求确定缺失分母，保留显式矛盾和未知零报告。回归为 `test_cache_usage_reporting.py`、`RuntimeBridgeTests.test_messages_and_responses_streams_map_text_tools_and_usage`、`model-runtime/tests/transcript.test.mjs`、`promptCache.test.ts` 和 `context-statistics-language-smoke.mjs`；产品规则统一见 [架构与模块](architecture.md)。这些回归使用隔离数据与回环接口，不代表在线供应商验收。

## Kilo 匿名免费池的接入依据

来源代码为 `dsh-our-free-model/src/kilo.js` 的公共网关地址、无认证 Chat Completions 调用，以及 `src/catalog.js` 的 `isFree === true` 筛选规则；未读取任意 research 目录。当前调用链为 `ModelConnections` → `/connection/discover` → `kilo_free.free_models` → 保存接口目录与模型身份 → `provider_runtime.provider_stream` → `KiloFreeAdapter` → 既有 HTTP/SSE 解析与 Harness 工具执行。认证字段为显式 `anonymous`，地址与协议在保存时校验，最终请求剔除认证头。

已证实的保存问题：接口配置采用 `anonymous` 后，`ProviderAuth.type` 的公开协议枚举仍只列原认证类型，导致浏览器 `responseValidation` 将已经写入成功的 `PATCH /connection` 回执及之后的 `GET /connection` 判为 `response/invalid-schema`，无法确认保存。现已同步协议源及 OpenAPI、TypeScript、响应校验生成产物；前端 `providerConnections.test.ts` 的匿名保存／读回契约用例在修复前于 `$.providers[0].connections[0].auth.type` 失败，修复后通过。未知认证值继续拒绝，未放宽校验。

`tests/test_kilo_free.py` 核对匿名配置重载、非法配置原件保留、免费筛选、真实发现路由无认证且不写配置、未知容量、明确推理档位、工具调用配对与传输收尾；语言错误按英中英切换。新增 HTTP 保存／读回／重启用例检查指定免费模型、目录缓存和 Kilo Free 分组保持。前端用例覆盖初始选择为空、获取目录不自动选择 Auto Free 或其他模型、稳定身份、显式选择时的推理元数据及后续刷新保留用户选择；数据说明核对英中英呈现。自动目录读取延迟到稳定挂载，避免开发模式试挂载发出后立即取消读取；沿用原请求代次、取消、保存准入及失败回退。未执行浏览器和安装包专项，不将单元或 HTTP 验证视为这些环境的验收。在线验收入口和产品限制见 [模型提供商](model-providers.md#免登录免费模型)。

重复渠道的触发条件是已经保存 Kilo Free 后再次从提供商目录选择它；原 `ModelConnections.onPick` 总是生成新的提供商与接口身份。现在 `providerDraftForTemplate` 对已配置的 Kilo Free 返回原身份的隔离草稿，不更改其他提供商的多配置行为，也不自动删除已有配置。`providerConnections.test.ts` 核对重复选择不产生新身份及草稿修改不污染原件；初始模型选择与目录刷新均为空选择起步，模型只通过显式勾选加入。

2026-11-04 在线核对：当前出口读取到 11 个明确免费的模型，以 `kilo-auto/free` 和具体模型 `nvidia/nemotron-3.5-super-vl:free` 分别使用空 API Key 配置运行隔离验收，真实 `write`／`read` 工具成功，生成的 Python 函数执行得到本次唯一标记，同对话续接和另一对话隔离均通过。该结果只代表本次模型、网络和输入，不代表持续可用、所有模型或桌面安装包验收。

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

- 手动模型配置曾在 `ConnectionEditor.addManualModel` 仅接收模型 ID，新增后才能展开下方列表补填参数；目录标题、数量与按钮也分散占行。当前由 `ProviderModelEditor.tsx` 提供添加前的完整草稿与共享编辑字段，`ModelConnections` 保留接口草稿归属、分页和重复准入；工具栏集中排列，已选模型默认收起参数。`provider-manual-model-smoke.mjs` 使用真实设置页核对添加前完整参数、容量拒绝、重复 ID／双击、输入法、连续添加重置与焦点、保存正文及重开读取，并在英中英、明暗与窄宽屏切换中保留同一份草稿。焦点事件核对确认 `ManualModelEditor.add` 已将焦点放回模型 ID 输入框；草稿重置收起推理字段后，物理双击的第二下可能落到下方新增卡片并移走焦点。因此单击和回车分别验收返回焦点，物理双击独立核对只新增一个模型；重开后的参数读取限定到原模型卡片，避免命中其他收起卡片中的同名输入。`provider-add-flow-smoke.mjs` 保留对应参数保存断言；这些受控回归不代表在线账号、桌面或完整浏览器套件验收。

- 协议字段曾被 `model-connections-smoke.mjs` 的精确 label 文本定位误报为不可见；实际 `ConnectionEditor` 接口面板已展开，无障碍树中具名 combobox 可见。场景改按角色和当前语言资源定位协议及认证，保留字段顺序、高级设置默认收起、草稿保持、单次保存与卸载后旧回执隔离断言；不为定位问题改变产品表单或增加重复标签。

- 没有已配置模型但进程带有单模型 Key 时，`web/models.py:model_catalog` 原先将 `config.model` 合成为 OpenRouter 条目；未装配提供商目录时还会把 `model_routes` 与当前模型合成为目录，导致设置空态下仍出现保底模型。现移除这两条合成路径、`ModelSpec`／`_synthetic` 及专用双语说明，目录继续投影已配置提供商和已登录订阅账号。`test_model_catalog.py` 经真实 `/api/v1/models` 路由验证环境变量、旧默认与可见性设置、无提供商目录、提供商移除及重开、账号准入和去重，并保留正常模型身份、容量、思考档位、默认选择与当前隐藏模型仍可见的行为；读取保持原配置和文件字节。`test_catalog_authoring_image_language.py` 的英中英回归改为核对空目录，图片与插件错误的语言断言继续保留。当前目录规则见 [模型提供商](model-providers.md)。

- 本地语义 worker 在启动退出或响应 EOF 时，`memory_semantic_worker.SemanticWorker` 原先立即调用 `_failure()`，异步 `_drain` 可能尚未读到 stderr；随后 `search` 虽执行 `_dispose`，对外错误已冻结为空诊断。现先等待所属进程和日志收尾，再构造同一 `memory/semantic-worker` 错误。`test_crash_diagnostic_waits_for_owned_log_drain_before_reporting` 用日志释放屏障覆盖启动和请求阶段，核对原始中文诊断、错误码及进程释放；相同回归对修改前实现的两个阶段均失败。这说明诊断丢失已修复，不能据此将未复现的历史子进程退出归为某个环境原因。服务生命周期规则见 [扩展机制](extensions.md#可选本地语义记忆候选)。
- Key 池构造原先直接调用 `_load()`，后续入口才由 `_transaction` 取得旁路锁，首次读取可能与另一实例的 `Path.replace` 发布竞争。构造现使用同一 `state_lock`；`test_key_pool_transactions.py` 以发布与读取屏障验证构造不能提前读取，并保留跨进程失败标记合并和保存失败后的重试。原先各处 Windows 锁适配在加锁前为初始空文件写占位字节，多个首次调用可在未取得锁时争用该字节；当前统一由 `core/file_lock.py` 直接锁定空区域，`test_file_lock_resources.py` 核对空文件互斥、跨进程竞争、原字节保持及释放后重入。产品规则见 [存储](storage.md)。
- 目录 HTTP 夹具只捕获断管和连接重置，Windows 写入抛出 `ConnectionAbortedError [WinError 10053]` 时不会设置断开事件，导致 `closed.wait(2)` 失败。当前响应头与正文共用 `ConnectionError` 边界，受控 10053 回归核对断开事件和单次请求。另一独立缺陷是 `catalog_fetch.read_catalog` 使用 `subprocess.run(timeout=...)` 仅终止首进程，读取器后代继续持有匿名输出与 HTTP 连接，Windows 的超时收尾仍等待管道 EOF。当前复用所属进程组／Job Object 和匿名输入，整个组退出并排空输出后才返回；凭据不进入参数，输出仍有界，回收未确认保留原异常并通过 `ProcessCleanupError` 阻止工具降级。`test_catalog_total_deadline.py` 覆盖启动预算、持续响应、真实派生读取器、取消、读取上限和停止失败；原先数百毫秒启动假设不再作为 HTTP 业务前提。
- `owned_process.spawn_owned` 与 `conpty_process.spawn_conpty` 原先将原生创建包装为子 Task，创建自行取消或循环同时取消创建者与子 Task 时，会在已取消结果上无限重试，也可能丢失线程迟到交付的句柄。当前共享 `create_owned`，保留上下文的执行器 Future 不进入循环的 Task 取消快照；自行取消原样传播，观察者取消继续等待唯一创建与所属回收。`test_process_creation_cleanup.py` 用独立解释器的外部截止时间覆盖这两个入口的自取消和整体取消快照，并保留迟到句柄及重复取消屏障回归。

- 本机 OpenCode 的系统上下文曾由 `895ffe052` 引入，`302afaad8` 清理时移除。此次对照同一 `https://opencode.ai/inference/openai/v1` 地址，原生请求和保留完整原生请求体的 Bun 转发均成功；删除上下文返回 `FreeUsageLimitError`。恢复上下文后，当前 Harness 请求中的 `spawn_agent` 仍触发相同拒绝；仅将该协议名映射为原生 `subagent` 即成功，其余工具定义无需移除。现恢复显式上下文与容量计入，并在请求、历史及响应之间双向映射名称，调用身份、参数和权限仍归 Harness。设置入口同时拒绝缺失资料后的 HTTP 静默回退；准备流程固定 `PWD`、私有服务与临时数据，避免钩子落在错误项目及常驻端口冲突。`test_opencode_local.py` 覆盖真实供应商分发、协议往返、分片工具调用、名称冲突、英中英消费、拒绝原文及进程回收。macOS 的 OpenCode 2.1.4 已通过 `deepseek-v4.1-flash-free` 设置链路在线文件读写、历史续接及另一对话隔离；`big-pickle` 也通过单接口在线检查。该实测不证明供应商识别规则完整、免费配额永久可用或其他操作系统已经验收。产品配置与生命周期统一见 [本机 OpenCode](opencode-local.md)。
- 本机转发曾丢弃 stderr，并将超时、断流、非法元数据及非零退出统一转为不可重试的 `llm/opencode-local-runtime`。本机历史追踪中，一次无模型事件的请求约 60 秒失败，另一次交付部分事件后失败；旧记录缺少底层诊断，不能追认具体网络原因。当前 `opencode_local.mjs` 通过独立错误管道输出固定分类，`OpenCodeLocalTransport.post_stream` 有界排空并保留阶段、字节数与已确认退出码，瞬时网络故障交回适配器的既有重试边界。`test_opencode_local.py` 经真实子进程及适配器验证无内容重试、部分内容不重放、不完整工具不交付、英中英资源消费及超限诊断；`model-runtime/tests/opencode-local.test.mjs` 直接运行实际转发脚本，验证请求映射、空闲重置及故障协议。未知退出及损坏诊断仍不可重试；此修复不保证远端服务不再断流，也不改写历史失败或重放既有工具。

- 生成配置原先暴露单次思考／总输出字符限制，并在普通 SSE、原生桥接与订阅工作器中执行；普通传输还会因有效进展超时停止，工具循环和收尾纠正也有固定次数终止。现移除对应配置、环境入口、SettingsPatch 字段、实时限制快照及运行拦截，仅保留默认无限的步骤／Token 预算及默认 180 秒网络超时。`test_generation_policy.py` 核对默认值、协议字段、心跳流、重复并行读取与多次收尾纠正；`test_adapter_stream_cleanup.py` 保留网络超时和全部所属流收尾，`test_plan_completion.py` 继续以显式步骤预算验证未审批时不可写入。设置页公共输入焦点改为边界内单层描边；本机页面读取确认三项设置及焦点无阴影、无外圈。

- 轮次接收 `length`／`max_tokens` 截断响应后，原先由 `Agent._prompt` 统计正文为空或与上一段相同的次数，累计三次便抛出 Harness 自有的 `llm/output-continuation-stalled`，原生供应商仅返回思考时也会命中。现移除该固定次数终止及其计数状态，继续保留各段响应、续写关联和主动停止。`test_output_continuation.py` 核对五段相同正文／原生思考截断后仍能完成，以及多次续写后用户取消仍结束唯一轮次；旧错误卡片保留历史诊断呈现。
- 输出能力未知而上下文较大时，`provider_runtime.provider_stream` 的 `normalize_request` 曾用上下文余量生成请求预算；`bridge.mjs` 将其直接传给 `streamSimple`，覆盖模型输出能力／回退值，可能发送 `max_completion_tokens: 984198`。先前修复将显式预算收敛到模型能力，并在未知能力、未设置预算的 Chat Completions 请求中省略上限。后续发现已知能力仍被当成默认预算，现统一采用本页“自定义网关模型失败与默认输出预算”的规则。`test_output_budget_cannot_expand_model_output_capacity` 保留缓存目录能力、大请求预算及较小显式预算断言，并补充已知容量、未设置预算时不发送上限的回归；日志与实际正文同时核对。
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

## 本机 OpenCode 资料失配与显式刷新

触发条件是已安装的 `opencode-cli` 内容变化，而默认资料仍绑定原 SHA-256。本机只读核对确认资料存在、可执行路径有效、摘要不匹配；不能把这次失败归因于远端网络。原 `configured_transport`／`load_profile` 将所有资料问题合并成笼统错误，`bootstrap_profile` 又拒绝覆盖现存目标，缺少保留原件的原路径刷新流程。

当前调用链为提供商分发 → `configured_transport(required=True)` → 严格资料读取与 `load_profile`。错误保留原代码，新增具体原因、路径、摘要差异与显式双语资源；`modelFailure` 为资料问题提供独立指引。CLI 的 `--refresh-backup` 经 `refresh_profile` 先准备并校验候选，再独占备份、回查原件并原子替换；失败、取消、备份冲突和已检测到的并发修改不自动重放。配置与并发限制统一见 [本机 OpenCode](opencode-local.md)。

`test_opencode_local.py` 的新增针对性用例覆盖原因分类、读取权限、英中英资源消费、成功刷新后的真实配置加载、失败／取消／无效捕获、备份冲突、准备期间的原件变化和最终替换失败；原设置入口及损坏资料回归同步调整，`opencodeProfileLanguage.test.ts` 核对真实提示投影与浏览器资源消费。此次 macOS 本机已通过隔离捕获、显式刷新、默认资料重新加载及备份权限核对，未发送模型生成请求；这不代表在线模型调用或 Windows 刷新已实机验收。

## 待验证或尚未完整迁移

在线供应商、OAuth 实际登录与刷新、真实云凭据及各操作系统安装包尚未逐项实机验收。DeepSeek 已具备专用账号认证、Messages 请求、目录和最低版本检查；源实现的 Files API 持久文件引用、图片卸载及历史内系统／工具更新等扩展尚未完整迁移，不能据此宣称 DeepSeek 专用能力全部等价。TypeSafe 的源目录没有聊天模型，不能将该供应商模板存在视为聊天调用通过。
