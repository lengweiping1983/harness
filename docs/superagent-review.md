# Superagent 协调审查

本记录关联当前源码与业务回归，维护设计依据、已核对的问题和待验证风险。产品配置与完整行为统一见 [Superagent 模式](superagent.md)，验证入口见 [开发与测试](development.md)。审查不读取或依赖任何 `research/` 目录。

## 源码事实与设计选择

默认 `superagent_router.py::_decision` 只接受每事件一个工作任务；`superagent_results.py::_poll` 只把内部结果整理为回复。它们保留单任务、订阅和跨来源续接用途，不能承担复杂目标的依赖规划与验收。自主方式由 `SuperagentService.open` 在显式配置时装配 `SuperagentMissions`，不是打开原宿主的普通团队／子智能体开关。

`MissionStore` 仅保存协调事实；运行与工具事实仍来自 `RunController.find_submission`、原对话和 `tool_invocations`。`SuperagentWorkers` 在自主模式从账本取得共同资源组和冻结提交，不订阅节点 Thread。工作结果经已有 `capture_delivery` → `plan_delivery_merge` → `apply_delivery_merge` 交接，最终集成节点负责修正；每轮复核创建独立资源。现有 `worktree_delivery.py` 的共同组、基线和交付谱系约束均保留。

显式 `max_work_turns` 使工作节点采用阶段协议。`superagent_checkpoints.py::validate_checkpoint` 校验模型提议；`SuperagentMissions._capture/_confirmed_checkpoint` 核对原运行与工具证据后冻结文件；`_schedule/_dependency_jobs` 只采用逻辑节点的最终阶段。新阶段拥有独立资源、原提交键与本节点有界引导上下文，复用同一任务图和基线；不增加失败或未知副作用的重试。`status_jobs` 只隐藏已冻结的旧阶段，完整历史不删除。

目标控制由 `MissionControl` 保存模式与原命令回执，`MissionControlRuntime` 只把已应用意图作用到确切原运行及当前所属后台作业。控制回执与进程收尾分开，`SuperagentService` 等待所属停止。相对于控制层初始提交 `44d45df`，本次核对了共享规划会话、命令顺序、活动容量、读事务和原回执链路；没有据此更新其他产品领域的审查结论。

一手资料支持的工程取舍：

- [OpenAI 多智能体编排](https://developers.openai.com/api/docs/guides/agents-api/multi-agent)：适合独立问题的子智能体拥有独立上下文，主协调者汇总结果。这里采用有界并行与最小依赖报告，不将所有工作历史混入每个 Worker。
- [Claude Code Agent teams](https://code.claude.com/docs/en/agent-teams)：任务依赖、原子认领、自动解锁和完成质量门。这里由确定性宿主执行依赖和完成门禁，模型不直接改账本状态。
- [Anthropic 长期应用 Harness](https://www.anthropic.com/research/harness-design-long-running-apps) 与[长期智能体 Harness](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents)：规划、生成和独立评估分工，明确完成清单，防止仅凭自评提前结束。这里将集成与复核分开，并限制修正预算。
- [DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)：工具轮次的推理字段有明确提供方协议。新协调器继续使用既有模型传输，不复制另一套请求协议，也不将提供方回灌失败解释为“缺少自主性”。
- [Codex App Server 的运行中引导](https://learn.chatgpt.com/docs/app-server#steer-an-active-turn) 要求绑定确切活动轮次；[OpenAI Mid-turn steering](https://developers.openai.com/api/docs/guides/steering) 区分已受理与模型实际应用。这里据此选择原运行／轮次绑定、独立意图与原生回执分工；采用宿主原日志验证应用，不把排队或模型自述当成完成。

[Codex App Server 中断协议](https://learn.chatgpt.com/docs/app-server#interrupt-a-turn) 用确切会话与轮次标识停止目标。控制层据此保留原任务与原运行核对，并在同一执行边界投递停止，避免把共用会话当作运行身份。

[Codex 长任务实践](https://developers.openai.com/blog/run-long-horizon-tasks-with-codex) 强调工具反馈和外部化工作状态；前述 Anthropic 长期应用资料讨论新上下文与结构化交接。阶段接续据此采用新对话、冻结文件及显式剩余步骤，而预算和重复检查点判定由宿主控制。这是工程设计依据，没有据此推断本产品优于竞品。

用户提供的文章把强依赖任务协作列为开放问题；参考原型的 Ticket／Assignment 主要承接独立任务。这些是选择目标依赖图与真实产物交接的背景，不构成质量或吞吐优于其他系统的证据。

## Phase 4 协调模型与文件适配审查

本节相对于整合改造前 `5f1c3b7` 核对当前增量，覆盖统一模型、显式内部适配入口、Phase 4.2 唯一服务生命周期、Phase 4.3 DAG Scheduler 真实交付闭环，以及 Phase 4.4 的独立质量门、有限修正、阶段接续与故障恢复。2026-11-08 已完成统一内部链路的定向验收；Phase 4.5 已在统一服务交付 Sender 与持续协作；生产 CLI、默认路由切换和旧生产链退役仍未交付。当前不切换生产默认路由。

| 触发条件 | 源码措施 | 对应回归 |
|---|---|---|
| 已受理但未应用的指令被带入新阶段，或节点／Mission 混入彼此的执行上下文 | CoordinatorMissionInstruction 仅投影当前计划的 applied 原回执，Scheduler 按 Work Job 隔离、系统角色按 Mission 汇总，保留原 Run / Turn / applied_seq；有界序列化优先保留最新内容，重启不 steer 到新 Run | `test_only_applied_original_instructions_reach_own_stage_and_system_roles`、`test_restart_preserves_applied_context_without_steering_the_new_run_again`、`test_context_is_bounded_keeps_newest_correction_and_preserves_original_text`、`test_wrong_scope_is_refused_and_other_nodes_have_no_instruction_context`、`test_applied_context_resource_is_resolved_at_actual_stage_submission`、`test_corrupt_applied_payload_is_refused_without_rewriting_or_execution`、`test_missing_native_status_is_corruption_instead_of_empty_context` |
| Work 阶段结束被当作节点完成，或重启重放旧阶段 | 宿主 Profile 阶段预算与结构化任务封存到 PlanRevision；continue 冻结原 Run 输出、Evidence 及 Artifact，新阶段独立资源且逐条导入；只有最终 ready 才 done | `test_continuation_uses_new_resources_and_unlocks_join_only_after_ready`、`test_restart_and_lost_import_receipt_reconcile_original_stage_once`、`test_dependent_node_continuation_retains_each_required_delivery` |
| 预算耗尽、停滞循环、非法检查点、未知工具或篡改冻结报告 | 阶段及总 Assignment 预算独立核验，与所有已成功阶段比较检查点和树；冻结报告重算并关联原 Run Evidence；失败／未知不解锁下游 | `test_final_stage_budget_preserves_checkpoint_and_does_not_claim_ready`、`test_repeated_earlier_checkpoint_cannot_cycle_through_a_different_stage`、`test_invalid_checkpoint_and_unconfirmed_tools_never_start_continuation`、`test_corrupt_frozen_checkpoint_is_preserved_and_refused` |
| 真实 Harness 活动有新事件，Watchdog 仍永久 blocked | 原生 `SessionEvent.time` 是 Unix 毫秒整数，`CoordinatorExecutionService.watchdog_activity` 在监督投影中规范成 UTC 文本；溢出和负时间拒绝，原 Evidence 保持原值 | `test_native_millisecond_activity_advances_watchdog_and_invalid_time_stays_blocked`、`test_staged_work_survives_restart_and_reaches_independent_quality_gate` |
| 重启后经历长期原生检查故障，或语言切换污染后续阶段 | 原 Reviewer Run 在受控 24 小时时间窗与反复重建监督器后仍被阻断，仅真实新活动解锁并完成；阶段数据与当前语言提示分离 | `test_staged_work_survives_restart_and_reaches_independent_quality_gate`、`test_language_round_trip_is_consumed_by_each_actual_stage_submission` |
| 同一模板承接多个节点或阶段 | 迁移及执行服务按 Mission / Job / stage 绑定 Worker，工作树共同组采用 Mission | `test_same_profile_has_independent_node_and_continuation_resources`、`test_worker_scope_and_input_receipt_bind_the_original_assignment` |
| 模型提出系统角色，或封存后改写节点 | Planner 只接收 work，宿主 system_profiles 物化 integrate / review 后整体封存并回查 | `test_planner_cannot_propose_system_roles`、`test_host_materializes_system_roles_and_seals_the_complete_plan` |
| 工作树创建等待期间暂停 | 执行服务在下一资源边界核验持久控制，恢复复用原工作树 | `test_pause_during_workspace_creation_prevents_session_and_submission` |
| 缺失或损坏控制状态默认放行 | 控制模式核对原命令、代次与回执；缺失明确拒绝，不修补原件 | `test_missing_control_never_defaults_to_allowed_and_reads_do_not_recreate_it`、`test_corrupt_control_mode_and_receipt_are_rejected_without_repair` |
| 暂停后原运行结束仍产生完成与回复 | 原报告可冻结，但最终结算及 Outbox Candidate 检查控制模式 | `test_pause_blocks_completion_and_reply_until_explicit_resume` |
| 同一 TicketInput 被多个 DAG Assignment 消费、未知投递被重放或原 Run 回执被错认 | CoordinatorAssignmentInputDelivery 按 Input / Assignment 独立幂等；unknown / submitting / accepted 只回查原 request_id；严格核对 Worker、Workspace、Session、原 Run、回执身份与原生序号；并发更新不允许状态回退 | test_input_has_independent_assignment_receipts_and_cannot_cross_tickets、test_one_ticket_input_is_consumed_independently_by_multiple_assignments、test_unknown_input_delivery_reconciles_the_original_receipt_without_resubmission、test_malformed_native_receipts_remain_unknown_and_are_never_replayed、test_unknown_delivery_can_be_reconciled_after_input_is_handed_back、test_receipt_lookup_failure_does_not_downgrade_confirmed_acceptance、test_concurrent_receipt_reconciliation_cannot_downgrade_applied_delivery、test_input_delivery_rejects_a_different_active_run_for_the_assignment、test_executor_adapter_rejects_steering_a_new_run_for_an_old_assignment、test_reconcile_returns_latest_terminal_state_after_native_lookup_race、test_native_receipt_storage_keeps_only_canonical_evidence_fields、test_input_delivery_revalidates_mission_authorization_before_native_acceptance |
| 同一 DAG 多个 Work 节点在首节点读取后遗漏原始 Input，或原生 Run 从未启动却留下悬空 InputDelivery | Scheduler 在各 Assignment 冻结独立初始输入清单和稳定投递身份；仅凭同一原 Run 的已启动快照及 Input 清单认定对应投递 applied，权威确认原 Run 未启动后拒绝该节点的未完成投递，不影响其他节点的 applied 回执 | `test_dag_initial_input_is_confirmed_for_each_independent_assignment`、`test_dag_absent_original_run_rejects_only_its_own_input_delivery` |
| Artifact 导入冲突在原 Run 开始前阻塞 Join 后仍留下 submitting InputDelivery，或事务半途失败 | 调度器对 pending Assignment 的取消、独立 InputDelivery 拒绝、未读 TicketInput 归还和 Job 阻塞使用同一事务，失败全部回滚；其他节点已确认的 applied 不回退 | `test_artifact_delivery_conflict_blocks_join_without_overwriting_original_artifacts`、`test_dag_blocking_rolls_back_assignment_and_job_if_input_rejection_fails` |
| DAG 提交成功但原生 ACK 丢失时重复创建 Run | 仅按稳定 submission_key 回查已接受的原 Run，重复 Scheduler Tick 和 reconcile 不再提交第二次 | `test_dag_lost_submit_ack_recovers_original_runs_without_replay` |
| 真实未提交二进制与删除文件需经 DAG 交接，源 HEAD 已变化 | 统一 Scheduler 以 Mission 冻结基线，为各节点建立独立 Worktree；真实 Git Manifest 导入二进制、删除项，源仓库后续提交不改变原基线 | `test_shared_dag_scheduler_delivers_real_binary_and_deletions_and_recovers_lost_import_receipt` |
| Artifact Import 已写入原 Worktree 但回执丢失，Mission 进入 unknown | 只核对稳定 Effect 的原工作树 `delivery_inputs` 回执；仅新 Effect 重新准入，未知 Effect 不再执行写入；下一调度轮次恢复 Mission | `test_shared_dag_scheduler_delivers_real_binary_and_deletions_and_recovers_lost_import_receipt` |
| 原 Run 在首次 running 观察前已 completed | 以原 Run reference 的 `started_at` 填补 Assignment accepted → capturing 的开始时间，保留 started / ended 时间约束 | `test_shared_dag_scheduler_delivers_real_binary_and_deletions_and_recovers_lost_import_receipt` |
| 未知 Mission 被优先处理后重复进入正常队列 | unknown 优先顺序去重，同一批次每个 Mission 只调度一次；慢任务不会阻塞其他 Mission，容量耗尽保持背压 | `test_schedule_pending_runs_missions_concurrently_with_bounded_global_capacity`、`test_shared_dag_scheduler_delivers_real_binary_and_deletions_and_recovers_lost_import_receipt` |
| 节点指令重复投递、错绑 Ticket / Assignment / Run，或受理与应用回执混淆 | CoordinatorMissionInstruction 以不可变 payload hash、aggregate_revision 和稳定原生键绑定 Mission / Job / Assignment / Run / Turn；受理锁内复核 Mandate / Mission；只接受同 Ticket 非 context-only Trigger；原生回执核对原 Run 事件，unknown 只回查，accepted 不降级 | test_mission_instruction_is_assignment_bound_and_tracks_native_application、test_unknown_mission_instruction_reconciles_original_receipt_without_replay、test_mission_instruction_request_id_cannot_be_reused_for_other_payload_or_assignment、test_mission_instruction_revalidates_mission_authorization_inside_native_accept_lock、test_mission_instruction_rejects_context_only_ticket_input、test_executor_adapter_reconciles_instruction_from_original_run_events_only |
| 多节点 Work 并行期间收到普通来源跟进，单目标路由会遗漏其他活动节点；部分投递 ACK 丢失 | Host route_trigger 对同一 Mission 的多个活动 Work Assignment 按 Input / Assignment 独立调用 InputDelivery；稳定 delivery key 和原生回执分别持久化，unknown 只回查原目标，重复请求不重放；单节点路径保持原行为 | test_route_trigger_fans_out_followup_to_each_active_work_assignment、test_route_trigger_reconciles_partial_fanout_without_replaying_unknown_delivery |
| 未提交二进制与删除项需要交接 | 冻结 Git 对象生成 Manifest，原生 Worktree 导入保留源仓库 | `test_real_uncommitted_binary_and_deleted_files_are_frozen_and_delivered` |
| 捕获 / 导入已发生但返回丢失 | 稳定 Effect 加原工作树回执回查，不重放原写入 | `test_lost_capture_receipt_recovers_original_frozen_delivery_without_second_capture`、`test_lost_import_receipt_recovers_exact_original_import_without_second_write` |
| Effect 已完成但 Delivery 投影确认丢失 | 按已存原回执补齐协调投影，不再次导入 | `test_completed_import_recovers_a_lost_delivery_projection_without_another_import` |
| 伪造产物工作树，或原 Run 未确认收尾 | 消费前核对账本身份及原生 Run / Tool / Background 证据 | `test_forged_artifact_workspace_cannot_supply_dependency_files`、`test_capture_without_a_settled_original_run_never_creates_an_effect` |
| 捕获无确切回执或预检期间暂停 | unknown 保留；预检后重新准入，不执行迟到写入 | `test_missing_capture_receipt_remains_unknown_without_repeating_the_operation`、`test_pause_after_import_preflight_prevents_late_workspace_write` |
| Artifact 被交付给无冻结依赖的 Job | Schema v16 的 SQLite 触发器要求同 Mission / PlanRevision 的 requires_delivery 边或同 Job 后续 Checkpoint；拒绝时不创建 Delivery 或导入 Effect | `test_artifact_delivery_requires_a_frozen_dag_dependency` |
| Review 引用错误角色或共享执行资源 | 数据库守卫要求冻结 Acceptance Hash、Integrator 候选及独立 Reviewer Workspace / Session | `test_review_requires_frozen_integrator_candidate_and_independent_reviewer` |
| Artifact-producing Plan 缺少宿主质量角色，或可写 Worker 试图降级为单报告 | Host 限定 Work Profile 的宿主归属、repo/source 与 Mandate；Artifact Review 必须声明 criteria 并物化独立 Integrator / Reviewer；Integrator 必须具备 workspace-write 工具，Reviewer 仅允许 read-effect 工具；single_worker_report 限定单个只读 Work 节点 | `test_artifact_plan_materializes_host_integrator_and_independent_reviewer`、`test_artifact_plan_without_host_quality_profiles_fails_closed`、`test_writable_worker_cannot_downgrade_acceptance_to_single_report`、`test_reviewer_profile_rejects_workspace_write_tools` |
| Mission 全局轮次误受默认 Worker Profile 限制，导致 Reviewer 无法启动 | Mission 采用 Ticket Mandate 的 max_turns 全局上限，Work / Integrator / Reviewer 再分别应用各自 Profile 限额；阶段门禁按角色派工 | `test_mission_budget_uses_mandate_scope_and_worker_profile_limits_separately`、`test_failed_review_runs_one_fresh_repair_round_and_completes_quality_gate` |
| 独立 Reviewer 未携带冻结 Acceptance Hash，或原 Run 先于 running 观测完成 | PlanRevision 恢复持久验收指纹与候选，Reviewer 用独立 Workspace / Session、原 Run 证据和显式授权结算；快速完成时只凭已冻结的 Review/Artifact 和 Delivery applied 补记状态 | `test_failed_review_runs_one_fresh_repair_round_and_completes_quality_gate` |
| 失败 Review 后旧成功 Assignment 被恢复扫描再次结算，或新 pending Assignment 抢占旧候选查询 | 按逻辑 Job 最新轮次过滤历史结算；修正从最近成功冻结的 Integrator Artifact 导入，使用新的原生运行身份；成功的第二次 Review 才完成 Mission | `test_failed_review_runs_one_fresh_repair_round_and_completes_quality_gate` |
| 修正预算为零／已耗尽，或冻结 Review 被伪造、过期、丢失交付、旧 Assignment 被复用 | 修正授权重新核对当前 PlanRevision Acceptance Hash、原候选与 Reviewer Report 哈希、Delivery applied、最新 Reviewer Assignment 和无活动操作；只有真实冻结 failed 且预算剩余才进入新轮次 | `test_repair_requires_frozen_failed_review_and_available_round`（9 个子场景） |
| 新候选引用上轮 Integrator Evidence 企图通过 Review | Reviewer prompt 仅列出当前冻结 Integrator Assignment 与 Work Evidence；冻结判定拒绝旧 Integrator 证据，Mission 完成前再核验引用属于当前 Integrator Assignment | `test_reviewer_prompt_lists_only_current_integrator_and_work_evidence`、`test_reviewer_cannot_use_prior_integrator_evidence_to_pass_new_candidate`、`test_completion_rejects_evidence_from_prior_integrator_attempt` |
| Reviewer 交回 invalid Verdict 或最后一轮 failed 而预算耗尽 | 统一 Scheduler 只冻结阻断理由并将 Mission 置于 blocked，既不进入 completed，也不派发额外修正 | `test_scheduler_blocks_invalid_review_and_exhausted_repair_budget` |
| Integrator Job 的实际结果仍为 unknown，但 Mission 恰好没有活跃 Assignment | unknown Mission 恢复先确认所有 Job 无 unknown，再读取原 Effect；未确认阶段不能转换成 integrating 或继续派发 | `test_unknown_integrator_job_cannot_be_resumed_as_confirmed_mission` |
| Review 结算前控制暂停／Mandate 变化，或原 Reviewer Run 已完成但尚未冻结 Verdict | 质量门推进和 Reviewer 捕获冻结分别在同一协调写事务内重校来源授权、当前 PlanRevision、Mission 控制；预检后暂停或撤权不创建 Review、不重放原 Run，恢复控制时只结算同一原 Run | `test_review_settlement_rechecks_authorization_after_opening_transaction`、`test_persisted_pause_and_resume_preserve_review_without_replaying_original_runs`、`test_reviewer_freeze_rechecks_pause_inside_write_transaction`、`test_reviewer_freeze_rechecks_source_revocation_inside_write_transaction` |
| Watchdog 已按 block 策略升级 blocked，但 Reviewer 仍尝试结算或冻结；或 stalled 关注被误当成强制失败 | CoordinatorHost 将同一持久 Watchdog 注入统一 Scheduler；结算与 Reviewer Verdict 冻结时共同重查持久 blocked，真实活动进度更新为 healthy 后沿旧 Run 恢复；stalled 不自动改变 Mission 终态 | `test_blocking_watchdog_prevents_review_settlement_until_true_progress`、`test_watchdog_blocks_reviewer_freeze_after_preflight_until_fresh_progress`、`test_coordinator_watchdog_attention_policy_never_auto_blocks` |
| Watchdog 在 healthy / stalled / blocked 期间检查超时、活动结构损坏、首次观察不可信、缺少原生 Run / Session 身份的伪 confirmed、重复／漏采／外来 Assignment、时钟回拨或跨 Run / Session 观察 | `CoordinatorWatchdog.observe` 不将 unconfirmed 或结构损坏的观察作为进度；事务内核对活动 Assignment 身份集合；首次可信观察只建立持久 `activity_baseline_trusted`，不刷新 `last_progress_at`、不解除既有 stalled / blocked，后续新的已确认进度才可恢复；Schema v17 将旧快照安全标为未建立可信基线；Schema v19 通过 SQLite 视图投影 Mission / Job / Assignment 账本事实，Snapshot 保存规范 JSON 与 SHA-256；仅完整活动快照推进账本基线，直接 SQL 伪造账本事实或指纹由触发器拒绝；`CoordinatorExecutionService.watchdog_activity` 核对原 Run、Session、状态与 settled 回执，不自动重交 Run | `test_watchdog_inspection_failure_does_not_clear_block_after_restart`、`test_repeated_unconfirmed_ticks_do_not_refresh_stagnation`、`test_watchdog_incomplete_active_assignment_snapshot_never_unblocks`、`test_watchdog_first_trusted_snapshot_after_untrusted_baseline_does_not_unblock`、`test_watchdog_activity_rejects_foreign_run_and_session_snapshots、`test_watchdog_rejects_confirmed_activity_without_original_run_session`` |
| 多个原生 Run 交错观察时，A 游标向前但 B 游标倒退、同序号内容冲突或 B 漏采；不可提前解除 blocked | Schema v18 原生 Run / Session 游标首先完整校验身份与各自单调性，再在同一事务一次性消费；Schema v19 将 Snapshot 的账本事实 JSON 与 SQLite 事实视图及 SHA-256 绑定；不完整或异常快照保持原游标和 blocked，重建 Watchdog 后仍使用原进度；全部 Run 完整核实后才恢复，代价是部分合法进度可能需要等下一次完整采样 | `test_watchdog_tracks_interleaved_run_cursors_across_restart` |
| Reviewer 原 Run 先报告 interrupted 但尚未 settled | 保留原 Reviewer Assignment 与 Run，不提前冻结 Review、创建修正轮或阻断；确认 native settled 后才将 Reviewer Job 和 Mission 标为 blocked | `test_unsettled_interrupted_reviewer_never_freezes_review_or_repairs` |
| Integrator 的原 Run 中断通知早于真正 settled | 原生状态未确认结束时不生成失败终态、不派 Reviewer；确认 interrupted 才冻结 Job 和 Mission 阻断，原 Assignment / Run 不重交 | `test_interrupted_integrator_must_settle_before_blocking_without_reviewer` |
| Artifact.capture 或 artifact.import 的 Effect 租约已认领、但原生 Git 操作尚未开始时发生暂停或来源撤权 | 原生写入前使用相同协调事务重新核验当前 PlanRevision、原 Assignment / Run、Mandate、持久控制及 Watchdog；拒绝时产生带 generation 的 not_started 失败回执及 effect.failed 审计，绝不写入工作树；恢复授权后仅在匹配该无副作用证明时创建新 Effect 代次，保留原尝试历史 | `test_capture_rechecks_control_after_effect_lease_before_git_write`、`test_capture_rechecks_mandate_after_effect_lease_before_git_write`、`test_import_rechecks_control_after_effect_lease_before_workspace_write`、`test_import_rechecks_mandate_after_effect_lease_before_workspace_write` |
| Watchdog blocked 后仍重复轮询 Artifact 捕获或导入，或 Watchdog 服务重启后错误解锁 | 新 Effect 认领前先读持久 Watchdog 快照，阻断期不持续创建失败 Effect；认领后于外部写入前再次事务复核；重建 Watchdog 实例不会清除 blocked，仅真实活动指纹更新成 healthy 后才创建唯一的有效新尝试 | `test_capture_watchdog_blocked_after_effect_lease_requires_fresh_progress`、`test_import_watchdog_blocked_after_effect_lease_preserves_pending_delivery` |
| Artifact Effect 已认领后使用过期 PlanRevision 快照，或原 Run 结果未知时伪装成无副作用重试 | 过期快照不能触发 Git 写入；原生 Run 确认完成且存有同一 Assignment / Run Evidence、最新 capture Effect 持久具有可信 not_started 回执及匹配审计、无其他未知 Job 时，unknown Mission 才恢复原阶段并使用新 Effect 尝试；无原回执、普通 unknown Job 或无证据时继续阻断，不绕过源授权 | `test_capture_stale_plan_snapshot_after_effect_lease_requires_new_admission`、`test_import_stale_plan_snapshot_after_effect_lease_cannot_write_workspace`、`test_unknown_capture_without_original_receipt_never_replays_git_write`、`test_unknown_integrator_job_cannot_be_resumed_as_confirmed_mission` |
| 原 Git 捕获已写入但 ACK 丢失，Mission 与 Job 均 unknown | 已认领 capture Effect 仅核对同一 Assignment、原 Run、Worktree 的持久回执；跳过新写入准入但不重放捕获，成功后使用完整 Run / Artifact 审计重新结算；无原回执继续 unknown | `test_unknown_capture_reconciles_original_worktree_receipt_without_second_write`、`test_unknown_capture_without_original_receipt_never_replays_git_write` |
| Reviewer 原 Run 的状态读取失败后返回 unknown | 回查精确的原 Reviewer Run 并冻结该 Run 的 invalid Verdict，不创建第二个 Review Run；未知 Assignment 成功结算必须携带 run_id、原生 Evidence 和冻结结果 hash | `test_unknown_reviewer_original_run_can_freeze_invalid_review_without_replay` |
| 构造形式完整但伪造的 Mission 恢复审计 | 恢复 reviewing 要求真实原 Integrator Run Evidence、当轮成功 Assignment、冻结 Artifact 的原 Tree/Manifest、当前计划版本及每条 DAG applied Delivery 的实际原回执；拒绝伪造 Run、Artifact、交付树或版本，不接受仅仅格式合格的引用 | `test_unknown_reviewer_original_run_can_freeze_invalid_review_without_replay`、`test_unknown_mission_cannot_enter_reviewing_from_audit_payload_alone` |
| 一次合法修正后第二次独立 Review 仍 failed | 保留两轮冻结的候选、Reviewer 判定和全部原 Assignment，Mission blocked 且 round_no=1，不产生第三轮 Integrator / Reviewer | `test_second_failed_review_exhausts_budget_preserves_both_frozen_attempts` |
| Reviewer 原 Run 进入 unknown 后缺少当前 Assignment 的 Run Evidence，或工具／外部 Evidence ID 被误认作运行凭据 | Scheduler 从当前 Reviewer Assignment 的实际 `run` 类型 Evidence 中选择回查引用；无可信 Run Evidence 时保留 unknown、不创建 Review、不把旧执行标记成功，原 Run 后续确认后沿原身份冻结 Verdict | `test_unknown_reviewer_requires_original_run_evidence_before_freeze`、`test_review_recovery_selects_run_evidence_not_first_tool_evidence`、`test_unknown_reviewer_original_run_can_freeze_invalid_review_without_replay` |
| 跨 Host 并发提交同一 MissionInstruction | SQLite 唯一身份认领配合原回执查询，竞争实例不重复调用原生 steer；严格拒绝 bool / float 冒充整数及非字符串回执字段 | `test_concurrent_mission_instruction_claim_across_hosts_never_submits_twice`、`test_mission_instruction_native_status_rejects_noncanonical_types` |
| 多 Run 的后续游标写入失败，或 Schema v18 / v19 DDL 遇到同名对象冲突 | 多 Run Cursor 与 WatchdogSnapshot 在同一写事务提交，后续任一游标写失败整体回滚；迁移失败回滚新增列、表和版本记录并保留原对象与数据 | `test_watchdog_multi_run_cursor_and_snapshot_updates_roll_back_together`、`test_migration18_rolls_back_ddl_when_cursor_table_name_conflicts`、`test_migration19_rolls_back_ddl_when_ledger_facts_view_name_conflicts`、`test_every_prior_schema_version_upgrades_without_losing_effects` |
| 直接 SQL 试图回退投递、指令、控制、Watchdog Snapshot 或原生 Run 游标，重绑游标身份、重放／回退事件序号或删除游标历史 | Schema v16 固化指令、控制与投递状态边；Schema v18 将 Cursor 插入绑定到同 Mission 的活动原始 Assignment / Run / Session，更新只允许严格递增且禁止改写身份，历史不可删除；Schema v19 将 WatchdogSnapshot 的账本进度证明绑定到当前 SQLite 事实投影与 SHA-256 | `test_mission_instruction_control_and_watchdog_state_guards_are_monotonic`、`test_watchdog_cursor_sql_guards_reject_rebinding_regression_and_replay`、`test_input_has_independent_assignment_receipts_and_cannot_cross_tickets` |

文件适配回归使用真实临时 Git 工作树和确定性模型传输驱动的原生 Run，不代表模型判断质量、生产账号、长期负载或跨平台实机验收。2026-11-06，`test_superagent_coordinator_store`、`test_superagent_unified_artifacts`、`test_superagent_unified_execution`、`test_superagent_execution` 与 `test_superagent_unified_model` 共 112 项回归全部通过，相关新增守卫的直接 SQL 越界、跨 Host 并发和畸形回执用例均在其中。后续缺口以领域模型的当前实现边界为准。 同日，`test_superagent_coordinator_host`、`test_superagent_phase2` 和 `test_superagent_service` 共 85 项回归全部通过，验证 CoordinatorHost 默认不再依赖 legacy_state_dir / Shadow，同时显式 Shadow 与 V1 / V2 状态目录隔离仍保持。

### Phase 4.2：唯一服务生命周期与持久控制

| 触发条件 | 源码措施 | 对应回归 |
|---|---|---|
| 统一服务在暂停、资源边界或重启后遗失已接受输入 | `SuperagentService.tick` 从持久 TicketInput 重读待路由输入，沿原 Ticket / Trigger 幂等入口恢复；暂停不创建第二个 Workspace、Session 或 Run | `test_single_superagent_service_runs_assessor_planner_executor_recovery_and_close` |
| 已结算的历史 Cancel 长期占用控制恢复批次，或前 100 个未知目标永久挤占后续任务 | `reconcile_controls` 只选仍处于活动状态的 cancelled Mission；终态 Mission 不再反复扫描；控制、Assignment 与 Mission 批次按持久 Watchdog 观察时间轮转，且服务显式暴露积压 | `test_cancel_stops_original_run_and_settles_persisted_mission`、`test_reconciliation_rotates_over_backlog_using_persisted_watchdog_cursor` |
| Mandate 撤销后仍允许 Resume，或 Cancel 停止错误运行 | Resume 在原控制边界重验 Mandate；Cancel 绑定 Assignment / 原 Run / 所属后台作业，未知停止仅回查 | `test_unified_service_revoke_blocks_resume_and_cancel_settles_original_run`、`test_unknown_cancel_stop_is_not_replayed_and_can_be_reconciled`、`test_unknown_stop_after_nonconfirming_receipt_never_replays_after_inspection_error`、`test_cancel_stops_a_capturing_assignment_after_run_settlement` |
| 缺少 Provider Adapter 被误当作外部操作未发生，或每轮恢复无限追加事件 | 统一服务始终装配 EffectRecovery；缺失适配器只留下 unknown / unavailable，完全相同的最近观测幂等 | `test_missing_adapter_leaves_unknown_without_lookup`、`test_stale_lookup_result_cannot_overwrite_concurrent_reconciliation` |
| serve 中途失败被关闭错误覆盖，或 Host / Executor 未释放 | 保留首次运行错误；关闭等待 Tick、Host 操作和 Executor 收尾，重复关闭等待同一任务 | `test_unified_service_serve_failure_is_preserved_after_resource_close`、`test_aclose_waits_for_active_operations_and_releases_the_instance_lease` |
| Watchdog 误将停滞解释为授权或自动终止 | Watchdog 持久化单调进度与关注状态，关注模式不自动阻断；服务将 stalled / blocked 计入不确定状态 | `test_coordinator_watchdog_persists_monotonic_escalation_and_progress`、`test_coordinator_watchdog_attention_policy_never_auto_blocks` |

本阶段回归仍使用隔离账本、确定性 Assessor 和 Fake Executor；验证服务装配、控制、幂等回查与资源所有权，不宣称在线模型质量、生产 Provider 发送能力或 Phase 4.3 DAG Scheduler 已完成。

**提交门禁状态（2026-11-06）：** 定向 `.venv` 回归已通过。`harness_verify` 曾在执行命令前被当前 MCP 进程内存中的旧执行器 8 MiB 指纹上限拒绝；仓库源码已有独立 128 MiB 单文件／512 MiB 总量指纹上限，且已将对应 `files.py` / `runtime.py` 同步到本机安装目录。当前 MCP 进程仍需完全重启以重新加载模块；未忽略或跳过任何工作树文件，官方验证证据与提交仍待重启后完成。

## 已核对问题与防回归依据

| 触发条件 | 当前源码措施 | 回归 |
|---|---|---|
| 后续任务需要前置未提交文件 | `SuperagentMissions._prepare_job` 导入冻结交付，`MissionStore.scope` 提供共同组与基线 | `test_dependency_files_are_imported_and_candidate_is_independently_verified` |
| 源 HEAD 在计划后变化 | 计划接纳时冻结提交，Worker 创建与回查均核对它 | `test_frozen_baseline_is_shared_after_source_head_advances` |
| 多个任务争用容量或有慢前置 | 活动目标轮转；依赖不就绪不创建运行，其他目标继续 | `test_slow_predecessor_does_not_block_other_work_or_lose_late_publication` |
| 快目标先完成，发布游标超过慢目标接纳序号 | `MissionStore.complete` 同事务追加独立 `publications` 序号 | 同上，核对两个本地候选均发布 |
| 模型无法知道内部事件序号 | 模型只引用自己可见的调用身份，宿主派生确切结果序号 | `test_dependency_files_are_imported_and_candidate_is_independently_verified` 的真实模型表面传输 |
| 伪造调用、在其他仓库运行检查或改写复核候选 | `_verdict` 限定本运行唯一调用和显式目录，`_capture` 核对导入树未变 | `test_forged_evidence_cannot_complete_or_publish`、`test_command_in_another_repository_cannot_prove_candidate`、`test_reviewer_changes_do_not_redefine_the_candidate` |
| 复核失败后原结论已过时 | 原集成对话新运行修正，新复核对话及工作树重新检查 | `test_repair_is_bounded_and_uses_same_integrator_and_fresh_reviewer` |
| 原运行结束但后台写入未结束 | `_capture` 等待所属作业结算，不冻结半成品或解锁依赖 | `test_background_writer_must_finish_before_delivery_or_dependency_unlock` |
| 提交／导入成功而确认丢失 | 原稳定键与确切导入元信息回查，不再调用写入 | `test_lost_plan_return_reconciles_one_original_submission`、`test_lost_dependency_import_return_is_not_applied_again` |
| 提交无确切结果或重启遇到已完成目标 | 未确认保留 `unknown`，已完成目标只重读原引用 | `test_missing_submission_remains_unknown_without_replay`、`test_restart_does_not_reexecute_completed_plan_or_workers` |
| 最后异步捕获期间撤销来源 | 最终结算及完成调度重新核验来源修订 | `test_source_revocation_during_final_capture_suppresses_completion` |
| 损坏 ready 对话引用将工作转到其他会话 | 每次提交与回查均核对原 Worker、来源、模板、会话及工作树 | `test_corrupt_ready_session_cannot_submit_to_another_conversation` |
| 损坏复核缓存或完成回复冒充通过 | 消费及候选重读时从原复核运行重算并比较 | `test_corrupt_review_cache_or_completed_reply_cannot_publish` |
| 关闭发生在导入只读预检等待中 | 预检后重新核对准入，不启动迟到写入 | `test_close_during_import_preflight_does_not_start_late_workspace_write` |
| 来源在导入只读预检期间撤销 | 预检后重新核对授权，保留原件而不启动导入 | `test_revocation_during_import_preflight_prevents_new_workspace_write` |
| 并发本机提交暴露半个文件或产生重复事件 | 同目录完整准备后硬链接独占公开，身份键与正文分别核对 | `test_superagent_local.py` 的并发、相同身份冲突与无宿主 CLI 回归 |
| 同一实例切换语言 | 实际规划、工作及独立复核调用按当前宿主语言解析显式资源，三次目标均执行到真实交付 | `test_runtime_language_round_trip_reaches_actual_planner_requests` |
| 旧活动／阻塞目标被新完成目标挤出监督列表 | `MissionStore.status_snapshots` 按活动、待关注、抑制、完成分组，有界输出并报告总量及截断 | `test_older_active_and_attention_goals_are_visible_ahead_of_new_completions` |
| 同一集成会话复用，后轮进度污染前轮 | `run_progress` 以原运行唯一轮次限定事件；终态任务不继承后轮后台等待 | `test_previous_run_in_reused_conversation_keeps_its_own_terminal_activity`、`test_terminal_run_cleanup_and_background_wait_have_distinct_meaning` |
| 主运行未更新计数，或工具结果未确认却显示完成／活动 | 从原事件的真实开始及唯一结果配对计数；中断或终态的缺失结果保留 `unconfirmed` | `test_tool_progress_is_derived_from_current_run_instead_of_unused_counter`、`test_interrupted_or_terminal_tool_without_result_stays_unconfirmed`、`test_unpaired_or_duplicate_result_does_not_fabricate_completion` |
| 状态读取推进未知提交或泄露执行正文 | `_submission` 只读核对原提交，摘要只投影元信息；不调用结算或执行入口 | `test_status_reads_original_run_without_writes_or_accepting_mismatched_identity`、`test_missing_submission_remains_unknown_without_replay`、`test_tool_progress_is_derived_from_current_run_instead_of_unused_counter` |
| 规划尚未接纳便进入未知或终止状态，预算误标为交付阶段 | 预算阶段按是否已有合法计划判定，不能仅凭目标是否仍为 `planning` | `test_missing_submission_remains_unknown_without_replay` |
| 多节点状态查询重复连接、验证计划及读取原事件 | `_decode_mission/_decode_job` 保留完整验证，一次读事务批量解析；不跨调用缓存 | `test_batch_read_checks_one_goal_and_original_event_without_repeating_connections`、`test_each_batch_entry_retains_hash_json_profile_dependency_and_resource_validation`、`test_plan_and_original_event_are_revalidated_on_each_snapshot` |
| 目标与任务之间并发更新形成混合快照 | 显式 `BEGIN` 绑定目标账本读取；返回嵌套对象独立，不回写 | `test_goal_and_jobs_share_one_read_transaction_during_concurrent_update`、`test_snapshot_returns_independent_nested_objects_and_does_not_write` |
| 服务一轮内构造两次状态 | `tick(include_status=False)` 只推进，服务在候选及信道处理后读取一次最终状态 | `test_service_builds_one_fresh_snapshot_per_tick` |
| 工作进行中缺少确切节点的执行指令入口 | `SuperagentGuidance` 复用 `Harness.steer`，绑定原运行和轮次；仅后续集成及复核接收已应用上下文 | `test_node_instruction_reaches_original_run_once_and_later_independent_context` |
| 引导等待锁期间来源撤销 | `before_accept` 在锁内同步复核，原接受事件之前不让出执行 | `test_revocation_during_steering_lock_wait_prevents_acceptance` |
| 复用会话使迟到输入误投给新运行 | 新请求重新核对原 job/run/turn，终止或错误归属明确拒绝 | `test_late_original_turn_does_not_redirect_to_new_run_in_same_conversation`、`test_new_requests_with_wrong_target_or_authority_never_reach_native_steering` |
| 引导接受返回或本地确认丢失 | `submitting/unknown` 只回查原稳定回执，不再调用 `steer` | `test_lost_accepted_return_reconciles_original_request_without_replay`、`test_confirmation_write_failure_recovers_one_original_acceptance`、`test_unconfirmed_intent_only_queries_and_keeps_original_run` |
| 关闭或重启后文件仍显示等待应用 | 原生回执按实际活动运行解析；宿主关闭后统一刷新最终投影 | `test_accepted_instruction_survives_revocation_and_close_flushes_unapplied_receipt`、`test_restart_keeps_original_unapplied_receipt_without_new_model_or_submission`、`test_close_before_queued_tick_enters_guidance_is_normal_shutdown` |
| 展示原文冒充真正模型指令，或异步准入未实际执行 | 接受与应用事件均核对真实正文及原运行；同步检查拒绝 awaitable 返回 | `test_original_display_text_cannot_replace_model_instruction_identity`、`test_async_acceptance_guards_are_rejected_and_existing_receipts_skip_new_guard` |
| 旧指令占满上下文，或截断清单误判唯一目标 | 有界上下文优先保留最近接纳内容；截断时要求显式任务，损坏投影保留并拒绝 | `test_recent_applied_guidance_retains_newest_correction_with_global_budget`、`test_ambiguous_and_truncated_status_require_exact_job`、`test_corrupt_target_snapshot_is_rejected_without_creating_or_rewriting_state` |
| 长节点阶段结束被误当作逻辑任务完成 | `checkpointed` 不解锁依赖，新阶段导入累计文件，最终阶段才参与集成 | `test_fresh_stages_preserve_files_and_unlock_only_the_final_delivery` |
| 检查点后重启或冻结回执丢失导致重做工作 | 原阶段保持原提交身份，捕获与导入按原交付只读回查，新阶段独立派工 | `test_restart_after_checkpoint_continues_without_replaying_the_previous_stage`、`test_lost_checkpoint_capture_confirmation_uses_original_frozen_delivery` |
| 没有结束的后台写入或未确认工具结果进入接续 | 原运行收尾、后台作业及原轮工具配对均进入准入；未确认保持未知 | `test_background_work_finishes_before_checkpoint_or_next_stage`、`test_unknown_tool_result_prevents_capture_and_continuation` |
| 长节点持续请求继续或重复原报告 | 独立阶段预算、同树同检查点的明确停滞判定，不解锁依赖或发布候选 | `test_budget_exhaustion_and_repeated_unchanged_checkpoint_do_not_unlock_dependencies` |
| 普通报告或损坏缓存冒充有效阶段交接 | 严格检查点协议，消费时重新核对原运行输出及工具证据 | `test_blocked_and_malformed_checkpoints_do_not_get_automatic_retries`、`test_corrupt_checkpoint_cache_cannot_supply_new_context_or_release_dependencies` |
| 新上下文丢失本节点已应用指令，或将指令混入其他节点 | `applied_context(node=...)` 按原工作节点过滤，最终集成／复核仍获得目标上下文 | `test_node_guidance_is_carried_only_to_its_own_continuation_and_final_review` |
| 取消旧目标时，共用规划会话已经在执行另一目标 | `MissionControlRuntime._cancel` 核对原任务资源、提交键和 `runtime.current`，只取消原操作句柄 | `test_cancelling_an_old_goal_never_interrupts_another_goal_in_the_shared_planner` |
| 取消后仍计入活动目标上限，后续目标无法接纳 | 尚在调度的目标进入独立 `cancelled` 终态，保留原运行并释放目标名额 | `test_cancelled_goals_release_admission_capacity_without_replaying_the_old_goal` |
| 超过百项目标时，最近列表漏掉需要停止的旧目标 | 控制采用原目标身份和完整活动清单，不从状态摘要选择停止对象 | `test_cancellation_finds_an_old_active_goal_beyond_a_hundred_recent_goals` |
| 暂停在异步合并预检期间到达，随后仍导入文件 | `_control_allows` 在资源准备及写入边界采用原目标控制，继续后复用原资源 | `test_pause_during_import_preflight_blocks_late_write_and_resume_uses_original_resources` |
| 控制队列前部都是未知目标，或时钟回拨改变动作顺序 | 持久插入顺序、有限轮转及每目标先后校验 | `test_unknown_targets_do_not_starve_later_valid_control_or_reorder_one_target`、`test_wall_clock_rollback_does_not_reorder_control_intent` |
| 停止慢后台进程阻塞其他目标，或停止失败被自动重试 | 每个所属作业拥有单次停止任务，状态保留未知，关闭继续传播原失败 | `test_slow_background_cancellation_does_not_block_another_goal`、`test_failed_background_stop_stays_unknown_and_is_not_replayed_by_control` |
| 原提交或重启后的后台进程未确认，被取消回执误称停止成功 | 控制模式与实际收尾分开；确切后台回执可核对，不可用保持未知，不向旧 PID 投递 | `test_cancel_does_not_resolve_or_replay_an_unknown_submission`、`test_restart_uses_exac
| 最近十六项目标全部暂停，隐藏的活动目标没有计入待执行 | 全部活动清单与展示统计来自同一目标读事务，再按原控制模式计数 | `test_pending_counts_include_active_goals_hidden_by_the_recent_list`、`test_full_active_inventory_and_d
| 损坏控制 JSON、模式或修订被当作合法历史或默认运行 | 严格解析、模式关联原已应用回执、修订核对真实已结算命令数，失败保留原件 | `test_corrupt_command_is_rejected_on_read_duplicate_and_reconcile_without_repair`、`test_mode_and_meta_corruption_are_not_treated_as_default_running` |
| 并发操作者重复控制、控制回执缺失或原动作被拒绝 | 稳定请求原子认领；独立只读回执入口，单批 CLI 按本条原回执核对退出码 | `test_concurrent_operators_share_one_stable_control_receipt`、`test_missing_receipt_is_read_only_and_known_receipts_keep_original_status`、`test_once_control_exit_code_comes_from_the_original_receipt` |
| 多模板有效配置超过单个说明字段大小 | 控制配置身份允许完整已验证配置，不按单个说明的大小截断 | `test_large_valid_profile_configuration_opens_and_closes_without_model_requests` |

这些回归均使用隔离临时数据和确定性模型传输。命令成功和调用配对能验证协调行为，不能验证在线模型的判断质量。

## 账本读取基准

2026-11-03 在本机 macOS、Python 3.14.7 使用同一 `scripts/benchmark_superagent_reads.py::benchmark` 比较基线 `8862d91` 与快照实现 `506ffc2` 的目标

| `jobs()` 单次读取 | 基线 | 快照实现 |
|---|---:|---:|
| 目标账本连接 | 17 | 1 |
| 原收件事件读取 | 8 | 1 |
| 中位耗时 | 4.9253 ms | 0.5309 ms |
| p95 耗时 | 5.3717 ms | 0.6282 ms |
| Python 分配峰值 | 29,480 B | 26,055 B |

基准脚本 SHA-256 为 `b2d880e214f5723982ed929c4aa8d3e1694e9a1f19017c37a645e4ddd8fc94ba`；基线存储源码为 `f157f4d1c1bd6db6a549e632c01b96dcef3ca338f1788dc5b9b478668afc4ded`，快照源码为 `a691cd2459ede295e77b9552d0559e5b73bf4111e22572ade24031f5fd45dc71`。当前实现可用 `python scripts/benchmark_superagent_reads.py` 复测；`benchmark(store_type)` 可接收从上述基线源码独立加载的存储类，保留同一测量方法。该结果仅证明固定小账本读取减少重复工作，不代表完整状态生成、模型执行、真实任务吞吐或跨平台性能；没有据此给出端到端加速结论。

### Phase 4.5：持续协作与既有信道

本节核对 `CoordinatorDeliveryRuntime` → SourceRegistry / Authorization / OutboxEffectService → 已认证 HTTP 客户端 → 只读 Provider Lookup，以及统一服务到原生指令回执的周期回查。既有阶段指令上下文和 DAG Attention / Handback 回归继续保留；本次新增证据针对完整服务接线。

| 触发条件 | 源码措施 | 对应回归 |
|---|---|---|
| 重复来源事件、跟进 ACK 丢失或飞书后续消息产生另一个 Channel | 来源入口复用 Trigger，跟进绑定原 InputDelivery，回复目标由原 Trigger 定位，全部 applied 后才读标记 | `test_github_followup_uses_original_delivery_key_and_never_starts_extra_mission`、`test_feishu_followup_keeps_original_reply_target_without_duplicate_result` |
| 部分跟进受理后原 Assignment 结束，恢复丢掉未知目标 | 原生投递前同事务冻结完整目标集合，终态 Assignment 仍回查原键 | `test_route_trigger_reconciles_partial_fanout_without_replaying_unknown_delivery` |
| POST 成功但回执丢失，或恢复遇到未找到、多个匹配、分页不完整 | 只凭唯一精确远端对象结算 sent；unknown 仅 GET 原操作，重启不 POST | `test_github_lost_post_ack_recovers_after_restart_without_second_post`、`test_feishu_lost_post_ack_recovers_after_restart_without_second_post`、`test_lost_local_write_ack_is_reconciled_without_a_second_publish` |
| 刷新凭据期间撤权，或关闭期间仍有所属发送 | 原生写入前同步复核；令牌刷新后再次复核，关闭等待所属请求 | `test_revocation_while_credentials_are_pending_prevents_late_provider_post`、`test_close_waits_for_owned_send_and_never_starts_late_post`、`test_real_feishu_token_refresh_rechecks_revocation_before_http_reply` |
| 真实传输无法消费发送准入或认证头 | JSONClient 在线程内执行准入回调，GitHubClient 原样传播并按原目标只读确认 | `test_real_github_http_transport_sends_then_confirms_original_comment` |
| Tick 或重启重新开启禁用回复能力、来源变更后沿用旧授权 | v20 独立绑定及 SQL 修订守卫；保留不可变 Channel，只初始化未登记绑定；来源修订变化后明确重新登记 | `test_reply_binding_preserves_immutable_channel_and_requires_explicit_regrant`、`test_migration20_preserves_v19_effects_and_rejects_conflicting_binding_table_atomically` |
| 已完成真实产物重复发布、语言切换改写冻结正文或重启重新执行 | 结果绑定 passing Review、候选及冻结语言，稳定 Outbox 身份；本机文件仅投影 | `test_staged_work_survives_restart_and_reaches_independent_quality_gate`、`test_local_result_is_projected_once_and_restart_does_not_execute_or_send_again`、`test_revocation_after_capture_prevents_queued_result_publication` |
| Attention 答案重放旧 Run，或 Handback 输入被再次派发 | 唯一获准人工答案关联原问题并创建新有界 Mission；Handback 只归还确切跟进输入 | `test_attention_answer_starts_fresh_bounded_activity_with_original_question_context`、`test_handback_settles_only_original_followup_and_restart_never_redelivers_it` |
| accepted-pending 指令没有周期追踪，或首个无 Adapter 的 unknown 永久饿死后续 Effect | 服务周期查询原 request_id；有界轮转恢复，未确认写入不重试 | `test_periodic_instruction_reconciliation_tracks_application_without_resubmission`、`test_unavailable_unknown_does_not_starve_later_effect_in_bounded_ticks` |

2026-11-08，本节新增或修改的服务、迁移、跟进、轮转用例及真实 Harness / Git 用例已通过定向验证。首次批量命令中真实用例类名写错导致加载错误；更正类名后单独通过。固定目标的终态 Assignment 回归曾暴露 NULL goal_ref 的 SQL 排除问题，修正后复测通过。未运行全量 Python／Web 回归、生产构建、桌面／浏览器专项；未访问外部账号或在线模型。

## 待验证风险与产品缺口

- 首次原生活动观察不可信时，首次后续可信观察只建立持久基线，不重置停滞时间或解除既有阻断；Schema v17 将历史快照保守标为未建立可信基线。若真实进度发生在可信基线建立前，该变化无法从旧指纹中追溯证明，必须等待后续可核验变化才恢复；这是避免误解锁的保守恢复代价，仍需真实长时运行评估。
- 完成标准由模型规划，语义覆盖与复核质量需要真实在线任务集、固定模型和人工判定；任意 Shell 正文的语义没有被宿主证明，显式 cwd 检查不是沙箱。
- 现有测试在本机运行；没有取得 Windows／Linux 实机、多设备、外部账号、长期负载或模型成本对比证据。
- 统一服务已支持原 Thread 跟进、节点指令和 Attention 答案；跨来源目标续接、目标验收或依赖变更、动态重规划及人工未知结果核实界面仍待开发。CLI 仍使用旧入口，不能把显式统一 API 的能力写成默认 CLI 已提供。
- 合并冲突、失败或中断运行、未知操作和资源总容量耗尽保留明确状态；当前不自动解决冲突、迁移配置、清理历史资源或重放任务。
- 长节点阶段接续为显式协议，真实模型的交接质量、是否过早选择 `ready`、上下文重建成本及长时负载仍需独立在线评估。同树同报告的停止条件不能识别所有语义停滞；新阶段也不能自动带走忽略文件、环境、进程或外部服务状态。
- 目标控制仅限可信本机操作者，尚未提供远端控制认证或多人权限审批工作台。停止只作用于当前拥有的句柄；重启前外部进程没有确切终态回执时仍需核实，不能根据旧 PID 重放停止。控制历史有明确容量上限，当前不自动归档或迁移。
- 状态只保存有界摘要；完整复核在账本，原报告、工具与用量在原运行。仍需要面向普通用户的工作台与分页读取接口。

尚无统一在线任务集与独立业务判定的比较证据，不记录“世界最强”或“全面超过其他系统”的结论。
