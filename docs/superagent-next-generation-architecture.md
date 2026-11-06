# 下一代 Harness Superagent 总体架构方案

> 状态：架构提案 v0.1，不是当前已实现能力说明。\
> 适用对象：Harness Superagent 维护者、Runtime 维护者、Source 适配器开发者及工作台开发者。\
> 设计基线：Harness HEAD 1b506de；本地 dsh-superagent-prototype HEAD 8c32a9d。

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
- 简单任务和复杂 DAG 使用同一套 Ticket / Mission / Job / Assignment 生命周期。简单任务是单节点 Mission，不再保留两套独立调度内核。
- 外部事件、模型输出、文件交付和远端消息均按不可信输入处理。
- 业务事务内不调用模型、网络、Shell 或文件系统写入。
- 不确定的副作用进入 unknown 并回查原操作；不能用超时、进程重启或模型自述作为重放授权。
- Harness 的 Run、Tool Event、Usage、Session 和 Workspace 不复制成第二套事实账本。
- 先单实例、本机、单仓库 Mission；多租户、跨仓库 DAG、远程 Worker 和 OS Sandbox 分阶段引入。

### 1.4 本方案不承诺的能力

本方案是目标架构，不代表当前已支持动态 Source Plugin、PR Review / CI 全生命周期、多人工作台、容器级 Sandbox、远程 Worker 或多机故障恢复。所有这些能力都必须通过后续阶段的真实实现和验证获得。

## 2. 当前实现基线与需要解决的问题

### 2.1 Harness 当前实现

当前代码主要位于：

| 模块 | 当前责任 |
|---|---|
| superagent_service.py | 配置绑定、双宿主装配、运行循环和关闭 |
| superagent_inbox.py | Source 配置、Task、Subscription、Trigger、Route |
| superagent_router.py | 固定批次、Router 研判和工作投递 |
| superagent_dispatch.py | 稳定提交键、Run 提交和原运行回查 |
| superagent_workers.py | Worker Profile、Session、Workspace 创建及绑定 |
| superagent_missions.py | Mission 规划、DAG 调度、集成、复核和修正 |
| superagent_mission_store.py | Mission / Job 协调账本、计划和快照校验 |
| superagent_results.py | 原运行结果收集及本地回复候选 |
| superagent_control.py / mission_control.py | 暂停、继续、取消及确切原操作收尾 |
| superagent_watchdog.py | Mission 进度指纹、停滞识别和升级 |
| superagent_guidance.py | 绑定确切 Job / Run 的运行中指令 |
| superagent_github.py / superagent_feishu.py | 当前 GitHub / 飞书接收与回复适配 |

当前默认 Router 和 Autonomy 是显式选择的两种协调路径。Router 主要完成事件路由和单 Worker 回复；Autonomy 负责有界任务图、真实文件交接、集成和独立复核。两者共享部分 Inbox、Dispatcher 和 Worker 能力，但领域生命周期尚未统一。

当前协调数据分布在 service、inbox、router、deliveries、workers、results、missions、control、watchdog、guidance 以及信道账本等多个 SQLite 文件。Harness 原生运行数据则保留在各自 Harness Home 中。这种分离保护了执行事实所有权，但跨库操作需要稳定键、CAS 和回查，不能假设跨数据库原子提交。

当前 Autonomy 已有的重要能力应保留：

- DAG 结构、依赖和循环校验；
- 同仓库计划及冻结基线；
- 独立 Worker Session / Worktree；
- 未提交文件的真实 Delivery Capture / Merge；
- 后台作业和原工具证据收尾；
- 独立 Reviewer 与逐项验收证据；
- 有限修正、阶段 Checkpoint 和 Watchdog；
- pause / resume / cancel 的独立持久控制。

### 2.2 dsh-superagent 本地参考实现

本地副本为 dsh-superagent-prototype，HEAD 8c32a9d。README 将其明确标为 Research Prototype，并说明不是生产就绪产品。

值得借鉴的部分：

- src/core/model.ts：Actor、Trigger、Thread、Evaluation、Decision、Mandate、Ticket、Assignment、TicketInput 等领域模型；
- src/core/engine.ts：Intake、Eval、Subscription Fan-out、Ticket 生命周期和领域事件；
- src/core/ports.ts：Policy、Assessor、Executor、Outbox、Repositories 等边界；
- src/policy.ts：版本化规则、Source admission、off / observe / auto；
- src/sources/kind.ts 与 src/sources/registry.ts：SourceKind 插件契约和 SourceRegistry；
- src/app/store.ts 与 src/app/effects.ts：事务存储、持久副作用队列；
- src/app/outbox.ts：消息意图、发送回执和撤回；
- src/executor/dsh.ts：Assignment、Worker Session、工作目录、并发槽位和恢复；
- docs/001-domain.md、docs/002-authorization.md、docs/003-execution.md、docs/007-architecture.md：领域、授权、执行和架构边界。

dsh 的 Store 使用单一 orchestrator SQLite 数据库，核心表包括 trigger、disposition、thread、ticket、decision、outbox、trigger_thread、effect 和 kv；当前源码迁移版本为 11，直接支持的最旧版本为 10。这个版本号只属于参考项目，不作为 Harness 新数据库版本。

### 2.3 需要统一解决的架构问题

1. **Task、Ticket、Mission 的语义重叠**：当前 Harness 的 Task 更接近 Worker / Session 绑定，Mission 是复杂目标；dsh Ticket 则代表持久授权工作。下一代必须给三者不同且稳定的含义。
2. **授权事实缺少统一对象**：Source Revision、Profile 和配置身份不能完全替代不可变 Mandate。
3. **Router 与 Autonomy 生命周期分离**：普通事件路由和复杂目标调度应共用 Ticket 与执行抽象。
4. **多账本协调复杂**：需要一个权威协调事务边界，降低事件接收、决策、派工和 Effect 入队之间的跨库不一致。
5. **事件来源扩展不够通用**：GitHub / Feishu 适配器尚未形成完整的可注册 SourceKind 契约。
6. **持续任务闭环不完整**：需要将 PR、Review、CI、后续消息、Ticket 结束和恢复纳入统一生命周期。
7. **管理面不完整**：缺少统一查看授权依据、Ticket、Mission DAG、Assignment、证据、待人工事项和未知回执的工作台。
8. **执行安全边界仍不足**：工具白名单不等于 OS Sandbox，独立 Worktree 也不等于凭证和进程隔离。

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
| Assignment | 一个 Job 的一次实际执行尝试 | 每次重试新建 Assignment；必须绑定确切 Worker、Workspace、Run 和提交键 |
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

默认 Router 的简单任务也创建一个单节点 Mission；Autonomy 创建多节点 DAG。这样统一调度、回执、复核和状态查询，不需要长期维护两套生命周期。

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

Assignment 状态：pending、submitting、accepted、running、capturing、stopping、succeeded、failed、interrupted、rejected、unknown、cancelled。每次执行、重试和续跑都创建新 Assignment；unknown 先回查原 submission_key / run_id，不创建替代 Run。

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

Harness 继续负责 Router 无工具、Worker Profile 白名单、Integrator / Reviewer 独立 Profile 和工具执行前复核。

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

## 10. 现有能力的统一归属

| 当前 Harness | 统一模型 | 整合原则 |
|---|---|---|
| Inbox sources / triggers / routes | SourceInstance / Trigger / Thread / Disposition | 保留外部身份和原始内容哈希 |
| Inbox tasks | Worker Binding 或 Ticket 关联 | 不把 Session ID 直接当 Ticket |
| Inbox subscriptions | Ticket Subscription | 明确 work / context 语义 |
| Router batches / inputs | Evaluation / Decision / TicketInput | 保留固定批次和原提交身份 |
| Dispatcher deliveries | Assignment | 原 submission_key / run_id 只读映射 |
| Workers | Worker / Assignment | 保留 Session / Workspace 原始引用 |
| Results reports / drafts | Report / Outbox Candidate | Draft 不等于 Sent |
| MissionStore missions / jobs | Mission / PlanRevision / Job | 保留原计划、哈希、基线和状态 |
| Control / MissionControl | ControlCommand / MissionControl | 先核对原命令和停止回执 |
| Watchdog heartbeats | WatchdogSnapshot | 保留指纹、窗口和升级记录 |
| Guidance | TicketInput / MissionInstruction | 保留确切 Run / Turn 绑定 |
| GitHub / Feishu outbound | Channel / OutboxMessage | pending / unknown 不自动重发 |
| Harness 原生存储 | Session / Run / Tool Evidence | 继续作为唯一执行事实来源 |

### 10.1 统一替换与数据边界

当前没有旧版 Superagent 运行数据。统一替换不提供旧配置别名、旧任务导入、离线转换或双轨运行工具；不再将旧版迁移作为生产接入的前置阶段。Phase 4 结束时只支持当前配置和统一服务。

旧状态目录不属于源码删除范围；若发现则在创建宿主或写入前明确拒绝混用，保留原件。Harness 原生 Session / Run / Tool / Workspace 仍是执行事实源，不能迁入协调库制造第二事实源。现有 Coordinator schema_migrations 的校验和及事务性保留，不手工改写历史迁移。

## 11. 分阶段实施计划

### Phase 0：领域和协议冻结

本阶段只冻结领域语义、状态机和接口边界，不改运行代码或现有数据库。三份详细规范互相引用：

- [领域模型与数据契约](superagent-domain-model.md)：身份、对象、关系、表清单、字段语义和不变量；
- [状态机与合法转移](superagent-state-machines.md)：状态集合、守卫、回执、重试和 unknown 恢复规则；
- [架构决策与接口协议](superagent-architecture-decisions.md)：ADR、Source / Policy / Assessor / Executor / Artifact / Evidence / Channel / Repository Ports。

交付：

- Domain Glossary 和稳定身份 / 版本语义；
- Trigger / Thread / Ticket / Mandate / Mission / Job / Assignment 的字段契约；
- 全部核心对象状态机和合法转移矩阵；
- SourceKind / Assessor / Executor / Channel / Repository Ports；
- 故障注入与确定性验收用例清单。

验收：维护者确认三份规范中的对象边界、授权范围、终态 / unknown 语义、数据所有权和接口契约；状态转移可由纯单元测试覆盖；不依赖真实模型或生产账号。文档写入完成不等于维护者评审通过。

### Phase 1：统一协调账本

交付：

- 新的 superagent.sqlite3；
- Repository / Unit of Work / CAS；
- schema_migrations；
- AuditEvent：追加式事件、correlation_id / causation_id 因果关联和 Effect last_event_id 游标；
- Durable Effects + Outbox 基础设施；
- Effect 身份、幂等键和原始 payload 不可变；同一 Ticket 活动 Mission 与同一 Job 活动 Assignment 由 SQLite 唯一索引约束；
- OutboxMessage 与 `outbox.send` Effect 的不可变一对一关联、同事务创建、领取、撤回和结算；
- Provider Receipt 与业务消息状态一致性、原幂等键回查和 unknown 恢复；
- CoordinatorApplicationService 的 Ticket / Mission / Job / Assignment 状态机与宿主事实守卫；
- Effect Provider 回查 Adapter 与恢复 Worker；unknown 只回查原操作，不自动重放；缺失 Adapter、indeterminate 结果和异常 / Receipt 校验失败均追加审计事件，恢复报告计数定义与状态结果一致；
- Mission / Job / Assignment 的 unknown 恢复证据契约：每条恢复都绑定 reconciliation_ref；原 Integrator Assignment / Run、Artifact 哈希、逐边 Delivery 回执、Job 的 Assignment 收尾清单和 Assignment 原 Run 回执均写入 Reconciliation AuditEvent；
- schema_migrations 1–8 从每个历史版本升级、迁移事务回滚和升级后旧 Effect 结算兼容验证；
- Runtime Composition Root：CoordinatorHost 只打开显式 v2 state_dir / superagent.sqlite3，与 v1 共用 superagent-runtime 进程锁；同目录及父子嵌套目录双向拒绝 v1/v2 混用；CoordinatorHost.open 必须接收 legacy_state_dir，并检查其规范化路径与 v2 state_dir 不重叠；v1 SuperagentService 在创建宿主前检查当前及祖先目录中的 v2 数据库标记；
- 宿主事实入口：CoordinatorHost.transition 只接受 HostEvidenceVerifier 签发的 VerifiedTransitionEvidence；证据绑定 aggregate_type、aggregate_id、target_state 和 expected_revision，模型报告不能直接映射为 facts；未注入核验器时 fail closed。Phase 1 只交付核验器注入契约，不交付真实 Executor / Provider 回查适配器；
- 宿主生命周期：Unit of Work、状态转移、Outbox 操作和同步／异步 Effect 回查均持有宿主活动操作租约；存在在途操作时 close 返回 runtime-busy，不释放共享进程锁；缓存的 Outbox / Recovery 句柄在每次调用时重新检查宿主是否关闭；
- Repository 状态边界：Ticket / Mission / Job / Assignment 的普通 Repository.update 禁止修改 state，状态变化只能经 CoordinatorApplicationService；
- v1/v2 接入隔离：CoordinatorHost 不创建旧 Harness / Router / Worker，不注册 CLI / HTTP 默认路由，不双写、不原地迁移；旧版 --superagent CONFIG 保持原入口；
- 稳定幂等键和未知状态模型。

Phase 1 仅持久化 `job_dependencies.requires_delivery` 与逐边 Delivery 回查证据；独立 Delivery 表及其状态生命周期仍属于 Phase 4。

验收：崩溃注入覆盖事务提交前后、Effect 领取前后、回执写入失败和并发领取；Outbox 与 Effect 的状态、Revision、回执和因果审计链保持一致；重启不重复执行未知副作用。宿主边界还须验证：v1 与 v2 共享实例锁、同目录及父子嵌套路径双向拒绝、旧状态目录内容不变、显式 v1/v2 路径重叠拒绝、未注入宿主核验器时禁止状态转移、模型报告不能直接作为宿主回执、Repository 普通 update 不能绕过 Application Service；CoordinatorHost.unit_of_work 只暴露创建 / 读取视图，不暴露原始 DB、Store、内部 UOW 或 ApplicationService，不提供 update，且新聚合只能从初始状态创建；核验结果必须绑定请求 Revision；在途 Unit of Work / Transition / Outbox / Recovery 操作期间不得释放实例锁，缓存服务句柄在关闭后不得继续访问数据库；默认 CLI / HTTP 路由仍只指向 v1，CoordinatorHost 不启动 Harness / Router / Worker、不执行来源接入或外部副作用。

### Phase 2：Source Registry 与授权 Engine

当前实现边界：

- `superagent_source_registry.py` 定义带 API 版本兼容检查的 SourceKind 契约、SourceInstance 注册与配置修订；版本不匹配的插件按 unavailable 处理。`CoordinatorHost.open(source_kinds=...)` 可在宿主启动时注入自定义插件；启动时会对持久化 SourceInstance 与当前插件清单做核对，缺失或版本不匹配的 SourceKind 会撤销对应 Mandate、取消在途 Evaluation 并过滤尚未评估的 Trigger。运行期注册/卸载同样通过宿主租约和注册表锁串行化，卸载不能与事件规范化并发穿越。内置 GitHub、Feishu、Local Adapter。GitHub HMAC 与 Feishu 加密 Webhook 验证后才规范化事件；Local Adapter 必须由宿主身份核验器提供 Actor 事实，未注入时只能形成 system actor。Coordinator 边界再次限制 SourceInstance、Trigger、Actor、Thread 与 Adapter 身份字段长度，避免自定义 SourceKind 把超长标识写入索引或账本。
- SourceInstance 身份由 kind + identity_hash 固定；legacy_source_id 是唯一的一对一映射，绑定后不可重映射，避免历史 Shadow 对照键碰撞；凭据只保存 Secret Reference。SourceCursor 按 (SourceInstance, stream_key) 持久化，以 expected_revision 执行 CAS；SourceRegistry 提供 SourceInstance / Trigger / Thread / Channel 只读 get/list 查询，Trigger 详情包含有界处置事件历史；事件本身仍以 (SourceInstance, external_event_id) 去重，同 ID 不同 fingerprint 直接拒绝。
- Trigger 保存不可变 Actor、正文、元数据、addressed、source_revision 与 adapter_version；Thread Key 包含 SourceInstance 身份，生命周期观察单独推进 lifecycle_revision。GitHub Issue / PR 生命周期以 Provider `updated_at` 作为观察顺序；较旧事件不得覆盖较新状态，同一观察时间发生冲突时 `closed` 优先，避免乱序 Webhook 意外重新开放已关闭 Thread。Channel 只保存不可变地址和能力快照，当前 reply / read_context 均为 false，不代表已经接入发送或上下文读取。
- `superagent_authorization.py` 将 PolicyRevision 的 JSON 快照、policy_rules 和 source_policies 投影写入同一事务。policy_version 是发布修订身份，policy_hash 是内容身份；回到旧内容会创建新修订，不复用历史 Revision。
- Gate 以来源启用状态、Actor、addressed、Admission、Policy Scope 与 Thread 生命周期确定 admissible 与 explicit / inferred 强度；GitHub close 事件仅更新生命周期，不成为新任务，已关闭 Thread 上的新消息也不能进入评估或 fanout。Assessor 只能看到 Gate 放行的 Trigger 正文并返回 Decision Proposal；拒绝项仅向宿主暴露身份 / Gate 结果摘要，不进入模型上下文。accept 的 direction.evidence 与 intent_evidence 必须分别引用同一条 Gate 放行 Trigger 的正文或标题原文，否则降级为 ignore；Proposal 与 Policy 的标识、说明、规则文本和整体快照均有大小边界。accept 只将 Gate 放行的 Trigger 写入 TicketInput / Subscription，拒绝项标记 filtered；Assessor unavailable 的重试也只重新排队 Gate 放行项。宿主重新核对策略版本、Source 配置修订、Thread 生命周期修订、Evaluation deadline、规则命中、来源原文证据和 Scope 后才写 Decision。已过期但未回执的 running Evaluation 会在下一次 begin_evaluation 时取消并重新排队，不会永久占用 Thread。
- observe 保留显式人类请求的接受路径；inferred 候选只记录 would_accept，不创建 Ticket。inferred 自动接受必须由具体来源策略显式启用 auto，并继续通过规则、意图与 Scope 检查。
- Authorization Engine 提供 PolicyRevision、Evaluation、Decision、Mandate 的有界只读历史查询；Evaluation 详情包含冻结策略与逐 Trigger Gate 快照，Decision 保留核验审计依据，Mandate 详情包含追加式状态事件。查询通过 CoordinatorHost 活动租约，不触发 revalidation 或写入。
- accept 在同一事务中创建 Decision、不可变 Mandate、Ticket、work / context Subscription、TicketInput 和审计事件。Mandate Scope 是 Subscription 的硬边界；context 输入不能 handback、恢复 Ticket 或成为 work。每个新 Trigger 在复用既有 Subscription 前都按当前 Policy 重新执行 Gate；只有主 Thread 命中 work Subscription 才形成 work fanout，相关 Thread 命中始终是 context-only，work / context 投影严格分离；Gate 拒绝项保持 evaluation.queued 并在 Evaluation 中过滤，不进入 Assessor 上下文。work follow-up 经 handback 后重新进入 Evaluation。Policy 变化会标记 Mandate affected，并在 revalidation 时核验当前 Gate、Thread 生命周期与 Scope；仍允许时追加 revalidated，Scope 缩窄时撤销。来源停用、SourceInstance 配置修订变化及来源主 Thread 关闭则在各自写事务中立即撤销相关 Mandate；SourceKind 卸载、启动时缺失或 API 版本不匹配也会撤销其 Mandate、取消该来源运行中的 Evaluation，并将待评估 Trigger 过滤；插件恢复后不会自动恢复旧授权或重放已过滤事件，必须重新接收来源事件并重新授权。inferred Mandate 在策略不再为 auto 时撤销，防止 observe 模式继续沿用旧的推断授权。
- `superagent_shadow.py` 只读打开 v1 Inbox / Router 数据库，对照旧新来源事实及决策；决策比较逐 Trigger 应用 v2 Gate 结果，拒绝项映射为 ignore，并报告两侧未匹配记录及样本截断标记；不写任一账本、不执行 Ticket，也不改变生产路由。

**运行与接入边界：** CoordinatorHost 只组合 Source Registry、Authorization Engine、Outbox 和 Shadow 读取服务；没有注册 CLI / HTTP 默认路由，没有启动旧版或新版 Worker，也没有调用 Provider 发送、拉取或执行接口。生产默认路由继续保持 v1；Phase 2 的接入、评估和 Shadow 只能通过显式宿主 API 使用。

**本阶段不包含：** GitHub / Feishu API 轮询与上下文读取、Channel 外发和回执、Harness Executor / Worker 派工、生产账号切换、v1 数据迁移或 v1 默认路由退役。这些能力仍属于后续阶段，不能由当前 Channel 地址记录或 Shadow 对照推断为已实现。

针对性回归覆盖签名错误与有效签名、Feishu 租户与聊天范围、Local 身份、事件重复与冲突、跨 SourceInstance ID 隔离、Policy 更新竞态、显式 / 推断模式、deadline、Scope、Mandate 撤销、work / context、handback、Cursor CAS 并发，以及 Shadow 只读对照。

### Phase 3：单 Worker 执行基础

已通过显式 CoordinatorHost API 实现 Harness Executor Adapter、Worker Profile / Assignment、单节点 Mission、TicketInput 跟进、原 Run / Tool / Background Evidence 回查，以及冻结 Report、Attention、Handback 和 Outbox Candidate。

本阶段没有替换生产默认 Router，也没有 V2 Outbox Sender 或统一 MissionControl Runtime。此前“简单事件不再走独立旧 Router”的生产替换验收尚未实现，明确列入 Phase 4；不追认其已完成。单 Worker 内部闭环与生产整合分别记录。

### Phase 4：完整 Superagent 整合与替换

本阶段结束时，产品只保留一个入口、一套协调状态机、一套调度、执行与恢复机制。Phase 1–3 的协调账本、来源授权及原生执行适配作为基础保留；其交付状态是基础能力已完成、生产整合尚未完成。不得将当前显式宿主 API 已实现写成生产入口已替换。

已确认没有旧版运行数据，不开发旧任务导入、离线转换、历史协议兼容或双轨运行工具。意外发现的旧状态目录保留原件并拒绝混用；既有协调库迁移的校验和与事务规则继续保留。

统一调用链为：Source → Gate / Evaluation → Ticket / Mandate → Mission / PlanRevision → Scheduler → Assignment → Harness Runtime → Report / Artifact / Review → Outbox。简单任务使用单节点计划，代码和文件交付即使只有一个工作节点也必须冻结真实产物并独立复核。模型不能自行降低完成契约。

按以下依赖顺序实施；各出口取得对应源码与针对性回归证据后才能标记完成：

| 子阶段 | 交付 | 出口 |
|---|---|---|
| 4.1 整合契约与协调模型 | Worker 按 Mission / Job / 阶段绑定；输入按 Input / Assignment 独立投递；Artifact、Delivery、Review、控制、指令及 Watchdog 进入同一账本；宿主在封存计划前物化系统角色 | 相同 Profile 多节点隔离、身份约束、不可变计划及跨作用域拒绝可验证 |
| 4.2 服务生命周期与控制 | 改造唯一 SuperagentService，组合 CoordinatorHost、原生 Assessor / Planner、Executor、Effect 与恢复循环；持久控制进入所有资源及写入边界 | 单节点经完整服务运行，暂停、取消及关闭有真实收尾依据；状态查询只读 |
| 4.3 DAG 与真实交付 | 单多节点共用 Scheduler；冻结单仓库基线；公平、背压；Capture / Delivery 使用原生 Worktree 原语及稳定操作身份 | 未提交文件真实交接，冲突保留，未知结果不重放 |
| 4.4 质量门与阶段接续 | 独立 Reviewer、冻结候选、逐项原生证据、有限修正、Checkpoint、Watchdog | 运行成功不代替业务验收；阶段接续不提前解锁下游 |
| 4.5 持续协作与既有信道 | TicketInput、精确节点指令、Attention / Handback；GitHub / Feishu Source / Channel、Outbox Sender 及回查接线 | 本地与受控 Provider 经同一链路完成任务，重复事件及丢响应不重复执行或发送 |
| 4.6 唯一入口与旧编排退役 | CLI 全部接入统一服务；抽离有效共用能力、迁接业务回归后删除旧编排、独立账本及生产 Shadow 装配 | 只有一个服务入口、协调账本及执行恢复链；既有业务能力有对应回归证据 |

Worker 的相同逻辑节点可有多个 Assignment。接续与修正不能覆盖原 Run 身份。Planner 仅提出 work DAG；系统 Integrator / Reviewer 由宿主验证并在计划封存前确定性建立。修正通过新 Assignment 推进，不改写冻结计划。

Pause 阻止新派工与后续写入，Resume 重新核验授权，Cancel 只停止确切所属 Run / Background Job。控制请求已受理不等于真实资源已停止；未知停止、提交、导入和发送保持 unknown，只回查原操作。

普通问答采用只读报告契约；文件或代码任务采用 Artifact / Review 契约；无法确认类型时采用严格契约。完成 Mission 不授予合并、部署或外部发布权限。

### Phase 5：统一系统上的产品协作完善

扩展同一应用服务，提供工作台、分页详情、人工未知结果核实、来源与策略管理、更完整的 GitHub 生命周期及飞书媒体支持。不再建立独立后台编排；既有信道接通属于 Phase 4，不能推迟到本阶段。

验收以对应界面、调用链、受控 Provider 和人工操作证据为准；外部账号、真实模型质量及长期负载单列，不由替身测试推断。

### Phase 6：统一执行边界上的隔离与远程 Worker

Sandbox Runner、Secret Broker、Device Identity / Worker Lease、远程回执、断线恢复和多设备调度均实现同一 Executor Port，保持同一任务、授权及恢复语义。真实双机、不同 OS、断网、迟到回执及 Sandbox 越权须单独实机验收。

原 Phase 7 的旧版迁移路线取消；旧编排退役已纳入 Phase 4 的必需出口。

## 12. 质量门禁与可量化指标

每一阶段必须保留确定性回归，不以“模型回答成功”作为验收。

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

### Harness 当前实现

- docs/superagent.md：当前运行模式、Autonomy、状态、限制和 CLI；
- docs/superagent-review.md：当前 Mission / Job / Delivery / Control / Watchdog 的源码事实及防回归依据；
- harness/app/superagent_service.py：运行装配和配置身份；
- harness/app/superagent_inbox.py：事件、Task、Subscription 和 Route；
- harness/app/superagent_router.py、superagent_dispatch.py：批次决策和稳定提交；
- harness/app/superagent_workers.py：Worker Profile / Session / Workspace；
- harness/app/superagent_missions.py、superagent_mission_store.py：DAG、调度、集成、复核和账本；
- harness/app/superagent_results.py：结果候选；
- harness/app/superagent_control.py、superagent_mission_control.py：控制与收尾；
- harness/app/superagent_watchdog.py：进度监督；
- harness/app/superagent_guidance.py：运行中指令。

### dsh-superagent 本地参考实现

- dsh-superagent-prototype/README.md：项目定位和研究原型声明；
- dsh-superagent-prototype/docs/001-domain.md：领域模型；
- dsh-superagent-prototype/docs/002-authorization.md：Gate、Admission、Mandate 和 Follow-up；
- dsh-superagent-prototype/docs/003-execution.md：Ticket Stage、Assignment、重试和恢复；
- dsh-superagent-prototype/docs/007-architecture.md：Runtime、Engine、Source、Assessor、Executor 和 Store；
- dsh-superagent-prototype/src/core/model.ts、engine.ts、ports.ts；
- dsh-superagent-prototype/src/policy.ts；
- dsh-superagent-prototype/src/sources/kind.ts、registry.ts；
- dsh-superagent-prototype/src/app/store.ts、effects.ts、outbox.ts；
- dsh-superagent-prototype/src/executor/dsh.ts。

以上路径用于说明本提案的设计来源；新架构的字段和状态以未来 Harness 自己的正式协议为准，不直接继承 dsh 的数据库 Schema。
