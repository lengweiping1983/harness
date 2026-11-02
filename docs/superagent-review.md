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
| 同一实例切换语言 | 实际规划调用按当前宿主语言解析显式资源 | `test_runtime_language_round_trip_reaches_actual_planner_requests` |

这些回归均使用隔离临时数据和确定性模型传输。命令成功和调用配对能验证协调行为，不能验证在线模型的判断质量。

## 待验证风险与产品缺口

- 完成标准由模型规划，语义覆盖与复核质量需要真实在线任务集、固定模型和人工判定；任意 Shell 正文的语义没有被宿主证明，显式 cwd 检查不是沙箱。
- 现有测试在本机运行；没有取得 Windows／Linux 实机、多设备、外部账号、长期负载或模型成本对比证据。
- 自主目标没有中途问答／引导、跨来源目标续接、动态重规划和人工未知结果核实界面。节点不订阅来源 Thread，不能把默认 Router 的跟进能力写成自主目标已支持。
- 合并冲突、失败或中断运行、未知操作和资源总容量耗尽保留明确状态；当前不自动解决冲突、迁移配置、清理历史资源或重放任务。
- 状态只保存有界摘要；完整复核在账本，原报告、工具与用量在原运行。仍需要面向普通用户的工作台与分页读取接口。

没有可复现实测，不记录“世界最强”“全面超过其他系统”或性能提升结论。
