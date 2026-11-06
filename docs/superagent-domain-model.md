# 下一代 Harness Superagent 领域模型与数据契约

> 文档状态：目标领域模型 v0.1；Phase 1 / Phase 2 当前实现边界见第 9 节。\
> 文档性质：目标领域模型，不代表当前代码已经实现。\
> 关联总览：[下一代 Harness Superagent 总体架构方案](superagent-next-generation-architecture.md)。\
> 关联规范：[状态机与合法转移](superagent-state-machines.md) · [架构决策与接口协议](superagent-architecture-decisions.md)。\
> 核查基线：Harness HEAD 1b506de；dsh-superagent-prototype HEAD 8c32a9d。

## 1. 目的与规范用语

本文冻结下一代 Superagent 的领域名词、身份边界、对象所有权、字段语义、关系和持久化映射。后续数据库 DDL、Repository、API、事件协议和迁移工具必须以本文及配套状态机为准。

- **必须**：协议实现必须满足；偏离需要新的 ADR。
- **建议**：默认采用；确有约束时可通过 ADR 说明替代方案。
- **允许**：合法实现选择，不构成强制行为。
- 本文中的目标对象、表和接口均为设计，不得在产品说明中写成当前可用功能。

设计基于 Harness 当前 Inbox、Router、Dispatcher、Workers、MissionStore、Control、Watchdog、Results 和原生 Runtime；dsh-superagent 的 Trigger / Thread / Mandate / Ticket / Effect / Outbox 仅作为领域参考。dsh README 明确声明它是 Research Prototype，不是生产就绪实现。

## 2. 事实所有权与边界

下一代系统有三个明确的事实边界：

| 事实域 | 权威所有者 | Superagent 保存什么 |
|---|---|---|
| 协作与授权事实 | Superagent Coordinator Store | Source、Trigger、Thread、Policy、Mandate、Ticket、Mission、Job、Assignment 意图及审计 |
| 模型和工具执行事实 | Harness Runtime / Harness Home | 只保存 Session、Run、Turn、Tool Event、Background Job、Usage 等原生身份引用及核验摘要 |
| 外部系统事实 | GitHub、飞书及其他 Source Provider | 保存远端对象身份、游标、回执和内容引用；需要时通过 Source Adapter 回查 |

文件和附件使用内容寻址 Artifact Store。数据库保存 ref、hash、长度、媒体类型和谱系，不把大体积正文、图片或完整工具事件复制进协调账本。

**禁止**把 Harness Run / Tool Event 历史复制成第二份可独立变化的事实账本；也禁止把模型输出直接当作授权、执行完成或远端发送成功的证明。

## 3. 领域关系

~~~mermaid
flowchart TD
  SI[SourceInstance] --> TR[Trigger]
  TR --> TT[TriggerThread]
  TT --> TH[Thread]
  TH --> EV[Evaluation]
  EV --> DE[Decision]
  DE --> MA[Immutable Mandate]
  MA --> TK[Ticket]
  TK --> SU[Work / Context Subscription]
  TK --> TI[TicketInput]
  TK --> MI[Mission]
  MI --> PR[Immutable PlanRevision]
  PR --> JB[Logical Jobs / DAG]
  JB --> AS[Assignment Attempts]
  AS --> WK[Harness Worker / Session / Workspace]
  AS --> AR[Artifact + Evidence]
  AR --> DL[Delivery]
  DL --> IG[Integrator Assignment]
  IG --> RV[Independent Review]
  RV --> OB[OutboxMessage]
  TH --> CH[Channel]
  CH --> OB
  DE --> AU[AuditEvent]
  AS --> EF[Durable Effect]
  OB --> EF
~~~

核心关系：

- 一个 SourceKind 可以创建多个 SourceInstance；不同账号、仓库或 Bot 身份不得仅凭 kind 合并。
- 一个 SourceInstance 可以产生多个 Trigger 和 Thread；Thread Key 必须包含 SourceInstance 身份。
- 一个 Trigger 可以关联多个 Thread，但必须指定唯一 primary Thread 作为首次 Evaluation 的串行化边界。
- 一个 Thread 同一时刻最多一个活动 Evaluation。
- 一个 Decision 最多签发一个 Mandate；一个 Mandate 最多对应一个 Ticket。
- 一个 Ticket 可以先后拥有多个 Mission；默认同一 Ticket 同时最多一个活动或尚未核实的 Mission。
- 一个 Mission 有多个不可变 PlanRevision；一个 PlanRevision 包含多个逻辑 Job 和 DAG 依赖。
- 一个 Job 可以有多个 Assignment；每次执行或重试都是新 Assignment，不覆盖旧尝试。
- 一个 Assignment 绑定确切 Worker、Workspace、Harness Session 和 Run；一个 Worker 可以在受控生命周期内服务多个顺序 Assignment，但不得混淆 Run 身份。
- Artifact 和 Evidence 必须能追溯到产生它们的 Assignment。
- Delivery 是 Artifact 的一次独立导入操作；Review 必须绑定冻结的候选 Artifact 和 Acceptance Hash。

## 4. 身份与版本语义

所有身份均为稳定、不透明的字符串；远端 ID 不得脱离 SourceInstance 单独使用。

| 名称 | 定义 | 用途 |
|---|---|---|
| instance_id | 一个 Superagent state_dir 的稳定身份 | 数据隔离、配置绑定、恢复 |
| source_kind | 代码中的 Source 类型标识 | 选择 Adapter 契约，不代表账号 |
| source_instance_id | 一个已配置的具体来源实例 | Source、Actor、Thread、Trigger 的命名空间 |
| external_event_id | Provider 给一条事件的原生 ID | 与 source_instance_id 组成去重键 |
| trigger_id | Coordinator 内部事件 ID | 领域引用和审计 |
| external_thread_id | Provider 原生会话、Issue、PR 或话题 ID | 由 Source Adapter 解析 |
| thread_id | Coordinator 内部 Thread ID | Thread 聚合身份 |
| external_actor_id | Provider 原生发送者 ID | 发送者身份核验 |
| channel_id | Coordinator 内部写入端点 ID | Outbox 的目标身份 |
| aggregate_revision | 单个领域聚合的 CAS 版本 | 防止并发旧写覆盖新状态 |
| source_revision | SourceInstance 配置修订 | 绑定 Trigger 接收时的来源配置 |
| provider_revision | Provider 原生对象版本 / ETag | 检测 Thread 生命周期快照是否过期；不替代 aggregate_revision |
| policy_version | 不可变授权策略快照版本 | Gate、Mandate 和重新核验 |
| plan_revision | Mission 的不可变计划版本 | DAG 和 Acceptance 快照 |
| round_no | 集成 / 复核修正轮次 | 限制修正预算，区分复核轮 |
| attempt_no | 同一 Job 的 Assignment 尝试序号 | 稳定重试身份 |
| schema_version | Coordinator 数据库迁移版本 | 数据结构升级 |

不得复用一个通用 revision 字段表达上述不同概念。若某个表同时需要 CAS 和业务版本，必须使用不同列。

### 4.1 Trigger 与 Thread 的复合身份

- Trigger 去重键：UNIQUE(source_instance_id, external_event_id)。
- Thread 去重键：UNIQUE(source_instance_id, external_thread_id)。
- Actor 身份：(source_instance_id, external_actor_id)；actor_kind 由已验证的 Adapter 声明，不能由消息正文自述。
- Channel 地址必须绑定 source_instance_id 和 Thread；地址 JSON 需要规范化并保存哈希。
- 相同 Trigger 身份、相同规范化内容哈希：返回原接收回执，不重复入队。
- 相同 Trigger 身份、不同内容哈希：返回冲突并保留原记录；不得覆盖原 Trigger。

### 4.2 JSON 与哈希

JSON 仅用于有界、版本化的扩展数据或不可变快照。需要查询、唯一约束、排序、外键和并发控制的关系必须规范化为列或关联表。

序列化必须固定 UTF-8、键排序和数值规则；拒绝重复对象键、NaN / Infinity、非法 UTF-8 和不符合字段 Schema 的值。哈希必须覆盖规范化后的确切字节。读取时重新验证哈希、Schema Version、对象归属和关联身份。

## 5. 聚合与实体定义

### 5.1 SourceKind 与 SourceInstance

**SourceKind** 是代码层插件描述，不是每个账号一行的业务实体。它声明：

- 稳定 kind 和 api_version；
- 配置 Schema、默认值和能力；
- 身份验证与事件规范化；
- Source Scope / Admission 元数据；
- Thread Context Reader；
- 可选 Thread Lifecycle Reader，用于确认 Thread closed / merged / reopened 等来源生命周期事实；
- 可选 Channel 地址解析能力；实际发送与发送回查由独立 Channel Port 执行，Provider 远端撤回不属于 Phase 1 基础契约；
- 健康状态与游标能力。

SourceCursor 以 (source_instance_id, stream_key) 为唯一身份，保存不透明 cursor_json、cursor_revision 和 updated_at。游标推进必须携带 expected_revision 并执行 CAS；相同内容重放是幂等重复，过期 Revision 冲突失败。Cursor 是拉取进度提示，不替代 Trigger 的事件去重键，也不授权跳过尚未持久化的事件。SourceRegistry 同时提供 SourceInstance、Thread、Channel 的只读 get/list 查询；Channel 查询只暴露地址与能力快照，不提供发送或读取上下文动作。

**SourceInstance** 是某个具体 Provider 账号、仓库、Bot 或本机事件入口。字段至少包括：

source_instance_id, kind, kind_api_version, enabled, config_json, config_revision, identity_hash, legacy_source_id, created_at, updated_at, health_json。

凭据只保存 Secret Reference，不保存 Token 明文。配置修订变更不能悄悄改变已接收 Trigger 的身份解释。legacy_source_id 只用于 v1/v2 Shadow 对照，不参与来源身份哈希；可以从未绑定状态绑定一次，绑定后不可更改或清空，避免历史 Trigger 的 Shadow 归属被追溯性重解释。

### 5.2 ActorRef、ThreadRef 与 ChannelRef

- ActorRef = {source_instance_id, external_actor_id, actor_kind}，actor_kind 为 human | bot | system | unknown。
- ThreadRef = {source_instance_id, external_thread_id}。
- ChannelRef = {channel_id, source_instance_id, thread_id, address_ref, address_hash}。

Channel 只说明消息可写到哪里，不代表该 Ticket 已获得对外发送授权。地址按 SourceInstance + Thread + address_hash 唯一保存且不可原地修改；当前 Phase 2 只记录地址，能力默认为不可发送、不可读取上下文。发送必须经 Mandate 校验和 Outbox。

### 5.3 Trigger 与 TriggerDisposition

Trigger 是不可变的来源事件，字段至少包括：

trigger_id, source_instance_id, external_event_id, actor_ref, event_kind, occurred_at, received_at, addressed, primary_thread_id, body_ref, body_hash, meta_ref, meta_hash, source_revision, adapter_version, verification_state。

addressed 只能是 Adapter 根据 Provider 事实确认的 mention | direct | none | unknown。explicit / inferred 是 Gate 结合主体、来源和 Policy 得出的授权强度，不应由模型或原始消息自行声明。

Trigger 的原始正文和元数据不因后续路由或策略变化而改写。数据库以 (source_instance_id, external_event_id) 唯一去重；同一身份、同一规范化事实返回 duplicate，身份相同但 fingerprint 不同则拒绝，不能覆盖旧事实。

TriggerDisposition 是 Trigger 的路由投影，不是 Trigger 本体。至少记录：

- Evaluation 身份及排队 / 决策结果；
- Fan-out 到的 Ticket；
- 仅作为 context 到达的 Ticket；
- 是否被重新排队；
- 每个 Ticket 的 handback 原因和时间；
- 已由哪个 Ticket 接受处理。

路由投影可以更新，但每次变化必须追加 trigger_disposition_events 和 audit_events。

### 5.4 Thread

Thread 是某个 SourceInstance 内的事件流、Evaluation 一致性边界和来源生命周期投影。字段至少包括：

thread_id, source_instance_id, external_thread_id, metadata_ref, source_state, source_closed_at, source_outcome, provider_revision, source_observed_at, created_at, last_event_at, aggregate_revision。

source_state = open | closed | unknown | unsupported；source_outcome = merged | closed | null。open 时 source_closed_at / source_outcome 必须为空；closed 时 source_closed_at 和 source_outcome 必须齐全；unknown / unsupported 时两者为空。source_observed_at 记录最近一次生命周期观察时间；provider_revision 是 Provider 原生版本，不得与 SourceInstance config_revision / source_revision 混用。SourceKind 不支持 Thread 生命周期时标记 unsupported；支持但尚未核实当前状态时标记 unknown。reopen 事件将当前投影恢复为 open 并清空当前 closed_at / outcome；历史仍由不可变 Trigger 和 AuditEvent 保留。

Thread 的 Subscription、待评估 Trigger、活动 Evaluation 和 Decision 历史通过关联表读取。共享 Trigger 产生的相关 Thread 只增加上下文关联，不能跨 SourceInstance 合并身份。

### 5.5 PolicyRevision、Evaluation 与 Decision

**PolicyRevision** 是不可变策略快照，包含全局规则、来源 Admission、允许主体、Bot 规则、显式 / 推断接受模式及预算边界。policy_version 标识一次发布修订，policy_hash 标识规范化内容；同一内容在切换到其他策略后再次启用会形成新的修订身份。policy_rules 与 source_policies 是不可变的关系投影，不能脱离该快照修改。

**Gate** 是宿主确定性准入检查：验证 SourceInstance 启用状态、Actor 类型、来源 Admission、addressed 事实和 Policy Scope。Gate 决定 admissible 与 explicit / inferred 强度；Assessor 不能提升强度或扩大 Scope，且只能接收 Gate 放行的 Trigger 正文。Gate 拒绝的 Trigger 仅保留身份与拒绝原因用于审计，不得暴露给 Assessor 或进入 TicketInput / Subscription Fan-out。observe 模式保留显式人类请求的正常接受路径，但 inferred 提案只记录 would_accept，不签发 Ticket；只有显式启用 auto 且其他 Gate / Rule 条件通过，才允许 inferred 接受。

**Evaluation** 是一个 Thread 上固定输入批次的一次研判。字段至少包括：

eval_id, thread_id, policy_version, state, trigger_batch_hash, strength, actor_ref, mandate_basis_json, started_at, delivered_at, deadline_at, paused_at, completed_at, aggregate_revision。

Evaluation 开始后，其 Trigger 列表、逐 Trigger Gate 结果和 Policy Version 不得原地变化。Assessor 候选只包含 Gate admissible 的 Trigger 正文；完整 Gate 结果以不含正文的身份 / 结果摘要单独保留。accept 时只有 Gate admissible 的 Trigger 能成为 TicketInput 或创建相关 Thread Subscription，其他 Trigger 标记为 filtered。后来到达的 Trigger 进入下一批。Assessor 回执必须在 deadline_at 前完成；过期回执作废并将原批次重新排队，不产生授权。若 Assessor 没有回执，下一次 begin_evaluation 也会先取消已过期的 running Evaluation，再为同一批 Trigger 创建新尝试，避免永久占住 Thread。completed_at 标记 Evaluation 终结；unavailable 可按有界重试重新排队，但每次重试创建新的 Evaluation 身份，且只重新排队 Gate admissible 的 Trigger，拒绝项保持 filtered。一个 Thread 同时最多一个活动 Evaluation。

**Decision** 是宿主核验后的不可变决策，字段至少包括：

decision_id, eval_id, thread_id, policy_version, kind, reason_json, audit_json, ticket_id, created_at。

Decision kind：ignore | accept | reopen | stop | unavailable。模型只提供 Decision Proposal；宿主重新核验 Gate、Policy、输入批次和幂等身份后，才写入 Decision。

### 5.6 Mandate

Mandate 是一次 accept 决策签发的不可变授权快照。至少包含：

- mandate_id, decision_id, ticket_id；
- strength = explicit | inferred；

- policy_version；
- 原始 Trigger、SourceInstance、ActorRef；
- basis_json：来源范围、Admission Row / Cell、addressed、允许主体、命中规则及 Gate 依据；
- scope_json：仓库、Source、Thread、Worker Profile、允许动作和预算；
- granted_at、可选 expires_at。

Mandate 的自然语言摘要不能代替结构化 scope_json。模型不能扩大仓库、Profile、工具、网络、预算或对外发送范围。

Mandate 本体不可变。mandate_events 追加 affected | revalidated | revoked | expired 等事件；当前有效性是派生投影。策略版本改变不自动等于撤销：写入路径按原 Gate Basis 重新核验，并在当前 Gate 与 Policy Scope 仍允许时追加 revalidated；新 Policy Scope 必须完整覆盖原 Mandate Scope，否则撤销授权并阻止后续 fanout 或重新激活订阅。SourceInstance 配置修订变化会使原 Mandate 失效；inferred Mandate 仅在当前策略仍为 auto 时有效，切换至 observe 或 off 时撤销。

### 5.7 Ticket、Subscription、TicketInput 与 Attention

**Ticket** 是长期授权工作单元。它持有 Mandate、原始来源、订阅关系、后续输入、Channel、Mission 历史和终结结果。

字段至少包括：

ticket_id, mandate_id, origin_trigger_id, title, state, outcome, ended_by, ended_at, created_at, updated_at, aggregate_revision。

Ticket state 只有 open | ended。终结 outcome：completed | merged | closed | dropped | stopped | revoked | failed。ended_by：human | executor | policy | system。merged / closed 来自满足 followsDone 的 Thread Source Outcome；若没有可跟踪 Thread，则 Worker ready / dropped 分别形成 completed / dropped。host 确认不可恢复的 Mission failure / rejection、预算已耗尽、无未决 Attention 且无 unknown 副作用时可终结为 failed。

**Subscription** 通过 ticket_subscriptions 表达：

- mode = work | context。work 允许符合原 Mandate 范围的人类后续消息成为 Ticket Input；context 只提供只读上下文，不得恢复 Ticket、派工、写文件或对外发言。
- Subscription 的 ticket_id、thread_id、source_instance_id、mode 和 owns_thread 是不可变身份；取消后只能按原身份重新激活，不能把 context 原地升级为 work。
- 自动从 Trigger 关联 Thread 创建 Subscription 时，必须逐个核对 Mandate 的 source_instance_ids 与 thread_ids；超出范围的 related Thread 不得被隐式订阅。
- owns_thread = boolean，独立于 mode，表示该 Ticket 是否创建或拥有该 Thread，例如它创建的 PR。

Source Closure 是 Thread 的来源事实，不在每个 Subscription 中重复保存；Thread 聚合维护 source_state / source_closed_at / source_outcome。

Ticket 可以跟随自己创建的 PR，也可以跟随相关 PR / Issue。followsDone 的默认规则是：只考虑 source_state 不为 unsupported 的可跟踪 Thread；若存在 own Thread，则所有 own Thread 都必须 closed；否则所有 work Thread 都必须 closed。context-only Thread 不参与结束判断。任何参与判断的 Thread 为 unknown 时，followsDone 不成立；没有可跟踪 Thread 时 followsDone=true。

一个 Trigger 同时到达多个订阅 Ticket 时，同一 Ticket 只接收一个 Input；任一 work Subscription 优先于同 Ticket 的 context Subscription。Ticket 的 ready / dropped 报告只有在 followsDone 满足时才终结；若 Thread 先关闭而工作尚未完成，Ticket 保持 open，等后续 ready / dropped 报告再终结；若 Worker 先 ready / dropped，则由最后一个必需 Thread 的 Source Closure 触发终结。若存在多个必需 Thread，只有全部 outcome=merged 时 Ticket outcome 才为 merged；只要其中一个为 closed，最终 outcome 为 closed。

**TicketInput** 是 Ticket 收到的有序输入。kind 至少支持 trigger | policy_changed | reopen | resumed | text | note。Trigger Input 记录 trigger_id, human, context_only, delivered_to_assignment_id, delivered_at, read_at, state。同一 Ticket 对同一 Trigger 只能存在一个有效 Trigger Input。

**Attention** 是等待人作决定的问题，字段至少包括 attention_id, ticket_id, mission_id, trigger_id, question_ref, options_json, state, answer_trigger_id, created_at, resolved_at。Attention 是人工协作状态，不是一个新的授权来源。

### 5.8 Mission、PlanRevision、Job 与 Assignment

**Mission** 是 Ticket 下的一次有界交付活动。一个 Ticket 可以顺序创建多个 Mission，但默认同一时刻只能有一个活动或尚未核实的 Mission。

Mission 字段至少包括：

mission_id, ticket_id, sequence, state, outcome, goal_ref, policy_version_at_start, source_revision_at_start, plan_revision, repo, base_commit, round_no, budgets_json, created_at, updated_at, aggregate_revision。

初期一个 Mission 只允许一个 Git Repository 和一个冻结 base_commit。跨仓库交付拆成多个 Mission 或由明确的上层流程协调。

**PlanRevision** 是不可变的 DAG / Acceptance 快照，字段至少包括：

mission_id, plan_revision, plan_json, plan_hash, acceptance_json, acceptance_hash, created_at, created_by_kind, created_by_ref。

created_by_kind = host | human | assignment；created_by_ref 指向对应 Actor 或生成该版本的 Planner Assignment。由 Planner 产生的 PlanRevision 必须引用其 Assignment。

计划中的每个节点必须有稳定 node_id、role、profile_id、指令引用、依赖和资源范围。node_id 在同一 Mission / PlanRevision 内唯一；模型规划的业务节点不得使用保留前缀 __system__。宿主生成的 plan / integrate / review 节点使用保留身份，其中规划节点固定使用 plan_revision=0、node_id=__system__plan，真实 PlanRevision 从 1 开始；集成和复核节点使用包含 round_no 的稳定 node_id。修正或重规划产生新 PlanRevision，不能覆盖旧计划。

**Job** 是 PlanRevision 中的逻辑节点，不代表一次执行。角色至少包括 plan | work | integrate | review。字段至少包括：

job_id, mission_id, plan_revision, node_id, role, profile_id, instruction_ref, instruction_hash, state, spec_hash, result_hash, aggregate_revision。result_hash 按角色绑定最终结果：plan 为 PlanRevision hash，work / integrate 为 Artifact manifest / tree hash，review 为 Verdict 与 Evidence Set hash。

依赖使用 job_dependencies(job_id, depends_on_job_id, ordinal, requires_delivery) 关系表，不只存在于 JSON。每条依赖必须连接同一 mission_id 和 plan_revision 的 Job；Schema Contract 通过复合外键或等效事务校验拒绝跨 Mission / PlanRevision 依赖、自依赖和环。requires_delivery 标记该依赖边是否必须导入 Artifact；requires_delivery=true 用于消费上游文件 / Git 交付的边，false 用于仅依赖逻辑结果的边。下游 Job 只有在依赖 Job done 且所有 requires_delivery=true 的对应 Delivery 均 applied 并核验目标树后才可 ready；checkpointed 不算完成。

**Assignment** 是 Job 的一次真实执行尝试。每次重试、续跑或重新提交都创建新 Assignment。字段至少包括：

assignment_id, job_id, attempt_no, worker_id, session_id, workspace_id, run_id, submission_key, state, report_json, started_at, ended_at, error_ref, aggregate_revision。

唯一约束：UNIQUE(job_id, attempt_no) 和 UNIQUE(submission_key)。Assignment 不得通过更新 run_id 把一个执行尝试改成另一个 Run。

### 5.9 Worker、Artifact、Delivery、Evidence 与 Review

**Worker** 是 Harness Runtime 资源绑定，至少包括：

worker_id, profile_id, repo, session_id, workspace_id, runtime_instance_id, state, created_at, last_seen_at, identity_hash。

Worker 保存 Harness 资源引用，不复制 Session 内容。Integrator 和 Reviewer 必须使用权限边界独立的 Profile；关键 Review 至少使用独立 Session / Workspace。

**Artifact** 是冻结的真实文件交付，至少包括：

artifact_id, assignment_id, kind, repo, base_commit, commit_id, tree_hash, manifest_ref, manifest_hash, created_at。

Manifest 应覆盖新增、修改、删除文件和每个文件的内容哈希。自然语言摘要不是 Artifact。

**Delivery** 是一次独立导入操作，至少包括：

delivery_id, artifact_id, target_job_id, target_workspace_id, state, import_plan_ref, import_plan_hash, expected_tree, applied_tree, applied_commit, idempotency_key, started_at, finished_at。

Phase 1 Coordinator Schema 尚未建立独立 Delivery 表。当前只持久化依赖边的 `requires_delivery` 标记；Mission unknown 恢复时将每条必需依赖边对应的宿主 applied 证据引用写入 Reconciliation AuditEvent，并要求 Integrator Artifact 绑定原 Integrator Assignment / Run。该审计证据不等于完整 Delivery 状态机；独立 Delivery 持久化在 Phase 4 实施。

**Evidence** 是 Harness 原生事实的引用，至少包括：

evidence_id, assignment_id, harness_instance_id, session_id, run_id, turn_id, tool_call_id, result_seq, evidence_type, payload_hash, reference_json, created_at。

原始事件仍由 Harness Runtime 持有。Evidence 必须能回到原 Run / Turn / Tool Call 核验。

**Review** 必须绑定 mission_id, reviewer_assignment_id, candidate_artifact_id, plan_revision, acceptance_hash, verdict_json, evidence_set_hash, state, created_at。Reviewer 不得修改候选 Artifact；Verdict 必须逐项引用当前轮 Evidence。

### 5.10 Effect、Outbox、Control、Watchdog 与 Audit

**Effect** 是事务外副作用的持久执行意图。至少包括：

effect_id, kind, aggregate_id, idempotency_key, payload_json, state, attempts, due_at, lease_owner, lease_until, generation, last_error, created_at。

**OutboxMessage** 是对外消息意图，至少包括：

message_id, ticket_id, channel_id, kind, body_ref, body_hash, state, provider_id, provider_key, attempts, sent_at。

Draft、模型报告或 send() 已调用均不等于消息 Sent；只有 Provider 确认或原操作回查证据才可标记 sent。withdrawn 只表示发送前在本地撤回 Outbox 意图，不调用 Provider；远端消息撤回如需支持，必须另设持久 Effect、授权和回执协议。

**ControlCommand** 保存 pause | resume | cancel 的独立命令、期望修订、提交身份和原回执。MissionControl 是 Mission 当前控制投影，不取代原 Run / Background Job 的真实状态。

**WatchdogSnapshot** 保存 mission_id, fingerprint, state, revision, first_seen, last_progress, last_seen, stagnant_ticks, reason。Watchdog 观察活性，不自行重放 Assignment 或 Effect。

**AuditEvent** 追加记录状态转移、授权、提交、回执、人工控制、策略变更、未知结果和恢复操作。至少包括：

seq, event_id, aggregate_type, aggregate_id, event_type, schema_version, payload_json, correlation_id, causation_id, occurred_at, actor_ref。

## 6. 目标协调数据库表清单

一个 Superagent Instance 使用一个专用 state_dir/superagent.sqlite3。下表是目标规范化表清单；具体 SQL DDL 在 Phase 1 实施，不应在 Phase 0 直接创建或迁移现有数据库。

| 分组 | 表 | 主键 / 关键唯一约束 | 说明 |
|---|---|---|---|
| Instance | instances | instance_id | state_dir 身份、配置哈希和创建时间 |
| Source | source_instances | source_instance_id | kind、配置版本、启停、Secret Reference |
| Source | source_cursors | (source_instance_id, stream_key) | Provider 轮询游标 |
| Thread | threads | thread_id；唯一 (source_instance_id, external_thread_id) | 来源内 Thread 与生命周期投影 |
| Event | triggers | trigger_id；唯一 (source_instance_id, external_event_id) | 不可变来源事件 |
| Event | trigger_threads | (trigger_id, thread_id) | Trigger 与多 Thread 的关联，含 primary / related |
| Event | trigger_dispositions | trigger_id | 当前路由投影 |
| Event | trigger_disposition_events | event_id | 路由历史 |
| Channel | channels | channel_id | Thread 写入地址及能力 |
| Policy | policy_versions | policy_version | 不可变策略快照及 hash |
| Policy | policy_rules | (policy_version, rule_id) | 规则投影 |
| Policy | source_policies | (policy_version, source_instance_id) | 来源 Admission 快照 |
| Evaluation | evaluations | eval_id | 固定输入批次、截止时间和策略版本 |
| Evaluation | evaluation_triggers | (eval_id, ordinal)；唯一 (eval_id, trigger_id) | Eval 输入顺序 |
| Decision | decisions | decision_id；唯一 eval_id | 不可变研判结果 |
| Authorization | mandates | mandate_id；唯一 decision_id、唯一 ticket_id | 不可变授权 |
| Authorization | mandate_events | event_id | 影响、重新核验、撤销和到期记录 |
| Ticket | tickets | ticket_id；唯一 mandate_id | 长期授权工作 |
| Ticket | ticket_subscriptions | (ticket_id, thread_id) | mode / owns_thread |
| Ticket | ticket_inputs | input_id；唯一 (ticket_id, trigger_id) | 输入投递和读取事实 |
| Ticket | attentions | attention_id | 人工待决问题 |
| Mission | missions | mission_id；唯一 (ticket_id, sequence) | 有界交付活动 |
| Mission | mission_plans | (mission_id, plan_revision) | 不可变计划和 Acceptance |
| DAG | jobs | job_id；唯一 (mission_id, plan_revision, node_id) | 逻辑节点 |
| DAG | job_dependencies | (job_id, depends_on_job_id) | 依赖关系及 requires_delivery 标记 |
| Execution | workers | worker_id | Harness 资源绑定 |
| Execution | assignments | assignment_id；唯一 (job_id, attempt_no)、submission_key | 实际执行尝试 |
| Artifact | artifacts | artifact_id | 冻结文件交付 |
| Artifact | deliveries | delivery_id；唯一 idempotency_key | Artifact 导入 |
| Evidence | evidence | evidence_id | 原生运行事实引用 |
| Review | reviews | review_id | 候选、Acceptance 和证据集绑定 |
| Effects | effects | effect_id；唯一 (kind, idempotency_key) | Durable Effect |
| Outbox | outbox_messages | message_id；Provider Key 按 Provider 作用域唯一 | 外部发送意图与回执 |
| Control | control_commands | command_id；唯一 idempotency_key | 控制命令及回执 |
| Control | mission_controls | mission_id | 当前控制投影和 generation |
| Watchdog | watchdog_snapshots | mission_id | 活性指纹与停滞观察 |
| Audit | audit_events | seq、唯一 event_id | 追加式审计 |
| API | idempotency_keys | (scope, operation_key) | API / CLI 操作幂等身份 |
| Schema | schema_migrations | version | DDL 迁移版本和 checksum |

### 6.1 必需索引与数据库约束

- triggers(source_instance_id, external_event_id) 唯一。
- threads(source_instance_id, external_thread_id) 唯一。
- evaluations(thread_id) 对 queued / running，以及尚未终结的 unavailable（completed_at IS NULL）建立部分唯一索引，确保同 Thread 单活动 Eval。
- decisions(eval_id) 唯一；mandates(decision_id) 唯一；tickets(mandate_id) 唯一。
- missions(ticket_id, sequence) 唯一；每 Ticket 对非终态 Mission 建立部分唯一索引，unknown 仍占用名额直至核实。
- jobs(mission_id, plan_revision, node_id) 唯一；依赖表拒绝跨 Mission / PlanRevision、自依赖和环。
- assignments(job_id, attempt_no) 唯一；submission_key 全局或 Instance 作用域唯一；每 Job 同时最多一个活动 / 未知 Assignment。
- deliveries(idempotency_key) 唯一；effects(kind, idempotency_key) 唯一。
- outbox_messages(state, created_at)、effects(state, due_at, kind)、assignments(state, started_at) 建立调度索引。
- 外键启用；写事务使用 BEGIN IMMEDIATE 或等效 CAS 机制。
- 列表读取必须分页并设硬上限；完整性错误不能静默修复或退化为空列表。

## 7. 核心不变量

1. SourceInstance 是 Actor、Thread、Trigger 和 Channel 的身份命名空间。
2. Trigger 不可变；重复身份同哈希是重复回执，异哈希是冲突。
3. Thread 一次只有一个活动 Evaluation；Evaluation 的输入和 Policy Version 在开始后冻结。
4. Model Assessor 没有 Repository 写权限；它只能返回 Proposal，且只能看到 Gate 放行的 Trigger 正文。
5. Mandate 只能由宿主在已核验的 Decision.accept 后签发，且本体不可变。
6. Bot、CI、引用文本和转发内容不能自行升级为显式人类授权。
7. context Subscription 只提供上下文，不恢复 Ticket、不派工、不扩大权限。
8. 一个 Ticket 只对应一个 Mandate；一个 Ticket 可顺序拥有多个 Mission。
9. Mission 计划、Acceptance、Job Spec 和 Artifact 均需哈希；历史版本不可覆盖。
10. Job 是逻辑节点；每次执行都是新的 Assignment；旧 Run 身份不可改写。
11. 下游 Job 必须同时满足依赖 Job done，以及所有标记 requires_delivery 的 Artifact Delivery 已 applied 并核验目标树；Checkpoint 不等于完成。
12. Artifact 必须来自冻结工作树；Reviewer 必须核验原始 Harness Evidence 和候选树不变。
13. 本地业务意图与 Effect 入队在一个事务提交；外部调用必须在事务外执行。
14. 不确定副作用保持 unknown；未知状态不得自动重试、覆盖或报告成功。
15. Ticket、Mission、Assignment 的状态转移必须追加 AuditEvent 并比较预期 revision。
16. 对外发送只能通过 Outbox；模型报告、Draft 和请求发起不是发送回执。
17. PR Merge、部署和其他高风险外部动作需要独立授权和回执，不能隐含在 Mission completed 中。

## 8. 与当前实现的映射

| 当前实现事实 | 下一代领域映射 | 需要改变的语义 |
|---|---|---|
| Inbox sources | SourceInstance / Source Policy | 增加 SourceKind 契约与配置修订 |
| Inbox triggers | Trigger | 保留远端身份和正文冲突检测；补充不可变内容引用 |

| Inbox tasks | Ticket / Worker 绑定拆分 | 当前 Task 绑定 Session，不等于长期 Ticket |

| Inbox subscriptions.context_only | Subscription mode | 明确 work / context 及 owns_thread |
| Router batches / inputs | Evaluation / Decision / TicketInput | 固定批次、Policy 快照和授权边界 |
| Dispatcher deliveries | Assignment | 稳定提交键和原 Run 回查继续保留 |
| Workers | Worker | Session / Workspace 资源仍引用 Harness |
| MissionStore missions / jobs | Mission / Job / Assignment | 当前 Job ID 含 attempt；目标模型将逻辑 Job 与执行 Assignment 分开 |
| MissionStore revision | source_revision / aggregate_revision 分离 | 禁止一个 revision 混合多个版本含义 |
| Checkpoint / Delivery | Artifact / Delivery | 真实文件树和冻结基线继续作为交付事实 |
| Watchdog heartbeats | WatchdogSnapshot | 活性观察与任务终态分离 |
| Results drafts | Report / Outbox Candidate | Draft 不等于 Sent |
| Harness 原生存储 | Evidence Provider | 不复制 Run / Tool Event 历史 |

## 9. Phase 1 / Phase 2 / Phase 3 当前实现边界

本文件定义完整目标领域模型。Phase 1 的独立持久化基础设施、状态机基础与宿主隔离已完成；Phase 2 的 Source Registry、Authorization Engine 和只读 Shadow 已通过显式 CoordinatorHost API 提供；Phase 3 的单 Worker 执行闭环也已通过显式 CoordinatorHost API 实现并完成专项回归。所有新能力仍保持显式 opt-in，不切换 v1 生产默认路由。

已落地的持久化基础：

- `CoordinatorStore` 只在调用方显式提供数据库路径和 Instance 身份时初始化专用 SQLite 文件；启用 WAL、Application ID、外键、事务和带 checksum 的版本迁移。
- Schema v1 提供 Instance 身份、聚合 Revision、追加式 AuditEvent 和 Durable Effect。
- Schema v2 提供 Ticket、Mission、PlanRevision、Job、Job Dependency、Assignment 规范化表及其基础唯一约束。
- Schema v3 提供 OutboxMessage、Provider Receipt Key 唯一约束和不可变身份约束。
- Schema v4 为 PlanRevision、Job 和 JobDependency 增加顺序、身份、不可变和 DAG 环约束；PlanRevision 仅允许在同一事务内完成物化后封存。
- Ticket、Mission、Job、Assignment、OutboxMessage Repository 通过 Unit of Work 写入；CAS、AuditEvent 和可选 Effect 入队处于同一 SQLite 事务。
- PlanRevisionRepository 通过 Mission CAS 创建不可变计划与 Acceptance 快照，并在同一事务内生成稳定 Job ID、物化依赖边、校验节点身份与 DAG 环，再封存版本；读取时重新校验快照哈希、Job 身份集合和物化依赖边；JobDependencyRepository 提供按 Job / PlanRevision 查询。
- 过期 Effect Lease 只会转为 unknown 并清除租约，不会自动重新入队；EffectRecoveryService 只调用注入的只读 lookup Adapter，只有原 Provider 回执证明已应用或明确证明未应用且 retry_safe 时才会完成 reconciliation。
- Mission 的 plan_revision 只能由 PlanRevisionRepository 推进；封存后的计划、Job 身份、计划规格字段及依赖关系不可追加或改写；读取时核对 PlanRevision 哈希、Job 规格哈希与 DAG 物化结果。
- Assignment 的 Run 身份一经绑定不可替换；Outbox 的状态转换拒绝从 unknown 自动回到 sending，发送成功必须同时保存 Provider 身份、幂等键和时间。

Phase 2 已实现的来源与授权能力：

- `SourceRegistry` 提供带 API 版本兼容检查的 `SourceKind` 插件契约、SourceInstance 配置修订，以及 GitHub、Feishu、Local 三种内置来源规范化；版本不匹配的插件按 unavailable 处理。`CoordinatorHost.open(source_kinds=...)` 可在启动时注入自定义插件，并核对持久化 SourceInstance 与当前插件清单；缺失或版本不匹配会撤销相关 Mandate、取消在途 Evaluation、过滤待评估 Trigger。运行期插件注册/卸载由注册表锁与宿主操作租约保护，卸载与事件规范化串行执行；插件恢复不会自动恢复旧授权或重放旧事件。Webhook 在调用插件前统一限制请求体、Header 数量及单值/总量大小，拒绝 NUL 与大小写不敏感的重复 Header；Feishu 数字字符串 `event_time` 按 Unix 时间规范化。规范化 Trigger 的 SourceInstance / Event / Actor / Thread / Adapter 身份字段均有长度上限；正文、元数据、Thread 元数据和 Channel 地址也有独立大小边界，并拒绝 NUL、无效空标识与空元数据类型。SourceCursor 只允许启用且插件版本兼容的来源推进，并以 CAS 防止并发覆盖；重复推进返回原持久化时间。`legacy_source_id` 是唯一的一对一映射，绑定后不可重映射，防止 Shadow 对照键发生碰撞。Trigger 去重、Trigger 事实及处置历史的有界查询、Thread / Channel 投影与 SourceCursor 均由独立协调账本持久化；GitHub Thread 生命周期按 Provider 更新时间排序，迟到事件不能覆盖较新状态，同一观察时间冲突时 `closed` 优先。
- `CoordinatorAuthorizationEngine` 提供不可变 PolicyRevision、Gate、Evaluation、Decision、Mandate、Ticket、Subscription 和 TicketInput；accept 在单一事务中创建 Decision、Mandate、Ticket、订阅、输入和审计记录。accept 的 direction.evidence 与 intent_evidence 必须是同一条 Gate 放行 Trigger 正文或标题中的原文片段，不能以模型自述替代来源事实；Proposal 和 Policy 字段及快照有明确大小上限。Gate 同时检查 Thread 生命周期：GitHub close 事件仅更新状态，已关闭 Thread 上的新消息不会进入评估或 fanout；关闭来源主 Thread 会在同一事务中撤销相关 Mandate。SourceInstance 停用或配置修订变化也会在配置事务中立即撤销该来源的 Mandate；SourceKind 卸载、宿主启动时缺失或 API 版本不匹配同样会撤销 Mandate、取消在途 Evaluation 并过滤待评估 Trigger。每个新 Trigger 在复用既有 Ticket Subscription 前重新执行当前 Gate；只有主 Thread 命中 work Subscription 才能形成 work fanout，相关 Thread 命中保持 context-only，work 与 context 投影分别记录；Policy 变化可触发 Mandate revalidation。
- 授权宿主 API 提供 PolicyRevision、Evaluation、Decision、Mandate 的有界只读历史查询；Evaluation 详情包含冻结 Policy 与逐 Trigger Gate 快照，Mandate 详情包含追加式状态事件。
- `ShadowComparisonService` 只读对照 v1 Inbox / Router 与 v2 来源事实和决策；决策比较按每个 Trigger 的 Gate 结果映射，Gate 拒绝项按 ignore 对照，并报告新旧两侧未匹配项及有界样本是否截断，不写任一账本、不执行 Ticket。

Phase 3 已实现的单 Worker 执行闭环：

- `CoordinatorExecutionService.route_trigger` 仅在调用方显式执行时，将已接受 Trigger 映射为一个 Mission、不可变单节点 PlanRevision、Work Job 和 Assignment；Assignment 使用稳定 submission key，避免重复提交。
- `HarnessExecutorAdapter` 为 Worker 创建并核验专属 Worktree / Session，绑定不可变 Worker Profile、工具白名单与来源能力；Session metadata 与持久 Worker 账本双向核验，不能仅凭 Session 元数据取得 Worker 身份。
- Run 提交结果不确定时只按原 submission key 回查，不盲目重提；重启后按原 Run ID 恢复。`HarnessEvidenceProvider` 从 Harness 原生 Run / Tool / Background Job 事件日志只读提取证据，不复制或伪造 Runtime 历史。
- Mandate 的 max_turns / max_seconds 约束进入 Assignment；时间预算 Watchdog 只尝试取消精确匹配的原生 Run，取消无法确认时将 Assignment 标为 unknown，禁止把不确定结果当作成功。排队但尚未启动的原生 Run 可按原 Run ID 移除。
- TicketInput 可按幂等 delivery key 送入当前 Assignment；交付前重新核验 Ticket、Mandate 和 Assignment。context-only 输入不启动执行；Handback 必须指向实际交付给该 Assignment 的非源始 TicketInput，无法验证时转为 Attention。
- Worker Report 先冻结并校验内容哈希，再幂等完成 Mission、Attention、Handback 和 Outbox Candidate；进程中断后可从冻结 Report 恢复后续效果。Outbox Candidate 创建时再次检查 Ticket 状态、Mandate 和 reply Scope，不把 Candidate 误报为已发送。
- Schema v13 为 Worker 与 Attention 聚合补齐 `updated_at`，并将旧行回填为 `created_at`；迁移覆盖 v1–v12 的升级路径。`tests/test_superagent_execution.py` 覆盖原 Run 恢复、报告效果幂等、后续输入 Handback、Mandate 撤销、预算 Watchdog、排队 Run 停止和并发去重。

后续阶段仍未实现或尚未验收：

- DAG 目前只验证结构、同版本依赖和环；多 Worker Job 的 Artifact Delivery applied、目标树核验、集成与 Review 完整生命周期仍待实现。
- EffectRecoveryService 提供同步与异步只读回查入口；OutboxProviderLookupAdapter 通过宿主注入的 Channel 与正文解析器分发到 GitHubIssueCommentLookupAdapter / FeishuThreadReplyLookupAdapter。两种适配器仅查询 Provider 资源 GET，只有在完整有界扫描中找到唯一、原幂等键标记和发送主体均匹配的远端对象才返回 applied；未找到、重复匹配或分页不完整均保持 indeterminate，不把缺失结果解释为安全重试。GitHub / 飞书列表接口不提供权威的“未执行”回执，因此这两种适配器不会返回 not_applied / permanent_rejection；retry_wait 仍必须由支持原操作状态查询的 Provider 明确证明。
- Phase 3 只提供单 Worker Report / Attention / Handback / Outbox Candidate 闭环；尚无 V2 Outbox Sender、生产 Channel 注册表 / 正文 Artifact Resolver 接线或周期性恢复 Worker。Outbox Candidate 不等于已发送。
- CoordinatorHost 的 Source / Authorization / Execution API 均须显式启用；尚未接入现有 Router、MissionStore、Dispatcher、Worker 或 Harness Runtime 的生产默认运行链，不会自动创建生产账本、迁移旧数据库、双写或切换 CLI / HTTP 路由。
- V2 CoordinatorHost 尚未接入独立 MissionControl Runtime；当前 `control_allows` 在未配置 V2 控制适配器时沿用旧系统“无控制器则允许”的默认行为。生产接入前必须实现持久化的 V2 Mission 暂停 / 取消 / 审批控制，并将其实际决策接入状态转移，不能继续把默认允许当作控制能力已交付。
- 推断自动接受仅在具体 Source 策略显式启用 `auto` 且 Gate、规则、意图与 Scope 全部通过时可用；它不会因此切换生产默认路由。远程 Worker 与 OS Sandbox 尚未启用。

因此，Phase 1 按持久化基础设施、状态机基础和宿主隔离范围完成；Phase 2 的来源事实、授权闭环和只读 Shadow 已交付；Phase 3 的单 Worker 执行、原 Run 恢复、冻结 Report 与后续效果恢复已在显式 CoordinatorHost API 中实现。多 Worker DAG / Artifact Delivery、V2 MissionControl、生产 Channel 外发和新架构默认入口仍未交付；v1 生产默认路由保持不变。

## 10. 源码依据

### Harness

- harness/app/superagent_coordinator_store.py：v2 独立协调账本、Schema v1–v13 迁移、Revision、AuditEvent、Durable Effect 与 UOW。
- harness/app/superagent_coordinator_migration_13.py：Worker / Attention `updated_at` 字段及历史行回填。
- harness/app/superagent_coordinator_host.py：v2 宿主组合、活动操作租约、来源 / 授权 / Shadow / Execution API 与 v1 隔离。
- harness/app/superagent_execution.py：单 Worker Mission / Job / Assignment 编排、TicketInput、Run reconciliation、冻结 Report、Attention / Handback 与 Outbox Candidate。
- harness/app/superagent_executor.py：Harness 原生 Worktree / Session 适配、受限工具、Run / Tool / Background 只读证据和精确 Run 预算 Watchdog。
- harness/app/superagent_source_registry.py：SourceKind、SourceInstance、GitHub / Feishu / Local 规范化、Trigger Intake、Thread / Channel 和 Cursor。
- harness/app/superagent_authorization.py：PolicyRevision、Gate、Evaluation、Decision、Mandate、Ticket、Subscription、TicketInput 与审计查询。
- harness/app/superagent_shadow.py：v1 Inbox / Router 与 v2 来源事实及决策的只读对照。
- harness/app/superagent_inbox.py：当前 v1 Source、Task、Subscription、Trigger、Route、重复事件和正文冲突校验。
- harness/app/superagent_dispatch.py：稳定 submission key、原 Run 回查及 submitting / accepted / unknown。
- harness/app/superagent_router.py：批次、输入、研判提交及恢复。
- harness/app/superagent_workers.py：Worker Profile、Workspace、Session 资源绑定。
- harness/app/superagent_mission_store.py：当前 Mission / Job 表、计划哈希、状态和同事务快照读取。
- harness/app/superagent_missions.py：Plan、Work、Integrate、Review、Checkpoint、Artifact Capture 和修正轮次。
- harness/app/superagent_effect_recovery.py：Effect unknown 回查编排，支持同步与异步只读 Adapter，并以 generation fencing 防止迟到结果覆盖。
- harness/app/superagent_provider_adapters.py：GitHub Issue 评论与飞书线程回复的只读 Outbox 回查、Channel 身份和正文哈希核验。
- harness/app/superagent_mission_control.py：精确原 Run / Background Job 停止和未知收尾。
- harness/app/superagent_watchdog.py：活性指纹、stalled / blocked 观察。

### dsh-superagent 参考

- dsh-superagent-prototype/src/core/model.ts：ActorRef、ThreadRef、Trigger、Mandate、Evaluation、Decision、Ticket、Assignment、TicketInput。
- dsh-superagent-prototype/src/core/ports.ts：Policy、Assessor、Executor、Outbox 和 Repository Ports。

- dsh-superagent-prototype/src/core/engine.ts：事务入口、Intake、Evaluation、Ticket 和 Assignment 协调。
- dsh-superagent-prototype/src/app/store.ts：协调账本表与事务。
- dsh-superagent-prototype/src/app/effects.ts、outbox.ts：持久副作用与消息回执。
- harness/app/superagent_mission_control.py：精确原 Run / Background Job 停止和未知收尾。
- harness/app/superagent_watchdog.py：活性指纹、stalled / blocked 观察。

### dsh-superagent 参考

- dsh-superagent-prototype/src/core/model.ts：ActorRef、ThreadRef、Trigger、Mandate、Evaluation、Decision、Ticket、Assignment、TicketInput。
- dsh-superagent-prototype/src/core/ports.ts：Policy、Assessor、Executor、Outbox 和 Repository Ports。

- dsh-superagent-prototype/src/core/engine.ts：事务入口、Intake、Evaluation、Ticket 和 Assignment 协调。
- dsh-superagent-prototype/src/app/store.ts：协调账本表与事务。
- dsh-superagent-prototype/src/app/effects.ts、outbox.ts：持久副作用与消息回执。
- harness/app/superagent_mission_store.py：当前 Mission / Job 表、计划哈希、状态和同事务快照读取。
- harness/app/superagent_missions.py：Plan、Work、Integrate、Review、Checkpoint、Artifact Capture 和修正轮次。
- harness/app/superagent_mission_control.py：精确原 Run / Background Job 停止和未知收尾。
- harness/app/superagent_watchdog.py：活性指纹、stalled / blocked 观察。

### dsh-superagent 参考

- dsh-superagent-prototype/src/core/model.ts：ActorRef、ThreadRef、Trigger、Mandate、Evaluation、Decision、Ticket、Assignment、TicketInput。
- dsh-superagent-prototype/src/core/ports.ts：Policy、Assessor、Executor、Outbox 和 Repository Ports。

- dsh-superagent-prototype/src/core/engine.ts：事务入口、Intake、Evaluation、Ticket 和 Assignment 协调。
- dsh-superagent-prototype/src/app/store.ts：协调账本表与事务。
- dsh-superagent-prototype/src/app/effects.ts、outbox.ts：持久副作用与消息回执。
- harness/app/superagent_mission_control.py：精确原 Run / Background Job 停止和未知收尾。
- harness/app/superagent_watchdog.py：活性指纹、stalled / blocked 观察。

### dsh-superagent 参考

- dsh-superagent-prototype/src/core/model.ts：ActorRef、ThreadRef、Trigger、Mandate、Evaluation、Decision、Ticket、Assignment、TicketInput。
- dsh-superagent-prototype/src/core/ports.ts：Policy、Assessor、Executor、Outbox 和 Repository Ports。

- dsh-superagent-prototype/src/core/engine.ts：事务入口、Intake、Evaluation、Ticket 和 Assignment 协调。
- dsh-superagent-prototype/src/app/store.ts：协调账本表与事务。

- harness/app/superagent_workers.py：Worker Profile、Workspace、Session 资源绑定。
- harness/app/superagent_mission_store.py：当前 Mission / Job 表、计划哈希、状态和同事务快照读取。
- harness/app/superagent_missions.py：Plan、Work、Integrate、Review、Checkpoint、Artifact Capture 和修正轮次。
- harness/app/superagent_effect_recovery.py：Effect unknown 回查编排，支持同步与异步只读 Adapter，并以 generation fencing 防止迟到结果覆盖。
- harness/app/superagent_provider_adapters.py：GitHub Issue 评论与飞书线程回复的只读 Outbox 回查、Channel 身份和正文哈希核验。
- harness/app/superagent_mission_control.py：精确原 Run / Background Job 停止和未知收尾。
- harness/app/superagent_watchdog.py：活性指纹、stalled / blocked 观察。

### dsh-superagent 参考

- dsh-superagent-prototype/src/core/model.ts：ActorRef、ThreadRef、Trigger、Mandate、Evaluation、Decision、Ticket、Assignment、TicketInput。
- dsh-superagent-prototype/src/core/ports.ts：Policy、Assessor、Executor、Outbox 和 Repository Ports。

- dsh-superagent-prototype/src/core/engine.ts：事务入口、Intake、Evaluation、Ticket 和 Assignment 协调。
- dsh-superagent-prototype/src/app/store.ts：协调账本表与事务。
- dsh-superagent-prototype/src/app/effects.ts、outbox.ts：持久副作用与消息回执。
- harness/app/superagent_mission_control.py：精确原 Run / Background Job 停止和未知收尾。
- harness/app/superagent_watchdog.py：活性指纹、stalled / blocked 观察。

### dsh-superagent 参考

- dsh-superagent-prototype/src/core/model.ts：ActorRef、ThreadRef、Trigger、Mandate、Evaluation、Decision、Ticket、Assignment、TicketInput。
- dsh-superagent-prototype/src/core/ports.ts：Policy、Assessor、Executor、Outbox 和 Repository Ports。

- dsh-superagent-prototype/src/core/engine.ts：事务入口、Intake、Evaluation、Ticket 和 Assignment 协调。
- dsh-superagent-prototype/src/app/store.ts：协调账本表与事务。
- dsh-superagent-prototype/src/app/effects.ts、outbox.ts：持久副作用与消息回执。
- harness/app/superagent_mission_store.py：当前 Mission / Job 表、计划哈希、状态和同事务快照读取。
- harness/app/superagent_missions.py：Plan、Work、Integrate、Review、Checkpoint、Artifact Capture 和修正轮次。
- harness/app/superagent_mission_control.py：精确原 Run / Background Job 停止和未知收尾。
- harness/app/superagent_watchdog.py：活性指纹、stalled / blocked 观察。

### dsh-superagent 参考

- dsh-superagent-prototype/src/core/model.ts：ActorRef、ThreadRef、Trigger、Mandate、Evaluation、Decision、Ticket、Assignment、TicketInput。
- dsh-superagent-prototype/src/core/ports.ts：Policy、Assessor、Executor、Outbox 和 Repository Ports。

- dsh-superagent-prototype/src/core/engine.ts：事务入口、Intake、Evaluation、Ticket 和 Assignment 协调。
- dsh-superagent-prototype/src/app/store.ts：协调账本表与事务。
- dsh-superagent-prototype/src/app/effects.ts、outbox.ts：持久副作用与消息回执。
- harness/app/superagent_mission_control.py：精确原 Run / Background Job 停止和未知收尾。
- harness/app/superagent_watchdog.py：活性指纹、stalled / blocked 观察。

### dsh-superagent 参考

- dsh-superagent-prototype/src/core/model.ts：ActorRef、ThreadRef、Trigger、Mandate、Evaluation、Decision、Ticket、Assignment、TicketInput。
- dsh-superagent-prototype/src/core/ports.ts：Policy、Assessor、Executor、Outbox 和 Repository Ports。

- dsh-superagent-prototype/src/core/engine.ts：事务入口、Intake、Evaluation、Ticket 和 Assignment 协调。
- dsh-superagent-prototype/src/app/store.ts：协调账本表与事务。
- dsh-superagent-prototype/src/app/effects.ts、outbox.ts：持久副作用与消息回执。
