"""执行入口共用当前参数类型与数值范围校验，不隐式转换用户输入。"""
from __future__ import annotations
import math
from ..core.errors import ToolExecutionError
from ..i18n import tr


def finite_number(value: object, argument: str, minimum: float, maximum: float) -> float:
    """只接受范围内有限数字，布尔值和数字字符串不作为数字。"""
    try:
        valid = type(value) in (int, float) and minimum <= value <= maximum and math.isfinite(value)
    except (OverflowError, TypeError):
        valid = False
    if not valid:
        raise ToolExecutionError(tr('tool.arguments.numberRange', argument=argument, minimum=minimum, maximum=maximum))
    return float(value)


def string_argument(value: object, argument: str) -> str:
    """拒绝空白或非字符串，合法正文保留原始空白和字符。"""
    if not isinstance(value, str) or not value.strip():
        raise ToolExecutionError(tr('tool.arguments.text', argument=argument))
    return value


def background_argument(value: object, available: bool) -> bool:
    """后台开关只接受布尔值，没有作业表时不能接受后台执行。"""
    if type(value) is not bool:
        raise ToolExecutionError(tr('tool.arguments.boolean', argument='run_in_background'))
    if value and not available:
        raise ToolExecutionError(tr('tool.arguments.backgroundUnavailable'))
    return value
