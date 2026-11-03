# Superagent 协调审查

本记录关联当前源码与业务回归，维护设计依据、已核对的问题和待验证风险。产品配置与完整行为统一见 [Superagent 模式](superagent.md)，验证入口见 [开发与测试](development.md)。审查不读取或依赖任何 `research/` 目录。

## 源码事实与设计选择

默认 `superagent_router.py::_decision` 只接受每事件一个工作任务；`superagent_results.py::_poll` 只把内部结果整理为回复。它们保留单任务、订阅和跨来源续接用途，不能承担复杂目标的依赖规划与验收。自主方式由 `SuperagentService.open` 在显式配置时装配 `SuperagentMissions`，不是打开原宿主的普通团队／子智能体开关。

`MissionStore` 仅保存协调事实；运行与工具事实仍来自 `RunController.find_submission`、原对话和 `tool_invocations`。`SuperagentWorkers` 在自主模式从账本取得共同资源组和冻结提交，不订阅节点 Thread。工作结果经已有 `capture_delivery` → `plan_delivery_merge` → `apply_delivery_merge` 交接，最终集成节点负责修正；每轮复核创建独立资源。现有 `worktree_delivery.py` 的共同组、基线和交付谱系约束均保留。

一手资料支持的工程取舍：

- [OpenAI 多智能体编排](https://developers.openai.com/api/docs/guides/agents-api/multi-agent)：适合独立问题的子智能体拥有独立上下文，主协调者汇总结果。这里采用有界并行与最小依赖报告，不将所有工作历史混入每个 Worker。
- [Claude Code Agent teams](https://code.claude.com/docs/en/agent-teams)：任务依赖、原子认领、自动解锁和完成质量门。这里由确定性宿主执行依赖和完成门禁，模型不直接改账本状态。
- [Anthropic 长期应用 Harness](https://www.anthropic.com/research/harness-design-long-running-apps) 与[长期智能体 Harness](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents)：规划、生成和独立评估分工，明确完成清单，防止仅凭自评提前结束。这里将集成与复核分开，并限制修正预算。
- [DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)：工具轮次的推理字段有明确提供方协议。新协调器继续使用既有模型传输，不复制另一套请求协议，也不将提供方回灌失败解释为“缺少自主性”。
- [Codex App Server 的运行中引导](https://learn.chatgpt.com/docs/app-server#steer-an-active-turn) 要求绑定确切活动轮次；[OpenAI Mid-turn steering](https://developers.openai.com/api/docs/guides/steering) 区分已受理与模型实际应用。这里据此选择原运行／轮次绑定、独立意图与原生回执分工；采用宿主原日志验证应用，不把排队或模型自述当成完成。

用户提供的文章把强依赖任务协作列为开放问题；参考原型的 Ticket／Assignment 主要承接独立任务。这些是选择目标依赖图与真实产物交接的背景，不构成质量或吞吐优于其他系统的证据。

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

这些回归均使用隔离临时数据和确定性模型传输。命令成功和调用配对能验证协调行为，不能验证在线模型的判断质量。

## 账本读取基准

2026-11-03 在本机 macOS、Python 3.14.7 使用同一 `scripts/benchmark_superagent_reads.py::benchmark` 比较基线 `8862d91` 与快照实现 `506ffc2` 的目标存储源码。两版源码分别在隔离临时数据库运行，未切换或改写主工作区。固定八个待执行工作节点、十六条完成标准，预热二十次后各采样一百次；连接与原事件次数单独统计，计时阶段不保留计数包装，分配峰值由另一次读取测量。

| `jobs()` 单次读取 | 基线 | 快照实现 |
|---|---:|---:|
| 目标账本连接 | 17 | 1 |
| 原收件事件读取 | 8 | 1 |
| 中位耗时 | 4.9253 ms | 0.5309 ms |
| p95 耗时 | 5.3717 ms | 0.6282 ms |
| Python 分配峰值 | 29,480 B | 26,055 B |

基准脚本 SHA-256 为 `b2d880e214f5723982ed929c4aa8d3e1694e9a1f19017c37a645e4ddd8fc94ba`；基线存储源码为 `f157f4d1c1bd6db6a549e632c01b96dcef3ca338f1788dc5b9b478668afc4ded`，快照源码为 `a691cd2459ede295e77b9552d0559e5b73bf4111e22572ade24031f5fd45dc71`。当前实现可用 `python scripts/benchmark_superagent_reads.py` 复测；`benchmark(store_type)` 可接收从上述基线源码独立加载的存储类，保留同一测量方法。该结果仅证明固定小账本读取减少重复工作，不代表完整状态生成、模型执行、真实任务吞吐或跨平台性能；没有据此给出端到端加速结论。

## 待验证风险与产品缺口

- 完成标准由模型规划，语义覆盖与复核质量需要真实在线任务集、固定模型和人工判定；任意 Shell 正文的语义没有被宿主证明，显式 cwd 检查不是沙箱。
- 现有测试在本机运行；没有取得 Windows／Linux 实机、多设备、外部账号、长期负载或模型成本对比证据。
- 自主目标已支持可信本机节点指令，但没有中途问答、跨来源目标续接、目标验收或依赖变更、动态重规划和人工未知结果核实界面。节点不订阅来源 Thread，不能把默认 Router 的跟进能力写成自主目标已支持；本机指令也不代表远端信道或工作台已经支持交互。
- 合并冲突、失败或中断运行、未知操作和资源总容量耗尽保留明确状态；当前不自动解决冲突、迁移配置、清理历史资源或重放任务。
- 状态只保存有界摘要；完整复核在账本，原报告、工具与用量在原运行。仍需要面向普通用户的工作台与分页读取接口。

尚无统一在线任务集与独立业务判定的比较证据，不记录“世界最强”或“全面超过其他系统”的结论。
