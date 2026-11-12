"""Python 宿主侧：驱动 JavaScript workflow 引擎并提供子代理能力。

``workflow_guest.mjs`` 是 JS 脚本引擎的固定副本，二者通过 stdin/stdout 上的
JSON-lines RPC 对话。宿主实现协议另一侧
的五个方法：

* ``begin``          —— 下发 ``WorkerInit``（meta / 脚本源码 / args / limits）
* ``startChild``     —— spawn 一个 Python 子代理，返回句柄
* ``childResult``    —— 等待某个子代理跑完并取回结果
* ``disposeChild``   —— 丢弃句柄
* ``progress``       —— 校验并转发引擎的进度事件

所有「是否允许」的判定都落在宿主这边（limits、JSON Schema 子集、结构化
输出校验）：引擎信任宿主，不自己校验。

子代理使用当前 SubagentManager 的 model 覆盖；provider 仅支持 spawn，
不支持的提供方在启动前明确拒绝。
"""

from __future__ import annotations

from harness.core.text_encoding import TextDecoder

import asyncio
import json
import math
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ...core.cleanup import join_cleanup, raise_cleanup_errors
from ...core.errors import ToolExecutionError
from ..processes import ProcessCleanupError, start_process
from ...i18n import tr

#: guest 主入口脚本的位置（随包分发，不依赖 cwd）
GUEST_ENTRY = Path(__file__).with_name("workflow_guest_main.mjs")

#: 默认 provider
DEFAULT_PROVIDER = "spawn"
#: 单次调用允许的最大 thunk / item 数
DEFAULT_MAX_ITEMS_PER_CALL = 4096
#: 单个脚本生命周期内允许启动的子代理总数
DEFAULT_MAX_TOTAL_AGENTS = 1000
#: 并发上限的绝对天花板
MAX_CONCURRENT_AGENTS_CEILING = 16


def resolve_node() -> str:
    """返回 ``node`` 可执行文件名，找不到就抛错。"""
    node = shutil.which("node") or shutil.which("node.exe")
    if node:
        return node
    raise ToolExecutionError(
        tr('runtime.validation.deb35ce0a59a')
    )


def default_max_concurrent_agents() -> int:
    """并发上限：``min(16, max(1, cpu_count - 2))``。"""
    cpus = os.cpu_count() or 1
    return min(
        MAX_CONCURRENT_AGENTS_CEILING,
        max(1, cpus - 2),
    )


@dataclass(frozen=True)
class WorkflowLimits:
    """引擎信任宿主下发的上限，因此这份数据只由宿主构造、从不接受脚本改写。"""

    provider: str = DEFAULT_PROVIDER
    max_concurrent_agents: int = MAX_CONCURRENT_AGENTS_CEILING
    max_items_per_call: int = DEFAULT_MAX_ITEMS_PER_CALL
    max_total_agents: int = DEFAULT_MAX_TOTAL_AGENTS
    sync_timeout_ms: int = 5_000

    @classmethod
    def coerce(
        cls,
        provider: str | None = None,
        max_concurrent_agents: int | None = None,
        max_items_per_call: int | None = None,
        max_total_agents: int | None = None,
        sync_timeout_ms: int | None = None,
    ) -> WorkflowLimits:
        """把工具参数里的可选上限合并进默认值，越界值一律夹紧。"""
        base = cls()

        def _int(value: int | None, fallback: int, low: int, high: int) -> int:
            if value is None:
                return fallback
            try:
                number = int(value)
            except (TypeError, ValueError):
                return fallback
            return max(low, min(high, number))

        return cls(
            provider=provider or base.provider,
            max_concurrent_agents=_int(
                max_concurrent_agents,
                base.max_concurrent_agents,
                1,
                MAX_CONCURRENT_AGENTS_CEILING,
            ),
            max_items_per_call=_int(
                max_items_per_call, base.max_items_per_call, 1, 1_000_000
            ),
            max_total_agents=_int(
                max_total_agents, base.max_total_agents, 1, 1_000_000
            ),
            sync_timeout_ms=_int(sync_timeout_ms, base.sync_timeout_ms, 1, 3_600_000),
        )

    def to_json(self) -> dict[str, Any]:
        """引擎 ``WorkerInit.limits`` 需要的形状。"""
        return {
            "provider": self.provider,
            "maxConcurrentAgents": self.max_concurrent_agents,
            "maxItemsPerCall": self.max_items_per_call,
            "maxTotalAgents": self.max_total_agents,
            "syncTimeoutMs": self.sync_timeout_ms,
        }


class SchemaError(ValueError):
    """JSON Schema 子集校验失败。"""

    def __init__(self, violations: Sequence[str]) -> None:
        self.violations = list(violations)
        super().__init__(tr('workflow.validation.unsupported', violations=tr('common.error_separator').join(self.violations)))


#: 会被真正强制执行的约束词
CONSTRAINT_KEYWORDS = frozenset(
    {
        "type",
        "oneOf",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
    }
)
#: 只透传、不强制执行的注解词
ANNOTATION_KEYWORDS = frozenset({"description", "title", "default", "examples"})
#: 允许的单一类型
SCHEMA_TYPES: tuple[str, ...] = (
    "object",
    "array",
    "string",
    "number",
    "integer",
    "boolean",
    "null",
)
#: 不能与 ``oneOf`` 并存的词
ONE_OF_SIBLING_KEYWORDS: tuple[str, ...] = (
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "const",
)
#: 每个约束词允许出现在哪些类型上
KEYWORD_TYPES: Mapping[str, tuple[str, ...]] = {
    "properties": ("object",),
    "required": ("object",),
    "additionalProperties": ("object",),
    "items": ("array",),
    "enum": ("string", "number", "integer", "boolean", "null"),
    "const": ("string", "number", "integer", "boolean", "null"),
}


def _is_json_number(value: Any) -> bool:
    """无损 JSON number：有限，且不含负零（对齐 JS 的 ``isJsonNumber``）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return False
    if isinstance(value, float) and value == 0.0 and math.copysign(1.0, value) < 0:
        return False
    return True


def _is_json_value(value: Any) -> bool:
    """无损 JSON 值：递归检查，键必须是字符串。"""
    if value is None or isinstance(value, (str, bool)):
        return True
    if _is_json_number(value):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_value(item) for key, item in value.items()
        )
    return False


def _is_schema_record(value: Any) -> bool:
    """schema 节点必须是普通对象（JSON 落地后即 ``dict``）。"""
    return isinstance(value, dict)


def _scalar_matches(schema_type: str, value: Any) -> bool:
    """标量是否匹配声明的类型。"""
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "number":
        return _is_json_number(value)
    if schema_type == "integer":
        return _is_json_number(value) and (
            isinstance(value, int) or float(value).is_integer()
        )
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "null":
        return value is None
    return False


def _check_object_tail(
    node: dict[str, Any], path: str, properties: Any, violations: list[str]
) -> None:
    """``type: object`` 专有字段的尾部校验（属性子树访问完之后才做）。"""
    has_required = "required" in node
    required = node.get("required") if has_required else None
    if has_required:
        if not isinstance(required, list) or not all(
            isinstance(entry, str) for entry in required
        ):
            violations.append(tr('workflow.validation.36c38830271d', p0=path))
    if isinstance(required, list) and all(isinstance(entry, str) for entry in required):
        declared = properties if _is_schema_record(properties) else {}
        for key in required:
            if key not in declared:
                violations.append(
                    tr('workflow.validation.9a801bfb55fb', p0=path, p1=key)
                )
    if "additionalProperties" in node and not isinstance(
        node["additionalProperties"], bool
    ):
        violations.append(tr('workflow.validation.55625f80bb6d', p0=path))


def _check_schema_node(
    root: Any, root_path: str, violations: list[str], seen: set[int]
) -> None:
    """收集整棵原始 schema 的全部违规，顺序稳定可复现。"""
    tasks: list[tuple[str, Any, str, Any]] = [("enter", root, root_path, None)]
    while tasks:
        kind, node, path, extra = tasks.pop()
        if kind == "leave":
            seen.discard(id(extra))
            continue
        if kind == "one-of-tail":
            for key in ONE_OF_SIBLING_KEYWORDS:
                if key in node:
                    violations.append(tr('workflow.validation.319f59b8940e', p0=path, p1=key))
            continue
        if kind == "object-tail":
            _check_object_tail(node, path, extra, violations)
            continue
        if not _is_schema_record(node):
            violations.append(tr('workflow.validation.9429aabfbde5', p0=path))
            continue
        if id(node) in seen:
            violations.append(tr('workflow.validation.4bf87611b089', p0=path))
            continue
        seen.add(id(node))
        tasks.append(("leave", node, path, node))
        for key in node:
            if key in CONSTRAINT_KEYWORDS:
                continue
            if key in ANNOTATION_KEYWORDS:
                if not _is_json_value(node[key]):
                    violations.append(
                        tr('workflow.validation.0bc909506d53', p0=path, p1=key)
                    )
                continue
            violations.append(
                tr('workflow.validation.c2a5ef0d2fc6', p0=path, p1=key)
            )
        if "description" in node and not isinstance(node["description"], str):
            violations.append(tr('workflow.validation.ed0e15feb909', p0=path))
        if "title" in node and not isinstance(node["title"], str):
            violations.append(tr('workflow.validation.f9e881b36b06', p0=path))
        has_type = "type" in node
        has_one_of = "oneOf" in node
        if has_type and has_one_of:
            violations.append(tr('workflow.validation.045c0ac969f5', p0=path))
            continue
        if not has_type and not has_one_of:
            for key in ONE_OF_SIBLING_KEYWORDS:
                if key in node:
                    violations.append(tr('workflow.validation.63be167d4da0', p0=path, p1=key))
            continue
        if has_one_of:
            one_of = node["oneOf"]
            tasks.append(("one-of-tail", node, path, None))
            if not isinstance(one_of, list) or len(one_of) < 2:
                violations.append(
                    tr('workflow.validation.cda9437bd35e', p0=path)
                )
            else:
                for index in range(len(one_of) - 1, -1, -1):
                    tasks.append(("enter", one_of[index], f"{path}.oneOf[{index}]", None))
            continue
        schema_type = node["type"]
        if not isinstance(schema_type, str) or schema_type not in SCHEMA_TYPES:
            if isinstance(schema_type, list):
                violations.append(
                    tr('workflow.validation.dee44a47ae35', p0=path)
                )
            else:
                violations.append(
                    tr('workflow.validation.222e4d963188', p0=path, p1='/'.join(SCHEMA_TYPES))
                )
            continue
        for key, types in KEYWORD_TYPES.items():
            if key in node and schema_type not in types:
                violations.append(
                    tr('workflow.validation.d8aaf80ab8f4', p0=path, p1=key, p2=schema_type)
                )
        if schema_type == "object":
            properties = node.get("properties")
            tasks.append(("object-tail", node, path, properties))
            if "properties" in node:
                if not _is_schema_record(properties):
                    violations.append(tr('workflow.validation.4fb6dcb76da6', p0=path))
                else:
                    for key in reversed(list(properties)):
                        tasks.append(("enter", properties[key], f"{path}.properties.{key}", None))
        elif schema_type == "array":
            if "items" in node:
                tasks.append(("enter", node["items"], f"{path}.items", None))
        else:
            has_enum = "enum" in node
            allowed = node.get("enum")
            enum_valid = (
                isinstance(allowed, list)
                and len(allowed) > 0
                and all(_scalar_matches(schema_type, entry) for entry in allowed)
            )
            if has_enum and not enum_valid:
                violations.append(
                    tr('workflow.validation.f53c183f7343', p0=path, p1=schema_type)
                )
            has_const = "const" in node
            declared_const = node.get("const")
            const_valid = _scalar_matches(schema_type, declared_const)
            if has_const:
                if not const_valid:
                    violations.append(tr('workflow.validation.e0e4480725f3', p0=path, p1=schema_type))
                elif enum_valid and declared_const not in allowed:
                    violations.append(
                        tr('workflow.validation.d4b134d35cb2', p0=path, p1=path)
                    )


def collect_schema_violations(schema: Any) -> list[str]:
    """返回 schema 相对根约束的全部违规文案（空列表代表通过）。"""
    violations: list[str] = []
    _check_schema_node(schema, "schema", violations, set())
    if not violations and (
        not _is_schema_record(schema) or schema.get("type") != "object"
    ):
        violations.append(
            tr('workflow.validation.de3fe05bbcc5')
        )
    return violations


def check_schema(schema: Any) -> None:
    """校验 schema 属于受支持子集且根为 object，否则抛 :class:`SchemaError`。"""
    violations = collect_schema_violations(schema)
    if violations:
        raise SchemaError(violations)


def _json_equal(left: Any, right: Any) -> bool:
    """JSON 语义相等；布尔与数字互不等价，数字按数值比较。"""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(
            _json_equal(left[key], right[key]) for key in left
        )
    return left == right


def _value_errors(node: Any, value: Any, path: str) -> list[str]:
    """对照受支持子集校验单个值，返回违规文案列表。"""
    if not _is_schema_record(node):
        return [tr('workflow.validation.2892cfd7d4fd', p0=path)]
    if "oneOf" in node:
        branches = node["oneOf"]
        matched = [
            index
            for index, branch in enumerate(branches)
            if not _value_errors(branch, value, path)
        ]
        if len(matched) != 1:
            return [tr('workflow.validation.49c20e154010', p0=path, p1=len(matched))]
        return []
    schema_type = node.get("type")
    errors: list[str] = []
    if schema_type == "object":
        if not isinstance(value, dict):
            return [tr('workflow.validation.e6d38577daac', p0=path)]
        properties = node.get("properties") or {}
        for key in node.get("required") or []:
            if key not in value:
                errors.append(tr('workflow.validation.b6b312d53d91', p0=path, p1=key))
        if node.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    errors.append(tr('workflow.validation.da54f7451ac5', p0=path, p1=key))
        for key, sub in properties.items():
            if key in value:
                errors.extend(_value_errors(sub, value[key], f"{path}.{key}"))
        return errors
    if schema_type == "array":
        if not isinstance(value, list):
            return [tr('workflow.validation.d3443b0fb0ae', p0=path)]
        item_schema = node.get("items")
        if _is_schema_record(item_schema):
            for index, item in enumerate(value):
                errors.extend(_value_errors(item_schema, item, f"{path}[{index}]"))
        return errors
    if not _scalar_matches(str(schema_type), value):
        return [tr('workflow.validation.e1c47461814f', p0=path, p1=schema_type)]
    if "enum" in node and not any(_json_equal(value, entry) for entry in node["enum"]):
        errors.append(tr('workflow.validation.b9f0551a6cd6', p0=path))
    if "const" in node and not _json_equal(value, node["const"]):
        errors.append(tr('workflow.validation.2506115fb114', p0=path))
    return errors


def validate_against_schema(value: Any, schema: Any) -> None:
    """子代理 structured 输出必须过 schema，失败抛 :class:`ToolExecutionError`。"""
    errors = _value_errors(schema, value, "value")
    if errors:
        raise ToolExecutionError(tr('workflow.validation.mismatch', violations=tr('common.error_separator').join(errors)))


def _schema_prompt(schema: Mapping[str, Any]) -> str:
    """把 schema 序列化进 prompt，供无 schema 通道的子代理自证结构。"""
    return json.dumps(schema, ensure_ascii=False, sort_keys=True)


def _render_thrown(error: BaseException) -> str:
    """把任意异常渲染成稳定文案。"""
    message = str(error).strip()
    return f"{type(error).__name__}: {message}" if message else type(error).__name__


def _default_label(prompt: str) -> str:
    """没有显式 label 时，用 prompt 首行做默认展示名。"""
    head = prompt.strip().splitlines()[0] if prompt.strip() else ""
    return head[:60] if head else "agent"


_STOP_BY_STATUS = {
    "completed": "completed",
    "failed": "failed",
    "interrupted": "cancelled",
}


@dataclass
class _Child:
    """一个在跑的子代理及其宿主侧 bookkeeping。"""

    call_id: int
    child_id: str
    state: Any
    label: str = "agent"
    phase: str | None = None
    outcome: str | None = None
    wait_task: asyncio.Task | None = None
    disposal: asyncio.Task | None = None


def _protocol_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """拒绝同一消息内重复字段，不能让后一个值覆盖协议身份。"""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


async def _protocol_lines(reader):
    """按块拆分协议行，支持长结果并限制未终止行的内存占用。"""
    pending = bytearray()
    limit = 16 * 1024 * 1024
    while True:
        chunk = await reader.read(4096)
        if not chunk:
            if pending:
                yield bytes(pending)
            return
        pending.extend(chunk)
        while (end := pending.find(b'\n')) >= 0:
            if end > limit:
                raise ToolExecutionError(tr('runtime.validation.bd0a0024574d'))
            yield bytes(pending[:end])
            del pending[:end + 1]
        if len(pending) > limit:
            raise ToolExecutionError(tr('runtime.validation.bd0a0024574d'))


def _child_call_id(params: Mapping[str, Any]) -> int:
    """成员读取和释放共用当前协议的正整数身份校验。"""
    call_id = params.get('callId')
    if type(call_id) is not int or call_id < 1:
        raise ToolExecutionError(tr('workflow.child.call_id'))
    return call_id


class WorkflowGuest:
    """在子进程里跑 workflow 脚本引擎，并通过 stdio JSON-lines 驱动子代理。

    生命周期：``run()`` 起 Node、驱动脚本、收 final；每个
    ``agent()`` 通过 ``startChild``/``childResult``/``disposeChild`` 映射到一个真实
    的 ``SubagentManager`` 子代理。model 覆盖交给当前管理器，provider 仅支持 spawn。
    """

    def __init__(
        self,
        manager: Any,
        *,
        script: str,
        meta: Mapping[str, Any],
        args: Mapping[str, Any] | None,
        limits: WorkflowLimits,
        cwd: str | None = None,
        on_progress: Callable[[str, Mapping[str, Any]], None] | None = None,
        parent_session_id: str = "workflow",
    ) -> None:
        self._manager = manager
        self._script = script
        self._meta = dict(meta)
        self._args = dict(args) if args is not None else None
        self._limits = limits
        self._cwd = cwd or os.getcwd()
        self._on_progress = on_progress
        self._parent_session_id = parent_session_id

        self._proc: Any = None
        self._started = 0
        self._children: dict[int, _Child] = {}
        self._rpc_tasks: set[asyncio.Task] = set()
        self._schemas: dict[int, Any] = {}
        self._cancelled = False
        self._stderr_tail: list[str] = []

    async def run(self) -> dict[str, Any]:
        """驱动脚本跑完，返回 ``WorkflowResult`` 形状的字典。"""
        from ...core.language import current_language
        from ..process_env import execution_env
        node = resolve_node()
        self._proc = await start_process(lambda: asyncio.create_subprocess_exec(
            node,
            str(GUEST_ENTRY),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
            env=execution_env({'HARNESS_WORKFLOW_LANGUAGE': current_language.get()}),
        ), stop=self._reclaim_process)
        stderr_task = asyncio.create_task(self._drain_stderr())
        try:
            final = await self._read_stdout()
            if final is None:
                return self._error_result(
                    tr('workflow.validation.2f7a420c0a1c')
                )
            if not final["ok"]:
                return self._error_result(
                    tr('workflow.validation.ff3cfca2f641', p0=final['error'])
                )
            return self._interpret_final(final["result"])
        except asyncio.CancelledError:
            self._cancelled = True
            raise
        finally:
            await join_cleanup(asyncio.create_task(self._teardown(stderr_task)))

    async def _reclaim_process(self, proc: Any) -> None:
        """接管所属句柄并有界停止，确认退出才释放传输层。"""
        self._proc = proc
        try:
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (OSError, TimeoutError) as exc:
            raise ProcessCleanupError(
                tr('workflow.process.cleanup_failed'), pid=getattr(proc, 'pid', None),
            ) from exc
        if proc.stdin is not None:
            proc.stdin.close()
        transport = getattr(proc, '_transport', None)
        if transport is not None:
            transport.close()

    async def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        decoder = TextDecoder()
        while True:
            line = await proc.stderr.read(4096)
            text_line = decoder.decode(line, final=not line).rstrip()
            if text_line:
                self._stderr_tail.append(text_line)
                if len(self._stderr_tail) > 40:
                    del self._stderr_tail[0]
            if not line:
                if decoder.error:
                    self._stderr_tail.append(tr('encoding.failure', channel='stderr', encoding=decoder.encoding, offset=decoder.error['offset']))
                return

    async def _read_stdout(self) -> dict[str, Any] | None:
        """逐行读 guest stdout，响应 RPC，直到读到 ``final`` 消息。"""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return None
        last_request_id = 0
        async for line in _protocol_lines(proc.stdout):
            try:
                text_line = line.decode('utf-8').strip()
                if not text_line:
                    continue
                message = json.loads(text_line, object_pairs_hook=_protocol_object)
                if not isinstance(message, dict) or not _is_json_value(message):
                    raise ValueError
                if 'final' in message:
                    if message['final'] is not True or type(message.get('ok')) is not bool:
                        raise ValueError
                    fields = {'final', 'ok', 'result'} if message['ok'] else {'final', 'ok', 'error'}
                    if set(message) != fields or (not message['ok'] and not isinstance(message['error'], str)):
                        raise ValueError
                    return message
                request_id = message.get('id')
                method = message.get('method')
                params = message.get('params')
                if (set(message) != {'id', 'method', 'params'}
                        or type(request_id) is not int or request_id <= last_request_id
                        or not isinstance(method, str) or not method
                        or not isinstance(params, dict)):
                    raise ValueError
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise ToolExecutionError(tr('workflow.protocol.invalid_message'),
                                         code='workflow/invalid-message') from exc
            last_request_id = request_id
            task = asyncio.create_task(self._serve(request_id, method, params))
            self._track(task)

    def _track(self, task: asyncio.Task) -> asyncio.Task:
        """登记一个后台 RPC 任务，收尾时统一 join。"""
        self._rpc_tasks.add(task)
        task.add_done_callback(self._rpc_tasks.discard)
        return task

    async def _serve(self, request_id: int, method: str, params: Mapping[str, Any]) -> None:
        """处理单个 guest RPC 请求并回写响应。"""
        try:
            result = await self._dispatch(method, params)
            self._respond(request_id, ok=True, result=result)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - RPC 错误回传给 guest
            self._respond(request_id, ok=False, error=_render_thrown(error))

    def _respond(
        self, request_id: int, *, ok: bool, result: Any = None, error: str = ""
    ) -> None:
        """把一条 RPC 响应写回 guest stdin。"""
        proc = self._proc
        if proc is None or proc.stdin is None:
            return
        if ok:
            payload: dict[str, Any] = {"id": request_id, "ok": True, "result": result}
        else:
            payload = {"id": request_id, "ok": False, "error": error}
        try:
            line = json.dumps(payload, ensure_ascii=False) + "\n"
            proc.stdin.write(line.encode("utf-8"))
        except (BrokenPipeError, RuntimeError, ValueError):
            pass

    async def _dispatch(self, method: str, params: Mapping[str, Any]) -> Any:
        """把 RPC 方法名路由到具体实现。"""
        if method == "begin":
            return self._rpc_begin()
        if method == "startChild":
            return await self._rpc_start_child(params)
        if method == "childResult":
            return await self._rpc_child_result(params)
        if method == "disposeChild":
            await self._rpc_dispose_child(params)
            return None
        if method == "progress":
            await self._rpc_progress(params)
            return None
        raise ToolExecutionError(tr('runtime.validation.ce58a3f18597', p0=method))

    def _rpc_begin(self) -> dict[str, Any]:
        """把脚本源码与运行上限交给 guest 引擎。"""
        payload: dict[str, Any] = {
            "meta": {"name": self._meta.get("name", "workflow")},
            "body": self._script,
            "limits": self._limits.to_json(),
        }
        if self._args is not None:
            payload["args"] = self._args
        return payload

    async def _rpc_start_child(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """校验一次 agent() 调用，然后真的起一个子代理。"""
        prompt = params.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ToolExecutionError(tr('runtime.validation.212ff01859f7'))
        opts = params
        for key in ("provider", "model"):
            value = opts.get(key)
            if value is not None and not isinstance(value, str):
                raise ToolExecutionError(tr('runtime.validation.a7031865e71f', p0=repr(key)))
        if opts.get('provider', DEFAULT_PROVIDER) != DEFAULT_PROVIDER:
            raise ToolExecutionError(tr('workflow.provider_unsupported', provider=opts.get('provider')))
        if 'model' in opts and (not isinstance(opts['model'], str) or not opts['model'].strip()):
            raise ToolExecutionError(tr('workflow.model_invalid'))
        schema = opts.get("schema")
        if schema is not None:
            check_schema(schema)

        label = opts.get("label")
        if label is not None and (not isinstance(label, str) or not label.strip()):
            raise ToolExecutionError(tr('runtime.validation.2372da6e6aac'))
        phase = opts.get("phase")
        if phase is not None and (not isinstance(phase, str) or not phase.strip()):
            raise ToolExecutionError(tr('runtime.validation.4cc22503770e'))

        self._started += 1
        if self._started > self._limits.max_total_agents:
            raise ToolExecutionError(
                tr('runtime.validation.933ee96c45b1', p0=self._limits.max_total_agents)
            )

        effective_prompt = prompt
        if schema is not None:
            effective_prompt = prompt + '\n\n' + tr('workflow.schema_prompt', schema=_schema_prompt(schema))
        state = self._manager.spawn(
            prompt=effective_prompt,
            description=label or _default_label(prompt),
            parent_session_id=self._parent_session_id,
            **({'model': opts['model']} if 'model' in opts else {}),
        )
        call_id = self._started
        child_id = state.id
        display = label or _default_label(prompt)
        child = _Child(
            call_id=call_id,
            child_id=str(child_id),
            state=state,
            label=display,
            phase=phase,
        )
        self._children[call_id] = child
        if schema is not None:
            self._schemas[call_id] = schema
        child.wait_task = asyncio.create_task(self._await_child(child))
        self._track(child.wait_task)
        return {"callId": call_id, "childId": child.child_id}

    async def _await_child(self, child: _Child) -> None:
        """后台等一个子代理跑完，并广播 agent-end 进度事件。"""
        outcome = "failed"
        try:
            await self._manager.wait(child.child_id, timeout=None)
            outcome = _STOP_BY_STATUS.get(getattr(child.state, "status", ""), "failed")
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception:  # noqa: BLE001 - 子代理异常等价于失败
            outcome = "failed"
        child.outcome = outcome

    async def _rpc_child_result(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """把子代理结果翻译成 guest 的 ChildResult。"""
        child = self._require_child(params)
        if child.wait_task is not None:
            await asyncio.shield(child.wait_task)
        status = getattr(child.state, "status", "failed")
        stop_reason = _STOP_BY_STATUS.get(status, "failed")
        text = child.state.output
        if stop_reason != "completed":
            return {
                "output": [{"type": "text", "text": text}],
                "stopReason": stop_reason,
            }
        schema = self._schemas.get(child.call_id)
        if schema is None:
            return {
                "output": [{"type": "text", "text": text}],
                "stopReason": "completed",
            }
        structured = _extract_structured(text)
        if structured is None:
            return {
                "output": [{"type": "text", "text": text}],
                "stopReason": "completed",
            }
        validate_against_schema(structured, schema)
        return {
            "output": [{"type": "text", "text": text}],
            "structured": structured,
            "stopReason": "completed",
        }

    async def _rpc_dispose_child(self, params: Mapping[str, Any]) -> None:
        """共享成员清理；确认执行与交付收尾结束后才移除登记。"""
        call_id = _child_call_id(params)
        child = self._children.get(call_id)
        if child is None:
            return
        if child.disposal is None:
            child.disposal = asyncio.create_task(self._dispose_child(child))
        task = child.disposal
        try:
            await join_cleanup(task)
        finally:
            if task.done() and (task.cancelled() or task.exception() is not None):
                if child.disposal is task:
                    child.disposal = None

    async def _dispose_child(self, child: _Child) -> None:
        """停止未收敛成员，失败保留句柄以供后续显式清理。"""
        if not getattr(child.state, 'settled', False):
            await self._manager.interrupt(
                child.child_id, reason=tr('workflow.validation.6c63e54db301'),
            )
        if not getattr(child.state, 'settled', False):
            raise ProcessCleanupError(tr('workflow.child.cleanup_incomplete'), child_id=child.child_id)
        if child.wait_task is not None and not child.wait_task.cancelled():
            await asyncio.shield(child.wait_task)
        if self._children.get(child.call_id) is child:
            self._children.pop(child.call_id)
            self._schemas.pop(child.call_id, None)

    def _require_child(self, params: Mapping[str, Any]) -> _Child:
        """按 callId 取回登记过的子代理。"""
        call_id = _child_call_id(params)
        child = self._children.get(call_id)
        if child is None:
            raise ToolExecutionError(tr('runtime.validation.0e570f056bf5', p0=call_id))
        return child

    async def _rpc_progress(self, params: Mapping[str, Any]) -> None:
        """校验并转发 guest 上报的一批进度事件。"""
        events = params.get("events")
        if not isinstance(events, list):
            raise ToolExecutionError(tr('runtime.validation.67a09fac1de1'))
        for event in events:
            self._validate_progress_event(event)
        for event in events:
            self._emit_progress(dict(event))

    def _validate_progress_event(self, event: Any) -> None:
        """闭合校验一条 progress 事件，非法即终止整个 run（对齐 host.ts）。"""
        if not isinstance(event, Mapping):
            raise ToolExecutionError(tr('runtime.validation.36c3c834d0ff'))
        kind = event.get("type")
        if kind == "phase":
            title = event.get("title")
            if not isinstance(title, str) or not title.strip():
                raise ToolExecutionError(tr('runtime.validation.d672f132f0bb'))
            return
        if kind == "log":
            message = event.get("message")
            if not isinstance(message, str) or not message.strip():
                raise ToolExecutionError(tr('runtime.validation.cff16d9a73bc'))
            return
        if kind in {"agent-start", "agent-end"}:
            info = event.get("info")
            if not isinstance(info, Mapping):
                raise ToolExecutionError(tr('runtime.validation.bd15bcae7920', p0=kind))
            seq = info.get("seq")
            label = info.get("label")
            child_id = info.get("childId")
            phase = info.get("phase")
            if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
                raise ToolExecutionError(tr('runtime.validation.bcb22bd80ba2', p0=kind))
            if not isinstance(label, str) or not label.strip():
                raise ToolExecutionError(tr('runtime.validation.c71cdff4d10e', p0=kind))
            if not isinstance(child_id, str) or not child_id.strip():
                raise ToolExecutionError(tr('runtime.validation.ca68ef004e9f', p0=kind))
            if phase is not None and (not isinstance(phase, str) or not phase.strip()):
                raise ToolExecutionError(tr('runtime.validation.c09c5a485a5f', p0=kind))
            if kind == "agent-end":
                outcome = info.get("outcome")
                if outcome not in {"completed", "failed", "cancelled"}:
                    raise ToolExecutionError(
                        tr('runtime.validation.06e1f9d9f212')
                    )
            return
        raise ToolExecutionError(tr('runtime.validation.5136612a263c', p0=repr(kind)))

    def _emit_progress(self, event: dict[str, Any]) -> None:
        """把一条进度事件交给上层回调（没回调就丢弃）。"""
        if self._on_progress is None:
            return
        kind = str(event.get("type", "log"))
        try:
            self._on_progress(kind, event)
        except Exception:  # noqa: BLE001 - 进度渲染失败不该弄死整个 run
            pass

    def _interpret_final(self, result: Any) -> dict[str, Any]:
        """校验并规范化 guest 的 final result。"""
        if not isinstance(result, Mapping):
            return self._error_result(
                tr('workflow.validation.c960f6b76def')
            )
        if "value" not in result:
            return self._error_result(
                tr('workflow.validation.bf6a54ccb347')
            )
        stop_reason = result.get("stopReason")
        if stop_reason not in {"completed", "error", "cancelled"}:
            return self._error_result(
                tr('workflow.validation.a78f6ef71360', p0=repr(stop_reason))
            )
        started = result.get("agentsStarted")
        if not isinstance(started, int) or isinstance(started, bool) or started < 0:
            return self._error_result(
                tr('workflow.validation.141fd405bf51')
            )
        error = result.get("error")
        if error is not None and not isinstance(error, str):
            return self._error_result(
                tr('workflow.validation.89f283a10a2e')
            )
        return {
            "value": result.get("value"),
            "stopReason": stop_reason,
            "agentsStarted": started,
            "error": error,
        }

    def _error_result(self, message: str) -> dict[str, Any]:
        """构造一个 ``stopReason='error'`` 的结果。"""
        return {
            "value": None,
            "stopReason": "error",
            "agentsStarted": self._started,
            "error": message,
        }

    async def _close_process(self) -> None:
        """正常结束先关闭输入，超时或取消则有界停止所属进程。"""
        proc = self._proc
        if proc is None:
            return
        if self._cancelled:
            await self._reclaim_process(proc)
            return
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except (BrokenPipeError, RuntimeError, OSError):
                pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=10.0)
        except TimeoutError:
            await self._reclaim_process(proc)
            return
        except OSError as exc:
            raise ProcessCleanupError(
                tr('workflow.process.cleanup_failed'), pid=getattr(proc, 'pid', None),
            ) from exc
        transport = getattr(proc, '_transport', None)
        if transport is not None:
            transport.close()

    async def _teardown(self, stderr_task: asyncio.Task) -> None:
        """收尾：取消后台任务、回收子代理、关管道、等进程、必要时 kill。"""
        errors: list[BaseException] = []
        child_waits = {child.wait_task for child in self._children.values()}
        pending = [task for task in self._rpc_tasks if task not in child_waits]
        for task in pending:
            task.cancel()
        for task in pending:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except BaseException as exc:
                errors.append(exc)
        for call_id in list(self._children):
            try:
                await self._rpc_dispose_child({'callId': call_id})
            except BaseException as exc:
                errors.append(exc)
        self._rpc_tasks.difference_update(task for task in tuple(self._rpc_tasks) if task.done())
        try:
            await self._close_process()
        except BaseException as exc:
            errors.append(exc)
        if not stderr_task.done():
            stderr_task.cancel()
        try:
            await stderr_task
        except asyncio.CancelledError:
            pass
        except BaseException as exc:
            errors.append(exc)
        raise_cleanup_errors(errors)


def _extract_structured(text: str) -> Any:
    """从子代理自由文本里抠出结构化 JSON，抠不出返回 None。"""
    from .ralph_tools import _extract_json_object

    return _extract_json_object(text)


__all__ = [
    "DEFAULT_MAX_ITEMS_PER_CALL",
    "DEFAULT_MAX_TOTAL_AGENTS",
    "DEFAULT_PROVIDER",
    "MAX_CONCURRENT_AGENTS_CEILING",
    "SchemaError",
    "WorkflowGuest",
    "WorkflowLimits",
    "check_schema",
    "collect_schema_violations",
    "default_max_concurrent_agents",
    "resolve_node",
    "validate_against_schema",
]
