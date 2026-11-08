# Harness Superagent 统一架构与开发计划

> 状态：已确认的唯一开发路线；Phase 6 支持单台原生 Worker、可选 Docker 隔离与多设备调度；单台无 Docker 的真实 CLI 闭环已验证。
> 适用对象：Harness Superagent、Runtime、Source 适配器及工作台开发者。
> 当前实现与未完成项统一见[领域模型第 9 节](superagent-domain-model.md#9-phase-16-当前实现边界)；本页定义开发目标和验收要求，不将目标写成已实现能力。

## 开发执行规则

本页是后续 Superagent 开发的唯一计划入口。领域模型、状态机和 ADR 提供配套协议；产品使用页记录当前实际行为，不构成另一条开发路线。

- **只开发一套完整 Superagent。** 所有新增及改造能力进入统一服务、协调账本、Scheduler、Executor 和 Outbox；简单任务与复杂 DAG 只有计划和完成契约差异。
- **不再沿旧链路开发。** 不为旧 Router / Autonomy 编排增加功能，不包装旧 Router、Dispatcher 或 MissionStore 来实现新服务，不新增 V1 / V2 服务、执行开关、兼容 Facade 或自动回退。缺失能力在统一链路补齐，不能转交旧链路执行。
- **只保留可复用能力与有效业务回归。** 文件交接、证据核验、停止和引导等能力抽离后接入统一接口；每项能力接通后清除对应旧编排与依赖。Phase 4.6 做最终收口核对，不是到该阶段才开始删除旧实现。
- **不开发旧运行数据转换。** 当前没有旧 Superagent 运行数据，不开发旧配置别名、旧任务导入、离线转换、双写、双轨运行或旧版回退工具。意外发现的旧目录保留原件并拒绝混用；已登记的 Coordinator Schema 迁移和原始 Harness 事实继续按现有规则保护。
- **从现有成果继续。** Phase 1–3 为已交付基础，不重做 Phase 0，不重新比较或移植 dsh 运行时。首批统一基础层审查基线为 `a0fbf7d`；以当前源码接续，不回退该提交覆盖后续工作。Phase 4.1 的统一协调契约、Schema v16 状态守卫及 112 项模块回归已验收；Phase 4.2–4.4 的统一生命周期、DAG、质量门与阶段接续已沿同一链路接通；Phase 4.5 的持续协作与既有信道已完成统一服务接线及离线定向验收，Phase 4.6 的唯一入口、统一配置与旧编排退役已完成，Phase 5 的本机产品协作与来源扩展已完成；Phase 6 已实现单台无 Docker 的原生 Worker、可选隔离和多设备路径；单台真实 CLI 与本机定向验证通过，Docker、物理多机和跨平台专项仍需独立验收。
- **以实际消费和业务证据验收。** 建表、增加内部 API、适配器单测或某个阶段提交，均不能代替统一服务接线和真实业务闭环。未接线阶段可通过统一应用服务定向验证，不能借旧链路完成验收。Phase 4 总体验收必须包含唯一入口和旧编排退役。

当前生产入口只装配统一链路；旧编排、独立业务账本消费者、Shadow 及其专用映射 API 已移除。历史迁移和旧目录拒绝混用检查保留，不构成旧版执行入口。

## 1. 目标、范围与设计结论

### 1.1 目标

下一代 Harness Superagent 应同时具备两种能力：

1. **持续协作**：接收 GitHub、飞书、本机及未来其他来源的事件，识别授权，维护长期工作上下文，处理跟进、Review、CI 和外部回复。
2. **复杂自主执行**：把已授权目标转成有界 DAG，调度多个 Worker，传递真实文件交付，执行集成、独立复核、有限修正和结果交付。

目标不是重写 Harness Agent Runtime，也不是把 dsh-superagent 移植成另一套运行时。Harness 现有 Session、RunController、Tool Registry、Worktree、后台作业及原始事件存储继续作为执行事实来源。

### 1.2 一句话架构决策

**以 Harness Runtime 为执行内核，以 dsh 的 Source / Trigger / Thread / Policy / Mandate / Ticket 领域模型为协作基础，以 Harness 的 Mission / Job DAG、真实文件交接、Integrator、Reviewer 和 Watchdog 为自主执行引擎。**

### 1.3 必须坚持的设计原则

- 领域状态与模型执行状态分离。模型只能提出决策和报告，不能直接改写宿主账本。
- 授权是正式、可审计、可重新核验的领域事实，不是 Prompt 中的一段说明。
- Ticket 表示长期授权工作；Mission 表示一次有界交付活动；Job 表示计划节点；Assignment 表示一次实际执行尝试。
- 简单任务和复杂 DAG 使用同一套 Ticket / Mission / Job / Assignment 生命周期及 Scheduler；普通问答可用单节点报告计划，代码和文件交付即使只有一个 work 节点也保留完整质量门。
- 外部事件、模型输出、文件交付和远端消息均按不可信输入处理。
- 业务事务内不调用模型、网络、Shell 或文件系统写入。
- 不确定的副作用进入 unknown 并回查原操作；不能用超时、进程重启或模型自述作为重放授权。
- Harness 的 Run、Tool Event、Usage、Session 和 Workspace 不复制成第二套事实账本。
- 先单实例、本机、单仓库 Mission；多租户、跨仓库 DAG、远程 Worker 和 OS Sandbox 分阶段引入。

### 1.4 本方案不承诺的能力

本方案同时记录目标与阶段出口。当前远程 Worker、原设备断线回查和隔离启动路径的具体实现见产品页；可选容器越权、物理多机及跨平台验收仍未完成；单台原生方式不以这些专项为前提。动态 Source Plugin、任意 PR Review / CI 事件、多人工作台和自动多机故障转移没有实现。

## 2. 当前实现基线与需要解决的问题

### 2.1 开发接续基线

Phase 1–3 已建立 CoordinatorStore、来源与授权服务、原生 Harness 执行适配及单 Worker 内部闭环；首批 Phase 4 改造已补充执行资源作用域、宿主系统节点封存、独立 InputDelivery 账本与逐 Assignment 消费 API、持久暂停和真实 Artifact / Delivery 适配。

统一服务的 Scheduler、质量门、目标停止、信道发送及生产 CLI 已接通。具体已实现行为与限制只维护在[产品页](superagent.md)和[领域模型第 9 节](superagent-domain-model.md#9-phase-16-当前实现边界)。

### 2.2 已确定的参考取舍

dsh-superagent-prototype 仅作为 Source / Trigger / Thread / Policy / Mandate / Ticket、Ports 和事务副作用队列的领域参考。相关选型已经完成，不再将比较原型、复制其 Executor / Store、导入其数据库或接入另一套 Runtime 列为后续任务。

Harness Runtime、原生 Worktree 交付、运行和工具证据继续复用。参考项目的实现状态不能作为 Harness 已完成或已验收的依据。

### 2.3 Phase 4 整合要求（已沿统一链路落实）

1. 将现有来源、授权和单 Worker 基础接入唯一 SuperagentService，清除生产装配对旧目录、Shadow 和旧编排的依赖。
2. 让单节点与多节点真正共用 Scheduler、Assignment 提交、输入投递和原操作恢复；取消单 Worker 的文件交付完成旁路。
3. 将资源创建、文件写入、阶段接续、最终完成与 Outbox 发布全部接入同一授权和持久控制边界。
4. 把真实 Artifact / Delivery、Integrator / Reviewer、有限修正及 Watchdog 接入同一 Mission 生命周期。
5. 将来源跟进、Attention / Handback、节点指令、Channel Sender 和发送回查接入同一协作闭环。
6. 迁接有效业务回归后删除旧编排、独立业务账本、旧配置选择器及旧架构断言，完成唯一 CLI 入口验收。

## 3. 下一代领域模型

### 3.1 核心关系

~~~mermaid
flowchart TD
  S[Source Instance] --> T[Immutable Trigger]
  T --> TH[Source-scoped Thread]
  TH --> E[Evaluation / Gate]
  E --> D[Decision]
  D --> M[Immutable Mandate]
  M --> K[Ticket]
  K --> SUB[Subscriptions / Inputs]
  K --> MS[Mission]
  MS --> P[Immutable Plan Revision]
  P --> J[Jobs / DAG]
  J --> A[Assignment Attempts]
  A --> W[Harness Worker Session / Workspace / Run]
  W --> AR[Artifact / Evidence]
  AR --> DL[Delivery / Merge]
  DL --> I[Integrator]
  I --> R[Independent Reviewer]
  R --> O[Outbox / Channel]
  TH --> CH[Channel Address]
  CH --> O
~~~

### 3.2 实体定义与边界

| 实体 | 下一代定义 | 关键约束 |
|---|---|---|
| SourceKind | 代码层来源类型契约（首批由静态 Registry 注册） | 声明配置 Schema、身份校验、接收、Thread 解析、上下文读取、可选 Thread 生命周期、Channel 和健康能力 |
| SourceInstance | 某个具体来源配置，如一个 GitHub 仓库或飞书 Bot | 身份、配置修订、启停、游标独立；不同账号不能仅凭 kind 合并 |
| Trigger | 来源产生的一条不可变事件 | 由 source_instance_id + external_event_id 唯一标识；相同身份不同正文必须冲突 |
| Thread | 某来源内有序事件流 | Thread Key 必须包含 Source Instance；维护来源生命周期投影；同一 Thread 同时最多一个活动 Eval |
| Channel | Thread 的写入端点 | 只提供地址和发送能力，不代表已经获得发送授权 |
| PolicyRevision | 完整授权策略快照 | 版本不可变；包含规则、Source admission、允许主体和 Bot 规则 |
| Evaluation | 对一批 Trigger 的一次准入和语义研判 | 输入固定、策略版本固定；每个 Thread 串行 |
| Decision | 一次 Evaluation 的最终决策 | 记录原因、审计字段、策略版本及可能创建的 Ticket |
| Mandate | 接受任务时签发的不可变授权 | 保存来源、请求者、强度、规则、范围、策略版本和准入依据 |
| Ticket | 长期存在的授权工作单元 | 持有 Mandate、Thread 订阅、输入、Channel、Mission 历史和终结结果 |
| Mission | Ticket 下的一次有限交付活动 | 每次有界目标或跟进可以产生新 Mission；同一 Ticket 同时最多一个活动 Mission |
| PlanRevision | Mission 的不可变计划版本 | 保存 DAG、Acceptance、Profile、依赖和哈希 |
| Job | DAG 中一个逻辑节点 | 具有稳定 node_id、依赖、Profile、指令和按角色定义的最终结果 |
| Assignment | 一个 Job 的一次实际执行尝试 | 每次获准的新执行创建 Assignment；必须绑定确切 Worker、Workspace、Run 和提交键 |
| Worker | Harness Runtime 中的执行资源绑定 | 保存 Session / Workspace 引用，不复制 Harness 的事件历史 |
| Artifact | 某次 Assignment 产生的冻结文件或 Git 交付 | 由 tree / commit / manifest / hash 标识，不用文本总结替代 |
| Delivery | Artifact 向依赖节点或 Integrator 的导入操作 | 有独立计划、目标、确认和冲突状态 |
| Evidence | 原 Harness 运行事实的引用 | 绑定 run_id、turn_id、tool_call_id、事件序号和内容哈希 |
| Review | 对冻结候选和 Acceptance 的一次独立复核 | 使用独立 Session / Workspace；结论必须引用当前轮 Evidence |
| Attention | 需要人作决定的问题 | 有状态、问题、可选动作、来源 Thread 和答复身份 |
| Effect | 需要在事务外执行的持久副作用 | 稳定幂等键、领取租约、尝试次数、回执和未知状态 |
| OutboxMessage | 对外发送意图 | 与 Ticket / Channel 关联，发送回执独立于模型报告 |
| AuditEvent | 不可变领域审计记录 | 记录状态转移、授权、提交、人工控制和未知结果 |

### 3.3 Ticket、Mission、Job 的关系（关键设计）

不要将 dsh 的 Ticket 直接替换成 Harness Mission，也不要继续让现有 Task 同时承担两者职责。

推荐关系：

- **Ticket = 为什么允许持续工作、工作范围是什么、哪些 Thread 属于这个工作。**
- **Mission = 这一次要交付什么、采用哪一版计划、何时算完成。**
- **Job = Mission 内的一个逻辑步骤。**
- **Assignment = Job 的第几次真实执行。**
- **Worker = 实际 Harness Session / Workspace 资源。**

一个 Ticket 可以先后拥有多个 Mission，但默认同一时刻只允许一个活动或尚未核实的 Mission。Ticket 结束后，若不是人工 Stop 或授权撤销，符合规则的人类后续消息可以在重新核验 Mandate 后恢复 Ticket，并创建新的 Mission。

普通问答可采用单节点报告计划，复杂任务采用多节点 DAG，两者都由同一 Scheduler 推进。代码或文件交付即使只有一个 work 节点，也由宿主增加 Integrator / Reviewer；不再以 Router / Autonomy 划分执行链路。

一个 Mission 的初期范围仍限定为一个 Git 仓库和一个冻结基线。跨仓库 Mission 暂缓，避免把多个 Git 交付组伪装成一个原子合并。

### 3.4 Subscription 与授权分离

Subscription 有两种 mode：

- **work**：Thread 的事件可作为 Ticket 输入；符合规则的人类后续消息可由 Ticket Worker 处理或交回。
- **context**：只提供上下文，不赋予恢复 Ticket、扩大 Mandate、写入或对外发言的权限。

owns_thread 是独立布尔属性；Thread 的 closed / merged / reopened 事实由 Thread 生命周期投影保存。

事件到达已订阅 Thread 时，可以 Fan-out 给多个 Ticket，但同一 Ticket 对同一 Trigger 只能生成一个 Input。owns_thread 是 Subscription 的独立布尔属性，不替代 work / context mode。Correlation 只增加相关上下文，不负责路由。

## 4. 总体分层与模块拆分

### 4.1 目标架构

~~~mermaid
flowchart TB
  subgraph Sources["Source Adapters"]
    GH[GitHub]
    FS[Feishu]
    LC[Local]
    EXT[Future Sources]
  end
  subgraph Domain["Domain / Authorization"]
    IN[Intake]
    POL[Policy + Gate]
    ENG[Evaluation + Ticket Engine]
    SRC[Source Registry]
  end
  subgraph Orchestration["Orchestration"]
    PLAN[Planner]
    SCH[Scheduler]
    CTRL[Control]
    WD[Watchdog]
  end
  subgraph Execution["Harness Execution Adapter"]
    WORK[Worker / Assignment]
    RT[Session / RunController]
    WT[Worktree / Tool Registry]
  end
  subgraph Delivery["Artifact / Quality"]
    ART[Artifact Capture]
    MERGE[Dependency Merge]
    INT[Integrator]
    REV[Evidence Reviewer]
  end
  subgraph Persistence["Coordinator Store"]
    DB[(superagent.sqlite3)]
    FX[Durable Effects / Outbox]
  end
  subgraph Product["API / Panel"]
    CLI[CLI]
    HTTP[HTTP API]
    UI[Management Panel]
  end
  GH & FS & LC & EXT --> SRC --> IN --> POL --> ENG
  ENG --> PLAN --> SCH --> WORK --> RT --> WT
  RT --> ART --> MERGE --> INT --> REV
  ENG & PLAN & SCH & CTRL & WD & ART & MERGE & REV --> DB
  DB --> FX
  FX --> Sources
  CLI & HTTP & UI --> ENG
  CLI & HTTP & UI --> CTRL
~~~

### 4.2 建议的包结构

以下包结构表达职责边界。实施时改造现有模块并按实际消费者抽离共用能力，不保留旧编排的兼容 Facade。

~~~text
harness/app/superagent/
  domain/
    model.py
    events.py
    policy.py
    transitions.py
    errors.py
  application/
    intake.py
    evaluation.py
    tickets.py
    missions.py
    scheduler.py
    controls.py
    queries.py
  ports/
    source.py
    assessor.py
    executor.py
    channel.py
    repositories.py
  sources/
    registry.py
    github/
    feishu/
    local/
  execution/
    harness_runtime.py
    workers.py
    assignments.py
    background.py
  artifacts/
    capture.py
    delivery.py
    merge.py
    evidence.py
    review.py
  persistence/
    sqlite.py
    schema.py
    repositories.py
    effects.py
    migrations.py
  runtime/
    service.py
    watchdog.py
    health.py
  api/
    cli.py
    http.py
    panel.py
~~~

### 4.3 模块职责与依赖规则

| 模块 | 职责 | 不允许承担的职责 |
|---|---|---|
| domain | 纯领域对象、状态转移、Policy 和 Mandate 规则 | HTTP、SQLite、Harness Session、网络 |
| intake | Trigger 去重、Thread 归属、Subscription Fan-out | 模型研判、Shell |
| evaluation | Gate、候选快照、Assessor 调用、Decision 提交 | 直接创建 Worktree 或发外部消息 |
| tickets | Ticket 创建、恢复、停止、Input 所有权和 Thread 订阅 | 复制原 Run / Tool Event |
| sources | SourceKind 注册、外部身份验证、事件规范化、Thread Context、Channel | 自行决定 Mandate |
| planner | 生成并验证 PlanRevision / DAG / Acceptance | 修改数据库状态、扩大 Profile 权限 |
| scheduler | Job 解锁、并发、优先级、租约、背压 | 直接调用外部 Source |
| execution | 把 Assignment 转成 Harness Session / Run / Workspace 操作 | 自行接受新业务授权 |
| artifacts | Capture、冻结、Merge、交付谱系 | 把自然语言摘要当成文件交付 |
| evidence / review | 原运行证据提取、独立复核和验收门禁 | 仅凭模型自述标记完成 |
| persistence | 权威协调账本、事务、CAS、迁移和 Durable Effects | 存储另一份可漂移的 Run 历史 |
| channels / outbox | 经授权的外部发送、回查、回执 | 从 Worker 原始输出直接广播 |
| controls / watchdog | Pause、Resume、Cancel、活性诊断 | 对 unknown 操作自动重放 |
| API / panel | 展示和人工控制 | 直接绕过 Application 层修改账本 |

依赖方向必须保持：Domain → Ports；Application → Domain + Ports；Adapters → Ports；Runtime 负责组装。Domain 不得反向依赖 GitHub、Feishu 或 Harness 的具体实现。

## 5. 持久化与数据表设计

### 5.1 数据库边界

目标方案为每个 Superagent Instance 使用独立的协调数据库：

**state_dir/superagent.sqlite3**

Coordinator Store 保存协作和授权事实、任务计划、执行意图、Effect、Outbox、控制投影和审计。Harness 原生数据库仍然是 Session、Run、Turn、Tool Event、Usage、Workspace 和 Background Job 的唯一事实来源。大附件和 Artifact Manifest 使用内容寻址存储，数据库保存引用、哈希、长度和媒体类型；凭据只保存 Secret Reference。

建议启用 SQLite WAL、外键和短事务。事务内只执行校验、CAS、AuditEvent 和 Effect 入队；模型、Harness、Git、文件系统和远端 Provider 调用都在事务外。初期一个 Instance 一个协调进程；多个 Instance 使用独立 state_dir。

### 5.2 规范化表分组

完整字段、主键、唯一约束和不变量统一维护在[领域模型与数据契约](superagent-domain-model.md)，本节只保留表分组总览，避免重复维护字段定义。

| 分组 | 主要表 | 责任 |
|---|---|---|
| Instance / Source | instances, source_instances, source_cursors | Instance 身份、来源配置、游标 |
| Event / Thread | triggers, trigger_threads, trigger_dispositions, trigger_disposition_events, threads | 不可变事件、Thread 关联和路由历史 |
| Channel | channels | 来源内写入端点 |
| Policy / Authorization | policy_versions, policy_rules, source_policies, evaluations, evaluation_triggers, decisions, mandates, mandate_events | 策略快照、研判和不可变授权 |
| Ticket | tickets, ticket_subscriptions, ticket_inputs, attentions | 长期工作、订阅、后续输入和人工待决 |
| Mission / DAG | missions, mission_plans, jobs, job_dependencies | 有界目标、不可变计划和依赖图 |
| Execution | workers, assignments | Harness 资源绑定和每次执行尝试 |
| Artifact / Quality | artifacts, deliveries, evidence, reviews | 真实文件交付、导入、原生证据和复核 |
| Side Effects | effects, outbox_messages | 持久副作用和外部消息回执 |
| Control / Audit | control_commands, mission_controls, watchdog_snapshots, audit_events | 控制、活性观察和追加式审计 |
| Infrastructure | idempotency_keys, schema_migrations | 稳定操作身份和数据库迁移版本 |

### 5.3 事务与索引原则

- SourceInstance + external_event_id 唯一标识 Trigger；SourceInstance + external_thread_id 唯一标识 Thread。
- 每个 Thread 最多一个活动 Evaluation；每个 Ticket 最多一个活动或未知 Mission；每个 Job 最多一个活动或未知 Assignment。
- Job Dependency 使用关系表，拒绝自依赖和环；只有 Job done 解锁下游。
- Assignment、Delivery、Effect、Outbox 和 Control 均使用稳定幂等键。
- unknown 保留原操作身份，不能由状态读取、租约过期或进程重启自动重放。
- 所有状态变更使用 Aggregate Revision / generation fencing；旧执行者的迟到回执不得覆盖新状态。
- 不把 Harness 原生 Run 数据搬入 Coordinator Store；跨账本一致性通过原提交键、原 Run 回查和显式 Reconciliation 实现。

## 6. 状态机设计

完整状态集合、转移守卫、重试矩阵和故障恢复规则统一维护在[状态机与合法转移](superagent-state-machines.md)。本节仅给出架构级摘要。

### 6.1 Intake、Trigger 与 Evaluation

- Intake Receipt：accepted / duplicate / conflict / rejected。
- Trigger 本体不可变；Disposition 通过追加事件记录过滤、Evaluation 排队、Ticket Fan-out、context-only、handback 和重新排队。
- Evaluation：queued → running → decided / unavailable / cancelled；同 Thread 同时最多一个活动 Evaluation。
- Decision：ignore / accept / reopen / stop / unavailable。
- Gate 在 Assessor 前执行；Assessor 只返回 Proposal，宿主核验 Policy Version、Thread Revision、原始输入和 Mandate Basis 后才提交 Decision。
- 模型评估期间新到的 Trigger 进入下一批，不改变当前 Candidate。

### 6.2 Mandate 与 Ticket

Mandate 是不可变授权快照；有效性由 valid / affected / revoked / expired 投影表示。Policy 更新不自动撤销全部活动工作，新的 Assignment、Ticket 恢复、Delivery 和 Outbox 发送前必须重新核验。

Ticket 持久化状态只有 open / ended。终结 outcome 为 completed / merged / closed / dropped / stopped / revoked / failed。只有来自 Work Subscription、未被人工停止且 Mandate 仍有效的后续人类消息才可能恢复 Ticket；扩大 Scope 必须重新授权。若 Ticket 跟随 PR / Issue，own Thread 的关闭事实和 Worker ready / dropped 报告共同决定终结时点；Thread 先关闭而工作未完成时，Ticket 保持 open，等待后续完成报告。

UI Stage 是只读投影，不与 Ticket state 混用。建议包含 queued、working、awaiting_human、awaiting_review、blocked、failed、idle、unknown、ended；failed 对应 Ticket 已结束且 outcome=failed。

### 6.3 Mission、Job 与 Assignment

Mission 状态：

- 活动阶段：planning、running、integrating、reviewing、cancelling。
- 自动调度终态：completed、blocked、rejected、suppressed、cancelled。
- unknown：调度静止、等待原操作核实；不是可自动重试状态。只有 Reconciliation 取得确切证据后，才允许恢复原阶段或确认终态。

Mission 不单独存储 awaiting_human；人工等待由 Attention 和 Mission blocked 投影表达。人类答复改变目标时，创建新的 Mission，不覆盖已终结计划和证据。

Job 状态：pending、ready、running、checkpointed、done、blocked、unknown、cancelled。importing 属于 Delivery；submitting / accepted / capturing 属于 Assignment，不混入 Job 状态。依赖 Job done 是必要条件；requires_delivery=true 的依赖边还必须对应 Delivery applied 并核验目标树后，下游才可 ready；checkpointed 不解锁。

Assignment 状态：pending、submitting、accepted、running、capturing、stopping、succeeded、failed、interrupted、rejected、unknown、cancelled。获准的新执行、Checkpoint 接续和有界修正创建新 Assignment；原操作回查沿用原 Assignment / submission_key / run_id，不能因失败、超时或 unknown 自动创建替代 Run。

### 6.4 Delivery、Effect 与 Outbox

- Delivery：planned → applying → applied / conflict / unknown / cancelled。
- Effect：pending → leased → completed / retry_wait / failed / unknown / cancelled。
- OutboxMessage：pending → sending → sent / retry_wait / dropped / unknown；未发送前可 withdrawn。
- 只有 Provider 幂等能力和原操作回查证明安全时，才允许按原 key 重试。不能确认的发送、导入和执行均保持 unknown。

### 6.5 Control 与 Watchdog

ControlCommand：queued → applying → applied / rejected / unknown。MissionControl：running / paused / cancelling / cancelled。

- pause 停止新派工，不撤销已接受的运行。
- resume 重新核验 Mandate 并增加 control generation。
- cancel 先阻止新派工，再停止确切 Run 和所属 Background Job；所有原操作确切收尾后才标记 cancelled。
- WatchdogSnapshot 状态为 healthy / stalled / blocked；attention 是产生的人工待决，不是 Watchdog 状态。Watchdog 不重放命令、不自动创建 Assignment、不凭没有文本输出认定失败。

## 7. 关键调用时序

### 7.1 外部事件接收与授权

~~~mermaid
sequenceDiagram
  participant S as Source Adapter
  participant D as Domain Engine
  participant DB as Coordinator Store
  participant A as Assessor
  participant FX as Durable Effects

  S->>S: 验签、租户校验、身份规范化
  S->>D: receive(Trigger)
  D->>DB: BEGIN
  D->>DB: 去重、保存 Trigger / Thread 关系
  D->>DB: Subscription Fan-out / Eval 入队
  D->>DB: AuditEvent + Effect
  D->>DB: COMMIT
  D-->>S: 稳定接收回执
  FX->>DB: 领取 Eval Effect
  FX->>D: 构造固定 Candidate
  D->>A: Gate 通过后请求语义评估
  A-->>D: Decision Proposal
  D->>DB: 核对 Policy / Thread Revision
  D->>DB: Decision + Mandate + Ticket + Mission + Effects
  D->>DB: COMMIT
~~~

注意：模型只返回 Decision Proposal。宿主必须重新验证规则命中、Mandate 范围、Profile、Source Revision 和幂等身份，之后才创建 Ticket / Mission。

### 7.2 Mission 执行与真实文件交接

~~~mermaid
sequenceDiagram
  participant Q as Scheduler
  participant DB as Coordinator Store
  participant H as Harness Adapter
  participant W as Worker Session
  participant I as Integrator
  participant R as Reviewer

  Q->>DB: 原子认领 Ready Job / Assignment
  Q->>DB: 写 submission_key
  Q->>DB: COMMIT
  Q->>H: ensure Worker / Workspace
  H->>W: submit with stable key
  W-->>H: accepted + run_id
  H->>DB: CAS 保存原 Run 引用
  W-->>H: run settled
  H->>H: 核对工具配对、后台作业和退出事实
  H->>DB: Capture Artifact + Evidence
  Q->>H: plan / apply 原 Artifact Merge
  H-->>DB: applied tree / conflict / unknown
  Q->>DB: 仅在 applied tree 核验后解锁依赖 Job
  Q->>I: 所有工作节点完成后集成
  I-->>DB: 集成 Artifact
  Q->>R: 独立 Session / Workspace 复核
  R-->>DB: Verdict + Evidence References
  Q->>DB: 确认 Acceptance 并完成 Mission
~~~

### 7.3 人类跟进与 Ticket 恢复

1. Source Adapter 验证真实发送者和 Thread。
2. Intake 根据 Ticket Subscription 找到所有目标 Ticket。
3. 对每个 Ticket 写入唯一 Input；context Subscription 只添加上下文。
4. 宿主先重新核验 Mandate、work Subscription 和 Control Mode；若存在可接收输入的活动 Assignment，通过 Executor.deliver_input 按稳定 operation key 投递，否则保留 Input，下一次 Assignment submit 时按序携带；不再为同一条消息重复启动 Eval。
5. 已结束但非人工 Stop 的 Ticket，先重新核验 Mandate 和当前 Policy，再决定是否恢复。
6. Worker 对超范围请求给出结构化 handback，保留原 Trigger 和原因。
7. 只有无人拥有该 Trigger 时才进入新 Evaluation，防止循环路由。
8. 新授权范围必须创建新的 Mandate / Ticket，不允许 Worker 自行扩大原 Mandate。

### 7.4 重启与未知操作恢复

启动时按顺序执行：

1. 获得 state_dir 进程锁，验证数据库 Schema 和 Instance Identity。
2. 读取活动 Effect、Assignment、Delivery、Outbox 和 Control。
3. 通过 Harness 原生提交键和确切 run_id 回查原操作。
4. 已接受的运行继续观察，不创建第二个 Run。
5. 已完成的 Artifact / Delivery 只核对原树，不重复导入。
6. 不能确认的记录保留 unknown，并出现在人工核实队列。
7. Source 按持久 cursor 补拉事件；重复事件由唯一身份去重。
8. 仅在确认没有原操作或原操作明确未启动时，才允许新 Assignment。

## 8. 关键接口契约

建议以 Protocol / ABC 定义边界，不让 Engine 直接依赖具体 SDK：

| 接口 | 核心方法 | 责任 |
|---|---|---|
| SourceAdapter | validate_config / verify_and_normalize / poll / read_context / read_thread_lifecycle / channel_for / health | 外部协议与来源事实 |
| SourceRegistry（内部组件） | register_builtin / resolve_kind / get_instance | 静态来源实现注册与 SourceInstance 配置解析；不是外部副作用 Port |
| PolicyProvider | snapshot / gate / revalidate | 确定性授权 |
| Assessor | evaluate(candidate) | 只给出语义决策提案 |
| Repository / UnitOfWork | atomic / typed get / insert / compare_and_swap | 协调账本事务 |
| Executor | ensure_worker / submit / deliver_input / inspect / stop / reconcile / reconcile_input | Harness Runtime 适配；提交和输入投递均使用稳定 operation key |
| ArtifactStore | capture / inspect / plan_merge / apply_merge / reconcile_merge | 真实文件交接 |
| EvidenceProvider | run_snapshot / tool_invocations / background_status | 原生运行证据 |
| Channel | send / reconcile_send | Outbox 外部消息交付 |
| EffectQueue | enqueue / claim / ack / retry / mark_unknown | Durable Effects |

所有外部调用都应接收稳定 operation_key，并返回明确的 accepted / rejected / unknown 等回执。request_id 只是请求关联身份，不能替代 Harness 原生 Run Submission Key。

## 9. 安全、授权与可靠性要求

### 9.1 授权默认值

- 默认只接受明确的人类请求。
- inferred 候选先进入 observe，不自动执行。
- auto 必须由维护者对具体 Source、Admission Row 和 Rule 显式开启。
- Bot 和 CI 可以提供事实，不得升级成显式人类授权。
- 不把引用、转发或第三方正文中的命令当作原发送者的权限。
- Mandate 在 Ticket 恢复、Job 提交、Artifact 导入和 Outbox 发送前重新校验。

### 9.2 工具权限与 Sandbox

Harness 负责 Assessor / Planner 的无工具研判、Worker Profile 白名单、Integrator / Reviewer 独立资源和工具执行前复核。

但这些都不是 OS Sandbox。正式允许不可信外部请求执行写操作前，必须增加独立执行隔离：

- 容器或轻量 VM；
- 独立 OS 身份和只读基础镜像；
- Workspace 显式挂载；
- 网络 egress 白名单；
- 短期凭证代理和 Secret Broker；
- 禁止读取宿主用户目录及其他项目凭证；
- 资源、进程、时间和磁盘配额；
- 可审计的命令、文件和网络操作。

Docker 的默认配置不自动等于安全隔离；Sandbox 必须按实际挂载、权限、网络和进程逃逸边界验证。

### 9.3 Exactly-once 与未知状态

外部模型提交、Git 操作和消息发送通常无法与本地 SQLite 事务原子提交。因此不能承诺普遍 exactly-once。

采用：

- 本地业务意图 exactly-once（唯一键 + 事务）；
- 外部操作 at-least-once only when provider idempotency / reconciliation proves safe；
- 无法确认时 unknown；
- 不以超时、连接断开或重启自动重放；
- 每个状态变更追加 AuditEvent；
- Effect Lease 使用 generation / owner fencing，旧 Worker 的迟到回执不得覆盖新状态。

### 9.4 容量与公平性

- 每个 Instance 有最大活动 Mission、Worker 和并发 Run 限额。
- 每个 Repo 有独立并发限制。
- 同一 Ticket 的人类跟进优先于新 Mission。
- 不同 Mission 采用轮转和有限批次，慢节点不阻塞其他目标。
- Effect Queue 有背压、最大重试和 Dead / Unknown 呈现。
- 所有列表 API 有分页和硬上限。
- 长期数据有显式归档策略；不能静默删除未确认或仍有审计价值的事实。

## 10. 能力的唯一归属与数据边界

下表规定功能进入哪一层，不是旧数据库导入映射，也不允许保留两套实现长期并行：

| 能力 | 唯一归属 | 接线要求 |
|---|---|---|
| 来源接收、事件去重、Thread 和订阅 | SourceRegistry / Authorization / TicketInput | 统一 Trigger、Gate、Mandate 和 work / context 边界 |
| 语义研判与任务规划 | 原生 Assessor / Planner + PlanRevision | 模型只提议，宿主校验、物化系统角色并封存 |
| 单节点及多节点派工 | Scheduler / Job / Assignment | 统一依赖、容量、公平和原提交键回查 |
| Session、Run 和执行资源 | HarnessExecutorAdapter / Harness Runtime | 独立 Worker 绑定，原生事件是唯一执行事实 |
| 文件捕获、依赖导入和质量门 | CoordinatorArtifacts / Delivery / Review | 真实冻结文件、原操作回执及独立复核 |
| 暂停、继续和取消 | CoordinatorControl + 原生 Run / Background 停止 | 控制意图和实际收尾分开，不实例化旧控制账本 |
| 节点指令和阶段接续 | MissionInstruction / InputDelivery / Assignment | 绑定确切 Run / Turn，原生接受与应用分别核验 |
| 活性监督 | WatchdogSnapshot | 同一协调账本，不以沉默重派 |
| 回复候选、发送和回执 | Report / Channel / Outbox / Effect | Candidate 不等于 Sent，unknown 只回查 |
| 状态和统一工作台 | 统一 Application 查询与控制接口 | 只读投影不驱动执行，不另建编排后台 |

每个 Instance 只使用一个权威协调 SQLite；Harness Home 保留原始执行事实，Manifest 和附件按既定资源边界保存。新服务不要求提供旧 state_dir，不初始化旧业务账本，不导入旧任务，不补造历史授权。旧目录若被发现只执行拒绝混用和保留原件，不做隐式迁移、归档或删除。

## 11. 分阶段实施计划

### 11.1 开发起点与执行顺序

Phase 0–3 是已经形成的领域和基础能力背景，不再作为待执行计划。沿现有 CoordinatorStore、CoordinatorExecutionService、HarnessExecutorAdapter、CoordinatorArtifacts 和共用组件继续开发，改造现有 SuperagentService 作为唯一外层装配，不创建第二个长期服务。

| 阶段 | 接续方式 |
|---|---|
| Phase 1：协调账本 | 复用事务、CAS、审计、Effect / Outbox 和现有迁移校验，不重新建立另一套数据库 |
| Phase 2：来源与授权 | 复用 SourceRegistry、Gate、Policy、Mandate、Ticket 和原始来源核验，不再新增旧 Inbox / Router 分发 |
| Phase 3：原生单 Worker | 复用原 Run、Report、Attention / Handback 和执行证据；生产整合未完成的部分纳入 Phase 4 |
| Phase 4：统一整合 | 4.1 → 4.6 已接通并取得本机离线定向证据；保留协议与业务回归 |
| Phase 5 / 6：扩展 | 仅扩展 Phase 4 完成后的同一系统，不能承担延后的核心整合或旧编排退役 |

后续开发先核对领域模型第 9 节与当前源码，从已完成的 Phase 5 接续 Phase 6；已接通的独立输入投递、作用域守卫、资源归属、系统节点封存和文件适配继续复用。不得把“已有表和适配器”直接标记为阶段完成。每次交付同步更新已核验的实现边界、相关正式文档及回归。

### Phase 4：完整 Superagent 整合与替换

唯一目标链路：**Source → Gate / Evaluation → Ticket / Mandate → Mission / PlanRevision → Scheduler → Assignment → Harness Runtime → Report / Artifact / Review → Outbox。**

#### Phase 4.1：整合契约与协调模型

- 沿现有统一账本补齐 Worker 的 Mission / Job / stage 绑定、独立 InputDelivery，以及 Artifact / Delivery / Review、控制、指令和 Watchdog 的约束与状态转移。
- 输入投递消费不再依赖 TicketInput 的单 Assignment 字段判断所有节点是否已读取；每条 Input / Assignment 投递使用自己的稳定身份、原生回执与 CAS，禁止跨 Ticket 或节点冒用。 当前已由 CoordinatorAssignmentInputDelivery 提供显式 Host API，并由统一 Scheduler 与持续协作入口实际消费，覆盖多 Assignment、丢回执恢复、Handback 后回查、畸形回执、原 Run 冲突和并发单调性；生产默认入口已在 Phase 4.6 切换。
- Planner 只提出 work DAG；宿主按允许的多角色 Profile 集确定性建立 Integrator / Reviewer 后整体封存。Assignment 的接续和修正不能覆盖旧 Run，不能改写冻结计划。
- 保持普通 Repository 更新不能绕过状态机；新增结构使用新迁移，不改写已有迁移校验和。Schema v16 必须在数据库层守住 InputDelivery / MissionInstruction / Delivery 状态与 Revision 单调、DAG 交付作用域、独立 Review 身份、MissionControl 取消终态、终态控制回执及 Watchdog 进度单调性。

**出口：** 相同 Profile 多节点及多阶段资源隔离、完整计划不可变、跨作用域拒绝、输入逐执行消费和未知投递不重放均有针对性证据。InputDelivery 已有显式 Host API 和服务级回归；Schema v16 已补充数据库级状态、Revision、回执、DAG 交付、独立 Review、MissionControl 与 Watchdog 守卫。2026-11-06，Coordinator Store、Unified Artifacts、Unified Execution、原 Execution 和 Unified Model 五个测试模块共 112 项全部通过，Phase 4.1 的统一协调契约与持久化状态模型出口完成。此结论仅限模型与约束层；Phase 4.2 起的统一服务生命周期、Scheduler 消费及生产路由仍按后续阶段验收。

#### Phase 4.2：唯一服务生命周期与持久控制

- 改造唯一 SuperagentService，组合 CoordinatorHost、来源、原生 Assessor / Planner、Executor、Effect 和恢复循环；CoordinatorHost 不再强制依赖 legacy_state_dir 或装配生产 Shadow。Phase 4.6 已移除 legacy_state_dir 参数和 Shadow API；旧运行目录冲突检查继续拒绝混用并保留原件。
- 唯一 `SuperagentService.open_coordinator` 组合 CoordinatorHost、SourceRegistry、原生 Assessor / Planner、Executor、EffectRecovery 与持久控制；默认不构造 V1 Shadow。统一 `tick` 先恢复控制、轮转评估 Thread，再从账本重读 waiting TicketInput 路由，回查活动 Assignment、Mission 和 Watchdog，最后执行有界 Effect Recovery。
- `pending_routes`、`active_mission_states` 均为有界只读清单。暂停或重启后未完成的 TicketInput 保留原 Ticket / Trigger 身份，恢复时复用 `route_trigger` 幂等路径；控制、路由、Assignment、Mission 和 Effect 的批次背压进入 `serve` 状态。控制、Assignment 与 Mission 恢复按持久 `WatchdogSnapshot.observed_at` 轮转，优先处理从未观察或最久未观察的项目，避免未知旧任务永久占满前 100 条。
- Effect Recovery 始终由统一服务装配。未配置 Provider Adapter 时明确记录 `lookup_unavailable` 并保留 `unknown`，不把缺少适配器当作未发生，也不重放外部副作用；重复相同观测不重复追加审计事件。
- 提供统一 `tick / serve / aclose`。服务拥有 Tick 与关闭任务，Host 拥有账本、Executor 和实例锁；关闭停止新准入、等待已接受 Tick 和 Host 操作收尾，重复关闭共享同一关闭任务，原始运行错误不会被清理错误覆盖。
- 持久控制覆盖资源创建、提交、文件导入与捕获、最终完成和 Outbox Candidate。Pause 阻止新派工及后续写入；Resume 重新核验 Mandate；Cancel 只停止确切原 Run 和其所属后台作业。已受理、实际停止、待停止与未知副作用分别记录。
- 只读状态、MissionControl 回执和 Effect 查询不补建资源、不推进运行、不触发恢复写入；取消完成后不再反复扫描终态 Mission，避免历史取消挤占后续控制恢复批次。

**出口状态（2026-11-06）：** Phase 4.2 功能实现与定向回归已完成；暂停／恢复、授权撤销、取消、TicketInput 恢复、进程重启、未知结果不重放和关闭收尾均有确定性回归及实际回执依据。最终 Harness 验证证据仍待运行中的 MCP 服务重载已同步的指纹运行时后生成；在此之前不将提交门禁标记为通过。此出口不代表 Phase 4.3 Scheduler、Phase 4.4 独立 Review 或生产默认路由已切换。

#### Phase 4.3：统一 DAG 调度与真实交付

- 单节点与多节点共用一个 Scheduler 和 Assignment 提交／恢复机制，提供依赖解锁、有限并发、公平轮转、背压和资源预算。
- Mission 创建时冻结单仓库基线，节点使用独立 Session / Workspace；源 HEAD 后续变化不改变该基线。
- 将已有 CoordinatorArtifacts 接入调度消费：原 Run、工具结果和所属后台写入均确认收尾后才冻结产物，逐条所需 Delivery 确认 applied 后才放行下游。
- Capture / Import / Submit 使用持久稳定身份；丢回执时仅回查原操作。冲突保留原件，容量耗尽明确等待或关注，不隐式清理历史资源。

**出口状态（2026-11-07）：已完成。** 同一 CoordinatorDagScheduler 已通过真实 Harness Worktree 端到端回归：Mission 冻结基线后源 HEAD 前进，两个独立节点分别生成二进制与删除文件，Join 仅在两份 Delivery applied 后启动；注入 Import 写入成功但回执丢失，后续 Tick 只核对原 Worktree 回执，不再次导入；另有慢任务并发、公平背压、冲突、后台收尾、Submit ACK 丢失、事务回滚与 unknown 不重放回归。对应证据见 `docs/superagent-review.md` Phase 4.3 表。Phase 4.3 完成不代表 Phase 4.4 质量门、生产 CLI、生产 Sender 或生产默认路由切换完成。

#### Phase 4.4：质量门、阶段接续与有限修正

- 宿主冻结完成契约：普通问答只允许只读报告完成；代码和文件交付即使只有一个 work 节点，也必须冻结 Artifact 并独立 Review。类型不明时采用严格契约，Worker 不得自行降级。
- Integrator 生成冻结候选，Reviewer 使用独立 Session / Workspace，按冻结验收标准逐项引用本轮真实证据。运行成功不等于验收通过。
- 有界修正使用新的 Assignment，保留旧候选与结论，每次 Review 使用新资源。Checkpoint 使用同一逻辑 Job、新 Assignment 和新执行上下文，最终阶段完成前不解锁下游。
- Watchdog 基于真实进度生成关注或安全阻塞；完成与发布前重新核验 Mandate、控制代次和全部交付证据。完成 Mission 不授予合并、部署或发布权限。

**出口状态（2026-11-08）：已完成统一内部链路的功能与定向验收。** 单节点产物任务与多节点 DAG 均经过冻结 Work Artifact、宿主 Integrator、独立 Session / Workspace 的 Reviewer 及持久 Verdict；失败 Review 只在冻结预算内创建新的修正 Assignment，旧候选和历史判定保留。原操作未知、候选改写、伪造／过期证据、撤权、暂停及 Watchdog blocked 均不能产生通过结论或交付。

Work 阶段接续已进入相同 Scheduler：冻结阶段报告及累计文件，使用同一逻辑 Job 下的新执行资源，只有最终 ready 才解锁下游；宿主阶段预算、异常、循环停滞及 unknown 不重放均有定向证据。完整协议和使用边界统一见 [Superagent 模式](superagent.md#授权工作与独立质量门)。

Watchdog 接收的原生日志时间在执行观察边界从 Unix 毫秒规范为 UTC 文本，原始 Evidence 保持原值，避免真实 Run 被误判为不可信活动。真实 Harness / Git 回归 `test_staged_work_survives_restart_and_reaches_independent_quality_gate` 已完成两阶段文件交接、Host 重启、受控时钟跨越 24 小时的监督检查失败与监督器重建、持续阻断、原 Reviewer Run 新事件恢复以及独立 Review passed → Mission completed；`test_failed_review_runs_one_fresh_repair_round_and_completes_quality_gate` 保留失败复核后的有限修正闭环。阶段异常、预算、上下文语言、逐边交付及不重放的针对性回归见 `tests/test_superagent_unified_checkpoints.py`。受控时钟故障注入不等于真实 24 小时负载或在线模型质量验收；这些持续评估风险保留在审查记录。生产信道发送归 Phase 4.5，CLI 切换和旧编排退役归 Phase 4.6，不作为本阶段延后的开发项。

**出口：** 单 work 节点代码任务也经过完整质量门；伪造证据、候选被改写、阶段预算耗尽或原操作未知均不能产生完成与交付。

#### Phase 4.5：持续协作与既有信道

- 普通跟进进入 TicketInput，节点指令绑定 Mission / Job / Assignment / 原 Run / Turn。统一协调 Host 已提供显式 MissionInstruction API：持久化稳定 request_id、payload hash 与 Revision；在原生接受锁内复核 Mandate / Mission；accepted 与 applied 分开记录，unknown 只回查原 request_id，不重放到新 Run。无法唯一定位目标时产生 Attention，不默认广播全部 Worker。
- 接通 Attention、Handback、原生指令接受／应用回执和阶段间有界上下文；context-only 不启动执行，不扩大授权，迟到结果不进入新运行。
- GitHub / Feishu 以 Source / Channel Adapter 接入统一 Trigger、Cursor、正文解析、Outbox Sender 和周期性回查，不保留旧信道的独立业务账本或依赖旧 Results 获取候选。
- Candidate、pending、sent、unknown 严格区分；未找到、重复匹配或分页不完整均不能授权重发。文件仅承担输入或投影职责。

**出口状态（2026-11-08）：已完成统一服务范围的开发与离线定向验收。** 阶段间已应用指令上下文、固定目标的单／多节点跟进、Attention / Handback、原生指令周期回查、Source 接收与 Cursor、Channel 回复绑定、冻结结果正文、Outbox Sender 及只读恢复均接入同一服务。真实 Harness / Git 产物经独立质量门后只发布一次；受控 GitHub／飞书验证重复事件、跟进 ACK 丢失、发送后丢响应、重启、撤权与关闭不会重复执行或发送。实际 HTTP 边界覆盖 GitHub POST→GET 确认及飞书令牌刷新后的撤权检查。完整行为见 [Superagent 模式](superagent.md#授权工作与独立质量门)，触发条件、回归与未验证环境见 [协调审查](superagent-review.md#phase-45持续协作与既有信道)。生产 CLI 的切换及退役已在 Phase 4.6 完成；本阶段没有取得真实外部账号、在线模型或长期负载验收。

**出口：** 本地、GitHub 和飞书通过统一服务完成授权、工作、跟进和结果交付；受控 Provider 验证重复事件、发送后丢响应、重启及撤权不会重复执行或发送。

#### Phase 4.6：唯一入口收口与旧编排清除

**当前状态（2026-11-08）：已完成开发与本机离线定向验收。** 唯一 `SuperagentService.open` 接收当前统一配置，构造无工具 Assessor / Planner 和受限原生 Executor；提交、状态、节点指令、控制与回执全部使用同一协调账本。旧编排模块、模式选择器、独立控制／信道账本消费者、Shadow 和旧来源映射 API 已删除。历史 Coordinator 迁移保持校验和，新增 Schema v21 为模型调用与本机请求固化身份；旧运行目录保留原件并拒绝混用。

真实 CLI 子进程以同一请求运行两次，只执行和发布一次；原生文件任务经过 Work → Integrator → 独立 Reviewer → 冻结结果。其余针对性证据覆盖本机并发、原 Run / Turn 指令、暂停恢复、未知模型提交、读操作无副作用、实例锁、关闭准入、真实 Webhook / HTTP、语言消费及正常 Schema 升级／冲突回滚。具体触发条件和旧回归迁接对应关系见[审查记录](superagent-review.md#phase-46入口收口与旧编排退役的当前证据)。未运行全量回归、生产构建、桌面／浏览器专项，也未访问在线模型或外部账号。
- 将 CLI 提交、状态、引导、控制和回执全部接到统一 Application 服务；autonomy 不再选择不同协调器，配置只支持当前统一格式。
- 核对实际调用方、Worker 动态注册和绑定、资源分发、脚本、示例及有效回归，删除残余旧 Router、Dispatcher、MissionStore、Worker / Results 编排和独立控制账本。
- 替换“必须启动旧版”“必须维持旧默认路由”等过渡架构断言；将有效业务断言迁接到统一实现，禁止为删除旧模块而直接删掉业务回归。
- 全局确认没有 V1 / V2 双服务、旧版执行选择、兼容回退、生产 Shadow 及其专用来源映射 API、旧目录必填依赖，并同步清除正式文档中的过期使用方式。

**Phase 4 总出口：** 只有一个产品入口、一个权威协调账本、一套协调状态机及调度／执行／恢复链；既有有效业务能力均在统一链路取得对应证据。任一旧编排仍可被生产入口选择，或仍依靠旧链路完成业务，Phase 4 就没有完成。

### Phase 5：统一系统上的产品协作完善

在同一 Application 服务上增加工作台、分页详情、人工未知结果核实、来源和策略管理、更完整的 GitHub 生命周期及飞书媒体支持。既有信道、基础控制、结果交付和唯一入口已在 Phase 4 闭环。

**当前状态：5.1–5.6 开发完成，已取得本机定向回归证据。** 工作台仍复用统一服务与协调账本；没有进行生产构建、浏览器／桌面专项或外部账号上线验收。

| 子阶段 | 范围与出口 | 当前状态 |
|---|---|---|
| 5.1 分页查询与任务详情 | 同库查询端口、完整历史翻页、Mission 详情及 Job / Assignment / Artifact / Review 摘要，CLI 实际消费；不驱动执行、恢复或迁移 | 已完成本机定向验收 |
| 5.2 统一工作台 | 消费相同查询／控制端口，展示目标、执行、交付与待关注状态；处理分页、隐藏和异步实例隔离 | 已完成本机定向验证 |
| 5.3 人工未知结果核实 | 显示原副作用和可核验证据，显式核实授权与回执；不能以人工点击盲目重放未知写入 | 已完成本机定向验证 |
| 5.4 来源和策略管理 | 来源启停、策略修订、授权影响与审计，沿用现有 Mandate 重验 | 已完成本机定向验证 |
| 5.5 GitHub 生命周期 | 在原来源模型扩展 Review / CI 等生命周期，保持事件、权限和原回复目标隔离 | 已完成本机定向验证 |
| 5.6 飞书媒体 | 媒体接收、资源生命周期与呈现，保持原消息及账号身份核验 | 已完成本机定向验证 |

Phase 5 的当前业务行为与限制统一见[产品页](superagent.md#统一工作台)，触发条件和针对性证据见[审查记录](superagent-review.md#phase-52至56统一工作台与来源协作)。完成范围是可信本机工作台、原操作只读核实及上述来源能力，不包含远端管理、多用户审批、任意 GitHub 事件或模型多媒体理解。

### Phase 6：统一执行边界上的隔离与远程 Worker

Native Runner、可选 Sandbox Runner、Secret Broker、Device Identity / Worker Lease、远程回执、断线恢复及多设备调度实现同一 Executor Port，沿用同一任务、授权和恢复语义，不另建远程版 Superagent。

**当前状态：支持 1–32 台设备；单台无 Docker 的原生执行已完成开发并取得真实 CLI 闭环证据。** 单台设备可以与协调器位于同一电脑。设备数量及执行方式由显式配置决定，不把两台物理设备、Docker、Rootless 或 cgroup 作为原生路径的必需条件。设备容量在创建 Worker 前准入，满载任务保持待执行，释放容量后继续。

| 子阶段 | 当前实现与证据 | 按所选能力单独验收 |
|---|---|---|
| 6.1 执行方式 | 显式 native 可信宿主或 docker 隔离；原生不探测 Docker／Unix Socket，Docker 不自动降级；真实原生 CLI 闭环 | Docker 镜像、Rootless UID／进程／文件／网络越权及实际配额 |
| 6.2 Secret Broker | 共享原 Mission／模型／预算能力，原生进程内传输、容器 Unix Socket；单台 CLI 消费固定模型端点 | Docker 凭据隔离及在线模型提供方 |
| 6.3 Device Identity / Worker Lease | 1–32 台不可变登记，握手固定 runner 及实际约束；固定原设备与代次，撤销和迟到 fencing | 远端 TLS／时钟部署；原生设备不能冒充 Docker |
| 6.4 远程 Executor 与回执 | 同一 Port、签名帧、pending／completed；原生文件质量门和统一 Outbox | 真实跨主机网络边界 |
| 6.5 断线与关闭恢复 | 原 Run／工作树回查，未知不重放；真实单设备 CLI 关闭及重启后仅查回原 Run | 异机断网、跨平台及异常杀进程专项 |
| 6.6 设备调度与工作台 | 单设备满载等待及继续，两个逻辑设备分配，CLI／API／组件设备和租约查询 | 物理多机与长期负载仅针对相应场景，不阻塞单设备原生使用 |

验收按配置执行：原生单设备以真实原生入口、授权、原回执、交付、收尾和恢复为依据；选择 Docker 时再核对真实隔离与配额；选择多设备时再验证相应调度与网络故障。当前没有 Docker 实机、物理双机和跨平台专项证据，不能将其记作通过，也不能把原生执行称为 OS 沙箱。部署、精确配置与限制只维护在[产品页](superagent.md#隔离与远程-worker)，源码与针对性证据见[审查记录](superagent-review.md#phase-6单设备与无-docker-原生入口)。

旧版迁移与退役的独立 Phase 7 已取消，不得恢复为新的排期；旧编排清除是 Phase 4 的必需出口。

## 12. 质量门禁与可量化指标

每一阶段必须保留确定性业务回归，不以“模型回答成功”作为验收。默认开发只执行本次新增或修改的针对性用例，优先精确到测试方法；纯文档修改只核对内容和差异。下表是各能力的覆盖要求，不是每次交付的全量必跑清单；聚合回归、完整构建及桌面／浏览器专项按仓库规则取得对应明确授权后执行。

| 类别 | 必测内容 |
|---|---|
| 领域一致性 | 状态转移、Mandate 不可变、Ticket / Mission 关系、Subscription Fan-out |
| 幂等性 | 重复 Webhook、重复 CLI 提交、相同 key 不同正文冲突 |
| 授权 | explicit / inferred、observe / auto、Bot、来源撤销、规则更新 |
| 调度 | DAG 环、依赖解锁、并发上限、慢任务公平性和背压 |
| 执行 | Run 提交、原 Run 回查、后台作业、停止与关闭收尾 |
| 交付 | 未提交文件、删除文件、Merge 冲突、基线冻结和未知导入 |
| 证据 | 唯一 Tool Call 配对、原始结果、命令退出、Reviewer 候选不变 |
| 消息 | Channel 不可用、发送超时、发送后丢响应、Provider 回查 |
| 恢复 | 每个外部副作用前后崩溃、数据库写入失败、迟到回执 |
| 安全 | 越权工具、来源伪造、跨实例读取、凭证泄露和 Sandbox 边界 |
| 运营 | 账本增长、归档、健康状态、人工待决、并发和成本 |

正式负载报告记录：

- Trigger 接收确认延迟 p50 / p95；
- Gate / Evaluation 决策延迟；
- Ticket / Mission 排队时间；
- Assignment 启动延迟；
- 端到端交付时间 p50 / p95；
- 重复 Run / 重复发送数量；
- 遗漏事件数量；
- unknown 数量及人工处理时间；
- 模型请求、Token、工具耗时和每个完成任务成本；
- 自动完成率、人工介入率和复核失败率。

性能比较必须固定模型、语料、仓库、工具、并发和重复次数。不能以减少测试、缩短验证范围或模拟耗时来宣称优于其他系统。

## 13. 关键风险和待决策事项

| 风险 / 决策 | 建议 |
|---|---|
| Ticket 与 Mission 的生命周期容易混淆 | 固定 Ticket 长期授权、Mission 有界交付的定义 |
| 中央 SQLite 写入竞争 | 初期每 Instance 独立；短事务、WAL、批量读取；测量后再考虑 PostgreSQL |
| 多数据库之间没有原子事务 | 只把协调事实放进中央账本；Harness 原运行仍用原提交键回查 |
| 多仓库 DAG 合并复杂 | 初期一个 Mission 一个仓库；跨仓库任务显式拆成多个 Mission |
| 同模型 Reviewer 可能有共同盲点 | Session / Workspace 独立是最低要求；关键任务增加独立模型或人工复核 |
| 自动接受推断请求的风险 | 默认 observe；先建立人工核实和完整审计，再开放 auto |
| 工具白名单不是 Sandbox | 对不可信外部写任务，在 Sandbox 完成前禁止宣称生产级隔离 |
| PR 合并与部署具有高风险 | 与 Mission completed 分离，使用独立授权和独立回执 |
| 历史数据与运行容量持续增长 | 提供显式归档和恢复，不隐式删除 |
| 动态 Source Plugin 的兼容性 | SourceKind 有版本、能力声明和契约测试；缺失插件的 Source 保留为 disabled / unavailable |

## 14. 最终架构决策摘要

| 决策 | 结论 |
|---|---|
| 执行内核 | 继续使用 Harness Runtime |
| 领域模型 | 采用 Source / Trigger / Thread / Policy / Mandate / Ticket |
| 自主规划 | 保留 Harness Mission / Job DAG |
| 简单与复杂任务 | 统一为单节点或多节点 Mission |
| 执行实例 | Assignment 绑定 Harness Session / Workspace / Run |
| 文件交接 | 复用真实 Artifact Capture / Delivery / Merge |
| 质量验证 | 独立 Reviewer + 宿主 Evidence 核验 |
| 持久化 | 新建每 Instance 一个权威协调 SQLite |
| 副作用 | Transactional Effects + Outbox + unknown |
| Source 扩展 | SourceKind / SourceRegistry |
| 管理面 | 先稳定 API，再建设统一 Panel |
| 统一替换 | Phase 4 完成唯一入口与旧编排退役；无旧运行数据转换 |
| 远程和 Sandbox | 后续阶段，不作为第一阶段前置实现 |

**最终判断：下一代 Harness Superagent 不应该只是一个更复杂的 Router，也不应该只是一个能生成 DAG 的 Multi-Agent。它应该成为一个具有明确授权、长期工作身份、可恢复执行、真实产物交接、证据验证和外部协作能力的持续自主工作系统。**

Phase 4 按依赖顺序完成协调模型、执行、交付、质量门、信道及唯一入口；不能将旧编排退役延后到远程执行或新增工作台之后。

## 15. 源码依据

当前入口、协调服务、协议源和最终消费者统一列在[领域模型的源码依据](superagent-domain-model.md#10-源码依据)；使用方法维护在[产品页](superagent.md)，针对性验证和退役回归映射维护在[审查记录](superagent-review.md)。不以原型或已删除的旧编排作为当前能力依据。
