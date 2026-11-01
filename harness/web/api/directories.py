"""目录浏览接口：项目选择器要能"逐级下钻"地挑目录。

``/api/v1/workspace`` 是**只读工作区视图**，它刻意不给出工作区之外的任何路径 ——
文件面板绝不能变成任意目录浏览器。这里是另一件事：用户明确在"添加项目"，
需要挑一个盘上任意目录并注册成项目，所以入口独立、只吐**目录名**、
单层、有上限、隐藏点目录。

纪律
----
* 只列子目录，不列文件、不列符号链接目标内容。
* 单层返回，不递归 —— 前端逐级下钻，避免一次 ``iterdir`` 扫穿整棵子树。
* ``hidden`` 以 ``.`` 开头的直接跳过：``.git``、``.venv`` 对人类没有浏览价值。
* 单层上限 :data:`MAX_ENTRIES`，配合 ``truncated`` 标记，前端显示"还有更多"。
"""

from __future__ import annotations

from harness.i18n import tr

from pathlib import Path
import sys
from typing import Any

from ..responses import json_response
from ..router import Router

#: 单层目录返回上限。目录树动辄几千项，一次全吐会让选择器卡住。
MAX_ENTRIES = 400


def _drive_roots() -> list[dict[str, str]]:
    """读取 Windows 已挂载盘符，不逐盘探测，避免离线网络盘拖慢浏览。"""
    if sys.platform != "win32":
        return []
    import ctypes

    mask = ctypes.windll.kernel32.GetLogicalDrives()
    return [
        {"name": f"{chr(65 + index)}:", "path": f"{chr(65 + index)}:\\"}
        for index in range(26) if mask & (1 << index)
    ]


def _resolve_dir(raw: str | None) -> tuple[Path | None, str | None]:
    """把请求里的 ``path`` 解析成一个真实目录。

    返回 ``(目录, 错误码)``。空路径回落到用户主目录 —— 目录选择器第一次打开
    时最合理的落点就是"我的文件"，而不是某个项目内部。
    """
    text = (raw or "").strip()
    if not text:
        return Path.home().resolve(), None
    if '\0' in text:
        return None, "dir/invalid-path"
    try:
        target = Path(text).expanduser().resolve()
    except (OSError, ValueError):
        return None, "dir/invalid-path"
    if not target.exists():
        return None, "dir/not-found"
    if not target.is_dir():
        return None, "dir/not-a-directory"
    return target, None


def build_router(ctx: Any) -> Router:
    router = Router()

    @router.get("/api/v1/directories")
    def list_directories(request: Any) -> Any:
        """列一层子目录。

        ``?path=`` 不传则从用户主目录开始，``::computer`` 只列盘符。
        响应里的 ``parent`` 为 ``null`` 表示已经在文件系统根；Windows 前端
        此时可回到盘符列表，虚拟列表的空路径不能注册为项目。
        """
        roots = _drive_roots()
        if request.get("path") == "::computer":
            return json_response({
                "path": "", "name": "", "parent": None,
                "entries": roots, "roots": roots, "truncated": False,
            })
        target, code = _resolve_dir(request.get("path"))
        if target is None:
            from ..errors import BadRequest

            message = {
                'dir/not-found': 'python.directories.not_found',
                'dir/not-a-directory': 'python.directories.not_directory',
            }.get(code, 'python.directories.invalid_path')
            raise BadRequest(tr(message), code=code or "dir/invalid-path")

        entries: list[dict[str, Any]] = []
        truncated = False
        try:
            children = sorted(
                (child for child in target.iterdir() if not child.name.startswith(".")),
                key=lambda p: p.name.lower(),
            )
        except OSError as exc:
            from ..errors import BadRequest

            raise BadRequest(tr('python.directories.eda6d26e4d96', p0=f'{exc}'), code="dir/unreadable") from exc

        for child in children:
            try:
                if not child.is_dir():
                    continue
            except OSError:
                continue
            entries.append({"name": child.name, "path": str(child)})
            if len(entries) >= MAX_ENTRIES:
                truncated = True
                break

        parent = target.parent
        return json_response(
            {
                "path": str(target),
                "name": target.name or str(target),
                "parent": str(parent) if parent != target else None,
                "entries": entries,
                "truncated": truncated,
                "roots": roots,
            }
        )

    return router


__all__ = ["build_router", "MAX_ENTRIES"]
