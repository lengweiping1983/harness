"""会话存储：内存索引 + JSONL 持久化。

存储布局::

    <root>/sessions/<session_id>/header.json     # 会话元数据
    <root>/sessions/<session_id>/events.jsonl    # append-only 事件日志（每行一条）

设计要点：
* **先写盘再广播**：事件落盘成功后才对外可见，避免"内存有、磁盘无"；
* **写失败阻断**：提交失败不广播，锁定会话，阻止后续副作用；
* 加载保留损坏日志，未完成回合由运行控制标记中断，不重放工具。
"""

from __future__ import annotations

from harness.i18n import tr

import json
import logging
import os
import tempfile
import threading
import weakref
from collections import OrderedDict
from collections.abc import Mapping
from pathlib import Path
from stat import S_ISREG
from typing import Any

from ..core.errors import SessionNotFoundError, SessionInvariantError
from ..core.fsatomic import atomic_write as _atomic_write
from ..core.jsonutil import deep_freeze
from .events import SESSION_FORMAT_VERSION, AppendOp, SessionEvent, SessionHeader, TurnEndReason
from .cache import SessionCache
from .session import Session, new_session_id, now_ms
from .write_lock import event_write_lock

logger = logging.getLogger(__name__)


class SessionStore:
    """会话仓库：管理多个会话的生命周期与持久化。

    :param root: 持久化根目录；``None`` 表示纯内存（测试用）。
    """

    def __init__(self, root: str | os.PathLike[str] | None = None, *, bus=None, request_traces=None, file_evidence=None) -> None:
        self.root = Path(root).expanduser().resolve() if root is not None else None
        self.bus = bus
        # 运行期缓存：session_id -> Session
        self._load_lock = threading.RLock()
        self._cache = SessionCache(persistent=self.root is not None, lock=self._load_lock)
        # 同一会话正在进行的操作共享一把锁；空闲或已删除的会话
        # 不会让长期运行服务的锁注册表无限增长。
        self._header_locks: weakref.WeakValueDictionary[str, Any] = weakref.WeakValueDictionary()
        # 已订阅落盘的会话，避免重复订阅
        self._persisted: set[str] = set()
        self._page_indexes: OrderedDict[str, tuple[int, int, list[int]]] = OrderedDict()
        self._append_versions: dict[str, tuple[tuple[int, ...], int]] = {}
        self.catalog_revision = 0
        from .file_evidence import FileEvidenceStore
        self.file_evidence = file_evidence if file_evidence is not None else FileEvidenceStore(self.root/'file-evidence' if self.root is not None else None)
        from .request_traces import RequestTraces
        self.request_traces = request_traces if request_traces is not None else RequestTraces(self.root/'request-traces.sqlite3' if self.root is not None else None)

    # ------------------------------------------------------------------
    # 创建 / 获取
    # ------------------------------------------------------------------
    def loaded_sessions(self) -> tuple[Session, ...]:
        """当前载入的会话；状态广播不应为此读取全部历史日志。"""
        with self._load_lock:
            return tuple(self._cache.values())

    def _header_lock(self, session_id: str) -> Any:
        with self._load_lock:
            lock = self._header_locks.get(session_id)
            if lock is None:
                lock = threading.RLock()
                self._header_locks[session_id] = lock
            return lock

    def create(
        self,
        *,
        cwd: str | None = None,
        title: str | None = None,
        parent_session: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Session:
        """创建会话；持久仓库先提交头信息，空会话也能跨重启恢复。"""
        header = SessionHeader(
            id=new_session_id(),
            created_at=now_ms(),
            version=SESSION_FORMAT_VERSION,
            cwd=cwd,
            title=title,
            parent_session=parent_session,
            metadata=dict(metadata) if metadata is not None else {},
        )
        session = Session(header, bus=self.bus)
        with self._load_lock:
            if self.root is not None:
                self._write_header(session)
            self._attach_persistence(session)
            self._cache[session.id] = session
            self.catalog_revision += 1
        return session

    def get(self, session_id: str) -> Session:
        """按 id 获取会话；内存没有则尝试从磁盘加载。"""
        session = self._cache.get(session_id)
        if session is not None:
            return session
        # HTTP 读取线程与核心循环可能同时打开尚未加载的历史。
        # 必须共享一个投影和提交监听器，不能生成两个可写对象。
        with self._load_lock:
            session = self._cache.get(session_id)
            if session is None:
                session = self._load(session_id)
                if session is None:
                    raise SessionNotFoundError(tr('python.store.3e747ad07bf5', p0=f'{session_id}'), session_id=session_id)
                self._attach_persistence(session)
                self._cache[session_id] = session
            return session

    def reopen(self, session_id: str) -> Session:
        """已关闭的持久成员会话重新装配写入器，保留原日志和所有回合。"""
        with self._load_lock:
            old = self.get(session_id)
            if not old._closed:
                return old
            if old.persistence_error or old.open_turn is not None:
                raise SessionInvariantError(tr('subagent.context.invalid'))
            if self.root is not None:
                session = self._load(session_id)
            else:
                session = Session(old.header, bus=self.bus)
                session.restore(old.events)
            if session is None:
                raise SessionInvariantError(tr('subagent.context.invalid'))
            self._persisted.discard(session_id)
            self._cache[session_id] = session
            self._attach_persistence(session)
            return session

    def has(self, session_id: str) -> bool:
        return session_id in self._cache or (self.root is not None and self._dir(session_id).exists())

    def events_path(self, session_id: str) -> Path | None:
        """会话事件日志路径：未持久化或文件不存在时返回 ``None``。

        供只想读原始事件、不需要重建 :class:`Session` 的调用方使用。
        例如 Web 侧边栏只需要消息条数、最后活动时间与最后一条用户文本，
        没必要为此把整个会话从磁盘重建一遍。"""
        if self.root is None:
            return None
        path = self._dir(session_id) / "events.jsonl"
        try:
            return path if S_ISREG(path.stat().st_mode) else None
        except FileNotFoundError:
            return None

    def list_sessions(self) -> list[SessionHeader]:
        """列出所有会话头，按创建时间倒序。"""
        headers: list[SessionHeader] = []
        with self._load_lock:
            cached = self._cache.snapshot()
        for session_id, session in cached.items():
            # 热缓存或外部持有者仍可保留投影；目录被外部删除时不能继续列出。
            # 持久标记用于区分纯内存会话，不根据缓存缺失删除任何文件。
            if session_id in self._persisted and self.root is not None:
                if not self._dir(session_id).exists():
                    with self._load_lock:
                        if self._cache.get(session_id) is session:
                            self._cache.pop(session_id, None)
                            self._persisted.discard(session_id)
                    continue
            headers.append(session.header)
        if self.root is not None and self.root.exists():
            for entry in (self.root / "sessions").glob("*/header.json"):
                if entry.parent.name.startswith('.'):
                    continue
                try:
                    raw = json.loads(entry.read_text("utf-8"))
                    header = SessionHeader.from_dict(raw)
                    if header.id != entry.parent.name:
                        raise SessionInvariantError(tr('session.header.invalid'))
                    if header.id not in cached:
                        headers.append(header)
                except Exception:
                    logger.warning(tr('log.c9b8fc3b8169'), entry)
        return sorted(headers, key=lambda h: h.created_at, reverse=True)

    def update_header(self, session_id: str, **changes: Any) -> dict[str, Any]:
        """更新会话头的可变字段（标题 / metadata 里的模型选择等）并落盘。

        只改白名单里的字段：``id`` / ``created_at`` 一旦被改写，历史事件里的
        ``session_id`` 就再也对不上，重放会直接坏掉。
        """
        with self._header_lock(session_id):
            session = self.get(session_id)
            metadata = dict(session.header.metadata)
            metadata_changes = changes.pop("metadata", None) or {}
            metadata.update(metadata_changes)
            title = changes.pop("title", ...)
            # 显式改名撤销未完成的自动命名，即使新旧名称相同也保留人工选择。
            if title is not ... and 'auto_title' in metadata and 'auto_title' not in metadata_changes:
                metadata['auto_title'] = 'manual'
            payload = session.header.to_dict()
            payload["metadata"] = metadata
            if title is not ...:
                payload["title"] = title
            candidate = SessionHeader.from_dict(payload)
            if self.root is not None:
                self._write_header(session, header=candidate)
            # 仅发布已成功持久化的值，失败写入不会泄露给读取者，
            # 也无需回滚其他写入者的结果。
            session.header = candidate
            with self._load_lock:
                self.catalog_revision += 1
            return candidate.to_dict()

    def delete(self, session_id: str) -> None:
        """删除一个会话（内存 + 磁盘）。"""
        with self._header_lock(session_id):
            self.request_traces.delete(session_id)
            self._delete(session_id)

    def _delete(self, session_id: str) -> None:
        # 删除成功后才释放投影和派生状态；文件系统失败保留当前身份。
        with self._load_lock:
            if self.root is not None:
                path = self._dir(session_id)
                if path.exists():
                    import shutil

                    shutil.rmtree(path)
            self._cache.pop(session_id, None)
            self._persisted.discard(session_id)
            self._page_indexes.pop(session_id, None)
            self._append_versions.pop(session_id, None)
            self.catalog_revision += 1

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def _dir(self, session_id: str) -> Path:
        assert self.root is not None
        if not session_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in session_id):
            raise SessionInvariantError(tr('python.store.dc259251f011'))
        return self.root / "sessions" / session_id

    def _attach_persistence(self, session: Session) -> None:
        """订阅会话事件，事件一追加就落盘。

        写入是**同步**的：单条 JSONL 追加只是几百字节的本地写，
        同步做换来的是"事件一旦进入日志就一定在磁盘上"这个强保证，
        不会出现"内存有、进程挂了就没了"的窗口。真正的 IO 重量级场景
        应由业务方换用自定义存储实现。
        """
        session.file_evidence = self.file_evidence
        session.request_traces = self.request_traces
        self.request_traces.register_session(session)
        if self.root is None or session.commit_event is not None:
            return
        self._persisted.add(session.id)
        self._ensure_dir(session.id)

        session_id = session.id
        projection = weakref.ref(session)
        def on_append(event: SessionEvent) -> None:
            # 弱引用避免提交回调反向持有投影；外部使用者仍保持唯一写入身份。
            with self._header_lock(session_id):
                current = projection()
                if current is None or self._cache.get(session_id) is not current:
                    raise SessionNotFoundError(tr('python.store.644f72e63e54'), session_id=session_id)
                self._append_event(session_id, event)

        session.commit_event = on_append
        weakref.finalize(session, self._release_projection_if_alive, weakref.ref(self), session.id)

    @staticmethod
    def _release_projection_if_alive(store_ref, session_id: str) -> None:
        """终结器不强持有仓库，避免已释放仓库被全局终结器表保活。"""
        store = store_ref()
        if store is not None:
            store._release_projection(session_id)

    def _release_projection(self, session_id: str) -> None:
        """最后持有者释放后移除派生元数据；不删除任何持久文件。"""
        with self._load_lock:
            if session_id in self._cache:
                return
            self._persisted.discard(session_id)
            self._append_versions.pop(session_id, None)
            self._page_indexes.pop(session_id, None)

    def _ensure_dir(self, session_id: str) -> None:
        assert self.root is not None
        self._dir(session_id).mkdir(parents=True, exist_ok=True)

    def _write_header(self, session: Session, *, header: SessionHeader | None = None) -> None:
        """原子写入 header.json（先写临时文件再 rename，避免半截文件）。"""
        assert self.root is not None
        path = self._dir(session.id) / "header.json"
        payload = json.dumps((header or session.header).to_dict(), ensure_ascii=False, indent=2)
        _atomic_write(path, payload)

    def _append_event(self, session_id: str, event: SessionEvent) -> None:
        """把一条事件同步追加到 events.jsonl。"""
        path = self._dir(session_id) / "events.jsonl"
        line = json.dumps(event.to_dict(), ensure_ascii=False) + "\n"
        # 进程内的头锁不能保护另一个后端的过期投影。序号比较和追加
        # 必须处于同一操作系统锁中；外部改动后重新验证整个日志。
        with event_write_lock(path):
            with path.open("a+b") as handle:
                def version() -> tuple[int, ...]:
                    stat = os.fstat(handle.fileno())
                    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

                stamp = version()
                cached = self._append_versions.get(session_id)
                if cached is not None and cached[0] == stamp:
                    count = cached[1]
                else:
                    count = 0
                    handle.seek(0)
                    for raw_line in handle:
                        if not raw_line.strip():
                            continue
                        raw = json.loads(raw_line)
                        if raw.get("seq") != count or not raw_line.endswith(b"\n"):
                            raise SessionInvariantError(tr('python.store.5e7500a77405'),
                                                        session_id=session_id, expected_seq=count,
                                                        actual_seq=raw.get("seq"))
                        count += 1
                if event.seq != count:
                    raise SessionInvariantError(tr('python.store.5e7500a77405'),
                                                session_id=session_id, expected_seq=count,
                                                actual_seq=event.seq)
                handle.write(line.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
                self._append_versions[session_id] = (version(), count + 1)

    def _load(self, session_id: str) -> Session | None:
        """从磁盘加载一个会话。"""
        if self.root is None:
            return None
        directory = self._dir(session_id)
        header_path = directory / "header.json"
        events_path = directory / "events.jsonl"
        if not header_path.exists():
            return None
        try:
            header = SessionHeader.from_dict(json.loads(header_path.read_text("utf-8")))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            logger.exception(tr('log.32bab1713494'), session_id)
            raise SessionInvariantError(tr('python.store.08bf4e82bdd3'), session_id=session_id) from exc

        if header.version != SESSION_FORMAT_VERSION or header.id != session_id:
            raise SessionInvariantError(tr('python.store.08bf4e82bdd3'), session_id=session_id)
        session = Session(header, bus=self.bus)
        events: list[SessionEvent] = []
        if events_path.exists():
            # 逐行流式读取：``read_text().splitlines()`` 会同时持有一份完整
            # 字符串和一份完整的行列表，峰值内存约为日志大小的 3~4 倍；长会话
            # （几十 MB）会让 Web 服务的常驻内存直接翻几倍。
            with events_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError:
                        # 崩溃可能留下半行，截断处之后全部丢弃。
                        logger.warning(tr('log.e3946e5f1c4d'), session_id)
                        session.persistence_error = tr('python.store.c7f3ce251019')
                        break
                    event = SessionEvent.from_dict(raw)
                    if event.seq != len(events):
                        raise SessionInvariantError(tr('python.store.5e7500a77405'), session_id=session_id,
                                                    expected_seq=len(events), actual_seq=event.seq)
                    events.append(event)

        # 用公共 API 重建：内部索引、表面与回合状态一次到位
        session.restore(events)
        return session

    def fork(self, session_id: str, *, through_seq: int | None = None, title: str | None = None,
             before_message_seq: int | None = None) -> Session:
        """复制完整前缀，或在用户消息前建立可编辑分支；不回退外部副作用。"""
        source = self.get(session_id)
        if source.persistence_error:
            raise SessionInvariantError(tr('python.store.e7146432fb8c'))
        if before_message_seq is not None:
            if through_seq is not None or type(before_message_seq) is not int:
                raise SessionInvariantError(tr('python.store.invalidEditTarget'))
            target = source.event_at(before_message_seq)
            if target is None or not (
                target.type == 'user/message' and target.data.get('source', 'user') in {'user', 'steer'}
                or target.type == 'note' and target.data.get('kind') == 'steering/accepted'
            ):
                raise SessionInvariantError(tr('python.store.invalidEditTarget'))
            # 应用后的引导也回到其首次接受的位置，避免复制已执行的后续步骤。
            if target.data.get('source') == 'steer':
                record = source.steering.records.get(target.data.get('request_id'))
                if record:
                    before_message_seq = record['accepted_seq']
            through_seq = before_message_seq - 1
        end = source.event_count - 1 if through_seq is None else through_seq
        if type(end) is not int or end < -1 or end >= source.event_count:
            raise SessionInvariantError(tr('python.store.8e5bc7c3f7e3'))
        events = list(source.events[:end + 1])
        probe = Session()
        probe.restore(events)
        if probe.open_turn is not None:
            if before_message_seq is None:
                raise SessionInvariantError(tr('python.store.634d7a46a46d'))
            start = probe.latest('turn/start')
            if not any(event.type == 'user/message' for event in events[start.seq + 1:]):
                # 首条提问尚未进入模型时，连同空回合一起撤回。
                events = events[:start.seq]
            else:
                # 分支不接管原执行。为尚未返回的调用补充明确的中断结果，
                # 保证下一次模型请求拥有成对的工具消息，不重放这些调用。
                pending = {}
                for message in probe.build_messages():
                    for call in getattr(message, 'tool_uses', ()):
                        pending[call.id] = call
                    for result in getattr(message, 'results', ()):
                        pending.pop(result.tool_use_id, None)
                if pending:
                    events.append(SessionEvent(type='tool/result', seq=len(events), time=now_ms(),
                        surface_op=AppendOp(), data=deep_freeze({'results': [
                            {'tool_use_id': call_id, 'content': tr('python.store.editInterruptedTool'), 'is_error': True}
                            for call_id in pending]})))
                events.append(SessionEvent(type='turn/end', seq=len(events), time=now_ms(), data=deep_freeze({
                    'turn': probe.open_turn, 'reason': TurnEndReason(kind='interrupted',
                        message=tr('python.store.editBranchBoundary')).to_dict()})))
        # 子会话在前缀和投影完整前不进入目录或缓存；复用原子导入发布。
        child = Session(SessionHeader(id=new_session_id(), created_at=now_ms(),
            version=SESSION_FORMAT_VERSION, cwd=source.header.cwd,
            title=title or source.header.title, parent_session=source.id), bus=self.bus)
        # 分叉拥有复制的证据，同时保留检查实际执行的位置。
        # 不改写对话、工具结果或原始日志。
        copied = []
        for event in events:
            if event.type == 'note' and isinstance(event.data, Mapping) and event.data.get('kind') == 'hook_run' and event.data.get('session_id') == source.id:
                raw = event.to_dict()
                raw['data'] = {**raw['data'], 'session_id': child.id,
                               'origin_session_id': event.data.get('origin_session_id') or source.id}
                copied.append(SessionEvent.from_dict(raw))
            else:
                copied.append(event)
        metadata = dict(source.header.metadata)
        for key in ("archived", "goal", "goal_state_version", "plan_mode", "permission_grants"):
            metadata.pop(key, None)
        if before_message_seq is not None:
            metadata['edit_origin'] = {'session_id': source.id, 'source_seq': before_message_seq}
        child.header = SessionHeader.from_dict({**child.header.to_dict(), "metadata": metadata})
        child.restore(copied)
        child = self.import_session({"header": child.header.to_dict(), "events": [event.to_dict() for event in copied]})
        self.catalog_revision += 1
        return child

    @staticmethod
    def _page_event(line: bytes, session_id: str) -> dict[str, Any]:
        """分页遇到损坏行返回明确存储错误，不跳过或改写原文件。"""
        try:
            raw = json.loads(line)
            event = SessionEvent.from_dict(raw)
            return event.to_dict()
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise SessionInvariantError(tr('python.store.invalid_event_page'), session_id=session_id) from exc

    def read_events(self, session_id: str, *, after: int = -1, limit: int = 200) -> list[dict[str, Any]]:
        """持久事件分页，与临时 WebSocket 游标无关。"""
        if type(after) is not int or type(limit) is not int or after < -1 or not 1 <= limit <= 1000:
            raise SessionInvariantError(tr('python.store.0fd418ff18a1'))
        session = self._cache.get(session_id)
        if session is not None:
            return [e.to_dict() for e in session._events[after + 1:after + 1 + limit]]
        path = self.events_path(session_id)
        if path is None or not path.exists():
            self.get(session_id)
            return []
        stat = path.stat()
        cached = self._page_indexes.get(session_id)
        if cached is None or cached[:2] != (stat.st_size, stat.st_mtime_ns):
            # 稀疏字节索引：不装载全量事件，分页也不用重复扫描整个文件。
            offsets = []
            count = 0
            with path.open("rb") as handle:
                while True:
                    offset = handle.tell()
                    line = handle.readline()
                    if not line:
                        break
                    if not line.strip():
                        continue
                    raw = self._page_event(line, session_id)
                    if raw["seq"] != count:
                        raise SessionInvariantError(tr('python.store.5e7500a77405'))
                    if count % 1000 == 0:
                        offsets.append(offset)
                    count += 1
            cached = (stat.st_size, stat.st_mtime_ns, offsets)
            with self._load_lock:
                self._page_indexes[session_id] = cached
                self._page_indexes.move_to_end(session_id)
                while len(self._page_indexes) > 128:
                    self._page_indexes.popitem(last=False)
        result = []
        block = (after + 1) // 1000
        if block >= len(cached[2]):
            return result
        with path.open("rb") as handle:
            handle.seek(cached[2][block])
            for line in handle:
                if not line.strip():
                    continue
                raw = self._page_event(line, session_id)
                if raw["seq"] > after:
                    result.append(raw)
                    if len(result) == limit:
                        break
        return result

    # ------------------------------------------------------------------
    # 导入 / 导出
    # ------------------------------------------------------------------
    def export_session(self, session_id: str) -> dict[str, Any]:
        """导出会话为纯 JSON（header + events），便于备份或迁移。"""
        session = self.get(session_id)
        return {
            "header": session.header.to_dict(),
            "events": [event.to_dict() for event in session.events],
        }

    def import_session(self, data: dict[str, Any], *, persist: bool = True) -> Session:
        """恢复完整导出；持久仓库默认原子导入，拒绝覆盖不同历史。

        persist=False 用于只读检查；该副本不替换缓存，后续追加也不会写入仓库。
        """
        if (not isinstance(data, dict) or not isinstance(data.get("header"), dict)
                or not isinstance(data.get("events"), list)
                or any(not isinstance(raw, dict) for raw in data["events"])):
            raise SessionInvariantError(tr('python.store.invalid_import'))
        header = SessionHeader.from_dict(data["header"])
        if not persist:
            return self._import_session(data, header, persist=False)
        with self._header_lock(header.id), self._load_lock:
            return self._import_session(data, header, persist=True)

    def _import_session(self, data: dict[str, Any], header: SessionHeader, *, persist: bool) -> Session:
        if header.version != SESSION_FORMAT_VERSION:
            raise SessionInvariantError(tr('python.store.5855e9a43bde'), session_id=header.id)
        events = [SessionEvent.from_dict(raw) for raw in data["events"]]
        if any(e.seq != i for i, e in enumerate(events)):
            raise SessionInvariantError(tr('python.store.c0dd70d6f5ea'), session_id=header.id)
        session = Session(header, bus=self.bus)
        session.restore(events)
        if persist and self.root is not None:
            import shutil
            target = self._dir(header.id)
            if (target / "header.json").exists():
                existing = self._load(header.id)
                if existing is None or existing.persistence_error or self.export_session(header.id) != data:
                    raise SessionInvariantError(tr('python.store.54f37783f96b'), session_id=header.id)
                # 相同快照复用仓库对象，避免两个对象以相同序号继续追加。
                return self.get(header.id)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                staged = Path(tempfile.mkdtemp(prefix=".import-", dir=target.parent))
                try:
                    _atomic_write(staged / "header.json", json.dumps(header.to_dict(), ensure_ascii=False))
                    _atomic_write(staged / "events.jsonl", "".join(json.dumps(e.to_dict(), ensure_ascii=False) + "\n" for e in events))
                    # create() 会提前建空目录；只允许替换空目录。
                    if target.exists():
                        target.rmdir()
                    os.replace(staged, target)
                finally:
                    if staged.exists():
                        shutil.rmtree(staged)
            self._persisted.discard(header.id)
            self._attach_persistence(session)
        if persist:
            with self._load_lock:
                self._cache[session.id] = session
        return session



__all__ = ["SessionStore"]
