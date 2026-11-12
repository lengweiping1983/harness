"""内置 LSP 工具：跳转定义 / 查引用 / 悬浮信息 / 符号列表。

设计要点：

* **优雅降级是硬要求**：本机没装 language server 是常态，绝不能抛栈打断对话，
  而是返回"装什么、怎么装"的可执行提示，模型看到后可以改用 grep/read 兜底。
* 纯标准库实现 JSON-RPC over stdio（``Content-Length`` 分帧），处理协议消息。
* 每次调用起一个短命 server 进程：LSP 的 ``didOpen`` 状态本来就要靠
  ``textDocument/definition`` 之类请求建立，长驻 server 的复杂度远大于收益。
* 单次请求的发送、通知及反向请求共享固定超时，不因服务器持续输出而续期；
  不支持的反向请求显式返回错误，查询超时后回收进程并提示文本检索兜底。
"""

from __future__ import annotations

from harness.i18n import tr
from ..process_env import execution_env

import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from ...core.cleanup import join_cleanup
from ...core.errors import ToolExecutionError
from ..processes import ProcessCleanupError, start_process
from ...llm.types import TextBlock
from ..registry import ToolDefinition, ToolRunContext
from .fs_text import decode_utf8_prefix, text_lines

#: 单次 LSP 请求超时
DEFAULT_TIMEOUT = 30.0
#: 回传给模型的文本上限
MAX_RESULT_CHARS = 20000
#: workspace/symbol 结果条数上限
MAX_SYMBOLS = 200
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_PREVIEW_BYTES = 256 * 1024

#: 语言 -> 候选语言服务器（按顺序探测，第一个可用的胜出）
_LANGUAGE_SERVERS: dict[str, list[str]] = {
    "python": ["pyright-langserver", "pylsp", "python-lsp-server"],
    "typescript": ["typescript-language-server"],
    "javascript": ["typescript-language-server"],
    "tsx": ["typescript-language-server"],
    "go": ["gopls"],
    "rust": ["rust-analyzer"],
    "java": ["jdtls"],
    "csharp": ["omnisharp", "csharp-ls"],
    "php": ["intelephense"],
    "ruby": ["solargraph"],
    "lua": ["lua-language-server"],
    "yaml": ["yaml-language-server"],
    "sh": ["bash-language-server"],
    "bash": ["bash-language-server"],
}

#: 语言 -> 默认文件后缀（未指定 path 时用）
_DEFAULT_SUFFIX: dict[str, str] = {
    "python": ".py",
    "typescript": ".ts",
    "javascript": ".js",
    "tsx": ".tsx",
    "go": ".go",
    "rust": ".rs",
    "java": ".java",
    "csharp": ".cs",
    "php": ".php",
    "ruby": ".rb",
    "lua": ".lua",
    "yaml": ".yaml",
    "sh": ".sh",
    "bash": ".sh",
}

#: 语言 id（需要时换成 LSP 标准 languageId）
_LANGUAGE_IDS: dict[str, str] = {
    "python": "python",
    "typescript": "typescript",
    "javascript": "javascript",
    "tsx": "typescriptreact",
    "go": "go",
    "rust": "rust",
    "java": "java",
    "csharp": "csharp",
    "php": "php",
    "ruby": "ruby",
    "lua": "lua",
    "yaml": "yaml",
    "sh": "shellscript",
    "bash": "shellscript",
}

#: 各 server 的启动参数：有的要 ``--stdio``，有的直接吃 stdio
_SERVER_ARGS: dict[str, list[str]] = {
    "pyright-langserver": ["--stdio"],
    "pylsp": [],
    "python-lsp-server": [],
    "typescript-language-server": ["--stdio"],
    "gopls": ["serve"],
    "rust-analyzer": [],
    "jdtls": [],
    "omnisharp": ["-lsp"],
    "csharp-ls": [],
    "intelephense": ["--stdio"],
    "solargraph": ["stdio"],
    "lua-language-server": ["-E", "-e", "lua.lsp.main", "--"],
    "yaml-language-server": ["--stdio"],
    "bash-language-server": ["start"],
}

#: 安装提示（降级时返回给模型）
_INSTALL_HINTS: dict[str, str] = {
    "python": "pip install pyright-langserver",
    "typescript": "npm install -g typescript-language-server typescript",
    "javascript": "npm install -g typescript-language-server typescript",
    "tsx": "npm install -g typescript-language-server typescript",
    "go": "go install golang.org/x/tools/gopls@latest",
    "rust": "rustup component add rust-analyzer",
    "php": "npm install -g intelephense",
    "ruby": "gem install solargraph",
    "yaml": "npm install -g yaml-language-server",
    "sh": "npm install -g bash-language-server",
    "bash": "npm install -g bash-language-server",
}
_INSTALL_HINT_KEYS = {'java': 'python.lsp_tools.1977f5a8fdd1', 'csharp': 'python.lsp_tools.f61ecfa2b39e', 'lua': 'python.lsp_tools.24c1f998819e'}


#: 文件后缀 -> 语言键。必须与 ``_LANGUAGE_SERVERS`` 的键保持一致，
#: 否则 ``a.rs`` 会被推断成 "rs" 并误报"不支持该语言"。
_SUFFIX_LANGUAGES: dict[str, str] = {
    "py": "python",
    "pyi": "python",
    "ts": "typescript",
    "mts": "typescript",
    "cts": "typescript",
    "js": "javascript",
    "mjs": "javascript",
    "cjs": "javascript",
    "jsx": "javascript",
    "tsx": "tsx",
    "go": "go",
    "rs": "rust",
    "java": "java",
    "cs": "csharp",
    "php": "php",
    "rb": "ruby",
    "lua": "lua",
    "yaml": "yaml",
    "yml": "yaml",
    "sh": "sh",
    "bash": "bash",
}


def _normalize_language(raw: str | None, path: Path | None) -> str:
    """从显式参数或文件后缀推断语言键。"""
    key = str(raw or "").strip().lower()
    if key:
        return _SUFFIX_LANGUAGES.get(key, key)
    if path is not None:
        suffix = path.suffix.lower().lstrip(".")
        if suffix:
            return _SUFFIX_LANGUAGES.get(suffix, suffix)
    return "python"


def find_server(language: str) -> tuple[str | None, list[str]]:
    """返回 (可执行文件路径, 启动参数)；都没装就返回 (None, [])。"""
    for candidate in _LANGUAGE_SERVERS.get(language, []):
        found = shutil.which(candidate)
        if found:
            return found, list(_SERVER_ARGS.get(candidate, []))
    return None, []


def missing_server_message(language: str) -> str:
    """给模型的降级说明：要装什么、装不上怎么办。"""
    candidates = _LANGUAGE_SERVERS.get(language)
    if not candidates:
        supported = tr('ui.common.list_separator').join(sorted(_LANGUAGE_SERVERS))
        return (
            tr('python.lsp_tools.85ca5d28fa45', p0=f'{language!r}', p1=f'{supported}')
        )
    hint = tr(_INSTALL_HINT_KEYS[language]) if language in _INSTALL_HINT_KEYS else _INSTALL_HINTS.get(language, '')
    tried = tr('ui.common.list_separator').join(candidates)
    message = tr('python.lsp_tools.8cef2529677b', p0=f'{language}', p1=f'{tried}')
    if hint:
        message += tr('python.lsp_tools.65d45328e2fa', p0=f'{hint}')
    message += tr('python.lsp_tools.cbf84250abae')
    return message


def path_to_uri(path: Path) -> str:
    """本地路径 -> ``file://`` URI（Windows 盘符要写成 /C:/...）。"""
    return path.resolve().as_uri()


def uri_to_path(uri: str) -> Path:
    parsed = urlparse(uri)
    raw = unquote(parsed.path)
    if parsed.netloc and parsed.netloc.lower() != "localhost":
        raw = "//" + parsed.netloc + raw
    elif os.name == "nt" and re.match(r"^/[A-Za-z]:", raw):
        raw = raw[1:]
    return Path(raw)


class _RpcError(RuntimeError):
    """JSON-RPC 层错误。"""


class LspClient:
    """一次性的 JSON-RPC over stdio 客户端。"""

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self._proc = proc
        self._next_id = 0
        self._buffer = b""
        self._close_task: asyncio.Task | None = None

    @classmethod
    async def start(
        cls, command: list[str], cwd: str
    ) -> "LspClient":
        env = execution_env()
        async def stop(proc):
            await cls(proc).close()

        try:
            proc = await start_process(lambda: asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            ), stop=stop)
        except OSError as exc:
            raise ToolExecutionError(tr('python.lsp_tools.6a994d0b2353', p0=f'{exc}')) from exc
        return cls(proc)

    async def close(self) -> None:
        """关闭共用所属任务，重复取消不能中止已接受的回收。"""
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        task = self._close_task
        try:
            await join_cleanup(task)
        except (OSError, RuntimeError) as exc:
            raise ProcessCleanupError(tr('python.lsp_tools.c6db052cc65b'), pid=self._proc.pid) from exc
        finally:
            if task.done() and (task.cancelled() or task.exception() is not None):
                if self._close_task is task:
                    self._close_task = None

    async def _close(self) -> None:
        """确认退出后释放传输层；失败保留进程以供显式重试。"""
        try:
            if self._proc.stdin is not None and not self._proc.stdin.is_closing():
                self._proc.stdin.close()
        except (OSError, RuntimeError):
            pass
        try:
            if self._proc.returncode is None:
                self._proc.terminate()
            # 也要排空待处理的标准输出；满管道可能使进程终止后
            # proc.wait() 仍无法完成。LSP 查询超时或取消时，
            # 不能遗留未等待收尾的已终止进程。
            async with asyncio.timeout(3.0):
                await self._proc.communicate()
        except (asyncio.TimeoutError, ProcessLookupError, OSError):
            try:
                if self._proc.returncode is None:
                    self._proc.kill()
            except ProcessLookupError:
                pass
            try:
                async with asyncio.timeout(3.0):
                    await self._proc.communicate()
            except (asyncio.TimeoutError, OSError) as exc:
                raise ProcessCleanupError(tr('python.lsp_tools.c6db052cc65b'), pid=self._proc.pid) from exc

        transport = getattr(self._proc, '_transport', None)
        if transport is not None:
            transport.close()

    async def _send(self, payload: dict[str, Any]) -> None:
        assert self._proc.stdin is not None
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        try:
            self._proc.stdin.write(header + body)
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            raise _RpcError(tr('python.lsp_tools.3e5d090ac62b')) from exc

    async def _read_message(self) -> dict[str, Any]:
        assert self._proc.stdout is not None
        # 1) 读 header，直到空行
        while b"\r\n\r\n" not in self._buffer:
            if len(self._buffer) > 65536:
                raise _RpcError(tr('lsp.frame_too_large'))
            chunk = await self._proc.stdout.read(4096)
            if not chunk:
                raise _RpcError(tr('python.lsp_tools.6dea0584cb8a'))
            self._buffer += chunk
        header, _, rest = self._buffer.partition(b"\r\n\r\n")
        if len(header) > 65536:
            raise _RpcError(tr('lsp.frame_too_large'))
        lengths = [line.split(b":", 1)[1].strip() for line in header.split(b"\r\n")
                   if line.lower().startswith(b"content-length:")]
        if len(lengths) != 1 or not re.fullmatch(rb"[0-9]{1,10}", lengths[0]):
            raise _RpcError(tr('python.lsp_tools.0a2e394d11bf'))
        length = int(lengths[0])
        if length > 32 * 1024 * 1024:
            raise _RpcError(tr('lsp.frame_too_large'))
        if length <= 0:
            self._buffer = rest
            raise _RpcError(tr('python.lsp_tools.0a2e394d11bf'))
        # 2) 读 body
        while len(rest) < length:
            chunk = await self._proc.stdout.read(65536)
            if not chunk:
                raise _RpcError(tr('python.lsp_tools.64cf2df56778'))
            rest += chunk
        body, self._buffer = rest[:length], rest[length:]
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _RpcError(tr('python.lsp_tools.c1acf168d8a4', p0=f'{exc}')) from exc

    async def request(self, method: str, params: Any, timeout: float) -> Any:
        self._next_id += 1
        request_id = self._next_id
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        try:
            # 发送、回调和通知共享同一截止时间；繁忙的服务端
            # 不能通过反复发送进度来延长等待。
            async with asyncio.timeout_at(deadline):
                await self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
                message_count = 0
                while True:
                    # 缓冲读取不一定让出执行权，需要显式检查，
                    # 避免通知洪泛阻碍超时回调。
                    if loop.time() >= deadline:
                        raise asyncio.TimeoutError
                    if message_count and message_count % 64 == 0:
                        await asyncio.sleep(0)  # 缓冲数据密集到达时，也让取消和其他工具有机会运行。
                    message_count += 1
                    message = await self._read_message()
                    if not isinstance(message, dict):
                        raise _RpcError(tr('python.lsp_tools.2ea88da8f9c6'))
                    if "method" in message:
                        if "id" in message:
                            # 此最小客户端不声明支持服务端发起的方法。
                            # 应回复以免对方一直等待，
                            # 对方请求标识与本客户端请求标识相互独立。
                            await self._send({"jsonrpc": "2.0", "id": message["id"], "error": {
                                "code": -32601, "message": tr('feedback.456ffbdf9049'),
                            }})
                        continue
                    if type(message.get("id")) is not int:
                        raise _RpcError(tr('python.lsp_tools.2ea88da8f9c6'))
                    if message["id"] != request_id:
                        continue
                    if ("result" in message) == ("error" in message):
                        raise _RpcError(tr('python.lsp_tools.78a74dc80a35'))
                    if "error" in message:
                        error = message["error"]
                        if not isinstance(error, dict):
                            raise _RpcError(tr('python.lsp_tools.0fe34bad4fc1'))
                        raise ToolExecutionError(
                            tr('python.lsp_tools.5640e7b93814', p0=f'{method}', p1=f"{error.get('message', tr('common.unknown_error'))}", p2=f"{error.get('code')}"), method=method,
                        )
                    return message["result"]
        except asyncio.TimeoutError as exc:
            raise ToolExecutionError(
                tr('python.lsp_tools.acebf74db1c9', p0=f'{method}', p1=f'{timeout}'),
                method=method,
            ) from exc

    async def notify(self, method: str, params: Any) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})


def _flatten_locations(result: Any) -> list[tuple[str, int, str]]:
    """把 definition/references 结果压平成 [(相对路径, 行号, 预览行)]。"""
    rows: list[tuple[str, int, str]] = []
    items = result if isinstance(result, list) else [result]
    for item in items:
        if not isinstance(item, dict):
            continue
        uri = item.get("uri") or item.get("targetUri")
        if not isinstance(uri, str) or not uri:
            # 没有 uri 的条目不是位置，跳过；否则会凭空造出一行 "(未知文件)"
            continue
        rng = item.get("range") or item.get("targetRange") or {}
        start = (rng.get("start") or {}) if isinstance(rng, dict) else {}
        line = int(start.get("line", 0)) + 1
        rows.append((str(uri_to_path(uri)), line, ""))
    return rows


def _read_lsp_source(target: Path) -> str:
    """启动服务器前有界读取原文，拒绝特殊文件和损坏编码。"""
    if not target.is_file():
        raise ToolExecutionError(tr('lsp.source.regular_file', path=str(target)), path=str(target))
    try:
        with target.open('rb') as source:
            data = source.read(MAX_SOURCE_BYTES + 1)
    except OSError as exc:
        raise ToolExecutionError(tr('lsp.source.read_failed', path=str(target), reason=str(exc)),
                                 path=str(target)) from exc
    if len(data) > MAX_SOURCE_BYTES:
        raise ToolExecutionError(tr('lsp.source.too_large', path=str(target), limit=MAX_SOURCE_BYTES),
                                 path=str(target), max_bytes=MAX_SOURCE_BYTES)
    try:
        return decode_utf8_prefix(data, truncated=False)
    except UnicodeError as exc:
        raise ToolExecutionError(tr('lsp.source.invalid_utf8', path=str(target)), path=str(target)) from exc


def _read_preview(path_text: str, line_no: int) -> str:
    """可选预览只读取有界前缀，不改写或猜测损坏原文。"""
    try:
        target = Path(path_text)
        if not target.is_file():
            return ''
        with target.open('rb') as source:
            data = source.read(MAX_PREVIEW_BYTES + 1)
        lines = text_lines(decode_utf8_prefix(data[:MAX_PREVIEW_BYTES], truncated=len(data) > MAX_PREVIEW_BYTES))
    except (OSError, ValueError):
        return ''
    if 1 <= line_no <= len(lines):
        return lines[line_no - 1].strip()[:200]
    return ''


def _format_symbols(result: Any, limit: int = MAX_SYMBOLS) -> str:
    """把 documentSymbol / workspaceSymbol 结果渲染成可读列表。"""
    lines: list[str] = []

    def walk(items: Any, depth: int = 0) -> None:
        if not isinstance(items, list) or len(lines) >= limit:
            return
        for item in items:
            if not isinstance(item, dict) or len(lines) >= limit:
                continue
            name = item.get("name", "?")
            kind = tr(_SYMBOL_KIND_KEYS.get(item.get("kind"), 'python.lsp_tools.e892654a499d'))
            line_no = ((item.get("location") or {}).get("range") or {}).get("start", {}).get("line")
            if line_no is None:
                line_no = (item.get("selectionRange") or item.get("range") or {}).get(
                    "start", {}
                ).get("line")
            if isinstance(line_no, int):
                line_no += 1
            else:
                line_no = 0
            indent = "  " * depth
            suffix = tr('python.lsp_tools.cdf990636fc9', p0=f'{line_no}') if line_no else ""
            lines.append(f"{indent}{kind} {name}{suffix}")
            walk(item.get("children"), depth + 1)

    if isinstance(result, dict):
        walk(result.get("symbols"))
    else:
        walk(result)
    if not lines:
        return tr('python.lsp_tools.08b6e4fa15d1')
    return "\n".join(lines)


_SYMBOL_KIND_KEYS = {
    1: 'python.lsp_tools.39932f24fe11', 2: 'python.lsp_tools.b07e5088eafa', 3: 'python.lsp_tools.db772f3ff724', 4: 'python.lsp_tools.26b9c4d2448e', 5: 'python.lsp_tools.4fb249b9d7ac', 6: 'python.lsp_tools.22b9f0b66212', 7: 'python.lsp_tools.86de52d17820',
    8: 'python.lsp_tools.49ecd0e342d6', 9: 'python.lsp_tools.ce8f0c9fcdef', 10: 'python.lsp_tools.74bb506bbf61', 11: 'python.lsp_tools.c80d519245a7', 12: 'python.lsp_tools.1832990660bb', 13: 'python.lsp_tools.a772fa4ebe36',
    14: 'python.lsp_tools.7dc406c92803', 15: 'python.lsp_tools.6aaf0b1804f9', 16: 'python.lsp_tools.c16ec9274e9d', 17: 'python.lsp_tools.8e607148f445', 18: 'python.lsp_tools.685bb17fdb2b', 19: 'python.lsp_tools.53f92c063963',
    20: 'python.lsp_tools.721b00cbfa23', 21: 'python.lsp_tools.2f8267a89ad5', 22: 'python.lsp_tools.eb257be37911', 23: 'python.lsp_tools.eaace0a971df', 24: 'python.lsp_tools.c560201b331c', 25: 'python.lsp_tools.6a4b1a3683a3',
    26: 'python.lsp_tools.52561ba4a937',
}


def _format_hover(result: Any) -> str:
    if not isinstance(result, dict):
        return tr('python.lsp_tools.9e8d160bd519')
    contents = result.get("contents")
    parts: list[str] = []
    if isinstance(contents, str):
        parts.append(contents)
    elif isinstance(contents, dict):
        parts.append(str(contents.get("value", "")))
    elif isinstance(contents, list):
        for item in contents:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("value", "")))
    text = "\n".join(part for part in parts if part).strip()
    return text or tr('python.lsp_tools.9e8d160bd519')


def build_lsp_tools(cwd: str | None, *, timeout: float = DEFAULT_TIMEOUT) -> list[ToolDefinition]:
    """构造 lsp 工具。"""

    async def _query(args: Any, ctx: ToolRunContext) -> dict[str, Any]:
        """统一的 LSP 查询入口。"""
        action = str(args.get("action", "definition")).strip().lower()
        valid_actions = {"definition", "references", "hover", "symbols", "workspace_symbol"}
        if action not in valid_actions:
            raise ToolExecutionError(
                tr('python.lsp_tools.216efd73bc06', p0=f'{action!r}', p1=f"{tr('ui.common.list_separator').join(sorted(valid_actions))}"),
                action=action,
            )

        workdir = Path(ctx.cwd or cwd or Path.cwd())
        query_text = str(args.get("query", "") or "").strip()

        target: Path | None = None
        line = int(args.get("line", 0) or 0)
        character = int(args.get("character", 0) or 0)

        raw_path = str(args.get("path", "") or "").strip()
        if raw_path:
            from .fs_tools import _not_found_error, resolve_path

            target = resolve_path(workdir, raw_path)
            if not target.exists():
                raise _not_found_error(target, str(workdir))

        language = _normalize_language(args.get("language"), target)
        server, server_args = find_server(language)
        if server is None:
            # 优雅降级：不是崩溃，是一条模型能据此改路的说明
            return {
                "action": action,
                "available": False,
                "language": language,
                "message": missing_server_message(language),
            }

        # workspace_symbol 不需要具体文件位置
        if action == "workspace_symbol":
            if not query_text:
                raise ToolExecutionError(tr('python.lsp_tools.8de1d828a31c'))
            root_uri = path_to_uri(workdir)
        else:
            if target is None:
                suffix = _DEFAULT_SUFFIX.get(language, "")
                raise ToolExecutionError(
                    tr('python.lsp_tools.51148907fbd0', p0=f'{action}', p1=f'{suffix}', p2=f'{language}')
                )
            if target.is_dir():
                raise ToolExecutionError(tr('python.lsp_tools.c2254dddf4ea', p0=f'{target}'))
            if action in {"definition", "references", "hover"} and line <= 0:
                raise ToolExecutionError(
                    tr('python.lsp_tools.f7491a0613b9', p0=f'{action}', p1=f'{line}')
                )
            root_uri = path_to_uri(workdir)

        source_text = _read_lsp_source(target) if target is not None else None
        client = await LspClient.start([server, *server_args], str(workdir))
        try:
            await client.request(
                "initialize",
                {
                    "processId": os.getpid(),
                    "rootUri": root_uri,
                    "rootPath": str(workdir),
                    "workspaceFolders": [{"uri": path_to_uri(workdir), "name": workdir.name}],
                    "capabilities": {
                        "textDocument": {
                            "definition": {"linkSupport": True},
                            "references": {},
                            "hover": {"contentFormat": ["plaintext", "markdown"]},
                            "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                        },
                        "workspace": {"symbol": {}},
                    },
                },
                timeout,
            )
            await client.notify("initialized", {})

            root = workdir
            if target is not None:
                await client.notify(
                    'textDocument/didOpen',
                    {'textDocument': {'uri': path_to_uri(target),
                                      'languageId': _LANGUAGE_IDS.get(language, language),
                                      'version': 1, 'text': source_text}},
                )

            position = {"line": max(line - 1, 0), "character": max(character, 0)}

            if action == "definition":
                result = await client.request(
                    "textDocument/definition",
                    {"textDocument": {"uri": path_to_uri(target)}, "position": position},
                    timeout,
                )
                rows = _flatten_locations(result)
                if not rows:
                    summary = tr('python.lsp_tools.a886fe0d0833')
                else:
                    lines_out = [tr('python.lsp_tools.8309b4a0ae04', p0=f'{len(rows)}')]
                    for path_text, line_no, _ in rows[:50]:
                        preview = _read_preview(path_text, line_no)
                        rel = _rel(path_text, root)
                        lines_out.append(f"- {rel}:{line_no}" + (f"  {preview}" if preview else ""))
                    summary = "\n".join(lines_out)
                payload: Any = summary

            elif action == "references":
                result = await client.request(
                    "textDocument/references",
                    {
                        "textDocument": {"uri": path_to_uri(target)},
                        "position": position,
                        "context": {"includeDeclaration": True},
                    },
                    timeout,
                )
                rows = _flatten_locations(result)
                if not rows:
                    payload = tr('python.lsp_tools.d1d976de3eb6')
                else:
                    lines_out = [tr('python.lsp_tools.fe39937ed042', p0=f'{len(rows)}')]
                    for path_text, line_no, _ in rows[:MAX_SYMBOLS]:
                        preview = _read_preview(path_text, line_no)
                        rel = _rel(path_text, root)
                        lines_out.append(f"- {rel}:{line_no}" + (f"  {preview}" if preview else ""))
                    payload = "\n".join(lines_out)

            elif action == "hover":
                result = await client.request(
                    "textDocument/hover",
                    {"textDocument": {"uri": path_to_uri(target)}, "position": position},
                    timeout,
                )
                payload = _format_hover(result)

            elif action == "symbols":
                result = await client.request(
                    "textDocument/documentSymbol",
                    {"textDocument": {"uri": path_to_uri(target)}},
                    timeout,
                )
                payload = _format_symbols(result)

            else:  # 工作区符号查询：workspace_symbol
                result = await client.request("workspace/symbol", {"query": query_text}, timeout)
                payload = _format_symbols(result)

            return {
                "action": action,
                "available": True,
                "language": language,
                "server": server,
                "path": str(target) if target else None,
                "line": line or None,
                "text": payload,
            }
        except _RpcError as exc:
            raise ToolExecutionError(
                tr('python.lsp_tools.f4922b500688', p0=f'{exc}'), action=action
            ) from exc
        finally:
            await client.close()

    def lsp(args: Any, ctx: ToolRunContext) -> Any:
        return _query(args, ctx)

    def lsp_to_content(args: Any, value: Any) -> list[TextBlock]:
        if not value.get("available"):
            return [TextBlock(text=value["message"])]
        header_bits = [f"LSP {value['action']}", tr('python.lsp_tools.7edf9553e536', p0=f"{value['language']}")]
        if value.get("path"):
            header_bits.append(str(value["path"]))
        if value.get("line"):
            header_bits.append(tr('python.lsp_tools.11117b4f7e5a', p0=f"{value['line']}"))
        text = tr('lsp.result_header', header=tr('ui.common.list_separator').join(header_bits), text=str(value.get('text', '')))
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + tr('python.lsp_tools.ec7772ad86f6')
        return [TextBlock(text=text)]

    def lsp_display(args: Any, value: Any) -> dict[str, Any]:
        return {
            "action": value.get("action"),
            "language": value.get("language"),
            "available": value.get("available"),
        }

    return [
        ToolDefinition(
            schema={
                "description_key": 'python.lsp_tools.efdc5874831b',
                "parameter_keys": {
                    ('properties', 'action', 'description'): 'python.lsp_tools.b0ccb599351c',
                    ('properties', 'path', 'description'): 'python.lsp_tools.af8e2c5a2bbc',
                    ('properties', 'line', 'description'): 'python.lsp_tools.22666d1d2d21',
                    ('properties', 'character', 'description'): 'python.lsp_tools.3e2d6ed349a7',
                    ('properties', 'query', 'description'): 'python.lsp_tools.830066bc535e',
                    ('properties', 'language', 'description'): 'python.lsp_tools.9c76b1e14e5c',
                },
                "name": "lsp",
                "description": (
                    tr('python.lsp_tools.efdc5874831b')
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["definition", "references", "hover", "symbols", "workspace_symbol"],
                            "description": tr('python.lsp_tools.b0ccb599351c'),
                        },
                        "path": {
                            "type": "string",
                            "description": tr('python.lsp_tools.af8e2c5a2bbc'),
                        },
                        "line": {
                            "type": "integer",
                            "description": tr('python.lsp_tools.22666d1d2d21'),
                        },
                        "character": {
                            "type": "integer",
                            "description": tr('python.lsp_tools.3e2d6ed349a7'),
                        },
                        "query": {
                            "type": "string",
                            "description": tr('python.lsp_tools.830066bc535e'),
                        },
                        "language": {
                            "type": "string",
                            "description": tr('python.lsp_tools.9c76b1e14e5c'),
                        },
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
            execute=lsp,
            to_content=lsp_to_content,
            describe=lsp_display,
            timeout=timeout * 3 + 10.0,
        )
    ]


def _rel(path_text: str, root: Path) -> str:
    """尽量以相对路径展示，找不到就返回原路径。"""
    try:
        return str(Path(path_text).resolve().relative_to(root.resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return path_text


__all__ = ["build_lsp_tools", "find_server", "missing_server_message", "path_to_uri", "uri_to_path"]
