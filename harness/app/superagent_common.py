"""Superagent 共用的参数、严格 JSON 与语言资源边界，不依赖业务账本。"""
from __future__ import annotations

import json
from typing import Any

from ..core.errors import HarnessError
from ..i18n import tr


def _error(code: str, **values: Any) -> HarnessError:
    return HarnessError(tr('superagent.' + code, **values), code='superagent/' + code)


def _json(raw):
    """拒绝重复对象键和非标准数值，不从歧义输入制造决策。"""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    def invalid(_):
        raise ValueError
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def _text(value: Any, field: str, *, limit: int = 256, empty: bool = False) -> str:
    try:
        valid = (isinstance(value, str) and '\0' not in value
                 and (empty or bool(value.strip())) and len(value.encode('utf-8')) <= limit)
    except UnicodeError:
        valid = False
    if not valid:
        raise _error('invalid', field=field)
    return value


def _names(value: Any, field: str, *, maximum: int = 256, nonempty: bool = False) -> list[str]:
    if not isinstance(value, (list, tuple)) or not int(nonempty) <= len(value) <= maximum:
        raise _error('invalid', field=field)
    result = [_text(item, field) for item in value]
    if len(set(result)) != len(result):
        raise _error('invalid', field=field)
    return sorted(result)


def _page(after: Any, limit: Any) -> None:
    if type(after) is not int or not 0 <= after < 9223372036854775807:
        raise _error('invalid', field='after')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise _error('invalid', field='limit')


def _array(raw: Any, field: str, *, maximum: int = 256, nonempty: bool = False) -> list[str]:
    if not isinstance(raw, str) or len(raw) > maximum * 1540 + 2:
        raise _error('corrupt')
    return _names(json.loads(raw), field, maximum=maximum, nonempty=nonempty)
