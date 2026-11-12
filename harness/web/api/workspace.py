"""工作区只读文件接口：让 Markdown 里的图片 / 视频 / 音频能真正显示出来。

模型回复里经常出现 ``![图](out/chart.png)`` 或 ``<video>`` 这类相对路径引用。
浏览器拿不到本地磁盘文件，所以需要一个受控的读取通道。这里按安全优先设计：

* **路径必须落在工作区内** —— 解析后若逃出 ``cwd``，一律 403；
* **只读、只列** —— 没有写入接口，避免 UI 变成任意文件写入面；
* **支持 Range** —— 视频/音频拖动进度条依赖 206 响应；
* **大小与类型白名单** —— 超过阈值直接拒，不给"下载整个磁盘"的机会。
"""

from __future__ import annotations

from harness.i18n import tr

import os
import mimetypes
import ipaddress
from pathlib import Path
from typing import Any

from ..errors import BadRequest, NotFound
from ..responses import bytes_response, json_response, content_disposition
from ..router import Router
from ..desktop_files import open_desktop_file

#: 单文件最大读取量（256 MiB），超过直接拒。
MAX_FILE_BYTES = 256 * 1024 * 1024

#: 允许按媒体类型直接内联预览的前缀。
INLINE_TYPES = ("image/", "video/", "audio/")

#: 内联后仍要关进沙箱的类型：脚本可用，但页面拿不到同源身份。
SANDBOXED_TYPES = ("text/html",)

#: 沙箱策略：允许脚本，否则交互页面全部失效；不给同源身份，因此页面无法调用本机接口。
SANDBOX_POLICY = "sandbox allow-scripts"

#: 站点预览路由里代表"工作区根"的目录名——路由需要一个真实片段，空片段会被路由丢掉。
ROOT_MARK = "__root__"

#: 目录列举时最多返回多少项。
MAX_LIST_ENTRIES = 500


def _safe_join(root: Path, raw: str) -> Path:
    """把用户给的相对路径解析成 ``root`` 内的绝对路径，逃逸即拒。

    ``root`` 必须是**已 resolve** 的路径：``candidate`` 一定经过 ``resolve()``，
    若 root 保留原始形态（Windows 8.3 短路径、符号链接），``relative_to`` 会
    因两者字面量不同而误判，把工作区内的合法子目录当成逃逸拒掉。
    """
    # Request.query 已经由 parse_qs 解码。再次 unquote 会把字面量 %20/%2F
    # 文件名改成另一条路径，甚至读到同目录里另一份文件。
    candidate_text = (raw or "").lstrip("/")
    if not candidate_text:
        raise BadRequest(tr('python.workspace.ab4d242c7092'), code="file/missing-path")
    candidate = (root / candidate_text).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise NotFound(tr('python.workspace.ddcf4d2cd77c'), code="file/forbidden", status=403) from exc
    return candidate


def _resolved_root(ctx: Any, requested: str | None = None) -> Path:
    """取工作区根并归一化，供 ``_safe_join`` 与条目相对路径统一使用。

    ``requested`` 让文件浏览跟随侧栏选中的项目（多项目工作区下的必需能力）。
    文件入口只接受默认根或已登记项目，避免页面误用其他目录。
    本机管理调用方可显式登记任意目录；这不是对本机进程的文件系统沙箱。
    """
    default = ctx.workspace_root().resolve()
    raw = (requested or "").strip()
    if not raw:
        return default
    try:
        candidate = Path(raw).expanduser().resolve()
    except OSError as exc:
        raise BadRequest(tr('python.workspace.64c73c3dfef5', p0=f'{raw}'), code="workspace/bad-root") from exc
    if candidate == default:
        return candidate
    try:
        record = ctx.workspaces().get(str(candidate))
    except Exception:
        record = None
    if record is None:
        raise NotFound(
            tr('python.workspace.4cade3b1c8a3', p0=f'{raw}'), code="workspace/unknown", status=403
        )
    return candidate


def _content_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _parse_range(header: str, size: int) -> tuple[int, int] | None:
    """解析单段 ``Range: bytes=start-end``，返回闭区间。"""
    if not header.lower().startswith("bytes="):
        return None
    spec = header.split("=", 1)[1].split(",")[0].strip()
    start_text, _, end_text = spec.partition("-")
    try:
        if not start_text:
            if not end_text:
                return None
            length = int(end_text)
            if length <= 0:
                return None
            start = max(0, size - length)
            end = size - 1
        else:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1
    except ValueError:
        return None
    if start >= size or start > end:
        return None
    return start, min(end, size - 1)


def _directory_page(root: Path, target: Path, offset: int, query: str) -> tuple[list[dict[str, Any]], int | None]:
    """目录优先稳定排序，只读取本页元数据；过滤覆盖整个目录。"""
    children = []
    needle = query.casefold()
    try:
        with os.scandir(target) as scan:
            for child in scan:
                if child.name.startswith(".") or needle not in child.name.casefold():
                    continue
                try:
                    directory = child.is_dir()
                except OSError:
                    continue
                children.append((not directory, child.name.casefold(), child.name, child))
    except OSError:
        return [], None
    children.sort(key=lambda item: item[:3])
    entries = []
    cursor = min(offset, len(children))
    while cursor < len(children) and (len(entries) < MAX_LIST_ENTRIES or len(children) - cursor < 5):
        file_type, _, _, child = children[cursor]
        cursor += 1
        try:
            stat = child.stat()
        except OSError:
            continue
        entries.append({
            "name": child.name,
            "path": (target / child.name).relative_to(root).as_posix(),
            "type": "file" if file_type else "directory",
            "size": stat.st_size,
            "modified": int(stat.st_mtime * 1000),
        })
    return entries, cursor if cursor < len(children) else None


def build_router(ctx: Any) -> Router:
    router = Router()

    @router.post('/api/v1/file/open')
    def open_file(request: Any) -> Any:
        """显式本地界面操作，仅允许已注册工作区中的路径。"""
        try:
            local = ipaddress.ip_address(request.client_ip or '').is_loopback
        except ValueError:
            local = False
        if not local:
            raise BadRequest(tr('python.workspace.5cb58df80aba'), code='file/local-only', status=403)
        payload = request.json()
        raw, requested, action = payload.get('path'), payload.get('root'), payload.get('action', 'default')
        if not isinstance(raw, str) or not raw or '\x00' in raw or (requested is not None and (not isinstance(requested, str) or '\x00' in requested)):
            raise BadRequest(tr('python.workspace.675baa6e6a73'), code='file/invalid-path')
        if not isinstance(action, str) or action not in {'default', 'terminal', 'folder'}:
            raise BadRequest(tr('python.workspace.a4b8a179dcf3'), code='file/invalid-action')
        path = _safe_join(_resolved_root(ctx, requested), raw)
        if not path.is_file():
            raise NotFound(tr('python.workspace.9258823564ec'), code='file/not-found')
        open_desktop_file(path, action)
        return json_response({'requested': True, 'action': action})

    @router.get("/api/v1/workspace")
    def workspace(request: Any) -> Any:
        """工作区信息 + 指定目录的条目列表。

        ``?path=`` 指定要列的子目录（相对工作区根）；不传就是根目录。
        前端文件面板靠它做目录导航，路径仍然走 :func:`_safe_join` 兜住逃逸。
        """
        root = _resolved_root(ctx, request.get("root"))
        raw = request.get("path") or ""
        if raw.strip():
            target = _safe_join(root, raw)
        else:
            target = root

        raw_offset = request.get("offset") or "0"
        try:
            offset = int(raw_offset)
            if offset < 0:
                raise ValueError
        except ValueError as exc:
            raise BadRequest(tr('python.workspace.invalid_offset'), code="workspace/invalid-offset") from exc
        query = (request.get("q") or "").strip()
        if len(query) > 256:
            raise BadRequest(tr('python.workspace.long_query'), code="workspace/long-query")
        entries, next_offset = _directory_page(root, target, offset, query)

        rel_dir = ""
        if target != root:
            try:
                rel_dir = target.relative_to(root).as_posix()
            except ValueError:  # pragma: no cover - _safe_join 已保证不逃逸
                rel_dir = ""

        return json_response(
            {
                "root": str(root),
                "path": rel_dir,
                # 面包屑：前端渲染"根 / a / b"，点哪一级都能跳回去。
                "parents": rel_dir.split("/") if rel_dir else [],
                "parent": rel_dir.rsplit("/", 1)[0] if "/" in rel_dir else "",
                "exists": target.is_dir(),
                "is_dir": target.is_dir(),
                "truncated": next_offset is not None,
                "next_offset": next_offset,
                "query": query,
                "entries": entries,
            }
        )

    @router.get("/api/v1/file")
    def read_file(request: Any) -> Any:
        """读取工作区内的一个文件（媒体预览 / 文档预览）。"""
        root = _resolved_root(ctx, request.get("root"))
        path = _safe_join(root, request.get("path") or "")
        if not path.exists():
            raise NotFound(tr('python.workspace.cef4dd39fa00', p0=f"{request.get('path')}"), code="file/not-found")
        if path.is_dir():
            raise BadRequest(tr('python.workspace.f68cd022e6e1'), code="file/is-directory")
        if not path.is_file():
            raise BadRequest(tr('python.workspace.9258823564ec'), code="file/not-regular")

        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise BadRequest(
                tr('python.workspace.d0adf150b018', p0=f'{size}'), code="file/too-large"
            )

        content_type = _content_type(path)
        previewable = content_type.startswith(INLINE_TYPES) or content_type == "application/pdf"
        sandboxed = content_type.startswith(SANDBOXED_TYPES)
        disposition = "inline" if (previewable or sandboxed) and request.get("download") != "1" else "attachment"
        common = {
            "Content-Type": content_type,
            "Accept-Ranges": "bytes",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=60",
            "Content-Disposition": content_disposition(path.name, disposition),
        }
        # 网页内联后仍然同源，靠 CSP 把它降级成沙箱文档：脚本能跑，但拿不到本机接口。
        if sandboxed and disposition == "inline":
            common["Content-Security-Policy"] = SANDBOX_POLICY

        range_header = request.header("Range")
        span = _parse_range(range_header, size) if range_header else None
        try:
            if span is None:
                body = path.read_bytes()
            else:
                start, end = span
                with path.open("rb") as handle:
                    handle.seek(start)
                    body = handle.read(end - start + 1)
        except OSError as exc:
            raise NotFound(tr('python.workspace.3343e64d27be', p0=f'{exc}'), code="file/unreadable") from exc

        if span is not None:
            start, end = span
            response = bytes_response(body, status=206, headers=common)
            response.headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            return response
        response = bytes_response(body, status=200, headers=common)
        response.headers["Content-Length"] = str(len(body))
        return response

    @router.get("/api/v1/preview/{entry}")
    @router.get("/api/v1/preview/{folder}/{entry}")
    def preview_page(request: Any, entry: str, folder: str = ROOT_MARK) -> Any:
        """按站点目录结构提供预览，让页面里的相对路径仍然指向工作区里的真实文件。

        ``/api/v1/file?path=`` 的基址是接口本身，页面里的 ``styles.css`` 会被解析成
        ``/api/v1/styles.css`` 而不是站点目录里的同名文件；多文件站点因此必须走这条
        路径形态的路由，由目录片段还原相对关系。
        """
        root = _resolved_root(ctx, request.get("root"))
        directory = "" if (folder or ROOT_MARK) == ROOT_MARK else folder
        path = _safe_join(root, f"{directory}/{entry}" if directory else entry)
        if not path.exists():
            raise NotFound(tr('python.workspace.cef4dd39fa00', p0=f"{directory}/{entry}"), code="file/not-found")
        if path.is_dir():
            raise BadRequest(tr('python.workspace.f68cd022e6e1'), code="file/is-directory")
        if not path.is_file():
            raise BadRequest(tr('python.workspace.9258823564ec'), code="file/not-regular")
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise BadRequest(tr('python.workspace.d0adf150b018', p0=f'{size}'), code="file/too-large")
        content_type = _content_type(path)
        common = {
            "Content-Type": content_type,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=60",
            "Content-Disposition": content_disposition(path.name, "inline"),
        }
        if content_type.startswith(SANDBOXED_TYPES):
            common["Content-Security-Policy"] = SANDBOX_POLICY
        return bytes_response(path.read_bytes(), status=200, headers=common)

    return router


__all__ = ["build_router", "MAX_FILE_BYTES"]
