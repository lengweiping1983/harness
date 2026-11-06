# 下一代 Harness Superagent 架构决策与接口协议

> 文档状态：Phase 0 决策基线 v0.1，待维护者评审确认。\
> ADR 状态说明：下列决策是推荐基线；在维护者确认前不得视为已实现的公共协议。\
> 关联：[领域模型与数据契约](superagent-domain-model.md) · [状态机与合法转移](superagent-state-machines.md) · [总体架构](superagent-next-generation-architecture.md)。

## 1. 决策记录规则

每项 ADR 记录背景、决策、替代方案和影响。维护者确认后将状态更新为 Accepted；若实施中需要偏离，新增 ADR 并说明迁移，不静默改写旧决定。

Phase 0 冻结领域与接口边界；后续实际交付见领域模型的当前实现边界。Phase 4 的整合路线以 ADR-011 和总体方案为准。参考原型不进入运行依赖。

## 2. ADR 摘要

| ADR | 决策 | 状态 |
|---|---|---|
| ADR-001 | Harness Runtime 继续作为唯一执行内核 | Proposed |
| ADR-002 | Ticket / Mission / Job / Assignment 语义分离 | Proposed |
| ADR-003 | 每个 Instance 一个 Coordinator SQLite；Harness 事实不复制 | Proposed |
| ADR-004 | Transactional Effect Queue + Outbox；未知副作用不重放 | Proposed |
| ADR-005 | SourceKind 与 SourceInstance 分离；先静态 Registry | Proposed |
| ADR-006 | Mandate 不可变；Policy 快照与执行边界重新核验 | Proposed |
| ADR-007 | 一个 Mission 一个 Repo 和冻结 Base Commit | Proposed |
| ADR-008 | Job 是逻辑节点，Assignment 是执行尝试 | Proposed |
| ADR-009 | Artifact / Evidence / Review 以真实文件和原始运行证据为准 | Proposed |
| ADR-010 | Pause / Resume / Cancel 与真实 Run 收尾分离 | Proposed |
| ADR-011 | Phase 4 统一替换，无旧版运行数据转换 | Accepted |
| ADR-012 | 不把工具白名单或 Worktree 宣称为 OS Sandbox | Proposed |
| ADR-013 | Domain / Application / Ports / Adapters 依赖方向 | Proposed |
| ADR-014 | Phase 0 先冻结协议，Phase 1 再实现协调账本 | Proposed |

## 3. ADR 详细内容

### ADR-001：Harness Runtime 继续作为唯一执行内核

**背景**

当前 Harness 已有 Session、RunController、Tool Registry、Worktree、Background Jobs、Usage、Tool Event 和原生提交 / 收尾能力。当前 Autonomy 已具备 DAG、冻结基线、真实文件交付、Integrator、Reviewer、Checkpoint 和 Watchdog。dsh-superagent 的 Executor 是参考抽象，不应被移植为第二套运行时。

**决策**

- Superagent 负责授权、协作、任务规划、调度、证据组织和外部交付。
- 所有模型运行和工具调用经 Harness Runtime。
- ExecutorPort 是 Superagent 到 Harness Runtime 的唯一执行边界。
- Superagent 只存 harness_instance_id / session_id / workspace_id / run_id / turn_id / tool_call_id 等引用和核验摘要。
- 不在 Superagent 中实现另一套模型传输、Tool Registry、Session 历史或 Background Job 运行器。

**替代方案**

- 把 dsh Executor 和 Store 直接移植进 Harness：拒绝，重复实现资源生命周期。
- 让 Worker 自行调用外部模型 SDK：拒绝，绕过 Harness 权限和执行事实。

**影响**

保留现有执行成果；新模块必须通过 Adapter 接入。若未来引入 Remote Worker，也必须实现同一 Executor Port 和回执协议。

### ADR-002：Ticket / Mission / Job / Assignment 语义分离

**背景**

当前 Harness Inbox Task 是 Session 绑定，Mission 是有界 DAG 目标；dsh Ticket 是持续授权工作单元。若继续让 Task、Ticket、Mission 混用，会产生生命周期和恢复歧义。

**决策**

- Ticket：为什么允许持续工作、工作范围和订阅哪些 Thread。
- Mission：这一次要交付什么，采用哪版 PlanRevision，何时算完成。
- Job：Mission DAG 中的逻辑节点。
- Assignment：Job 的一次真实执行尝试。
- Worker：Harness Session / Workspace 等运行资源绑定。
- 默认 Router 简单任务也创建单节点 Mission；Autonomy 创建多节点 DAG。

同一 Ticket 可有多个顺序 Mission；同一 Ticket 同时最多一个活动或未知 Mission。Ticket 不是 Session，Mission 不是 Ticket，Job 不是 Assignment。

**替代方案**

- 保留 Router / Autonomy 两套独立生命周期：拒绝，重复实现状态和恢复。
- 把 dsh Ticket 直接改名为 Mission：拒绝，长期授权与有界交付不是同一对象。

### ADR-003：每个 Instance 一个 Coordinator SQLite，Harness 事实不复制

**决策**

- 每个 Superagent Instance 的协调账本使用独立 state_dir/superagent.sqlite3。
- 使用 WAL、外键、短事务、CAS 和明确 schema_migrations。
- 初期一个 Instance 一个协调进程；多进程写入必须有进程锁和 fencing。
- 只存领域协调事实、Effect、Outbox、Audit 和原生执行引用。
- Harness Home 继续保存 Session、Run、Tool Event、Usage、Workspace 和 Background Job 事实。
- 大附件和 Artifact Manifest 使用内容寻址文件存储，数据库保存引用和哈希。

**替代方案**

- 把 Harness 原生 Run 数据迁入新库：拒绝，制造双事实源。
- 继续把每个领域拆成独立 SQLite：不作为目标架构，跨库原子事务和恢复复杂。
- Phase 0 直接改 PostgreSQL：暂缓，先以单实例 SQLite 验证边界和负载；只有实测写竞争、部署或多设备需求超过 SQLite 约束后再评估。

**影响**

协调事务不可能与 Harness Run 提交形成跨数据库原子事务；必须通过稳定提交键、原操作回查和 unknown 管理。

### ADR-004：Transactional Effect Queue + Outbox；未知副作用不重放

**决策**

- 业务事务内同时保存领域意图和 Effect / Outbox 行。
- Commit 后由 Effect Runner 领取并调用 Harness、Git 或 Source Provider。
- Effect 记录稳定幂等键、generation、租约、尝试次数、due_at 和原回执。
- Outbox 独立保存 Message Intent 与 Provider Receipt。
- 对可证明幂等、且 Provider 能回查的操作，可以按原 key 安全重试。
- 不能确认是否已发生的操作进入 unknown，先回查，必要时人工核实。
- 不承诺普遍 exactly-once。

**替代方案**

- 在 SQLite 事务内调用模型或网络：拒绝，长事务和外部失败会破坏一致性。
- 所有超时都指数退避重试：拒绝，可能重复 Run、文件导入或消息发送。
- 依赖进程内 asyncio Task：拒绝，进程崩溃后丢失意图。

### ADR-005：SourceKind 与 SourceInstance 分离；先静态 Registry

**决策**

SourceKind 是代码插件契约；SourceInstance 是某个账号、仓库或 Bot 的具体配置。Thread、Actor、Trigger、Channel 的唯一身份均包含 SourceInstance。

Phase 2 先实现静态 SourceRegistry 和内置 GitHub / Feishu / Local Adapter。动态第三方 Python 包加载、远程插件和热更新不属于首批能力。

每个 SourceKind 必须声明 kind_api_version、配置 Schema、能力、身份验证、规范化、Scope、Context Reader、Channel 和 Health。缺少能力时必须明确 unsupported，不能推测支持。

**替代方案**

- 按 kind 合并多个账号：拒绝，跨账号 ID 冲突和权限串扰。
- 先支持任意动态 Python 插件：暂缓，信任边界、代码加载和版本兼容尚未定义。

### ADR-006：Mandate 不可变；Policy 快照与执行边界重新核验

**决策**

- Gate 是确定性宿主逻辑，先于模型 Assessor。
- Decision.accept 才签发 Mandate。
- Mandate 保存原始 Trigger、Actor、Source、Policy Version、Gate Basis、Scope 和授权预算。
- explicit / inferred 由宿主依据可信 Source 事实与 Policy 推导。
- Bot / CI / 引用文本不能成为 explicit authority。
- inferred 默认 observe，只有维护者明确启用具体 Admission Row / Rule 后才允许 auto。
- Ticket 恢复、Assignment 提交、Artifact 导入和 Outbox 发送前重新核验授权。
- 已接受 Run 仍由原执行边界收尾；撤销阻止新副作用，不抹去原运行事实。

**替代方案**

- 将授权只写进 Prompt：拒绝，无法进行宿主级重核验。
- Policy 更新自动取消所有 Run：拒绝，无法区分原执行收尾和新写操作。
- 模型可修改 Mandate Scope：拒绝。

### ADR-007：一个 Mission 一个 Repo 和冻结 Base Commit

**决策**

- Phase 1–4 的 Mission 只允许一个 Repo。
- Plan 接纳时冻结 base_commit。
- 每个 Work Assignment 在独立 Workspace / Worktree 执行。
- 依赖通过真实 Artifact Capture / Delivery 导入。
- Integrator 基于全部工作交付创建候选；Reviewer 对冻结候选独立复核。
- 多仓库目标由上层拆成多个 Mission；跨仓库发布和部署需单独流程。

**替代方案**

- 一个 DAG 直接跨多个 Repo 原子合并：暂缓，Git 交付、失败恢复和回滚边界尚不明确。
- 只传递 Worker 文本总结：拒绝，无法证明真实文件内容。

### ADR-008：Job 是逻辑节点，Assignment 是执行尝试

**决策**

- Job ID 在 PlanRevision 内稳定；Job 不携带可被覆盖的唯一 Run 身份。
- Assignment 每次执行单独创建，绑定 attempt_no / submission_key / worker / workspace / session / run。
- Checkpoint continuation 创建新 Assignment，保留原 Job 和先前 Artifact 链。
- 失败重试创建新 Assignment，不重写旧记录。
- unknown Assignment 在回查前阻止新 attempt。
- 下游依赖必须同时满足源 Job done 和 requires_delivery=true 的对应 Delivery applied 且目标树核验通过；不认 Assignment succeeded 的单独文本报告，也不认 Job checkpointed。

**替代方案**

- 继续用 mission/role/node/attempt 组合 ID 直接表示 Job：作为当前实现可以运行，但目标模型拒绝，因其把逻辑节点与执行尝试混在一起。

### ADR-009：Artifact / Evidence / Review 以真实文件和原始运行证据为准

**决策**

- Artifact 由冻结的文件 Manifest、Tree Hash、Base Commit 和 Git 元信息标识。
- Delivery 记录独立 import plan、目标 Workspace、idempotency key 和最终 Tree。
- Evidence 指向原 Harness Run / Turn / Tool Call / Result Seq。
- Reviewer 使用独立 Profile、Session 和 Workspace；不得修改候选 Artifact。
- Verdict 必须逐项匹配当前 PlanRevision 的 Acceptance Hash 和当前候选 Evidence Set。
- 消费 Verdict 时回到原 Run 重新核验，不信任缓存。

**替代方案**

- 仅凭 Reviewer 自述“通过”：拒绝。
- 复制 Tool Event 全量正文到 Coordinator DB：拒绝，造成双事实源和数据增长。

### ADR-010：Pause / Resume / Cancel 与真实 Run 收尾分离

**决策**

- ControlCommand 与 MissionControl 是 Superagent 协调状态。
- Pause 阻止新派工，不撤销已接受运行。
- Resume 重新核验 Mandate 并增加 control generation。
- Cancel 先阻止新派工，再对确切 Run / Background Job 发起所属停止。
- 只有原 Run、Background Job 和文件收尾均有确切终态，才标记 cancelled。
- Stop 超时、旧 PID、进程句柄丢失或控制模式变化都不能证明已停止。

**替代方案**

- 仅将 Mission 状态改为 cancelled：拒绝，可能遗留写入进程。
- 根据 Session ID 停止整个会话：拒绝，Session 可能复用且包含其他 Run。

### ADR-011：Phase 4 统一替换，无旧版运行数据转换

**决策**

- Phase 4 结束时只有一个 SuperagentService、一个协调账本、一套调度、执行和恢复机制；Phase 5 起仅扩展同一系统。
- 当前没有旧版运行数据，不开发旧任务导入、离线转换、历史协议兼容或双轨运行工具；本决策取代此前将迁移退役延后至 Phase 7 的路线。
- 旧目录仍属于用户数据。发现旧账本或目录混用时在写入前拒绝，保留原件，不隐式清理或迁移。
- Coordinator 已登记的迁移内容及校验和不变，新增结构使用新迁移；Harness 原生执行事实不复制。
- 生产宿主不依赖旧目录和 Shadow；旧源码仅在调用方、动态绑定、资源分发及有效业务回归完成迁接后删除。

### ADR-012：不把工具白名单或 Worktree 宣称为 OS Sandbox

**决策**

- Tool Profile 和 Source Admission 仍由 Harness 宿主控制。
- Worktree 是文件隔离和交付机制，不等于 OS 进程或凭证隔离。
- 在 Sandbox Runner / Secret Broker / egress policy 完成并通过实机验证前，不对不可信外部写任务宣称生产级 OS 隔离。
- 远程 Worker、跨设备租约和凭证代理放在后续阶段。

**替代方案**

- 仅凭 Docker 默认配置或独立目录宣称安全沙箱：拒绝。

### ADR-013：Domain / Application / Ports / Adapters 依赖方向

**决策**

依赖方向固定为：

~~~mermaid
flowchart TB
  API[CLI / HTTP / Panel] --> APP[Application Services]
  RT[Runtime Composition] --> APP
  APP --> DOM[Domain]
  APP --> PORTS[Ports]
  AD[Source / Harness / SQLite / Git Adapters] --> PORTS
  AD --> DOM
~~~

- Domain：纯对象、值类型、规则和合法状态转移；不依赖 SQLite、网络或 Harness。
- Application：事务编排、权限核验、状态转移和 Effect 入队。
- Ports：Source、Policy、Assessor、Executor、Artifact、Evidence、Channel、Repository 和 Effect Queue 接口。
- Adapters：GitHub、Feishu、Local、Harness Runtime、SQLite 和 Artifact Store 的具体实现。
- Runtime Composition：负责依赖注入、资源所有权和关闭收尾；Phase 1 的 `CoordinatorHost` 是仅面向可信宿主代码的组合边界，不注册公开路由，也不创建旧 Harness / Worker。
- 宿主事实必须由注入的 EvidenceVerifier 从原始 Harness / Provider 回执核验为 `VerifiedTransitionEvidence`；模型报告不得直接提供 facts。没有真实核验器时状态转移失败关闭。
- API / Panel 不得直接更新数据库或绕过 Application Service；普通 Repository.update 也不得修改 Ticket / Mission / Job / Assignment 的 state。

**替代方案**

- Domain 直接 import harness.app 或 Provider SDK：拒绝，导致测试和迁移耦合。

### ADR-014：Phase 0 先冻结协议，Phase 1 再实现协调账本

**决策**

当前阶段只建立领域模型、状态机、Ports 和 ADR 文档。Phase 1 才实现 Coordinator SQLite、Repository、Unit of Work、CAS、AuditEvent、Effect Queue 和 Outbox。

不在本阶段创建数据库、修改现有模块、迁移数据或双写。文档确认后以纯状态转移测试和 Schema Contract 测试锁定协议，再开始实现。

## 4. 目标 Ports 接口契约

以下是概念接口，不是已经注册的 Python API。实施时使用 Protocol / ABC 和明确的 DTO；具体 SDK 对象不得穿过 Port。

### 4.1 Source Adapter

~~~python
class SourceAdapter(Protocol):
    def validate_config(self, config: Mapping[str, object]) -> ValidatedConfig: ...
    async def verify_and_normalize(self, raw: RawEvent) -> NormalizedTrigger: ...
    async def poll(self, cursor: Cursor | None) -> PollBatch: ...
    async def read_context(self, thread: ThreadRef, cursor: str | None, limit: int) -> ContextPage: ...
    async def read_thread_lifecycle(self, thread: ThreadRef) -> ThreadLifecycleSnapshot: ...
    async def channel_for(self, thread: ThreadRef) -> ChannelRef | None: ...
    async def health(self) -> SourceHealth: ...
~~~

约束：

- verify_and_normalize 负责验证外部签名、租户和发送者身份；Domain 不信任未经验证的字段。
- Adapter 只提供 Source 事实，不决定 Mandate；channel_for 只解析写入地址，不发送消息。
- 缺失的 poll / context / channel / thread-lifecycle 能力必须明确声明 unsupported。
- Thread closed / merged / reopened 是 Source 事实，由 Adapter 规范化为 Trigger 并更新 Thread 生命周期投影；read_thread_lifecycle 用于回查当前状态，ThreadLifecycleSnapshot 至少包含 source_state、source_outcome、source_closed_at、observed_at 和 provider_revision，并区分 open / closed / unknown / unsupported；不支持生命周期的 Source 必须标记 unsupported，不能推测为 closed。
- 外部发送由独立 Channel Port 执行，并使用稳定 operation key 和可回查回执。

### 4.2 Policy 与 Assessor

~~~python
class PolicyProvider(Protocol):
    def snapshot(self) -> PolicyRevision: ...
    def gate(self, trigger: Trigger, thread: Thread, tickets: Sequence[Ticket]) -> GateResult: ...
    def revalidate(self, mandate: Mandate) -> MandateValidity: ...

class Assessor(Protocol):
    async def evaluate(self, candidate: EvaluationCandidate) -> DecisionProposal: ...
~~~

Assessor 只能读取固定 Candidate 并返回 Proposal。它没有 Repository、Executor、Channel 或 Effect Queue 的写能力。Gate、Mandate Scope、Profile、预算和外部目标必须由宿主校验。

### 4.3 Harness Executor

~~~python
class Executor(Protocol):
    async def ensure_worker(self, assignment: AssignmentIntent, operation_key: str) -> WorkerReceipt: ...
    async def submit(self, assignment: AssignmentIntent, operation_key: str) -> SubmissionReceipt: ...
    async def deliver_input(self, assignment_ref: AssignmentRef, ticket_input: TicketInput, operation_key: str) -> InputReceipt: ...
    async def reconcile_input(self, assignment_ref: AssignmentRef, operation_key: str) -> InputReconciliation: ...
    async def inspect(self, run_ref: HarnessRunRef) -> RunSnapshot: ...
    async def stop(self, run_ref: HarnessRunRef, operation_key: str) -> StopReceipt: ...
    async def reconcile(self, operation_key: str, run_ref: HarnessRunRef | None) -> Reconciliation: ...
~~~

- submit 在调用前必须已有持久 submission key。
- deliver_input 仅在宿主重新核验 Mandate、work Subscription 和 Control Mode 后调用，并使用稳定 operation key；没有可接收输入的活动 Assignment 时，TicketInput 留在队列并在下一次 Assignment submit 时按序携带。
- inspect / reconcile / reconcile_input 是原操作查询，不得创建新 Run 或重复投递输入。
- stop 只针对确切 Run 和其拥有的后台作业。
- Worker Profile、Repo、工具和目录范围由宿主提供，模型不能扩大。

### 4.4 Artifact、Evidence 与 Channel

~~~python
class ArtifactStore(Protocol):
    async def capture(self, assignment: AssignmentRef) -> ArtifactReceipt: ...
    async def inspect(self, artifact: ArtifactRef) -> ArtifactManifest: ...
    async def plan_merge(self, artifact: ArtifactRef, target: WorkspaceRef) -> MergePlan: ...
    async def apply_merge(self, plan: MergePlan, operation_key: str) -> DeliveryReceipt: ...
    async def reconcile_merge(self, operation_key: str) -> DeliveryReconciliation: ...

class EvidenceProvider(Protocol):
    async def run_snapshot(self, run_ref: HarnessRunRef) -> RunEvidence: ...
    async def tool_invocations(self, run_ref: HarnessRunRef) -> Sequence[ToolEvidence]: ...
    async def background_status(self, run_ref: HarnessRunRef) -> Sequence[BackgroundEvidence]: ...

class Channel(Protocol):
    async def send(self, message: OutboxMessage, operation_key: str) -> SendReceipt: ...
    async def reconcile_send(self, operation_key: str, provider_id: str | None) -> SendReconciliation: ...
~~~

ArtifactStore 不接受自然语言摘要代替真实文件。EvidenceProvider 只读原 Harness 事实。Channel 只由 Outbox Effect Runner 调用，不暴露给 Worker 直接发送。pending Outbox 的 withdrawn 是本地撤回；Provider 远端撤回不属于基础 Channel Port。

### 4.5 Repository 与 Effect Queue

~~~python
class UnitOfWork(Protocol):
    def __enter__(self) -> "UnitOfWork": ...
    def __exit__(self, exc_type, exc, tb) -> None: ...
    def commit(self) -> None: ...

class Repository(Protocol):
    def atomic(self) -> UnitOfWork: ...
    def compare_and_swap(self, aggregate_id: str, expected_revision: int, change: DomainChange) -> bool: ...

class EffectQueue(Protocol):
    def enqueue(self, kind: str, key: str, payload: Mapping[str, object]) -> None: ...
    def claim(self, worker_id: str, limit: int) -> Sequence[EffectLease]: ...
    def ack(self, lease: EffectLease, receipt: EffectReceipt) -> None: ...
    def retry(self, lease: EffectLease, reason: str, due_at: datetime) -> None: ...
    def mark_unknown(self, lease: EffectLease, reason: str) -> None: ...
~~~

UnitOfWork 暴露 Ticket、Mission、Job、Assignment 等有类型的 Repository；读取、插入和 CAS 均在同一事务边界内完成，具体聚合方法在 Phase 1 Schema Contract 中冻结。Repository 事务内只写协调账本。Effect Queue 的 enqueue 必须参与当前业务事务；claim / ack / retry 使用 lease generation fencing。所有网络、模型、Git 和 Harness 调用均在事务外。

## 5. 事务与调用顺序约定

1. Application Service 开启短事务。
2. 校验当前 Aggregate Revision、Policy Version、Mandate、Control Mode 和稳定幂等键。
3. 保存领域状态、AuditEvent 和 Effect / Outbox 意图。
4. 提交事务。
5. Effect Runner 领取并执行外部副作用。
6. 使用原 operation key 回写回执，CAS 更新状态并追加 AuditEvent。
7. 回执丢失时进入原操作 Reconciliation；无法确认则保持 unknown。

禁止在 SQLite 写事务中等待模型、网络、Git、Shell 或 Harness Run。

## 6. 安全与兼容约束

- Source Event、Thread Context、模型 Proposal、Worker Report、文件 Artifact、Tool Output 和 Provider Receipt 均按不可信输入校验。
- SourceInstance 的身份、配置和 Secret Reference 必须隔离。
- Mandate Scope 由宿主强制执行；Prompt 只作说明。
- 未启用 OS Sandbox 前，禁止把本机 Shell / Worktree 宣称为不可信执行沙箱。
- PR Merge、部署、发布和凭证变更需要独立授权和回执。
- 外部操作不能与 SQLite 事务组成 exactly-once 原子提交；unknown 是一等状态。
- API 列表分页、有界；审计记录不可静默删除。
- 数据迁移不得自动补造授权、发送消息或重放旧任务。

## 7. 未决问题

这些问题不阻止冻结基础领域模型，但在对应阶段开始前必须由维护者确认：

| 问题 | 建议默认值 | 最迟决策阶段 |
|---|---|---|
| Source Plugin 是否支持第三方动态加载 | 先静态 Registry；插件代码视为可信代码 | Phase 2 |
| SourceKind API 如何兼容升级 | 明确 kind_api_version，不兼容时停用并提示 | Phase 2 |
| Ticket 是否允许多人 / 多操作者权限 | Phase 2 先单一可信操作者模型；多人授权后续单独设计 | Phase 5 |
| Policy 更新是否撤销所有活动 Mandate | 只对 Gate Basis 失效者撤销；其余 affected 并重新核验 | Phase 2 |
| unknown 由谁核实 | 先提供 CLI / API 只读回查和显式人工确认 | Phase 1 / 5 |
| Coordinator SQLite 是否需要 PostgreSQL | 先单 Instance SQLite；按写竞争与部署数据评估 | Phase 1 / 6 |
| 多仓库 Mission 如何交付 | 上层拆分多个单仓库 Mission | Phase 4 之后 |
| PR Merge / 部署授权模型 | 与 Mission completed 分离，独立 Mandate / Control | Phase 5 |
| Reviewer 是否必须使用不同模型 | 独立 Session / Workspace 为底线；关键任务可要求不同模型或人工 | Phase 4 |
| OS Sandbox 技术选型 | 不在 Phase 0 预定；按平台和威胁模型评估 | Phase 6 |

## 8. Phase 0 出口门禁

进入 Phase 1 前必须完成：

- [ ] 维护者确认 SourceInstance / Trigger / Thread / Mandate / Ticket 的身份和授权语义。
- [ ] 维护者确认 Ticket / Mission / Job / Assignment 的边界及恢复规则。
- [ ] 确认所有 revision / round / attempt 字段含义不重叠。
- [ ] 确认状态机中的终态、unknown、cancelling 和重试边界。
- [ ] 确认 Coordinator SQLite 与 Harness 原生执行事实的所有权。
- [ ] 确认 Source / Policy / Assessor / Executor / Artifact / Evidence / Channel / Repository Ports。
- [ ] 确认迁移禁止双写、禁止自动补造 Mandate、unknown 不重放。
- [ ] 将状态转移矩阵转换为确定性测试清单。
- [ ] 将表清单转换为 Schema Contract 和唯一索引测试。
- [ ] 由维护者将 ADR 状态从 Proposed 更新为 Accepted，或记录拒绝和替代决定。

Phase 0 文档写入完成不代表这些评审门禁已经通过。没有维护者确认前，不应启动 Phase 1 的数据库和运行代码改造。

## 9. 参考源码

### Harness

- harness/app/superagent_inbox.py
- harness/app/superagent_dispatch.py
- harness/app/superagent_router.py
- harness/app/superagent_workers.py
- harness/app/superagent_mission_store.py
- harness/app/superagent_missions.py
- harness/app/superagent_mission_control.py
- harness/app/superagent_watchdog.py

### dsh-superagent

- dsh-superagent-prototype/src/core/model.ts
- dsh-superagent-prototype/src/core/ports.ts
- dsh-superagent-prototype/src/core/engine.ts
- dsh-superagent-prototype/src/app/store.ts
- dsh-superagent-prototype/src/app/effects.ts
- dsh-superagent-prototype/src/app/outbox.ts
- dsh-superagent-prototype/src/sources/kind.ts
- dsh-superagent-prototype/src/sources/registry.ts

以上源码用于追溯设计依据；接口示例是目标协议草案，不代表当前项目已经暴露这些 API。
