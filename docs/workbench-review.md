# 工作台与本机管理接口审查

本记录核对工作台 QA 和本机热部署 QA 的结论，保留触发条件、当前调用链与针对性回归。产品行为分别维护在 [接口](api.md)、[模型提供商](model-providers.md) 和 [扩展](extensions.md)，这里不将现场报告中的推测升级为已证实缺陷。审查未读取 research 目录，现场追踪仅作只读核对；运行验证使用受控传输和临时数据，不调用在线模型或改动用户配置。

## 已确认问题与对应处理

| 来源 | 触发条件与源码依据 | 当前处理与回归 |
|---|---|---|
| 工作台 QA-01 | 草稿保留旧 `creationRequest`，随后更换项目、检出或智能体；`app.ts:materializeSession` 原先回查完成后因作用域不同直接返回，要求用户再次发送 | 只有旧身份经 `sessionCreation.ts:recoverSessionCreation` 唯一完成记录及详情核实后，才释放身份并在本次发送创建当前对话；提交只归当前对话。未知结果继续只读，回查期间切换则停止后续创建。`sessionCreationRecovery.test.ts` 覆盖三个作用域变化、未知结果和迟到恢复 |
| 工作台 QA-02 | 启动时内存中的活动会话已被删除，但 `app.ts:boot` 原先直接保留它，只有持久偏好经过列表校验 | 内存与持久选择都与最新目录求交，空目录清除选择，否则使用有效后备项；`creationCatalogOwnership.test.ts` 核对选择、持久偏好和媒体根。当前 boot 自身不请求详情，现场“详情 404 导致列表失败”的完整归因不能仅由该函数证明 |
| 工作台 QA-03 的命名推理配置 | 复验请求 `d0fb6c6c…` 的只读追踪确认约 30 秒后取消，头信息为 `timeout`；归档正文有 256 token 上限但没有推理参数，而所选连接目录明确支持 `off`。`SessionTitles.generate` 原先传 `reasoning_effort=None`，表示模型默认，不能表达关闭推理 | 能力明确支持关闭且未强制推理时，仅在命名请求副本选择 `off`，清除继承的同名扩展正文覆盖；能力查询也计入原超时。`test_title_disables_reasoning_only_when_selected_model_supports_off` 覆盖能力边界、扩展覆盖和原配置保留；`test_capability_lookup_shares_title_deadline_without_sending_or_replaying` 核对超时、清理和不重放；`test_title_off_reaches_provider_bridge_and_archived_request_without_changing_settings` 经本机 HTTP、实际提供商桥接及归档验证两种推理字段映射。未证明现场超时一定由推理导致，也未实测在线延迟改善 |
| 工作台 QA-03 的就绪提示 | `ReadinessCheck` 读取 `config.has_api_key`，无法表达其他连接与订阅来源 | `Harness.status` 提供 `has_model_credentials`，界面消费与执行、标题相同的判定；`test_model_readiness.py` 经真实状态路由、提供商存储及标题消费者核对无主 Key 的来源，`projectCatalogRecovery.test.tsx` 核对双语提示，`creationCatalogOwnership.test.ts` 核对关闭设置后的凭据刷新及迟到回包隔离 |
| 工作台 QA-03 的命名可观测性 | `SessionTitles.generate` 原先直接调用适配器，未传请求观察器，fallback 也没有原因字段；因此仅凭追踪库没有记录不能判断未调用模型 | 命名物理传输接入既有追踪，标记 `purpose=title`，会话头保存 fallback 原因、逻辑请求号和异常类型；不补造未发送记录、不重放历史命名。`test_session_titles.py` 经真实适配器验证归档、空响应与异常、持久重开、手动改名和删除隔离；`ExecutionTracePanel.test.tsx` 验证命名标签与英中英 |
| 复验 N1 | 选择器各区域总高度可超过视口，目录区原有最小高度进一步撑开；确认按钮可能落到视口外。底部辅助说明在本次修复前的基线中已存在，不能将其出现本身当作新增变更证据 | 选择器按视口限制高度，正文独立滚动，确认区移到最后并保留独立空间，极低高度仍允许面板滚动。`workspace-picker-height-smoke.mjs` 新增按钮无需预先滚动即在视口内、命中测试与真实确认选择的浏览器回归；本轮只做语法核对，未运行浏览器专项 |
| 复验 N2 的提示与重读入口 | `ConversationCheckout` 在页面隐藏时不启动读取，这是既有观察策略；初始状态只显示检查中，无法解释等待原因 | 隐藏时明确提示后台暂停并在返回后自动恢复；可见的检查等待态提供只读重新检查。`ConversationCheckout.test.tsx` 验证同实例语言切换不读取、恢复后单次检查、取消旧读取及更换目录后的归属。后台暂停策略保留 |
| 工作台 QA-05 的状态说明 | 项目目录已删除，后端返回 `exists=false`；选择器已有禁选，但未说明原因，侧栏无状态标识 | 选择器打开时重读目录，选择器与侧栏均标明目录不存在；保留历史与已有移除入口。`projectCatalogRecovery.test.tsx` 核对标识、禁选及历史保留。目录在最后一次读取后仍可能被外部删除，后端继续校验实际目录 |
| 工作台 QA-06 的空会话名称 | 已物化会话无消息、无标题且 `auto_title=awaiting`，`sessionTitle` 原先只给出未命名提示 | 统一显示草稿对话，不自动删除、迁移或隐藏；组件回归核对原记录保留。复验读到先前已创建的空记录仍存在符合该边界，不能视为再次创建；如需删除应使用现有显式删除入口。创建与首次提交之间仍可能存在空记录，创建结果未知时不能据此判断无副作用 |
| 工作台 QA-08 | 已有对话继承全局思考强度时，`ModelMenuInstance` 同时高亮具体档位，且标题与候选重复显示模型名 | 跟随全局作为独立互斥选项；显式档位、模型默认各自表达，菜单标题标明选择操作。`modelMenu.test.tsx` 覆盖继承、覆盖、模型默认和全局编辑 |
| 工作台 QA-09 | 设置卡片按窗口断点使用三列布局，嵌套窄容器的模型名被截断，提供商实际 ID 不显示 | 名称及实际模型 ID 换行，操作另起一行，容量指标保留。`providerModelPreferences.test.tsx` 验证实际 ID 和输出容量；具体窄屏布局仍需浏览器验收 |
| 热部署 P2-05 | `packages.py:_snapshot` 使用 `sorted(source.rglob('*'))`，在文件数量检查前已遍历完整目录 | `_snapshot_paths` 增量枚举，文件及子目录分别有界，超限即关闭枚举句柄；限额内保留原全路径顺序，避免改变审核摘要。`test_plugin_preview_bounds.py` 核对提前停止、原件保留、摘要及复制字节；不以该测试宣称固定毫秒级性能 |
| 热部署 P2-06 | 全部启动资源均不可用时，HTTP 失败返回值不抛异常，`app.ts:boot` 原先仍发布空工作台 | 全部失败且无状态快照时进入已有全局失败／重试页面；重试期间保持连接界面，确认数据和草稿不清除。单项目录失败仍局部降级。`creationCatalogOwnership.test.ts` 覆盖完全失败、重试及恢复；`http.test.ts` 验证网络主提示资源化，原浏览器诊断保留为 `details.cause` |
| 热部署 P2-07 | 隧道启动通过按钮调用，HTML 输入约束未参与提交；`McpServicePanel.change` 原先直接转换端口 | 启动前检查整数端口、HTTPS 根地址、令牌及复用状态；非法值不写入，停止不受草稿错误阻挡。`McpServicePanel.test.tsx` 通过实际 HTTP 传输替身核对无启动请求、输入保留及同实例英中英，原后端拒绝测试保留 |
| macOS 固定隧道端口冲突与重启误报 | `mcp_tunnel.DEFAULT_TUNNEL` 与 `codex_gateway.desktop.DesktopIntegration` 原默认端口均为 18765；本机只读核对确认该端口由 `harness.codex_gateway` 监听。`McpService._launch` 原使用无地址复用的 bind 探测，隔离回环连接的服务端主动关闭后可复现 TIME_WAIT 导致的 EADDRINUSE | macOS／Windows 新配置默认 MCP 端口统一为 12345，面板与后端同步；已保存配置不迁移。端口探测先于隧道准备，POSIX 启用地址复用并核对可监听，Windows 保留默认绑定；仅 EADDRINUSE 使用占用提示，其他原始错误继续报告。`test_mcp_named_tunnel.py` 的定向用例覆盖新默认值与原配置字节、真实 TIME_WAIT、双平台探测策略、权限错误及真实占用；`McpServicePanel.test.tsx` 覆盖默认与回填、占用提示英中英及无重放。Windows 分支使用替身，未进行 Windows 实机或真实 Cloudflare 连接验收 |
| 热部署 P2-08 | `CapabilitiesPanel.NumberField` 在失焦时直接提交负数、小数或将必填空值转零 | 失焦前置正整数与安全范围校验；可选预算空值仍为 null，必填超时空值拒绝，组合输入不提交。`CapabilitiesPanel.test.tsx` 经实际父组件、字段及 HTTP 替身核对三个设置的无非法 PATCH、草稿及空值语义 |
| 热部署复测 N1（插件 I/O 错误） | 增量扫描对不存在目录直接抛出 `OSError`，插件 API 的 `plugin-package/io-error` 分支原先将 `str(exc)` 作为用户提示，回显英文 errno 与绝对路径 | 该分支改用共享双语资源，保留原 HTTP 状态和错误码；响应 details 仅提供资源身份与 errno，原异常链和完整诊断进入服务端日志。`test_plugin_io_error_hides_paths_preserves_diagnostics_and_uses_shared_resources` 经实际缺失目录扫描及受控权限错误核对路径不进入响应、日志仍保留原因、英中英与无文件写入；`httpErrorLanguage.test.ts` 的对应错误码用例核对实际传输到页面资源消费 |
| 热部署 P2-09 的重复错误前缀 | 插件 API 对 `HarnessError` 使用 `str(exc)`，把日志用的 `[code]` 再次放入用户正文 | API 使用 `exc.message` 并保留状态、错误码和 details；`test_plugin_validation_language.py` 核对冲突、普通错误与英中英。解析位置与请求路由仍保留用于诊断；通用插件文件 I/O 错误另按资源提示与服务端日志分层处理 |

## 本机来源策略的收紧

`Handler._origin_allowed` 原先特例放行任意 localhost 的 5173 来源；`do_OPTIONS` 未执行实际请求的来源拒绝流程。当前移除该例外，预检与请求共用来源检查，拒绝来源不附加 CORS 许可头，损坏的来源也明确拒绝。无 Origin 的原生本机客户端继续可用；`Sec-Fetch-Site` 收集所有重复头及逗号分隔值，先去除首尾空白并统一大小写；跨站／同站跨来源值既不能以缺少 Origin 放行，也不能用同源 Origin 覆盖。`test_mcp_discovery_cors.py:WebCorsTests` 核对开发代理保留 Host、其他端口、恶意及损坏来源、预检、错误响应、大小写／空白变体和原生客户端。Vite 开发配置禁用自身 CORS 预处理，将 API OPTIONS 及响应头交给代理后端；`dev-api-cors.test.mjs` 用实际 Vite 服务和隔离 HTTP 后端验证拒绝预检不再提前返回 204、拒绝响应不被追加许可头、允许预检沿用后端方法列表及 Host 保留。这处理了热部署 P3-13 与复测 N3／N4 的入口一致性问题，属于受控 HTTP 验证，未代替真实跨站浏览器验收。

这对应热部署 P2-04 与 P3-13 中有价值的收紧部分，不等于证实了报告中的跨站写入链。报告使用的无 Origin 原生 HTTP 请求不能代表浏览器跨站表单；任意本机进程仍拥有完整管理能力。本轮没有引入 CSRF Token、远程管理认证或新的可信根白名单，也未实测不同浏览器的 Fetch Metadata 行为。

## 全接口 QA 的问题裁决

现场的 Q01–Q59 不是 59 个独立、已证实且同等级的缺陷：其中含重复根因、正常管理能力、加固建议与未证实风险。以下按当前路由、协议源、最终消费者和隔离回归裁决；没有向用户的常驻实例写入测试数据，也未清理现场遗留附件。报告中的请求数、生产表现及安全严重程度不作为本轮验收依据。复验按“行为仍与首轮一致”统计的未修复项，仍须核对该行为是否违反现行合同，不能直接沿用首轮的严重程度；无正文动作缺少 requestBody 也不是契约缺口。

| 来源 | 判定、价值与当前处理 | 源码／回归依据 |
|---|---|---|
| Q01、Q19、Q48 | 同一代理遗漏，有修复价值；整个 API 命名空间交给后端，Accept 不影响未知 API 的 JSON 404，类似 `/apiculture` 不被误代理 | `vite.config.ts`；`dev-api-cors.test.mjs` |
| Q02 | 代理原先等待首段正文才发送请求头，已改为立即发送。完整上传另有早期拒绝竞态：上游已返回 413，代理仍发送正文可先触发 EPIPE，破坏客户端回执。代理现向后端协商 100-continue，通过长度准入才继续正文；后端超限直接发送最终拒绝，不先确认继续上传 | `Handler.handle_expect_100`、`_build_request` 与 Vite 代理；原始 socket、完整超大正文、Expect 客户端、正常正文及持久连接下一请求的定向回归 |
| Q03、Q20 | 现场差异存在，但 FOO 与畸形长度在 Node HTTP 解析器阶段被拒，产品中间件无法接收这类报文；为统一 JSON 自行替换 HTTP 解析器代价与风险过高。文档明确开发入口边界 | `vite.config.ts` 使用 Vite 原生 HTTP；后端 `send_error` 与 `_build_request` 保留原统一拒绝路径 |
| Q04、Q44 | “无长度上限”不成立：普通 HTTP 正文已有 4 MiB 上限。50 万字符被正常受理和 10 万字符草稿可保存不证明故障；没有固定输入的卡顿、配额失败或存储增长预算证据，不新增正文截断及草稿压缩 | `server.MAX_REQUEST_BODY_BYTES`；`test_http_body_limits.py` 将真实运行路由接到受控提交替身，核对 50 万 ASCII／补充平面字符的有限正文保留原文，超出 4 MiB 在读取和提交前拒绝，不触发真实模型；现场草稿测试仍可响应 |
| Q05、Q43 | 显式标题确无单独预算，有约束价值；创建和 PATCH 最多 512 个 Unicode 码点，写入前拒绝，原有数据不改写。完整标题进入 DOM 本身不是缺陷，也不截断用户已有文本 | `input_schemas.py`、`sessions.py`；`test_session_rejections_happen_before_creation_or_update`、`test_valid_title_boundary_preserves_original_unicode_and_creates_once` |
| Q06 | 成立：未知 `/ws/` 子路径曾进入静态首页回退；现于握手前返回 JSON 404，来源拒绝仍为 403 | `Handler.parse_request`；`test_unknown_ws_paths_cannot_serve_spa_or_upgrade` |
| Q07 | 部分成立且有价值：缺请求体的操作包含本就无参的动作，不能要求所有 POST 都有 requestBody。已为实际读取 JSON 的管理入口补字段、版本及引用，保留无参动作；新增协议检查追踪真实处理器，不按数量制造通过 | `request_contracts.py`、`protocol.py`、生成器及 `test_json_consumers_publish_request_fields_and_bodyless_actions_remain_bodyless` |
| Q08 | 成立：monthly/yearly 实现与公开分支不同步；创建和预览均补齐六类日程及日期边界 | `protocol.py`、`app/schedules.py`；`test_calendar_schedule_contract_accepts_all_current_kinds` 同时核对真实日程准入 |
| Q09、Q29、Q36、Q52 | 所选目录、规范化路径与设置中的本机目录属于已声明的管理能力；插件 I/O 的路径隐藏策略不能外推为所有管理 API 均禁止展示路径。没有共享入口越权证据，不增加任意目录禁用或自动遮罩 | `docs/api.md` 本机管理边界、`workspaces.workspace_options`、设置投影 |
| Q10、Q59 | 判断逻辑确有缺口，收紧有价值；检查所有 Fetch Metadata 值，Origin 不能覆盖拒绝。本机原始客户端伪造头不等于已证明浏览器跨站利用或请求走私 | `Handler._origin_allowed`；`test_fetch_metadata_checks_every_header_and_origin_before_dispatch` |
| Q11、Q21 | 开发服务读取 Web 源码及调试依赖是正常功能；越界请求返回首页不能证明读到了凭据。`cors: false` 不授予跨源脚本读取权限，原生工具伪造 Origin 得到 200 不证明浏览器可读取正文；不关闭开发调试能力 | `vite.config.ts`、开发与生产入口边界；现场记录明确越界响应为 SPA HTML |
| Q12、Q37、Q50 | 有限响应放大值得收紧，但“无限”及 XSS 定性过强。路由回显限制为前 200 码点，超长附件名在解码落盘前拒绝；并未承诺所有业务诊断统一截断，也不改写已有错误原文 | `Router.resolve`、`UPLOAD_SCHEMA`；`test_route_error_echo_is_bounded_without_changing_route_identity`、附件准入回归 |
| Q13、Q55 | 尚未证实注入链：JSON 中的 HTML 字符合法；存在 dangerouslySetInnerHTML 不证明其消费了错误正文。没有来自错误字段到 HTML sink 的调用链，不进行全局 HTML 转义，以免用户原文被双重编码 | `Request`／响应 JSON 边界；现场净化样本通过只能证明那些样本，不能保证全部 XSS 安全 |
| Q14 | 校验先后可稳定统一；无效游标先于会话读取拒绝，不触发重放 | `sessions.session_events`；`test_live_events_invalid_query_precedes_missing_session` |
| Q15 | HEAD 未映射 GET 的事实成立；现共用 GET 的路由、授权与响应头，仅省略正文，不进入 POST 处理器 | `Handler._dispatch`、`_send`；`test_head_matches_get_headers_without_body_or_write_execution` |
| Q16、Q22、Q56 | 非严格查询入口忽略额外字段的事实存在，但并非所有路由承诺 additionalProperties=false；全局拒绝会改变现有调用协议。未证明错误业务行为或固定性能损失，不把所有入口强制改成严格查询 | `query.QUERY_SCHEMAS` 仅声明具体入口；其他入口仍由各自处理器消费参数 |
| Q17 | 缺少单项 DELETE 是功能缺口，不能直接当作高危缺陷。内容寻址附件可被多个对话引用，未经引用生命周期设计增加删除会破坏历史；本轮不新增自动回收、不操作用户附件 | `AttachmentStore` 内容寻址、`api/attachments.py` 回读入口 |
| Q18、Q27 | 写入前识别拼错字段有价值；附件顶层与每项、会话创建及 PATCH 均拒绝未知字段，附件类型与数量预算同步公开契约 | `input_schemas.py`、`sessions.py`、`attachments.py`；准入回归核对不初始化仓库、不创建会话 |
| Q23 | 成立：非法 title 类型会退化成空更新诊断；现以 session/invalid-field 明确拒绝，模型与思考强度的非法类型同样先拒绝 | `sessions.update_session`；会话准入测试 |
| Q24 | 英文解析原句混入产品提示成立，已改为双语资源；解析位置保留结构化字段，非法 UTF-8 同样明确拒绝 | `Request.json`；`test_bad_json_is_bilingual_and_diagnostics_are_structured` 与 `http.test.ts` 的同实例语言切换、资源校验及零重放回归 |
| Q25 | 已登记共享错误资源的重复机器码后缀已去除。复验改用不存在会话，`actor → SessionAccess.snapshot → SessionStore.get` 产生未登记的 session/not-found，因而落到未知码兜底；已为该已知码登记双语资源，保留原 403 和未启用共享的 409。真正未知的码仍保留诊断身份，不通过全局剥离丢失证据 | 双语资源、`error_messages.py`、`collaboration_error_message`；`test_missing_session_and_disabled_sharing_use_registered_messages_without_mutation` 验证真实授权读取及原件不变，`collaborationError.test.tsx` 经实际问题读取入口核对同回执英中英、机器码独立及零重读 |
| Q26 | 非缺陷：语言由实例设置决定，Accept-Language/X-Language 未定义为覆盖入口；已在接口文档说明 | `Handler.parse_request`、`test_http_request_language.py` 的长连接实际资源消费 |
| Q28 | 未声明 Content-Type 仍按 JSON 解析是现有协议，不会绕过正文、来源和业务准入；没有必须拒绝的产品契约，不增加媒体类型限制 | `Request.json`、`Handler._build_request` |
| Q30、Q31 | 非法追踪号替换、空格与点的允许集是当前选择；合法 header 字符本身不证明注入。浏览器生成合法 UUID，恢复身份与幂等键另有合同，不因建议更换关联协议 | `Handler._build_request`、`api.ts:createSession`、创建回查合同 |
| Q32、Q33、Q34 | OPTIONS 统一预检、尾斜杠归一化及前导零是可接受的协议选择，不自动构成错误；尤其 `[0-9]+` 同样接受前导零，原报告建议不能解决其所称问题 | `do_OPTIONS`、`Router.resolve` 路径分段、`read_query` 数字转换 |
| Q35 | Server 版本头属低价值加固建议，当前本机管理威胁边界下未发现功能故障或利用链，不为隐藏指纹替换传输行为 | `BaseHTTPRequestHandler` 及回环部署边界 |
| Q38、Q39 | 大响应的实测大小成立，但不等于性能故障；优先接入已有 gzip 协商，保留 JSON 内容及 no-store，不引入分页或不可靠缓存 | `provider_connections.providers`、`v1.schema`、`bytes_response`；`test_large_catalogs_negotiate_gzip_without_changing_payload_or_cache_policy` |
| Q40 | 目录有不同消费者与视图投影；没有固定输入的耗时与网络测量，不按响应大小直接重构成分页。已有目录使用 Accept-Encoding | `catalog.py`、插件目录及其调用方；不宣称本轮已优化全部目录 |
| Q41 | 准备接口忽略畸形 JSON 的事实成立；现先校验空对象或空正文，再启动引擎，非法输入不触发准备。合法准备耗时仍属于实际引擎工作，不承诺固定响应时间 | `voice.prepare`；`test_voice_preparation_rejects_bad_json_before_engine` |
| Q42 | 现场只用 textContent 检测，未证明提示可见；源码确认未激活组件会保留 loading 文本。移除隐藏状态的无请求提示，继续保持隐藏不读取；错误保存资源身份，语言切换不重发请求 | `TerminalTypeSelect`、现场 `qa_ui_final.mjs`；`TerminalTypeSelect.test.tsx` 核对隐藏、恢复、迟到结果和同实例英中英 |
| Q45 | 成立：统计吞 OSError 可返回不完整数字；现明确失败，合法未初始化仓库仍为零。若附件已写入后统计失败，以 503 和已写入引用报告，不能伪装成写入前拒绝 | `AttachmentStore.stats`、上传出口；统计失败与写入后回执回归，前端核对资源及零重放 |
| Q46 | 不同领域的非法标识不必共用错误码；附件有意用 404 隐藏探测细节，未证明越权或错误状态，不机械统一 | `attachments.fetch` 的非法 ID 拒绝说明、会话不变量错误 |
| Q47 | 当前同源流程不需要跨源凭据许可；没有需支持跨端口 Cookie 的合同，不添加 Access-Control-Allow-Credentials 扩大来源能力 | 来源与共享入口既有边界 |
| Q49 | URL 路径大小写、编码和异常分隔不应未经契约就自动纠正。规范 API 命名空间已修复；未证明这些路径应与正常路由等价，不新增宽松 URL 解码代理 | `vite.config.ts` 命名空间匹配与 `Router.resolve` |
| Q51 | 缺路径与相对路径均要求重新选择项目，现有诊断可操作；细分错误不是已证实故障，不额外扩展错误协议 | `workspace_options` 字段检查 |
| Q53、Q54 | 中文／English 为语言自称；代码和图表排版已有明文例外，报告引用的旧 AGENTS 行号不能替代当前约束，不改成普通正文排版 | `SettingsDialog`、`project-contract.md` 的内容排版例外 |
| Q57 | 复验在 DOMContentLoaded 立即读取，而语音正文和 i18n 为异步模块，确有启动占位语言与 HTML lang 暂时不一致的窗口；此前仅以最终 i18n 消费推断启动期无误，证据不足。语音 bootstrap 现与主入口一样，在显示占位前按同一 initialLocale 写 lang，不等待正文模块或完整语言包；原组件的语言监听独立于桌面 bridge，无桥接时也同步提示与 lang | `voice-bootstrap.ts`、`i18n/index.ts`、`VoiceCapture.tsx`；`voiceBootstrap.test.ts` 挂起真实入口的动态依赖，核对英中英启动选择、超时失败文案及对应 lang；未运行真实浏览器复验 |
| Q58 | 主题由 head 中同步脚本应用，报告自身未观察到闪烁；没有首帧错误证据，不新增服务端主题状态 | `index.html` 启动脚本 |

本轮验证为定向 Python、Vitest 与隔离 Vite HTTP 用例，不代表完整业务回归、安全审计、在线模型、生产构建或浏览器／桌面实机验收。新协议回归对 JSON 消费处理器核对 requestBody，引用检查覆盖生成文档；这不等于对每个写入口执行真实副作用验收。压缩回归只证明固定 JSON 字节可无损协商和缩小传输，不承诺现场延迟改善。

## 开放清单 P-01–P-07 的裁决

该清单再次使用首轮期望筛选行为。以下分别核对探针是否真正观察了结果、操作本身的用途、验证环境和现有管理边界；不把测试入口失败自动归因于产品代码，也不通过吞掉日志错误或绕过预检制造通过。

| 来源 | 当前结论与依据 | 处理与针对性验证 |
|---|---|---|
| P-01 | 原探针写异常后将 resp 直接设空，证据不足；但补充真实 Vite＋正常 fetch 的完整上传用例确实复现代理上游 EPIPE，客户端没有取得 413，因此此前仅按探针问题关闭的证据也不完整 | 代理通过 100-continue 等待后端长度准入再转发正文；后端延后临时回执，超限直接拒绝。实际 Handler 核对写失败后读取 413、超限／畸形长度不发送 100、合法正文单次执行及长连接标记复位；真实 Vite＋受控后端核对完整超大正文的 fetch／Expect 客户端都取得 413，并保留正常正文。未读取、缓存或 drain 后端超限正文，未重放用户常驻实例写入 |
| P-02 | 不是 Q41 的实现落差：空正文／空对象正是显式预热请求，合法请求必须进入模型准备，不能直接伪造 ready。首次可能下载并加载模型；15 秒探针超时不足以证明挂死 | `voice.prepare` 和客户端均使用 630 秒等待预算；Hex 启动等待为 15 秒、模型准备 HTTP I/O 超时为 600 秒，已确认的同端点同选择由管理器复用。`test_empty_body_and_object_prepare_the_engine_with_the_declared_wait_budget` 验证真实路由的两种合法正文与预算；`voiceApi.test.ts` 核对 15 秒后仍等待真实回执且不重发。等待预算不保证底层任务撤销或绝对完成时限，冷启动及下载延迟未做性能验收 |
| P-03 | 成立：VoiceCapture 将存储监听放在桌面 bridge 准入之后，无桥接普通浏览器会提前返回，缺少后续语言同步 | 语言／主题监听独立于桥接效果，卸载撤销原监听；无桥接仍只显示桌面能力提示，不录音、不预热、不转写。`VoiceCapture.test.tsx` 经真实组件核对同实例存储事件的英中英、HTML lang、提示文案及监听收尾；未运行浏览器专项 |
| P-04 | 成立：startWorker 的场景日志和生命周期日志仅调用 end 而未等待异步打开及关闭；子进程 exit 也不代表输出管道已排空，调用者随后删除临时目录会触发 ENOENT | 场景回执等待所属日志完成；关闭共享同一 Promise，等待进程 close 与全部日志流完成。立即观察日志异步异常，在所属操作中原样抛出；真实 ENOENT 仍判失败，不做忽略。`browser-runner.test.mjs` 以实际受控子进程验证最后输出、并发关闭、延迟日志打开、打开失败传播及超时回收。未运行完整前端聚合入口，不能据此宣称全部阶段通过；一个阶段失败后 && 不启动后续阶段本身不是缺陷 |
| P-05 | 现有日志明确显示 WorkBuddy 注入的 sitecustomize 在已存在目录上破坏 mkdir(exist_ok=True)；拒绝发现是预检的既定目的，不应在仓库中按 shim 名称绕过。discover 运行 25 分钟无汇总也不能单独证明挂死或全部失败 | 保留预检与原始异常。本次相同仓库解释器的独立预检未加载 sitecustomize、支持 dir_fd 且通过；这不证明原 WorkBuddy 上下文已恢复。`test_broken_exist_ok_stops_before_discovery_with_original_diagnostic` 确认异常环境不进入课程探针或测试发现。未执行全量 Python／课程沙箱验收；原宿主环境限制仍未解决 |
| P-06 | 不是可由所列请求证明的故障：未提供服务端实体标签，却发送任意 If-None-Match 得到 200，不构成“应返回 304”的依据。未声明 gzip 时返回完整表示也是当前协商选择 | 接口文档明确 OpenAPI、提供商及工具目录保留 no-store，不提供 ETag／304 契约；不引入新的客户端缓存，不改动已有内容寻址附件和静态文件的 ETag 行为 |
| P-07 | 本机管理信任边界包括配置、登录与服务启停，不限于目录读取。MCP 服务管理还核对回环 IP、Host 与 Origin；原生本机管理调用方不靠浏览器凭据认证。没有声明所有管理动作均有统一幂等键或审计账本，不能仅凭 200 或无 requestBody 证明越权 | 在接口文档明确管理动作范围及无统一审计／幂等保证；共享身份继续按各自权限准入，结果未知不得自动重放。未操作真实账号或服务状态，不将统一审计、远程管理认证作为隐式新增功能 |

## 后端回归清单 R-01／R-02 的裁决

| 来源 | 触发条件与源码／现场依据 | 当前处理与验证边界 |
|---|---|---|
| R-01 | 默认环境日志保留 WorkBuddy sitecustomize 在 mkdir(exist_ok=True) 上抛 EEXIST 的原始栈；无 shim 的定向日志仅剩 R-02，不能据此推断全量通过。报告指定的无 shim 聚合预检日志实际只有 env: timeout: No such file or directory，命令未执行，不能作为 sandbox_apply 拒绝的证据 | 原宿主限制继续保留；seatbelt 失败结论待有效原始回执核对。本轮不运行全量或重新启动课程沙箱，不按 shim 名称绕过目录语义检查，也不增加无隔离降级以制造聚合通过。发现前明确失败符合当前验证合同 |
| R-02 | 预热失败回归使用仅供转写的 request() 默认 WAV，请求在 Request.json 阶段即被拒绝，未到达模拟的 prepare_transcription 引擎失败，因而无法覆盖其原有断言 | 原用例改为正确的预热路径、JSON 类型和空对象正文，保留原始失败原因、503、资源身份及单次准备断言，并确认不转写；新增 test_preparation_rejects_wav_before_starting_engine 验证音频误投返回 400 bad-json，准备与转写均不执行。仅执行这两个针对性用例，不改变生产准入行为 |

## 不成立或不宜按报告建议改动的结论

| 来源 | 核对依据与判断 |
|---|---|
| 热部署 P1-01 | `PackageSources.root` 接受本机用户选择的已有插件来源，`target` 将读、写、删交给 `packages._inside`，已检查相对路径、越界和符号链接；托管目录写入另行拒绝。报告“未做逃逸检查”与源码不符。在所选插件内创建源码是正常管理操作，不应限制为只能编辑草稿目录 |
| 热部署 P1-02 | `workspaces.create` 的目的就是显式登记已有本机目录，`workspace._resolved_root` 限制当前文件浏览选择范围，并非对同一个管理调用方的授权隔离。注册后读取所注册目录符合当前产品。已澄清误导性的沙箱描述，未禁止用户主目录或其他正常项目位置 |
| 热部署 P1-03 | 目录选择器在所选绝对父目录中创建单层文件夹、插件作者在显式目录中生成插件是既有功能；系统文件权限继续生效。`instructions` 为用户编写的技能正文，不应人为限制为固定结构。报告未证明来自共享入口的越权写入 |
| 工作台 QA-07 | 会话创建调用链未调用项目注册；目录选择器显式添加／使用项目调用 `addWorkspace`，空注册表还会通过 `ensure_seeded` 播种启动目录。旧项目仍保留也不能证明当前创建调用会自动注册；不能仅凭新项目条目推断“任意会话目录自动升级为项目” |
| 热部署 P2-10 的 Accept-Language | HTTP 请求语言来自实例已保存设置，`server._build_request` 在实际请求解析后绑定当前语言；语言切换会刷新存在本地化说明的工具、技能和模型目录。未发现 `status` 或文件条目因缺少同轮重读而残留产品译文的证据，不把请求头覆盖实例设置作为修复 |
| 热部署 P2-10 的缺键回退建议 | 缺失自有资源必须明确失败，不能通过返回键名或默认语言掩盖。未按建议降低 `t()` 的严格检查；未知键导致整页失败的黑盒响应仍未复现 |
| 热部署 P3-11 | 运行提交的追踪号与幂等键用途不同，运行回查使用 `Idempotency-Key`；对话创建另保存 `creation_request_id` 并只读核实。仅回显 `X-Request-ID` 不承诺去重，不能自动将追踪号替换成幂等键或合并不同操作的键空间 |

## 待验证与验收限制

- 工作台 QA-03 的现场 fallback 已只读确认为超时，实际请求未显式关闭推理。能力选择与传输映射已受控验证，但是否能消除现场超时仍待新的在线请求确认；没有调用在线模型，未改写历史标题或增加自动重放。
- N1 的源码布局调整与浏览器回归脚本已完成；脚本同时覆盖项目检查挂起和检查完成两阶段的按钮几何、命中及英中英。热更新入口只读核对已提供该限高与滚动布局，但本轮未执行浏览器专项；647×676 的实机按钮位置与命中仍待复验，不记录为几何验收通过。
- 工作台 QA-10 的默认模型不一致、热部署 P3-12 的目录长度变化：均缺少固定输入与时序的受控复现，不作为已确认缺陷；未修改默认模型或目录同步协议。
- 共享入口二次授权竞态、跨平台原生路径竞态及缺键造成全局页面失败：本轮未完成实机验证，不宣称已修复。
- 增量扫描有数量与字节上限，但文件系统调用仍可能阻塞；线程取消不等于底层操作已停止，未新增可取消扫描进程或缓存。
- Kimi 及其错误卡片按用户边界排除。本轮未运行浏览器／桌面专项、完整回归、lint、类型检查或生产构建，也未操作用户测试会话、项目注册表与账号。
