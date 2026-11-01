"""Ralph 自治循环工具。

ralph 把固定脚本当数据跑，逐轮 spawn 一个**全新**子代理，并要求每轮返回
一份结构化 report，只有这份 report 会跨轮传递。这里用 Python 原生循环
实现同一套语义：

* 每轮 spawn 一个全新子代理（不注入父会话、不复用上一轮的子会话）；
* 模型上下文只传递上一轮结构化 report，超过长度上限则拒绝报告；共享工作区保留实际成果；
* report 必须满足固定 schema（``status`` / ``summary`` / ``evidence`` /
  ``nextSteps`` / ``blocker``），字段之间有强一致性约束：
  ``continue`` 必须有 nextSteps 且 blocker 为空，
  ``complete`` 必须有 evidence、无 nextSteps 且 blocker 为空，
  ``blocked`` 必须给出具体 blocker；
* 轮数上限由部署方配置，默认 64；模型只能在上限内选择正整数；
* 任一轮子代理失败或报告非法 -> 整体报错，**不返回半成品成功**。

子代理没有结构化输出通道，因此改成「prompt 里写死 schema + 解析时按同一套
规则严格校验」。校验偏保守是好事：非法报告会明确报错，而不是被静默接受。
"""

from __future__ import annotations

from harness.i18n import tr

import asyncio
import json
import math
from typing import Any

from ...core.errors import ToolExecutionError
from ...core.cleanup import join_cleanup, raise_cleanup_errors
from ...llm.types import TextBlock
from ...subagent.manager import SubagentManager
from ..registry import ToolDefinition, ToolRunContext, ToolResult

#: 部署方封顶的轮数（模型传入的 maxRounds 只能小于等于它）
MAX_ROUNDS_CEILING = 64
#: 单次 report 允许的最大序列化字符数
MAX_HANDOFF_CHARS = 16_384
#: 原始成员报告含包装文字的解析上限，先拒绝再扫描。
MAX_REPORT_CHARS = 65_536
#: 返回给模型的最终文本上限
MAX_RESULT_CHARS = 16_384
#: 每轮等待子代理的默认上限（秒）
DEFAULT_ROUND_TIMEOUT = 900.0
#: 单个 round 允许的最小值
MIN_ROUNDS = 1

#: 允许的回合状态
ROUND_STATUSES = ("continue", "complete", "blocked")

def _extract_json_object(text: str) -> dict[str, Any] | None:
    """从自由文本里抠出第一个 JSON 对象；抠不出返回 None。"""
    if len(text) > MAX_REPORT_CHARS:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    # 1) 整段就是 JSON
    try:
        parsed = json.loads(stripped)
    except (ValueError, RecursionError):
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    # 2) ```json ... ``` 代码块
    fence = "```"
    idx = stripped.find(fence)
    while idx != -1:
        body_start = stripped.find("\n", idx)
        if body_start == -1:
            break
        body_end = stripped.find(fence, body_start)
        if body_end == -1:
            break
        block = stripped[body_start + 1 : body_end].strip()
        if block.startswith("json"):
            block = block[4:].strip()
        try:
            parsed = json.loads(block)
        except (ValueError, RecursionError):
            parsed = None
        if isinstance(parsed, dict):
            return parsed
        idx = stripped.find(fence, body_end + len(fence))
    # 3) 括号配平扫描；累计工作量有界，避免残缺嵌套反复扫描后缀。
    remaining_scan = MAX_REPORT_CHARS * 4
    for start, char in enumerate(stripped):
        if char != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for pos in range(start, len(stripped)):
            remaining_scan -= 1
            if remaining_scan < 0:
                return None
            cur = stripped[pos]
            if in_string:
                if escaped:
                    escaped = False
                elif cur == "\\":
                    escaped = True
                elif cur == '"':
                    in_string = False
                continue
            if cur == '"':
                in_string = True
            elif cur == "{":
                depth += 1
            elif cur == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(stripped[start : pos + 1])
                    except (ValueError, RecursionError):
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
    return None


def _normalized_text(value: Any) -> bool:
    return isinstance(value, str) and len(value) > 0 and value == value.strip()


def _normalized_list(value: Any) -> bool:
    return isinstance(value, list) and all(_normalized_text(v) for v in value)


def validate_report(raw: Any, max_chars: int = MAX_HANDOFF_CHARS) -> dict[str, Any]:
    """按既定规则严格校验一份 round report。"""
    if not isinstance(raw, dict):
        raise ToolExecutionError(tr('python.ralph_tools.2698c8ba2923'))
    status = raw.get("status")
    if status not in ROUND_STATUSES:
        raise ToolExecutionError(tr('python.ralph_tools.2ef7edc2705a', p0=f'{status!r}'))
    summary = raw.get("summary")
    if not _normalized_text(summary):
        raise ToolExecutionError(tr('python.ralph_tools.948625b168ba'))
    evidence = raw.get("evidence")
    next_steps = raw.get("nextSteps")
    if not _normalized_list(evidence) or not _normalized_list(next_steps):
        raise ToolExecutionError(
            tr('python.ralph_tools.ad63ce9ae25f')
        )
    blocker = raw.get("blocker")
    if not isinstance(blocker, str) or blocker != blocker.strip():
        raise ToolExecutionError(tr('python.ralph_tools.0463d4c9648f'))
    if status == "continue" and (len(next_steps) == 0 or blocker != ""):
        raise ToolExecutionError(tr('python.ralph_tools.474a75c23021'))
    if status == "complete" and (len(evidence) == 0 or len(next_steps) != 0 or blocker != ""):
        raise ToolExecutionError(tr('python.ralph_tools.3f01140901db'))
    if status == "blocked" and not _normalized_text(blocker):
        raise ToolExecutionError(tr('python.ralph_tools.2bc35ef276c6'))
    report = {
        "status": status,
        "summary": summary,
        "evidence": list(evidence),
        "nextSteps": list(next_steps),
        "blocker": blocker,
    }
    serialized = json.dumps(report, ensure_ascii=False, separators=(",", ":"))
    if len(serialized) > max_chars:
        raise ToolExecutionError(
            tr('python.ralph_tools.ac59e33256ee', p0=f'{len(serialized)}', p1=f'{max_chars}')
        )
    return report


def bound_result(text: str, max_chars: int = MAX_RESULT_CHARS) -> str:
    """把返回给模型的文本截断到上限（含截断标记）。"""
    if len(text) <= max_chars:
        return text
    notice = tr('python.ralph_tools.e1f057685052')
    if max_chars <= len(notice):
        return notice[:max_chars]
    return text[: max_chars - len(notice)] + notice


def render_result(result: dict[str, Any], max_chars: int = MAX_RESULT_CHARS) -> str:
    """渲染终态信封（明确标注这是 worker 自述，不是独立评测）。"""
    rounds = result["roundsStarted"]
    body = json.dumps(result["report"], ensure_ascii=False, indent=2)
    status = result["status"]
    if status == "complete":
        head = tr('python.ralph_tools.6b2f544a163d', p0=rounds)
    elif status == "blocked":
        head = tr('python.ralph_tools.b930844ef955', p0=rounds)
    else:
        head = tr('python.ralph_tools.188befbff7e1', p0=rounds)
    return bound_result(tr('python.ralph_tools.07483bcf5495', p0=f'{head}', p1=f'{body}'), max_chars)


def render_round_failure(rounds_started: int, last_report: dict[str, Any] | None) -> str:
    """渲染轮次失败的说明，附带最近一次有效交接。"""
    header = tr('python.ralph_tools.2505264c70ac', p0=f'{rounds_started}')
    if last_report is None:
        return tr('python.ralph_tools.eb5522426b8c', p0=f'{header}')
    body = json.dumps(last_report, ensure_ascii=False, indent=2)
    return bound_result(tr('python.ralph_tools.94497cc56be0', p0=f'{header}', p1=f'{body}'))


def resolve_max_rounds(requested: Any, ceiling: int = MAX_ROUNDS_CEILING) -> int:
    """要求模型给出的 maxRounds 是 ``[1, ceiling]`` 内的整数，越界明确拒绝。"""
    if isinstance(requested, bool):
        raise ToolExecutionError(tr('python.ralph_tools.5e379d16b79d'))
    if not isinstance(requested, int):
        raise ToolExecutionError(tr('python.ralph_tools.885b6dd0c10f', p0=repr(requested)))
    value = requested
    if value < MIN_ROUNDS:
        raise ToolExecutionError(tr('python.ralph_tools.616b19df1497', p0=f'{MIN_ROUNDS}'))
    if value > ceiling:
        raise ToolExecutionError(tr('python.ralph_tools.e7d203fb71f3', p0=f'{value}', p1=f'{ceiling}'))
    return value


def build_ralph_tools(
    manager: SubagentManager,
    *,
    round_timeout: float = DEFAULT_ROUND_TIMEOUT,
    max_rounds_ceiling: int = MAX_ROUNDS_CEILING,
) -> list[ToolDefinition]:
    """构造 ``ralph``，绑定到给定的 :class:`SubagentManager`。"""
    if type(max_rounds_ceiling) is not int or max_rounds_ceiling < MIN_ROUNDS:
        raise ValueError(tr('ralph.config.max_rounds_ceiling'))
    if type(round_timeout) not in (int, float) or round_timeout <= 0:
        raise ValueError(tr('ralph.config.round_timeout'))
    try:
        valid_timeout = math.isfinite(round_timeout) and math.isfinite(round_timeout * max_rounds_ceiling)
    except OverflowError:
        valid_timeout = False
    if not valid_timeout:
        raise ValueError(tr('ralph.config.round_timeout'))

    async def ralph(args: Any, ctx: ToolRunContext) -> ToolResult:
        """按不可变目标跑一个前台的新鲜代理迭代循环。"""
        objective = args.get("objective")
        if not isinstance(objective, str) or not objective.strip():
            raise ToolExecutionError(tr('python.ralph_tools.07cfd33c7c6f'))
        objective = objective.strip()
        max_rounds = resolve_max_rounds(args.get("maxRounds", max_rounds_ceiling), max_rounds_ceiling)

        previous: dict[str, Any] | None = None
        for round_no in range(1, max_rounds + 1):
            prior = (
                tr('python.ralph_tools.eda2bfab0704')
                if previous is None
                else json.dumps(previous, ensure_ascii=False, indent=2)
            )
            prompt = tr('python.ralph_tools.c819f80cf055',
                objective=objective,
                round=round_no,
                max_rounds=max_rounds,
                prior=prior,
            )
            state = manager.spawn(
                prompt=prompt,
                description=tr('python.ralph_tools.43c9f442d99b', p0=f'{round_no}'),
                parent_session_id=ctx.session_id,
            )
            try:
                finished = await manager.wait(state.id, timeout=round_timeout)
            except asyncio.CancelledError as cancelled:
                cleanup = asyncio.create_task(manager.interrupt(state.id, reason=tr('ralph.cancelled')))
                try:
                    await join_cleanup(cleanup)
                except asyncio.CancelledError:
                    raise cancelled
                except BaseException as failure:
                    raise_cleanup_errors([cancelled, failure])
                raise

            if not finished.finished:
                await manager.interrupt(finished.id, reason=tr('ralph.roundTimeoutReason', round=round_no))
                raise ToolExecutionError(
                    render_round_failure(round_no, previous)
                    + tr('python.ralph_tools.3cd770d73d5a', p0=f'{round_no}', p1=f'{round_timeout:g}')
                )
            if finished.status != "completed":
                detail = (finished.error_message or finished.status or tr('python.ralph_tools.a1a9da9ee6ed')).strip()
                raise ToolExecutionError(render_round_failure(round_no, previous) + tr('python.ralph_tools.a947dbd4e1e5', p0=f'{detail}'))

            report = _extract_json_object(finished.output)
            if report is None:
                raise ToolExecutionError(
                    render_round_failure(round_no, previous)
                    + tr('python.ralph_tools.147676f7b1d0')
                )
            try:
                report = validate_report(report)
            except ToolExecutionError as exc:
                raise ToolExecutionError(render_round_failure(round_no, previous) + f"\n{exc}") from None

            if report["status"] == "complete":
                result = {"status": "complete", "roundsStarted": round_no, "report": report}
                return ToolResult(
                    content=[TextBlock(text=render_result(result))],
                    display=result,
                    data=result,
                )
            if report["status"] == "blocked":
                result = {"status": "blocked", "roundsStarted": round_no, "report": report}
                return ToolResult(
                    content=[TextBlock(text=render_result(result))],
                    display=result,
                    data=result,
                )
            previous = report

        result = {"status": "budget-limited", "roundsStarted": max_rounds, "report": previous}
        return ToolResult(
            content=[TextBlock(text=render_result(result))],
            display=result,
            data=result,
        )

    return [
        ToolDefinition(
            schema={
                "description_key": 'python.ralph_tools.afcfd040b188',
                "parameter_keys": {
                    ('properties', 'objective', 'description'): 'python.ralph_tools.d457d3476f4e',
                    ('properties', 'maxRounds', 'description'): ('python.ralph_tools.a990c5a6a505', {'p0': f'{max_rounds_ceiling}'}),
                },
                "name": "ralph",
                "description": tr('python.ralph_tools.afcfd040b188'),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "objective": {
                            "type": "string",
                            "description": tr('python.ralph_tools.d457d3476f4e'),
                        },
                        "maxRounds": {
                            "type": "integer",
                            "minimum": MIN_ROUNDS,
                            "maximum": max_rounds_ceiling,
                            "description": (
                                tr('python.ralph_tools.a990c5a6a505', p0=f'{max_rounds_ceiling}')
                            ),
                        },
                    },
                    "required": ["objective"],
                    "additionalProperties": False,
                },
            },
            execute=ralph,
            concurrency_safe=False,
            timeout=round_timeout * max_rounds_ceiling,
        ),
    ]
