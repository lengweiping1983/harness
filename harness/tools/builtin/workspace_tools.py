"""工作区运行时依赖工具。

让模型在选择工具链前发现工作区入口与服务环境，避免猜测路径。
本实现只做只读探测，不运行工作区脚本或自动安装依赖：

* Python 解释器与 site-packages 目录；
* Node / pnpm / npx / npm 的可执行文件与版本；
* 当前进程 ``sys.path`` 里的模块目录；
* 已安装的 Python distribution（按需限量，避免把几千个包塞进上下文）。
* 调用方绑定的工作区内已存在的依赖目录和清单路径。

本 harness 是独立 Python 进程，因此**探测真实环境**而不是读取宿主注入的
「bundled runtime 路径表」，对模型更有用且不依赖任何宿主假设。
"""

from __future__ import annotations

from harness.i18n import tr

from copy import deepcopy
from bisect import insort

import importlib.metadata as importlib_metadata
import asyncio
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from typing import Any

from ...core.errors import ToolExecutionError
from ...core.cleanup import join_cleanup, raise_cleanup_errors
from ...llm.types import TextBlock
from ..registry import ToolDefinition, ToolRunContext, ToolResult
from ..runtime_probe import probe_version

#: 一次最多枚举多少个已安装 distribution（防止上下文爆炸）
MAX_PACKAGES = 200
#: 探测单个外部命令的超时（秒）
PROBE_TIMEOUT = 5.0
#: 会被探测的 Node 侧命令
NODE_COMMANDS = ("node", "npm", "npx", "pnpm", "yarn", "bun", "deno")
#: 只定位工具链和常用文档、媒体工具，不运行其项目配置或安装命令。
WORKSPACE_COMMANDS = ("git", "rg", "uv", "python", "python3", "go", "cargo", "rustc", "java",
                      "ffmpeg", "ffprobe", "pandoc", "soffice", "pdftoppm", "pdftotext")
DEPENDENCY_ROOTS = {".venv": "python_environment", "venv": "python_environment", "node_modules": "node_modules"}
DEPENDENCY_MANIFESTS = ("pyproject.toml", "requirements.txt", "uv.lock", "Pipfile", "Pipfile.lock",
                        "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock",
                        "deno.json", "deno.jsonc", "Cargo.toml", "go.mod")
#: 探测结果的缓存时长（秒）。Node 版本探测要 spawn 若干子进程，
#: 而运行时装载在一次会话里会被反复调用，因此按 TTL 缓存而不是每次重探。
PROBE_CACHE_TTL = 300.0

#: 缓存按探测输入身份隔离，值为 ``(expires_at, payload)``。
_PROBE_CACHE: dict[str, tuple[float, Any]] = {}
_PROBE_LOCK = threading.Lock()
_PROBE_FLIGHTS: dict[str, threading.Lock] = {}


def _cached(key: str, produce):
    """按 TTL 复用探测结果；``monotonic`` 时钟避免系统时间回拨影响。"""
    with _PROBE_LOCK:
        flight = _PROBE_FLIGHTS.setdefault(key, threading.Lock())
    # 同类探测只运行一次；等待发生在工作线程，不占用聊天事件循环。
    with flight:
        with _PROBE_LOCK:
            hit = _PROBE_CACHE.get(key)
            if hit is not None and hit[0] > time.monotonic():
                return deepcopy(hit[1])
        payload = deepcopy(produce())
        with _PROBE_LOCK:
            _PROBE_CACHE[key] = (time.monotonic() + PROBE_CACHE_TTL, payload)
        return deepcopy(payload)


def _version_of(executable: str) -> str:
    """有界读取可执行文件版本；普通探测失败为空，清理失败仍向上传播。"""
    return asyncio.run(probe_version([executable, '--version'], PROBE_TIMEOUT))


def _probe_node_runtimes() -> list[dict[str, Any]]:
    """有界并行查询独立版本，保持命令顺序并缩短首次探测等待。"""
    candidates: list[tuple[str, str]] = []
    for name in NODE_COMMANDS:
        path = shutil.which(name)
        if not path:
            continue
        candidates.append((name, os.path.abspath(path)))
    if not candidates:
        return []
    with ThreadPoolExecutor(max_workers=min(4, len(candidates)), thread_name_prefix='harness-runtime-version') as pool:
        # 每个并行探测拥有独立上下文；进程清理错误须保留本次调用语言。
        futures = [pool.submit(copy_context().run, _version_of, path) for _, path in candidates]
        versions, errors = [], []
        for future in futures:
            try:
                versions.append(future.result())
            except Exception as exc:
                errors.append(exc)
        # 所有已开始的探测均须收尾，不能只保留第一个线程的清理失败。
        raise_cleanup_errors(errors)
        return [{'command': name, 'path': path, 'version': version}
                for (name, path), version in zip(candidates, versions)]


def _node_runtimes() -> list[dict[str, Any]]:
    """探测 Node 侧运行时（带 TTL 缓存，避免每次调用都 spawn 子进程）。"""
    key = repr(("node", os.environ.get("PATH"), os.environ.get("PATHEXT"), os.getcwd()))
    return _cached(key, _probe_node_runtimes)


def _probe_python_packages() -> tuple[list[dict[str, str]], int]:
    packages: list[dict[str, str]] = []
    limit = MAX_PACKAGES
    total = 0
    for dist in importlib_metadata.distributions():
        name = (dist.metadata["Name"] or "").strip()
        if not name:
            continue
        total += 1
        insort(packages, {"name": name, "version": (dist.version or "").strip()},
               key=lambda item: (item['name'].casefold(), item['version']))
        if len(packages) > limit:
            packages.pop()
    return packages, total


def _python_packages(limit: int) -> tuple[list[dict[str, str]], int]:
    """枚举已安装 distribution（带 TTL 缓存；``limit`` 会被缓存键忽略）。"""
    packages, total = _cached(repr(("packages", sys.path, os.getcwd())), _probe_python_packages)
    if limit == MAX_PACKAGES:
        return packages, total
    return packages[:limit], total


def _workspace_layout(root: Path) -> dict[str, Any]:
    """仅检查固定入口的身份；不跟随链接、不读取配置正文、不推断安装成功。"""
    dependencies, manifests = [], []
    for name in (*DEPENDENCY_ROOTS, *DEPENDENCY_MANIFESTS):
        path = root / name
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        linked = stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0)
                                                   & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))
        entry = {"path": str(path), "linked": linked}
        if name in DEPENDENCY_ROOTS and (linked or stat.S_ISDIR(info.st_mode)):
            dependencies.append({**entry, "kind": DEPENDENCY_ROOTS[name]})
        elif name in DEPENDENCY_MANIFESTS and (linked or stat.S_ISREG(info.st_mode)):
            manifests.append(entry)
    return {"dependency_roots": dependencies, "manifests": manifests}


def collect_workspace_dependencies(*, include_packages: bool = True, cwd: str | os.PathLike | None = None) -> dict[str, Any]:
    """探测当前工作区可用的运行时，返回结构化结果。"""
    if type(include_packages) is not bool:
        raise ToolExecutionError(tr('tool.arguments.boolean', argument='include_packages'))
    try:
        if cwd is not None and (not isinstance(cwd, (str, os.PathLike)) or not os.fspath(cwd)):
            raise ValueError('cwd')
        root = Path(cwd if cwd is not None else os.getcwd()).resolve(strict=True)
        if not root.is_dir():
            raise ValueError('cwd')
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        raise ToolExecutionError(tr('workspace.invalid_cwd')) from exc
    site_dirs: list[str] = []
    for entry in sys.path:
        if not entry:
            entry = os.getcwd()
        try:
            resolved = os.path.abspath(entry)
        except (OSError, ValueError):
            continue
        if resolved not in site_dirs:
            site_dirs.append(resolved)

    data: dict[str, Any] = {
        "python": {
            "source": "harness_process",
            "executable": sys.executable,
            "version": sys.version.split()[0],
            "implementation": sys.implementation.name,
            "prefix": sys.prefix,
            "site_packages_dirs": site_dirs,
        },
        "node": _node_runtimes(),
        "cwd": str(root),
        "process_cwd": os.getcwd(),
        "workspace": _workspace_layout(root),
        "executables": [{"command": name, "path": os.path.abspath(path)}
                        for name in WORKSPACE_COMMANDS if (path := shutil.which(name))],
        "runtime_scope": tr('workspace.runtime_scope'),
    }
    if include_packages:
        packages, total = _python_packages(MAX_PACKAGES)
        data["python"]["packages"] = packages
        data["python"]["package_count"] = total
        data["python"]["packages_truncated"] = total > len(packages)
    return data


def _render(data: dict[str, Any]) -> str:
    python = data["python"]
    lines = [
        tr('python.workspace_tools.d077aa93c3ed'),
        tr('workspace.python_runtime', version=python['version'], implementation=python['implementation']),
        tr('python.workspace_tools.09beb1bb5b69', p0=f"{python['executable']}"),
        tr('python.workspace_tools.2d78850be504', p0=f"{len(python['site_packages_dirs'])}", p1=f"{(python['site_packages_dirs'][0] if python['site_packages_dirs'] else tr('common.empty'))}"),
    ]
    if "package_count" in python:
        suffix = tr('python.workspace_tools.77cde5d25373') if python.get("packages_truncated") else ""
        lines.append(
            tr('python.workspace_tools.b42179f3e4a7', p0=f"{python['package_count']}", p1=f'{suffix}')
        )
    node = data["node"]
    if node:
        lines.append(tr('python.workspace_tools.7597aaddc475'))
        for item in node:
            version = f" {item['version']}" if item["version"] else ""
            lines.append(f"  {item['command']}{version} -> {item['path']}")
    else:
        lines.append(tr('python.workspace_tools.3d66984a18fe'))
    lines.append("")
    lines.append(tr('python.workspace_tools.6dfc0264e2ba'))
    # data 不自动进入模型上下文；完整结构必须随正文交给实际调用者。
    lines.append(json.dumps(data, ensure_ascii=False, indent=2))
    return "\n".join(lines)


def build_workspace_dependency_tools() -> list[ToolDefinition]:
    """构造 ``load_workspace_dependencies``。"""

    async def load_workspace_dependencies(args: Any, ctx: ToolRunContext) -> ToolResult:
        if not isinstance(args, dict):
            raise ToolExecutionError(tr('python.workspace_tools.217393fbc154'))
        include = args.get("include_packages", True)
        if type(include) is not bool:
            raise ToolExecutionError(tr('tool.arguments.boolean', argument='include_packages'))
        try:
            kwargs = {"include_packages": include}
            if ctx is not None and ctx.cwd is not None:
                kwargs["cwd"] = ctx.cwd
            reading = asyncio.create_task(asyncio.to_thread(collect_workspace_dependencies, **kwargs))
            data = await join_cleanup(reading)
        except Exception as exc:  # pragma: no cover - 探测异常罕见
            raise ToolExecutionError(tr('python.workspace_tools.484acd7994ec', p0=f'{exc}')) from exc
        return ToolResult(
            content=[TextBlock(text=_render(data))],
            display=data,
            data=data,
        )

    return [
        ToolDefinition(
            schema={
                "description_key": 'python.workspace_tools.d7179d748869',
                "parameter_keys": {
                    ('properties', 'include_packages', 'description'): 'python.workspace_tools.4005e269d0c1',
                },
                "name": "load_workspace_dependencies",
                "description": (
                    tr('python.workspace_tools.d7179d748869')
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "include_packages": {
                            "type": "boolean",
                            "description": (
                                tr('python.workspace_tools.4005e269d0c1')
                            ),
                        }
                    },
                    "additionalProperties": False,
                },
            },
            execute=load_workspace_dependencies,
            effect="read",
            idempotent=True,
            concurrency_safe=True,
            timeout=30.0,
        )
    ]


__all__ = [
    "build_workspace_dependency_tools",
    "collect_workspace_dependencies",
    "MAX_PACKAGES",
]
