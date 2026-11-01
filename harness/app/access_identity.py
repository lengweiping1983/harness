"""接入层验证的不可变身份；会话范围属于凭据，不能从请求正文获取。"""
from dataclasses import dataclass

from ..core.identity import validate_subject


@dataclass(frozen=True, slots=True)
class AccessIdentity:
    """宿主身份可无资源限制；会话凭据必须绑定显式资源集合。"""

    subject: str
    session_ids: frozenset[str] | None = None

    def __post_init__(self):
        validate_subject(self.subject)
        if self.session_ids is not None and (not isinstance(self.session_ids, frozenset)
                or any(not isinstance(value, str) or not value for value in self.session_ids)):
            raise ValueError('access/invalid-resource-scope')
