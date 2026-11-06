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
from ..session.tool_activity import confirmed_tool_result, tool_invocations
from ..session.turn_scope import turn_events
from .superagent_background import background_invocation, background_status
from .superagent_mission_status import run_progress
from .superagent_coordinator_store import _hash, _json


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
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "repo", repo)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "source_instance_ids", sources)
        object.__setattr__(self, "actions", actions)
        object.__setattr__(self, "tools", tools)

    @property
    def fingerprint(self) -> str:
        return _hash(_json({
            "profile_id": self.profile_id, "repo": self.repo, "description": self.description,
            "source_instance_ids": list(self.source_instance_ids), "actions": list(self.actions),
            "tools": list(self.tools), "model": self.model,
            "max_turns": self.max_turns, "max_seconds": self.max_seconds,
        }))


class ExecutionAdapter(Protocol):
    instance_id: str
    profiles: dict[str, WorkerProfile]
    max_workers: int

    def set_worker_lookup(self, lookup: Callable[[str], dict | None]) -> None: ...
    def profile_for_source(self, source_instance_id: str) -> WorkerProfile | None: ...
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
                 profiles: tuple[WorkerProfile, ...] | list[WorkerProfile], max_workers: int = 32):
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
        source_owners: dict[str, str] = {}
        available_tools = set(harness.tools.names())
        for profile in self.profiles.values():
            if not set(profile.tools) <= available_tools:
                raise ValueError(f"WorkerProfile {profile.profile_id} contains unavailable tools")
            root = Path(harness.worktrees.git(Path(profile.repo), "rev-parse", "--show-toplevel").strip()).resolve()
            if root != Path(profile.repo).resolve():
                raise ValueError(f"WorkerProfile {profile.profile_id} repo must be a Git root")
            for source_id in profile.source_instance_ids:
                if source_id in source_owners:
                    raise ValueError(f"source {source_id} maps to more than one WorkerProfile")
                source_owners[source_id] = profile.profile_id
        self._source_profiles = source_owners
        self._worker_lookup: Callable[[str], dict | None] | None = None
        self._evidence = HarnessEvidenceProvider(harness)
        self.team = "superagent-v2-" + hashlib.sha256(self.instance_id.encode()).hexdigest()[:20]
        self.closed = False
        self._budget_watchers: dict[str, asyncio.Task] = {}
        self._budget_failures: dict[str, str] = {}
        for service in harness._superagent_workers:
            inbox = getattr(service, "inbox", None)
            service_instance = getattr(service, "instance_id", None) or getattr(inbox, "instance_id", None)
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
        profile_id = self._source_profiles.get(source_instance_id)
        return self.profiles.get(profile_id) if profile_id else None

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
                   and item.get("team_run_id") == worker["worker_id"]
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
            owner_session_id=self.owner_session_id, run_id=worker["worker_id"],
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

    async def deliver_input(self, worker: dict, assignment: dict, text: str, operation_key: str,
                            before_accept: Callable[[], None]) -> dict:
        self._open()
        session = self.harness.get_session(worker["session_id"])
        if worker["session_id"] not in self.harness.runtime.active:
            return {"accepted": False, "deferred": True}
        return await self.harness.steer(
            worker["session_id"], text, expected_turn=session.open_turn,
            request_id=operation_key, original_text=text, before_accept=before_accept,
        )

    def reconcile_input(self, worker: dict, assignment: dict, operation_key: str) -> dict | None:
        self._open()
        return self.harness.steering_receipt(worker["session_id"], operation_key, summary=False)

    async def stop(self, worker: dict, assignment: dict) -> dict:
        self._open()
        session_id = worker["session_id"]
        record = self.harness.runtime.get(assignment["run_id"])
        if record.session_id != session_id:
            raise PermissionError("Assignment Run belongs to a different Worker Session")
        if record.status == "queued" and record.started_at is None:
            self.harness.runtime.remove(record.id)
            return {"stopped": True, "status": "queued-removed"}
        if record.status in {"completed", "failed", "interrupted"} or record.finished_at is not None:
            return {"stopped": False, "status": record.status}
        agent = self.harness._agents.get(session_id)
        active = self.harness.runtime.active.get(session_id)
        current = getattr(self.harness.runtime, "_executions", {}).get(active)
        if active is None or current is not record or agent is None or getattr(agent, "run_id", None) != record.id:
            return {"stopped": False, "status": record.status}
        turn = self.harness.get_session(session_id).open_turn
        if turn is None:
            return {"stopped": False, "status": "not-active"}
        stopped = await self.harness.cancel(session_id, expected_turn=turn)
        return {"stopped": bool(stopped), "status": "stopping" if stopped else "not-active"}

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
