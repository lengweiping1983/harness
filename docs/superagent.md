# Superagent 模式与实现边界

当前 Harness 提供两种显式 Superagent 协调方式：默认 Router 为事件选择 Worker 并整理回复；配置 `autonomy` 后，一个明确目标可分解为有限任务图，经真实文件交接、集成、独立复核和有限修正后交付。显式配置阶段预算后，长工作节点可在新对话、新工作树中自动接续；可选 Watchdog 会把目标进度和停滞窗口持久化，并在达到明确阈值时生成关注项或安全阻塞目标。两种方式都复用持久收件箱、原运行、模板工具权限及独立工作树。GitHub／飞书信道可在显式授权后发送并核实原来源回复；默认 Router 还支持跨来源续接同一 Worker。动态来源管理、工作台和远程执行仍未形成完整闭环。现有团队、持续目标和定时自动化保留各自语义。

后续开发只按[统一架构与开发计划](superagent-next-generation-architecture.md#开发执行规则)推进。以下配置和命令记录当前尚存入口的实际使用方式，不是获准继续开发旧链路的要求；唯一服务与 CLI 切换尚未完成，不能把文档更新解释为旧代码已经移除。

## 本地文件模式运行入口

在专用配置文件中显式指定模型、来源、工作模板和独立状态目录，例如：

```json
{
  "version": 1,
  "instance_id": "project-agent",
  "state_dir": "~/.harness-superagent/project-agent",
  "language": "zh-CN",
  "router_model": "your-router-model",
  "worker_model": "your-worker-model",
  "base_url": "https://your-model-endpoint.example/v1",
  "permission_policy": "read_only",
  "sources": {
    "local-project": {
      "mode": "requests",
      "actors": ["operator"],
      "threads": ["issue/1"]
    }
  },
  "profiles": {
    "inspect": {
      "repo": "/absolute/path/to/git-project",
      "description": "核对项目中的问题并报告可核验依据。",
      "sources": ["local-project"],
      "tools": ["read", "glob", "grep"]
    }
  }
}
```

模型名称、端点、仓库和模板说明由维护者填写；模板说明作为维护者原文保留。`permission_policy` 必须显式选择 `read_only` 或 `full_access`，同时受模板工具名单限制。需要修改代码或运行命令时，由维护者在新配置中明确授予对应工具和权限；该模式不提供交互审批界面，不隐式升级权限。本地只读模式通过注册表限制和执行前复核直接拒绝非只读、危险或要求审批的工具，避免进入普通宿主的审批等待。工作宿主当前装配文件、Shell、Todo 和工作树能力，复用项目默认的所属进程执行器选择；模板只能选择实际注册的工具，Router 始终无工具。

设置 `HARNESS_API_KEY` 或 `OPENROUTER_API_KEY` 后运行：

```sh
python -m harness.cli --superagent /path/to/superagent.json
# 只处理当前批次，并等待工作结果和 Router 整理结束：
python -m harness.cli --superagent /path/to/superagent.json --superagent-once
```

普通 CLI 的对话、UI、外部执行器、记忆归档及运行覆盖参数不能与该模式混用。全局 `--language` 控制 CLI 帮助和入口提示，配置中的 `language` 控制两个模型宿主及运行提示词。运行器直接使用显式配置和上述密钥环境变量，不通过普通 `make_harness` 或 `HarnessConfig.from_env` 读取个人运行设置、密钥池文件或连接器配置；两个专用宿主仍使用自身状态目录中的当前存储。此命令会调用配置的真实模型端点并执行模板授予的工具，不能把只读示例配置误当作操作系统沙箱。

配置最多 131072 字节，拒绝未知字段、重复 JSON 对象键及非标准数值。`state_dir` 和模板 `repo` 支持 `~`；相对路径以配置文件所在目录为基准解析。必填字段见示例；可选 `max_active` 默认为 3、`max_workers` 默认为 128、`interval` 默认为 0.5 秒、`once_timeout` 默认为 600 秒，范围分别为 1–128、1–128、0.01–60 秒和大于 0 至 86400 秒。`autonomy` 的格式见下节；其中可选 `watchdog` 的 `stale_after` 为 1–604800 秒，`max_stale_ticks` 为 1–32，`action` 只能是 `attention` 或 `block`。来源和模板各最多 32 项，其余字段边界沿用后文 SDK。配置按标准化后的完整值绑定该状态目录，不能在重启时静默替换模型、来源或工具权限；当前没有热更新配置、迁移或重新绑定入口。

运行目录包含 `incoming/`、`receipts/`、`drafts/`、`status.json`、`service.sqlite3`、收件箱及协调账本，以及独立的 `router-home/`、`worker-home/` 和网关目录。启动前持有本机非阻塞进程锁，第二个实例在构造模型宿主前拒绝；正常退出或进程结束释放锁，锁文件保留。首次 Router 对话先记录创建意图，再在初始头中保存配置指纹；重启按确切元数据回查，不因回执丢失重复建立对话。已有结构或身份不符时保留原件并明确报错。

维护者或可信适配器把完整事件 JSON 文件发布到 `incoming/`。写入时先使用不以 `.json` 结尾的临时文件，完成后再显式重命名，避免把正在写入的半个文件当作事件。文件字段必须恰好为：

```json
{
  "source_id": "local-project",
  "event_id": "request-1",
  "actor_id": "operator",
  "text": "请核对当前问题，并报告具体证据。",
  "thread_ids": ["issue/1"],
  "human": true,
  "explicit": true
}
```

这是本机可信文件信道，不是公开 Webhook 接口。能写入该目录的一方被视为可信适配器；它须核实发送者和明确请求标记，不能把外部请求体中的身份字段直接当成授权。目录一次最多保留 1024 个 `.json` 输入，单文件最多 524288 字节；程序每轮轮转处理最多 16 项，来源正文仍受收件箱的 65536 字节限制。拒绝输入文件符号链接。未授权事件只保存过滤回执，不调用模型；结构错误和身份冲突保留原文件并列入 `input_errors`，其他合法文件仍可继续处理。运行器不自动删除、改名或归档输入。

`receipts/` 为每个文件内容版本保存稳定接收回执，重复扫描不改变原事件的路由事实。`drafts/` 保存 Router 的本地候选及原工作、整理运行引用；`state=draft` 不是已发送回执，也不是验收证明。回执和候选用独占创建、刷新及 `fsync` 保存，已存在时只核对相同字节，不覆盖人工修改。发布中进程退出可能留下不完整文件，重启会报冲突并保留原件，不自动修复或重写；维护者可从对应原账本回查后显式处理。

`status.json` 是可重建的当前状态投影，包含协调、创建、结果及输入错误；启用外部信道时另含各自接收队列及发件箱状态计数。正常关闭标记 `runtime_state=stopped`，已观察失败标记 `failed` 并保留机器错误码。它是最后一次快照，不是进程存活、实际进展或业务完成证明，进程崩溃后可能保留旧值。连续模式等待后续事件，Ctrl+C 等待所属创建、已接受信道请求和宿主收尾后退出；不会删除已有资源。单批模式确认已扫描所观察到的输入、处理待派送工作并整理结果后才返回，运行中到达的事件也可能纳入本次消费；需要持续接收时应使用连续模式。正常完成处理为 0，输入错误、未知或拒绝的协调结果、未知发送为 2，超过等待上限报错并保留事实，Ctrl+C 为 130。未知 Router 批次暂停后续收集；其他未知输入、结果或发送保留原事实，独立的已知工作仍可推进。退出码 0 不代表代码质量或业务验收通过。重启不会自动重放已经中断的原工作运行。

`tests/test_superagent_service.py` 覆盖独立宿主、完整文件链路、真实工具读取及编辑、只读拒绝、源仓库保留、重启不重放、进程锁、输入错误隔离、关闭和 CLI 参数；其中真实 CLI 子进程连接临时本机 HTTP 模型服务，核对研判、Worker、结果整理三次请求及最终候选。该证据不代表在线模型质量或真实外部信道验收。

## 直接提交目标与读取状态

可信本机操作者无需手写事件 JSON。配置完成后，启动持续运行器，再从其他终端提交自然语言目标：

```sh
python -m harness.cli --superagent /path/to/superagent.json
python -m harness.cli --superagent /path/to/superagent.json --superagent-submit "修复解析器的空输入问题，补充回归并验证结果"
python -m harness.cli --superagent /path/to/superagent.json --superagent-submit "修复解析器的空输入问题，补充回归并验证结果" --superagent-once
python -m harness.cli --superagent /path/to/superagent.json --superagent-status
```

单独提交与状态命令不构造模型宿主、不要求模型密钥，也不启动运行器。提交返回 `queued` 只表示输入文件已原子公开，实际采用和处理由运行器负责；状态是最后一次快照，不表示进程此刻存活。尚未启动时，提交仍可排队，状态明确报尚无快照。提交和 `--superagent-once` 可以组合为一条命令：先原子公开目标，再启动一次运行并等待结果；此组合需要模型密钥，失败时仍保留已公开的输入，不能自动重放。状态查询仍不能与 `--superagent-once` 组合。

本机来源必须已经配置、未忽略且至少被一个模板接收，并且不能是 GitHub／飞书绑定的远端来源。只有一个合法本机来源、操作者和 Thread 时自动选择；多项时分别传 `--superagent-source`、`--superagent-actor`、`--superagent-thread`。选择参数只允许随提交使用，不扩大原白名单。正文保留原文，`/goal` 等字面内容不会变成宿主命令。

需要跨调用核实时传 `--superagent-request-id`；同一实例、来源与请求身份的相同正文只公开一份文件，不同正文报冲突并保留原件。未提供时创建新的请求身份，因此手动重复提交会建立新的事件。写入先在同目录准备不以 `.json` 结尾的独占临时文件，完成 `flush/fsync` 后以硬链接独占公开；扫描器看不到半份提交。目录链接明确拒绝；支持目录句柄的平台将发布及清理绑定原目录，无目录句柄的平台清理前核对临时文件身份，其原生目录替换竞态仍需实机验证。`tests/test_superagent_local.py` 核对并发相同提交、身份选择、正文冲突、无宿主执行及英中英帮助。

## 自主目标、任务依赖与独立复核

在**新的专用状态目录**配置 `autonomy`，启用目标协调器；不修改已经绑定配置的运行目录，也不迁移默认 Router 账本。例如在前文配置中增加：

```json
"autonomy": {
  "max_steps": 8,
  "max_attempts": 3,
  "max_missions": 16,
  "integrator_profile": "inspect",
  "reviewer_profile": "inspect",
  "watchdog": {
    "stale_after": 900,
    "max_stale_ticks": 3,
    "action": "attention"
  }
}
```

这五个字段都必填。`max_steps` 范围 1–8，`max_attempts` 范围 1–5，`max_missions` 范围 1–128；可选 `max_work_turns` 的阶段协议见下节。上例使用前文已有的 `inspect` 模板进行只读分析；代码任务应配置具有文件写入权限的集成模板，以及具有必要 `bash`／`pwsh` 检查能力的复核模板，并显式选择 `full_access`。两个模板标识必须已在 `profiles` 中登记：维护者选择工具权限，模型不能发明。复核使用独立对话及工作树，同一模型也能承担不同角色，但不代表独立模型评审或操作系统沙箱。

执行链为：

1. **规划**：无工具的协调模型输出 `steps` 与 `acceptance`。每步有稳定 `id`、`profile_id`、明确指令和 `depends_on`；完成标准有 `id`、可观察要求及 `command`／`inspection` 类型。宿主拒绝环、缺失依赖、重复身份、未知或越权模板、跨仓库计划。格式无效时可重新规划，包含首次的规划总尝试最多 `max_attempts` 次；只有合法计划才建立工作资源。
2. **冻结与派工**：接纳计划时冻结仓库提交。所有节点共享同一目标资源组和基线，延迟创建不再读取新的源 HEAD。独立节点可并行，整个工作宿主仍受 `max_active` 限制；活动目标轮转推进，慢目标不阻塞其他目标。已接受运行及其收尾未结束时不交接；同对话仍有后台作业时继续等待，并显示 `waiting_for=background-jobs`。交付捕获与恢复取消共用原工具证据判定，既核对显式后台请求，也核对前台超时提升的后台作业；唯一规范失败回执明确 `not_executed` 或 `execution_state=not-started` 且无矛盾作业身份时，确认未启动。已启动作业按确切身份核对实时归属或归档收尾；缺失、重复、矛盾结果、普通工具错误或归档清理失败都不能当成收尾成功。
3. **真实交接**：节点完成后冻结交付树，包含未提交新增、修改和删除。依赖节点启动前复用 `plan_delivery_merge/apply_delivery_merge` 导入真实前置文件，并附上原运行、交付引用和有截断标记的报告。报告不是依赖文件的替代。合并冲突保留全部原件并阻塞目标，不自动选择一方覆盖。
4. **集成**：所有工作节点产生候选后，宿主使用指定模板建立集成工作树，合并全部交付并运行集成任务。它负责最终整体一致性，源项目分支和真实暂存区保持不变。
5. **独立复核**：新对话、新工作树导入本轮集成候选，逐项检查 `acceptance`。复核模型引用自己本轮的 `call_id`，宿主从唯一 `turn/start` 和真实工具开始／结果配对派生事件序号，拒绝其他运行、重复调用身份、伪造或未执行证据。命令项要求真实前台 `bash/pwsh` 已退出、整数零退出码、无后台和编码异常，且工作目录为本复核工作树根目录；省略 `cwd` 时继承该目录。检查项要求本树内 `read/glob/grep` 的成功证据。复核前后交付树须一致，不能边修改候选边宣布通过。
6. **有限修正**：复核明确失败时，原集成对话在同一工作树中接收结构化反馈，形成新运行；随后建立新的复核对话和工作树。工作节点的历史不重放，旧复核不能替代本轮证据。集成与复核最多各运行 `max_attempts` 次；耗尽进入 `blocked`，原因 `budget/attempts-exhausted`。
7. **完成与发布**：所有标准通过、最终回复有效且来源修订仍授权时，目标进入 `completed`，并在同一协调事务追加独立的完成序号。快慢目标反序结束不会漏掉后完成的旧目标。最终候选通过现有本地发布及已授权信道发送；中间节点和失败推测不自动广播。

目标状态为 `planning/running/integrating/reviewing/completed/blocked/unknown/rejected/suppressed/cancelled`。`max_missions` 限制活动目标，`max_workers` 仍限制保留的全部 Worker 创建记录；不会隐式删除旧工作树或释放历史容量。完整报告与逐项证据留在 `missions.sqlite3`，原工具日志、用量及轮次仍属于原宿主，协调账本不复制执行历史。启用 autonomy 时会在同一状态目录旁创建独立的 `watchdog.sqlite3`，保存每个目标的进度指纹、最后进展、停滞窗口和升级原因；它不复制工具输出，也不改变原运行所有权。未配置 `watchdog` 时使用 `stale_after=900` 秒、`max_stale_ticks=3`、`action=attention` 的安全默认值。

Watchdog 按每个目标独立的有界采样间隔观察目标账本和任务状态，采样不会把高频协调 tick 变成额外的心跳写入，也不会让并行目标互相抢占心跳。进度指纹同时包含目标账本、任务交付和原运行的有界活动摘要；工具事件、轮次、最后活动或确切归属后台作业的 stdout/stderr 累计字符数变化会清零停滞计数。后台摘要只读取原调用对应的实时句柄状态和输出量，不读取正文或推进模型消费游标；其他对话的输出、缺失句柄与旧归档不能制造活性。暂停不采样，人工恢复以持久控制修订开启新的观察窗口，同时清除暂停前的停滞；暂停中重启也沿用此规则。

指纹在一个 `stale_after` 窗口内没有变化时最多增加一个停滞窗口，避免高频 tick 放大证据。`attention` 将心跳标为 `stalled` 并在 `status.json` 给出有界摘要，供工作台或人工处理；它不会取消、重放或复制未知运行。只有显式选择 `action=block` 且达到 `max_stale_ticks`，协调器才把活动目标转为 `blocked`，原因固定为 `watchdog/stalled`。重启只回读心跳账本，不发送信号、不创建资源、不重放命令。活性仅说明观察到变化，不能替代业务验收。

运行提交、导入、捕获均先记录意图。丢失确认后只按原提交键或确切导入／交付树回查；不能确认则 `unknown`，不重放写入。原运行失败或重启中断进入 `blocked`，不会因为长时间没有文本重新执行原命令。来源撤销在创建、提交和最终完成前复核，已受理操作仍由原宿主收尾。

`completed` 表示**模型逐项复核通过，宿主核对了引用工具事实和候选交付树**。它不证明模型选取的完成标准覆盖了全部意图，也不证明任意 Shell 正文实际测试了所声明语义；例如命令内部仍可自行 `cd`。它不是可信宿主 `VerificationCapture`、操作系统隔离、人工业务验收、已发布或已合并主分支的替代。真实在线模型质量、长期负载及跨平台实机仍须分别取得证据。

自主模式的工作节点不订阅原 Thread，防止一句跟进广播到全部依赖节点。每个明确事件建立一个独立目标，普通非明确跟进不会续接目标；默认 Router 的单任务 Thread 续接继续保留，但两种协调方式不混用。运行中的工作和集成节点可通过下述本机入口接收执行指令。目标依赖图与验收标准的中途变更、跨来源目标关联、用户问答、动态重规划和未知状态的人工核实界面尚未提供。失败目标保留状态与全部资源供检查，当前不能通过自动重试命令强行恢复未知副作用。

### 目标级控制与少关注运行

自主模式在独立的 `control.sqlite3` 保存可信本机操作者的控制意图，不迁移已有目标账本。暂停、继续、取消和原回执核对均无需构造模型宿主或读取模型密钥：

```sh
python -m harness.cli --superagent /path/to/superagent.json \
  --superagent-pause --superagent-mission MISSION_ID --superagent-request-id pause-1
python -m harness.cli --superagent /path/to/superagent.json \
  --superagent-resume --superagent-mission MISSION_ID --superagent-request-id resume-1
python -m harness.cli --superagent /path/to/superagent.json \
  --superagent-cancel --superagent-mission MISSION_ID --superagent-request-id cancel-1
python -m harness.cli --superagent /path/to/superagent.json \
  --superagent-control-receipt cancel-1
```

返回 `queued=true` 只表示意图已完整保存。相同请求号、目标和动作返回原回执；已应用或拒绝的原请求返回 `queued=false`，冲突正文明确拒绝，不覆盖原件。回执 `status=applied` 表示控制模式已在同一控制事务应用，不证明工具副作用已撤销或所有进程已经停止。`--superagent-control-receipt` 只读原控制库，不建立目录、空库或新命令；已有 `--superagent-receipt` 仍只读取节点引导回执。

动作按 SQLite 持久插入顺序处理，不依据可能回拨的系统时间或请求号排序。每次全局推进最多检查 64 条，并轮转未知目标；不存在的目标保持排队，不阻塞其他目标，单个目标的命令仍保持先后次序。工作树准备、合并预检、运行提交、交付捕获及结算的安全边界优先核对本目标的控制。新请求及现有回执均验证当前格式、重复 JSON 字段、原目标／动作、状态及结果身份；控制模式与其最后一条已应用原回执相符，修订和计数在一次读事务中核对。损坏时保留原件并明确报错，不用默认运行模式掩盖损坏。

- 暂停使控制模式进入 `paused`，阻止后续调度和异步准备后迟到的新写入；已经受理的运行、Git 写入及后台作业仍由原所有者收尾，不将等待取消当作撤销。继续只解除暂停，不复活已经完成、失败或结果未知的目标。当前仍活动的节点可以接受原有本机执行指令。
- 取消使控制模式进入终态 `cancelled`；仍在调度的目标也进入 `cancelled`，释放活动目标名额。已有完成、失败、未知和发布事实不改写。每个停止核对原任务、资源、稳定提交键及确切运行，复核到投递取消之间不让出执行权；不会因共用规划会话误停另一个目标。未提交的节点不会补造运行；排队节点按原运行移除。
- 原工作会话仍拥有的后台作业各有一次独立停止收尾。慢停止不阻塞其他目标；控制层不重复执行已失败或未确认的停止。停止进度和原运行／作业的实际状态分开保存和呈现，失败原件在关闭时继续向上传播。后续又产生的所属后台作业继续进入原目标的停止收尾，服务关闭等待已经接受的停止。

`missions.recent[].control` 给出模式及修订，取消后本次运行器观察到的 `stopping` 区分 `pending/settled/unknown`，并给出未收尾运行、后台会话、停止失败数量及未确认执行标记；`settled` 仅说明已核对的原运行与当前所属作业完成收尾，不等于撤销外部副作用。重启首次推进从控制账本的完整已应用取消集合恢复监督，包括已经进入终态的目标；首次观察前该字段为 `null`。恢复只采用确切运行和原后台作业回执；旧后台回执仍不可用或原执行未确认时保持 `unknown`，不向旧 PID 发送信号，也不重放命令。已保存的完成候选不因后来取消被追溯撤回。

顶层 `control` 提供全局命令状态计数、总量及摘要截断标记；命令或目标摘要不是完整清单。`inputs.pending` 统计全部仍允许调度的目标，不受最近 16 项展示限制；目标账本中的活动清单、游标、统计、目标及任务使用同一读事务，控制库与原运行仍是独立事实源。暂停或取消的未运行节点显示 `control-paused/control-cancelled`，已接受运行及后台作业仍显示真实等待，并标明调度已停止。

单批模式等待已接受运行、后台作业、控制意图及所属停止收尾；未知目标的控制仍排队时可能达到 `once_timeout`，不提前声称已应用。控制与 `--superagent-once` 合用时需要正常运行器凭据，最终退出码还核对本条原请求是否实际应用；拒绝、未知执行或停止未确认不返回通过。取消后的目标不能通过继续命令复活，需要新尝试时提交新的明确目标。

控制账本最多保留 1024 条命令，不隐式归档、删除或循环复用身份。达到上限明确拒绝新控制意图，旧请求和只读回查继续可用。SDK 调用方在原事件循环中关闭 `SuperagentMissions` 时须等待 `aclose()`，以收完控制层已经接受的所属停止；CLI 服务已装配此生命周期。

回归见 `tests/test_superagent_control.py`、`tests/test_superagent_control_runtime.py` 与目标快照回归，覆盖共享规划会话隔离、取消释放容量、暂停期间禁止迟到导入、慢后台停止不阻塞独立目标、失败及未知不重放、重启回执、时钟回拨、未知目标轮转、并发相同请求、损坏保留、只读 CLI、语言消费及完整计数。确定性模型与本机进程证据不代表在线模型质量、远端操作者认证或跨平台进程实机验收。

### 长工作节点的阶段接续

在新状态目录的 `autonomy` 中显式增加 `"max_work_turns": 8`，允许每个工作节点最多执行八个阶段；合法范围为整数 1–32，拒绝布尔值、字符串和小数。省略该字段时仍为原来的单次工作报告协议；显式设为 1 也启用检查点协议，但不允许第二阶段。不改写或迁移已经绑定的配置。阶段预算与规划／复核的 `max_attempts` 分开，每个新阶段仍消耗一个 `max_workers` 保留名额并受全局 `max_active` 限制。阶段不会因长时间沉默自动结束，也不是固定时间片或失败重试。

工作节点每阶段结束时输出严格 JSON，例如：

```json
{
  "status": "continue",
  "summary": "解析器已经实现并写入回归；尚需完成边界条件检查。",
  "next_steps": ["读取现有实现和回归，补充非法输入的边界检查并运行验证。"]
}
```

只有 `status/summary/next_steps` 三个字段。`status` 为 `continue/ready/blocked`；非空 `summary` 最多 8192 个 UTF-8 字节。`continue` 要求 1–16 项非空后续步骤，每项最多 2048 个 UTF-8 字节；`ready/blocked` 要求空列表。`ready` 仅表示节点提供了最终候选，不替代集成或独立复核；`blocked` 的摘要记录需要的事实、权限或故障。缺字段、重复 JSON 键、非法值或不符合协议的普通正文使目标以 `work/checkpoint-invalid` 阻塞，原输出仍留在原运行，不猜测其含义。

宿主只在原运行为 `completed`、实际收尾完成、所属后台作业已结束、原轮次存在且工具开始／结果均确切配对时处理检查点。未配对工具使目标进入 `unknown`，原因 `work/effects-unconfirmed`；失败、停止或中断运行沿原阻塞语义处理，不能接续。有效 `continue` 先冻结真实交付，阶段进入 `checkpointed`；随后用新的稳定任务身份、新对话、新工作树导入上一阶段的累计交付，按原指令和检查点继续，不重新提交上一运行。导入与捕获丢失确认时只回查原事实。模型不能借此扩大权限、重写依赖图或验收标准。

每个逻辑工作节点只有最后阶段参与依赖解锁及集成。中间阶段有文件产物仍不能解锁；最后阶段必须明确 `ready` 且完成冻结。下一阶段收到上一运行和交付引用、有截断标记的报告、完整检查点及本节点确认已应用的本机执行指令；其他独立工作节点不会收到这些指令。集成和独立复核仍接收整个目标已应用的上下文。文件交接沿已有交付规则，只包含可冻结的 Git 文件变化；忽略文件、环境变量、进程与外部系统状态不能靠检查点自动搬运，后续阶段必须检查实际状态。

阶段预算耗尽而仍请求继续时，保留检查点并以 `budget/work-turns-exhausted` 阻塞。若再次提交与本节点任意早期阶段完全相同的检查点，且冻结文件树也相同，以 `work/no-progress` 阻塞；这只检测该明确重复，不证明语义上所有停滞均能被识别。缓存消费前重新核对原运行输出及工具证据，修改缓存及其哈希不能冒充有效交接。重启可接着调度已确认检查点之后尚未提交的阶段；已提交阶段仍按原身份回查，失败、被中断或结果未知时不会自动重放。

监督摘要在每个工作节点仅保留最近两阶段，`jobs_total/jobs_shown/jobs_has_more` 明示历史截断；规划、集成和复核仍展示原有有界轮次。`checkpointed` 是已冻结的历史阶段，不继承后续运行的活动或等待；`work.delivered` 只统计最终 `done` 的逻辑节点。每个阶段展示 `attempt/max_work_turns`；检查点摘要最多 512 字符，后续步骤只显示前四项、每项最多 256 字符，并明确总项数、遗漏及正文截断。全部阶段仍在目标账本及原运行中。目标列表或阶段历史截断时，本机引导必须明确给出当前可见的活动 `--superagent-job`，不能从不完整列表猜唯一目标。

回归见 `tests/test_superagent_checkpoints.py`，覆盖真实跨阶段文件、延迟解锁、原运行与资源隔离、重启、丢失捕获回执、未确认工具、后台写入、预算、重复报告、损坏缓存、指令继承及实际英→中→英模型输入。确定性模型回归不代表真实模型能稳定选择阶段边界、检查点语义充分或完成全部用户意图。

### 自主目标的只读监督

`status.json` 的 `missions.states` 统计全部目标；`recent` 最多展示 16 项，依次优先活动目标、`unknown/blocked/rejected/cancelled`、`suppressed`、已完成目标，同类按接纳序号倒序。`total/shown/has_more` 明示总量与截断，不能把 `recent` 当成全部任务清单。每个目标含原来源、工作树、交付树、修正轮数、有界完成标准及复核摘要，并含 `watchdog` 心跳摘要或 `null`；心跳的 `monitoring/mission_state/control_mode` 区分当前监督与历史证据。`budget` 提供当前尝试、上限及 `planning/delivery` 阶段，尚无合法计划的终止目标仍归规划阶段。顶层 `watchdog.states/items/total` 仅统计控制模式为 running 的活动目标，在截断前按完整身份集合筛选；暂停或终止目标退出当前警报，自己的历史心跳仍保留，阻塞原因仍在目标字段中。`work.total/delivered` 仅表示计划工作节点数与已冻结交付数，没有计划时总数为 `null`，这些数值不是业务完成百分比。

任务的 `run` 按原稳定提交键核对运行身份，从该 `run_id` 的唯一轮次事件派生最后活动及工具计数。同一集成对话的下一轮不会覆盖上一轮进度。`run.turn` 是本运行的确切轮次，尚未开始时为 `null`；目标同时提供原 `revision/thread_ids`，用于绑定本机节点指令。创建、开始、结束时间保留原运行的 Unix 秒值，`last_activity.time` 保留原事件的 Unix 毫秒值；无本轮事件时为 `null`。`tools.started/completed/errors` 分别统计实际开始、具有唯一且身份匹配结果、已确认错误；`unconfirmed` 是已开始但缺少确切配对结果的总数，包含尚在执行与已中断的调用。`active` 只列当前仍执行的调用名称，最多 16 项，每项最多 128 字符，`active_truncated` 标记截断。轮次结束不能把缺失结果伪装为完成，停止也不证明未知副作用已撤销。运行正文、工具参数、输出和错误原文不进入该运行摘要。

`waiting_for` 来自当前事实，不依据沉默时长猜测卡死：

| 机器值 | 当前等待 |
|---|---|
| `control-paused`、`control-cancelled` | 目标控制已停止该节点的后续调度；已接受的运行及作业仍展示实际等待 |
| `dependencies` | 前置未交付；`unresolved_dependencies` 列出确切节点、任务与状态，尚未创建时后两项为 `null` |
| `worker-capacity`、`conversation-busy` | 工作宿主已满，或该对话的原运行尚未结束 |
| `workspace-preparation`、`dispatch` | 工作资源准备或就绪任务待提交 |
| `run-queue`、`run`、`run-stop` | 原运行排队、执行或停止中 |
| `run-cleanup`、`background-jobs` | 原运行尚未收尾，或已收尾但所属后台作业未结束 |
| `plan-validation`、`delivery-capture`、`failure-settlement` | 等待协调器验证规划、冻结交付或结算明确失败 |
| `submission-reconciliation`、`delivery-reconciliation`、`result-reconciliation` | 已持久记录的提交、导入／捕获或未知操作只读回查 |
| `mission-halted` | 目标已停止调度；未结束节点另带 `scheduling_halted=true` |

已接受的运行在目标停止调度后仍展示其真实执行／收尾状态；已 `done/blocked` 的节点不继承同一会话后续修正轮次的后台等待。状态读取不创建资源、不探测 Git、不推进结算、不发模型请求，也不重放未知操作。目标账本的游标、统计、目标及任务在一次 SQLite 读事务内读取，逐项保留原有哈希与身份验证；原收件箱、运行和后台作业属于独立事实源，不宣称跨库或跨运行的原子快照，也不跨调用缓存授权。

`tests/test_superagent_mission_status.py` 覆盖轮次隔离、工具配对、隐私、截断及等待矩阵；`tests/test_superagent_mission_snapshots.py` 覆盖只读快照、损坏拒绝和并发更新一致性；实际协调器的零写入、未知不推进及原提交身份回归在 `tests/test_superagent_missions.py`。`--superagent-status` 读取最后保存的投影，不证明进程仍存活；完整任务分页和普通用户工作台尚未提供。

### 运行中的节点执行指令

运行服务保持开启时，可向当前 `work` 或 `integrate` 节点补充执行指令，无需停止任务或重新提交整个目标：

```bash
python -m harness.cli --superagent /path/to/superagent.json --superagent-steer "保留现有接口，先核对已有回归再继续实现" --superagent-job 'mission_…/work:first:1' --superagent-request-id guide-1
python -m harness.cli --superagent /path/to/superagent.json --superagent-receipt guide-1
```

只有状态清单未截断、且恰有一个合法活动节点时，`--superagent-job` 才可省略；多节点或 `missions.has_more=true` 时必须指定当前可见的确切任务。只有本机非远端绑定的来源可使用此入口；来源从原目标取得，操作者和原 Thread 范围只有一个合法选项时自动选择，否则分别传 `--superagent-actor`、`--superagent-thread`。这些选项不扩大配置白名单，`ignore` 来源不能提交。规划、独立复核、排队或结束的运行不能接收节点指令；该入口不修改目标依赖图、冻结验收标准或模板权限。

命令只读取最后的状态投影，将不可变输入原子公开到 `guidance/incoming/`，不构造模型宿主、不读取模型凭据，也不能与 `--superagent-once` 合用。返回 `queued=true` 只表示本机输入文件完整公开，不证明服务仍存活、模型已经收到或任务已完成。输入绑定实例、来源修订、操作者、Thread、目标、任务、原 `run_id` 与 `turn`。同一请求身份的相同内容返回原输入，即使运行已经结束也不会转向新节点；任何显式身份或正文变化均报冲突并保留原件。省略请求身份会建立新的请求，跨调用核实时应保留该身份。

服务在独立的 `missions.guidance.sqlite3` 保存操作意图。每次新提交在认领前和等待原生引导锁后复核当前任务、原运行、原轮次及来源授权；锁内检查到接受事件写入之间不让出事件循环。接受后来源撤销不抹去原事实，也不撤销已经接受的输入。受理与应用仍以原会话的唯一轮次、`steering/accepted` 及 `source=steer` 用户事件为事实来源，原始模型正文须与输入相同，不能用展示原文或附件替代。

`--superagent-receipt` 只读 `guidance/receipts/` 中对应身份的最后投影；`status.json` 的 `guidance` 提供计数、最近回执和截断信息，`guidance_files.input_errors` 报告输入文件错误。回执不包含指令正文或工具输出：

| 字段 | 含义 |
|---|---|
| `state=pending/submitting` | 意图等待处理，或已记录一次提交意图 |
| `state=accepted` | 原会话已经持久接受输入 |
| `state=unknown` | 缺少确切原回执；后续只读回查，不重新提交 |
| `state=rejected` | 新提交在明确准入边界被拒绝；`reason` 保留机器原因 |
| `application=pending/applied/unapplied` | 原生输入等待应用、已经进入原轮模型上下文、或该轮结束而未应用；没有原接受事实时为 `null` |
| `accepted_seq/applied_seq` | 原会话中的确切事件序号，缺少对应事实时为 `null` |

`applied` 仅证明指令已进入原轮次的用户消息，不证明模型遵循、工具执行或验收完成。返回或确认丢失时先查原稳定请求；不能确认则保持未知，原异常仍向调用方传播。重启不会把未应用的旧指令投给新运行。服务在应用变化、一次性消费结束和关闭原宿主后刷新回执；关闭前的 `pending` 投影不能替代关闭后的最终核对。

后续集成和独立复核的 `applied_guidance` 只纳入同一目标、原日志确认已应用的上下文；独立工作节点不会收到其他节点的引导。按本实例意图接纳序号优先保留最近内容，再按顺序呈现，最多 16 条、每条 2048 字符、全局 8192 字符，包含 `text_chars/text_truncated` 及 `total/shown/has_more`。它不修改已有运行的 payload、计划哈希或验收标准，也不是通过证据。全目标需求变更需要新的明确目标，动态重规划仍未实现。

引导账本和输入目录各最多保留 1024 条记录，不隐式删除历史或循环复用身份。每轮有界轮转，未知记录不阻塞新输入。已有文件损坏时保留原件并明确报错，不迁移或补造接受事实。SDK 宿主可显式构造 `SuperagentGuidance(coordinator, sources=...)` 并赋给 `coordinator.guidance`，通过 `enqueue(command)` 保存完整协议输入；协调器的 `tick()` 会推进引导，也可显式调用引导器的 `tick()`。构造器不创建后台任务，宿主关闭时须停止准入并等待 `aclose()`。

`tests/test_superagent_guidance.py` 使用真实 Harness、工作树和受控模型验证受理／应用、锁等待撤销、丢失确认、未知零重放、重开、关闭、错误归属及下游上下文；`tests/test_superagent_guidance_local.py` 核对并发原子公开、过期投影、明确目标选择、只读回执、损坏保留和无宿主 CLI。它们不证明在线模型质量或远端信道的交互能力；当前引导未接入 GitHub／飞书或工作台。

## GitHub Webhook 与评论信道

在上述配置中增加可选的 `github` 对象即可启用一个固定仓库的信道；未配置外部信道时保持本地文件模式。所有字段均须显式填写，例如：

```json
{
  "source_id": "github-main",
  "repository": "owner/repository",
  "repository_id": 123456,
  "bot_login": "project-agent",
  "bot_user_id": 234567,
  "secret_env": "HARNESS_GITHUB_WEBHOOK_SECRET",
  "token_env": "HARNESS_GITHUB_TOKEN",
  "api_url": "https://api.github.com",
  "webhook_port": 9010,
  "reply_mode": "disabled"
}
```

`source_id` 必须对应 `sources` 中已有来源，所用 Worker 模板的 `sources` 也须包含它。来源人员使用稳定数字身份，例如 `actors: ["github:user:345678"]`；Thread 使用 `threads: ["issue/1", "issue/2"]`。仓库名与数字仓库 ID、发言账号名与数字用户 ID 均由维护者核实并填写。白名单按确切 Thread 生效，未知 Issue 不会自动获得授权；没有通配符、仓库级自动扩张或配置热更新。需要新范围时使用显式配置的新实例，不能直接替换已有状态目录的绑定身份。

运行前设置 `secret_env` 指定的环境变量，值为 16–4096 个 UTF-8 字节的 Webhook 密钥。`reply_mode=disabled` 只接收事件并生成本地候选，不读取评论令牌，也不调用 GitHub API。改为 `requested` 表示维护者明确授权：向通过来源策略的真人明确请求，以及已有工作订阅下的真人跟进，发送 Router 整理后的评论；此时还须设置 `token_env`。令牌须能通过 [`GET /user`](https://docs.github.com/en/rest/users/users#get-the-authenticated-user) 核对配置的用户，并具有目标仓库评论权限；当前不支持以安装令牌替代用户令牌。API 版本固定为 `2026-03-10`。

配置和账本只保存环境变量名称，密钥值不写入事件、模型提示、状态投影或信道账本。当前宿主环境没有凭据隔离：授予 `full_access` 及 Shell 的 Worker 可以继承进程环境，普通文件权限也不是操作系统沙箱；不能据此宣称密钥对工作执行器不可见。`api_url` 允许 HTTPS，HTTP 仅允许字面回环地址或 `localhost`；带账号、查询或片段的 API 根被拒绝。客户端拒绝所有重定向，避免把 Bearer 凭据转发到重定向地址。

监听器只绑定 `127.0.0.1:<webhook_port>`，接收 `POST /github`。端口 0 由系统分配并在启动提示中打印，固定部署通常填写固定端口。维护者须另行配置 HTTPS 反向代理和 GitHub 仓库 Webhook，使用 JSON 正文、相同密钥并订阅 Issues 与 Issue comments；程序不会部署代理或修改账号设置。公开入口使用 HTTPS 并启用证书验证，遵循 [GitHub Webhook 建议](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks)。

接收端先对原始正文核对 `X-Hub-Signature-256` 的 HMAC-SHA256，再解析严格 JSON，按 [GitHub 验签协议](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries) 使用常量时间比较。只消费 `issues/opened` 和 `issue_comment/created`，其他种类或动作验签后忽略。Issue comments 包括 PR 的顶层会话评论，不包括代码行 review comments；不处理编辑、关闭、CI 或历史事件补拉。事件形状依据 [GitHub Webhook 事件定义](https://docs.github.com/en/webhooks/webhook-events-and-payloads)，不信任正文中的 URL 来选择访问目标。

接入时核对数字仓库 ID 和仓库全名、正文作者与 `sender` 的数字身份及类型。真人映射为 `github:user:<id>`，机器人映射为 `github:bot:<id>`；配置的自身用户始终不能取得真人请求资格。正文首个非空行须以 `@bot_login` 开头，随后为空白、逗号、冒号或行尾，才标记明确请求；行中、引用或代码块中的提及不产生该标记。来源白名单、真人和订阅规则仍由原收件箱执行。稳定事件 ID 来自签名正文中的 Issue／评论数字 ID，改变 `X-GitHub-Delivery` 不会绕过去重；同身份不同规范化内容报冲突。

验签结果先保存到独立 `github.sqlite3` 的 `origins` 队列，再返回 HTTP 202；`state=verified` 只表示已持久排队，不表示来源策略准入、模型受理或任务完成。每轮先推进最多 16 条事件到收件箱，完成后标记 `accepted`，重复推进复用原回执。未推进队列最多 1024 条，满时返回 503。HTTP 边界限制正文 1 MiB、请求头 8192 字节、并发接收 16 个；读取头和正文分别限制 3 秒与 5 秒。签名错误拒绝，损坏记录保留并明确报错；模型运行不占用 Webhook 确认等待。关闭和启动取消均等待已接受操作并释放所属监听器。

`outbound` 表独立保存 Router 候选对应的评论意图、来源修订、原 Issue 和远端回执，`cursors` 保存收集及轮转位置，`channels` 绑定配置。只消费具有已验签来源依据的原事件；本地文件即使写入相同 `source_id`，也不能自行获得 GitHub 发言资格。发送前复核当前来源模式、人员、Thread 和精确修订，再以 API 核对令牌用户及数字仓库 ID。目标仅由固定 API 根、仓库和原 Issue 编号构造，Worker 和 Router 输出不能选择收件目标。

每次认领先把意图从 `pending` 持久改为 `sending`，再发起一次评论 POST。正文含稳定隐藏标记 `<!-- harness-superagent:<候选ID> -->`；收到并校验 201 回执后记录 `sent` 和评论 ID。评论创建和分页查询使用 [Issue comments API](https://docs.github.com/en/rest/issues/comments?apiVersion=2026-03-10)。`drafts/` 中的原候选仍为本地 `draft`，实际外部发送事实以发件箱为准；`sent` 也不代表代码验收、PR 合并或用户满意。

网络异常、返回丢失或本地确认失败后保留原异常，只读取原 Issue 的评论，核对完整正文、稳定标记、自身作者数字 ID、Issue URL 和正数评论 ID。最多读取十页、每页 100 条；唯一确切匹配才能补记 `sent`，多个匹配报冲突，没有证据则保留 `unknown`。未知项每次回查后至少间隔 30 秒，轮转时仍允许独立新意图推进。重启后 `sending` 同样只回查，不因缺失评论或超时再次 POST；不承诺平台层面的恰好一次投递。已观察到的原错误仍向调用方报告，回查成功不会掩盖它。

`tests/test_superagent_github.py` 使用隔离仓库、签名夹具和实际回环 HTTP 服务，覆盖接收、Router／Worker／结果链路、评论发送、返回丢失、未知不重发、重开、范围撤销、禁止重定向、并发及取消收尾。测试没有访问真实 GitHub 账号或在线模型；实际账号权限、公开代理、平台投递和在线模型质量仍需各自的运行证据。

## 飞书加密 Webhook 与原消息回复

使用项目解释器安装 `pip install -e ".[superagent]"` 后，可在同一配置中增加 `feishu` 对象。基础安装不会因未启用飞书而导入加密库；`.[test]` 包含对应依赖，以便回归覆盖实际解密。所有字段均须显式填写，例如：

```json
{
  "source_id": "feishu-main",
  "app_id": "cli_your_app",
  "tenant_key": "your-tenant-key",
  "bot_open_id": "ou_your_bot",
  "chats": ["oc_your_chat"],
  "encrypt_key_env": "HARNESS_FEISHU_ENCRYPT_KEY",
  "verification_token_env": "HARNESS_FEISHU_VERIFY_TOKEN",
  "app_secret_env": "HARNESS_FEISHU_APP_SECRET",
  "api_url": "https://open.feishu.cn",
  "webhook_port": 9011,
  "reply_mode": "disabled"
}
```

`source_id` 对应 `sources` 中已有来源，例如人员 `actors: ["feishu:user:ou_person"]`，Thread `threads: ["chat/oc_your_chat/root/om_topic_root"]`；工作模板的 `sources` 还须包含此来源。`chats` 最多 256 项，限制事件及回复目标的聊天范围，不代替确切 Thread 白名单。Thread 由聊天 ID 和消息 `root_id` 构造，根消息缺少 `root_id` 时使用自身消息 ID；平台 `thread_id`、显示名及正文 URL 不参与授权。新话题不自动准入，当前仍需显式的新实例配置，尚无动态范围编辑入口。

同一实例可同时配置 GitHub 与飞书，它们必须使用不同的 `source_id`，固定的非零监听端口也须不同。要跨平台续接任务，先在同一 Worker 模板中授权两个来源及相应人员／Thread；Router 选择已有任务后复用其对话、工作树、模型与工具范围。来源身份与原消息引用各自独立，不通过标题相似自动共享所有上下文。

当前支持一个自建应用及固定租户的加密 HTTP 事件订阅，不实现商店应用多租户票据、长连接、历史补拉、图片、文件、富文本或卡片回调。只消费 schema `2.0` 的 `im.message.receive_v1` 文本消息，字段来源见[接收消息](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)及[官方 SDK 事件类型](https://github.com/larksuite/oapi-sdk-python/blob/v2_main/lark_oapi/api/im/v1/model/event_message.py)。接入核对应用 ID、事件与发送者的租户、聊天、发送者 Open ID 和类型；用户映射为 `feishu:user:<open_id>`，应用消息与自身机器人不能取得真人请求资格。

私聊中的授权真人消息视为明确请求。群聊须在正文首个非空行以原消息 `mentions` 中、指向配置机器人 Open ID 的 `@_user_N` 占位符开头，随后为空白、逗号、冒号或行尾。显示名称、普通 `@名字`、行中提及、引用和代码块不授予该标记。正文保留平台原文和占位符，不按显示名反向匹配身份；已有工作订阅下的授权真人跟进不必再次提及机器人。

设置 `encrypt_key_env` 和 `verification_token_env` 指定的变量，值均为 16–4096 个 UTF-8 字节。监听器只绑定 `127.0.0.1:<webhook_port>`，接收 `POST /feishu`；维护者须另行配置 HTTPS 代理和加密消息事件订阅，程序不部署代理或修改应用／账号。加密格式依据[官方 SDK 解密实现](https://github.com/larksuite/oapi-sdk-python/blob/v2_main/lark_oapi/core/utils/decryptor.py)：密钥由 Encrypt Key 的 SHA-256 派生，使用 AES-256-CBC，前 16 字节为 IV，校验 PKCS#7 填充；不接受普通明文事件。

普通事件在解密前对原始正文核对 `X-Lark-Signature`，计算 `SHA256(timestamp + nonce + EncryptKey + raw_body)`，使用常量时间比较，再检查解密后的 Verification Token。算法和地址验证分支参见[官方事件处理器](https://github.com/larksuite/oapi-sdk-python/blob/v2_main/lark_oapi/event/dispatcher_handler.py)。本实现允许签名时间戳最多过去 8 小时、未来 5 分钟；时间窗覆盖平台列出的重试间隔，另按稳定消息身份去重。平台存在重复投递和多次重试，见[事件订阅说明](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/event-subscription-guide/event-subscriptions)。

地址验证是窄例外：加密的 `url_verification` 可以没有签名头，但仍须解密成功且 Verification Token 匹配，随后只返回 `{"challenge":"原 challenge"}`，不排队、不调用模型或 API。没有签名的普通事件、部分签名头、错误令牌、非法密文及错误填充均拒绝；对外认证错误不暴露密钥或解密细节。

独立 `feishu.sqlite3` 保存事件 ID、首次投递 ID、规范化事件、原聊天／消息／根引用、指纹及加密原正文摘要，再返回 HTTP 200。`state=verified` 只表示已持久排队，不表示来源准入或工作完成。改变投递 ID 不能绕过去重，同身份不同内容报冲突；每轮最多推进 16 条到收件箱，未推进队列最多 1024 条。HTTP 大小、并发和读取时限与 GitHub 相同。启动取消或后续监听器失败会收尾此前已绑定的监听器，状态投影分别列出各信道的队列与发送计数。

`reply_mode=disabled` 只生成本地候选，不读取 App Secret，也不访问 API。显式选择 `requested` 后，读取 `app_secret_env` 指定的 App Secret，以自建应用凭据获取租户访问令牌；令牌仅在内存缓存，过期前刷新，并发请求共享一次刷新。维护者须为应用配置发送、读取原消息及历史所需权限；程序不自动申请权限。发送前核对机器人 Open ID，通过原消息 API 确认聊天、消息 ID、类型、根引用及未删除状态，再复核来源人员、Thread、模式和精确修订。API 根由维护者固定，允许 HTTPS，HTTP 仅允许回环地址或 `localhost`，所有请求拒绝重定向。

发件箱只消费具有已核验飞书原事件依据的 Router 候选；本地文件不能仅靠相同来源名取得发言权。每个意图先持久认领为 `sending`，再调用一次[回复消息 API](https://open.feishu.cn/document/server-docs/im-v1/message/reply)，固定原消息 ID、`msg_type=text` 和 `reply_in_thread=true`，并携带稳定 UUID。回复添加可见的 `[ref:report_…]` 引用，供丢失回执后核对；Router 为此来源最多生成 99 词，连同引用最多 100 词，汉字逐字计数。超限结果明确拒绝，不拆分消息或静默截断。

成功响应须匹配回复 ID、原聊天、根、直接父消息、完整正文及自身应用／租户作者身份，才能记录 `sent`。网络异常、API 错误或本地确认失败保留原异常，按原聊天分页回查完整引用和上述身份。每轮最多十页、每页 50 条；仅完整分页范围中的唯一确切匹配才能补记 `sent`。未读完的范围或没有依据保留 `unknown`，分页令牌循环明确报错；未知消息不再次 POST 回复，即使携带相同 UUID。读取可能刷新访问令牌，正常未知回查至少间隔 30 秒，后续独立意图仍可推进。

密钥值、App Secret 和租户访问令牌不写入事件、模型提示、状态或信道账本。它们仍受宿主环境边界约束：获得 Shell 和完整权限的 Worker 可以继承环境，本地文件权限不构成凭据隔离。`sent` 只证明外部回复事实，不代表代码验收、合并或业务完成。

`tests/test_superagent_feishu.py` 以隔离 Git 仓库、实际本机 HTTP 服务和确定性模型驱动接收、续接及发送，另用独立 Node AES 实现核对互操作；覆盖挑战、重投、身份拒绝、策略撤销、未知不重发、重启、令牌共享、关闭和监听失败收尾、100 词边界及 GitHub→飞书续接。没有访问真实账号、公开代理或在线模型，不代表真实部署、跨平台或模型研判质量验收通过。

## 已实现的持久事件入口

`harness.app.superagent_inbox.SuperagentInbox` 是供可信宿主显式构造的 SDK 存储入口，也由上述显式 CLI 模式装配；普通 CLI 和工作台不自动启用。它只保存事件、任务到对话的引用、订阅和接收时的分发意图；执行接入由独立投递器负责。来源签名、账号身份、发送者身份和明确请求标记须由接入适配器验证，不能直接相信外部请求体中的同名字段。

```python
from pathlib import Path
from harness.app.superagent_inbox import SuperagentInbox

# 此目录由宿主明确指定；不修改现有对话或运行数据库。
inbox = SuperagentInbox(Path("/srv/agent-state/superagent.sqlite3"), instance_id="team")
policy = inbox.configure_source(
    "github-main", mode="requests", actors=["alice"], threads=["issue/1", "pr/2"]
)
# 对话须由宿主实际创建并确认；登记引用本身不创建或核验对话。
inbox.register_task("fix-1", session_id="existing-worker-conversation")
inbox.subscribe("fix-1", source_id="github-main", thread_id="issue/1")
receipt = inbox.receive(
    source_id="github-main", event_id="remote-event-1", actor_id="alice",
    text="Please investigate the regression.", thread_ids=["issue/1"],
    human=True, explicit=True,
)
stored = inbox.event(source_id="github-main", event_id="remote-event-1")
```

`configure_source` 使用 `expected_revision` 比较保存：首次创建为 0，更新必须传入 `source(source_id)` 返回的当前修订。`mode` 为 `ignore`、`requests` 或 `candidates`；人员和 Thread 均须在显式白名单内，空白名单不表示允许全部。事件关联的所有 Thread 都须被允许，不能借合法 Thread 夹带另一来源范围的上下文。

`requests` 接收授权真人的明确请求，或授权真人在已有工作订阅下的跟进；机器人即使设置明确请求标记也不在此模式下准入。`candidates` 可接收白名单中的其他候选事件，但不代表已获准执行或回复。`ignore` 保留过滤回执，不分发。来源、人员、Thread 或请求条件不满足时，`disposition` 分别为 `source_ignored`、`actor_denied`、`thread_denied`、`request_required`；通过时为 `accepted`。

事件身份按实例、来源实例和远端事件 ID 隔离。同一身份与相同内容重复接收只返回原回执并标记 `duplicate=true`；正文、发送者、关联 Thread 或真人／明确请求标记不同则报冲突，不覆盖原件。Thread 列表按集合处理，顺序变化不产生新事件。正文保留原文，包括斜杠命令；此层不会解释或执行正文。

事件、准入结论、使用的策略修订和全部分发意图在同一 SQLite 事务中保存。已订阅任务直接记入 `routes`；同一任务关联多个 Thread 时只记一次，任一工作订阅优先于上下文订阅。仅上下文订阅不取得处理归属，仍给 Router 留一条意图；存在工作订阅时不额外要求 Router 再分派。Worker 意图携带原 `task_id` 和 `session_id`，Router 意图中两者为 `null`。这些记录只是接收时的路由事实，不是执行或消费回执。

`unsubscribe` 只移除指定任务的指定订阅。`stop_task` 停止后续路由并移除其订阅，不停止执行器中可能已受理的任务，也不撤销历史分发。任务到对话的绑定不可替换，停止后不能通过重复登记隐式恢复。重开数据库、修改订阅或调整来源策略不会重新分发旧事件；执行层消费意图时仍须重新验证当前权限和任务状态。

`subscribe_assigned(task_id, source_id=..., event_id=..., expected_revision=...)` 是供可信协调宿主使用的关联原语：在同一收件箱事务内核对原事件、真人明确请求、Router 意图、当前来源修订及范围、任务活动状态，再把事件的全部 Thread 设为该任务的工作订阅；原事件的历史 `routes` 保持原样。它不校验宿主模板或对话资源，这些由下文创建器的 `attach` 入口复核。`subscribed(task_id, source_id=..., thread_ids=[...])` 只读检查所有指定 Thread 是否已有工作订阅，不授予权限。协调运行器通过独立关联账本控制认领，不能在每次容量续投时重复调用订阅原语、覆盖后来的取消订阅。

`events(after=0, limit=100)` 按实例内的接收顺序有界读取，返回 `events` 与 `next_after`，末页为 `null`。`event` 按来源和远端身份精确回查；`source`、`task` 读取当前登记。返回值为独立快照。事件正文最多 65536 个 UTF-8 字节，单事件关联最多 16 个不同 Thread；来源白名单各最多 256 项，实例／来源／远端事件／人员／Thread／任务／对话身份各最多 256 个 UTF-8 字节。一次接收最多分发给 128 个工作任务，超过时整个接收事务拒绝，不留下部分事件。身份和正文拒绝 NUL 及非法 UTF-8，布尔字段不接受数字或字符串转换。

存储沿用当前 SQLite 结构校验，不对已有数据库补表、迁移或修复损坏记录；首次构造应使用专用的新数据库路径。每次操作使用独立连接，不持有后台任务或长期连接。错误按调用语言解析，事件原文不翻译。回归见 `tests/test_superagent_inbox.py`，覆盖接收竞争、策略竞争、隔离、多对多分发、上下文订阅、事务回滚、过滤回执保持、分页及损坏原件保留；这些回归不证明后续执行、外部来源验签或在线交付已完成。

## 已实现的工作层投递

`harness.app.superagent_dispatch.SuperagentDispatcher(harness, inbox)` 将已有工作订阅的真人事件交给真实 `RunController`，并接收可信协调宿主指定的明确真人请求归属。宿主须拥有接入来源与操作所绑定工作对话的授权；来源白名单不代替外部身份认证，也不自动授予任意工具或外部发言权限。投递器本身不进行语义研判，不消费上下文订阅、机器人候选或新的主动授权决策，不生成任务、不创建工作对话，也不自动运行后台轮询。

宿主在异步上下文中调用 `await dispatcher.dispatch(source_id="github-main", event_id="remote-event-1")`。`dispatch_assigned(source_id=..., event_id=..., task_id=..., expected_revision=...)` 供可信协调宿主派送具有 Router 意图的明确真人请求，不能派送机器人、仅跟进或已经归属工作订阅的事件；任务仍须由宿主实际建立并登记。可选的 `expected_revision` 绑定研判使用的来源修订，启动等待后再次核对。两种入口共用事件到任务的稳定提交键；指定归属不修改原接收回执。

`deliveries` 返回已经受理的 `task_id`、`run_id` 和 `state`；`skipped` 列出当前不准入的工作任务，`deferred` 列出达到并发上限而尚未提交的任务。`accepted` 仅表示执行器受理，不表示模型已经读取、执行成功、产出有效交付物或业务验收通过。运行结果仍从原 `RunController` 查询，不在协调存储复制一份运行状态。

投递器在 `<收件箱文件名去扩展名>.deliveries.sqlite3` 中保存独立投递事实，也可由可信宿主显式传入另一专用 `path`。它与收件箱、运行数据库不是跨库原子事务。先持久认领事件到任务的投递，再以绑定实例、事件及任务的稳定 `key` 提交现有运行；`RunController.find_submission(session_id, key)` 提供无副作用的精确回查。并发重复请求只有首次认领者调用 `submit`。

来源正文封装为带引用身份的 JSON 事件，完整保留原文；外层双语提示说明来源内容与授权的区别，不让正文起始的 `/goal` 等文本进入宿主命令解析。首次投递保存当时宿主语言的完整正文，以后切换语言或重开投递器不重建该正文，避免改变已受理提交的指纹。该封装不是提示词注入的完整防护，也不替代现有工具权限；自动创建的 Worker 另有后文所述模板范围，对外评论由可选 GitHub 发件箱独立核对授权。

每次新提交前核对当前来源白名单、来源模式和任务状态；等待宿主启动后再核对一次。`close()` 同步停止投递器后续准入，宿主关闭期间也拒绝新投递；已受理运行继续归原宿主收尾。`max_active` 默认 3，范围为 1–128；按宿主当前活动及排队的根运行数量限制新增提交，达到上限时保留事件原件并返回 `deferred`。当前没有自动续投，宿主须在容量释放后显式再次调用原事件投递。

投递表状态为 `submitting`、`accepted` 或 `unknown`。运行提交及本地确认之间失败时，保留原异常并只回查原提交键；有匹配运行证据则补齐原受理回执，缺失证据则保留 `unknown`。已有认领没有租约到期重试，不因看不到提交就再次调用执行器。再次请求同一事件也只回查原键，不能自动恢复未知执行；原任务停止或策略改变不抹去已受理运行的归属。显式解决未知状态、输入采用回执和安全边界内的实时引导仍属后续工作。

`tests/test_superagent_dispatch.py` 使用临时数据和确定性传输驱动真实 Harness，验证实际工作对话、并发隔离、稳定键查回、返回丢失、本地确认失败、未知结果不重放、重开宿主、来源撤销、容量保留和双语正文。它不调用在线模型或真实外部账号，不能证明长期自动调度和实际业务质量。

## 已实现的固定批次 Router 与消费循环

`harness.app.superagent_router.SuperagentRouter` 为可信宿主提供显式 SDK 协调循环。宿主准备工作层 `dispatcher`，以及独立的 Router Harness 和对话，然后构造：

```python
from harness.app.superagent_router import SuperagentRouter

# router_host 使用专用的新 home，并关闭 enable_agents、enable_skills、
# enable_goal 和 enable_plan_mode；不与工作层复用 Harness 或 home。
coordinator = SuperagentRouter(
    dispatcher, router_host, session_id=router_conversation.id,
    workers={"fix-1": "Investigate and fix regressions in this project"},
)
status = await coordinator.tick()
# 只推进、不构造状态；需要时另行调用 coordinator.status()。
# await coordinator.tick(include_status=False)
# 或由宿主管理停止事件，并等待消费循环；构造器不创建后台任务。
# await coordinator.serve(stop_event)
```

`workers` 是宿主预授权的已有任务 ID 到语义说明的映射，最多 128 项，说明最多 4096 个 UTF-8 字节。这些任务和对应对话必须已经存在；未配置下一节的 `worker_factory` 时至少提供一项，配置后可为空。Router 使用独立 `RunController` 和连续对话，以空的 `ToolRegistry.restricted([])` 同时约束实际模型目录与工具调用；禁用技能、团队、目标和计划入口，避免来源正文触发这些解释路径。这是运行时能力约束，不是操作系统沙箱；可信宿主的钩子、模型传输和其他进程仍由宿主负责。

协调器按收件箱游标读取事件。已有工作订阅的事件进入持久输入队列，直接复用投递器；只有未归属且具有明确真人请求标记的事件进入模型批次。过滤事件和机器人候选推进游标，不启动模型。主动规则授权尚未实现，`candidates` 中的机器人不会自动派工。

`batch_size` 默认为 8，范围为 1–16。批次固定原事件、当前来源修订及 Worker 名单，完整模型输入最多 8388608 个 UTF-8 字节；超过上限拒绝本次收集并保留原游标。推理期间新到事件留在收件箱，最多保留一个未结算研判批次。Router 必须返回严格 JSON：一个 `assignments` 数组，批次内每个事件恰好对应一个 `trigger_id` 与允许名单内的 `task_id`，或以 `null` 明确不派工。遗漏、重复、未知身份、额外字段、非 JSON 正文和运行失败都拒绝该批次，不自动纠正或重跑模型。原模型输出保留在原运行记录中。

批次、游标和输入意图保存在独立的 `<收件箱文件名去扩展名>.router.sqlite3`，可通过专用 `path` 指定。配置绑定原收件箱、投递库、两个宿主 home、Router 对话及 Worker 名单；可选创建器的配置和数据库路径也参与绑定。重开时不允许静默替换这些身份。收集和决策各自在协调库内原子提交，不与运行库宣称跨库原子性。先把批次从 `ready` 持久认领为 `submitting`，再提交原执行器。稳定键绑定实例、Router 对话及冻结正文；已认领批次只回查原键，已完成的原运行可在重开宿主后生成原决策，不重新推理。无运行证据时保留 `unknown` 并暂停后续收集，当前没有自动重试或人工解决未知批次的 SDK。

模型决策落盘后，待派送输入复用原投递器。来源修订在新提交前及启动等待后复核，改变后记为 `skipped`，不能用旧研判扩大当前策略。任务停止也阻止新提交；已经受理的投递仍按原键核对，不撤销或抹去原运行。容量不足的输入保持 `pending`，后续 `tick` 自动续投；输入队列施加有界背压，每次最多推进 16 项。`unknown` 工作投递单独保留，不通过消费循环重放。

`status()` 只读返回游标、输入状态计数及最近批次的状态、原 `run_id` 和机器原因。输入状态包括 `waiting`、`pending`、`settled`、`unknown`、`skipped`、`rejected` 和 `ignored`；其中 `settled` 仅表示派发阶段已结算，仍不表示 Worker 完成或业务验收通过。`tick(include_status=True)` 默认推进后返回状态，显式传 `False` 时只推进并返回 `None`，参数只接受布尔值；自主协调器沿用这一调用约定。CLI 服务在完成候选与信道处理后统一构造一次最终状态，不在同一轮重复回读。

`serve(stop_event, interval=0.5)` 持续调用有界推进，间隔范围为 0.01–60 秒；停止事件、取消等待或 `close()` 停止后续消费，不取消宿主已受理的运行。两个运行器须归属同一事件循环，跨线程调用协调器会回到工作宿主所属循环。调用失败向宿主传播，循环不会吞掉错误继续运行。

`tests/test_superagent_router.py` 使用独立临时宿主和确定性模型传输，核对真实模型请求、实际 Worker 对话、英中英切换、冻结批次、并发消费者、容量续投、严格决策、策略撤销、原返回丢失、损坏原件及重开后的精确回查。它不证明在线模型的研判质量，也不证明真实信道、长期运行或业务验收已完成。

## 已实现的工作任务自动创建

`harness.app.superagent_workers.SuperagentWorkers` 将可信宿主的创建模板接入 Router。模板是 `SuperagentWorkerProfile(repo, description, sources, tools, model=None)`：仓库目录、说明、允许的来源、工具名单及可选模型全部由宿主指定，模型不能提供路径或扩大授权。来源须已登记，工具须已存在于宿主当前有效目录中；空工具名单明确禁止全部工具。最多 32 个模板，模板 ID 最多 64 个 UTF-8 字节，来源和工具名单各最多 256 项，说明最多 4096 个 UTF-8 字节。

```python
from pathlib import Path
from harness.app.superagent_workers import SuperagentWorkerProfile, SuperagentWorkers

# worker_host 使用专用 home，启用 enable_worktrees，关闭 enable_agents、
# enable_skills、enable_goal、enable_plan_mode、enable_subagent 和 enable_ralph。
# tools 使用该宿主实际注册的工具名，由宿主按任务授权选择。
factory = SuperagentWorkers(
    worker_host, inbox, owner_session_id=router_conversation.id,
    profiles={"coding": SuperagentWorkerProfile(
        repo=Path("/srv/project"), description="Investigate project regressions",
        sources=("github-main",), tools=("read", "edit"),
    )},
)
coordinator = SuperagentRouter(
    dispatcher, router_host, session_id=router_conversation.id,
    workers={}, worker_factory=factory,
)
```

创建器、投递器须使用同一工作宿主及收件箱，创建器的 `owner_session_id` 必须是 Router 对话 ID。每个工作宿主中同一实例只绑定一个活动创建器。构造时只登记配置和生命周期，不创建工作树、对话或模型运行。创建器与普通对话可共存，但本阶段要求工作宿主关闭上述六个功能入口；尚未实现受管理 Worker 的子智能体权限继承，不能据此启用子智能体或 Ralph。显式自定义工具仍由可信宿主负责其真实能力。

模型目录中的 `new:<模板 ID>` 表示为该事件创建新任务；已有任务 ID 表示复用原工作对话，`null` 表示不派工。创建器已就绪的活动任务也自动进入后续批次的可选名单，不需要宿主再次手工登记。模板目录、已有任务卡片、原事件和来源修订随批次冻结；决策事务将创建选择转换为绑定实例、批次及事件的稳定任务 ID，并保存模板 ID。模型输出不能任意指定新 ID。Router 在工作容量释放后才调用创建器；`ensure(batch_id=..., item={id,source_id,event_id,revision}, profile_id=...)` 是同一流程的可信宿主 SDK 入口。它只为仍获准的、尚未归属的明确真人请求创建资源；机器人、上下文事件、来源范围不符或修订变化均不新建资源。

创建器先保存任务意图和当时仓库的 `HEAD` 提交，再调用原 `WorktreeService.create_team` 为该任务建立独立分支与工作树。只包含该提交的内容，不导入源工作目录的暂存、未暂存、未跟踪或本地环境文件，不隐式清理源目录。随后在该工作树中建立独立对话，初始对话头原子保存模板、实例、任务、工作树及配置指纹；可选模型也在初始头中固定。最后登记任务并订阅原事件的全部 Thread；后续事件经原收件箱直接路由给该 Worker，不再调用 Router。订阅、创建账本与对话头不是跨库原子事务，部分完成时保留事实，以原身份继续核对，任务停止不能被重复登记隐式撤销。

创建账本默认为 `<收件箱文件名去扩展名>.workers.sqlite3`，也可指定新的专用 `path`。配置绑定宿主 home、收件箱、Router 身份、全部模板与创建数量上限；重开时不改写配置，不迁移已有数据库。`max_workers` 默认为 128、范围为 1–128，限制该实例账本中的创建记录总量，包含未知和停止任务，达到后保留待派送输入并明确报 `superagent/worker-capacity`；当前没有自动清理、循环复用名额或归档接口。

资源状态按 `planned`、`workspace_creating`、`workspace_ready`、`session_creating`、`session_ready`、`ready` 推进，另外保留 `unknown` 和 `skipped`，`stage` 表示所属资源阶段。每次创建前持久认领；重复请求共享同一所属操作，已认领阶段只按确切工作树标签或初始对话元数据回查，不把“找不到”当作可重试证据。返回丢失或本地确认失败保留原异常；资源存在且身份一致时补齐引用，无法确认则保留 `unknown`，Router 将该输入保留为 `unknown` 而不重复创建。资源就绪不代表模型执行或业务完成。

受管理对话每次进入运行和获取 `AgentLoop` 时，都核对绑定创建器、创建记录、原工作树及模型元数据，并重新绑定模板的 `ToolRegistry.restricted` 视图。重启后未重新装配创建器、角色或资源损坏、模型元数据被替换时，明确拒绝执行，不退回全局工具目录；普通对话保留原工具范围。受管理对话不能执行 `/runner`、`/agent`、`/skill`、`/goal`、`/plan` 宿主命令，这一检查在命令和外部运行分流之前进行。该限制及独立工作树都不是文件系统沙箱，工具自身权限与外部副作用仍受原宿主策略约束。

`worker(task_id)` 回查确切创建事实，`status()` 返回状态计数与最近 16 项资源引用，Router 的 `status()` 也包含该摘要。`stop()` 同步停止创建准入，`aclose()` 等待所属操作；宿主关闭时同步停止运行准入，并等待创建器已经接受的 Git 写入。创建等待者取消不会遗落后台线程或重放操作，关闭后不建立迟到对话。创建失败和清理失败保持原异常。已建立的工作树、分支和对话不会因关闭、撤销来源或失败而自动删除。

### 已有任务的续接与 Thread 关联

`ready_catalog(source_ids)` 只列出模板允许接收本批次至少一个来源、任务仍活动且原事件仍在当前来源人员／Thread 范围内的 Worker。卡片保留模板、来源、原事件与 Thread 引用，原请求只内联前 512 个字符，并记录原长度与截断标记；来源预览是不可信数据。`routing_entry(task_id)` 用于核对确切卡片来源，不表示当前准入。当前卡片描述初始请求，没有伪造最新工作摘要，也没有提供完整链式召回工具。

Router 对照冻结名单选择已有任务；推理期间新建 Worker 不能被旧批次选中，新增或停止任务也不会改变已提交的正文。选择自动创建的已有 Worker 后，`attach(task_id, source_id=..., event_id=..., revision=...)` 再检查模板的来源名单、任务活动状态、原对话／工作树／模型身份和当前事件授权，然后把新 Thread 关联到原任务。批次包含多个来源时，某个任务可供其中一条事件选择，不代表它获准接收其他来源；每条派送仍独立复核。

关联沿用原任务 ID、工作对话、目录及工具范围；首次事件使用原投递器的稳定键进入同一工作对话，后续授权真人的非明确跟进按订阅直通。工作树仓库路径按当前操作系统解析后的身份核验，Windows 的正反斜杠等价形式不构成不同仓库；其他仓库、提交或资源身份仍拒绝，不改写已有登记。可以把两个已授权来源中的 Thread 关联到同一任务，从而保留工作上下文；可选 GitHub／飞书适配器按各自原事件核验身份和回复目标，实际部署与账号权限仍需另行验证。模型只负责判断请求是否属于同一任务，不能扩大来源白名单或复活已停止任务。回复始终关联到各自原事件；例如续接另一条 GitHub Issue 后，结果评论仍发往这次请求的原 Issue。

关联账本位于 `<创建账本文件名去扩展名>.bindings.sqlite3`，独立绑定原创建器身份，状态为 `applying`、`applied`、`unknown` 或 `skipped`。先按实例、任务和原事件保存认领，再在收件箱内执行一次原子订阅；两个数据库不是跨库原子事务。返回或本地确认失败时保留原异常，只核对所有原 Thread 的工作订阅；当前确有完整依据才能确认 `applied`，没有依据则保留 `unknown`，不能因看不到订阅再次写入。`applied` 保留原关联事实，后来的取消订阅不会被重复消费覆盖。关联未知的输入记为 `unknown`，不进入工作执行；当前没有自动重新关联或人工解决入口。

`tests/test_superagent_workers.py` 使用临时 Git 仓库和真实 Harness 验证模型选择模板、多个独立 Worker、任务复用、跨来源续接、冻结名单、来源修订、订阅直通、工具目录及幻觉调用拒绝、重启角色重绑定、宿主命令拒绝、返回和确认丢失、未知结果不重放、取消、关闭、数量上限与英中英资源实际消费。`tests/test_superagent_github.py` 另以真实回环 HTTP 验证关联另一条 Issue 后复用原 Worker，且评论返回各自来源。它们不运行在线模型、真实账号或云端执行器。

## 已实现的内部结果与本地回复候选

`harness.app.superagent_results.SuperagentResults(router)` 是可选的 SDK 结果消费者，由本地 CLI 模式自动装配。`SuperagentDispatcher.deliveries(after=0, limit=100)` 和 `delivery(trigger_id, task_id)` 提供只读投递引用；结果消费者独立保存读取游标及等待中的运行引用，不复制或改写原运行状态。它每轮有界收集与轮转检查，尚未结束的 Worker 不阻塞其他已结束 Worker 的报告。

工作运行的终态及收尾句柄都已结束后，消费者重新核对原稳定提交键、原 `run_id` 和正文，读取原最终输出与机器错误码。提供给 Router 的结构化报告携带任务、原事件、Thread、运行状态和原运行引用；Worker 输出最多取前 8192 个字符，并附原长度和截断标记，完整原文保留在工作运行中。工具中间输出、推理和进度日志不自动广播。

整理提示按 Router 当前语言生成并冻结，以 SHA-256 核对保存正文；来源请求和 Worker 输出均标明为不可信数据。Router 在同一个连续对话中整理报告，仍无工具，并与研判共用原串行执行器。整理必须返回严格 JSON `{"reply": "候选正文或 null"}`，机器 `null` 表示保持安静，不能用文字 `"null"` 代替。重复对象键、额外字段、非标准数值及非法正文均拒绝，不自动修复或重跑模型。候选最多 4096 个 UTF-8 字节且最多 100 个词；汉字逐字计数，其余非空白片段按词计数。可信宿主可用 `SuperagentResults(..., reply_limits={source_id: 词数})` 为来源设置 1–100 的更小预算，预算绑定消费者身份，读取时返回独立快照；CLI 为飞书来源设置 99，为发送引用预留一个词。提示与实际输出校验使用同一来源预算。Worker 声明不是独立验收证据，运行结束也不自动成为业务完成。

结果账本默认为 `<收件箱文件名去扩展名>.results.sqlite3`，也可使用新的专用 `path`。状态为 `waiting`、`ready`、`submitting`、`accepted`、`unknown`、`draft`、`silent`、`rejected` 或 `suppressed`。先认领整理意图，再以稳定键提交 Router；提交返回或本地确认丢失时保留原异常，只回查原键，不因缺失记录重放。重启已完成的候选不会重新生成，来源策略在整理前、启动等待后和整理完成时复核，修订变化或范围撤销会抑制尚未生成的候选。已保存候选保留历史事实，不能据此绕过未来外部发送时的独立权限复核。

装配后 `router.tick()` 自动推进结果收集与整理，`router.status()` 增加 `results` 计数；`drafts(after=0, limit=100)` 只读返回本地候选及两个原运行的引用。结果消费者只产出本地候选；可选 GitHub／飞书发件箱独立消费这些引用并记录发送事实，其他信道的发送适配仍待实现。`tests/test_superagent_results.py` 验证实际 Router 请求、两类运行引用、快慢 Worker 隔离、来源撤销、返回丢失、未知结果、严格格式、损坏原件保留、重启和英中英切换。

## 当前可复用能力

| 能力 | 源码事实 | 接入时的边界 |
|---|---|---|
| 持久运行与幂等提交 | `app/runtime.py` 的 `RunController.submit` 在运行记录和 `run_keys` 中保存提交及正文指纹；相同键不同正文拒绝 | 事件分发须传稳定键，不能仅设置 `request_id` 就假定获得幂等性 |
| 独立对话与并发执行 | `app/harness.py` 的 `create_session`、`run_async` 与 `RunController` 按对话组织运行 | 一个协调对话不应承担所有具体任务；新任务应绑定独立工作对话 |
| 后台成员与后续消息 | `subagent/manager.py` 的 `spawn`、`send_message`、`followup_task`、`wait_many`；批量等待返回每个成员的完成或仍运行快照，消息有提交回执，续接核对原委派者及原上下文 | 成员消息不是通用外部事件入口；不能绕过可见性或把任意外部身份伪装成成员 |
| 引导与普通排队 | `RunController` 及对话引导事件分别保留排队和安全边界采用语义 | 外部新事件不应默认取消当前轮次，也不能把普通排队改成隐式引导 |
| 定时自动化 | `app/automations.py` 的 `AutomationService` 提供租约、有限并发、运行记录和关闭准入 | 计划触发与手动运行不构成跨信道 Thread 订阅；调度租约不等于所有运行的分布式所有权 |
| 工具权限与来源 | `app/permissions.py`、`app/session_access.py` 和运行前的 `require_submitter` | “收到事件”“订阅事件”“获准执行”“获准对外发言”须分别判断 |
| 专用工具范围 | `tools/registry.py` 的 `ToolRegistry.restricted` 同时限制模型目录与实际调用，复用原权限钩子及写入协调 | Router 已绑定空工具范围；自动创建的 Worker 绑定宿主模板范围，专用协调工具及工作台入口尚未接入 |
| 工作树与交付 | `app/team_workspaces.py`、`app/worktrees.py`、`collaboration/delivery.py` | 上下文隔离不保证文件隔离；并发写入需独立工作树或明确的共享目录协调 |
| 日志、恢复和证据 | `session/`、`app/recovery.py`、`session/turn_delivery.py`、`session/verification.py` | 轮次结束、工具成功、验证通过、业务验收分别表达，不据摘要猜测成功 |
| 执行环境接口 | `execution/` 的环境提供器边界 | 当前接口不自动实现云端运行、远程机器连接或操作系统沙箱，见 [架构与模块](architecture.md) |

以上路径相对 `harness/`。语言、权限、生命周期和工作树的完整行为继续维护在 [项目约束](project-contract.md)、[扩展能力](extensions.md) 与 [存储与恢复](storage.md)，本页不另建一套协议说明。

### 当前恢复语义

`RunController.__init__` 读取活动运行后，将不属于明确保留队列的运行结算为 `interrupted`，原因为 `run/restarted`。暂停队列中的未开始请求有单独的保留规则。它不会因重启自动重放活动任务；这保护了可能已执行的写入。

`AutomationService` 的调度租约、桌面后端监管及成员续接分别解决不同范围的恢复问题。Superagent 的事件、创建、关联、投递、结果和外部信道账本各自保存恢复依据；它们不替代任务监督、远程设备恢复或长期运行的实测证据。

## 尚缺少的产品闭环

1. 外部来源适配器：在已有 GitHub／飞书身份核验及固定 Thread 范围上补齐动态来源管理、历史补拉、其他信道及真实部署验收。
2. 常驻协调层：在已有连续 Router、任务续接和跨来源 Thread 关联上补齐完整按需召回及协调工具。
3. 自动调度与任务关联：在已有模板创建、任务复用、持续消费和容量续投之上补齐输入采用回执、交回、循环路由限制及归档管理。
4. 持久消费与交付：补齐事件已被模型采用的独立证据、其他外部信道的发送核实及未知状态的显式人工处理入口。
5. 任务监督：Watchdog 已提供持久心跳、停滞识别和安全升级；仍需补齐实际工具进展的跨运行诊断、人工核实及长期运行证据，不能只看进程是否存活。
6. 独立部署与远程执行：计算、持久状态及设备环境的实际解耦，以及设备断线重连的核验。
7. 用户可操作入口：在已有本地 CLI 运行入口上补齐工作台、来源策略编辑、任务归属、待决问题和结果核实。

Watchdog 仍不是跨设备租约、操作系统级进程监督或业务验收。远程执行、断线重连和工作台需要在这些事实之上继续补齐。

`--superagent CONFIG` 开启前述运行模式，按配置选择本地文件及可选 GitHub／飞书信道；以上尚未实现的完整闭环不能由该参数开启，也不能直接加载绑定其他 Harness 运行时的 Superagent 插件。

## 统一整合架构与当前边界

当前 `--superagent CONFIG` 仍启动前述事件路由或自主目标服务。统一协调账本、来源授权与单 Worker 执行基础已通过显式 CoordinatorHost API 提供；这不等于生产入口已经替换。

Phase 4 已增加执行资源作用域、宿主系统节点物化、逐 Assignment 输入投递记录、持久暂停准入，以及经原生 Run 核验的真实文件捕获和导入适配。Artifact / Delivery 服务当前仅能显式内部调用，尚未接入统一 Scheduler；取消意图也尚未接入统一目标停止 Runtime。完整已实现边界和缺口统一见[领域模型](superagent-domain-model.md#9-phase-14-当前实现边界)。

[总体实施计划](superagent-next-generation-architecture.md#phase-4完整-superagent-整合与替换)要求 Phase 4 结束时完成唯一入口、完整业务能力接线及旧编排退役。当前没有旧版运行数据，不开发旧任务导入或历史协议兼容工具；意外发现的旧目录保留原件并明确拒绝混用。工作台、远程执行及跨平台 Sandbox 分别在同一系统上扩展和验收。
