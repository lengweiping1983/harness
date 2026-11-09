# Superagent 模式

Superagent 只有一条运行链路：Source → Gate / Evaluation → Ticket / Mandate → Mission / PlanRevision → Scheduler → Harness Run → Report / Artifact / Review → Outbox。CLI、本机工作台与可信 SDK 宿主使用同一个 `SuperagentService` 和 `superagent.sqlite3`；任务复杂度只影响冻结计划与验收契约，不选择另一套协调器。

## 启动与本机操作

先复制并编辑 [当前配置示例](../examples/superagent.json)，填写模型标识、仓库根目录、来源身份及授权规则。相对路径以配置文件目录为基准；示例中的 `repo: ".."` 指向本仓库根目录，复制到其他目录后需相应调整。模型凭据由显式 `HARNESS_API_KEY` 或 `OPENROUTER_API_KEY` 提供。

```sh
python -m harness.cli --superagent /path/to/config.json
python -m harness.cli --superagent /path/to/config.json --superagent-once
python -m harness.cli --superagent /path/to/config.json --superagent-submit "修复已确认的问题" --superagent-request-id request-001
python -m harness.cli --superagent /path/to/config.json --superagent-submit "修复已确认的问题" --superagent-request-id request-001 --superagent-once
python -m harness.cli --superagent /path/to/config.json --superagent-status
```

单独提交、读取状态、排队指令及写入控制意图不构造模型宿主，也不读取模型凭据。提交直接写入同一协调账本，固定来源、请求身份、原文和首次事件时间；重复请求返回原 Trigger，不重放执行，同一身份不同原文明确冲突。未指定 Source / Actor / Thread 时必须能从当前本机配置唯一选择；有多个候选时显式使用 `--superagent-source`、`--superagent-actor`、`--superagent-thread`。其他身份和 Thread 在创建运行目录前拒绝。

`--superagent-once` 完成当前可推进批次后退出：成功返回 0，未知结果、不可用模型／传输、开放问题或路由错误返回 2，用户中断返回 130。等待超过 `once_timeout` 明确报错，已受理记录不撤销、不重放。未确认的协调模型提交只查询原 Run，研判不可用的相同冻结批次不自动反复创建新尝试；可信宿主可使用授权服务的显式重新排队入口。

## 当前配置

只接受 `version: 2`。旧 `router_model`、`worker_model`、`autonomy`、事件目录配置和未知字段均拒绝；不转换旧配置或旧运行目录。CLI 启动前核对账本身份，并先取得实例锁、完成协调 Schema 初始化／正常升级，再创建原生宿主。实例身份不符、迁移失败或另一个实例仍持锁时，不打开原生运行库。

| 字段 | 含义 |
|---|---|
| `instance_id`、`state_dir` | 固定实例身份和专用协调目录；配置路径与身份不能被后续启动悄悄重新绑定 |
| `language` | `en` 或 `zh-CN`，控制产品提示和原生模型调用语言，用户原文不翻译 |
| `model`、`base_url` | 协调角色与 Worker 的默认模型；Profile 可显式提供 `model` |
| `permission_policy` | `read_only` 或 `full_access`；只读策略在实际工具执行前拒绝写入，即使 Profile 中能看到该工具 |
| `sources` | SourceInstance 配置，内置 `local`、`github`、`feishu`；Source ID 使用字母、数字、下划线或连字符 |
| `profiles` | 宿主持有的 `repo`、`description`、`source_instance_ids`、`actions`、`tools`，以及可选阶段／运行预算；仓库必须是可用 Git 根目录 |
| `default_profiles` | 每个 Source 明确选择默认 Work Profile；不会根据模型提出的身份绑定资源 |
| `system_profiles` | 不同的宿主持有 Integrator / Reviewer Profile；不能兼作默认 Work Profile，Reviewer 工具必须只读 |
| `policy` | Gate、规则研判、来源白名单、Scope 与预算，完整协议见[领域模型](superagent-domain-model.md) |
| `secrets` | Secret 名称到显式环境变量名的映射，只保存引用，不把凭据值写入协调账本 |
| `interval`、`once_timeout`、`max_workers` | 可选轮询间隔、批次等待上限和资源总量上限；统一 Scheduler 保持有界并发与背压 |
| `watchdog` | 可选 `stale_after`、`max_stale_ticks`、`action`；只支持 `attention` 或 `block`，不授权自动重放 |
| `execution` | 可选远程设备与租约配置；省略时仍使用本机原生 Executor，显式配置后 Worker 只走登记设备，详见下文 |

`policy.sources.<id>.scope.thread_ids: ["$current"]` 表示在研判时绑定当前来源的确切 Thread。它不是进入 Mandate 的通配权限：Gate、创建及后续重验均解析为实际 Thread ID，不能据此订阅另一个 Thread。需要跨来源协作时，可信宿主须先显式授权确切 Source / Thread Scope，再调用 Subscription API；自动推断跨来源关联与动态重规划仍属后续产品能力。

`local` 来源需要 `config: {"namespace": "..."}`、`actors` 与 `threads` 白名单。其他来源由验证后的平台身份产生 Actor，不允许本机参数伪装远端原文或获得远端回复权限。

## 来源与既有信道

可信宿主通过 `receive_source(source_id, headers=..., raw=...)` 或 `source_ingress(source_id)` 提交原始事件；两者进入同一 SourceRegistry。可选 SourceCursor 仅在事件持久接收后按修订推进，不能代替事件去重。SDK 可通过 `open_coordinator` 注入 Executor、Assessor、Planner 和 Provider 客户端；这只是同一服务的端口装配，不是另一个运行模式。

生产 CLI 为每个启用的远端 Source 启动本机有界监听器，路径为 `/github/SOURCE_ID` 或 `/feishu/SOURCE_ID`。仅监听 `127.0.0.1`，由操作者自行配置外部反向代理；本产品不自动部署代理或访问个人连接器。

GitHub 来源配置包含 `repository`、`repository_id`、`bot_login`、`bot_user_id`、`webhook_secret_ref`。`transport` 需要 `webhook_port`，可选 `api_url` 和用于发言的 `credential_ref`。原始 Webhook 先验签再核对仓库、发送者及原 Issue。不同 Delivery ID 的同一已验证事件作为平台重投单独审计，原 Trigger 和生命周期事实保持不变；正文或其他已验证事实冲突仍拒绝。

飞书来源配置包含 `app_id`、`tenant_key`、`bot_open_id`、`chats`、`verification_token_ref`、`encrypt_key_ref`；`transport` 字段与 GitHub 相同，发言凭据引用应用 Secret。启用来源前加载 `.[superagent]` 的 cryptography 依赖与所需显式凭据，失败不创建运行宿主。普通事件必须通过签名、AES 解密、Token、App、Tenant 和 Chat 核验；无签名只允许解密且 Token 正确的 `url_verification` challenge，不接受普通事件。

例如引用 `secret://github-webhook` 时，配置 `secrets: {"github-webhook": "PROJECT_GITHUB_WEBHOOK_SECRET"}`，凭据只从该环境变量读取。未配置发言 `credential_ref` 时不读取发言凭据，也不自动取得回复能力。已接收内容、冻结 Report / Artifact 和当前交付状态仍在统一账本中。

### GitHub Review 与 CI 上下文

除原 Issue／PR 开关和评论入口外，接收 Review 的 submitted／edited／dismissed、Review Comment 的 created／edited／deleted、PR 的 synchronize／ready_for_review／converted_to_draft／edited／labeled／unlabeled，以及 Check Run、Check Suite 和 Workflow Run 的受支持进度事件。Review 保留原提交 Hash 并标识是否落后于事件中的 PR Head；这些外部 Review 不等于 Harness 的独立质量门。

这些事件只作为已授权 Ticket 的 context-only 输入；不因提及机器人、检查成功或评论删除创建 Evaluation、Mission、原生运行或恢复旧任务。Review 作者仍受来源白名单约束；CI 一律使用明确列入 bots 的身份。CI 必须给出唯一 PR、匹配 Head SHA 和当前配置的 Base Repo；缺少唯一目标的检查被忽略，不通过 SHA 猜测归属。原回复目标仍是确切仓库和 Issue／PR，不接受事件中的任意 URL。

### 飞书媒体

已验证消息支持 text、image、file、audio、media 和 post。富文本保留用户文字和资源引用；只有附件的消息保留空正文。Webhook 接收不下载资源，工作台在操作者明确获取后才读取原消息及资源。获取前核对当前来源、机器人账号、Chat、原消息和 Root、资源引用；令牌刷新后、落盘前和登记时再次核对来源修订。

单资源有界读取，最多 32 MiB。图片验证实际 PNG／JPEG／WebP／GIF 格式并限制像素及帧数；其他文件作为二进制附件下载，不内联执行提供方 HTML。文件与摘要登记在同一实例的媒体资源记录，重复获取复用原件；原件损坏或路径冲突明确拒绝，保留原数据。禁用来源或移除 Chat 后不能通过缓存接口继续读取。工作台仅在可见页面显示已获取的图片；本阶段未将附件自动注入模型视觉输入，也不自动转写音视频或清理历史文件。

## 授权、工作与独立质量门

Gate 先确定 Actor、是否明确指向、来源修订和 Thread 范围；Assessor 只能对可准入候选提出结构化研判。接受目标必须有规则依据、已经确定的实现方向和实际原文引文。模型不能提高授权强度或扩大 Scope；`observe` 下的推断候选不自动执行。

原生 Assessor / Planner 使用无工具的独立会话，模型提交、原 Run 和冻结提案记录在 `coordinator_model_calls`。模型调用发生在业务事务外；等待结束后再次核对来源及 Mandate。Planner 只能提出 Work DAG，宿主生成指令引用和哈希、物化 Integrator / Reviewer 后封存计划。丢失提交回执或重启只回查原生身份，不创建第二个调用来替代未知结果。

只读单节点问答可以使用 `single_worker_report`。代码／文件任务即使只有一个 Work 节点，也必须经过冻结基线、独立工作树、真实 Artifact / Delivery、Integrator、独立 Reviewer 与有限修正；模型自己声称“完成”不能替代该质量门。Reviewer 的判定绑定候选、Acceptance Hash 和原生证据；失败或未知副作用不会自动重放。

Profile 可用 `max_work_turns` 显式启用有限阶段协议：`continue` 冻结当前交付，新 Assignment 使用独立资源并导入原产物；`ready` 才允许解锁下游。阶段和总 Assignment 预算、非法报告、重复检查点、未确认工具及后台作业都会参与门禁。只有当前计划已确认 applied 的节点指令进入有界阶段上下文，不向新 Run 重放旧指令。

运行、工具和文件事实仍归 Harness 原生 Session / Run / Worktree / Background Job。协调账本保存引用、回执和决策，不复制另一套执行器。本机 Worktree 本身不构成操作系统沙箱；远程隔离入口见下一节。

## 隔离与远程 Worker

Worker 设备数量为 **1–32 台**。单台设备可以与协调器位于同一电脑，也可以位于远端；不要求两台设备或 Docker。不配置 `execution` 时，原 CLI 继续在本机使用原生 Harness 执行。需要独立 Worker 网关时，可选择 `native` 可信宿主执行；需要容器隔离时选择 `docker`。

两种执行方式共用一个 SuperagentService 的授权、计划、调度、质量门及交付，只将 Executor Port 接到登记设备。Assessor / Planner 继续在协调端运行，Worker 的 Session、Run、工具和文件原件位于所属设备。一个 Mission 的全部 Work / Integrator / Reviewer 固定在同一设备，沿原 Artifact / Delivery 协议交接；当前不做跨设备 DAG 文件传输，也不将失联任务迁到另一设备。

### 单台原生 Worker

可从[完整单台原生配置示例](../examples/superagent-native.json)复制并修改模型、仓库和专用协调目录。统一配置中的设备部分如下，其他字段沿用当前统一格式：

```json
{
  "secrets": {"worker": "PROJECT_WORKER_KEY"},
  "execution": {
    "lease_seconds": 60,
    "devices": [
      {"device_id": "worker", "url": "http://127.0.0.1:18766", "runner": "native", "key_ref": "secret://worker", "capacity": 1}
    ]
  }
}
```

`capacity` 为设备同时承载的未结束 Mission 数，范围 1–256，不是设备数量。设备满载时，在创建 Worker 前保留待执行任务，后续 Tick 在容量释放后继续；不会把没有发出的工作树创建记为未知操作。多台设备按当前未结束 Mission 数／容量分配；未知或过期 Mission 仍保留原设备容量。

每台设备可显式指定 `runner: "native"` 或 `"docker"`。省略 `runner` 的既有 Docker 配置仍按 Docker 执行，不探测环境后切换后端；选择原生执行必须明确填写 `native`。同一设备的协调端配置与实际握手必须一致，原生设备不能满足 Docker 要求。

导出设备模板：

```sh
python -m harness.superagent_worker --export-config /path/to/config.json --device worker --output /path/to/worker.json
```

导出只创建不存在的模板，不创建状态、不读取或导出凭据值。原生模板包含 `runner: "native"`、`sandbox: null`，无需镜像、Docker、Rootless、cgroup 或 Unix Socket。编辑模板中的以下设备字段：

- `host_state`：已存在的专用设备数据目录，与其他实例及用户项目分开。
- `profiles.*.repo`：该设备上的明确 Git 仓库副本，须包含协调端冻结的基线提交；同一逻辑仓库的 Profile 指向同一副本。
- `listen`：明确 IP 与端口，与协调端设备 URL 对应。跨主机监听同时填写 `cert`、`key` 的证书文件路径。
- `key_env`、`api_key_env`：设备签名密钥与模型凭据的环境变量名。前者与协调端该设备引用相同，后者默认 `HARNESS_WORKER_MODEL_API_KEY`；凭据值由运行环境提供。

模板中的 `coordinator_profiles`、`coordinator_repos`、`identity_hash` 和执行约束保持导出值。填写完成后，在设备启动 Worker，再在协调端使用原 CLI：

```sh
python -m harness.superagent_worker --config /path/to/worker.json
python -m harness.cli --superagent /path/to/config.json
```

原生方式在网关进程中装配同一个 HarnessExecutorAdapter，不运行第二个 Coordinator。它复用独立 Session／工作树、Profile、权限策略、原生进程回收、租约与回执；模型能力通过进程内 Broker 消费。原生执行继承宿主用户的文件与网络权限，不提供 OS 沙箱、Docker 配额或同用户进程之间的凭据硬隔离；`native` 必须使用 `sandbox: null`，不能填写未生效的容器限制。当前已在 macOS 以真实 CLI、签名回环 HTTP 和受控模型端点验证单台原生设备完成、关闭及重启后原 Run 回查；其他操作系统实机仍待验证。

### 设备身份与共同传输

每台设备使用独立且至少 32 字节的签名密钥、独立根 URL 和 Secret 引用；不同引用解析成相同密钥也拒绝启动。跨主机必须 HTTPS，使用系统信任库验证证书，禁止重定向；HTTP 仅限回环地址。URL 不带路径，端点固定为 `/executor/v1`。请求和回执都签名，绑定实例、设备、配置哈希、原操作号和 nonce，请求时钟容差 60 秒；租约范围 15–300 秒，设备与协调端须同步时钟。

`execution`（包括显式 `runner`）纳入配置身份，修改后必须显式使用匹配的新专用协调目录，不迁移旧 Mission 或改写旧账本。设备的实际执行方式、Profile、模型、语言、权限策略、默认 Profile、工具效果和资源上限须匹配协调端握手；仓库物理路径经模板映射，不改变逻辑 Profile。此握手信任持有设备密钥的操作者，不是硬件或镜像远程证明。机器协议及握手结构由 `superagent_remote_protocol.worker_openapi()` 生成到 [Worker OpenAPI](openapi-worker-v1.json)。

### 可选 Docker 隔离

使用容器时，将设备配置明确设为 `runner: "docker"` 并重新导出模板；设备模板的 `sandbox` 为包含 `image`、`memory_mb`、`cpus`、`pids`、`disk_bytes` 的对象。Docker 路径要求 Linux、非 root 宿主用户、本机 Unix Socket 上的 Rootless Docker，以及生效的 cgroup v2 CPU／内存／进程／禁用 Swap 边界。缺少任何条件时明确失败，不回退到原生执行。原生路径不执行这些探测。

在明确授权构建的环境准备镜像；Dockerfile 要求操作者提供经核对的 Debian 系 Python 3.14 基础镜像摘要，构建上下文仅含打包源码。以下部署命令未在当前无 Docker 的开发环境执行：

```sh
docker build --build-arg PYTHON_IMAGE='python:3.14-slim@sha256:经核对的摘要' -f docker/superagent-worker.Dockerfile -t harness-worker:local .
docker image inspect harness-worker:local --format '{{.Id}}'
```

将实际本地 `sha256:…` 镜像 ID 或 `仓库@sha256:…` 填入 `sandbox.image`，启动禁止自动拉取。容器仅挂载自己的状态、独立工作树／Git 视图、Broker Socket 目录及登记的只读仓库副本，不挂载宿主 Home 或 Docker Socket。Rootless 命名空间内的受信内核以 UID 0 管理原生记录，仅保留身份切换、文件归属和终止所需能力；每个不可信 Worker 的 Shell／文件辅助进程使用独立非 root UID，清空附加组，不携带长期凭据。文件辅助进程不可被同 UID Shell 调试。

命令使用独立 Git 视图；受信内核始终使用受保护的原生 Git 元数据，忽略用户可替换的 `.git` 入口。容器禁用网络，根文件系统只读，禁止提权；启动核对实际 cgroup，不能仅凭 Docker 参数认定配额生效。`disk_bytes` 还按周期统计专用状态内普通文件的逻辑总大小，超限或观察失败结束容器并保留未知回执；这是采样限制，不能保证瞬间总磁盘用量不超限。容器工具只允许 read／write／edit／glob／grep／bash 及所属后台作业管理，其他工具在启动前拒绝。

Docker 的长期模型密钥留在宿主 Broker，容器经受限 Unix Socket 使用原 Mission／租约代次／模型／预算绑定的短期能力；原生路径通过进程内传输消费相同能力规则。两者只向固定模型根地址的 `/chat/completions` 转发，不采纳 Worker URL 或鉴权头，不重试未知请求，不承诺其他模型协议。`model-proxy-audit.jsonl` 只追加身份、时间和请求摘要，不记录密钥或能力值；原生运行内容仍按 Harness 机制保存。

### 租约、断线与原回执

协调账本保存不可变设备身份与 Mission 的原设备／代次，设备只保存原生资源引用、租约和操作回执，不复制 Mission 状态机。写入先记录 pending，再调用一次原操作；完成回执只可按相同操作号和冻结参数读取。连接丢失、pending、原生未确认或后台进程仍在时保留 unknown；恢复只查询原资源，不能用新 Run、另一设备或另一执行方式替代。

Tick 重新核验 Mandate 后才续租。来源停用或范围收窄导致授权失效时，协调端撤销原租约并向设备提交一次固定 revoke；设备持久零到期值阻止旧心跳恢复授权。撤销回执表示停止准入，不能当作原 Run 已停止的证明；失联时等待原租约自然到期。设备到期后按当前拥有的原 Run 和进程句柄发起停止，失败保留未知及原错误，不使用历史 PID。已撤销租约不能续期，其迟到结果不能结算为当前成功。

`remote_lease_errors` 保存本轮续租／撤销传输错误，服务继续提供查询与原操作恢复；一次模式将其作为未完成条件。关闭停止新准入，按确切原运行提交停止并只读等待实际收尾，无法确认时明确失败并保留原账本与租约。同一设备状态的第二个内核因进程锁拒绝接管；退出等待所属请求、原生资源和代理收尾，不删除运行数据。Docker 正常关闭后将自己的工作树／Git 视图归还宿主映射身份；异常退出可能保留 Rootless 映射 UID，产品不自动清理或迁移。

CLI 的 `--superagent-list devices` 与 `--superagent-list leases`、工作台的设备／租约分页及 Mission 详情显示原设备、代次、状态与到期观察，不返回凭据。设备列表表示登记事实，不能当作设备当前在线证明。

## 持续协作、指令与控制

普通跟进进入 TicketInput，首次原生投递前固定本次活动的 Work Assignment 集合；每个 Input / Assignment 独立保存接受／应用回执。全部目标 applied 后才将全局输入标为 read。目标结束后仍只查询其原回执；context-only 不启动执行，Handback 输入不再次投递。显式授权的跨来源 Subscription 同样复用原活动，结果按实际参与的每个 Thread 保留最早原回复目标。

```sh
python -m harness.cli --superagent /path/to/config.json --superagent-steer "保留公共接口" --superagent-job JOB_ID --superagent-request-id steer-001
python -m harness.cli --superagent /path/to/config.json --superagent-receipt steer-001
python -m harness.cli --superagent /path/to/config.json --superagent-pause --superagent-mission MISSION_ID --superagent-request-id pause-001
python -m harness.cli --superagent /path/to/config.json --superagent-resume --superagent-mission MISSION_ID --superagent-request-id resume-001
python -m harness.cli --superagent /path/to/config.json --superagent-cancel --superagent-mission MISSION_ID --superagent-request-id cancel-001 --superagent-once
python -m harness.cli --superagent /path/to/config.json --superagent-control-receipt cancel-001
```

本机指令写入 `coordinator_requests`，固定 Job、Assignment、原 Run、Turn、授权 Trigger 与正文；不通过普通跟进广播。目标不唯一或状态摘要被截断时须给出确切 Job。处理时若 Run／Turn、权限或控制模式已变化则拒绝，不投向新运行。accepted 与 applied 分开，周期 Tick 沿原 request_id 回查应用状态；回执读取不执行重投。

暂停、继续和取消直接写入同一账本的 CoordinatorControl。Resume 前重新核验 Mandate；暂停停止新的执行准入，取消沿原 Run 与其所属后台作业停止，不根据旧 PID 重放。控制命令 `state=applied` 仅证明控制意图落盘，实际停止是否完成应查看目标状态。历史和未知结果保留，达到容量上限时明确拒绝，不自动归档。

Attention 问题经 Outbox 交付。唯一已阻塞问题的获准人工回答可关联原问题并创建新的有界 Mission；多个问题需要显式 attention_id，不能猜测。原运行不重放，范围和预算不扩大。原始目标的 Handback 转为 Attention，跟进的 Handback 只归还确切 Input。

## 状态、交付与关闭

`--superagent-status` 从当前协调账本的同一读事务读取有界 Mission / Job / Assignment、控制、依赖和 Outbox；活动与待关注目标优先于已完成历史，并保留总量和截断标记，同时附带 `runtime-status.json` 的最后一次运行观察。`runtime_state=running` 不是当前进程仍存活的证明；应结合 `runtime_observation.observed_at` 判断新旧。读取不创建数据库、不迁移、不读取凭据或启动模型。完整原报告、文件和工具证据仍保存在所属原生运行及协调账本，状态摘要不包含原始工具参数或输出。

回复能力以当前 Source 修订登记到独立 Channel 绑定，不改写原地址。显式禁用或来源修订变化后，Tick／重启不会自动重新授权。每次原生 POST 前核对 Ticket、Mandate、Source、原回复目标、控制、Watchdog、正文哈希和提供方身份；GitHub 核对账号与仓库，飞书核对机器人与原消息。刷新令牌后仍复核写入准入。

Candidate、pending、sent 和 unknown 严格区分。POST 返回后还须只读查到唯一、原标记、正文、目标和主体匹配的远端对象才确认 sent。未找到、多个匹配或分页不完整不能授权重发；未知结果只 GET 原操作。GitHub 仓库路径的大小写不改变同一资源身份。产品回复保持有界，飞书引用标记计入字数预算；超限保留冻结正文并明确报错，不偷偷截断或发送。

本机 `replies/` 只保存结果投影，`runtime-status.json` 只保存运行观察；它们不承担独立业务账本或消费队列职责。关闭立即停止新准入和监听，等待已经受理的所属请求及原生资源收尾；取消等待不等于撤销已接受写入。关闭期间不会继续派生新的模型调用、会话或 Worker 执行。

## 分页浏览与任务详情

`--superagent-status` 保留运行摘要；需要浏览完整历史时使用同一协调账本的分页查询：

```sh
python -m harness.cli --superagent /path/to/config.json --superagent-list missions --superagent-limit 20
python -m harness.cli --superagent /path/to/config.json --superagent-list missions --superagent-limit 20 --superagent-cursor NEXT_CURSOR
python -m harness.cli --superagent /path/to/config.json --superagent-show MISSION_ID
python -m harness.cli --superagent /path/to/config.json --superagent-list jobs --superagent-mission MISSION_ID
python -m harness.cli --superagent /path/to/config.json --superagent-list assignments --superagent-mission MISSION_ID
python -m harness.cli --superagent /path/to/config.json --superagent-list artifacts --superagent-mission MISSION_ID
python -m harness.cli --superagent /path/to/config.json --superagent-list reviews --superagent-mission MISSION_ID
```

每页默认 32 条，允许 1–100 条；返回 `instance_id`、`resource`、`mission_id`、`items`、`total`、`has_more` 与 `next_cursor`，末页游标为 `null`。条目按首次入库顺序倒序排列，游标固定账本、资源、Mission 范围及首次插入水位；后续新增记录须从第一页重新查询才能看到。不能把 Mission 列表游标用于 Job、另一 Mission 或另一账本。游标是本机查询参数，不是认证凭据。

每页及单次详情各自使用一个读事务，保证其内部数据一致；跨页期间已有记录的状态仍可更新，不承诺跨页冻结整个运行状态。Job、Assignment、Artifact、Review 查询必须指定 Mission，保留全部计划／执行历史，不只取当前尝试。摘要不返回 Assignment 原始报告、Artifact 完整 Manifest／回执或 Review 完整判定正文；原件仍在所属协调记录和原生运行。

任务详情返回 Mission、当前冻结计划的 Hash／验收条件、控制、监督摘要和各类关联记录数量。冻结计划复用既有完整 Hash、Job 物化和依赖校验，损坏原件明确拒绝，不修复或重建。分页与详情不读取运行观察文件、凭据，不启动宿主，不执行恢复或 Schema 升级。

可信 SDK 使用 `CoordinatorHost.queries.page(resource, mission_id=..., limit=..., cursor=...)` 和 `.mission(mission_id)`；查询持有同一 Host 操作租约，缓存句柄在关闭后同样拒绝。工作台与 CLI 使用相同 CoordinatorQueries；全局资源还包括 devices、leases、sources、channels、mandates、audit、effects、attentions、events、evaluations；leases 也可指定 Mission 范围。effects 只列出 unknown；inputs 和 outbox 必须指定 Mission，展示其所属 Ticket 的输入与回复，因此同一 Ticket 的多个 Mission 可看到相同记录。

## 统一工作台

在设置的智能体分组打开 Superagent，输入明确的配置文件路径并连接实例。连接只登记当前主宿主的本机连接，不创建运行目录或模型宿主；已有账本可直接浏览。启动复用 `SuperagentService.open` 和同一实例锁；工作台只停止自己拥有的运行。界面中的运行状态指工作台拥有的句柄，不能据此判断另一个 CLI 进程是否存活。

首次连接尚无可读取账本的实例时，页面明确提示先提交目标或配置模型及凭据后启动运行，不将读取失败显示为空列表。本机 Source／Actor／Thread 选项独立于账本列表保留，可直接提交首个目标；提交只入队，不调用模型。使用原生 Worker 示例启动执行前，还需替换 `YOUR_MODEL_ID`、设置模型凭据及 `PROJECT_WORKER_KEY`，并按[单台原生 Worker](#单台原生-worker)步骤导出配置、启动 Worker。

工作台提供目标及冻结验收条件、Job／Assignment 执行引用、Artifact／Review 交付摘要、输入／回复、人工待决和未知效果分页。支持明确本机 Source／Actor／Thread 的目标提交、Mission 暂停／继续／取消和活动节点指令；所有操作沿现有控制、授权与原生执行端口。人工待决答案绑定确切 attention_id 和原本机来源及线程，仍经 Gate／Mandate 核验，沿新的有界活动继续；远端问题须在原通道由已授权账号回答，不冒充远端用户。

读请求绑定实例、查询范围和页面代次，切换实例、离开或隐藏页面后拒绝迟到结果；没有后台自动轮询。用户目标草稿按实例保留在当前界面会话中。已经受理的写入由主宿主等待收尾，关闭设置不撤销写入；网络失败后保留原请求号，使用“查回原请求”读取回执，不能自动重复 POST。主宿主和自有 Web 循环关闭前等待工作台及其运行收尾。连接目录和界面草稿不作为跨进程持久账户目录。

### 来源、策略与授权影响

来源启停和可变配置沿原 SourceRegistry 修订检查；来源身份、配置文件及凭据映射不由编辑器改写。配置中的来源与初始策略用于首次登记，已有账本的当前修订在重启后继续生效；本机操作者／线程白名单和传输入口仍由明确配置固定。Secret 只能引用该配置已经声明的环境变量映射，不能从 UI 提交凭据值。运行中的服务同步当前凭据和所属监听器，来源修订并不自动重授回复能力。

策略发布沿同一授权引擎及预期修订，重验受影响的 Mandate；工作台可查看授权影响和不可变审计。回复通道单独显示原地址，操作者须按当前 Source 修订显式授权或撤销回复；此传输能力不代替每次发送的 Mandate。研判不可用的冻结批次可显式重新排队，仍受原策略／Thread 修订和尝试预算限制。来源、策略、回复授权及重新排队与操作者固定请求回执在同一事务提交；过期修订失败且不留伪造成功回执。

### 人工未知结果核实

未知效果列表先读取原 effect_id、generation、冻结参数和已有回执。操作者填写核实说明后，只通过当前运行的原 Adapter 查回原操作，不接受操作者提供的成功证明，不提供重发按钮。证明一致的 Outbox 结果沿原领域服务结算；无查回端口、结果不确定或证明不充分时保留 unknown，其他原生效果交由对应恢复流程结算。查回完成时再次核对原代次及状态，记录操作者、请求、证据和结论，不能覆盖后来已结算的事实。

工作台 HTTP 面仅限本机连接与本机 Host，拒绝共享主体和跨站请求。入口位于 `/api/v1/superagents`：连接、只读查询、固定请求号命令及按当前来源复核的媒体读取；准确字段与响应见[生成协议](openapi-v1.json)。这些检查不认证本机进程，不构成远端管理或多人审批系统。

## 当前边界

Phase 4 的 CLI 切换、统一配置、持续协作、质量门、信道发送和旧编排退役沿同一服务实现。旧 Router、Dispatcher、MissionStore、Worker / Results 编排、独立信道／控制账本、生产 Shadow 和旧来源映射 API 已删除。已有 Coordinator Schema 历史继续受校验和保护；不导入、清理或迁移旧 Superagent 运行目录。

Phase 5 的本机统一工作台、人工只读核实、来源与策略管理、GitHub Review／CI 上下文和飞书媒体接收／呈现已实现。Phase 6 支持单台原生 Worker、可选 Docker 隔离及多设备调度，共用租约、原生回执、文件质量门和断线恢复。单台无 Docker 的真实 CLI 完成、关闭及重启回查已取得本机证据；满载时另一任务保持待执行，释放容量后在同一设备完成。两台物理设备或 Docker 不是原生方式的启动及完成前提。可选 Docker 的真实 Rootless 越权、跨平台和物理多机专项仍未验收，不能将其写为已获证明的能力。

多人审批、自动跨来源关联、目标与依赖变更、动态重规划、跨设备 DAG 和自动故障转移仍未实现。本机测试也不代表在线模型质量、外部账号部署或长期负载验收。定向验证与历史问题依据见[协调审查](superagent-review.md)，执行规则见[开发与测试](development.md)。
