"""前端静态资源服务（SPA + 缓存 + 压缩）。

前端是独立的 Vite 工程，构建产物在 ``web/dist``。这里负责把它挂到
Web 服务根路径上，并做到成熟产品该有的三件事：

* **SPA 回退** —— 任何未命中静态文件、且不带点号的路径都回 ``index.html``，
  前端路由（``/s/<id>`` 之类）刷新才不会 404。
* **分级缓存** —— 带内容哈希的 ``/assets/*`` 用 ``immutable`` 永久缓存；
  ``index.html`` 必须 ``no-cache``，否则发版后用户永远拿旧壳。
* **条件请求** —— ``ETag`` + ``If-None-Match`` → 304，回访几乎零带宽。
"""

from __future__ import annotations

import gzip
import logging
import mimetypes
import os
import stat as file_stat
import threading
from collections import OrderedDict
from pathlib import Path

from ..core.file_handles import check_handle_root

from .responses import (
    GZIP_CONTENT_TYPES,
    GZIP_MIN_BYTES,
    accepts_gzip,
    weak_etag,
)
from .router import Response
from .errors import ServiceUnavailable
from ..i18n import tr

logger = logging.getLogger(__name__)
_DIRECTORY_HANDLES = os.open in os.supports_dir_fd

#: 允许通过环境变量指定构建产物目录（打包分发时用）。
DIST_ENV_VAR = "HARNESS_WEB_DIST"

#: 不可缓存的目录名（源码映射、调试残留）。
NO_CACHE_DIRS = ("source-map",)

#: 内容哈希资源可以永久缓存。
IMMUTABLE_PREFIXES = ("assets/",)

#: 已编码响应体的内存缓存条目上限。
#: 一次全量构建的 dist 约 4~6 MB，200 条上限足够覆盖
#: 首屏 + 首次渲染分片，同时把常驻内存控在安全线内。
ENCODED_CACHE_MAX = 200


def candidate_roots(package_root: Path | None = None) -> list[Path]:
    """按优先级列出可能的前端构建目录。"""
    roots: list[Path] = []
    override = os.environ.get(DIST_ENV_VAR)
    if override:
        roots.append(Path(override).expanduser())
    here = Path(__file__).resolve()
    package_root = package_root or here.parent.parent
    # 开发态：仓库里的 web/dist
    roots.append(package_root.parent / "web" / "dist")
    # 分发态：随包一起安装的 harness/web/dist
    roots.append(here.parent / "dist")
    return roots


def resolve_dist(explicit: str | os.PathLike[str] | None = None) -> Path | None:
    """找到可用的构建目录；找不到返回 ``None``。"""
    if explicit is not None:
        path = Path(explicit).expanduser().resolve()
        return path if (path / "index.html").is_file() else None
    for root in candidate_roots():
        try:
            if (root / "index.html").is_file():
                return root.resolve()
        except OSError:
            continue
    return None


def guess_content_type(path: Path) -> str:
    """推断 MIME 类型，补齐 Windows 上 ``mimetypes`` 常见缺失项。"""
    suffix = path.suffix.lower()
    overrides = {
        ".js": "text/javascript; charset=utf-8",
        ".mjs": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml",
        ".woff": "font/woff",
        ".woff2": "font/woff2",
        ".ttf": "font/ttf",
        ".otf": "font/otf",
        ".webp": "image/webp",
        ".avif": "image/avif",
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".wasm": "application/wasm",
        ".map": "application/json; charset=utf-8",
    }
    if suffix in overrides:
        return overrides[suffix]
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def is_immutable(relative: str) -> bool:
    """带哈希的资源可永久缓存。"""
    return any(relative.startswith(prefix) for prefix in IMMUTABLE_PREFIXES)


class StaticAssets:
    """一个已构建前端目录的只读视图。"""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.index_path = self.root / "index.html"
        # 编码缓存同时绑定路径、设备、文件身份、变化时间、修改时间和大小。
        self._encoded: "OrderedDict[tuple, tuple[bytes, bytes | None]]" = (
            OrderedDict()
        )
        self._lock = threading.Lock()

    # -- 探测 ------------------------------------------------------------
    @classmethod
    def locate(cls, explicit: str | os.PathLike[str] | None = None) -> "StaticAssets | None":
        root = resolve_dist(explicit)
        if root is None:
            logger.info(tr('web.static.build_missing'))
            return None
        return cls(root)

    @property
    def available(self) -> bool:
        return self.index_path.is_file()

    # -- 解析 ------------------------------------------------------------
    def resolve(self, url_path: str) -> Path | None:
        """把 URL 路径解析成磁盘路径，**越界一律拒绝**。

        这是唯一的静态文件入口，必须防 ``../../etc/passwd``：先规范化，
        再确认结果仍在 ``root`` 之内。
        """
        from urllib.parse import unquote

        relative = unquote(url_path).lstrip("/")
        candidate = (self.root / (relative or "index.html")).resolve()
        if candidate.is_dir():
            candidate = (candidate / "index.html").resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def _resource_stat(self, path: Path) -> os.stat_result:
        """支持目录句柄时逐级打开已解析的根内路径，替换链接不能改变来源。"""
        if not _DIRECTORY_HANDLES:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0))
            try:
                check_handle_root(descriptor, self.root)
                info = os.fstat(descriptor)
                if not file_stat.S_ISREG(info.st_mode):
                    raise OSError('resource changed')
                return info
            finally:
                os.close(descriptor)
        parts = path.relative_to(self.root).parts
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        parent = os.open(self.root, directory_flags)
        try:
            for name in parts[:-1]:
                child = os.open(name, directory_flags, dir_fd=parent)
                os.close(parent)
                parent = child
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0), dir_fd=parent)
            try:
                info = os.fstat(descriptor)
                if not file_stat.S_ISREG(info.st_mode):
                    raise OSError('resource changed')
                return info
            finally:
                os.close(descriptor)
        finally:
            os.close(parent)

    # -- 响应 ------------------------------------------------------------
    def serve(
        self,
        url_path: str,
        *,
        if_none_match: str = "",
        accept_encoding: str = "",
        spa_fallback: bool = True,
    ) -> Response | None:
        """服务一个静态资源；找不到时按 SPA 规则回退到 ``index.html``。"""
        path = self.resolve(url_path)
        if path is None:
            if not spa_fallback or not self.available or "." in url_path.rsplit("/", 1)[-1]:
                return None
            path = self.resolve("/index.html")
            if path is None:
                return None

        # 只 stat 不读盘：命中 If-None-Match 时直接 304，
        # 不要为了一个空响应把 640 KB 的 JS 包整个读进内存。
        try:
            stat = self._resource_stat(path)
        except OSError:
            logger.warning(tr('log.0225d7609e89'), path)
            return None

        etag = weak_etag(path, stat.st_mtime_ns, stat.st_size)
        relative = path.relative_to(self.root).as_posix()
        cache_control = (
            "no-cache, must-revalidate"
            if relative == "index.html"
            else "public, max-age=31536000, immutable"
            if is_immutable(relative)
            and not any(part in NO_CACHE_DIRS for part in Path(relative).parts)
            else "public, max-age=3600"
        )

        if if_none_match and etag in {tag.strip() for tag in if_none_match.split(",")}:
            response = Response(status=304, body=b"")
            response.headers["ETag"] = etag
            response.headers["Cache-Control"] = cache_control
            return response

        content_type = guess_content_type(path)
        raw, gzipped = self._body(path, stat)
        if gzipped is not None and accepts_gzip(accept_encoding):
            response = Response(status=200, body=gzipped)
            response.headers["Content-Encoding"] = "gzip"
            response.headers["Vary"] = "Accept-Encoding"
        else:
            response = Response(status=200, body=raw)
        response.headers["Content-Type"] = content_type
        response.headers["ETag"] = etag
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = cache_control
        return response

    def _body(self, path: Path, stat: os.stat_result) -> tuple[bytes, bytes | None]:
        """拿到 (原文, gzip 编码体)，命中缓存就不重复读盘、不重复压缩。

        gzip level=5 对 640 KB 的 JS 大约要十几毫秒 CPU，而这个文件在一次
        会话里会被反复请求（首屏、重连、刷新）。内容哈希资源是不可变的，
        缓存包含文件身份及变化时间，读取同时核对已打开句柄。
        """
        key = (str(path), stat.st_dev, stat.st_ino, stat.st_ctime_ns, stat.st_mtime_ns, stat.st_size)
        with self._lock:
            hit = self._encoded.get(key)
            if hit is not None:
                self._encoded.move_to_end(key)
                return hit[0], hit[1]
        try:
            def identity(info):
                return (info.st_dev, info.st_ino, info.st_ctime_ns, info.st_mtime_ns, info.st_size)
            flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
            descriptor = os.open(path, flags)
            try:
                incoming = os.fdopen(descriptor, 'rb')
            except BaseException:
                os.close(descriptor)
                raise
            with incoming:
                check_handle_root(incoming.fileno(), self.root)
                opened = os.fstat(incoming.fileno())
                if not file_stat.S_ISREG(opened.st_mode) or identity(opened) != identity(stat):
                    raise OSError('resource changed')
                raw = incoming.read(stat.st_size + 1)
                check_handle_root(incoming.fileno(), self.root)
                if identity(os.fstat(incoming.fileno())) != identity(stat) or len(raw) != stat.st_size:
                    raise OSError('resource changed')
        except OSError:
            raise ServiceUnavailable(tr('web.static.unreadable'), code='static/unreadable') from None
        gzipped: bytes | None = None
        if len(raw) >= GZIP_MIN_BYTES and guess_content_type(path).startswith(
            GZIP_CONTENT_TYPES
        ):
            candidate = gzip.compress(raw, compresslevel=5, mtime=0)
            # 压缩后反而更大就不用压缩体。
            gzipped = candidate if len(candidate) < len(raw) else None
        with self._lock:
            self._encoded[key] = (raw, gzipped)
            self._encoded.move_to_end(key)
            while len(self._encoded) > ENCODED_CACHE_MAX:
                self._encoded.popitem(last=False)
        return raw, gzipped

__all__ = [
    "DIST_ENV_VAR",
    "candidate_roots",
    "resolve_dist",
    "guess_content_type",
    "is_immutable",
    "ENCODED_CACHE_MAX",
    "StaticAssets",
]
