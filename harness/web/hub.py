"""实时连接中枢：多端同步、断线重连补发、慢客户端隔离。

三个职责：

* **多端同步** —— 同一会话可以开多个浏览器标签，A 标签跑的一轮回合会实时
  镜像到 B/C 标签，而不是各自跑一份。
* **断线补发** —— 每条事件带单调递增的 ``seq`` 并进环形缓冲；客户端重连时
  带上 ``?since=<seq>``，服务端把断线期间的事件补齐，避免"刷新一次丢一段"。
* **慢客户端隔离** —— 观察者连接走独立发送队列 + 独立写协程；队列积压超限
  就丢弃并告警，必要时按 1013 断开，**绝不让一个卡住的标签页把 agent 回合拖死**。
"""

from __future__ import annotations

from harness.i18n import tr

import asyncio
import threading
from functools import wraps
import contextlib
import hashlib
import json
import logging
import time
import uuid
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field

from ..core.jsonutil import to_jsonable
from typing import Any, Iterable

logger = logging.getLogger(__name__)

#: 每个会话保留多少条事件用于断线补发。
DEFAULT_REPLAY_SIZE = 600

#: 观察者发送队列上限。超过说明这个标签页消费不过来（最小化/后台标签页）。
DEFAULT_OUTBOX_SIZE = 400

#: 单连接累计丢弃超过这个数就直接断开，避免前端在残缺流上继续渲染。
DEFAULT_MAX_DROPS = 240

# 仅排空已排队事件，不为凑满批次而延迟首个事件。
# 限制每次写入的工作量，让控制帧和其他客户端有机会运行。
SEND_BATCH_EVENTS = 32


@dataclass(slots=True)
class Subscriber:
    """一个观察者连接（不带驱动回合的"跟随者"）。"""

    conn: Any
    queue: "asyncio.Queue[dict[str, Any] | None]"
    writer: "asyncio.Task[None] | None" = None
    dropped: int = 0

    def close(self) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            self.queue.put_nowait(None)
        if self.writer is not None and not self.writer.done() and not self.writer.cancelling():
            self.writer.cancel()


@dataclass(slots=True)
class Room:
    """一个会话对应的实时房间。"""

    session_id: str
    seq: int = 0
    run_id: str | None = None
    replay: deque = field(default_factory=lambda: deque(maxlen=DEFAULT_REPLAY_SIZE))
    subscribers: dict[int, Subscriber] = field(default_factory=dict)


def _locked(method):
    """HTTP读取与所属循环更新共享快照锁；同步方法内部不等待异步I/O。"""
    @wraps(method)
    def invoke(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return invoke


class ConnectionHub:
    """全局连接中枢（每个 Web 服务实例一个）。"""

    def __init__(
        self,
        *,
        replay_size: int = DEFAULT_REPLAY_SIZE,
        outbox_size: int = DEFAULT_OUTBOX_SIZE,
        max_drops: int = DEFAULT_MAX_DROPS,
    ) -> None:
        self._lock = threading.RLock()
        self._rooms: dict[str, Room] = {}
        self._replay_size = replay_size
        self._outbox_size = outbox_size
        self._max_drops = max_drops
        self.server_id = uuid.uuid4().hex
        self._drafts: dict[str, dict[str, Any]] = {}
        self._generations: dict[str, dict[str, Any]] = {}
        self._member_previews: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
        self._member_closed_runs: dict[str, deque[str]] = {}
        self._completion_cursor = 0
        self._completion_floor = 0
        self._completion_counts: dict[str, int] = {}
        self._completions: deque[dict[str, Any]] = deque(maxlen=DEFAULT_REPLAY_SIZE)

    @_locked
    def activity_completions(self, server_id=None, after=None, visible=None) -> dict[str, Any]:
        """独立保留轻量终态，短任务不依赖客户端曾观察到运行中。"""
        identity, cursor, floor = self.server_id, self._completion_cursor, self._completion_floor
        rows = list(self._completions)
        if visible is not None:
            # 游标和序号同样按授权集合投影，不能泄漏其他对话的运行次数。
            identity += ':' + hashlib.sha256(json.dumps(sorted(visible)).encode('utf-8')).hexdigest()
            rows = [item for item in rows if item['session_id'] in visible]
            cursor = sum(self._completion_counts.get(sid, 0) for sid in visible)
            floor = cursor - len(rows)
            rows = [{**item, 'seq': floor + index + 1} for index, item in enumerate(rows)]
        resumed = server_id == identity and after is not None and after <= cursor
        return {'server_id': identity, 'completion_cursor': cursor,
                'completions_lost': bool(resumed and after < floor),
                'completions': [dict(item) for item in rows if resumed and item['seq'] > after]}

    # -- 房间 ------------------------------------------------------------
    @_locked
    def room(self, session_id: str) -> Room:
        room = self._rooms.get(session_id)
        if room is None:
            room = Room(
                session_id=session_id,
                replay=deque(maxlen=self._replay_size),
            )
            self._rooms[session_id] = room
        return room

    @_locked
    def forget(self, session_id: str) -> None:
        """会话被删除时清掉它的房间。"""
        room = self._rooms.pop(session_id, None)
        self._drafts.pop(session_id, None)
        self._generations.pop(session_id, None)
        self._member_previews.pop(session_id, None)
        self._member_closed_runs.pop(session_id, None)
        self._completion_counts.pop(session_id, None)
        self._completions = deque((item for item in self._completions if item['session_id'] != session_id),
                                  maxlen=self._completions.maxlen)
        if room is None:
            return
        for subscriber in list(room.subscribers.values()):
            subscriber.close()
        room.subscribers.clear()

    @_locked
    def rooms(self) -> Iterable[str]:
        return tuple(self._rooms)

    # -- 订阅 ------------------------------------------------------------
    @_locked
    def subscribe(self, session_id: str, conn: Any) -> Subscriber:
        """把连接登记为某会话的观察者，并启动它的写协程。"""
        room = self.room(session_id)
        previous = room.subscribers.get(id(conn))
        if previous is not None:
            previous.close()
        subscriber = Subscriber(conn=conn, queue=asyncio.Queue(maxsize=self._outbox_size))
        room.subscribers[id(conn)] = subscriber
        subscriber.writer = asyncio.ensure_future(self._writer(session_id, subscriber))
        return subscriber

    @_locked
    def unsubscribe(self, session_id: str, conn: Any) -> None:
        room = self._rooms.get(session_id)
        if room is None:
            return
        subscriber = room.subscribers.pop(id(conn), None)
        if subscriber is not None:
            subscriber.close()


    @_locked
    def observer_count(self) -> int:
        return sum(len(room.subscribers) for room in self._rooms.values())

    # -- 事件 ------------------------------------------------------------
    @_locked
    def next_seq(self, session_id: str) -> int:
        room = self.room(session_id)
        room.seq += 1
        return room.seq

    @_locked
    def stamp(self, session_id: str, event: dict[str, Any]) -> dict[str, Any]:
        """复制调用方正文后编号，缓存与各观察者分别拥有独立JSON快照。"""
        event = to_jsonable(event)
        seq = self.next_seq(session_id)
        event["seq"] = seq
        event["stream_seq"] = seq
        event["server_id"] = self.server_id
        kind = event.get("type")
        room = self.room(session_id)
        run_id = event.get('id') if kind == 'run/state' else event.get('run_id')
        if (kind == 'run/state' and event.get('kind') != 'context_compact'
                and event.get('started_at') is not None and isinstance(run_id, str) and run_id
                and event.get('status') in {'completed', 'failed', 'interrupted'}
                and not any(item['run_id'] == run_id and item['session_id'] == session_id
                            for item in self._completions)):
            if len(self._completions) == self._completions.maxlen:
                self._completion_floor = self._completions[0]['seq']
            self._completion_cursor += 1
            self._completion_counts[session_id] = self._completion_counts.get(session_id, 0) + 1
            self._completions.append({'seq': self._completion_cursor, 'session_id': session_id,
                                      'run_id': run_id, 'status': event['status']})
        if kind == 'turn/start' and isinstance(run_id, str):
            room.run_id = run_id
        elif kind in {'run/state', 'done'} and room.run_id and isinstance(run_id, str) and run_id != room.run_id:
            # 保留原事件供订阅者按身份处理，但旧运行不能改当前临时投影。
            room.replay.append(to_jsonable(event))
            return to_jsonable(event)
        generation = self._generations.get(session_id)
        request_id = event.get('request_id')
        matched = generation is not None and (request_id is None or request_id == generation['request_id'])
        if kind == 'request/start' and isinstance(event.get('generation'), dict) and isinstance(request_id, str):
            self._generations[session_id] = {**event['generation'], 'request_id': request_id,
                                             '_clock': time.monotonic(), '_last_visible': None}
        elif kind == 'request/progress' and matched and isinstance(event.get('generation'), dict):
            update = event['generation']
            generation.update(update)
            if 'elapsed_ms' in update:
                generation['_clock'] = time.monotonic() - update['elapsed_ms'] / 1000
                generation['_last_visible'] = None
            for field in ('network_idle_ms', 'progress_idle_ms'):
                if field in update:
                    generation['_' + field] = time.monotonic() - update[field] / 1000
        elif kind == 'assistant/delta' and matched:
            field = 'thinking_chars' if event.get('delta_type') == 'thinking' else 'text_chars'
            generation[field] = generation.get(field, 0) + len(str(event.get('delta', '')))
            generation['phase'] = 'thinking' if field == 'thinking_chars' else 'writing'
            generation['_last_visible'] = time.monotonic()
        elif kind == 'request/end' and matched:
            self._generations.pop(session_id, None)
        elif kind in {'turn/start', 'turn/end', 'done', 'error'} or (
                kind == 'run/state' and event.get('started_at', True) is not None and event.get('status') in {'completed', 'failed', 'interrupted'}):
            self._generations.pop(session_id, None)
        elif kind in {'assistant/message', 'assistant/partial'} and matched:
            self._generations.pop(session_id, None)
        if kind in {"turn/start", "request/start"}:
            self._drafts[session_id] = {"text": "", "thinking": "", "turn": event.get("turn"), **({"speaker": dict(event["speaker"])} if isinstance(event.get("speaker"), Mapping) else {})}
        elif kind == "assistant/delta":
            if generation is None or matched:
                draft = self._drafts.setdefault(session_id, {"text": "", "thinking": ""})
                field = "thinking" if event.get("delta_type") == "thinking" else "text"
                draft[field] += str(event.get("delta", ""))
        elif kind in {"assistant/message", "assistant/partial", "turn/end"} and not (kind == "assistant/partial" and isinstance(event.get("author"), Mapping)):
            self._drafts.pop(session_id, None)
        previews = self._member_previews.setdefault(session_id, {})
        run_id, member = event.get('run_id'), event.get('member')
        closed = self._member_closed_runs.setdefault(session_id, deque(maxlen=64))
        if kind == 'agent/member-stream' and isinstance(run_id, str) and isinstance(member, str):
            key = (run_id, member)
            old = previews.get(key)
            version = event.get('version')
            valid = isinstance(version, int) and not isinstance(version, bool) and version > 0 and isinstance(event.get('agent_id'), str) and isinstance(event.get('text'), str) and len(event['text']) <= 12000
            newer = valid and (old is None or old.get('agent_id') == event.get('agent_id') and version > old.get('version', 0) or old.get('stage') == 'planning' and event.get('stage') in ('reply', 'summary'))
            if valid and newer and run_id not in closed:
                if key not in previews and len(previews) >= 32:
                    previews.pop(next(iter(previews)))
                if event.get('status') == 'cleared':
                    previews[key] = dict(event)
                elif event.get('status') == 'streaming':
                    previews[key] = dict(event)
        elif kind == 'agent/team' and event.get('phase') in ('done','failed','interrupted') and isinstance(run_id, str):
            closed.append(run_id)
            for key in list(previews):
                if key[0] == run_id:
                    previews.pop(key)
        elif kind in ('turn/end','error'):
            closed.extend(key[0] for key in previews)
            previews.clear()
        self.room(session_id).replay.append(to_jsonable(event))
        return to_jsonable(event)

    @_locked
    def member_previews(self, session_id: str) -> list[dict[str, Any]]:
        """返回当前有界预览，重连不依赖增量重放窗口。"""
        return [to_jsonable(row) for row in self._member_previews.get(session_id, {}).values() if row.get('status') == 'streaming']

    @_locked
    def draft(self, session_id: str) -> dict[str, Any]:
        return to_jsonable(self._drafts.get(session_id, {}))

    @_locked
    def generation(self, session_id: str) -> dict[str, Any] | None:
        """临时模型尝试遥测，使用服务端单调时钟计时。"""
        state = self._generations.get(session_id)
        if state is None:
            return None
        now = time.monotonic()
        last_visible = state['_last_visible'] if state['_last_visible'] is not None else state['_clock']
        return {**{key: value for key, value in state.items() if not key.startswith('_')},
                'elapsed_ms': max(0, int((now - state['_clock']) * 1000)),
                'idle_ms': max(0, int((now - last_visible) * 1000)),
                **{field: max(0, int((now - state.get('_' + field, state['_clock'])) * 1000))
                   for field in ('network_idle_ms', 'progress_idle_ms') if field in state}}

    @_locked
    def replay(self, session_id: str, since: int) -> list[dict[str, Any]]:
        """返回 ``seq > since`` 的缓存事件。"""
        room = self._rooms.get(session_id)
        if room is None:
            return []
        return [to_jsonable(event) for event in room.replay if int(event.get("seq", 0)) > since]

    @_locked
    def last_seq(self, session_id: str) -> int:
        room = self._rooms.get(session_id)
        return room.seq if room else 0

    @_locked
    def recovery(self, session_id: str, since: int) -> dict[str, Any]:
        room = self.room(session_id)
        oldest = int(room.replay[0]["seq"]) if room.replay else room.seq + 1
        return {"server_id": self.server_id, "last_seq": room.seq,
                "reset_required": since > room.seq or since < oldest - 1,
                "oldest_seq": oldest}

    @_locked
    def publish(
        self,
        session_id: str,
        event: dict[str, Any],
        *,
        exclude: Any = None,
    ) -> None:
        """把事件扇出给所有观察者（不含 ``exclude`` 指向的驱动连接）。"""
        room = self._rooms.get(session_id)
        if room is None or not room.subscribers:
            return
        for key, subscriber in list(room.subscribers.items()):
            if exclude is not None and key == id(exclude):
                continue
            self._enqueue(session_id, key, subscriber, to_jsonable(event))

    @_locked
    def _enqueue(
        self,
        session_id: str,
        key: int,
        subscriber: Subscriber,
        event: dict[str, Any],
    ) -> None:
        try:
            subscriber.queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass
        # 队列满：丢掉最旧的一条给新事件腾位置，并计数。
        subscriber.dropped += 1
        with contextlib.suppress(asyncio.QueueEmpty):
            subscriber.queue.get_nowait()
        try:
            subscriber.queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(tr('log.7d19c19e95f3'), session_id)
        if subscriber.dropped >= self._max_drops:
            logger.warning(
                tr('log.cebbc9dbb261'), session_id, subscriber.dropped
            )
            room = self._rooms.get(session_id)
            if room is not None:
                room.subscribers.pop(key, None)
            subscriber.close()
            asyncio.create_task(self._disconnect(subscriber.conn))

    async def _disconnect(self, conn: Any) -> None:
        try:
            if hasattr(conn, "send_close"):
                await conn.send_close(1013, "client too slow; recover snapshot")
            else:
                await conn.close()
        except Exception:
            logger.debug(tr('log.b6ef3477d920'), exc_info=True)

    # -- 写协程 ----------------------------------------------------------
    async def _writer(self, session_id: str, subscriber: Subscriber) -> None:
        """把队列里的事件写给一个观察者连接。"""
        conn = subscriber.conn
        send_batch = getattr(conn, 'send_json_batch', None)
        try:
            while True:
                event = await subscriber.queue.get()
                if event is None:
                    return
                if getattr(conn, "closed", False):
                    return
                if not callable(send_batch):
                    await conn.send_json(event)
                    continue
                batch = [event]
                stop = False
                while len(batch) < SEND_BATCH_EVENTS:
                    try:
                        pending = subscriber.queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if pending is None:
                        stop = True
                        break
                    batch.append(pending)
                await send_batch(batch)
                if stop:
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug(tr('log.ce6e019d25bc'), session_id, exc_info=True)
        finally:
            with self._lock:
                room = self._rooms.get(session_id)
                if room is not None and room.subscribers.get(id(conn)) is subscriber:
                    room.subscribers.pop(id(conn), None)

    # -- 收尾 ------------------------------------------------------------
    @_locked
    def close_all(self) -> None:
        """服务停止时断开全部观察者连接。"""
        for room in list(self._rooms.values()):
            for subscriber in list(room.subscribers.values()):
                subscriber.close()
            room.subscribers.clear()
        self._rooms.clear()
        self._drafts.clear()
        self._generations.clear()
        self._member_previews.clear()
        self._member_closed_runs.clear()
        self._completions.clear()
        self._completion_counts.clear()
        self._completion_floor = self._completion_cursor


__all__ = [
    "DEFAULT_REPLAY_SIZE",
    "DEFAULT_OUTBOX_SIZE",
    "DEFAULT_MAX_DROPS",
    "Subscriber",
    "Room",
    "ConnectionHub",
]
