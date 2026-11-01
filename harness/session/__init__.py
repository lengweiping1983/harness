"""会话层：事件词汇表、append-only 日志与持久化存储。"""

from .events import (
    KNOWN_EVENT_TYPES,
    SESSION_FORMAT_VERSION,
    SURFACE_EVENT_TYPES,
    AppendOp,
    EpochHeader,
    ReplaceOp,
    SessionEvent,
    SessionHeader,
    SurfaceOp,
    TurnEndReason,
    is_surface_event,
)
from .session import Session, create_session, new_session_id, now_ms
from .store import SessionStore

__all__ = [
    "Session",
    "SessionHeader",
    "create_session",
    "new_session_id",
    "now_ms",
    "SessionStore",
    "SessionEvent",
    "EpochHeader",
    "TurnEndReason",
    "AppendOp",
    "ReplaceOp",
    "SurfaceOp",
    "KNOWN_EVENT_TYPES",
    "SURFACE_EVENT_TYPES",
    "SESSION_FORMAT_VERSION",
    "is_surface_event",
]
