"""持久化本地调度器：独占租约、有限并发、合并错过的执行。"""
from __future__ import annotations

from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..core.sqlite import prepare_database

from harness.i18n import tr
import asyncio
import json
import logging
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from ..core.errors import HarnessError
from .schedules import invalid, next_due, validate_schedule

logger = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE automations (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, session_id TEXT NOT NULL,
    prompt TEXT NOT NULL, due REAL NOT NULL, interval_seconds REAL,
    enabled INTEGER NOT NULL, running INTEGER NOT NULL DEFAULT 0,
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    schedule_json TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'session',
    failures INTEGER NOT NULL DEFAULT 0, last_error TEXT);
CREATE TABLE automation_runs (
    id TEXT PRIMARY KEY, automation_id TEXT NOT NULL, started REAL NOT NULL,
    finished REAL, status TEXT NOT NULL, result TEXT,
    name TEXT, session_id TEXT, scheduled REAL,
    trigger TEXT NOT NULL DEFAULT 'schedule', unread INTEGER NOT NULL DEFAULT 1);
CREATE TABLE automation_scheduler (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), owner TEXT, expires REAL);
CREATE INDEX automation_runs_started ON automation_runs(started DESC);
"""


class AutomationService:
    MAX_CONCURRENT = 3
    LEASE_SECONDS = 30
    LEASE_RENEW_SECONDS = 10

    def __init__(self, path: Path, harness: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.harness = path, harness
        self.owner = uuid.uuid4().hex
        self.tasks: set[asyncio.Task] = set()
        self.executions: dict[str, asyncio.Task] = {}
        self.runner: asyncio.Task | None = None
        self.last_error: str | None = None
        self._close_task: asyncio.Task | None = None
        self.closed = False
        with self.db() as db:
            prepare_database(db, SCHEMA)
            db.execute("INSERT OR IGNORE INTO automation_scheduler VALUES(1,NULL,0)")

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _row(self, row) -> dict[str, Any]:
        item = dict(row)
        item["schedule"] = json.loads(item.pop("schedule_json"))
        return item

    def list(self) -> list[dict[str, Any]]:
        with self.db() as db:
            return [self._row(row) for row in db.execute("SELECT * FROM automations ORDER BY enabled DESC,due")]

    def _get(self, db, aid):
        row = db.execute("SELECT * FROM automations WHERE id=?", (aid,)).fetchone()
        if row is None:
            raise HarnessError(tr('python.automations.6205508a57ad'), code="automation/not-found")
        return row

    def save(self, body: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise invalid(tr('python.automations.4e4c62d0d38b'))
        fields = [body.get(key) for key in ("name", "prompt", "session_id")]
        if any(not isinstance(value, str) or not value.strip() for value in fields):
            raise invalid(tr('python.automations.a98866fc2586'))
        name, prompt, session_id = [value.strip() for value in fields]
        if len(name) > 120 or len(prompt) > 32000:
            raise invalid(tr('python.automations.d81c0b990f09'))
        self.harness.get_session(session_id)
        schedule, tz = validate_schedule(body)
        mode = body.get("mode", "session")
        if not isinstance(mode, str) or mode not in {"session", "fresh"}:
            raise invalid(tr('python.automations.15315c70d21d'))
        enabled = body.get("enabled", True)
        if type(enabled) not in (bool, int) or enabled not in (0, 1):
            raise invalid(tr('python.automations.3585b038beee'))
        if "id" in body and (not isinstance(body["id"], str) or not body["id"].strip()):
            raise invalid(tr('python.automations.14da6c2ebfb4'))
        creating = 'id' not in body
        creation_id = body.get('creation_id')
        if ((creating and (not isinstance(creation_id, str) or not re.fullmatch('[0-9a-f]{32}', creation_id)))
                or (not creating and 'creation_id' in body)):
            raise invalid(tr('ui.automations.creationIdentityInvalid'))
        aid = 'auto_' + creation_id if creating else body['id']
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if creating and db.execute('SELECT 1 FROM automations WHERE id=?', (aid,)).fetchone():
                raise HarnessError(tr('ui.automations.creationExists'), code='automation/create-exists')
            old = self._get(db, aid) if not creating else None
            if old and old["running"]:
                raise HarnessError(tr('python.automations.d9bb1d6c2915'), code="automation/running")
            same = old and self._row(old)["schedule"] == schedule and old["timezone"] == tz
            due = old["due"] if same else next_due(schedule, tz, time.time())
            if enabled and old and not old["enabled"] and due <= time.time():
                if same and schedule["kind"] == "once":
                    raise invalid(tr('python.automations.once_expired'))
                due = next_due(schedule, tz, time.time(), due)
            db.execute("""INSERT INTO automations(id,name,session_id,prompt,due,interval_seconds,enabled,timezone,schedule_json,mode)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,session_id=excluded.session_id,prompt=excluded.prompt,due=excluded.due,
                interval_seconds=excluded.interval_seconds,enabled=excluded.enabled,timezone=excluded.timezone,
                schedule_json=excluded.schedule_json,mode=excluded.mode,failures=0,last_error=NULL""",
                (aid, name, session_id, prompt, due, schedule.get("seconds"), int(enabled), tz, json.dumps(schedule), mode))
            return self._row(self._get(db, aid))

    def remove(self, aid: str, *, session_id: str | None = None) -> None:
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._get(db, aid)
            if session_id is not None and row["session_id"] != session_id:
                raise HarnessError(tr('python.automations.6205508a57ad'), code="automation/not-found")
            if row["running"]:
                raise HarnessError(tr('python.automations.02c64258ba45'), code="automation/running")
            db.execute("DELETE FROM automations WHERE id=?", (aid,))

    def history(self) -> list[dict[str, Any]]:
        with self.db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM automation_runs ORDER BY started DESC,rowid DESC LIMIT 200")]

    def mark_read(self, rid: str) -> None:
        with self.db() as db:
            if not db.execute("SELECT 1 FROM automation_runs WHERE id=?", (rid,)).fetchone():
                raise HarnessError(tr('python.automations.192492bcf93e'), code="automation/not-found")
            db.execute("UPDATE automation_runs SET unread=0 WHERE id=?", (rid,))

    def _lease(self, db) -> bool:
        now = time.time()
        lease = db.execute("SELECT * FROM automation_scheduler WHERE singleton=1").fetchone()
        if lease["owner"] != self.owner and lease["expires"] > now:
            for task in self.tasks:
                task.cancel()
            return False
        if lease["owner"] != self.owner:
            db.execute("UPDATE automation_runs SET status='interrupted',finished=?,result=? WHERE status='running'",
                       (now, json.dumps({"error": tr('python.automations.a0df1324fb83')}, ensure_ascii=False)))
            for row in db.execute("SELECT * FROM automations WHERE running=1").fetchall():
                item = self._row(row)
                if item["schedule"]["kind"] == "once":
                    db.execute("UPDATE automations SET enabled=0 WHERE id=?", (item["id"],))
                else:
                    db.execute("UPDATE automations SET due=? WHERE id=?", (next_due(item["schedule"], item["timezone"], now, item["due"]), item["id"]))
            db.execute("UPDATE automations SET running=0")
        # 每次仍在调用方的写事务内核对所有权，仅在续期间隔到达时写租约。
        if lease["owner"] != self.owner or lease["expires"] <= now + self.LEASE_SECONDS - self.LEASE_RENEW_SECONDS:
            db.execute("UPDATE automation_scheduler SET owner=?,expires=? WHERE singleton=1", (self.owner, now + self.LEASE_SECONDS))
        return True

    def _busy_sessions(self) -> set[str]:
        runtime = getattr(self.harness, "runtime", None)
        paused = {sid for sid, control in getattr(runtime, "_queue_controls", {}).items() if control.get("paused")}
        records = runtime.live_records() if hasattr(runtime, 'live_records') else getattr(runtime, "records", {}).values()
        return paused | set(getattr(runtime, "active", {})) | {record.session_id for record in records if record.status == "queued"}

    def _claim(self, db, row, trigger: str) -> dict[str, Any]:
        item = self._row(row)
        rid = "ar_" + uuid.uuid4().hex
        db.execute("UPDATE automations SET running=1 WHERE id=?", (item["id"],))
        db.execute("""INSERT INTO automation_runs(id,automation_id,started,status,name,session_id,scheduled,trigger)
            VALUES(?,?,?,'running',?,?,?,?)""", (rid, item["id"], time.time(), item["name"], item["session_id"], item["due"], trigger))
        return {**item, "run_id": rid, "trigger": trigger}

    def _forget_execution(self, aid: str, task: asyncio.Task) -> None:
        if self.executions.get(aid) is task:
            self.executions.pop(aid, None)

    def _spawn(self, row):
        task = asyncio.create_task(self._execute(row))
        self.tasks.add(task)
        self.executions[row["id"]] = task
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(lambda done: self._forget_execution(row["id"], done))

    async def tick(self, now: float | None = None) -> int:
        self._require_open()
        now = time.time() if now is None else now
        claimed = []
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not self._lease(db):
                return 0
            busy = self._busy_sessions() | {row[0] for row in db.execute("SELECT session_id FROM automations WHERE running=1 AND mode='session'")}
            capacity = self.MAX_CONCURRENT - db.execute("SELECT COUNT(*) FROM automations WHERE running=1").fetchone()[0]
            for row in db.execute("SELECT * FROM automations WHERE enabled=1 AND running=0 AND due<=? ORDER BY due", (now,)).fetchall():
                if len(claimed) >= capacity:
                    break
                if row["mode"] == "session" and row["session_id"] in busy:
                    continue
                claimed.append(self._claim(db, row, "schedule"))
                if row["mode"] == "session":
                    busy.add(row["session_id"])
        for item in claimed:
            self._spawn(item)
        return len(claimed)

    async def run_now(self, aid: str) -> dict[str, Any]:
        self._require_open()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not self._lease(db):
                raise HarnessError(tr('python.automations.c296282c8ad5'), code="automation/busy")
            row = self._get(db, aid)
            occupied = {r[0] for r in db.execute("SELECT session_id FROM automations WHERE running=1 AND mode='session'")}
            if row["running"] or (row["mode"] == "session" and row["session_id"] in self._busy_sessions() | occupied):
                raise HarnessError(tr('python.automations.2de273d0596e'), code="automation/running")
            if db.execute("SELECT COUNT(*) FROM automations WHERE running=1").fetchone()[0] >= self.MAX_CONCURRENT:
                raise HarnessError(tr('python.automations.ed030f07b3f5'), code="automation/busy")
            item = self._claim(db, row, "manual")
        self._spawn(item)
        return {"run_id": item["run_id"]}

    async def stop(self, aid: str) -> None:
        task = self.executions.get(aid)
        if task is None:
            raise HarnessError(tr('python.automations.e3b0a557ee67'), code="automation/running")
        # 先让执行进入 try/finally，再发送取消信号。
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _execute(self, row: dict[str, Any]) -> None:
        rid, session_id = row["run_id"], row["session_id"]
        status, result = "failed", {}
        try:
            if row["mode"] == "fresh":
                source = self.harness.get_session(session_id)
                session_id = self.harness.create_session(cwd=source.header.cwd, title=row["name"]).id
                with self.db() as db:
                    db.execute("UPDATE automation_runs SET session_id=? WHERE id=?", (session_id, rid))
            output = await self.harness.run_async(session_id, row["prompt"], source="automation")
            result = output.to_dict()
            status = "completed" if output.reason == "completed" else "interrupted" if output.reason == "aborted" else "failed"
            if status == "failed":
                result["error"] = result.get("error") or tr('python.automations.b2e2f5ebcd36', p0=f'{output.reason}')
        except asyncio.CancelledError:
            status, result = "interrupted", {"error": tr('python.automations.40f09059064d')}
        except Exception as exc:
            result = {"error": str(exc)}
        finally:
            finished = time.time()
            failures = row["failures"] + 1 if status == "failed" else 0
            recurring = row["schedule"]["kind"] != "once"
            # 手动执行跨过到期点时消费该次计划，避免调度器立即重复执行。
            consumes_due = row["trigger"] == "schedule" or row["due"] <= finished
            due = next_due(row["schedule"], row["timezone"], finished, row["due"]) if recurring and consumes_due else row["due"]
            enabled = int(recurring) * row["enabled"] if consumes_due else row["enabled"]
            if failures >= 3 or status == "interrupted":
                enabled = 0
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                owned = db.execute("SELECT owner FROM automation_scheduler WHERE singleton=1").fetchone()[0] == self.owner
                if owned:
                    db.execute("UPDATE automation_runs SET finished=?,status=?,result=? WHERE id=?", (finished, status, json.dumps(result, ensure_ascii=False), rid))
                    db.execute("UPDATE automations SET running=0,due=?,enabled=?,failures=?,last_error=? WHERE id=?", (due, enabled, failures, result.get("error"), row["id"]))
            try:
                if owned:
                    await self.harness.bus.emit("automation/completed", {"session_id": session_id, "id": row["id"], "run_id": rid, "status": status, "result": result})
            except Exception:
                logger.exception(tr('feedback.5ec16c1a71db'))

    def _require_open(self) -> None:
        """关闭开始即停止所有生产入口，不再认领租约或登记运行。"""
        if self.closed:
            raise HarnessError(tr('automation.scheduler.closed'), code='automation/closed')

    def start(self) -> None:
        self._require_open()
        if self.runner and not self.runner.done():
            return
        async def loop():
            while True:
                try:
                    await self.tick()
                    self.last_error = None
                except Exception as exc:
                    self.last_error = str(exc)
                    logger.exception(tr('feedback.2cb0dc06b8f6'))
                await asyncio.sleep(2)
        self.runner = asyncio.create_task(loop())

    def health(self) -> dict[str, Any]:
        with self.db() as db:
            lease = db.execute("SELECT * FROM automation_scheduler WHERE singleton=1").fetchone()
        return {"running": bool(self.runner and not self.runner.done()), "owner": lease["owner"] == self.owner,
                "last_error": self.last_error, "max_concurrent": self.MAX_CONCURRENT}

    async def close(self) -> None:
        """共享所属收尾，重复取消不能再次打断作业或跳过租约释放。"""
        self.closed = True
        if self._close_task is None or self._close_task.done():
            self._close_task = asyncio.create_task(self._close_owned())
        await join_cleanup(self._close_task)

    async def _close_owned(self):
        # 已完成的Future可能属于旧循环；只汇集仍在运行的所属任务。
        tasks = list({task for task in (*self.tasks, self.runner) if task is not None and not task.done()})
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        errors = [value for value in results if isinstance(value, BaseException) and not isinstance(value, asyncio.CancelledError)]
        try:
            with self.db() as db:
                db.execute("UPDATE automation_scheduler SET owner=NULL,expires=0 WHERE owner=?", (self.owner,))
        except BaseException as error:
            errors.append(error)
        raise_cleanup_errors(errors)
