"""重复工具调用提醒。

默认启用（``thresholds: [3, 5, 8]``），这是一道**咨询型护栏**：

* 按 agent（会话）维护一条"最近一次被追踪的调用"的链，键为
  ``(工具名, 参数规范化字符串)``；
* 链长度命中阈值时，向会话注入一条提醒消息（附加上下文），让模型停下来
  复盘而不是继续原地打转；
* 参数做**深键排序**后再序列化，因此仅属性顺序不同的调用视为同一次；
* ``exclude`` 中的工具既不计数也不重置链（对链透明）；
* 用户新消息重置链——跨越用户干预的重复不算死循环。

关键点：**计数发生在工具执行之后**，因此被 guard 拒绝、参数非法
等"没有真正跑起来"的调用同样计入——模型死磕一个被拒绝的调用恰恰是最该被
提醒的场景。本实现只提醒，不拦截、不改写结果。
"""

from __future__ import annotations

from harness.i18n import tr

import json
import re
import threading
from typing import Any

#: 默认阈值。
DEFAULT_THRESHOLDS: tuple[int, ...] = (3, 5, 8)

#: 详细提醒里引用的参数长度上限。
DEFAULT_ARGUMENTS_PREVIEW_CHARS = 500

GENTLE_REMINDER_KEY = 'owned.repeat_reminder.b8da397db9f0'


def detailed_reminder(tool_name: str, count: int, canonical_arguments: str) -> str:
    """第 2 档及以后的详细提醒：点名工具、连击次数与规范化参数。"""
    return tr('prompt.repeat.detailed', tool=tool_name, count=count, arguments=canonical_arguments)


def validate_thresholds(values: Any) -> tuple[int, ...]:
    """校验阈值：非空、整数、>= 2、无重复；返回升序元组。

    非法配置**快速失败**而不是静默降级。
    """
    if isinstance(values, (bool, int, float, str)) or not isinstance(values, (list, tuple)):
        raise ValueError(tr('python.repeat_reminder.41b9d763c3e3'))
    items = list(values)
    if not items:
        raise ValueError(tr('python.repeat_reminder.3858133313ac'))
    for value in items:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(
                tr('python.repeat_reminder.896c01128775', p0=f'{value!r}')
            )
        if value < 2:
            raise ValueError(
                tr('python.repeat_reminder.896c01128775', p0=f'{value}')
            )
    if len(set(items)) != len(items):
        raise ValueError(tr('python.repeat_reminder.2ae224c29b63'))
    return tuple(sorted(items))


def wildcard_to_regex(pattern: str) -> re.Pattern[str]:
    """把 ``*`` 通配模式编译成锚定正则（其余正则元字符按字面量匹配）。"""
    escaped = re.sub(r"[|\\{}()\[\]^$+?.]", r"\\\g<0>", pattern)
    escaped = escaped.replace("*", ".*")
    return re.compile(rf"^{escaped}$")


def _sort_json_value(value: Any) -> Any:
    """递归按键排序，使仅属性顺序不同的对象规范化为同一形式。"""
    if isinstance(value, dict):
        return {key: _sort_json_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_sort_json_value(item) for item in value]
    return value


def canonicalize(arguments_value: Any) -> str:
    """调用参数的规范字符串：深键排序后紧凑序列化。

    遇到无法 JSON 序列化的对象时退回 ``repr``，保证比较键永不为空。
    """
    try:
        return json.dumps(
            _sort_json_value(arguments_value),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        return repr(arguments_value)


def preview_arguments(canonical: str, cap: int) -> str:
    """截断详细提醒里引用的参数，并标注省略了多少字符。

    只限制模型可见文本——链键始终用完整规范串比较，检测不受影响。
    """
    if len(canonical) <= cap:
        return canonical
    return tr('prompt.repeat.arguments_truncated', text=canonical[:cap], count=len(canonical) - cap)


def validate_preview_chars(value: Any) -> int:
    """校验详细提醒的参数引用长度上限（整数 >= 1）。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(
            tr('python.repeat_reminder.5ba4c27840f5', p0=f'{value!r}')
        )
    return value


class RepeatToolReminder:
    """按 agent 维度维护重复调用链的提醒器。

    :param thresholds:             触发提醒的连续重复次数（升序、>= 2、去重）。
    :param include:                仅追踪匹配这些 ``*`` 模式的工具；为空表示全部。
    :param exclude:                这些工具对链透明（既不计数也不重置）。
    :param arguments_preview_chars: 详细提醒里引用参数的长度上限。
    """

    def __init__(
        self,
        thresholds: Any = DEFAULT_THRESHOLDS,
        *,
        include: Any = (),
        exclude: Any = (),
        arguments_preview_chars: Any = DEFAULT_ARGUMENTS_PREVIEW_CHARS,
    ) -> None:
        self.thresholds = validate_thresholds(thresholds)
        self._threshold_set = set(self.thresholds)
        self._include = [wildcard_to_regex(str(p)) for p in (include or ())]
        self._exclude = [wildcard_to_regex(str(p)) for p in (exclude or ())]
        self.arguments_preview_chars = validate_preview_chars(arguments_preview_chars)
        self._lock = threading.Lock()
        self._chains: dict[Any, tuple[str, int]] = {}

    # ------------------------------------------------------------------
    def tracked(self, tool_name: str) -> bool:
        """该工具是否参与追踪。未追踪的调用对链透明（既不计数也不重置）。"""
        if self._include and not any(p.match(tool_name) for p in self._include):
            return False
        return not any(p.match(tool_name) for p in self._exclude)

    def observe(self, agent_key: Any, tool_name: str, arguments_value: Any) -> str | None:
        """推进该 agent 的链，返回需要注入的提醒文本（无则 ``None``）。

        链键始终使用**完整**规范串，因此比较不受 ``arguments_preview_chars``
        影响——上限只约束模型可见文本。
        """
        if not self.tracked(tool_name):
            return None
        canonical = canonicalize(arguments_value)
        key = json.dumps([tool_name, canonical], ensure_ascii=False)
        with self._lock:
            chain = self._chains.get(agent_key)
            count = chain[1] + 1 if chain is not None and chain[0] == key else 1
            self._chains[agent_key] = (key, count)
        if count not in self._threshold_set:
            return None
        if count == self.thresholds[0]:
            return tr(GENTLE_REMINDER_KEY)
        return detailed_reminder(
            tool_name, count, preview_arguments(canonical, self.arguments_preview_chars)
        )

    def reset(self, agent_key: Any) -> None:
        """清空某个 agent 的链（用户新消息时调用）。"""
        with self._lock:
            self._chains.pop(agent_key, None)

    def forget(self, agent_key: Any) -> None:
        """:func:`reset` 的别名，语义上更贴近"删除会话时清理"。"""
        self.reset(agent_key)

    def clear_all(self) -> None:
        """清空全部链状态（测试与配置重载用）。"""
        with self._lock:
            self._chains.clear()
