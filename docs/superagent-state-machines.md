# Harness Superagent 统一状态机与合法转移

> 文档状态：统一 Superagent 的目标状态契约；后续开发按[唯一开发计划](superagent-next-generation-architecture.md#开发执行规则)执行。\
> 本文定义目标状态机，不代表当前代码已实现这些状态。\
> 关联：[领域模型与数据契约](superagent-domain-model.md) · [架构决策与接口协议](superagent-architecture-decisions.md) · [总体架构](superagent-next-generation-architecture.md)。

## 1. 状态机总原则

1. 每个状态只表达一个对象的生命周期；不要把 Ticket、Mission、Job、Assignment 的状态放在同一字段。
2. state 是持久化生命周期；面板 stage 是从多个对象和原始回执派生的只读投影。
3. 每次状态变更必须由宿主执行、校验前置条件、CAS 更新并追加 AuditEvent。
4. 模型输出是 Proposal / Report，不是状态转移命令。状态转移由 Application Service 根据原始事实完成。
5. unknown 表示副作用可能已发生但无法确认；它不是普通失败、不是可重试错误，也不是成功。
6. 状态读取不触发执行、重试、补发、停止或隐式修复。
7. 事务内只做校验、CAS、审计和 Effect 入队；模型、Harness Runtime、Git、Source Provider 和文件系统操作在事务外。
8. 每个外部操作使用稳定 operation key；超时或进程重启后先回查原操作。

## 2. 状态分类

| 对象 | 持久化状态 / 结果 | 说明 |
|---|---|---|
| Intake Receipt | accepted / duplicate / conflict / rejected | 一次接收调用的结果，不是 Trigger 的生命周期 |
| Trigger | 不可变，无可变状态字段 | 通过 Disposition Events 记录路由和处理事实 |
| Evaluation | queued / running / decided / unavailable / cancelled | 一个 Thread 的固定输入批次 |
| Decision | ignore / accept / reopen / stop / unavailable | 一次研判结果 |
| Mandate validity | valid / affected / revoked / expired | 派生状态，由 Mandate Events 和当前 Policy 核验 |
| Ticket | open / ended | 长期授权工作单元 |
| Mission | planning / running / integrating / reviewing / cancelling / completed / blocked / unknown / rejected / suppressed / cancelled | 一次有界交付活动 |
| Job | pending / ready / running / checkpointed / done / blocked / unknown / cancelled | DAG 逻辑节点 |
| Assignment | pending / submitting / accepted / running / capturing / stopping / succeeded / failed / interrupted / rejected / unknown / cancelled | 一次实际执行尝试 |
| InputDelivery | pending / submitting / accepted / applied / unapplied / unknown / rejected | 一个 TicketInput 对一个 Assignment 的独立消费事实 |
| Delivery | planned / applying / applied / conflict / unknown / cancelled | Artifact 导入 |
| Effect | pending / leased / retry_wait / completed / failed / unknown / cancelled | 持久副作用 |
| OutboxMessage | pending / sending / retry_wait / sent / dropped / withdrawn / unknown | 外部消息交付 |
| ControlCommand | queued / applying / applied / rejected / unknown | 单条 pause / resume / cancel 命令 |
| MissionControl | running / paused / cancelling / cancelled | Mission 当前控制投影 |
| WatchdogSnapshot | healthy / stalled / blocked | 活性观察，不等于 Mission 状态 |
| Attention | open / answered / dismissed / expired | 人工待决问题 |

Mission.unknown 是调度静止但尚未核实的状态，仍占用该 Ticket 的活动 Mission 名额。它必须先通过原操作回查或人工核实，不能创建第二个 Mission 掩盖它。

## 3. Intake、Trigger 与 Evaluation

### 3.1 Intake 接收结果

| 当前事实 | 接收结果 | 宿主动作 |
|---|---|---|
| 新身份、签名和 Source Scope 均有效 | accepted | 原子写 Trigger、Thread 关联、Disposition、Fan-out / Eval Effect |
| 已存在相同 SourceInstance + external_event_id，内容哈希相同 | duplicate | 返回原 Trigger 和原接收回执，不重复派工 |
| 已存在相同身份但内容哈希不同 | conflict | 保留原件，追加安全审计，不覆盖、不派工 |
| 签名、租户、身份、Source 或 Scope 不通过 | rejected | 按安全策略拒绝；必要时保留有限审计元数据，不把未经验证正文作为可信 Trigger |

Source Adapter 必须在调用 Domain Intake 前验证外部签名和租户身份。当前 SuperagentInbox.receive() 明确假设调用方已经验证来源身份和指向性；下一代不能把这个假设误写成 Inbox 自身已经完成签名验证。

### 3.2 Trigger Disposition

Trigger 本体不转状态。Disposition 通过事件追加：

- source.filtered
- evaluation.queued
- evaluation.started
- ticket.fanout
- ticket.context_only
- ticket.owns_followup
- ticket.handback
- evaluation.requeued
- decision.recorded

同一 Trigger 可 Fan-out 给多个 Ticket，但每个 Ticket 只收到一次 Input。Context-only 不构成 Work Ownership。人类消息只要仍由一个 Work Ticket 决定，就不再同时送入新 Evaluation；只有没有 Ticket 拥有它，或所有 Ticket 明确 handback，才进入新的 Evaluation。

### 3.3 Evaluation

~~~mermaid
stateDiagram-v2
  [*] --> queued
  queued --> running: Gate passed and candidate frozen
  queued --> cancelled: Source revoked before assessment
  running --> decided: Valid Decision committed
  running --> unavailable: Bounded timeout or assessor unavailable
  running --> cancelled: Explicit host shutdown before delivery
  unavailable --> queued: Bounded requeue with same Trigger batch
  unavailable --> [*]: Retry budget exhausted / human dismissal; set completed_at
  decided --> [*]
  cancelled --> [*]
~~~

合法转移与守卫：

| 转移 | 必须满足 | 副作用 |
|---|---|---|
| queued → running | Thread 无其他活动 Eval；Policy Snapshot 固定；Gate 通过 | 固定 Trigger Batch、Actor、Basis、Candidate Hash 和 deadline |
| running → decided | Assessor Proposal 已返回；Eval ID、Thread Revision、Policy Version 仍匹配 | 宿主验证 Proposal，写 Decision；必要时创建 Mandate / Ticket |
| running → unavailable | 超时、Assessor 不可用或返回不可核验结果 | 记录原因和尝试；不伪造 ignore / accept |
| unavailable → queued | 未超过有限重排次数，原输入仍可用，Policy 允许 | 保持 completed_at 为空，使用原批次重新排队；不扩大输入 |
| queued/running → cancelled | 只在明确取消且未留下未知外部操作时 | 保留历史，不创建 Ticket |

Gate 必须先于模型调用。模型调用期间到达的新 Trigger 进入下一批。Policy 或 Thread Revision 在模型返回时已变化，旧 Proposal 作废，原输入重新排队或进入人工核实，不能按旧策略接受。

### 3.4 Decision

- accept：宿主确认授权后创建不可变 Mandate 和 Ticket。
- reopen：仅恢复已有 Ticket；原 Mandate 必须仍有效，且请求范围不扩大。
- ignore：已作出不接单判断，不产生执行授权。
- stop：只接受符合策略的明确人类停止请求；Bot、引用文本和推断内容不能停止或撤销他人授权。
- unavailable：研判未能可靠完成，不等于 ignore。

## 4. Mandate 有效性

Mandate 本体不修改。有效性是按原 basis_json 对当前 Policy 重新核验的派生状态：

| 派生状态 | 触发 | 允许行为 |
|---|---|---|
| valid | 当前 Gate 仍承认原授权依据 | 可在原 Scope 内恢复、派工和发送 |
| affected | Policy 规则、指令或 Admission 发生相关变化，但尚未证明原授权无效 | 写 policy_changed Input；新提交边界重新核验 |
| revoked | 原主体、来源或 Gate Basis 已不再具备授权资格，或人工明确撤销 | 禁止新的 Assignment、Delivery 和 Outbox 发送；原已接受 Run 仍须确切收尾 |
| expired | 宿主规则确认授权期限已过 | 与 revoked 相同，不再启动新的副作用 |

Policy 更新不自动等于 Mandate 撤销。重新核验必须引用原 Policy Version、原 Gate Basis 和新 Policy Version。若不确定，保持 affected / unknown 并请求人工处理。

## 5. Ticket

~~~mermaid
stateDiagram-v2
  [*] --> open: Mandate accepted
  open --> ended: Worker outcome exists and followsDone becomes true
  open --> ended: Host confirms non-recoverable failure; no unknown operation
  open --> ended: Human stop
  open --> ended: Policy revoked / expired
  ended --> open: Eligible human follow-up + Mandate revalidated
  ended --> [*]: No valid resume path
~~~

Ticket 状态只表达 open | ended；Stage 从 Mission、Assignment、Attention、Subscription 和 Outbox 派生。

| 转移 | 守卫 | 结果 |
|---|---|---|
| 创建 → open | Decision.accept 和 Mandate 已在同一事务确认 | 创建 Ticket、Subscription、首个 Mission Effect |
| open → ended | Worker ready / dropped 报告已存在且 followsDone 成立；两类事实中最后到达的一方触发转移 | 写 outcome 和 ended_by；merged / closed 由 Thread Source Outcome 确定 |
| open → ended(failed) | Host 确认 Mission 不可恢复失败 / rejected，预算耗尽，无未决 Attention 且无 unknown 副作用 | outcome=failed，ended_by=executor/system；retryable blocked 或 unknown 不得走此转移 |
| open → ended(stopped) | 人类明确停止 | 撤销 Mandate；撤销未发送 Outbox；停止新派工 |
| open → ended(revoked) | Policy / Source 重新核验明确撤销 | 停止新副作用，保留原运行收尾 |
| ended → open | 人类消息来自 Work Subscription；Ticket 不是 human-stopped；Mandate 仍有效 | 追加 resumed Input；重新排队执行 |
| 任意状态 → 新 Ticket | 目标扩大、仓库范围变化或新授权主体 | 必须重新 Evaluation 并签发新 Mandate |

人类停止、Mandate 撤销或到期后不允许通过普通 follow-up 恢复。Ticket 恢复不自动等于新授权；新的 Scope 必须创建新的 Mandate / Ticket。

Ticket 的 Follow Condition：只考虑 source_state 不为 unsupported 的可跟踪 Thread；存在 own Thread 时，所有 own Thread 必须 closed；否则所有 work Thread 必须 closed。context-only Thread 不参与；参与判断的 Thread 为 unknown 时 followsDone=false；没有可跟踪 Thread 时 followsDone=true。Source Closure 存在 Thread 聚合，不在每个 Subscription 中重复保存。PR / Issue 先关闭时，Ticket 等 Worker ready / dropped；Worker 先报告时，Ticket 等最后一个必需 Thread 关闭；两类事实中最后到达的一方触发结束，并按 Source Outcome 选择 merged / closed。

## 6. Mission

### 6.1 状态图

~~~mermaid
stateDiagram-v2
  [*] --> planning
  planning --> running: Plan accepted and base commit frozen
  planning --> rejected: Invalid plan or planning budget exhausted
  planning --> blocked: Human decision required
  planning --> suppressed: Mandate no longer admits work
  planning --> unknown: Original planning submission uncertain
  planning --> cancelling: Cancel accepted
  running --> integrating: All work Jobs done and required Deliveries applied
  running --> blocked: Known execution / dependency failure
  running --> suppressed: Mandate revoked before next write
  running --> unknown: Assignment or side effect uncertain
  running --> cancelling: Cancel accepted
  integrating --> reviewing: Integration Artifact frozen
  integrating --> blocked: Merge conflict or known integration failure
  integrating --> unknown: Import / integration outcome uncertain
  integrating --> cancelling: Cancel accepted
  reviewing --> completed: Acceptance fully verified
  reviewing --> integrating: Bounded repair round authorized
  reviewing --> blocked: Review failed / budget exhausted / human decision
  reviewing --> unknown: Review evidence cannot be confirmed
  reviewing --> cancelling: Cancel accepted
  cancelling --> cancelled: All exact owned operations settled
  cancelling --> unknown: Stop or background work cannot be confirmed
  unknown --> planning: Reconciliation proves original operation did not start
  unknown --> running: Reconciliation proves original work settled and next step is safe
  unknown --> integrating: Reconciliation proves original delivery applied
  unknown --> reviewing: Reconciliation proves original integration Artifact exists
  unknown --> completed: Reconciliation proves Acceptance already satisfied
  unknown --> blocked: Reconciliation proves known failure / human decision required
  unknown --> rejected: Reconciliation proves original planning rejection
  unknown --> suppressed: Reconciliation confirms Mandate revoked before next write
  unknown --> cancelled: Reconciliation confirms all owned operations settled
~~~

unknown → phase 或终态都不是重试；必须由原 operation key、原 Run、原 Delivery 或 Provider Receipt 的回查证明原操作的真实结果，并追加 Reconciliation AuditEvent。每条 unknown 转移都必须提供非空 `reconciliation_ref`，并固定使用 `<aggregate>.reconciled.<target>` 事件类型（调用方传入的自定义 event_type 不覆盖该类型）；事件 payload 同时保存 from / to / revision、宿主核验的 facts 和结构化 evidence。只有原始事实已满足 Acceptance、明确失败、明确拒绝、授权撤销或所有所属操作确切收尾时，才可分别落到 completed、blocked、rejected、suppressed 或 cancelled；不允许根据模型报告推断。

Phase 1 的关键恢复证据契约：

- Mission `unknown → reviewing / completed`：保存 `reconciliation_ref`、当前 `mission_id / plan_revision`，以及原 Integrator Assignment ID、原 Run ID、succeeded 结果和 Run Evidence 引用；Integration Artifact 必须同时绑定同一 Integrator Assignment 与原 Run，并保存 artifact_ref、tree / manifest SHA-256 与 Evidence 引用。completed 还须由宿主核验 Acceptance 已满足；不得以该终态绕过 Integration / Delivery 原始证据。`deliveries` 必须逐一覆盖当前 PlanRevision 中所有 `requires_delivery=true` 依赖边，包含唯一 delivery_id、applied_tree_hash 和 Evidence 引用。所有 Work Job 必须为 done 且 result_hash 有效；不得存在活动 Assignment。
- Job `unknown → cancelled`：Mission 必须处于 cancelling / cancelled；所有 Assignment 均为终态；审计中的 `settled_assignments` 必须与账本中的 assignment_id、state、run_id 完全一致，并附带 reconciliation_ref 与 ownership_evidence_ref。
- Assignment `unknown → succeeded / cancelled`：审计证据中的 run_id 必须等于持久化的原始 run_id，并引用原 Run Evidence。succeeded 还须证明原 Run 已完成，report_json 的 output_ref / output_hash 与冻结输出证据一致；cancelled 还须有原 Run 取消回执和所属后台操作收尾证据。

Phase 1 尚未建立独立 Delivery 表；上述 Delivery 是按依赖边记录在 Reconciliation AuditEvent 中的宿主证据引用，不代表已经实现 Delivery 生命周期表。独立 Delivery 持久化属于 Phase 4。

### 6.2 合法转移矩阵

| 当前 | 目标 | 触发 / 前置条件 | 禁止事项 |
|---|---|---|---|
| planning | running | Plan Schema、DAG、Profile、Acceptance 和单仓库范围验证通过；base commit 已冻结 | 不允许部分接受 Plan |
| planning | rejected | 计划格式无效或规划预算耗尽 | 不得把未知提交当成拒绝 |
| planning | blocked | 需要人工补充范围或决策 | 不允许继续创建 Work Job |
| planning/running/integrating/reviewing | suppressed | Mandate / Source 明确不再允许新写操作，且所属 Assignment 已确切收尾 | 不删除已接受 Run 或 Artifact |
| 任一活动阶段 | unknown | 原提交、导入、捕获、停止或发送回执不确定 | 不新建替代 Assignment |
| unknown | reviewing | 原 Integration 操作已回查收尾；所有 Work Job done 且 result_hash 有效；无活动 Assignment；Integration Artifact 冻结，所有必需 Delivery 均有逐边 applied 证据 | 不以布尔事实代替 Artifact / Delivery 引用 |
| unknown | completed | 原操作回查证明 Acceptance 已满足；所有 Work Job done、必需 Delivery applied、Integration Artifact 冻结且哈希匹配 | 不重新运行 Reviewer 或重复交付 |
| unknown | blocked | 原操作回查证明已知失败或必须等待人工决定 | 不把未知状态直接当作可重试失败 |
| unknown | rejected | 原规划提交被明确拒绝且未开始执行 | 不把丢失回执当作拒绝 |
| unknown | suppressed | Mandate / Source 已明确撤销，且原操作回查确认没有尚未核实的写入 | 不删除已接受运行事实 |
| unknown | cancelled | 所属 Run、Background Job 和写入收尾均有确切终态 | 不用控制回执冒充退出证据 |
| running | integrating | 所有 Work Job 最终 done，且所有必需 Artifact Delivery 均 applied 并核验目标树 | Checkpointed 或仅有文本报告不满足 |
| integrating | reviewing | 所有 Work Job 与必需 Delivery 已结算；Integrator Assignment 已结束、Artifact Capture 已冻结、树哈希匹配 | 不以 Integrator 文本报告代替 Artifact |
| reviewing | completed | 每项 Acceptance 都有当前候选的有效 Evidence；所有 Work Job / Delivery 已结算、Integration Artifact 冻结且没有活动 Assignment | 不接受过期 Verdict 或损坏缓存 |
| reviewing | integrating | 修正授权及预算有效，round_no 推进，原计划／Job 下的新修正 Assignment 已明确生成 | 不无限循环复核 |
| 活动阶段 | blocked | 已知失败、冲突、预算耗尽或需人工决策 | 不将未知操作误标为 blocked |
| 活动阶段 | cancelling | 宿主接受 cancel 命令 | 不把控制模式已改成 cancelling 当成 Run 已停止 |
| cancelling | cancelled | 所属 Run、Background Job 和写入收尾均有确切终态 | 不用旧 PID、超时或控制回执冒充退出证据 |
| cancelling | unknown | 停止结果无法确认 | 不向未知旧操作再次发送停止命令 |

completed / blocked / rejected / suppressed / cancelled 对自动调度是终态。unknown 是静止待核实状态，不允许自动重放。若人类答复改变目标，优先创建新的 Mission；不能在原终态 Mission 上覆盖计划或证据。

## 7. Job 与 Assignment

### 7.1 Job

| 转移 | 守卫 | 说明 |
|---|---|---|
| pending → ready | 所有依赖 Job done；requires_delivery=true 的依赖边对应 Delivery 均 applied 并核验目标树；Mandate 有效且控制允许 | 可领取调度槽位 |
| ready → running | 新 Assignment 已受理并确认开始 | Assignment 保存原 Run 身份 |
| ready/running → checkpointed | 可续跑的 work / integrate Job：原 Assignment 已确切结束，Checkpoint 和 Artifact Capture 均确认；节点尚需下一阶段。Work 的 ready 路径只用于原 Run 已在首次 running 观察前完成，仍须相同冻结证据 | 不允许仍活动或 unknown 的 Assignment 悬挂在 checkpointed Job 上 |
| checkpointed → running | 创建新 Assignment，attempt_no + 1，原检查点和累计 Artifact 已导入 | 同一个逻辑 Job 的续跑 |
| running → done | 角色最终结果冻结并通过宿主校验：plan = PlanRevision；work / integrate = Artifact；review = Review / Verdict + Evidence Set | 标记逻辑 Job 完成；下游仍须等待 requires_delivery 对应 Delivery applied |
| pending/ready/running/checkpointed → blocked | 已知不可继续错误或需要人工 | 不自动伪造成功 |
| pending/ready/running/checkpointed → unknown | 原操作结果不确定 | 停止自动调度 |
| unknown → running | 回查证明原 Assignment 和原 Run 仍处于活动状态 | 不创建替代 Assignment |
| unknown → done | 回查证明原角色结果已冻结、哈希有效且宿主校验通过 | 不重复执行原 Job |
| unknown → blocked | 回查证明已知失败或需要人工决定 | 不把 unknown 直接当作 retryable |
| unknown → cancelled | Mission 已取消；证明没有外部副作用开始，或全部所属 Assignment 均已结算；审计中的 Assignment ID / state / run_id 清单与账本精确一致 | 不以 Mission 控制回执代替 Assignment 收尾 |
| pending/ready → cancelled | Mission 取消且 Job 尚未产生外部副作用 | 保留审计 |

Job done 是依赖满足的必要条件；requires_delivery=true 的依赖边还必须有 applied 且目标树核验通过的 Delivery，才可将下游 Job 置为 ready。checkpointed 只允许同节点继续。blocked / unknown 不得被调度器当作完成。

### 7.2 Assignment

~~~mermaid
stateDiagram-v2
  [*] --> pending
  pending --> submitting: Stable submission key persisted
  submitting --> accepted: Harness confirms acceptance
  submitting --> rejected: Explicit pre-execution rejection
  submitting --> unknown: Submission acknowledgement lost
  accepted --> running: Exact original Run observed active
  accepted --> capturing: Original Run settled
  accepted --> failed: Exact original Run failed
  accepted --> interrupted: Exact original Run interrupted
  accepted --> unknown: Original Run identity or outcome cannot be confirmed
  running --> capturing: Original Run settled
  running --> stopping: Host confirms exact cancel request accepted
  running --> failed: Original Run failed
  running --> interrupted: Original Run interrupted
  running --> unknown: Original Run cannot be confirmed; persist exact run_id
  capturing --> succeeded: Role-specific output and Evidence frozen; host validated
  capturing --> failed: Known capture or validation failure
  capturing --> unknown: Capture outcome uncertain; preserve original run_id
  stopping --> cancelled: Exact Run and owned background work settled
  stopping --> succeeded: Original Run completed before stop took effect
  stopping --> unknown: Stop outcome uncertain; preserve original run_id
  unknown --> accepted: Reconcile original submission / Run
  unknown --> running: Reconcile original Run active
  unknown --> capturing: Reconcile original Run settled
  unknown --> rejected: Prove original submission never started
  unknown --> succeeded: Reconcile confirms role-specific output frozen and validated
  unknown --> failed: Reconcile confirms original Run or finalization failed
  unknown --> interrupted: Reconcile confirms original Run interrupted
  unknown --> cancelled: Reconcile confirms exact Run and owned background work settled after stop
~~~

关键规则：

- submission_key 在调用 Harness 前先持久化；同一个 key 永远只代表同一个请求正文。
- accepted 是 Harness 已接收，不代表运行已完成。
- capturing 必须等待原 Run、工具结果配对和所属后台作业全部结算。
- succeeded 必须绑定角色对应的冻结结果并通过宿主核验：plan Assignment 绑定 PlanRevision，work / integrate Assignment 绑定 Artifact，review Assignment 绑定 Review / Verdict 与 Evidence Set；模型自报完成不能推进状态。
- failed / interrupted / rejected 只有在原事实明确时才能记录。
- unknown 只能查询原提交键和原 Run；不得新建 Run。回查可以将 Assignment 核实为 succeeded / failed / interrupted / cancelled / rejected 等确切结果；只有证明原提交未启动后，才可结束原 Assignment 并在预算内创建新 attempt。
- unknown → succeeded 必须把原 run_id、Run Evidence、冻结 output_ref / output_hash 写入 Reconciliation AuditEvent，并与 report_json 完全一致；同时要求原 Run 已完成及角色专属输出校验通过。
- unknown → cancelled 必须把原 run_id、Run Evidence、取消回执和后台所有权收尾证据写入 Reconciliation AuditEvent；原 Run 必须确切取消，不能只凭 cancel 命令已受理。
- 已知失败可在预算内创建新 Assignment；新 attempt 不覆盖旧 Assignment。
- Assignment 的停止只针对该 Assignment 实际拥有的 Run / Background Job，不按 Session ID 或旧 PID 猜测目标。

### 7.3 InputDelivery

唯一消费身份为 (input_id, assignment_id)。同一 TicketInput 可由多个 DAG Assignment 独立消费，每条记录有独立 delivery_id、稳定 delivery_key、Revision 和原生回执。TicketInput.delivered_to_assignment_id 只是旧单 Worker 路径的投影，不是多节点消费权威状态。

| 转移 | 守卫 |
|---|---|
| pending → submitting | 核验 Input / Assignment 同 Ticket、Mandate、Mission 控制及原活动 Run；先持久化稳定 delivery_key |
| pending → rejected | 明确的提交前拒绝 |
| submitting → accepted | 原生 request_id、Turn、accepted_seq 与原 delivery_key 匹配，状态为 pending |
| submitting → applied / unapplied | 原生回执直接证明原请求的应用结果 |
| submitting → unknown | 接受回执丢失、回查失败或回执无效；禁止重发 |
| submitting → rejected | 原生适配器明确证明副作用接受前拒绝 |
| accepted → applied / unapplied | 同一原生 request_id 的后续应用回执；applied_seq 必须大于 accepted_seq |
| unknown → accepted / applied / unapplied / rejected | 只回查原 delivery_key / request_id，重新核验身份和序号；禁止回到 submitting |

accepted 是已确认受理的单调事实，回查失败仍保持 accepted；unknown 是只读待核实状态，读取失败保持原状态。applied、unapplied、rejected 为终态。Handback 后不得开始新投递，但仍可回查既有投递的原回执。HarnessExecutorAdapter 只允许向 Assignment 绑定的 Worker / Workspace / Session / 原活动 Run 接受输入；身份或序号不匹配均保持 unknown，不进行重放。Schema v16 在 SQLite 层再次限制合法状态边、每次更新恰好递增一个 aggregate_revision、终态回执与 delivery / input / assignment 身份一致，并禁止删除投递历史；普通 SQL 更新不能绕过这些守卫。

### 7.4 MissionInstruction

唯一身份为 request_id；稳定原生操作键绑定 request_id 与 Assignment。payload_json / payload_hash 固定来源 Trigger、Source revision、Thread、Human Actor 和指令正文。MissionInstruction 额外绑定 Mission、Job、Assignment、原 Run 与原 Turn，并以 aggregate_revision 和 AuditEvent 记录每次状态变化。

| 转移 | 守卫 |
|---|---|
| pending → submitting | 单一数据库事务中写入不可变身份、payload hash、Revision 与 submitting 审计；数据库触发器核对 Mission / Job / Assignment / Run 关系 |
| submitting → accepted | 原生 request_id、Turn、正文、accepted_seq 与原操作键完全匹配；application 可为 pending / applied / unapplied |
| submitting → unknown | 接受回执丢失、回查失败或原生事件证据无效；禁止重新 steer |
| submitting → rejected | 原生适配器明确证明接受前拒绝 |
| unknown → accepted | 只回查原 request_id，并重新核对原 Run 事件、Turn、正文与序号 |
| accepted → accepted | 同一原生回执从 application=pending 单调推进到 applied / unapplied；只递增 Revision，不改变已受理事实 |

accepted 是原生已受理事实，application 单独描述是否已应用；任何回查失败或畸形回执都不能将 accepted 降级。unknown 只能查询原操作，不能回到 submitting，也不能在新 Run 上重放。指令来源必须属于同一 Ticket 的非 context-only TicketInput，Thread 与 Source 必须在当前 Mandate 范围内；HarnessExecutorAdapter 在原生 steering 锁内再次核验活动 Run 与 Turn。applied 必须有 accepted_seq 之后的同请求 user/message 事件；错误 Run、错误 Turn、错误正文或序号不一致均不构成应用证据。Schema v16 将严格 JSON 类型、原生 request_id / Turn / status / 序号和 Revision 单调性固化为数据库守卫，并禁止删除指令历史。

## 8. Delivery、Effect 与 Outbox

### 8.1 Delivery

| 转移 | 条件 |
|---|---|
| planned → applying | 导入计划、源 Artifact、目标 Workspace 和 idempotency key 已冻结 |
| applying → applied | 原目标树与实际 applied tree 核验一致 |
| applying → conflict | 明确发现内容冲突；源和目标原件保留 |
| applying → unknown | 写入可能已发生但回执不确定 |
| planned → cancelled | 尚未开始任何写入且 Mission 已取消 |
| unknown → applied/conflict/cancelled | 只通过原 key、原目标树和 Git 元信息回查；只有证明未写入且已取消才可 cancelled |

unknown 绝不自动回到 planned 或 applying。Schema v16 还要求 Delivery 插入的 Artifact 与目标 Assignment 属于同一 Mission / PlanRevision，且存在冻结的 requires_delivery DAG 边或同一 Job 的后续 Checkpoint Assignment；状态更新必须沿合法边并递增 Revision，已产生的交付历史不可删除。

### 8.2 Effect

| 转移 | 条件 |
|---|---|
| pending → leased | 事务中原子领取；写 lease owner、expiry 和 generation |
| leased → completed | Effect 回执确认完成 |
| leased → retry_wait | 明确可安全重试的失败；保留同一幂等键 |
| leased → failed | 明确失败且已耗尽允许的安全重试 |
| leased → unknown | 外部副作用可能已发生但无确定回执 |
| pending/retry_wait → cancelled | 可证明尚未启动，或业务明确撤回 |
| unknown → completed/failed/retry_wait | 回查原操作取得确切结果；只有证明外部副作用未发生且原 key 可安全复用才可 retry_wait |

租约过期不自动证明外部操作没有发生。恢复 Worker 必须先核对 generation、原回执和外部幂等能力。旧 Worker 的迟到回执不能覆盖新 generation。

### 8.3 OutboxMessage

~~~mermaid
stateDiagram-v2
  [*] --> pending
  pending --> sending: Outbox effect claimed
  pending --> withdrawn: Withdraw before send
  sending --> sent: Provider receipt confirmed
  sending --> retry_wait: Provider confirms safe retry
  sending --> dropped: Provider confirms target gone / permanent rejection
  sending --> unknown: Provider may have accepted but receipt missing
  retry_wait --> sending: Same provider idempotency key
  unknown --> sent: Reconcile original provider operation
  unknown --> dropped: Reconcile target gone / rejected
  unknown --> retry_wait: Reconcile proves no send occurred
~~~

每条 OutboxMessage 在创建事务中自动创建一个 `outbox.send` Durable Effect，并在 `outbox_effect_links` 中建立不可变的一对一关联。消息、Effect、关联和创建审计必须同事务提交；任一写入失败全部回滚。Outbox 的状态不能通过普通 Repository 独立修改，只能由 OutboxEffectService 同步推进。

每个 Unit of Work 固定一个 `correlation_id`；未显式提供时由宿主生成。领域事件的 `event_id` 作为其创建 Effect 的 `causation_id`。Effect 持久化 `correlation_id` 与 `last_event_id`，状态变化追加 `effect.enqueued / leased / completed / retry_wait / failed / unknown / cancelled`；恢复尝试还追加 `effect.lookup_unavailable / lookup_indeterminate / lookup_error`。每个回查观察事件都推进 `last_event_id`，并以之前的游标作为 `causation_id`。Outbox 投影事件以对应 Effect 事件为 `causation_id`，并推进 Effect 的 `last_event_id`，形成领域事件 → Effect 事件 → 恢复观察 / 业务投影事件的连续因果链。数据库拒绝不存在的因果事件以及跨 correlation 的因果引用。

领取时，Effect 从 pending / retry_wait 变为 leased，OutboxMessage 从 pending / retry_wait 变为 sending；两者的 generation、attempts、Revision 和 AuditEvent 在同一事务提交。Provider 使用 Outbox 原始 `idempotency_key` 作为稳定发送键；重试不得生成新键或改变消息正文哈希。通用 Effect Worker 不得领取 `outbox.send`，避免绕过业务投影。

业务回执与 Effect 结算映射：

| Effect 结果 | Outbox 结果 | 必须核验 |
|---|---|---|
| completed | sent | Provider Receipt 的 idempotency_key 与原消息一致；provider_id、sent_at 有效 |
| retry_wait | retry_wait | Provider Receipt 明确证明 retry_safe=true，且确认原幂等键 |
| failed | dropped | Provider Receipt 明确证明 permanent_rejection=true，且确认原幂等键 |
| unknown | unknown | 保留原错误；不允许发送新请求 |

Effect 与 Outbox 状态、回执字段和审计必须同事务提交。Effect 租约过期时，两者同时进入 unknown；恢复 Worker 只查询原 Provider 操作。适配器缺失追加 `effect.lookup_unavailable`，回查不确定或未证明可安全重试追加 `effect.lookup_indeterminate`，Adapter 异常或 Receipt 校验失败追加 `effect.lookup_error`；这些事件均保留 unknown 并推进因果游标。恢复报告中 `looked_up` 计 Adapter 调用尝试，`unavailable` 计缺失 Adapter，`errors` 计异常 / 非法回执，`left_unknown` 计本轮后仍为 unknown 的 Effect。旧 generation 的迟到回执不得覆盖新状态。

Provider 支持幂等发送时，重试必须复用原 message_id / provider_key。Provider 不支持幂等且不能回查时，发送超时进入 unknown，不盲目重发。withdrawn 仅是发送前本地撤回 pending / retry_wait 意图，且必须同时取消关联 Effect；Provider 远端撤回不属于基础 Outbox / Channel 协议。

## 9. Control 与 Watchdog

### 9.1 ControlCommand

命令状态：queued → applying → applied | rejected | unknown。

MissionControl 模式：

- running：允许新 Job / Assignment 调度。
- paused：停止新派工和新 Effect 启动；已接受 Run 继续由原执行边界收尾。
- cancelling：停止新派工，对确切 Run 和所属 Background Job 发起一次停止。
- cancelled：所有拥有的运行和后台写入均已确切结算。

命令携带 expected_revision 和稳定 idempotency_key。CAS 失败必须返回原状态，不得覆盖并发操作。Schema v16 额外要求 MissionControl 每次更新 generation 恰好递增 1，取消后不可恢复；终态 ControlCommand 的状态与回执不可改写，控制历史不可删除。

Pause / Resume / Cancel 的业务含义：

| 命令 | 行为 | 不代表 |
|---|---|---|
| pause | 暂停新派工；已接受任务继续观察和收尾 | 不代表所有 Run 已暂停 |
| resume | 重新核验 Mandate，递增 control generation，恢复调度 | 不代表重放原未知提交 |
| cancel | 停止新派工，精确停止原 Run / Background Job | 不代表控制回执等于进程退出 |

### 9.2 WatchdogSnapshot

healthy → stalled → blocked；有进展时 stalled → healthy。

- stale_after、连续停滞次数和动作均来自维护者配置。
- action=attention 时创建或更新 Attention，不改 Mission 状态。
- action=block 时，在已确认无未知副作用且符合策略时将 Mission 置为 blocked。
- Watchdog 不创建 Assignment、不重放 Effect、不凭没有文本输出判断失败。
- 活动指纹必须基于已核验 Mission / Job / Assignment / Artifact / 原运行活动，不把外部正文当作控制命令。
- Schema v16 固化观察时间与最近进展时间单调、指纹变化必须伴随新的 healthy 进展、同一指纹下状态只能 healthy → stalled → blocked；blocked 不得仅凭同一指纹恢复。

## 10. Stage 投影

UI Stage 不单独持久化，建议按以下优先级从同一只读快照派生：

1. unknown：存在尚未核实的提交、导入、停止或发送副作用。
2. awaiting_human：存在 Open Attention 或 Mission blocked 等待人工。
3. awaiting_review：Mission 位于 reviewing，且有 Review Assignment。
4. working：存在 running / accepted / capturing Assignment，或 applying Delivery / leased Effect / sending Outbox。
5. queued：存在 ready Job，或待执行的 Delivery / Effect / Outbox，且没有更高优先级活动。
6. failed：Ticket ended 且 outcome=failed。
7. blocked：存在已知失败、依赖冲突或预算耗尽，且没有更高优先级未知项。
8. idle：Ticket open，但当前无活动 Mission / Input / Delivery / Effect / Outbox。
9. ended：Ticket ended，且 outcome 不是 failed。

查询结果必须同时返回底层对象状态和派生依据；不能只返回一个 stage 让调用方猜测是否可执行。

## 11. 重试与恢复矩阵

| 对象 | 已知失败 | 未知结果 | 恢复方式 |
|---|---|---|---|
| Evaluation | 有界重排 | 不适用模型副作用，但 Proposal 超时记 unavailable | 固定原批次和 Policy Version；超过预算转人工 |
| Assignment | 预算内新 attempt | 不创建新 attempt | 回查原 submission_key / run_id |
| Delivery | 新修复计划需人工或新显式导入 | 不再 apply | 核对原目标 tree / commit |
| Effect | 仅可安全重试的失败进入 retry_wait | 不重放 | 按原 key 回查 |
| Outbox | Provider 明确支持同 key 幂等时重试 | 不重发 | Provider Receipt / 原消息查询 |
| Stop | 只允许已确认的原操作重试规则 | 不再次发停止命令 | 查询确切 Run / Background Job |
| Migration | 可重复的离线导入，使用稳定 import key | 保留未核实记录 | Dry-run、哈希核对、人工批准 |

## 12. 并发、事件与审计约束

- 同一 Thread 最多一个活动 Evaluation。
- 同一 Ticket 最多一个活动或未知 Mission。
- 同一 Job 最多一个活动或未知 Assignment。
- 一个 Mission 内不同 Job 可以并行，受 Repo / Profile / Instance 并发上限约束。
- Job 依赖按稳定 node ID 校验 DAG，不按数组顺序猜依赖。
- 每次状态转移检查 aggregate_revision；旧 Worker 迟到回执不能覆盖较新状态。
- 每次人工控制、授权撤销、未知结果核实和外部发送回执均追加 AuditEvent。
- 只读状态接口不得推进状态、消费 Effect 或自动重试。
- SQLite 部分唯一索引强制同一 Ticket 最多一个活动或 unknown Mission，同一 Job 最多一个活动或 unknown Assignment；只有原聚合进入确切非活动终态后才允许新一轮。
- Ticket 结束必须核实 Follow Condition、非恢复失败且无未知操作、人类停止或 Mandate 撤销的原始事实；human-stopped / revoked Ticket 不可普通恢复。
- Mission 启动必须引用已封存 PlanRevision；进入 integrating 前必须核实所有 Work Job done 和必需 Delivery applied；unknown 的每个恢复目标有独立回查事实，不存在 retry_wait 转移。
- Job ready 必须满足 DAG 依赖、Delivery、Mandate 与控制条件；done 必须有角色对应的冻结结果和 SHA-256。Assignment 的 accepted / running / capturing / terminal 结果必须绑定原 submission_key、原 Run 和角色输出，不能以模型报告代替。
- 取消等待不等于撤销已接受写入；资源收尾必须由原操作所有者完成。

## Phase 4 的统一运行约束

实施顺序见[总体方案](superagent-next-generation-architecture.md#phase-4完整-superagent-整合与替换)。控制、输入和文件交付的状态均在同一协调账本，不以文件投影替代业务事实。

- InputDelivery 独立记录 pending / submitting / accepted / applied / unapplied / unknown / rejected；原生接受和实际应用分别核验，unknown 不自动再次投递。
- Delivery 的 pending / planning / applying / applied / conflict / unknown / cancelled 不与 Assignment 状态混用；原文件导入操作未知时仅回查。
- 控制模式 running / paused / cancelled 与 Mission 的真实取消收尾状态分离。没有完整 Run / Background Job 收尾证据不能标记 cancelled。
- work 阶段结束不等于 Job done；Checkpoint 接续创建新 Assignment，最终阶段才解锁依赖。
- 文件交付完成必须绑定冻结候选及独立 Review；普通报告的完成分支不能绕过该质量门。

## 13. 统一状态机验收场景

保留已有有效单元及 SQLite 事务回归，Phase 4 按实际改动补齐下列场景的缺口；不要将它们理解为重做 Phase 0 / 1 或每次默认全量执行：

1. 同一 SourceInstance 同一 external_event_id 同哈希重复接收，只创建一个 Trigger / Fan-out。
2. 同一身份不同哈希返回 conflict，原 Trigger 字节不变。
3. 两个并发 Evaluation 不得同时占有同一 Thread。
4. Policy 在 Assessor 调用期间更新，旧 Proposal 不得创建 Mandate。
5. Bot / CI / context-only Trigger 不得签发 explicit Mandate 或恢复 Ticket。
6. Ticket 人工停止后，普通 follow-up 不得恢复。
7. Ticket 在 Mandate 仍有效且 Work Subscription 上收到人类跟进时可恢复。
8. Mission 的 Job 依赖存在环、自依赖或未知节点时拒绝 Plan。
9. Checkpointed Job 不解锁下游；Job done 只是依赖满足的必要条件，requires_delivery=true 的依赖边还必须有 applied 且目标树核验通过的 Delivery；Delivery conflict / unknown 不得解锁。
10. 同一 Job 每次执行都有新的 Assignment / attempt_no，旧 Run 引用不可改写。
11. Submission 丢回执后，重启只查询原 submission_key，不创建第二个 Run。
12. Background Job 未结算时，不冻结 Artifact、不解锁依赖。
13. Delivery apply 回执丢失后，不重复导入。
14. Reviewer 修改候选工作树时，Review 失败且原候选保留。
15. Outbox 发送后丢响应时进入 unknown；Provider 幂等键或查询回执决定后续。
16. Cancel 已受理但原 Run / Background Job 未结束时，Mission 保持 cancelling。
17. Watchdog 只产生 Attention 或按配置 block，不自动创建新 Assignment。
18. 所有状态转移均有 AuditEvent 和 revision CAS；非法转移无副作用。

这些测试不依赖真实模型质量或生产账号；在线模型和真实 Provider 验收属于后续阶段。
