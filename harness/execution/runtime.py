"""无需模型的开发运行层：会话、工具流水线、作业与验证。"""
from __future__ import annotations

from harness.core.text_encoding import diagnostic_text, utf8_prefix

import asyncio
import hashlib
import json
import os
import shutil
import uuid
from weakref import WeakValueDictionary
from contextlib import nullcontext

from ..i18n import tr
from ..core.errors import HarnessError, SkillNotFoundError
from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..core.jsonutil import to_jsonable
from ..llm.types import TextBlock, ToolUseBlock
from ..plugins.state import EscStateStore
from ..skills.registry import build_default_registry
from ..skills.tool import render_catalog
from ..tools.builtin.fs_tools import build_fs_tools
from ..tools.builtin.job_tools import JobRegistry
from ..tools.builtin.workspace_tools import collect_workspace_dependencies
from ..core.text_encoding import output_encoding
from ..tools.process_env import execution_env
from ..tools.builtin.pwsh_tools import _build_argv, _build_script, _resolve_powershell
from ..tools.job_archive import JobArchive
from .environment import ExecutionEnvironment, LocalExecutionEnvironment
from ..tools.process_output import collect_process, cleanup_process
from ..tools.registry import ToolDefinition, ToolRegistry, ToolResult, ToolRunContext
from ..tools.schema import validate_or_raise
from .catalog import definitions
from .capture import BoundedProcess
from .config import ExecutionConfig
from .files import (
    MAX_SNAPSHOT_FILE_BYTES,
    MAX_SNAPSHOT_TOTAL_BYTES,
    apply_changes,
    search,
    snapshot,
)
from .images import download_image, inspect_image
from .policy import PathPolicy, fail, file_version
from .scheduling import ExecutionGate
from .store import ExecutionStore
from .workspaces import sync_projects
from .pipeline import PipelineRuntime


class ExecutionRuntime(PipelineRuntime):
    """所有可信上下文来自本机配置与已认证身份，不接受模型覆盖。"""

    def __init__(self, config: ExecutionConfig, *, environment: ExecutionEnvironment | None = None):
        self.config = config
        self.execution_environment = environment if environment is not None else LocalExecutionEnvironment()
        self._configured_projects = dict(config.projects)
        self.store = ExecutionStore(config.home)
        try:
            self._initialize(config)
        except BaseException as primary:
            try:
                self.store.close()
            except BaseException as cleanup:
                raise_cleanup_errors([primary, cleanup])
            raise

    def _initialize(self, config: ExecutionConfig) -> None:
        """装配注册表及运行登记；构造失败由所属入口关闭已取得的存储。"""
        #: 技能启用状态（安装 ≠ 启用）；与本地 Agent 共用同一 home 的显式关闭项
        self.skill_home = config.skill_home if config.skill_home is not None else config.home
        self.esc_state = EscStateStore(self.skill_home)
        self.registry = ToolRegistry()
        self.native = ToolRegistry()
        for definition in build_fs_tools(None):
            self.native.register(definition)
        self.jobs = JobRegistry(max_result_bytes=config.max_output_chars, archive=JobArchive(config.home / "jobs.sqlite3"))
        self._locks: WeakValueDictionary[str, ExecutionGate] = WeakValueDictionary()
        self._tasks: set[asyncio.Task] = set()
        self._monitors: set[asyncio.Task] = set()
        self._job_evidence: dict[str, str] = {}
        self._job_monitors = {}
        self._pipeline_tasks = {}
        self._pipeline_controls = WeakValueDictionary()
        self.closed = False
        self._close_task: asyncio.Task | None = None
        self._shared_reads = {}
        for name, description, parameters, effect, method in definitions():
            handler = getattr(self, "_" + method)

            async def execute(args, ctx, selected=handler):
                try:
                    return await selected(args, ctx)
                except HarnessError as exc:
                    return ToolResult(content=[TextBlock(text=exc.message)], is_error=True,
                                      data={"error": exc.to_dict()})

            self.registry.register(ToolDefinition(schema={"name": name, "description": description, "parameters": parameters,
                **({"description_key": "execution.catalog.load_workspace_dependencies"} if name == "load_workspace_dependencies" else {}),
                **({"parameter_keys": {("properties", "output_encoding", "description"): "encoding.parameter"}} if name == "harness_exec" else {})},
                                                 execute=execute, effect=effect, timeout=None,
                                                 idempotent=effect == "read"))
            # 新增工具需单独核对共享状态，不能仅凭只读声明自动放行。
            if effect == "read" and method in {
                "context", "session_resume", "image_get", "read", "search",
                "git_status", "git_diff", "skill_list", "skill_load", "pipeline_get", "request_get",
                "workspace_dependencies",
            }:
                self._shared_reads[name] = self.registry.get(name)
        self.store.event('', 'runtime/start', {'instance_id': uuid.uuid4().hex})

    def _project(self, project_id, principal):
        project = self.config.projects.get(project_id)
        if project is None or principal not in project.principals:
            fail("execution/project-denied", tr('execution.runtime.6c8a84910bbf'))
        return project

    def _session(self, sid, principal):
        session = self.store.session(sid, principal)
        project = self._project(session["project_id"], principal)
        if session.get("project_root") != str(project.root):
            fail("execution/project-changed", tr('execution.runtime.73db6e7068e2'))
        return session, project

    def _policy(self, args, ctx):
        session, project = self._session(args["session_id"], ctx.services["principal"])
        return session, project, PathPolicy(project.root, self.config.home)

    def _dependency_project(self, args, principal):
        """只选择当前身份可访问的工作区，不根据服务进程目录或上次调用猜测。"""
        if 'session_id' in args and 'project_id' in args:
            fail('execution/workspace-selection-invalid', tr('execution.workspace.selection_invalid'))
        if 'session_id' in args:
            session, project = self._session(args['session_id'], principal)
            return session['project_id'], project
        if 'project_id' in args:
            return args['project_id'], self._project(args['project_id'], principal)
        candidates = [(key, project) for key, project in self.config.projects.items()
                      if principal in project.principals]
        if len(candidates) != 1:
            fail('execution/workspace-selection-required', tr('execution.workspace.selection_required'))
        return candidates[0]

    def _skills(self, project):
        """按项目根构建技能目录：项目技能覆盖用户与内置技能，停用的不暴露。"""
        registry = build_default_registry(cwd=str(project.root), home=str(self.skill_home))
        try:
            enabled = {skill.name for skill in registry.list() if self.esc_state.is_enabled("skill", skill.name)}
        except ValueError as exc:
            fail("execution/skill-state-invalid", tr('execution.runtime.skill_state', detail=str(exc)))
        registry.set_enabled_filter(lambda skill: skill.name in enabled)
        return registry

    @staticmethod
    def _skill_entries(registry):
        """目录条目；不输出项目外的绝对路径，模型按需加载即可。"""
        return [{"name": skill.name, "description": skill.description,
                 "when_to_use": skill.when_to_use, "source": skill.source}
                for skill in registry.list(model_invocable_only=True)]

    async def call(self, principal: str, name: str, arguments: dict):
        """副作用任务独立存活，客户端中断不会取消已进入执行的工作。"""
        task = asyncio.create_task(self._call(principal, name, arguments))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        return await asyncio.shield(task)

    async def _call(self, principal, name, arguments):
        try:
            if self.closed:
                fail("execution/closed", tr('execution.runtime.1698e935a29a'))
            if not isinstance(principal, str) or not principal:
                fail("execution/identity-required", tr('execution.runtime.b2da6bf1c2c6'))
            self.config = sync_projects(self.config, self._configured_projects)
            definition = self.registry.get(name)
            args = validate_or_raise(arguments, definition.schema.parameters)
            sid = args.get("session_id", "")
            root = None
            dependency_project_id = None
            if name == "load_workspace_dependencies":
                dependency_project_id, project = self._dependency_project(args, principal)
                root = project.root
            elif sid:
                session, project = self._session(sid, principal)
                root = project.root
            elif name == "harness_session_open":
                root = self._project(args["project_id"], principal).root
            lock_key = 'registered-projects' if self.config.workspace_registry else str(root) if root else "principal:" + principal
            # 仅信任初始化时核对过的内置定义；同名替换或扩展工具仍独占。
            shared = self._shared_reads.get(name) is definition and definition.effect == "read"
            gate = self._locks.setdefault(lock_key, ExecutionGate()) if name not in {"harness_job_poll", "harness_pipeline_control"} else None
            lock = gate.hold(shared=shared) if gate is not None else nullcontext()
            async with lock:
                request_id = args.get("request_id")
                fingerprint = hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                scope = sid or args.get("project_id", "context")
                mutation = definition.effect != "read"
                if mutation:
                    cached = self.store.begin(principal, scope, name, request_id, fingerprint)
                    if cached is not None:
                        # 已完成回执损坏不能退回新请求，也不能返回其他对话的结果。
                        if (set(cached) != {'session_id', 'call_id', 'request_id', 'is_error', 'status', 'data'}
                                or cached.get('request_id') != request_id
                                or not isinstance(cached.get('call_id'), str) or not cached['call_id']
                                or type(cached.get('is_error')) is not bool
                                or cached.get('status') != ('error' if cached['is_error'] else 'completed')
                                or cached.get('session_id') is not None and not isinstance(cached['session_id'], str)
                                or sid and cached.get('session_id') != sid):
                            fail("execution/store-invalid", tr('execution.store.invalid_record'))
                        return cached
                call_id = "call_" + uuid.uuid4().hex
                ctx = ToolRunContext(call_id=call_id, session_id=sid, cwd=str(root) if root else None, turn=0, step=0,
                                     services={"principal": principal, "jobs": self.jobs, "registry": self.registry,
                                               "dependency_project_id": dependency_project_id,
                                               "workspace_write_root": str(root) if root else None})
                if sid:
                    self.store.event(sid, "tool/call", {"name": name, "call_id": call_id, "request_id": request_id,
                                                         "description": definition.schema.description})
                result = await self.registry.execute_call(ToolUseBlock(id=call_id, name=name, input=args), ctx)
                data = to_jsonable(result.data)
                if result.is_error and not isinstance(data, dict):
                    data = {"error": {"code": "execution/tool-error", "message": "\n".join(block.text for block in result.content)}}
                payload = {"session_id": sid or (data.get("session_id") if isinstance(data, dict) else None),
                           "call_id": call_id, "request_id": request_id, "is_error": result.is_error,
                           "status": "error" if result.is_error else "completed", "data": data}
                if sid:
                    self.store.event(sid, "tool/result", {"name": name, **payload})
                if mutation:
                    self.store.finish(principal, scope, name, request_id, payload)
                return payload
        except HarnessError as exc:
            return {"is_error": True, "status": "error", "data": {"error": exc.to_dict()}}

    async def _context(self, args, ctx):
        principal = ctx.services["principal"]
        projects = []
        for key, value in self.config.projects.items():
            if principal not in value.principals:
                continue
            projects.append(self._project_capabilities(key, value))
        recent = []
        for row in self.store.db.execute('SELECT id,project FROM sessions WHERE principal=? ORDER BY rowid DESC LIMIT 20', (principal,)):
            project = self.config.projects.get(row['project'])
            if project is None or principal not in project.principals:
                continue
            session = self.store.session(row['id'], principal)
            if session['project_root'] == str(project.root):
                recent.append({key: session[key] for key in ('session_id', 'project_id', 'title', 'revision')})
        return {"mode": "executor", "inference": "external", "model_required": False,
                  "projects": projects,
                "recent_sessions": recent,
                "tools": self.registry.names(), "instructions": tr('execution.runtime.9a9c30bb2f0d'),
                  "execution_environment": self.execution_environment.capabilities().to_dict(),
                  "limitations": [tr('execution.runtime.5ff6418f2110'), tr('execution.runtime.02720b8fb338'), tr('execution.runtime.5310d15abd23'), tr('execution.runtime.bf2de15695f7')]}

    def _project_capabilities(self, project_id, project):
        """发现入口与上下文使用相同的技能启停、覆盖和权限事实。"""
        return {"project_id": project_id, "name": project.display_name or project.root.name, "root": str(project.root),
                "writable": project.writable, "commands": project.commands, "commits": project.commits,
                "verification": list(project.verification), "skills": self._skill_entries(self._skills(project))}

    async def _workspace_dependencies(self, args, ctx):
        """在所属线程探测真实环境，工作区能力仍按本次已授权项目装配。"""
        project_id = ctx.services['dependency_project_id']
        project = self._project(project_id, ctx.services['principal'])
        if str(project.root) != ctx.cwd:
            fail('execution/project-changed', tr('execution.runtime.73db6e7068e2'))
        workspace = self._project_capabilities(project_id, project)
        try:
            reading = asyncio.create_task(asyncio.to_thread(collect_workspace_dependencies,
                include_packages=args.get('include_packages', True), cwd=project.root))
            data = await join_cleanup(reading)
        except Exception as exc:
            fail('execution/dependency-probe-failed', tr('python.workspace_tools.484acd7994ec', p0=str(exc)))
        return {**data, "project": workspace, "tools": self.registry.names(),
                "execution_environment": self.execution_environment.capabilities().to_dict()}

    async def _session_open(self, args, ctx):
        project = self._project(args["project_id"], ctx.services["principal"])
        session = self.store.open_session(ctx.services["principal"], args["project_id"], args.get("title", tr('execution.runtime.18343e5cb63e')), str(project.root))
        try:
            status = await self._git(project.root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"], check=False)
        except HarnessError as exc:
            if exc.code != "execution/git-unavailable":
                raise
            status = {"exit_code": -1, "stdout": "", "stderr": exc.message, "truncated": False}
        self.store.event(session["session_id"], "session/open", {"baseline_git": status})
        return session

    async def _session_resume(self, args, ctx):
        session, project, _ = self._policy(args, ctx)
        return {**session, **self.store.events(session["session_id"], args.get("cursor", 0)),
                "jobs": (await self.jobs.history_page(session["session_id"]))["jobs"], "project_root": str(project.root),
                "verification": self._verification(session["session_id"], project),
                "image_requests": self.store.images(session["session_id"]),
                "pipelines": [self._pipeline_view(value) for value in self._pipeline_records(ctx.session_id)],
                "recovery_instruction": tr('execution.pipeline.guidance')}

    async def _image_request(self, args, ctx):
        _, project, policy = self._policy(args, ctx)
        if not project.writable:
            fail("execution/write-denied", tr('execution.runtime.75b4f546cd1c'))
        target = policy.resolve(args["path"])
        if file_version(target) != "absent":
            fail("execution/file-conflict", tr('execution.runtime.9f1f2c5f65fb'))
        if target.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            fail("execution/image-format", tr('execution.runtime.5b8dc521f44a'))
        return self.store.image_request(ctx.session_id, {"prompt": args["prompt"], "path": str(target.relative_to(policy.root))})

    async def _image_get(self, args, ctx):
        self._policy(args, ctx)
        return {"image_requests": self.store.images(ctx.session_id, args.get("image_id"))}

    async def _image_deliver(self, args, ctx):
        _, project, policy = self._policy(args, ctx)
        if not project.writable:
            fail("execution/write-denied", tr('execution.runtime.75b4f546cd1c'))
        self._assert_idle(project, image_id=args['image_id'], image_session=ctx.session_id)
        image = self.store.images(ctx.session_id, args["image_id"])[0]
        if image["status"] == "delivery_unconfirmed":
            fail("execution/image-delivery-unconfirmed", tr('execution.runtime.image_delivery_unconfirmed'))
        if image["status"] != "pending":
            fail("execution/image-already-delivered", tr('execution.runtime.2c41cfa9f7c1'))
        target = policy.resolve(image["path"])
        if file_version(target) != "absent":
            fail("execution/file-conflict", tr('execution.runtime.d987976aeb07'))
        content = await asyncio.to_thread(download_image, args["file"]["download_url"], self.config.max_file_bytes)
        metadata = await asyncio.to_thread(inspect_image, content, target.suffix, args["file"].get("mime_type"))
        delivery = {**metadata, "file_id": args["file"]["file_id"], "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest()}
        self.store.image_delivery_begin(ctx.session_id, args["image_id"], delivery)
        changed = apply_changes(policy, [{"path": image["path"], "expected_version": "absent", "content": content}],
                                self.config.home, self.config.max_file_bytes)
        delivery.update(changed)
        value = self.store.image_delivered(ctx.session_id, args["image_id"], delivery)
        self._pipeline_wake_images(ctx.services['principal'], ctx.session_id)
        return value

    async def _read(self, args, ctx):
        _, _, policy = self._policy(args, ctx)
        target = policy.resolve(args["path"])
        before = file_version(target) if not target.is_dir() else None
        native_args = {key: args[key] for key in ("path", "offset", "limit") if key in args}
        native_args["path"] = str(target)
        result = await self.native.execute_call(ToolUseBlock(id=ctx.call_id, name="read", input=native_args), ctx)
        if result.is_error:
            return result
        value = dict(result.data)
        if before is not None and file_version(target) != before:
            fail("execution/file-conflict", tr('execution.runtime.eb0c1fb39952'))
        value.pop("_fs_observation", None)
        value["version"] = before
        return value

    async def _search(self, args, ctx):
        _, _, policy = self._policy(args, ctx)
        return search(policy, args["query"], args.get("path", "."), args.get("limit", 100), self.config.max_file_bytes)

    def _assert_idle(self, project, *, image_id=None, image_session=None):
        pipeline = self._pipeline_reserved(project.root)
        matching_image = (pipeline is not None and pipeline['status'] == 'waiting_image'
                          and pipeline['session_id'] == image_session and pipeline['index'] < len(pipeline['steps'])
                          and pipeline['steps'][pipeline['index']]['input'] == {'kind': 'image', 'image_id': image_id})
        if pipeline and not matching_image:
            fail('execution/pipeline-busy', tr('execution.pipeline.busy'))
        for owner in self.jobs.active_owners():
            row = self.store.db.execute("SELECT principal FROM sessions WHERE id=?", (owner,)).fetchone()
            # 项目撤销不改变既有作业目录，也不能让旧标识索引当前配置。
            if row and self.store.session(owner, row["principal"])["project_root"] == str(project.root):
                fail("execution/project-busy", tr('execution.runtime.8f929f44f128'))

    async def _apply_patch(self, args, ctx):
        _, project, policy = self._policy(args, ctx)
        if not project.writable:
            fail("execution/write-denied", tr('execution.runtime.75b4f546cd1c'))
        self._assert_idle(project)
        return apply_changes(policy, args["changes"], self.config.home, self.config.max_file_bytes)

    def _environment(self):
        env = execution_env()
        env["GIT_TERMINAL_PROMPT"] = "0"
        return env

    def _snapshot(self, policy):
        """验证快照使用独立大小预算，不扩大普通文件操作上限。"""
        return snapshot(
            policy,
            self.config.max_file_bytes,
            snapshot_max_bytes=MAX_SNAPSHOT_FILE_BYTES,
            snapshot_max_total_bytes=MAX_SNAPSHOT_TOTAL_BYTES,
        )

    async def _start_job(self, ctx, project, argv, timeout_ms, yield_ms, verification=None, output_encoding="utf-8"):
        if len([job for job in self.jobs.list_all_jobs() if job["status"] == "running"]) >= 8:
            fail("execution/job-limit", tr('execution.runtime.cc1d9c06c910'))
        timeout_ms = min(timeout_ms, self.config.max_command_ms)
        before = self._snapshot(PathPolicy(project.root, self.config.home)) if verification else None

        async def launch():
            ctx.check_execution()
            return await self.execution_environment.spawn(argv, cwd=str(project.root), env=self._environment())

        job = await self.jobs.spawn(kind="executor", label=verification or tr('execution.runtime.4f72dd8db025'), owner=ctx.session_id, spawn=launch, output_encoding=output_encoding)

        async def monitor():
            await self.jobs.wait(job.id, ctx.session_id, timeout_ms)
            if not job.is_settled:
                await self.jobs.kill(job.id, ctx.session_id, tr('execution.runtime.8df6eb745aef'))
            evidence = {"job_id": job.id, "verification_id": verification, "exit_code": job.exit_code,
                        "status": job.status, "before": before}
            if verification:
                evidence["command_hash"] = hashlib.sha256(json.dumps(argv).encode()).hexdigest()
                try:
                    evidence["after"] = self._snapshot(PathPolicy(project.root, self.config.home))
                    evidence["passed"] = job.status == "completed" and job.exit_code == 0 and not job.public().get("encoding_errors") and before == evidence["after"]
                except (HarnessError, OSError):
                    evidence["passed"] = False
                    evidence["error"] = tr('execution.runtime.4b25aef19e83')
                self._job_evidence[job.id] = self.store.evidence(ctx.session_id, "verification", evidence)
            self.store.event(ctx.session_id, "job/finished", evidence)

        task = asyncio.create_task(monitor())
        self._job_monitors[job.id] = task
        self._monitors.add(task)
        task.add_done_callback(self._monitors.discard)
        task.add_done_callback(lambda done: self._job_monitors.pop(job.id, None))
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        self.store.event(ctx.session_id, "job/started", {"job_id": job.id, "timeout_ms": timeout_ms, "verification_id": verification})
        await self.jobs.wait(job.id, ctx.session_id, min(yield_ms, 5000))
        if job.is_settled:
            await task
        return await self._job_poll({"session_id": ctx.session_id, "job_id": job.id}, ctx)

    async def _exec(self, args, ctx):
        _, project, _ = self._policy(args, ctx)
        if not project.commands:
            fail("execution/command-denied", tr('execution.runtime.c05a2a5a06b3'))
        self._assert_idle(project)
        if os.name == "nt":
            powershell = _resolve_powershell()
            if powershell is None:
                fail("execution/shell-unavailable", tr('execution.runtime.d7825bb121e8'))
            argv = _build_argv(powershell[0], _build_script(args["command"]))
        else:
            argv = ["/bin/sh", "-c", args["command"]]
        return await self._start_job(ctx, project, argv, args.get("timeout_ms", self.config.max_command_ms), args.get("yield_ms", 1000), output_encoding=output_encoding(args.get("output_encoding", "utf-8")))

    async def _job_poll(self, args, ctx):
        self._policy(args, ctx)
        job_id = args["job_id"]
        if job_id in self.jobs._jobs:
            try:
                await self.jobs.wait(job_id, ctx.session_id, args.get("wait_ms", 0))
            except LookupError:
                fail("execution/job-not-found", tr('execution.runtime.6974695774e4'))
        try:
            output = await self.jobs.output_snapshot(job_id, ctx.session_id, limit=self.config.max_output_chars)
        except LookupError:
            fail("execution/job-not-found", tr('execution.runtime.6974695774e4'))
        for channel in ("stdout", "stderr"):
            cursor = args.get(channel + "_cursor", 0)
            offset = output.get(channel + "_offset", 0)
            if cursor > output.get(channel + "_end", 0):
                fail("execution/cursor-invalid", tr('execution.runtime.8e6ff33eb8d7'))
            output[channel] = output[channel][max(0, cursor - offset):]
            output["truncated"] = output.get("truncated", False) or cursor < offset
        output["evidence_id"] = self._job_evidence.get(job_id)
        if output["evidence_id"] is None:
            rows = self.store.db.execute("SELECT id,data FROM evidence WHERE session_id=? AND kind='verification'", (ctx.session_id,)).fetchall()
            output["evidence_id"] = next((row["id"] for row in rows if json.loads(row["data"]).get("job_id") == job_id), None)
        if output["job"].get("encoding_errors") or output["job"]["status"] in {"failed", "killed", "cleanup-failed"} or output["job"].get("exitCode") not in (None, 0):
            output["error"] = {"code": "execution/command-failed", "message": tr('execution.runtime.256ea0ec11ff')}
        return output

    async def _job_cancel(self, args, ctx):
        self._policy(args, ctx)
        try:
            outcome, job = await self.jobs.kill(args["job_id"], ctx.session_id, tr('execution.runtime.510d61ee5a9b'))
        except LookupError:
            fail("execution/job-not-found", tr('execution.runtime.6974695774e4'))
        return {"outcome": outcome, "job": job}

    async def _git(self, root, args, *, check=True):
        executable = shutil.which("git")
        if not executable:
            fail("execution/git-unavailable", tr('execution.runtime.46cdf3612ebf'))
        proc = await self.execution_environment.spawn([executable, "--no-pager", "--no-optional-locks", "--literal-pathspecs", "-C", str(root), *args], cwd=str(root), env=self._environment())
        bounded = BoundedProcess(proc, self.config.max_output_chars * 4)
        out, err, code, timed_out = await collect_process(bounded, 30)
        if timed_out:
            await cleanup_process(proc)
            fail("execution/git-timeout", tr('execution.runtime.f5a33aaca222'))
        result = {"stdout": utf8_prefix(out, truncated=bounded.stdout.total > len(out))[:self.config.max_output_chars],
                  "stderr": diagnostic_text(err, truncated=bounded.stderr.total > len(err))[:self.config.max_output_chars], "exit_code": code,
                  "truncated": bounded.stdout.total > self.config.max_output_chars or bounded.stderr.total > self.config.max_output_chars}
        if code and check:
            fail("execution/git-failed", result["stderr"])
        return result

    async def _git_status(self, args, ctx):
        _, project, _ = self._policy(args, ctx)
        return await self._git(project.root, ["status", "--short"])

    async def _git_diff(self, args, ctx):
        _, project, policy = self._policy(args, ctx)
        listing = await self._git(project.root, ["ls-files", "-z"])
        if listing["truncated"]:
            fail("execution/git-output-limit", tr('execution.runtime.4f86a1305914'))
        paths = []
        for path in listing["stdout"].split("\x00"):
            if not path:
                continue
            try:
                policy.resolve(path)
            except HarnessError:
                continue
            paths.append(path)
        if not paths:
            return {"unstaged": {"stdout": ""}, "staged": {"stdout": ""}}
        unstaged = await self._git(project.root, ["diff", "--no-ext-diff", "--no-textconv", "--", *paths])
        staged = await self._git(project.root, ["diff", "--cached", "--no-ext-diff", "--no-textconv", "--", *paths])
        return {"unstaged": unstaged, "staged": staged}

    async def _skill_list(self, args, ctx):
        """技能目录：模型据此判断是否需要加载某个技能的正文。"""
        _, project, _ = self._policy(args, ctx)
        registry = self._skills(project)
        return {"skills": self._skill_entries(registry), "catalog": render_catalog(registry)}

    async def _skill_load(self, args, ctx):
        """按需加载正文：目录在前、正文在后，与本地 Agent 的两段式一致。"""
        _, project, _ = self._policy(args, ctx)
        registry = self._skills(project)
        try:
            skill = registry.get(args["name"])
        except SkillNotFoundError as exc:
            fail("execution/skill-not-found", str(exc))
        from ..skills.rendering import render_loaded_skill
        return {"skill": {"name": skill.name, "description": skill.description, "when_to_use": skill.when_to_use,
                          "source": skill.source, "chars": len(skill.content),
                          "version": hashlib.sha256(skill.content.encode('utf-8')).hexdigest()},
                "content": render_loaded_skill(skill),
                "instruction": tr('execution.runtime.026989cbc015')}

    async def _checkpoint(self, args, ctx):
        self._policy(args, ctx)
        return self.store.checkpoint(ctx.session_id, ctx.services["principal"], args["expected_revision"], args["summary"], args["next_steps"])

    def _verification(self, sid, project):
        commands = self.store.verification(sid) if project.commands else {}
        return {**commands, **project.verification}

    async def _verification_register(self, args, ctx):
        _, project, _ = self._policy(args, ctx)
        if not project.commands:
            fail("execution/command-denied", tr('execution.runtime.ade8e2549f9e'))
        self._assert_idle(project)
        name = args["verification_id"]
        if name in project.verification:
            fail("execution/verification-configured", tr('execution.runtime.726efdcaf3a8'))
        self.store.register_verification(ctx.session_id, name, args["command"])
        return {"verification_id": name, "command": args["command"], "scope": "session"}

    async def _verify(self, args, ctx):
        _, project, _ = self._policy(args, ctx)
        commands = self._verification(ctx.session_id, project)
        command = commands.get(args["verification_id"])
        if command is None:
            fail("execution/verification-denied", tr('execution.runtime.verification_register' if project.commands else 'execution.runtime.verification_local', available=tr('ui.common.list_separator').join(commands)))
        self._assert_idle(project)
        return await self._start_job(ctx, project, list(command), self.config.max_command_ms, args.get("yield_ms", 1000), args["verification_id"])

    async def _finalize(self, args, ctx):
        _, project, policy = self._policy(args, ctx)
        self._assert_idle(project)
        current = self._snapshot(policy)
        evidence = [self.store.get_evidence(ctx.session_id, eid) for eid in args["evidence_ids"]]
        commands = self._verification(ctx.session_id, project)
        if {row.get("verification_id") for row in evidence} != set(commands):
            fail("execution/verification-incomplete", tr('execution.runtime.65e78a3bf231'))
        for row in evidence:
            command = commands.get(row.get("verification_id"))
            command_hash = hashlib.sha256(json.dumps(list(command)).encode()).hexdigest() if command else None
            if row["kind"] != "verification" or not row.get("passed") or row.get("after") != current or row.get("command_hash") != command_hash:
                fail("execution/verification-stale", tr('execution.runtime.ecd0610d81ec'))
        result = {"verified": True, "evidence_ids": args["evidence_ids"], "tree_version": current}
        if "commit_message" in args:
            if not project.commits:
                fail("execution/commit-denied", tr('execution.runtime.c201fadc9bd0'))
            events = self.store.db.execute("SELECT kind,data FROM events WHERE session_id=? AND (kind='session/open' OR "
                "(kind='tool/result' AND json_extract(data,'$.name') IN ('harness_apply_patch','harness_image_deliver') "
                "AND NOT COALESCE(json_extract(data,'$.is_error'),0))) ORDER BY seq", (ctx.session_id,)).fetchall()
            owned = set()
            baseline = None
            for event in events:
                value = json.loads(event["data"])
                if event["kind"] == "session/open":
                    baseline = value["baseline_git"]
                if event["kind"] == "tool/result" and value.get("name") in {"harness_apply_patch", "harness_image_deliver"} and not value.get("is_error"):
                    owned.update(value["data"].get("delivery", value["data"])["changed_files"])
            if not owned:
                fail("execution/no-owned-changes", tr('execution.runtime.d90bc1feeef6'))
            if baseline is None or baseline["exit_code"] or baseline["truncated"]:
                fail("execution/git-baseline-missing", tr('execution.runtime.943d9ff2c9a3'))
            for row in baseline["stdout"].split("\x00"):
                if row and ((len(row) > 3 and row[3:] in owned) or row in owned):
                    fail("execution/user-changes-present", tr('execution.runtime.4498c1729fba'))
            staged = await self._git(project.root, ["diff", "--cached", "--name-only", "-z"])
            if staged["stdout"] or staged["truncated"]:
                fail("execution/staged-changes-present", tr('execution.runtime.a6744ef40bd1'))
            for path in owned:
                policy.resolve(path)
            paths = sorted(owned)
            await self._git(project.root, ["add", "--", *paths])
            commit = await self._git(project.root, ["commit", "--only", "-m", args["commit_message"], "--", *paths], check=False)
            if commit["exit_code"]:
                await self._git(project.root, ["reset", "-q", "HEAD", "--", *paths], check=False)
                fail("execution/commit-failed", commit["stderr"] or commit["stdout"])
            head = await self._git(project.root, ["rev-parse", "HEAD"])
            result["commit"] = head["stdout"].strip()
            result["committed_files"] = paths
            if self._snapshot(policy) != current:
                return ToolResult(content=[TextBlock(text=tr('execution.runtime.7e70f680422f'))], is_error=True,
                    data={**result, "verified": False, "error": {"code": "execution/verification-changed-after-commit", "message": tr('execution.runtime.7e3b26e408be')}})
        return result

    async def close(self):
        """共用已拥有的清理，完成后再向等待者传递取消或失败。"""
        if self._close_task is None:
            self.closed = True
            self._close_task = asyncio.create_task(self._close(), name='executor-close')
        await join_cleanup(self._close_task)


    async def _close(self):
        """先收敛已接受工作和进程，再关闭持久存储。"""
        await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        pipelines = tuple(getattr(self, '_pipeline_tasks', {}).values())
        for task in pipelines:
            task.cancel()
        await asyncio.gather(*pipelines, return_exceptions=True)
        await self.jobs.cancel_all()
        for task in tuple(self._monitors):
            task.cancel()
        await asyncio.gather(*tuple(self._monitors), return_exceptions=True)
        self.store.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
