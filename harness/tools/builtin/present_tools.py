"""交付物声明工具。

``present`` 让模型把已经写好的文件显式声明为**最终交付物**，UI 据此渲染
可点击的产物卡片。声明通过事件广播，让 Web UI / 后续 CLI 都能消费。
"""

from __future__ import annotations

from harness.i18n import tr

import os
import time
from pathlib import Path
from typing import Any

from ...core.errors import ToolExecutionError
from ...core.events import EventBus
from ...llm.types import TextBlock
from ..registry import ToolDefinition, ToolRunContext, ToolResult

#: 单次最多声明几个文件
MAX_FILES = 8
#: 建议一次只声明 1-2 个（超过 4 个说明该拆成多轮）
RECOMMENDED_FILES = 2
MAX_FILES_STRICT = 4

def _resolve(root: str | None, raw_path: str) -> Path:
    candidate = Path(os.path.expanduser(raw_path.strip()))
    if not candidate.is_absolute():
        base = Path(root) if root else Path.cwd()
        candidate = base / candidate
    return candidate


def build_present_tools(bus: EventBus | None = None, *, cwd: str | None = None) -> list[ToolDefinition]:
    """构造 ``present``。"""

    async def present(args: Any, ctx: ToolRunContext) -> ToolResult:
        raw_files = args.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise ToolExecutionError(tr('python.present_tools.83ad6087f8b2'))
        if len(raw_files) > MAX_FILES:
            raise ToolExecutionError(
                tr('python.present_tools.76849b50f19f', p0=f'{MAX_FILES}', p1=f'{len(raw_files)}')
            )

        root = str(Path(ctx.cwd or cwd or Path.cwd()).expanduser().resolve())
        entries: list[dict[str, Any]] = []
        missing: list[str] = []
        for item in raw_files:
            if not isinstance(item, dict):
                raise ToolExecutionError(tr('python.present_tools.2ec85a2fe35c'))
            raw_path = str(item.get("path", "") or "").strip()
            if not raw_path:
                raise ToolExecutionError(tr('python.present_tools.4cfb1c2e6f85'))
            path = _resolve(root, raw_path)
            try:
                resolved = path.resolve()
            except OSError as exc:
                raise ToolExecutionError(tr('python.present_tools.a176e5be61d8', p0=f'{raw_path}', p1=f'{exc}')) from exc
            if not resolved.is_relative_to(root):
                raise ToolExecutionError(tr('present.outside_workspace', path=str(resolved), root=root),
                                         code='PRESENT_OUTSIDE_WORKSPACE', path=str(resolved), cwd=root)
            if not resolved.is_file():
                missing.append(raw_path)
                continue
            try:
                size = resolved.stat().st_size
            except OSError:
                size = 0
            entries.append(
                {
                    "path": str(resolved),
                    "name": resolved.name,
                    "description": str(item.get("description", "") or "").strip(),
                    "size_bytes": size,
                }
            )

        if missing:
            listed = tr('ui.common.list_separator').join(missing)
            raise ToolExecutionError(
                tr('python.present_tools.ab90c1d303bc', p0=f'{listed}')
            )

        if len(entries) > MAX_FILES_STRICT:
            hint = (
                tr('python.present_tools.d6a82a5a34f1', p0=f'{len(entries)}', p1=f'{RECOMMENDED_FILES}')
            )
        else:
            hint = ""

        payload = {
            "call_id": ctx.call_id,
            "workspace_root": str(Path(root).resolve()) if root else None,
            "turn": ctx.turn,
            "step": ctx.step,
            "session_id": ctx.session_id,
            "files": entries,
            "presented_at": time.time(),
        }
        if bus is not None:
            try:
                await bus.emit("deliverables/presented", payload)
            except Exception:  # pragma: no cover - 事件总线自身异常
                pass

        lines = [tr('python.present_tools.788b4db5f6f7', p0=f'{len(entries)}')]
        for entry in entries:
            desc = tr('python.present_tools.description_suffix', description=entry['description']) if entry['description'] else ''
            lines.append(tr('python.present_tools.54c3f7a8fead', p0=f"{entry['name']}", p1=f"{entry['size_bytes']}", p2=f'{desc}').rstrip())
            lines.append(f"  {entry['path']}")
        if hint:
            lines.append("")
            lines.append(hint)
        return ToolResult(content=[TextBlock(text="\n".join(lines))], display=payload, data=payload)

    return [
        ToolDefinition(
            schema={
                "description_key": 'python.present_tools.2cfa899caa53',
                "description_values": {'p0': RECOMMENDED_FILES, 'p1': MAX_FILES},
                "parameter_keys": {
                    ('properties', 'files', 'description'): 'python.present_tools.1e5e1be2fa35',
                    ('properties', 'files', 'items', 'properties', 'path', 'description'): 'python.present_tools.073d22bd758f',
                    ('properties', 'files', 'items', 'properties', 'description', 'description'): 'python.present_tools.97775356e5f3',
                },
                "name": "present",
                "description": tr('python.present_tools.2cfa899caa53', p0=RECOMMENDED_FILES, p1=MAX_FILES),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "files": {
                            "type": "array",
                            "description": tr('python.present_tools.1e5e1be2fa35'),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {
                                        "type": "string",
                                        "description": tr('python.present_tools.073d22bd758f'),
                                    },
                                    "description": {
                                        "type": "string",
                                        "description": tr('python.present_tools.97775356e5f3'),
                                    },
                                },
                                "required": ["path"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["files"],
                    "additionalProperties": False,
                },
            },
            execute=present,
            concurrency_safe=True,
            timeout=30.0,
        )
    ]


__all__ = ["build_present_tools", "MAX_FILES", "MAX_FILES_STRICT"]
