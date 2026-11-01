"""会话事件词汇表。

会话日志是 **append-only** 的类型化事件流，是 agent 全部交互历史的唯一真相源。
LLM 消息历史永远是从日志 **派生** 出来的，不单独存储。

事件分两类：

* **surface 事件**（产生 LLM 消息，会进入"有序表面"）：
  ``system/message``、``developer/message``、``user/message``、
  ``assistant/message``、``tool/result``。
* **log-only 事件**（只记录，不进消息历史）：
  ``turn/start``、``turn/end``、``step/start``、``step/end``、
  ``request/header``、``tool/call``、``skill/catalog``、``todo/write``、``note``。

插件可以通过 :data:`PLUGIN_EVENTS` 追加自己的事件类型。
"""

from __future__ import annotations

from harness.i18n import tr

from dataclasses import dataclass, field
from typing import Any, Literal

from ..core.jsonutil import JsonValue
from ..core.errors import SessionInvariantError

# ---------------------------------------------------------------------------
# 表面操作（SurfaceOp）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AppendOp:
    """默认的追加操作：在表面尾部追加一个节点。"""

    kind: Literal["append"] = "append"

    def to_dict(self) -> dict[str, Any]:
        return {"op": "append"}


@dataclass(frozen=True, slots=True)
class ReplaceOp:
    """替换操作：把表面 ``[start_seq, end_seq]`` 区间（含两端）替换为一个新节点。

    用于上下文压缩——用一个摘要消息替换掉一大段旧消息。
    """

    start_seq: int
    end_seq: int
    kind: Literal["replace"] = "replace"

    def to_dict(self) -> dict[str, Any]:
        return {"op": "replace", "start_seq": self.start_seq, "end_seq": self.end_seq}


SurfaceOp = AppendOp | ReplaceOp

#: 产生 LLM 消息、可以出现在有序表面上的事件类型。
SURFACE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "system/message",
        "developer/message",
        "user/message",
        "assistant/message",
        "tool/result",
    }
)

#: 已知事件类型 → 事件名的描述信息（用于文档与错误提示）。
KNOWN_EVENT_TYPES: dict[str, str] = {
    # 表面事件
    "system/message": tr('python.events.6dded31b2203'),
    "developer/message": tr('python.events.0b0379a79282'),
    "user/message": tr('python.events.a7dfeaf0c12c'),
    "assistant/message": tr('python.events.5f6e93f682c0'),
    "tool/result": tr('python.events.7615dda6cdcc'),
    "agent/message": tr('agent.message.event'),
    # 生命周期
    "turn/start": tr('python.events.7a38eb4a258f'),
    "turn/end": tr('python.events.a71284bbb516'),
    "step/start": tr('python.events.f14e74e6152f'),
    "step/end": tr('python.events.a4d422e0989c'),
    # 请求元数据
    "request/header": tr('python.events.2f789cbb4a85'),
    "request/attempt": tr('python.events.a17b37d02571'),
    "tool/call": tr('python.events.0a9da2994c12'),
    "tool/output": tr('python.events.8da794cfcfd7'),
    # 状态投影
    "skill/catalog": tr('python.events.2f584e7bc0a8'),
    "todo/write": tr('python.events.abdea898997d'),
    "plan/mode": tr('python.events.4fce6ffa823f'),
    "note": tr('python.events.182f2abb45fa'),
    "compaction/start": tr('python.events.f3c2440c0366'),
    "compaction/summary": tr('python.events.67e6bf8eef5a'),
    "compaction/progress": tr('python.events.cb251dd26237'),
    "compaction/end": tr('python.events.e0905ccd16b7'),
}

#: 插件可追加的事件类型（每个元素为 ``(类型名, 是否 surface 事件)``）。
PLUGIN_EVENTS: list[tuple[str, bool]] = [
    ("compaction/task", False),
    ("compaction/node", False),
    ("compaction/reuse", False),
    # 重复工具调用提醒的 UI 通知本身是 log-only：真正进消息历史的是那条
    # 带 source 标签的 user/message，这条只用于让前端高亮"模型在原地打转"。
    ("notice/repeat-tool", False),
    # 工具结果被裁剪前的"影子计价"事件（log-only）。它紧贴替换节点落库，
    # 纯消费者据此把被遮蔽节点的启发式开销减回去，无需逐节点保存状态。
    ("compaction/prune", False),
    # Agent挂载是 log-only 的会话协作状态：它只改变"下一次请求装配出来的
    # 系统提示"（agent:active 动态片段），本身不进消息历史——
    # 与 plan/mode 同一纪律。
    ("agent/mode", False),
    # Agent 团队进度保留在聊天日志中，刷新与重启可还原，不进入模型消息。
    ("agent/team", False),
]


def is_surface_event(event_type: str) -> bool:
    """判断事件类型是否是 surface 事件（会进入 LLM 消息历史）。"""
    return event_type in SURFACE_EVENT_TYPES or any(
        name == event_type and surface for name, surface in PLUGIN_EVENTS
    )


# ---------------------------------------------------------------------------
# 会话事件
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SessionEvent:
    """会话日志中的一条不可变事件。

    :param type: 事件类型，必须是 :data:`KNOWN_EVENT_TYPES` 或插件追加的类型。
    :param seq:  会话内单调递增的序号（从 0 开始）。
    :param time: Unix 毫秒时间戳。
    :param data: 事件负载，必须是 JSON 友好值。
    :param surface_op: surface 事件必填；log-only 事件必须为 ``None``。
    :param source_event_seqs: 本节点引用的更早源事件序号（替换类操作用于校验）。
    :param ignorable: 标记该事件在不认识其类型时可安全跳过。
    """

    type: str
    seq: int
    time: int
    data: JsonValue
    surface_op: SurfaceOp | None = None
    source_event_seqs: tuple[int, ...] | None = None
    ignorable: bool = False

    def to_dict(self) -> dict[str, Any]:
        """转成 JSON 友好字典（落库与传输用）。"""
        out = self.json_view()
        out['data'] = selfdata_to_json(self.data)
        if self.source_event_seqs is not None:
            out['source_event_seqs'] = list(self.source_event_seqs)
        return out

    def json_view(self) -> dict[str, Any]:
        """序列化视图，不复制不可变负载容器。"""
        out: dict[str, Any] = {
            "type": self.type,
            "seq": self.seq,
            "time": self.time,
            "data": self.data,
        }
        if self.surface_op is not None:
            out["surface_op"] = self.surface_op.to_dict()
        if self.source_event_seqs is not None:
            out["source_event_seqs"] = self.source_event_seqs
        if self.ignorable:
            out["ignorable"] = True
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SessionEvent":
        """从 JSON 字典还原事件。"""
        surface_op: SurfaceOp | None = None
        raw_op = raw.get("surface_op")
        if raw_op is not None:
            if not isinstance(raw_op, dict) or raw_op.get('op') not in ('append', 'replace'):
                raise SessionInvariantError(tr('session.event.invalidOperation'))
            if raw_op.get("op") == "replace":
                start, end = raw_op.get('start_seq'), raw_op.get('end_seq')
                if type(start) is not int or type(end) is not int or not 0 <= start <= end:
                    raise SessionInvariantError(tr('session.event.invalidOperation'))
                surface_op = ReplaceOp(start_seq=raw_op["start_seq"], end_seq=raw_op["end_seq"])
            else:
                surface_op = AppendOp()
        src = raw.get("source_event_seqs")
        if src is not None and (not isinstance(src, list)
                                or any(type(seq) is not int or seq < 0 for seq in src)):
            raise SessionInvariantError(tr('session.event.invalidSources'))
        return cls(
            type=raw["type"],
            seq=raw["seq"],
            time=raw["time"],
            data=raw.get("data"),
            surface_op=surface_op,
            source_event_seqs=tuple(src) if isinstance(src, list) else None,
            ignorable=bool(raw.get("ignorable", False)),
        )


def selfdata_to_json(data: JsonValue) -> JsonValue:
    """把事件负载转成 JSON 友好值（薄封装，便于统一处理）。"""
    from ..core.jsonutil import to_jsonable

    return to_jsonable(data)


# ---------------------------------------------------------------------------
# 请求头（EpochHeader）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EpochHeader:
    """一次请求的配置快照。

    系统提示词 **不在** 这里——它是 ``system/message`` 事件，属于派生历史。
    """

    config: dict[str, Any]
    tools: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"config": self.config}
        if self.tools:
            out["tools"] = self.tools
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EpochHeader":
        return cls(config=raw.get("config", {}), tools=raw.get("tools"))


#: ``request/header`` 被记录的原因。
RequestHeaderReason = Literal["initial", "resume", "change", "series"]

# ---------------------------------------------------------------------------
# 回合结束原因
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TurnEndReason:
    """一轮对话结束的原因。"""

    kind: str  # 结束类型：completed / aborted / error / max-tokens / blocked / interrupted / forked
    cause: str | None = None  # aborted 时的取消来源：user / parent / hook / disposed / unknown
    error: dict[str, Any] | None = None  # error 时的结构化失败信息
    message: str | None = None  # 人类可读描述

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind}
        if self.cause is not None:
            out["cause"] = self.cause
        if self.error is not None:
            out["error"] = self.error
        if self.message is not None:
            out["message"] = self.message
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TurnEndReason":
        return cls(
            kind=raw.get("kind", "completed"),
            cause=raw.get("cause"),
            error=raw.get("error"),
            message=raw.get("message"),
        )


# ---------------------------------------------------------------------------
# 会话头
# ---------------------------------------------------------------------------


#: 当前会话格式版本。格式结构发生不兼容变化时递增。
SESSION_FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class SessionHeader:
    """不可变的会话元数据（与事件日志分离存储）。"""

    id: str
    created_at: int
    version: int = SESSION_FORMAT_VERSION
    cwd: str | None = None
    parent_session: str | None = None
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "created_at": self.created_at, "version": self.version}
        if self.cwd is not None:
            out["cwd"] = self.cwd
        if self.parent_session is not None:
            out["parent_session"] = self.parent_session
        if self.title is not None:
            out["title"] = self.title
        if self.metadata:
            out["metadata"] = self.metadata
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SessionHeader":
        if (not isinstance(raw, dict)
                or not isinstance(raw.get('id'), str) or not raw['id'].strip()
                or type(raw.get('version')) is not int or raw['version'] != SESSION_FORMAT_VERSION
                or type(raw.get('created_at')) is not int or raw['created_at'] < 0
                or any(raw.get(key) is not None and not isinstance(raw[key], str) for key in ('cwd', 'parent_session', 'title'))
                or not isinstance(raw.get('metadata', {}), dict)):
            raise SessionInvariantError(tr('session.header.invalid'))
        return cls(
            id=raw["id"],
            created_at=raw["created_at"],
            version=raw["version"],
            cwd=raw.get("cwd"),
            parent_session=raw.get("parent_session"),
            title=raw.get("title"),
            metadata=dict(raw.get("metadata", {})),
        )


__all__ = [
    "AppendOp",
    "ReplaceOp",
    "SurfaceOp",
    "SURFACE_EVENT_TYPES",
    "KNOWN_EVENT_TYPES",
    "PLUGIN_EVENTS",
    "is_surface_event",
    "SessionEvent",
    "EpochHeader",
    "RequestHeaderReason",
    "TurnEndReason",
    "SessionHeader",
    "SESSION_FORMAT_VERSION",
]


def user_message_text(data) -> str:
    """展示用户原始输入；模型仍从同一事件的 text 构建上下文。"""
    original = data.get("original_text")
    return original if isinstance(original, str) else str(data.get("text", ""))
