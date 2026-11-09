# Superagent 协调审查

本记录关联当前源码与业务回归，维护设计依据、已核对的问题和待验证风险。产品配置与完整行为统一见 [Superagent 模式](superagent.md)，验证入口见 [开发与测试](development.md)。审查不读取或依赖任何 `research/` 目录。

## 源码事实与设计选择

Phase 5.2–5.6 增量从只读查询提交 `146b202` 接续。`SuperagentService.open` → `superagent_bootstrap.open_service` → 当前配置 → CoordinatorHost → SourceRegistry / Authorization / Execution / Scheduler / DeliveryRuntime 是唯一生产链。只读本机命令直接打开现有协调账本，不构造模型；没有旧 Router / Autonomy 选择器或 Shadow 入口。

`CoordinatorStore` 保存协调事实和原生调用引用，Harness 继续持有原始 Session / Run / Tool / Worktree。`HarnessExecutorAdapter` 的 Worker Lookup 由 CoordinatorExecutionService 注册，`Harness` 的实际运行与 AgentLoop 入口重新核对资源身份和工具范围；关闭由相同实例锁覆盖原生宿主清理和最终状态投影。包发现使用现有 setuptools 规则，CLI 直接引用统一服务；认证客户端和监听器不依赖退役模块。

长节点由 `CoordinatorDagScheduler` 使用同一逻辑 Job 下的新 Assignment；纯检查点校验保留在 `superagent_checkpoints.py`。输入、指令、控制、效果与 Outbox 均在协调账本，受理与应用分开，unknown 只回查原身份。完整产品行为维护在产品页，本记录只保存问题证据和迁接依据。

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

本节相对于整合改造前 `5f1c3b7` 核对当前增量，覆盖统一模型、显式内部适配入口、Phase 4.2 唯一服务生命周期、Phase 4.3 DAG Scheduler 真实交付闭环，以及 Phase 4.4 的独立质量门、有限修正、阶段接续与故障恢复。2026-11-08 已完成统一内部链路的定向验收；Phase 4.5 已在统一服务交付 Sender 与持续协作；Phase 4.6 已切换生产 CLI、默认路由并删除旧生产编排；对应证据见本页入口收口记录。

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

文件适配回归使用真实临时 Git 工作树和确定性模型传输驱动的原生 Run，不代表模型判断质量、生产账号、长期负载或跨平台实机验收。2026-11-06，`test_superagent_coordinator_store`、`test_superagent_unified_artifacts`、`test_superagent_unified_execution`、`test_superagent_execution` 与 `test_superagent_unified_model` 共 112 项回归全部通过，相关新增守卫的直接 SQL 越界、跨 Host 并发和畸形回执用例均在其中。后续缺口以领域模型的当前实现边界为准。 同日，`test_superagent_coordinator_host`、`test_superagent_phase2` 和 `test_superagent_service` 共 85 项回归全部通过，当时验证 Host 默认不依赖 legacy_state_dir / Shadow；这是历史过程证据。Phase 4.6 已删除显式 Shadow 与旧目录参数，当前回归改为核对 API 不存在、原件保留及目录混用拒绝。

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

**提交门禁状态（2026-11-06）：** 定向 `.venv` 回归已通过。`harness_verify` 曾在执行命令前被当前 MCP 进程内存中的旧执行器 8 MiB 指纹上限拒绝；仓库源码已有独立 128 MiB 单文件／512 MiB 总量指纹上限，且已将对应 `files.py` / `runtime.py` 同步到本机安装目录。该记录仅描述当时的工具环境，不能作为当前代码未交付的判断；本次按当前仓库约定执行具体新增或修改用例，没有运行该聚合入口。

## 退役编排的业务回归迁接

删除旧实现前核对生产入口、动态 Worker 注册、原生工具执行和资源分发。旧架构装配断言替换为无旧 API / 导入的断言；来源、授权、文件、证据、停止、指令和幂等业务保留在下列当前消费者中。旧文件的测试数量不作为业务完成或性能证据，未在本次运行的既有用例不能称作本次通过。

| 退役测试／编排家族 | 当前消费者与可核验回归 |
|---|---|
| Inbox / Router / Dispatcher：来源、批次、稳定提交和恢复 | SourceRegistry / Authorization / CoordinatorModels；Phase2 的来源冲突与授权回归保留，生产入口 `test_concurrent_local_submissions_keep_one_trigger_and_conflicting_body_is_preserved`、`test_lost_coordinator_submit_ack_finds_original_run_and_never_replays`、`test_absent_unknown_coordinator_submission_is_not_resubmitted` |
| Worker / Binding：资源归属、工具限制和跨来源参与 | HarnessExecutorAdapter / CoordinatorWorker；`test_worker_scope_and_input_receipt_bind_the_original_assignment`、`test_readonly_policy_refuses_a_visible_write_in_the_native_worker`、`test_explicit_cross_source_subscription_keeps_original_run_and_each_reply_target`；Subscription 必须先有确切授权，不自动猜测跨来源关联 |
| Missions：冻结基线、真实文件、依赖与独立质量门 | CoordinatorDagScheduler / Artifacts；`test_shared_dag_scheduler_delivers_real_binary_and_deletions_and_recovers_lost_import_receipt`、`test_production_file_task_uses_integrator_independent_review_and_frozen_result`、`test_failed_review_runs_one_fresh_repair_round_and_completes_quality_gate` |
| Results：冻结正文、原目标发布及重启去重 | CoordinatorDeliveryRuntime / Outbox / EffectRecovery；`test_local_result_is_projected_once_and_restart_does_not_execute_or_send_again`、两个 Provider 的 lost_post_ack 回归及真实 CLI 子进程用例 |
| Control / ControlRuntime：暂停、原 Run 停止和未知收尾 | CoordinatorControl / Executor；`test_pause_resume_and_instruction_queue_use_original_native_run`、`test_unified_service_revoke_blocks_resume_and_cancel_settles_original_run`、`test_unknown_cancel_stop_is_not_replayed_and_can_be_reconciled` |
| Guidance / GuidanceLocal：原 Run / Turn、稳定回执及应用上下文 | CoordinatorMissionInstruction / 同库本机请求；`test_real_followup_receipt_binds_original_run_and_is_applied_without_extra_mission`、`test_pause_resume_and_instruction_queue_use_original_native_run`，UnifiedExecution 的锁内撤权、错目标和 unknown 回查，以及 UnifiedInstructionContext 的 actual_stage_submission 回归 |
| Checkpoints / Background / Watchdog：阶段、真实工具与所属后台作业 | 纯检查点协议用例保留；`test_staged_work_survives_restart_and_reaches_independent_quality_gate`、UnifiedCheckpoints 的阶段／预算／停滞／恢复回归保留；`test_superagent_background.py` 迁接真实后台回执、stdout / stderr 活动和未知停止，CoordinatorWatchdog 的持久可信进度回归保留 |
| MissionSnapshots / MissionStatus：只读、有界和原 Run 事件 | `superagent_status.snapshot` 在同一读事务读取有界 Mission / Job / Assignment，活动与待关注优先；`test_older_active_and_attention_missions_remain_visible_ahead_of_completed_history`、`test_mission_plan_and_jobs_share_one_read_transaction_during_concurrent_planning` 核对历史不会挤占活动及并发计划不混合快照；`test_superagent_mission_status.py` 保留原工具配对、轮次和隐私断言；生产入口核对状态无宿主／凭据、外来观察拒绝与缺失状态不创建目录 |
| GitHub / Feishu：验签、身份、原对象和真实 HTTP | 当前 SourceKind + 独立认证客户端；重写后的两个来源测试覆盖真实 Webhook、AES 标准互操作、挑战与错误语言；UnifiedDelivery 保留真实 HTTP POST / GET、刷新后撤权及 unknown 不重发 |

迁接后的当前协议不再保留旧事件目录、旧自动创建模板或旧任务快照格式。旧目标计划的重复实现被统一 PlanRevision / Scheduler 和质量门取代；历史迁移仍保留并验证。自动跨来源关联和动态重规划仍是明确的后续产品缺口，不把显式 Subscription API 写成自动能力。

## 账本读取基准

2026-11-03 在本机 macOS、Python 3.14.7 使用同一 `scripts/benchmark_superagent_reads.py::benchmark` 比较基线 `8862d91` 与快照实现 `506ffc2` 的目标

| `jobs()` 单次读取 | 基线 | 快照实现 |
|---|---:|---:|
| 目标账本连接 | 17 | 1 |
| 原收件事件读取 | 8 | 1 |
| 中位耗时 | 4.9253 ms | 0.5309 ms |
| p95 耗时 | 5.3717 ms | 0.6282 ms |
| Python 分配峰值 | 29,480 B | 26,055 B |

基准脚本 SHA-256 为 `b2d880e214f5723982ed929c4aa8d3e1694e9a1f19017c37a645e4ddd8fc94ba`；基线存储源码为 `f157f4d1c1bd6db6a549e632c01b96dcef3ca338f1788dc5b9b478668afc4ded`，快照源码为 `a691cd2459ede295e77b9552d0559e5b73bf4111e22572ade24031f5fd45dc71`。以上为已退役 MissionStore 的历史固定输入记录，不能用当前脚本复现或据此比较。当前 `scripts/benchmark_superagent_reads.py` 已改为统一 CoordinatorStore 的八个 Work、两个系统节点及十六条标准，预热 20 次后采样 100 次，读取当前只读快照；输入和实现均不同，不与旧数字直接比较。该结果仅证明固定小账本读取减少重复工作，不代表完整状态生成、模型执行、真实任务吞吐或跨平台性能；没有据此给出端到端加速结论。

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

### Phase 4.6：入口收口与旧编排退役的当前证据

| 触发条件 | 源码措施 | 对应回归 |
|---|---|---|
| CLI 仍选择旧编排、本机操作另建队列或重复任务 | 当前配置和 Bootstrap 只装配统一服务；本机事件、请求与控制同库，首次时间及原正文固定 | 生产入口完整执行／重启用例，`test_real_cli_subprocess_drives_unified_models_and_publishes_once`，并发提交及配置边界用例 |
| 运行时把 Task 误当 Run ID，真实跟进／指令不能投向原任务 | Executor 从 runtime.active 的 Task 读取原 _executions 记录，再核对 Assignment Run、Session 与 Agent Run | `test_real_followup_receipt_binds_original_run_and_is_applied_without_extra_mission`、`test_pause_resume_and_instruction_queue_use_original_native_run` |
| 原生协调提交 ACK 丢失、原记录缺失或相同 unavailable 批次反复研判 | 持久调用认领，冻结 Prompt / Session / Run；仅查原提交，相同冻结 unavailable 批次等待显式重新排队 | `test_lost_coordinator_submit_ack_finds_original_run_and_never_replays`、`test_absent_unknown_coordinator_submission_is_not_resubmitted` |
| 规划等待期间撤权或关闭，仍派生迟到 Worker；清理期间新实例接管 | 规划在事务外等待，恢复后重新准入；关闭立即停止新准入，原生清理与最终观察写入完成后才释放 Host 锁 | `test_planning_await_releases_database_and_rechecks_revocation_before_workers`、`test_close_during_planning_waits_original_model_and_starts_no_worker`、`test_instance_lease_is_held_until_owned_native_harnesses_finish_closing` |
| 配置或实例不符仍打开原生运行库，状态读取创建或升级数据 | 创建宿主前只读核对身份，锁内先升级协调 Schema；本机读仅附着现有库 | `test_changed_configuration_rejects_before_constructing_or_mutating_native_hosts`、`test_runtime_lease_rejects_second_owner_before_constructing_model_hosts`、只读现有库与缺失状态用例 |
| 凭据主体与获准机器人不同，或回复过长 | 新 POST 前核对 Provider 账号、资源与原消息；超限保留原文并明确拒绝，预检失败返回错误码且不认领写入 | `test_wrong_github_token_owner_never_posts_or_claims_send`、`test_wrong_feishu_bot_never_posts_or_claims_send`、`test_oversized_reply_preserves_frozen_body_and_never_reaches_provider` |
| GitHub 同事件用新 Delivery ID 重投，或原资源 URL 大小写不同 | 重投单独审计且保留原 Trigger / 生命周期；查回按资源 ID 与规范路径核对 | `test_actual_webhook_acknowledges_durable_intake_without_another_model_run`、`test_repository_case_in_original_receipt_does_not_force_reposting` |
| Schema 升级覆盖原 Effect 或 DDL 冲突留下半个迁移 | v21 保持旧迁移校验和，全部 DDL 和版本记录原子提交；模型、事件和请求身份不可变 | `test_every_prior_schema_version_upgrades_without_losing_effects`、`test_migration21_preserves_prior_effect_and_conflicting_table_rolls_back_all_new_objects`、`test_current_model_and_request_identities_cannot_be_rebound_or_replayed` |
| 新角色提示语言固定或重启改写已提交正文 | 当前语言资源在实际调用处解析，已提交提示与输出冻结；系统角色提示也经显式资源 | `test_native_model_prompts_consume_language_roundtrip_and_freeze_original_body`、`test_applied_context_resource_is_resolved_at_actual_stage_submission`；`test_coordinator_model_refuses_a_native_receipt_from_another_session` 拒绝外来运行凭据 |
| 动态装配或认证客户端仍隐式加载退役模块 | 删除旧模块与 API，CLI／脚本／示例／分发使用当前入口 | `test_unified_tick_and_authenticated_clients_do_not_import_legacy_orchestrators`、Phase2 与 Host 的已退役 API 拒绝用例 |

2026-11-08，本次最终定向验证覆盖 99 个不同用例，含针对性复测，全部通过，无跳过。覆盖生产入口／本机 CLI／来源、统一交付／后台与原事件投影、迁移守卫、实际 DAG 质量门及阶段接续、语言、控制／关闭和退役 API。语言生成器已按源执行，当前八 Work／两系统节点的隔离读取基准脚本执行成功；不与旧输入作性能比较。

验证使用隔离数据库、确定性模型、原生 Harness / Git 和本机 HTTP，不访问用户运行数据。执行过程中发现的失败先定位修复并重跑相应用例；未运行的全量 Python／Web、生产构建、桌面／浏览器专项、在线模型和外部账号不计为通过。正式使用页、架构、领域、ADR 与示例已同步当前单入口行为。

### Phase 5.1：分页查询与任务详情

从 `599dd88` 接续。当前调用链为 CLI → `query_local / mission_local` → 现有只读 CoordinatorStore → CoordinatorQueries；可信运行宿主通过 `CoordinatorHost.queries` 复用相同查询，操作与关闭共用租约。没有增加业务数据库、Schema 迁移或 HTTP 管理面。`PlanRevisionRepository.read` 与原 `get` 共用完整冻结计划校验，不在只读查询中创建写事务。

| 触发条件 | 当前源码措施 | 对应回归 |
|---|---|---|
| 历史超过状态摘要上限，翻页期间仍有新记录插入 | 固定资源插入水位，按唯一行位置前进；每页有界并返回末页标记 | `test_pages_cover_history_once_and_exclude_insertions_after_the_first_watermark` |
| 其他实例／资源／Mission 的游标被复用，或输入畸形 | 游标绑定账本与查询范围，严格字段、版本和整数边界校验；SQL 关系及列由宿主固定 | `test_cursors_reject_other_ledgers_resources_and_malformed_shapes`、`test_scope_and_projection_keep_other_missions_and_private_reports_out` |
| 列表泄露原报告或混入其他任务，真实产物和独立 Review 无法定位 | 所属 Mission 显式查询，Assignment／Artifact／Review 仅投影摘要与原身份引用 | `test_scope_and_projection_keep_other_missions_and_private_reports_out`、生产文件任务的真实 Artifact / Review 查询断言 |
| 并发封存计划造成详情的计划指针与 Job 数量不一致 | 单次详情及其计数共用读事务，计划使用现有完整 Hash／Job／依赖校验 | `test_detail_and_counts_keep_one_snapshot_while_another_connection_freezes_a_plan`、`test_corrupt_frozen_plan_is_refused_without_rewriting_its_original`，以及原计划 Hash／Job 物化拒绝用例 |
| 本机读取启动模型、迁移、修复或创建缺失状态 | 附着现有只读库；不读取凭据或运行观察；CLI 冲突在数据访问前拒绝 | `test_cli_queries_are_readonly_without_credentials_hosts_or_runtime_projection`、`test_missing_state_and_conflicting_cli_selectors_do_not_create_or_repair_data` |
| 查询期间关闭释放实例租约，或缓存查询句柄在关闭后继续工作 | Host 的相同操作租约覆盖查询全过程，关闭后拒绝缓存句柄 | `test_cached_host_queries_reject_after_close_and_hold_the_operation_lease` |
| 新 CLI 说明或查询错误固定首次语言 | 在实际呈现和抛错处解析显式双语资源 | `test_new_cli_help_is_consumed_in_english_chinese_english`、`test_same_query_instance_consumes_error_language_english_chinese_english` |

2026-11-08，本项 15 个定向用例通过，包含真实原生文件任务及原冻结计划校验回归，无失败或跳过。该查询提供逐页一致的当前账本视图，跨页不承诺冻结已有条目的状态。插入水位不代替原生运行证据或认证，条目数量限制不构成负载性能结论。验证仅针对本次新增／修改用例、真实隔离 Git 和原生文件质量门；没有运行全量回归、生产构建、桌面／浏览器专项或外部账号验收。该项仅覆盖查询；工作台与后续来源能力的当前证据见下一节。

### Phase 5.2至5.6：统一工作台与来源协作

本次核对 `146b202` 后的实际调用链：SettingsDialog 的 SuperagentPanel → 本机专用 `/api/v1/superagents` → 主 Harness 的 SuperagentWorkbench → 原查询／本机控制／CoordinatorOperator → 同一协调账本和 SuperagentService。工作台管理器不持有第二份 Mission 状态。公共协议由 `harness/web/superagent_protocol.py` 与原查询资源共同生成；双语文案由现有语言源生成，设置目录、路由、关闭入口及实际消费者同步更新。

| 触发条件 | 当前源码措施 | 对应回归 |
|---|---|---|
| HTTP 分发向工作台路由传入路径参数，处理器签名不接收参数，导致状态／列表／命令／媒体在业务入口前返回 500 | `Handler._dispatch` 按路由契约传入关键字参数；Superagent 的四个带参数处理器显式接收 `connection_id`／`media_id`。API 回归统一通过真实分发方法，不再直接调用处理器绕过签名 | `test_superagent_workbench_api.py` 的连接状态、资源列表、命令、媒体及拒绝路径用例 |
| 首次连接无账本实例时提示不明确，列表读取失败还使本机身份选项无法显示 | `superagent/status-unavailable` 映射为显式双语 `ui.sg.error.uninitialized`；组件在同一实例及页面代次验证后先发布本机选项和运行状态，列表失败仍保留来源及提交入口，不伪造空列表或隐式初始化数据 | `test_new_connection_keeps_local_options_without_initializing_state_or_models`、`test_api_error_keeps_resource_identity_in_english_chinese_english`，组件 `keeps local submission available when the ledger is uninitialized and translates its guidance without rereading` |
| 工作台重复启动、启动过程中停止或关闭页面 | 固定请求身份，按实例串行启动／停止；所属请求独立于 HTTP 等待，主宿主等待运行收尾 | `test_workbench_submits_starts_and_stops_the_real_unified_service_once`、`test_start_stop_race_waits_for_the_original_factory_and_close_owns_it`、`test_owned_loop_closes_services_after_mcp_failure` |
| 共享／远端请求读取配置路径，重复 JSON 字段或跨实例连接 | 本机 IP、Host／Origin 和主体边界；严格解析，在业务写入前拒绝 | `test_remote_shared_and_cross_origin_requests_cannot_connect_or_read_paths`、`test_malformed_and_duplicate_parameters_are_rejected_before_business_writes`、`test_changed_config_and_foreign_connection_are_rejected_without_rebinding` |
| 来源、策略或回复授权修订过期，重启用初始配置覆盖当前设置 | 领域服务与固定操作者回执共用事务；已有来源身份固定、可变配置及启停以账本为准，当前回复能力独立显式重授 | `test_source_and_policy_changes_commit_original_receipts_atomically_and_survive_restart`、`test_explicit_reply_grant_is_revision_bound_atomic_and_preserves_original_address`、`test_stored_enabled_revision_is_authoritative_over_disabled_local_seed` |
| 来源刷新并发创建监听器，或原来的可变 Chat 配置被误判身份变化 | 监听器刷新串行，使用当前来源配置解析凭据，停用后关闭所属监听器，不重放原输入 | `test_source_refresh_serializes_listener_creation_and_preserves_edited_config` |
| 人工答案被投向另一问题／线程，或重复点击重放原 Run | 精确 attention_id 与原来源／线程核验，沿已有 Mandate 与新有界 Mission；旧答案不会启动无关活动 | `test_local_attention_answer_uses_original_thread_and_starts_fresh_activity`，实际组件 `answers the exact attention through explicit local identity and thread` |
| 人工核实被当作成功证明或触发再次发送 | Host 提供原 Adapter 的只读 lookup 端口；先记录核实请求，再按原代次结算领域回执，说明不代替提供方证明 | `test_human_unknown_check_uses_original_provider_proof_and_never_posts_again`，实际组件 `inspects the original effect before checking its exact generation without a replay command` |
| Review／CI 的提及、成功、过时提交、模糊 PR 或异仓库结果取得执行权 | 新事件仅为受白名单及 Scope 约束的 context-only；CI 强制 Bot 与唯一 PR／Head／Base 绑定，Review 保留原 Commit，回复仍固定原目标 | `test_review_and_check_events_are_scoped_context_and_never_start_or_resume_work`、`test_check_without_unique_original_pull_or_with_foreign_head_never_claims_authority`、`test_github_lifecycle_variants_preserve_original_thread_and_authors` |
| 飞书接收就下载、任意资源或账号被混用、下载中撤权 | Webhook 仅持久引用；显式获取核验机器人、原消息／Root／Chat／资源，并在刷新令牌后和本地发布前复核来源 | `test_rich_post_preserves_text_resources_and_never_downloads_at_webhook_ack`、`test_media_foreign_owner_resource_and_mid_request_revocation_prevent_local_publication`、`test_guard_after_token_refresh_prevents_resource_get_when_authority_changes` |
| 文件伪装可执行页面、损坏原件被覆盖、撤权后读取缓存 | 验证真实图片，其他资源强制附件；摘要固定、损坏保留、每次缓存读取重验当前来源；认证 HTTP 有界且禁止重定向 | `test_feishu_image_is_verified_saved_once_and_corruption_never_overwrites_original`、`test_file_audio_video_media_are_opaque_and_cached_reads_respect_source_revocation`、`test_binary_read_uses_refreshed_token_and_enforces_size_and_redirect_boundary`、`test_media_route_binds_existing_connection_and_returns_safe_binary_headers` |
| 翻页丢历史、A 的回包覆盖 B、页面隐藏仍读取或迟到图片跨页面代次恢复 | 前端验证范围／游标／重复记录，按实例保留草稿与原请求；读绑定页面代次，可见性暂停读取／图片 | Superagent 客户端测试及 SuperagentPanel 的分页、切换、隐藏、unknown 查回、离开再返回同范围的媒体测试 |
| 语言切换丢用户原文或重新执行操作 | 实际 API 错误与相同组件消费显式资源，英文→中文→英文不重新读写或翻译用户原文 | `test_api_error_keeps_resource_identity_in_english_chinese_english`，客户端和组件的同实例语言测试 |
| 正式 Web 构建因工作台客户端的结果解包与连接断言类型错误而中断 | `web/src/lib/superagent.ts` 的 `call` 对已取得的结果保留资源错误或抛出原 `ApiError`，成功直接返回 `data`；`connection` 验证字段后构造生成契约要求的连接，不使用双重断言 | `superagent.test.ts` 覆盖连接列表／连接／状态四种运行状态、畸形字段／重复及跨实例身份、通用错误原诊断与单次请求；2026-11-09 本机该文件 8 项通过，无失败或跳过，正式入口 `npm --prefix web run build` 通过；未运行全量回归、桌面／浏览器专项 |
| 正常协调 Schema 升级失败覆盖原数据，或固定请求历史被改写 | 新迁移整笔回滚，保留 v21 原 Effect；回执、资源来源和原件不可静默覆盖／删除 | `test_v21_upgrade_preserves_original_effect_and_adds_empty_operator_media_tables`、`test_conflicting_media_table_rolls_back_entire_new_migration_and_retains_original`、`test_completed_receipt_payload_and_history_cannot_be_overwritten_or_deleted` |

上述新增／修改的 Python 和前端定向用例已在隔离数据下通过。开发中发现并修复 Host 查回端口未公开、来源初始禁用标志覆盖当前修订及媒体回包在页面往返后恢复的问题；回归分别对应原操作核实、当前来源修订和页面代次用例。迁移及 Chat 规范排序的夹具预期修正后复测通过，保留有效业务断言。语言和协议已从源重新生成。

本次未执行全量回归、lint／类型检查、完整生产构建或桌面／浏览器专项；组件测试使用实际组件与 HTTP 客户端的受控 Hook 宿主，不能替代真实浏览器视觉验收。GitHub／飞书使用签名事件、受控 Provider 和真实回环 HTTP，没有操作外部账号，也没有取得在线模型、多平台或长期负载证据。媒体只覆盖接收、显式资源获取及操作者呈现，不包含视觉模型或音视频理解。非 Outbox 未知原生效果仍由相应恢复流程结算；操作者说明不补造完成事实。

### Phase 6：隔离与远程执行

本轮增量基线为 `9c4c911`。核对的生产链为统一配置 `execution` → bootstrap 选择 RemoteExecutionAdapter → 原 Coordinator／Scheduler／Artifact／Review → 签名 WorkerGateway → SandboxNode → NativeWorkerNode → 同一个 HarnessExecutorAdapter。设备端不装配第二个 Coordinator。Schema v23 保存设备身份和原 Mission 租约，设备本地只保存原生引用与操作回执；配置导出、Worker CLI、Dockerfile、协议生成器、CLI／工作台查询和双语消费者已同步。

| 触发条件 | 源码措施与证据 | 对应定向回归 |
|---|---|---|
| 第一次观察前原 Run 已结束，单节点终态缺少开始时间 | `superagent_execution` 使用 DAG 已有的 `_run_started_at` 从原生事件取得精确时间，保留状态守卫 | `test_real_remote_harness_completes_through_original_executor_and_ledger` |
| 断线后原报告完成，unknown 恢复缺少确切 Run 审计 | 报告冻结审计保留原 `run_id` 与原输出／证据引用，不新建运行 | `test_disconnect_keeps_service_alive_and_reconnect_reads_original_run` |
| 提交／工作树响应丢失，设备重启或原写入仍 pending | 原生稳定资源和操作账本，先 pending 再调用一次；恢复只查询原 Run／资源，pending 拒绝重放 | `test_lost_remote_submit_ack_only_queries_original_run_and_never_resubmits`、`test_lost_workspace_ack_reuses_original_native_resource`、`test_native_kernel_restart_reads_original_run_without_new_model_calls`、`test_remote_native_pending_operation_is_not_replayed_after_reopen` |
| 伪签名、跨设备配置、执行策略不同或复用设备密钥 | 固定 HMAC／实例／设备／配置／nonce；实际 Profile 与 runtime_manifest 握手，密钥值必须独立 | `test_signature_device_and_profile_identity_fail_before_native_resources`、`test_runtime_policy_mismatch_fails_handshake_before_worker_creation`、`test_different_device_secret_references_cannot_resolve_to_the_same_signing_key` |
| 来源撤权、旧心跳、租约到期和迟到结果 | 同一 Mandate 重验，撤销固定原代次；设备持久零到期值不可被旧心跳恢复，文件操作排队后实际发送前重验准入，当前 UID／Run 句柄停止 | `test_source_revocation_reaches_native_gate_and_cannot_be_undone_by_old_heartbeat`、`test_lease_expiry_stops_the_owned_run_and_does_not_start_a_replacement`、`test_revoked_mission_stays_pinned_and_late_remote_receipt_is_fenced`、`test_queued_filesystem_request_revalidates_revoked_lease_before_starting_helper` |
| 关闭只得到停止受理回执，或第二内核打开同一状态 | aclose 只读等待原 Run 实际收尾，未知明确失败；设备持有原生运行跨进程锁 | `test_coordinator_close_stops_original_remote_run_and_waits_for_receipt`、`test_second_native_kernel_cannot_open_the_same_device_state` |
| 多设备容量与数据串用，文件交付绕过质量门 | Mission 固定原设备，未知／过期不释放容量；原文件交接、独立 Reviewer 与统一 Outbox | `test_two_devices_share_capacity_but_missions_never_move_after_expiry_or_restart`、`test_two_logical_devices_run_distinct_missions_and_never_exchange_receipts`、`test_remote_file_quality_gate` |
| 长期模型凭据进入容器、任意 URL／Header 转发或能力越界 | 宿主 Unix Broker 固定上游，只授原 Mission／模型／请求预算的短期能力；审计无凭据值，独立进程真实消费 | `test_real_unix_transport_consumes_scoped_capability_without_forwarding_worker_headers`、`test_expired_revoked_wrong_model_and_exhausted_capabilities_never_call_upstream`、`test_native_child_process_uses_unix_broker_and_preserves_original_submission` |
| Broker 启动绑定失败或重复／取消关闭等待 | 启动失败释放审计句柄；单次收尾任务拥有 Socket 和线程，重复调用等待原任务 | `test_failed_broker_bind_releases_audit_handle_and_repeated_close_waits_original_cleanup` |
| 路径／符号链接越界、文件观察缓存丢失或异步 grep 未消费 | 精确原工作树路径检查，文件工具使用持久辅助进程；真实文件读写、异步搜索及同桥接语言消费 | `test_absolute_targets_symlinks_and_glob_escape_are_rejected_but_original_workspace_is_allowed`、`test_filesystem_helper_keeps_observation_cache_and_consumes_async_grep`、`test_same_filesystem_bridge_consumes_scope_error_english_chinese_english` |
| Worker 改写 Git 配置使受信内核执行用户 Git 设置 | Shell 使用独立可写 Git 视图，IsolatedWorktreeService 始终显式指定保护目录；Artifact 有界对象读取也走同一服务 | `test_private_git_view_supports_git_without_mutating_kernel_refs`、`test_kernel_capture_ignores_worker_replaced_git_config_and_keeps_bounded_object_reads` |
| Docker 上下文变化、Rootful／远端 Engine 或配额未生效 | 探测／运行／终止固定同一本机 Unix Engine 及过滤后的 CLI 环境，避免后来的 DOCKER_CONTEXT 干扰；要求 Rootless，启动核对真实 cgroup 文件；无宿主回退 | `test_probe_pins_rootless_socket_and_does_not_pass_model_secrets_to_docker_client`、`test_rootful_or_remote_engine_is_rejected_instead_of_falling_back`、`test_owned_container_kill_keeps_pinned_engine_and_filtered_environment`、`test_actual_cgroup_values_must_be_bounded_and_complete_before_execution`；这些为受控输入验证，尚无真实 Docker 证据 |
| TLS 客户端停在握手，阻塞后续连接或网关关闭 | 监听 Socket 不在 accept 中同步握手，握手在有界连接线程及 Socket 超时内执行 | `test_stalled_tls_client_does_not_block_admission_of_the_next_connection`；回环夹具不代表部署证书验收 |
| 设备迁移冲突、配置重复字段、旧数据被覆盖 | 仅新增迁移，失败整笔回滚；原件及迁移校验和保留，模板独占创建，设备配置明确报错 | `test_migration_preserves_v22_effects_and_creates_no_fake_devices_or_leases`、`test_conflicting_lease_table_preserves_original_and_rolls_back_device_migration`、`test_invalid_worker_config_uses_explicit_error_and_preserves_original_bytes`及模板／协议回归 |
| 工作台错看另一 Mission 租约或语言切换重新执行 | 相同查询端口按 Mission 范围读取，不暴露凭据，页面仍复用原异步隔离；双语资源实际消费 | 修改的工作台 API 用例、远程任务查询断言，以及组件 `shows device ownership and the original lease without exposing credentials`、`reads a mission lease only within the selected mission detail` |

提交 `5df36cd` 中，五个新增 Phase 6 测试文件和修改的工作台 API 方法共 42 个后端定向用例通过；随后补充的真实回环 TLS 握手阻塞用例单独通过，共 43 个不同用例。原任务租约查询、文件排队撤权和固定 Engine 收尾均有对应当前断言。两个新增／修改组件用例通过，同文件其他 10 项因精确选择器未执行；此前旧组件输入的整文件结果不作为最终新增场景的替代证据。协议与语言从正式源正常生成，未手工编辑生成文件。

本轮没有运行全量回归、lint／类型检查、生成全套检查、生产构建或桌面／浏览器专项，没有使用在线模型、外部账号、部署或发布。两个设备用例是独立 Harness／Git 副本／密钥／网关的回环逻辑设备；独立进程用例显式注入原生子进程，仅验证 stdio 与 Unix 代理。当前宿主无 Docker，未构建／运行镜像，Linux Rootless 的真实 UID、PID、网络、文件、凭据、资源越权及跨平台双机断网／迟到回执仍未验收。采样磁盘预算不是硬性总盘配额；设备密钥握手不是硬件证明。这段为 `5df36cd` 的隔离基线记录；后续验收按下节的单设备／原生／可选隔离范围分别核对。

### Phase 6：单设备与无 Docker 原生入口

本次增量基线为 `5df36cd`。设备数量原本允许 1–32 台，但生产 `serve` 固定构造 SandboxNode，且 RemoteExecutionAdapter 的握手强制 `sandbox=docker`，导致单设备配置仍无法在没有 Docker 的环境启动。当前同一设备配置增加显式 `runner=native`：`serve` → `open_worker_node` → NativeRunner → 原 NativeWorkerNode／HarnessExecutorAdapter；同一 ModelBroker 提供原生进程内传输与 Docker Unix Socket 传输。`runner=docker` 或省略 runner 的固定 Docker 配置继续拒绝自动降级。原生方式使用宿主权限，不宣称 OS 隔离。

| 触发条件 | 当前源码措施 | 对应定向回归 |
|---|---|---|
| 单台设备没有 Docker、Rootless／cgroup 或 Unix Socket | native 分支不调用 sandbox_command、SandboxNode 或 SecretBroker Socket，导出 `sandbox:null`；设备列表下限保持 1 | `test_one_native_worker_opens_without_docker_unix_socket_or_container_configuration`、`test_single_native_device_export_has_no_docker_requirement_and_rejects_invalid_runner_or_empty_devices` |
| 原生设备被当作 Docker 设备，或 Docker 不可用时隐式在宿主执行 | 协调配置、导出及握手固定 runner，响应明确 `sandbox=none`；Docker 启动错误继续向上传播 | `test_native_device_cannot_satisfy_an_explicit_docker_runner_requirement`、`test_native_config_rejects_sandbox_limits_and_docker_failure_never_selects_native` |
| 单设备满载时第二任务提前进入工作树创建，缺少租约而卡在未确认状态 | `_ensure_worker` 在创建协调 Worker 前调用可选 reserve_mission 准入，设备满载明确背压，原任务完成后同设备继续 | `test_one_capacity_device_queues_another_mission_without_unknown_worker_or_duplicate_run` |
| 原生模型传输绕开能力或重试撤权请求，线程丢语言 | NativeBrokerTransport 复用 ModelBroker，原 ContextVar 能力／当前语言经 to_thread 传递；错误消费英文→中文→英文不再次转发 | `test_native_transport_consumes_shared_capability_and_actual_bilingual_errors`；修改的 Unix Transport 用例同时核对错误模型不消耗有效能力 |
| 单设备只接通内部类，真实入口无法闭环或重启产生新 Run | 真正启动 `python -m harness.superagent_worker --config` 子进程，签名 HTTP、固定模型端点、统一协调任务和 Outbox；停止后重新启动只查原 Run | `test_single_native_cli_device_completes_and_reopens_original_run_without_docker` |
| 原生文件任务未经过真实工具和质量门 | 同一 CLI 子进程执行写／读工具，Work → Artifact／Delivery → Integrator → 独立 Reviewer，保留三份工作树及原始 Run；仓库原文件不改写 | `test_native_cli_file_task_passes_artifact_integration_and_independent_review`，断言两份产物、两次应用交付及通过的独立 Review |

本次 16 个新增或修改输入的定向用例已通过。单台原生方式已在本机 macOS 取得上述真实 CLI 证据，模型端点仍为受控回环提供方，不代表在线模型质量。单设备任务排队、Runner 身份、协议源／生成产物、模板和完整示例均有对应验证；断线、撤权、丢回执、文件质量门及可选多逻辑设备保留原业务断言，按本次实际修改的夹具定向复核。验证结果按本次实际执行记录维护，不以基线的旧计数代表当前输入。

没有构建 Docker 镜像、运行全量回归、lint／类型检查、桌面／浏览器专项或访问外部账号。物理多机、其他操作系统及可选 Docker 越权／配额仍属于各自未验收专项，不能记作通过；它们不再是单设备无 Docker 原生方式的启动或完成前提。生产使用、完整配置与原生权限边界只维护在[产品页](superagent.md#隔离与远程-worker)。

## 待验证风险与产品缺口

- 首次原生活动观察不可信时，首次后续可信观察只建立持久基线，不重置停滞时间或解除既有阻断；Schema v17 将历史快照保守标为未建立可信基线。若真实进度发生在可信基线建立前，该变化无法从旧指纹中追溯证明，必须等待后续可核验变化才恢复；这是避免误解锁的保守恢复代价，仍需真实长时运行评估。
- 完成标准由模型规划，语义覆盖与复核质量需要真实在线任务集、固定模型和人工判定；任意 Shell 正文的语义没有被宿主证明，显式 cwd 检查不是沙箱。
- Phase 6 已取得本机双逻辑设备、原生进程与原回执恢复证据；没有取得 Windows／Linux 实机、真实双机、Rootless Docker 隔离越权、外部账号、长期负载或模型成本对比证据。
- 统一服务已支持原 Thread 跟进、节点指令、Attention 答案和确切授权的跨来源 Subscription。自动跨来源关联、目标验收或依赖变更、动态重规划仍待开发；CLI 与本机工作台均已使用统一入口。
- 合并冲突、失败或中断运行、未知操作和资源总容量耗尽保留明确状态；当前不自动解决冲突、迁移配置、清理历史资源或重放任务。
- 长节点阶段接续为显式协议，真实模型的交接质量、是否过早选择 `ready`、上下文重建成本及长时负载仍需独立在线评估。同树同报告的停止条件不能识别所有语义停滞；新阶段也不能自动带走忽略文件、环境、进程或外部服务状态。
- 目标控制仅限可信本机操作者，尚未提供远端控制认证或多人权限审批工作台。停止只作用于当前拥有的句柄；重启前外部进程没有确切终态回执时仍需核实，不能根据旧 PID 重放停止。控制历史有明确容量上限，当前不自动归档或迁移。
- 状态只保存有界摘要；完整复核在账本，原报告、工具与用量在原运行。分页摘要与任务详情已提供 SDK／CLI／本机工作台入口；完整原生工具记录与报告仍在所属运行，工作台不复制原生运行浏览器。

尚无统一在线任务集与独立业务判定的比较证据，不记录“世界最强”或“全面超过其他系统”的结论。
