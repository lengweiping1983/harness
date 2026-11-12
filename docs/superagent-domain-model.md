# Harness Superagent 统一领域模型与数据契约

> 文档状态：统一开发契约；Phase 1–6 当前实现边界见第 9 节。\
> 文档性质：目标领域模型，不代表当前代码已经实现。\
> 后续开发唯一入口：[Harness Superagent 统一架构与开发计划](superagent-next-generation-architecture.md#开发执行规则)。\
> 关联规范：[状态机与合法转移](superagent-state-machines.md) · [架构决策与接口协议](superagent-architecture-decisions.md)。\
> 初始领域设计参考（历史）：Harness 1b506de；dsh-superagent-prototype 8c32a9d。当前实现以第 9 节及实际源码为准。

## 1. 目的与规范用语

本文冻结下一代 Superagent 的领域名词、身份边界、对象所有权、字段语义、关系和持久化映射。后续数据库 DDL、Repository、API、事件协议及 Coordinator Schema 演进必须以本文及配套状态机为准；不开发旧 Superagent 数据导入或兼容运行工具。

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

source_instance_id, kind, kind_api_version, enabled, config_json, config_revision, identity_hash, created_at, updated_at, health_json。

凭据只保存 Secret Reference，不保存 Token 明文。配置修订变更不能悄悄改变已接收 Trigger 的身份解释。统一目标模型不包含旧版来源映射；历史 Schema 的 legacy_source_id 列仅为保持迁移完整性保留，当前没有映射 API、Shadow 或运行消费者，不进入新配置或新来源身份。已有迁移校验和与持久原件仍受保护，不能为清除依赖而改写历史迁移。

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

**InputDelivery** 是一条 TicketInput 投递给一个特定 Assignment 的独立消费记录。唯一身份为 (input_id, assignment_id)，至少包括 delivery_id、input_id、assignment_id、delivery_key、state、receipt_json、aggregate_revision、created_at 和 updated_at。同一 Input 可以有多个 Assignment 的独立 InputDelivery；TicketInput.delivered_to_assignment_id 只保留单 Worker 路径的单目标投影，不是多节点消费的权威状态。

状态按 pending → submitting → accepted → applied / unapplied 推进；提交或回执不确定时进入 unknown，明确的提交前拒绝可进入 rejected。submitting、accepted 和 unknown 只能按原 delivery_key 回查原生 request_id，不能重新投递。回执信封绑定 delivery_id、input_id、assignment_id 和 delivery_key，并校验原生 request_id、Turn、accepted_seq 与 applied_seq；只有该 Assignment 绑定的原始活动 Run / Session 可以接受输入。

**Attention** 是等待人作决定的问题，字段至少包括 attention_id, ticket_id, mission_id, trigger_id, question_ref, options_json, state, answer_trigger_id, created_at, resolved_at。Attention 是人工协作状态，不是一个新的授权来源。

### 5.8 Mission、PlanRevision、Job 与 Assignment

**Mission** 是 Ticket 下的一次有界交付活动。一个 Ticket 可以顺序创建多个 Mission，但默认同一时刻只能有一个活动或尚未核实的 Mission。

Mission 字段至少包括：

mission_id, ticket_id, sequence, state, outcome, goal_ref, policy_version_at_start, source_revision_at_start, plan_revision, repo, base_commit, round_no, budgets_json, created_at, updated_at, aggregate_revision。

初期一个 Mission 只允许一个 Git Repository 和一个冻结 base_commit。跨仓库交付拆成多个 Mission 或由明确的上层流程协调。

**PlanRevision** 是不可变的 DAG / Acceptance 快照，字段至少包括：

mission_id, plan_revision, plan_json, plan_hash, acceptance_json, acceptance_hash, created_at, created_by_kind, created_by_ref。

created_by_kind = host | human | assignment；created_by_ref 指向对应 Actor 或生成该版本的 Planner Assignment。由 Planner 产生的 PlanRevision 必须引用其 Assignment。

计划中的每个节点必须有稳定 node_id、role、profile_id、指令引用、依赖和资源范围。node_id 在同一 Mission / PlanRevision 内唯一；模型规划的业务节点不得使用保留前缀 __system__。宿主生成的 plan / integrate / review 节点使用保留身份，其中规划节点固定使用 plan_revision=0、node_id=__system__plan，真实 PlanRevision 从 1 开始；集成和复核逻辑节点固定使用 __system__integrate / __system__review。有限修正沿用原 PlanRevision 和 Job，推进 round_no 并创建新 Assignment；不能每轮追加节点、改写依赖或重建计划。动态重规划不是 Phase 4 的隐式能力，后续若另行实现，必须明确授权并创建新 PlanRevision。

**Job** 是 PlanRevision 中的逻辑节点，不代表一次执行。角色至少包括 plan | work | integrate | review。字段至少包括：

job_id, mission_id, plan_revision, node_id, role, profile_id, instruction_ref, instruction_hash, state, spec_hash, result_hash, aggregate_revision。result_hash 按角色绑定最终结果：plan 为 PlanRevision hash，work / integrate 为 Artifact manifest / tree hash，review 为 Verdict 与 Evidence Set hash。

依赖使用 job_dependencies(job_id, depends_on_job_id, ordinal, requires_delivery) 关系表，不只存在于 JSON。每条依赖必须连接同一 mission_id 和 plan_revision 的 Job；Schema Contract 通过复合外键或等效事务校验拒绝跨 Mission / PlanRevision 依赖、自依赖和环。requires_delivery 标记该依赖边是否必须导入 Artifact；requires_delivery=true 用于消费上游文件 / Git 交付的边，false 用于仅依赖逻辑结果的边。下游 Job 只有在依赖 Job done 且所有 requires_delivery=true 的对应 Delivery 均 applied 并核验目标树后才可 ready；checkpointed 不算完成。

**Assignment** 是 Job 的一次真实执行尝试。获准的新执行、Checkpoint 接续及有界修正创建新 Assignment；原操作回查继续使用原 Assignment / Run，不因超时、失败或 unknown 自动重派。字段至少包括：

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

**WatchdogSnapshot** 持久保存 mission_id、fingerprint、ledger_fingerprint、activity_baseline_trusted、state、aggregate_revision、last_progress_at、observed_at；**WatchdogAssignmentCursor** 按原 Assignment / Run / Session 记录 last_event_seq、last_event_time、last_event_type、aggregate_revision、last_progress_revision。Schema v18 持久化每个原生 Run / Session 的单调游标；Schema v19 增加由 SQLite 视图投影的 Mission / Job / Assignment 账本事实，快照逐字绑定该事实并校验 SHA-256，只有完整活动快照才能更新账本基线；Watchdog 仅以已确认账本进度或完整且单调的原生 Run 活动观察恢复 stalled / blocked；confirmed 检查必须绑定非空且持久化的原生 Run / Session 身份，缺失身份视为不可信观察，不建立游标；全部活跃 Run 的身份、游标须先完整核验，单个 Run 缺失、倒退、同序号内容冲突时不得提前消耗其他 Run 的新回执。Schema v17 的首次可信观察仍只建立基线，不刷新进度或解除阻断。Watchdog 仅观察活性，不自行重放 Assignment 或 Effect；此保守策略可能推迟其他已进展 Run 的状态恢复，须在下一次完整观察后重新确认。

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
| Watchdog | watchdog_snapshots | mission_id | 原生活动与账本进度指纹、可信基线与停滞状态 |
| Watchdog | watchdog_assignment_cursors | (mission_id, assignment_id)；assignment_id 唯一 | 绑定原 Run / Session 的持久单调事件游标 |
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
- watchdog_assignment_cursors 插入必须绑定同 Mission 的活动原始 Assignment / Run / Session；更新不得改写身份、回退或重放事件序号、跳跃 Revision，且原 Assignment 必须仍活动；游标历史不可删除。CoordinatorWatchdog 在同一写事务保存多 Run 游标与 Snapshot，任一游标写入失败整体回滚；Schema v18 DDL 迁移失败必须回滚新增列、表和迁移记录；Schema v19 账本事实视图、快照列和触发器迁移失败也必须整体回滚。
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

## 8. 统一开发与能力归属

功能进入哪一统一模块、接续顺序和完成门禁，统一维护在[总体计划第 10–11 节](superagent-next-generation-architecture.md#10-能力的唯一归属与数据边界)。不再提供旧任务／旧账本导入映射，不将旧 Router、Autonomy 或 MissionStore 作为并行开发路线。

旧实现仅用于提取可复用原语、识别待改调用方并迁接有效业务回归。新增能力只进入统一协调账本、应用服务和原生 Harness 执行边界；接线缺失不能通过调用旧编排补足。

## 9. Phase 1–6 当前实现边界

Phase 4.1–4.6 的统一链路已接通：CLI 和嵌入端口装配同一个 SuperagentService，SourceRegistry、Gate / Evaluation、Mandate / Ticket、Mission / PlanRevision、CoordinatorDagScheduler、原生 Executor、Artifact / Delivery / Review、控制及 Outbox 共用单实例协调账本。生产默认入口不再选择旧 Router / Autonomy，也不装配 Shadow 或旧来源映射。

当前配置、CLI、来源协议、质量门、阶段接续、持续协作及关闭边界的完整行为统一维护在[产品页](superagent.md)。实现和验收范围见[开发计划](superagent-next-generation-architecture.md#phase-46唯一入口收口与旧编排清除)及[审查证据](superagent-review.md#phase-46入口收口与旧编排退役的当前证据)。表清单描述领域结构；当前具体列、约束及迁移以 CoordinatorStore 和各迁移源为准。

Schema v21 为原生协调模型调用、本机事件身份、本机指令请求和原回复目标增加同库记录。原生调用使用固定 Session / Run / prompt hash，丢回执后只查原提交；本机指令固定 Assignment / Run / Turn，不经普通跟进广播。只读 CLI 使用现有 Schema 和实例身份，不初始化、迁移或修复用户状态。已有迁移及原件保持完整；意外发现旧运行目录时拒绝混用，不导入、转换或清理。

Phase 5 已提供同库分页／任务详情、本机工作台、人工原操作只读核实、来源／策略／回复授权管理，以及 GitHub Review／CI 上下文和飞书媒体接收／呈现。操作者请求回执与媒体摘要进入同一协调账本，固定请求身份及原消息来源，不复制业务状态。CLI、工作台与可信宿主使用相同查询和领域操作；完整行为与限制见[产品页](superagent.md#统一工作台)。

Phase 6 的 Schema v23 增加不可变 remote_devices 和 remote_mission_leases，Mission 的原设备、代次和撤销状态进入同一协调账本。设备仅保存原生资源及操作回执；Session / Run / 工具 / 工作树仍归该设备的 Harness。远程适配复用同一 Executor Port，固定 Mission 的设备，未知或租约过期不产生替代执行；来源失效停止续租并撤销设备准入。隔离、代理、握手、部署和恢复完整说明见[产品页](superagent.md#隔离与远程-worker)。

当前仍限单实例和单仓库 Mission；多设备分配以 Mission 为单位，不支持跨设备 DAG、自动故障转移或跨设备数据迁移。动态 Source Plugin、自动跨来源关联、动态重规划和多人工作台属后续扩展。支持 1–32 台设备，显式 native 可信宿主方式不依赖 Docker，单台真实 CLI 已验证完成及重启原 Run 回查。设备满载在创建 Worker 前准入；Docker／物理多机／跨平台按所选场景单独验收，不能把原生执行或替身当作 OS 隔离证明。离线回归也不代表在线模型质量、外部账号或长期负载验收。

## 10. 源码依据

- `harness/superagent_cli.py`、`superagent_bootstrap.py`、`superagent_config.py`：唯一 CLI、当前配置和原生宿主装配。
- `superagent_service.py`、`superagent_coordinator_host.py`：周期处理、恢复、所属操作与实例锁生命周期。
- `superagent_coordinator_store.py` 及迁移模块：事务、CAS、审计、效果队列和数据库守卫。
- `superagent_source_registry.py`、`superagent_authorization.py`：来源原文、身份、Gate、授权、Ticket 和 Subscription。
- `superagent_models.py`：无工具原生 Assessor / Planner、冻结提案与原提交恢复。
- `superagent_execution.py`、`superagent_dag_scheduler.py`、`superagent_executor.py`：Mission、调度、独立资源和原 Run 证据。
- `superagent_remote_client.py`、`superagent_remote_gateway.py`、`superagent_remote_node.py`、`superagent_remote_leases.py`：同端口远程传输、原生回执、设备归属及撤销；`harness/superagent_worker.py` 为设备入口，`superagent_native_runner.py` 持有可信宿主原生入口，`superagent_sandbox.py`、`superagent_process_isolation.py`、`superagent_secret_broker.py` 持有所选执行方式及凭据生命周期。
- `superagent_artifacts.py`、`superagent_quality_gate.py`、`superagent_checkpoints.py`：文件交付、独立 Review 和阶段协议。
- `superagent_coordination.py`、`superagent_input_delivery.py`、`superagent_mission_instruction.py`、`superagent_watchdog.py`：控制、逐执行输入、指令和持久监督。
- `superagent_delivery_runtime.py`、`superagent_provider_adapters.py`、`superagent_outbox.py`、`superagent_effect_recovery.py`：发送准入、原回复目标、确认及未知效果只读恢复。
- `superagent_local.py`、`superagent_state.py`、`superagent_status.py`、`superagent_queries.py`：同库本机操作、运行摘要、分页查询和任务详情。
- `superagent_workbench.py`、`superagent_operator.py`：主宿主持有的工作台运行、固定操作回执和原效果核实；HTTP／UI 消费者分别位于 `harness/web/api/superagent.py` 与 `web/src/components/overlays/SuperagentPanel.tsx`。
- `superagent_github_events.py`、`superagent_media_content.py`、`superagent_media.py`：来源上下文、媒体引用与当前来源复核的资源生命周期。

除明确标出的 CLI、HTTP 和 UI 消费者外，以上 Python 模块均在 `harness/app/`；语言源在 `harness/i18n/locales/`，原生 Session / Run / Worktree 事实仍归各自模块。
