"""JSON Schema 子集校验器。

模型给出的工具参数必须先过这一关才能进入执行体。这里实现一个
**够用且严格** 的 JSON Schema 子集：

支持的类型：``object`` / ``array`` / ``string`` / ``number`` / ``integer`` /
``boolean`` / ``null``，以及 ``enum`` / ``const`` / ``properties`` /
``required`` / ``additionalProperties`` / ``items`` / ``minimum`` / ``maximum`` /
``minLength`` / ``maxLength`` / ``pattern`` / ``minItems`` / ``maxItems``。

不支持的关键字会被忽略，以兼容第三方 MCP 工具的 schema。
已支持的 ``type`` 关键字必须包含有效的 JSON Schema 类型名；
仅声明未知类型不会放行参数。
"""

from __future__ import annotations

from harness.i18n import tr

import re
import math
from typing import Any

from ..core.errors import ToolArgsError

# JSON Schema 的基本类型集合
_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}


def validate(value: Any, schema: dict[str, Any], *, path: str = "$") -> list[str]:
    """校验 ``value`` 是否满足 ``schema``，返回错误列表（空表示通过）。"""
    errors: list[str] = []
    _validate(value, schema, path, errors)
    return errors


def validate_or_raise(value: Any, schema: dict[str, Any], *, tool: str = "tool") -> Any:
    """校验失败时附带当前参数提示；不改写输入或执行工具。"""
    errors = validate(value, {**schema, "type": schema.get("type", "object")})
    if errors:
        hints = [tr('python.schema.not_executed')]
        props = schema.get('properties')
        if isinstance(props, dict):
            hints.append(tr('python.schema.parameter_fields',
                            fields=tr('ui.common.list_separator').join(props) or tr('common.none'),
                            required=tr('ui.common.list_separator').join(schema.get('required', []) or []) or tr('common.none')))
        hints.append(tr('python.schema.correct_call'))
        raise ToolArgsError(
            tr('python.schema.b004c6665581', p0=tool,
               errors=tr('common.error_separator').join(errors), hints='\n'.join(hints)),
            tool=tool,
            errors=errors,
            not_executed=True,
        )
    return value


def _with_description(error: str, schema: dict[str, Any]) -> str:
    """为约束错误附加原始字段说明，完整标点由资源控制。"""
    hint = schema.get('description')
    return tr('python.schema.db743a3ab9ed', error=error, hint=hint) if isinstance(hint, str) and hint else error


def _validate(value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    if not isinstance(schema, dict):
        return

    # -- 常量与枚举 -----------------------------------------------------
    if "const" in schema and not _json_equal(value, schema["const"]):
        errors.append(tr('python.schema.09139b4eb993', p0=f'{path}', p1=f"{schema['const']!r}", p2=f'{value!r}'))
    if "enum" in schema and isinstance(schema["enum"], list):
        if not any(_json_equal(value, item) for item in schema["enum"]):
            errors.append(tr('python.schema.eef6ffee35ef', p0=f'{path}', p1=f'{value!r}', p2=f"{schema['enum']!r}"))

    # -- 类型 -----------------------------------------------------------
    declared = schema.get("type")
    if isinstance(declared, str):
        declared = [declared]
    if isinstance(declared, list) and declared:
        if not any(_matches_type(value, t) for t in declared if t in _TYPES):
            errors.append(tr('python.schema.1b4ab236fc45', p0=f'{path}', p1=f"{'/'.join(declared)}", p2=f'{type(value).__name__}'))
            return  # 类型都不对，后面的约束检查没有意义

    # -- 字符串约束 ------------------------------------------------------
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(tr('python.schema.167e20fe390b', p0=f'{path}', p1=f'{len(value)}', p2=f"{schema['minLength']}"))
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(tr('python.schema.a0b867f336d5', p0=f'{path}', p1=f'{len(value)}', p2=f"{schema['maxLength']}"))
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            try:
                anchored = pattern.startswith("^") and pattern.endswith("$")
                if anchored:
                    escapes = len(pattern[:-1]) - len(pattern[:-1].rstrip("\\"))
                    anchored = escapes % 2 == 0
                matched = re.fullmatch(pattern, value) if anchored else re.search(pattern, value)
                if matched is None:
                    errors.append(tr('python.schema.7d249b705886', p0=f'{path}', p1=f'{pattern!r}'))
            except re.error:
                pass  # 非法正则直接跳过，不因 schema 缺陷阻塞调用

    # -- 数值约束 --------------------------------------------------------
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            errors.append(tr('python.schema.nonfinite', path=path))
            return
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(tr('python.schema.00046100056e', p0=f'{path}', p1=f'{value}', p2=f"{schema['minimum']}"))
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(_with_description(tr('python.schema.8734de7319e7', p0=f'{path}', p1=f'{value}', p2=f"{schema['maximum']}"), schema))
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(tr('python.schema.d2f44fcb2006', p0=f'{path}', p1=f'{value}', p2=f"{schema['exclusiveMinimum']}"))
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            errors.append(tr('python.schema.d1e0ecbd5bd4', p0=f'{path}', p1=f'{value}', p2=f"{schema['exclusiveMaximum']}"))

    # -- 数组 -------------------------------------------------------------
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(tr('python.schema.72fb668231c5', p0=f'{path}', p1=f'{len(value)}', p2=f"{schema['minItems']}"))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(_with_description(tr('python.schema.d0a04123c9a3', p0=f'{path}', p1=f'{len(value)}', p2=f"{schema['maxItems']}"), schema))
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, item in enumerate(value):
                _validate(item, item_schema, f"{path}[{i}]", errors)

    # -- 对象 -------------------------------------------------------------
    if isinstance(value, dict):
        props = schema.get("properties")
        props = props if isinstance(props, dict) else {}
        for name in schema.get("required", []) or []:
            if name not in value:
                errors.append(tr('python.schema.8e8235a5a2ca', p0=f'{path}', p1=f'{name!r}'))
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    errors.append(tr('python.schema.5d38167b4287', p0=f'{path}', p1=f'{key!r}',
                                     p2=', '.join(props) or tr('common.none')))
        for key, item in value.items():
            sub = props.get(key, schema.get("additionalProperties"))
            if isinstance(sub, dict):
                _validate(item, sub, f"{path}.{key}", errors)


def _json_equal(left: Any, right: Any) -> bool:
    """按 JSON 值比较，数字可相等，布尔值不能冒充数字（含嵌套值）。"""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_json_equal(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    return left == right


def _matches_type(value: Any, expected: str) -> bool:
    """判断一个值是否匹配 JSON Schema 的某个类型名。

    注意 Python 的 ``bool`` 是 ``int`` 的子类，必须单独排除。
    """
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def string_field(description: str, *, required: bool = True, default: str | None = None) -> dict[str, Any]:
    """构造一个字符串字段的 JSON Schema（工具定义里最常用）。"""
    node: dict[str, Any] = {"type": "string", "description": description}
    if not required:
        # 可选字段用 type 数组表达"字符串或缺省"，实际缺省由 default 表达
        node["type"] = ["string", "null"]
        if default is not None:
            node["default"] = default
    return node


__all__ = ["validate", "validate_or_raise", "string_field"]
