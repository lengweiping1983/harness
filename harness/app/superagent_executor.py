"""基于 Harness 原生 Session 与 Run 的 V2 单 Worker 执行适配器。"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from harness.i18n import tr
from ..core.language import current_language, resolve_language

from ..core.errors import HarnessError
from ..core.cleanup import join_cleanup
from ..core.text_encoding import diagnostic_text
from ..session.tool_activity import confirmed_tool_result, tool_invocations
from ..session.turn_scope import turn_events
from .superagent_background import background_invocation, background_status
from .superagent_run_evidence import run_progress
from .superagent_coordinator_store import _hash, _json
from .git_process import run_git


_LOG = logging.getLogger(__name__)


def _text(value: object, field: str, *, limit: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field} must be a non-empty string of at most {limit} characters")
    return value.strip()


@dataclass(frozen=True)
class WorkerProfile:
    """宿主持有的不可变 Worker 配置；每个 Trigger 来源只能映射到一个配置。"""

    profile_id: str
    repo: str
    description: str
    source_instance_ids: tuple[str, ...]
    actions: tuple[str, ...]
    tools: tuple[str, ...]
    model: str | None = None
    max_turns: int = 1
    max_seconds: int = 900
    max_work_turns: int | None = None

    def __post_init__(self) -> None:
        profile_id = _text(self.profile_id, "profile_id", limit=128)
        repo = str(Path(_text(self.repo, "repo", limit=4096)).expanduser().resolve(strict=True))
        if not Path(repo).is_dir():
            raise ValueError("WorkerProfile repo must be a directory")
        description = _text(self.description, "description", limit=1024)
        sources = tuple(_text(item, "source_instance_id", limit=128) for item in self.source_instance_ids)
        actions = tuple(_text(item, "action", limit=128) for item in self.actions)
        tools = tuple(_text(item, "tool", limit=128) for item in self.tools)
        if not sources or len(set(sources)) != len(sources):
            raise ValueError("WorkerProfile requires unique source_instance_ids")
        if not actions or len(set(actions)) != len(actions) or len(set(tools)) != len(tools):
            raise ValueError("WorkerProfile actions and tools must be unique; actions cannot be empty")
        if self.model is not None:
            _text(self.model, "model", limit=256)
        if type(self.max_turns) is not int or not 1 <= self.max_turns <= 100:
            raise ValueError("WorkerProfile max_turns must be between 1 and 100")
        if type(self.max_seconds) is not int or not 1 <= self.max_seconds <= 86400:
            raise ValueError("WorkerProfile max_seconds must be between 1 and 86400")
        if self.max_work_turns is not None and (
            type(self.max_work_turns) is not int or not 1 <= self.max_work_turns <= 32
        ):
            raise ValueError(tr("superagent.invalid", field="WorkerProfile.max_work_turns"))
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "repo", repo)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "source_instance_ids", sources)
        object.__setattr__(self, "actions", actions)
        object.__setattr__(self, "tools", tools)

    @property
    def fingerprint(self) -> str:
        return self.fingerprint_for_repo(self.repo)

    def fingerprint_for_repo(self, repo: str) -> str:
        """设备路径映射不改变其余 Profile 授权；逻辑仓库身份仍由原宿主固定。"""
        return _hash(_json({
            "profile_id": self.profile_id, "repo": repo, "description": self.description,
            "source_instance_ids": list(self.source_instance_ids), "actions": list(self.actions),
            "tools": list(self.tools), "model": self.model,
            "max_turns": self.max_turns, "max_seconds": self.max_seconds,
            "max_work_turns": self.max_work_turns,
        }))


class ExecutionAdapter(Protocol):
    instance_id: str
    profiles: dict[str, WorkerProfile]
    max_workers: int

    def set_worker_lookup(self, lookup: Callable[[str], dict | None]) -> None: ...
    def profile_for_source(self, source_instance_id: str) -> WorkerProfile | None: ...
    def tool_effects(self, profile: WorkerProfile) -> Mapping[str, str]: ...
    def base_commit(self, profile: WorkerProfile) -> str: ...
    def find_worker(self, worker: dict, profile: WorkerProfile) -> dict | None: ...
    async def create_workspace(self, worker: dict, profile: WorkerProfile) -> dict: ...
    async def create_session(self, worker: dict, profile: WorkerProfile) -> str: ...
    async def submit(self, worker: dict, assignment: dict, prompt: str, input_ids: list[str]) -> dict: ...
    def reconcile(self, worker: dict, assignment: dict) -> dict: ...
    def inspect(self, worker: dict, assignment: dict) -> dict: ...
    async def deliver_input(self, worker: dict, assignment: dict, text: str, operation_key: str,
                            before_accept: Callable[[], None]) -> dict: ...
    def reconcile_input(self, worker: dict, assignment: dict, operation_key: str) -> dict | None: ...
    async def stop(self, worker: dict, assignment: dict) -> dict: ...
    def close(self) -> None: ...

class HarnessEvidenceProvider:
    """从 Harness 原始事件日志只读提取 Run、Tool 和后台任务证据。"""

    def __init__(self, harness):
        self.harness = harness

    def inspect(self, session_id: str, run_id: str) -> dict:
        record = self.harness.runtime.get(run_id)
        if record.session_id != session_id:
            raise PermissionError("Harness Run belongs to a different Worker session")
        session = self.harness.get_session(session_id)
        progress = run_progress(record, session.events)
        scoped = turn_events(session.events, run_id=record.id)
        calls = tool_invocations(scoped)
        result = record.result if isinstance(record.result, dict) else {}
        output = result.get("text", "")
        if not isinstance(output, str):
            output = json.dumps(result, ensure_ascii=False, sort_keys=True)
        output_hash = hashlib.sha256(output.encode("utf-8")).hexdigest()
        prompt_input = record.prompt_input if isinstance(record.prompt_input, dict) else {}
        input_ids = prompt_input.get("superagent_input_ids", [])
        if not isinstance(input_ids, list) or any(not isinstance(item, str) for item in input_ids):
            raise HarnessError("Corrupt Superagent input references in the original Run", code="superagent/evidence-corrupt")

        run_reference = {
            "run_id": record.id, "session_id": session_id, "status": record.status,
            "settled": bool(record.settled), "turn": progress["turn"],
            "started_at": record.started_at, "finished_at": record.finished_at,
            "last_activity": progress["last_activity"], "output_hash": output_hash,
            "input_ids": input_ids,
        }
        tools, backgrounds = [], []
        unconfirmed_tools = 0
        unsettled_background = 0
        background_failures = 0
        for call in calls:
            if call.get("_start_event") is not None:
                if not confirmed_tool_result(call):
                    unconfirmed_tools += 1
                else:
                    value = dict(call["_result"])
                    reference = {
                        "call_id": call["call_id"], "name": call["name"], "turn": call["turn"],
                        "start_seq": call["_start_event"].seq, "result_seq": call["_result_event"].seq,
                        "is_error": value["is_error"],
                    }
                    tools.append({
                        "reference": reference, "reference_hash": _hash(_json(reference)),
                        "payload_hash": _hash(_json(value)), "result_seq": call["_result_event"].seq,
                    })
            if background_invocation(call):
                status = background_status(self.harness.jobs, session_id, call, archived=True)
                if not status.get("confirmed") or not status.get("settled"):
                    unsettled_background += 1
                else:
                    reference = {
                        "call_id": call.get("call_id"), "job_id": status.get("job_id"),
                        "status": status.get("status"), "settled": status.get("settled"),
                        "stdout_offset": status.get("stdout_offset", 0),
                        "stderr_offset": status.get("stderr_offset", 0),
                    }
                    backgrounds.append({
                        "reference": reference, "reference_hash": _hash(_json(reference)),
                        "payload_hash": _hash(_json(status)),
                    })
                    if status.get("status") in {"failed", "killed"}:
                        background_failures += 1
        return {
            "found": True, "run_id": record.id, "session_id": session_id,
            "status": record.status, "settled": bool(record.settled),
            "started": record.started_at is not None, "turn": progress["turn"],
            "output_text": output, "output_hash": output_hash, "input_ids": list(input_ids),
            "run_reference": run_reference, "run_reference_hash": _hash(_json(run_reference)),
            "run_payload_hash": output_hash, "tools": tools, "backgrounds": backgrounds,
            "unconfirmed_tools": unconfirmed_tools, "unsettled_background": unsettled_background,
            "background_failures": background_failures,
        }


class HarnessExecutorAdapter:
    """将 Worker 绑定到专属 Worktree/Session，并且只提交具备幂等键的原生 Harness Run。"""

    def __init__(self, harness, *, instance_id: str, owner_session_id: str,
                 profiles: tuple[WorkerProfile, ...] | list[WorkerProfile], max_workers: int = 32,
                 default_profiles: Mapping[str, str] | None = None):
        self.harness = harness
        self.instance_id = _text(instance_id, "instance_id", limit=128)
        self.owner_session_id = _text(owner_session_id, "owner_session_id", limit=128)
        self.max_workers = max_workers
        if type(max_workers) is not int or not 1 <= max_workers <= 256:
            raise ValueError("max_workers must be between 1 and 256")
        if harness.worktrees is None or harness.sessions.root is None:
            raise ValueError("HarnessExecutorAdapter requires persistent sessions and WorktreeService")
        harness.get_session(self.owner_session_id)
        if any(getattr(harness.config, name) for name in
               ("enable_agents", "enable_skills", "enable_goal", "enable_plan_mode", "enable_subagent", "enable_ralph")):
            raise ValueError("V2 Worker Harness must use the native single-agent execution mode")
        if getattr(harness, "agent_mount", None) is not None:
            raise ValueError("V2 Worker Harness cannot use agent_mount")
        self.profiles = {profile.profile_id: profile for profile in profiles}
        if not self.profiles or len(self.profiles) != len(profiles):
            raise ValueError("HarnessExecutorAdapter requires unique WorkerProfiles")
        source_owners: dict[str, list[str]] = {}
        available_tools = set(harness.tools.names())
        for profile in self.profiles.values():
            if not set(profile.tools) <= available_tools:
                raise ValueError(f"WorkerProfile {profile.profile_id} contains unavailable tools")
            root = Path(harness.worktrees.git(Path(profile.repo), "rev-parse", "--show-toplevel").strip()).resolve()
            if root != Path(profile.repo).resolve():
                raise ValueError(f"WorkerProfile {profile.profile_id} repo must be a Git root")
            for source_id in profile.source_instance_ids:
                source_owners.setdefault(source_id, []).append(profile.profile_id)
        if default_profiles is not None and not isinstance(default_profiles, Mapping):
            raise ValueError(tr("superagent.invalid", field="default_profiles"))
        selected = dict(default_profiles or {})
        if any(source not in source_owners or profile not in source_owners[source]
               for source, profile in selected.items()):
            raise ValueError(tr("superagent.invalid", field="default_profiles"))
        for source, profiles_for_source in source_owners.items():
            if source not in selected:
                if len(profiles_for_source) != 1:
                    raise ValueError(tr("superagent.invalid", field="default_profiles"))
                selected[source] = profiles_for_source[0]
        self._source_profiles = {source: tuple(identities) for source, identities in source_owners.items()}
        self._default_profiles = selected
        self._worker_lookup: Callable[[str], dict | None] | None = None
        self._evidence = HarnessEvidenceProvider(harness)
        self.team = "superagent-v2-" + hashlib.sha256(self.instance_id.encode()).hexdigest()[:20]
        self.closed = False
        self._budget_watchers: dict[str, asyncio.Task] = {}
        self._budget_failures: dict[str, str] = {}
        for service in harness._superagent_workers:
            service_instance = service.instance_id
            if service_instance == self.instance_id and not service.closed:
                raise ValueError("another Superagent Worker service already owns this instance")
        harness._superagent_workers.append(self)

    def _maybe_unregister(self) -> None:
        if self.closed and not self._budget_watchers:
            with contextlib.suppress(ValueError):
                self.harness._superagent_workers.remove(self)

    def set_worker_lookup(self, lookup: Callable[[str], dict | None]) -> None:
        self._worker_lookup = lookup

    def profile_for_source(self, source_instance_id: str) -> WorkerProfile | None:
        profile_id = self._default_profiles.get(source_instance_id)
        return self.profiles.get(profile_id) if profile_id else None

    def tool_effects(self, profile: WorkerProfile) -> dict[str, str]:
        if not isinstance(profile, WorkerProfile) or self.profiles.get(profile.profile_id) != profile:
            raise ValueError("tool effect lookup requires an exact host-owned WorkerProfile")
        return {name: self.harness.tools.get(name).effect for name in profile.tools}

    def profiles_for_source(self, source_instance_id: str) -> tuple[WorkerProfile, ...]:
        """只提供宿主配置允许的模板；来源默认模板不限制多角色选择。"""
        return tuple(self.profiles[identity] for identity in self._source_profiles.get(source_instance_id, ()))

    def runtime_manifest(self):
        """只暴露执行约束，不包含模型凭据、会话内容或运行中的资源。"""
        return {'model':self.harness.config.model,'language':self.harness.config.language,'provider_root':self.harness.config.base_url,
            'permission_policy':self.harness.config.permission_policy,'max_workers':self.max_workers,
            'default_profiles':dict(self._default_profiles),
            'tool_effects':{key:self.tool_effects(profile) for key,profile in self.profiles.items()}}

    def snapshot_workspace(self, worker):
        """核对所属工作树并读取含未提交文件的真实树身份。"""
        from .worktree_delivery import snapshot_tree
        self._open()
        profile = self.profiles[worker["profile_id"]]
        workspace = self._workspace(worker, profile)
        if workspace is None:
            raise HarnessError(tr("superagent.worker-unavailable"), code="superagent/worker-unavailable")
        _, tree = snapshot_tree(self.harness.worktrees, workspace)
        return {"workspace": workspace, "tree": tree}

    async def capture_workspace(self, worker):
        """Git 写入归单次任务所有，取消等待不能遗落已接受的捕获。"""
        return await join_cleanup(asyncio.create_task(asyncio.to_thread(
            self.harness.worktrees.capture_delivery, worker["workspace_id"])))

    def artifact_manifest(self, worker, receipt):
        """从冻结 Git 对象计算文件内容哈希；不读取仍可变化的工作文件。"""
        manager = self.harness.worktrees
        view = self.snapshot_workspace(worker)
        workspace = view["workspace"]
        target = manager._target(workspace)
        if (receipt.get("base_commit") != worker["base_commit"]
                or manager.git(target, "rev-parse", "--verify", receipt["ref"] + "^{tree}").strip() != receipt["tree"]
                or manager.git(target, "rev-parse", "--verify", receipt["ref"] + "^{commit}").strip() != receipt["commit"]):
            raise HarnessError(tr("superagent.corrupt"), code="superagent/artifact-corrupt")
        def bounded(*args, limit):
            result = manager.run_git(target, *args, output_limit=limit)
            if result.returncode != 0:
                raise HarnessError(diagnostic_text(result.stderr).strip(), code="git/failed")
            if len(result.stdout) > limit:
                raise HarnessError(tr("superagent.artifact.capacity"), code="superagent/artifact-capacity")
            return result.stdout

        entries = bounded("ls-tree", "-r", "-l", "-z", receipt["tree"], limit=2 * 1024 * 1024).split(b"\0")
        if len(entries) > 4097:
            raise HarnessError(tr("superagent.artifact.capacity"), code="superagent/artifact-capacity")
        files, total = [], 0
        for entry in entries:
            if not entry:
                continue
            metadata, raw_path = entry.split(b"\t", 1)
            mode, kind, object_id, raw_size = metadata.split()
            path = raw_path.decode("utf-8")
            if kind == b"commit":
                files.append({"path": path, "mode": mode.decode("ascii"), "kind": "gitlink",
                              "object_id": object_id.decode("ascii")})
                continue
            size = int(raw_size)
            total += size
            if size > 16 * 1024 * 1024 or total > 64 * 1024 * 1024:
                raise HarnessError(tr("superagent.artifact.capacity"), code="superagent/artifact-capacity")
            content = bounded("cat-file", "blob", object_id.decode("ascii"), limit=16 * 1024 * 1024)
            if len(content) != size:
                raise HarnessError(tr("superagent.corrupt"), code="superagent/artifact-corrupt")
            files.append({"path": path, "mode": mode.decode("ascii"), "kind": "blob", "size": size,
                          "sha256": hashlib.sha256(content).hexdigest()})
        deleted = bounded("diff", "--name-only", "--diff-filter=D", "-z",
                          worker["base_commit"], receipt["tree"], "--", limit=2 * 1024 * 1024)
        return {"files": files, "deleted": [p.decode("utf-8") for p in deleted.split(b"\0") if p]}

    async def plan_artifact_delivery(self, worker, artifacts):
        """依赖预检只接收经协调账本核验的冻结来源。"""
        source_ids = [artifact["workspace_id"] for artifact in artifacts]
        manager = self.harness.worktrees
        for artifact in artifacts:
            source = manager.get(artifact["workspace_id"])
            receipt = json.loads(artifact["receipt_json"])
            if source.get("delivery") != receipt:
                raise HarnessError(tr("superagent.corrupt"), code="superagent/artifact-corrupt")
        return await join_cleanup(asyncio.create_task(asyncio.to_thread(
            manager.plan_delivery_merge, worker["workspace_id"], source_ids)))

    async def apply_artifact_delivery(self, worker, artifacts, plan):
        manager = self.harness.worktrees
        return await join_cleanup(asyncio.create_task(asyncio.to_thread(
            manager.apply_delivery_merge, worker["workspace_id"],
            [artifact["workspace_id"] for artifact in artifacts],
            expected_target_tree=plan["target_tree"], expected_tree=plan["tree"])))

    def _open(self) -> None:
        if self.closed or self.harness.runtime.closed or self.harness._close_task is not None:
            raise HarnessError("Harness Worker adapter is closed", code="superagent/executor-closed")
        if (self.harness.worktrees is None or self.harness.sessions.root is None
                or any(getattr(self.harness.config, name) for name in
                       ("enable_agents", "enable_skills", "enable_goal", "enable_plan_mode", "enable_subagent", "enable_ralph"))
                or getattr(self.harness, "agent_mount", None) is not None):
            raise HarnessError("Harness is not in the required native Worker mode", code="superagent/executor-invalid")

    def base_commit(self, profile: WorkerProfile) -> str:
        self._open()
        root = Path(self.harness.worktrees.git(Path(profile.repo), "rev-parse", "--show-toplevel").strip()).resolve()
        if root != Path(profile.repo).resolve():
            raise PermissionError("WorkerProfile repo root changed")
        commit = self.harness.worktrees.git(Path(profile.repo), "rev-parse", "--verify", "HEAD^{commit}").strip()
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit):
            raise HarnessError("Git returned an invalid Worker base commit", code="superagent/executor-corrupt")
        return commit

    def _workspace(self, worker: dict, profile: WorkerProfile) -> dict | None:
        matches = [item for item in self.harness.worktrees.list()
                   if item.get("team") == self.team
                   and item.get("team_run_id") == (worker.get("mission_id") or worker["worker_id"])
                   and item.get("team_member") == worker["worker_id"]
                   and item.get("owner_session_id") == self.owner_session_id]
        if len(matches) > 1:
            raise HarnessError("Worker owns multiple Worktrees", code="superagent/worker-conflict")
        if not matches:
            return None
        result = matches[0]
        root = Path(self.harness.worktrees.git(Path(profile.repo), "rev-parse", "--show-toplevel").strip()).resolve()
        if (Path(result["repo"]).resolve() != root or result["base_commit"] != worker["base_commit"]
                or result["status"] != "active"
                or worker.get("workspace_id") is not None and result["id"] != worker["workspace_id"]
                or not Path(result["path"]).is_dir()):
            raise HarnessError("Worker Worktree identity does not match the durable Worker row", code="superagent/worker-conflict")
        target = self.harness.worktrees._target(result)
        common = lambda path: Path(self.harness.worktrees.git(path, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()).resolve()
        actual_root = Path(self.harness.worktrees.git(target, "rev-parse", "--show-toplevel").strip()).resolve()
        if actual_root != target or common(target) != common(Path(root)):
            raise HarnessError("Worker Worktree Git identity is inconsistent", code="superagent/worker-conflict")
        return result

    def _metadata(self, worker: dict) -> dict:
        return {
            "instance_id": self.instance_id, "worker_id": worker["worker_id"],
            "ticket_id": worker["ticket_id"], "profile_id": worker["profile_id"],
            "identity_hash": worker["identity_hash"], "workspace_id": worker["workspace_id"],
        }

    def _session(self, worker: dict, profile: WorkerProfile):
        headers = [header for header in self.harness.sessions.list_sessions()
                   if isinstance(header.metadata.get("superagent_worker"), dict)
                   and header.metadata["superagent_worker"].get("worker_id") == worker["worker_id"]]
        if len(headers) > 1:
            raise HarnessError("Worker owns multiple Harness Sessions", code="superagent/worker-conflict")
        if not headers:
            return None
        header = headers[0]
        workspace = self._workspace(worker, profile)
        expected = self._metadata(worker)
        if (workspace is None or header.metadata.get("superagent_worker") != expected
                or header.cwd != workspace["path"]
                or worker.get("session_id") is not None and header.id != worker["session_id"]
                or profile.model is not None and header.metadata.get("model") != profile.model):
            raise HarnessError("Worker Session identity does not match its Worktree and Profile", code="superagent/worker-conflict")
        return header

    def find_worker(self, worker: dict, profile: WorkerProfile) -> dict | None:
        self._open()
        workspace = self._workspace(worker, profile)
        header = self._session(worker, profile)
        if workspace is None:
            if header is not None:
                raise HarnessError("Worker Session exists without its owned Worktree", code="superagent/worker-conflict")
            return None
        return {
            "workspace_id": workspace["id"], "session_id": header.id if header is not None else None,
            "base_commit": workspace["base_commit"], "workspace_path": workspace["path"],
        }

    async def create_workspace(self, worker: dict, profile: WorkerProfile) -> dict:
        self._open()
        existing = self.find_worker(worker, profile)
        if existing is not None:
            return existing
        created = self.harness.worktrees.create_team(
            profile.repo, [worker["worker_id"]], team=self.team,
            owner_session_id=self.owner_session_id, run_id=worker.get("mission_id") or worker["worker_id"],
            ref=worker["base_commit"],
        )
        if len(created) != 1:
            raise HarnessError("Harness did not create exactly one Worker Worktree", code="superagent/worker-conflict")
        result = self.find_worker({**worker, "workspace_id": created[0]["id"]}, profile)
        if result is None:
            raise HarnessError("Created Worker Worktree cannot be verified", code="superagent/worker-conflict")
        return result

    async def create_session(self, worker: dict, profile: WorkerProfile) -> str:
        self._open()
        found = self.find_worker(worker, profile)
        if found is None or not found.get("workspace_id"):
            raise HarnessError("Worker Session requires its verified Worktree", code="superagent/worker-unavailable")
        if found.get("session_id"):
            return found["session_id"]
        bound = {**worker, "workspace_id": found["workspace_id"]}
        metadata = {"superagent_worker": self._metadata(bound)}
        if profile.model is not None:
            metadata["model"] = profile.model
        language_token = current_language.set(resolve_language(self.harness.config.language).effective_language)
        try:
            title = tr("superagent.worker_title", task=worker["worker_id"])
        finally:
            current_language.reset(language_token)
        session = self.harness.create_session(cwd=found["workspace_path"], title=title, metadata=metadata)
        return session.id

    def bind_session(self, session, agent) -> bool:
        metadata = session.header.metadata.get("superagent_worker")
        if self.closed or not isinstance(metadata, dict) or metadata.get("instance_id") != self.instance_id:
            return False
        if self._worker_lookup is None:
            raise HarnessError("Worker ledger lookup is unavailable", code="superagent/worker-unavailable")
        worker = self._worker_lookup(metadata.get("worker_id"))
        if (worker is None or worker["state"] != "ready" or worker["session_id"] != session.id
                or metadata != self._metadata(worker)):
            raise HarnessError("Worker is not authorized by the durable Coordinator ledger", code="superagent/worker-unavailable")
        profile = self.profiles.get(worker["profile_id"])
        if profile is None or self._session(worker, profile) is None:
            raise HarnessError("Worker Profile or Session is unavailable", code="superagent/worker-unavailable")
        agent.tools = self.harness.tools.restricted(profile.tools)
        return True

    def _watch_run_budget(self, record, max_seconds: int) -> None:
        current = self._budget_watchers.get(record.id)
        if current is not None and not current.done():
            return
        watcher = asyncio.create_task(self._enforce_run_budget(record, max_seconds),
                                      name=f"superagent-budget:{record.id}")
        self._budget_watchers[record.id] = watcher

        def finished(task):
            if self._budget_watchers.get(record.id) is task:
                self._budget_watchers.pop(record.id, None)
            if not task.cancelled():
                error = task.exception()
                if error is not None:
                    _LOG.error("Worker Run budget watcher failed", exc_info=(type(error), error, error.__traceback__))
            self._maybe_unregister()

        watcher.add_done_callback(finished)

    async def _enforce_run_budget(self, record, max_seconds: int) -> None:
        task = record.task
        if task is None:
            return
        while not task.done() and record.started_at is None:
            await asyncio.sleep(0.05)
        if task.done() or record.started_at is None:
            return
        deadline = record.started_at + max_seconds
        while not task.done():
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(remaining, 0.25))
        if task.done():
            return
        session = self.harness.get_session(record.session_id)
        active = self.harness.runtime.active.get(record.session_id)
        current = getattr(self.harness.runtime, "_executions", {}).get(active)
        if current is not record or session.open_turn is None:
            return
        try:
            stopped = await self.harness.cancel(record.session_id, expected_turn=session.open_turn)
            if not stopped and not task.done():
                raise RuntimeError("Harness did not confirm cancellation after the Worker time budget expired")
            await asyncio.gather(task, return_exceptions=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._budget_failures[record.id] = type(exc).__name__
            _LOG.exception("Worker Run exceeded its time budget but cancellation was not confirmed")

    async def submit(self, worker: dict, assignment: dict, prompt: str, input_ids: list[str]) -> dict:
        self._open()
        profile = self.profiles.get(worker["profile_id"])
        if profile is None or self._session(worker, profile) is None:
            raise HarnessError("Worker Session is unavailable", code="superagent/worker-unavailable")
        max_seconds = assignment.get("max_seconds")
        if type(max_seconds) is not int or not 1 <= max_seconds <= profile.max_seconds:
            raise HarnessError("Assignment execution time budget is invalid", code="superagent/budget-invalid")
        session = self.harness.get_session(worker["session_id"])
        agent = self.harness._agent_for(session)
        agent.tools = self.harness.tools.restricted(profile.tools)
        prompt_input = {"text": prompt, "parts": [], "superagent_input_ids": list(input_ids), "superagent_max_seconds": max_seconds}
        record = self.harness.runtime.submit(
            worker["session_id"], prompt, key=assignment["submission_key"],
            request_id=assignment["submission_key"], prompt_input=prompt_input,
        )
        self._watch_run_budget(record, max_seconds)
        return {"run_id": record.id, "status": record.status}

    def _inspect_run(self, session_id: str, run_id: str) -> dict:
        snapshot = self._evidence.inspect(session_id, run_id)
        failure = self._budget_failures.get(run_id)
        if failure is not None:
            snapshot["budget_failure"] = failure
        return snapshot

    def reconcile(self, worker: dict, assignment: dict) -> dict:
        self._open()
        record = self.harness.runtime.find_submission(worker["session_id"], assignment["submission_key"])
        if record is None:
            return {"found": False, "authoritative": True}
        return self._inspect_run(worker["session_id"], record.id)

    def inspect(self, worker: dict, assignment: dict) -> dict:
        self._open()
        return self._inspect_run(worker["session_id"], assignment["run_id"])

    @staticmethod
    def _input_session_id(worker: dict, assignment: dict) -> str:
        session_id = worker.get("session_id")
        worker_id = worker.get("worker_id")
        workspace_id = worker.get("workspace_id")
        if (
            not isinstance(session_id, str) or not session_id
            or not isinstance(worker_id, str) or not worker_id
            or not isinstance(workspace_id, str) or not workspace_id
            or assignment.get("session_id") != session_id
            or assignment.get("worker_id") != worker_id
            or assignment.get("workspace_id") != workspace_id
            or not isinstance(assignment.get("run_id"), str) or not assignment["run_id"]
        ):
            raise HarnessError(tr("superagent.conflict"), code="superagent/run-conflict")
        return session_id

    @staticmethod
    def _validate_input_key(assignment: dict, operation_key: str) -> None:
        assignment_id = assignment.get("assignment_id")
        if (
            not isinstance(assignment_id, str) or not assignment_id
            or not isinstance(operation_key, str)
            or not operation_key.startswith("superagent-v2:input:")
            or not operation_key.endswith(f":{assignment_id}")
        ):
            raise HarnessError(tr("superagent.conflict"), code="superagent/run-conflict")

    async def deliver_input(self, worker: dict, assignment: dict, text: str, operation_key: str,
                            before_accept: Callable[[], None]) -> dict:
        self._open()
        self._validate_input_key(assignment, operation_key)
        session_id = self._input_session_id(worker, assignment)
        session = self.harness.get_session(session_id)
        active_task = self.harness.runtime.active.get(session_id)
        if active_task is None:
            return {"accepted": False, "deferred": True}
        record = getattr(self.harness.runtime, "_executions", {}).get(active_task)
        agent = self.harness._agents.get(session_id)
        original_run_id = assignment.get("run_id")
        if (
            not isinstance(original_run_id, str) or active_task is None
            or record is None or getattr(record, "id", None) != original_run_id
            or getattr(record, "session_id", None) != session_id
            or agent is None or getattr(agent, "run_id", None) != original_run_id
        ):
            raise HarnessError(tr("superagent.conflict"), code="superagent/run-conflict")
        if session.open_turn is None:
            return {"accepted": False, "deferred": True}
        turn_id = session.open_turn
        def guard():
            self._instruction_target(worker, assignment, turn_id)
            before_accept()
        return await self.harness.steer(
            session_id, text, expected_turn=turn_id,
            request_id=operation_key, original_text=text, before_accept=guard,
        )

    def reconcile_input(self, worker: dict, assignment: dict, operation_key: str) -> dict | None:
        self._open()
        self._validate_input_key(assignment, operation_key)
        session_id = self._input_session_id(worker, assignment)
        return self.harness.steering_receipt(session_id, operation_key, summary=False)

    @staticmethod
    def _validate_instruction_key(assignment: dict, operation_key: str) -> None:
        assignment_id = assignment.get("assignment_id")
        if (
            not isinstance(assignment_id, str) or not assignment_id
            or not isinstance(operation_key, str)
            or not operation_key.startswith("superagent-v2:instruction:")
            or not operation_key.endswith(f":{assignment_id}")
            or len(operation_key) > 200
        ):
            raise HarnessError(tr("superagent.conflict"), code="superagent/run-conflict")

    def _instruction_target(self, worker: dict, assignment: dict, turn_id: int | None = None):
        session_id = self._input_session_id(worker, assignment)
        session = self.harness.get_session(session_id)
        active_task = self.harness.runtime.active.get(session_id)
        record = getattr(self.harness.runtime, "_executions", {}).get(active_task)
        agent = self.harness._agents.get(session_id)
        original_run_id = assignment.get("run_id")
        if (
            not isinstance(original_run_id, str) or active_task is None
            or record is None or getattr(record, "id", None) != original_run_id
            or getattr(record, "session_id", None) != session_id
            or agent is None or getattr(agent, "run_id", None) != original_run_id
        ):
            raise HarnessError(tr("superagent.conflict"), code="superagent/run-conflict")
        current_turn = session.open_turn
        if type(current_turn) is not int or current_turn < 1:
            raise HarnessError(tr("superagent.conflict"), code="run/stale-turn")
        if turn_id is not None and current_turn != turn_id:
            raise HarnessError(tr("superagent.conflict"), code="run/stale-turn")
        return session_id, session

    def instruction_turn(self, worker: dict, assignment: dict, operation_key: str) -> int:
        self._open()
        self._validate_instruction_key(assignment, operation_key)
        _, session = self._instruction_target(worker, assignment)
        return session.open_turn

    async def deliver_instruction(self, worker: dict, assignment: dict, text: str,
                                  operation_key: str, turn_id: int,
                                  before_accept: Callable[[], None]) -> dict:
        self._open()
        self._validate_instruction_key(assignment, operation_key)
        session_id, _ = self._instruction_target(worker, assignment, turn_id)
        if not isinstance(text, str) or not text.strip() or len(text) > 65536:
            raise HarnessError(tr("superagent.conflict"), code="run/invalid")

        def guard():
            self._instruction_target(worker, assignment, turn_id)
            return before_accept()

        response = await self.harness.steer(
            session_id, text, expected_turn=turn_id,
            request_id=operation_key, before_accept=guard,
        )
        if isinstance(response, dict) and response.get("accepted") is True:
            receipt = self.reconcile_instruction(
                worker, assignment, operation_key, turn_id, text,
            )
            if receipt is None:
                raise HarnessError(tr("superagent.conflict"), code="run/submission-unconfirmed")
            response = {**response, "receipt": receipt}
        return response

    def reconcile_instruction(self, worker: dict, assignment: dict, operation_key: str,
                              turn_id: int, expected_text: str) -> dict | None:
        self._open()
        self._validate_instruction_key(assignment, operation_key)
        session_id = self._input_session_id(worker, assignment)
        session = self.harness.get_session(session_id)
        receipt = self.harness.steering_receipt(session_id, operation_key, summary=False)
        if receipt is None:
            return None
        if not isinstance(receipt, Mapping):
            raise HarnessError(tr("superagent.corrupt"), code="superagent/instruction-receipt-invalid")
        try:
            events = turn_events(session.events, run_id=assignment["run_id"])
        except (TypeError, ValueError) as exc:
            raise HarnessError(tr("superagent.corrupt"), code="superagent/instruction-receipt-invalid") from exc
        accepted = [
            event for event in events
            if event.seq == receipt.get("accepted_seq") and event.type == "note"
            and isinstance(event.data, Mapping)
            and event.data.get("kind") == "steering/accepted"
            and event.data.get("request_id") == operation_key
            and event.data.get("run_id") == assignment["run_id"]
            and type(event.data.get("turn")) is int and event.data["turn"] == turn_id
            and event.data.get("text") == expected_text
            and "original_text" not in event.data and "attachments" not in event.data
        ]
        status = receipt.get("status")
        applied_seq = receipt.get("applied_seq")
        if (
            len(accepted) != 1 or type(receipt.get("request_id")) is not str
            or receipt["request_id"] != operation_key
            or type(receipt.get("turn")) is not int or receipt.get("turn") != turn_id
            or type(receipt.get("accepted_seq")) is not int or receipt.get("accepted_seq") < 1
            or type(receipt.get("text")) is not str or receipt["text"] != expected_text
            or type(status) is not str or status not in {"pending", "applied", "unapplied"}
        ):
            raise HarnessError(tr("superagent.corrupt"), code="superagent/instruction-receipt-invalid")
        if status == "applied":
            applied = [
                event for event in events
                if event.seq == applied_seq and event.type == "user/message"
                and isinstance(event.data, Mapping)
                and event.seq > receipt["accepted_seq"]
                and event.data.get("text") == expected_text
                and event.data.get("source") == "steer"
                and event.data.get("request_id") == operation_key
                and type(event.data.get("turn")) is int and event.data["turn"] == turn_id
                and event.data.get("run_id") == assignment["run_id"]
                and "original_text" not in event.data and "attachments" not in event.data
            ]
            if type(applied_seq) is not int or applied_seq <= receipt["accepted_seq"] or len(applied) != 1:
                raise HarnessError(tr("superagent.corrupt"), code="superagent/instruction-receipt-invalid")
        elif applied_seq is not None:
            raise HarnessError(tr("superagent.corrupt"), code="superagent/instruction-receipt-invalid")
        return {
            "request_id": operation_key, "turn": turn_id, "text": expected_text,
            "status": status, "accepted_seq": receipt["accepted_seq"],
            "applied_seq": applied_seq,
        }

    async def stop(self, worker: dict, assignment: dict) -> dict:
        self._open()
        session_id = worker["session_id"]
        run_id = assignment["run_id"]
        if assignment.get("session_id") not in {None, session_id}:
            raise PermissionError("Assignment Session does not match its Worker")
        record = self.harness.runtime.get(run_id)
        if record.id != run_id or record.session_id != session_id:
            raise PermissionError("Assignment Run belongs to a different Worker Session")

        run_settled = record.finished_at is not None
        if record.status == "queued" and record.started_at is None:
            self.harness.runtime.remove(record.id)
            run_settled = record.finished_at is not None
        elif not run_settled:
            agent = self.harness._agents.get(session_id)
            active = self.harness.runtime.active.get(session_id)
            current = getattr(self.harness.runtime, "_executions", {}).get(active)
            if active is None or current is not record or agent is None or getattr(agent, "run_id", None) != record.id:
                return {"stopped": False, "status": record.status, "run_settled": False}
            turn = self.harness.get_session(session_id).open_turn
            if turn is None:
                return {"stopped": False, "status": "not-active", "run_settled": False}
            stopped = await self.harness.cancel(session_id, expected_turn=turn)
            run_settled = record.finished_at is not None
            if not run_settled:
                return {
                    "stopped": bool(stopped),
                    "status": "stopping" if stopped else "not-active",
                    "run_settled": False,
                }

        if not run_settled:
            return {"stopped": False, "status": record.status, "run_settled": False}

        # 只有原始 Run 已结束后，才检查并停止它自己创建的后台作业。
        # 不按 Worker Session 扫描，避免误杀同一 Session 的其他 Run。
        session = self.harness.get_session(session_id)
        try:
            events = turn_events(session.events, run_id=record.id)
            invocations = tool_invocations(events)
        except (KeyError, TypeError, ValueError) as exc:
            raise HarnessError(
                "Original Run background evidence cannot be parsed",
                code="superagent/background-evidence-unknown",
            ) from exc

        backgrounds_stopped = 0
        for invocation in invocations:
            if not background_invocation(invocation):
                continue
            status = background_status(self.harness.jobs, session_id, invocation, archived=True)
            if not status.get("confirmed"):
                raise HarnessError(
                    "Original Run background job ownership cannot be confirmed",
                    code="superagent/background-unknown",
                )
            if status.get("settled"):
                continue
            job_id = status.get("job_id")
            if not isinstance(job_id, str) or not job_id:
                raise HarnessError(
                    "Original Run background job identity is missing",
                    code="superagent/background-unknown",
                )
            outcome, _view = await self.harness.jobs.kill(
                job_id, session_id,
                reason=f"Superagent cancellation for assignment {assignment.get('assignment_id', '')}",
            )
            if outcome == "cleanup-failed":
                raise HarnessError(
                    "Original Run background job cleanup failed",
                    code="superagent/background-cleanup-failed",
                )
            settled = background_status(self.harness.jobs, session_id, invocation, archived=True)
            if not settled.get("confirmed") or not settled.get("settled"):
                raise HarnessError(
                    "Original Run background job stop outcome cannot be confirmed",
                    code="superagent/background-stop-unknown",
                )
            backgrounds_stopped += 1

        return {
            "stopped": True,
            "status": record.status,
            "run_settled": True,
            "backgrounds_stopped": backgrounds_stopped,
        }

    def close(self) -> None:
        self.closed = True
        self._maybe_unregister()

    async def aclose(self) -> None:
        self.close()
        watchers = tuple(self._budget_watchers.values())
        for watcher in watchers:
            watcher.cancel()
        if watchers:
            await asyncio.gather(*watchers, return_exceptions=True)
        self._maybe_unregister()
