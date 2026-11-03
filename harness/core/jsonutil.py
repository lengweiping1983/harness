"""JSON 值类型与工具函数。

会话日志里的每一条事件都必须是 **可无损 JSON 化** 的值。因此我们需要一个
:data:`JsonValue` 递归类型别名，以及：

* :func:`to_jsonable` —— 把任意 Python 对象尽量转成 JSON 友好值；
* :func:`deep_freeze` —— 冻结嵌套结构，防止外部误改已落库的事件；
* :func:`canonical_json` —— 稳定序列化（键排序），用于内容哈希。

这些函数刻意写得保守：宁可抛 :class:`TypeError`，也不要静默丢数据。
"""

from __future__ import annotations

from harness.i18n import tr

import hashlib
import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Mapping, Sequence, TypeAlias, Union

# 递归 JSON 类型别名。Python 3.10+ 支持在类型别名里递归引用字符串。
JsonValue: TypeAlias = Union[
    None,
    bool,
    int,
    float,
    str,
    Sequence["JsonValue"],
    Mapping[str, "JsonValue"],
]

class JsonConversionError(TypeError):
    """无法把某个 Python 对象转换成 JSON 值。"""


def to_jsonable(value: Any, *, _path: str = "$") -> JsonValue:
    """把任意 Python 对象转换为 :data:`JsonValue`。

    支持：``None`` / ``bool`` / ``int`` / ``float`` / ``str``、``list`` / ``tuple``、
    ``dict``（键必须是 ``str``）、``dataclass``、``Enum``、``set`` / ``frozenset``。

    :raises JsonConversionError: 遇到无法无损表示的对象。
    """
    # 基础标量快速路径。
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        # NaN / Inf 不是合法 JSON，显式拒绝而不是产出 ``NaN`` 这种非法文本。
        if value != value or value in (float("inf"), float("-inf")):
            raise JsonConversionError(tr('python.jsonutil.c73ed506f4db', p0=f'{_path}', p1=f'{value!r}'))
        return value
    if isinstance(value, Enum):
        return to_jsonable(value.value, _path=_path)
    if is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(asdict(value), _path=_path)
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise JsonConversionError(tr('python.jsonutil.22cba73d169f', p0=f'{_path}', p1=f'{type(key).__name__}'))
            result[key] = to_jsonable(item, _path=f"{_path}.{key}")
        return result
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item, _path=f"{_path}[{i}]") for i, item in enumerate(value)]
    if isinstance(value, (set, frozenset)):
        # 集合无序，先排序保证结果稳定，再转换。
        return [to_jsonable(item, _path=f"{_path}[]") for item in sorted(value, key=repr)]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return to_jsonable(value.to_dict(), _path=_path)
    raise JsonConversionError(tr('python.jsonutil.2d05eaaa6d8d', p0=f'{_path}', p1=f'{type(value).__name__}'))


def deep_freeze(value: Any) -> Any:
    """递归冻结容器：``dict`` -> ``MappingProxyType``，``list`` -> ``tuple``。

    冻结用于保证已经 ``append`` 进会话的事件不会被调用方在事后修改，
    保持"日志不可变"的核心约束。标量值原样返回。
    """
    from types import MappingProxyType

    if isinstance(value, dict):
        return MappingProxyType({k: deep_freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(deep_freeze(v) for v in value)
    if isinstance(value, tuple):
        return tuple(deep_freeze(v) for v in value)
    return value


def canonical_json(value: Any) -> str:
    """稳定序列化：键排序、无多余空格、保留非 ASCII。

    用于内容哈希与去重。``ensure_ascii=False`` 让中文在文件里保持可读。
    """
    return json.dumps(to_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(value: Any) -> str:
    """对任意 JSON 友好值求 SHA-256 十六进制摘要。"""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


__all__ = [
    "JsonValue",
    "JsonConversionError",
    "to_jsonable",
    "deep_freeze",
    "canonical_json",
    "content_hash",
]
