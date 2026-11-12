"""内置 Web 工具：web_fetch / web_search。

这是"强编程能力"的检索面：
模型查官方文档、API 参考、报错信息时不再靠训练记忆猜。

HTTP 请求使用 ``urllib``；搜索服务可能需要额外配置。

安全设计：
* 只允许 http/https，禁止 file:// 等本地协议（防 SSRF 到内网文件）；
* 拒绝内网地址（localhost / 私有网段），避免把内网服务暴露给模型；
* 响应有大小上限，超出截断并明确告知；
* HTML 会做粗提取（去脚本/样式/标签），把正文交给模型而不是原始标签。
"""

from __future__ import annotations

from harness.i18n import tr

import asyncio
import codecs
from contextlib import aclosing
import html
import http.client
import ipaddress
import json
import math
import re
import socket
import time
import urllib.parse
import urllib.request
from typing import Any, Callable

from ...core.errors import LlmError, ToolExecutionError
from ..arguments import string_argument
from ...core.language import current_language
from ...llm.transport import UrllibTransport
from ...net import ipv4_preferred_get
from ...net.http import controlled_urlopen
from ...llm.types import TextBlock
from ..registry import ToolDefinition, ToolRunContext

#: 默认超时（秒）
DEFAULT_TIMEOUT = 30.0
#: 单个响应最大读取字节数
MAX_BYTES = 5 * 1024 * 1024
#: 回传给模型的正文上限
MAX_CONTENT_CHARS = 40000
#: 默认 User-Agent
USER_AGENT = "Harness/1.0 (+https://github.com/harness)"

#: 允许的协议
ALLOWED_SCHEMES = {"http", "https"}

#: 搜索引擎名称 -> 结果页模板。DuckDuckGo 的 HTML 搜索结果 可能被防火墙拦截，
#: 因此默认先试 DuckDuckGo，失败时自动回落 Bing。
SEARCH_ENGINES: dict[str, str] = {
    "duckduckgo": "https://html.duckduckgo.com/html/?q={query}&kl={region}",
    "bing": "https://{bing_host}/search?q={query}&setlang={bing_language}&ensearch={english}",
}
# 请求协商与搜索参数属于第三方协议值，不使用产品文案翻译。
_SEARCH_LANGUAGE_SETTINGS = {
    'en': {'accept_language': 'en', 'region': 'us-en', 'bing_host': 'www.bing.com', 'bing_language': 'en', 'english': '1'},
    'zh-CN': {'accept_language': 'zh-CN,zh;q=0.9,en;q=0.8', 'region': 'cn-zh', 'bing_host': 'cn.bing.com', 'bing_language': 'zh-hans', 'english': '0'},
}


def _search_url(name: str, query: str = '') -> str:
    """每次调用按实际语言构造搜索地址，原查询仅作URL编码。"""
    return SEARCH_ENGINES[name].format(query=urllib.parse.quote(query), **_SEARCH_LANGUAGE_SETTINGS[current_language.get()])


#: 默认尝试的引擎顺序。
SEARCH_ENGINE_ORDER: tuple[str, ...] = ("duckduckgo", "bing")

#: 引擎健康记忆：host -> 下次直接试探的最早时间。
#: 部分网络中 DuckDuckGo 不可达，冷静期避免持续重复探测。
#: 记下一次失败后，在它的“冷静期”内直接跳过。
_ENGINE_UNREACHABLE_UNTIL: dict[str, float] = {}
#: 引擎失败后的冷静期（秒）。
ENGINE_COOLDOWN_SECONDS = 900.0


def _engine_is_usable(name: str, now: float) -> bool:
    """返回该引擎目前是否尝试一下的（冷静期内直接跳过）。"""
    until = _ENGINE_UNREACHABLE_UNTIL.get(urllib.parse.urlsplit(_search_url(name)).hostname)
    return until is None or now >= until


def _mark_engine_failed(name: str, now: float) -> None:
    """记下引擎不可达，进入冷静期。"""
    _ENGINE_UNREACHABLE_UNTIL[urllib.parse.urlsplit(_search_url(name)).hostname] = now + ENGINE_COOLDOWN_SECONDS


def _mark_engine_ok(name: str) -> None:
    """引擎恢复正常时清掉冷静期。"""
    _ENGINE_UNREACHABLE_UNTIL.pop(urllib.parse.urlsplit(_search_url(name)).hostname, None)


async def _probe_engine(name: str, timeout: float) -> bool:
    """非阻塞 TCP 检查，截止时间涵盖 DNS 与连接建立。"""
    parsed = urllib.parse.urlsplit(_search_url(name))
    writer = None
    try:
        async with asyncio.timeout(timeout):
            _, writer = await asyncio.open_connection(parsed.hostname,
                parsed.port or (443 if parsed.scheme == 'https' else 80), family=socket.AF_INET)
        return True
    except (OSError, TimeoutError):
        return False
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style|noscript|template)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_BLOCK_TAG_RE = re.compile(
    r"</?(p|div|section|article|br|hr|li|ul|ol|tr|td|th|h[1-6]|pre|blockquote|table)[^>]*>",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\f\v]+")
_BLANK_RE = re.compile(r"\n{3,}")


def _decode_bytes(raw: bytes, content_type: str, *, truncated: bool = False) -> tuple[str, str]:
    """按响应明确声明的 charset 解码；未声明时严格使用 UTF-8。"""
    match = re.search(r"charset\s*=\s*[\"']?([\w\-]+)", content_type or "", re.IGNORECASE)
    encoding = match.group(1) if match else "utf-8"
    return codecs.getincrementaldecoder(encoding)('strict').decode(raw, final=not truncated), encoding


def _is_blocked_address(ip: ipaddress._BaseAddress) -> bool:
    """拒绝本地、组播和保留地址，隧道地址先核对嵌入的 IPv4。"""
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
        return True
    if isinstance(ip, ipaddress.IPv6Address):
        # Teredo / 6to4 不是内网，但要确认它带的 IPv4 嵌套地址也不是内网。
        mapped = ip.ipv4_mapped or ip.sixtofour
        if mapped is not None:
            return _is_blocked_address(mapped)
        # Teredo 同时携带服务器和客户端身份，两端均不能指向受限地址。
        if ip.teredo is not None:
            return any(_is_blocked_address(address) for address in ip.teredo)
        return ip.is_private or ip.is_reserved or ip.is_site_local
    if ip.is_private:
        return True
    # IPv4：100.64/10 是 CGNAT，也属于不应被模型访问的范围。
    return ip in ipaddress.ip_network("100.64.0.0/10")


def _check_url(url: str) -> urllib.parse.ParseResult:
    """校验 URL：协议白名单 + 内网地址拦截。"""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ToolExecutionError(
            tr('python.web_tools.be0ff6f8ca0f', p0=f'{parsed.scheme!r}'), url=url
        )
    host = parsed.hostname
    if not host:
        raise ToolExecutionError(tr('python.web_tools.22b235381633', p0=f'{url}'), url=url)
    if host.lower() in {"localhost", "localhost.localdomain"} or host.lower().endswith(".local"):
        raise ToolExecutionError(tr('python.web_tools.4933ba61b296', p0=f'{host}'), url=url, host=host)
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        # DNS 解析失败不在这里报错，交给实际请求去暴露更准确的错误
        return parsed
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if _is_blocked_address(ip):
            raise ToolExecutionError(
                tr('python.web_tools.b8e05a0e2f2b', p0=f'{host}', p1=f'{address}'), url=url, host=host, address=address
            )
    return parsed


def html_to_text(source: str) -> str:
    """把 HTML 粗提取成纯文本（保留段落换行，去掉脚本与样式）。"""
    text = _COMMENT_RE.sub("", source)
    text = _SCRIPT_STYLE_RE.sub("\n", text)
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANK_RE.sub("\n\n", text).strip()


def _truncate(text: str, limit: int = MAX_CONTENT_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + tr('python.web_tools.f81fa4230fa2', p0=f'{len(text)}'), True


async def _http_request(opener, url: str, *, timeout: float, max_bytes: int) -> dict[str, Any]:
    """限制整个响应大小，超时或取消时关闭套接字。"""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ToolExecutionError(tr('python.web_tools.9f0e3b9cdc96'), url=url)
    max_bytes = max(1, min(MAX_BYTES, max_bytes))
    metadata = {}
    def observed(response):
        length = response.headers.get('Content-Length', '')
        metadata.update(content_type=response.headers.get('Content-Type', '') or '',
                        final_url=getattr(response, 'url', None) or url, status=response.status,
                        reason=getattr(response, 'reason', ''),
                        content_length=int(length) if length.isdigit() else None)
    transport = UrllibTransport(max_body_bytes=max_bytes + 4096)
    raw = bytearray()
    truncated = False
    try:
        async with asyncio.timeout(timeout), aclosing(transport._stream_response(
                opener, timeout=timeout, on_response=observed, raise_for_status=False)) as stream:
            async for chunk in stream:
                raw.extend(chunk[:max_bytes - len(raw)])
                if len(raw) >= max_bytes:
                    truncated = metadata['content_length'] != len(raw)
                    break
    except TimeoutError as exc:
        raise ToolExecutionError(tr('python.web_tools.45340f3d13fb'), url=url, timeout=timeout, failure_kind='timeout',
                                 recovery='use_web_search_or_another_source') from exc
    except (LlmError, OSError, http.client.HTTPException) as exc:
        raise ToolExecutionError(tr('python.web_tools.22f904f68ff1', p0=f'{exc}'), url=url) from exc
    text, _ = _decode_bytes(raw, metadata['content_type'], truncated=truncated)
    status = metadata['status']
    return {'text': text, **metadata, 'truncated': truncated,
            'error': f"HTTP {status} {metadata['reason']}" if status < 200 or status >= 300 else None}


async def _http_get(url: str, *, timeout: float, max_bytes: int, accept: str) -> dict[str, Any]:
    def opened():
        _check_url(url)
        return ipv4_preferred_get(url, headers={'User-Agent': USER_AGENT, 'Accept': accept,
            'Accept-Language': _SEARCH_LANGUAGE_SETTINGS[current_language.get()]['accept_language']}, timeout=timeout, method='GET', url_validator=_check_url)
    return await _http_request(opened, url, timeout=timeout, max_bytes=max_bytes)


def build_web_tools(*, timeout: float = DEFAULT_TIMEOUT,
                    provider_settings: Callable[[], dict[str, tuple[bool, str]]] | None = None) -> list[ToolDefinition]:
    """构造 web_fetch / web_search 两个工具。"""
    web_timeout = min(timeout, DEFAULT_TIMEOUT)

    async def provider_request(request, request_timeout: float) -> dict:
        """请求提供方接口；只保留状态错误，不把鉴权或原始错误正文回传。"""
        response = await _http_request(lambda: controlled_urlopen(request, timeout=request_timeout),
                                       request.full_url, timeout=request_timeout, max_bytes=MAX_BYTES)
        if response['error'] or response['truncated']:
            raise ToolExecutionError(response['error'] or tr('web.deepseek.invalidResponse'))
        try:
            value = json.loads(response['text'])
        except ValueError:
            raise ToolExecutionError(tr('web.deepseek.invalidResponse')) from None
        if not isinstance(value, dict):
            raise ToolExecutionError(tr('web.deepseek.invalidResponse'))
        return value

    async def deepseek_request(operation: str, payload: dict, request_timeout: float, key: str) -> dict:
        if not key or any(not 33 <= ord(char) <= 126 for char in key):
            raise ToolExecutionError(tr('web.deepseek.missingKey'))
        request = urllib.request.Request(f"https://api.deepseek.com/v1/tools/{operation}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="POST")
        return await provider_request(request, request_timeout)

    async def deepseek_search(args, ctx, key):
        query, limit = args['query'].strip(), args['max_results']
        if not query or len(query) > 128 or not 1 <= limit <= 50:
            raise ToolExecutionError(tr('web.deepseek.invalidSearch'))
        value = await deepseek_request('search', {'query': query, 'count': limit, 'need_content': False}, args['timeout'], key)
        items = value.get('results')
        if not isinstance(items, list) or any(not isinstance(item, dict) or
                any(not isinstance(item.get(field), str) for field in ('title', 'url', 'summary')) for item in items):
            raise ToolExecutionError(tr('web.deepseek.invalidResponse'))
        results = [{'title': item['title'], 'url': item['url'], 'snippet': item['summary']} for item in items[:limit]]
        return {'query': query, 'engine': 'deepseek', 'count': len(results), 'results': results}

    async def brave_search(args, ctx, key):
        query, limit = args['query'].strip(), args['max_results']
        if not key or any(not 33 <= ord(char) <= 126 for char in key):
            raise ToolExecutionError(tr('web.brave.missingKey'))
        if not query or len(query) > 600 or len(query.split()) > 75 or limit < 1:
            raise ToolExecutionError(tr('web.brave.invalidSearch'))
        params = urllib.parse.urlencode({'q': query, 'count': min(limit, 20),
            'result_filter': 'web', 'text_decorations': 'false'})
        request = urllib.request.Request(f'https://api.search.brave.com/res/v1/web/search?{params}',
            headers={'X-Subscription-Token': key, 'Accept': 'application/json'}, method='GET')
        value = await provider_request(request, args['timeout'])
        web = value.get('web')
        if web is None and value.get('type') == 'search':
            items = []
        elif isinstance(web, dict):
            items = web.get('results')
        else:
            items = None
        if not isinstance(items, list) or any(not isinstance(item, dict) or
                any(not isinstance(item.get(field), str) for field in ('title', 'url')) or
                item.get('description') is not None and not isinstance(item['description'], str) for item in items):
            raise ToolExecutionError(tr('web.deepseek.invalidResponse'))
        results = [{'title': html_to_text(item['title']), 'url': item['url'],
                    'snippet': html_to_text(item.get('description') or '')} for item in items[:limit]]
        return {'query': query, 'engine': 'brave', 'count': len(results), 'results': results}

    async def deepseek_fetch(args, ctx, key):
        url = args['url'].strip()
        await asyncio.to_thread(_check_url, url)
        parsed = urllib.parse.urlsplit(url)
        if parsed.username is not None or parsed.password is not None or len(url) > 8192:
            raise ToolExecutionError(tr('web.deepseek.invalidUrl'))
        value = await deepseek_request('fetch', {'url': url}, args['timeout'], key)
        if not isinstance(value.get('content'), str) or not isinstance(value.get('title', ''), str):
            raise ToolExecutionError(tr('web.deepseek.invalidResponse'))
        body, truncated = _truncate(value['content'], args['max_length'])
        return {'url': url, 'final_url': url, 'status': 200, 'content_type': 'text/markdown',
                'title': value.get('title', ''), 'chars': len(body), 'truncated': truncated, 'content': body}

    async def default_fetch(args: Any, ctx: ToolRunContext) -> Any:
        """抓取一个 URL 并返回正文。"""
        url: str = args['url'].strip()
        limit = args['max_length']
        raw = args.get("raw", False)
        fetch_timeout = float(args.get("timeout", web_timeout))
        max_bytes = args['max_bytes']

        response = await _http_get(url, timeout=fetch_timeout, max_bytes=max_bytes, accept="*/*")
        content_type: str = response["content_type"] or ""
        source: str = response["text"]
        # JSON 直接原样返回，模型要的就是原始结构
        if "json" in content_type.lower() and not raw:
            body = source
        elif raw:
            body = source
        else:
            body = html_to_text(source)

        body, truncated = _truncate(body, limit)
        result = {
            "url": url,
            "final_url": response["final_url"],
            "status": response["status"],
            "content_type": content_type,
            "chars": len(body),
            "truncated": truncated or response.get('truncated', False),
            "content": body,
        }
        if response["error"]:
            result["error"] = True
        return result

    def web_fetch_to_content(args: Any, value: Any) -> list[TextBlock]:
        key = 'web.fetch.summary.truncated' if value['truncated'] else 'web.fetch.summary.complete'
        return [TextBlock(text=tr(key, url=value['final_url'], status=value['status'],
                                  mime=value['content_type'] or tr('common.unknown_type'),
                                  count=value['chars'], content=value['content']))]

    def web_fetch_display(args: Any, value: Any) -> dict[str, Any]:
        return {
            "url": value["final_url"],
            "status": value["status"],
            "chars": value["chars"],
            "truncated": value["truncated"],
        }

    async def default_search(args: Any, ctx: ToolRunContext) -> Any:
        """搜索网页。"""
        query: str = args['query'].strip()
        limit = args['max_results']
        fetch_timeout = float(args.get("timeout", web_timeout))
        if not math.isfinite(fetch_timeout) or fetch_timeout <= 0:
            raise ToolExecutionError(tr('python.web_tools.9f0e3b9cdc96'))
        deadline = time.monotonic() + fetch_timeout
        engine = str(args.get("engine") or "auto").lower()

        supported = set(SEARCH_ENGINES) | {"openrouter", "auto"}
        if engine not in supported:
            raise ToolExecutionError(
                tr('web.search.unsupported', engine=repr(engine),
                   choices=tr('ui.common.list_separator').join(sorted(supported))),
                engine=engine,
            )
        if engine == "openrouter":
            return await _openrouter_search(query, limit, fetch_timeout)

        # 显式指定引擎时不回落：用户要就那个引擎的结果。
        # auto（默认）则按顺序尝试，因为鸭鸠在某些网络上整体不可达。
        plan = [engine] if engine != "auto" else list(SEARCH_ENGINE_ORDER)

        errors: list[str] = []
        now = time.monotonic()
        # 每个探针共享自身的截止时间，支持并发和取消。
        # 显式请求绕过健康状态缓存，自动选择遵守其冷却时间。
        if engine == "auto":
            reachable = []
            candidates = [name for name in plan if _engine_is_usable(name, now)]
            if not candidates:
                raise ToolExecutionError(tr('python.web_tools.2e0d32668478'),
                                         query=query, engines=plan)
            probes = [asyncio.create_task(_probe_engine(name, min(1.5, max(.01, fetch_timeout))))
                      for name in candidates]
            try:
                states = await asyncio.gather(*probes)
            finally:
                for probe in probes:
                    if not probe.done():
                        probe.cancel()
                await asyncio.gather(*probes, return_exceptions=True)
            for name, available in zip(candidates, states):
                if available:
                    reachable.append(name)
                else:
                    _mark_engine_failed(name, time.monotonic())
                    errors.append(tr('python.web_tools.7a75945f4e26', p0=f'{name}'))
            plan = reachable

        for index, name in enumerate(plan):
            template = SEARCH_ENGINES.get(name)
            if template is None:
                continue
            url = _search_url(name, query)
            is_last = name == plan[-1]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                errors.append(tr('python.web_tools.3650c4200d57'))
                break
            try:
                response = await _http_get(
                    url,
                    timeout=remaining / (len(plan) - index),
                    max_bytes=MAX_BYTES,
                    accept="text/html",
                )
            except ToolExecutionError as exc:
                _mark_engine_failed(name, time.monotonic())
                errors.append(tr('web.source.error', source=name, error=str(exc)))
                continue
            if response["error"]:
                _mark_engine_failed(name, time.monotonic())
                errors.append(tr('web.source.error', source=name, error=response['error']))
                continue
            results = _SEARCH_PARSERS[name](response["text"], limit)
            if not results and not is_last:
                # 页面能达但解析不到结果（往往是逆向引擎的版本变更）。
                _mark_engine_failed(name, time.monotonic())
                errors.append(tr('python.web_tools.690fcd9cdbfc', p0=f'{name}'))
                continue
            _mark_engine_ok(name)
            return {
                "query": query,
                "engine": name,
                "count": len(results),
                "results": results,
                "attempts": errors,
            }
        raise ToolExecutionError(
            tr('web.search.failed', errors=tr('common.error_separator').join(errors)),
            query=query,
            engines=plan,
        )

    async def _openrouter_search(query: str, limit: int, fetch_timeout: float) -> Any:
        """走 OpenRouter 的站内搜索（需要 API key）。"""
        import os

        api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("HARNESS_API_KEY", "")
        if not api_key:
            raise ToolExecutionError(
                tr('python.web_tools.3d5a2d9a7c17')
            )
        base = os.environ.get("HARNESS_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
        payload = json.dumps({"query": query}).encode("utf-8")
        request = urllib.request.Request(
            f"{base}/search",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )

        response = await _http_request(lambda: controlled_urlopen(request, timeout=fetch_timeout),
                                       request.full_url, timeout=fetch_timeout, max_bytes=MAX_BYTES)
        if response['error'] or response['truncated']:
            raise ToolExecutionError(response['error'] or tr('python.web_tools.f4ca3d7561a3'))
        try:
            raw = json.loads(response['text'])
        except (ValueError, TypeError):
            raise ToolExecutionError(tr('web.deepseek.invalidResponse')) from None
        if not isinstance(raw, dict):
            raise ToolExecutionError(tr('web.deepseek.invalidResponse'))
        items = raw.get('data') if raw.get('data') is not None else raw.get('results', [])
        if (not isinstance(items, list) or any(not isinstance(item, dict) or
                any(item.get(key) is not None and not isinstance(item[key], str)
                    for key in ('title', 'name', 'url', 'link', 'content', 'snippet', 'description')) for item in items)):
            raise ToolExecutionError(tr('web.deepseek.invalidResponse'))
        results = []
        for item in items[:limit]:
            results.append(
                {
                    "title": str(item.get("title") or item.get("name") or ""),
                    "url": str(item.get("url") or item.get("link") or ""),
                    "snippet": str(item.get("content") or item.get("snippet") or item.get("description") or "")[:500],
                }
            )
        return {"query": query, "engine": "openrouter", "count": len(results), "results": results}

    # 提供方表决定顺序和能力；新增服务只需注册适配器，回退规则保持一致。
    providers = (
        ('deepseek', {'search': deepseek_search, 'fetch': deepseek_fetch}),
        ('brave', {'search': brave_search}),
    )

    def checked_arguments(operation, args):
        """直接 SDK 与模型入口共用参数校验，网络请求前拒绝隐式类型转换。"""
        value = dict(args)
        text = 'query' if operation == 'search' else 'url'
        value[text] = string_argument(value.get(text), text)
        fields = {'max_results': 8} if operation == 'search' else {'max_length': MAX_CONTENT_CHARS, 'max_bytes': MAX_BYTES}
        for name, default in fields.items():
            number = value.get(name, default)
            if type(number) is not int or number < 1:
                raise ToolExecutionError(tr('tool.arguments.positiveInteger', argument=name))
            value[name] = number
        wait = value.get('timeout', web_timeout)
        if type(wait) not in (int, float) or wait <= 0:
            raise ToolExecutionError(tr('python.web_tools.9f0e3b9cdc96'))
        try:
            finite = math.isfinite(wait)
        except OverflowError:
            finite = False
        if not finite:
            raise ToolExecutionError(tr('python.web_tools.9f0e3b9cdc96'))
        value['timeout'] = wait
        if operation == 'fetch' and type(value.get('raw', False)) is not bool:
            raise ToolExecutionError(tr('tool.arguments.boolean', argument='raw'))
        if operation == 'search':
            engine = value.get('engine', 'auto')
            supported = set(SEARCH_ENGINES) | {'openrouter', 'auto'}
            if not isinstance(engine, str) or engine.lower() not in supported:
                raise ToolExecutionError(tr('web.search.unsupported', engine=repr(engine), choices=tr('ui.common.list_separator').join(sorted(supported))))
            value['engine'] = engine.lower()
        return value

    async def first_success(operation, args, ctx):
        """顺序尝试非空结果，为后续提供方保留总等待预算；取消不触发回退。"""
        settings = provider_settings() if provider_settings else {}
        plan = [(name, adapters[operation], settings[name][1]) for name, adapters in providers
                if operation in adapters and settings.get(name, (False, ''))[0]]
        fallback = default_search if operation == 'search' else default_fetch
        if not plan:
            return await fallback(args, ctx)
        total = float(args.get('timeout', web_timeout))
        if not math.isfinite(total) or total <= 0:
            raise ToolExecutionError(tr('python.web_tools.9f0e3b9cdc96'))
        deadline = time.monotonic() + total
        plan.append(('default', None, ''))
        errors = []
        for index, (name, adapter, key) in enumerate(plan):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                errors.append(tr('python.web_tools.3650c4200d57'))
                break
            allotted = remaining / (len(plan) - index)
            try:
                async with asyncio.timeout(allotted):
                    request_args = {**args, 'timeout': allotted}
                    result = await adapter(request_args, ctx, key) if adapter else await fallback(request_args, ctx)
                success = bool(result.get('results')) if operation == 'search' else bool(result.get('content')) and not result.get('error')
                if success:
                    result['attempts'] = errors + result.get('attempts', [])
                    return result
                errors.append(tr('web.source.error', source=name, error=tr('web.providers.noResults')))
            except (ToolExecutionError, TimeoutError) as exc:
                errors.append(tr('web.source.error', source=name, error=str(exc) or tr('python.web_tools.3650c4200d57')))
        raise ToolExecutionError(tr('web.providers.failed', errors=tr('common.error_separator').join(errors)))

    async def web_search(args, ctx):
        return await first_success('search', checked_arguments('search', args), ctx)

    # 超时记忆仅属于当前工具实例和会话，避免其他会话被故障来源牵连。
    fetch_timeouts: dict[tuple[str, str], float] = {}

    async def web_fetch(args, ctx):
        args = checked_arguments('fetch', args)
        url = args['url'].strip()
        origin = urllib.parse.urlsplit(url)
        key = (ctx.session_id, f"{origin.scheme}://{origin.netloc}")
        now = time.monotonic()
        for expired in [item for item, until in fetch_timeouts.items() if until <= now]:
            del fetch_timeouts[expired]
        if key in fetch_timeouts:
            raise ToolExecutionError(
                tr('web.fetch.cooldown'), failure_kind='source_cooldown',
                retry_after=math.ceil(fetch_timeouts[key] - now),
                recovery='use_web_search_or_another_source')
        try:
            return await first_success('fetch', args, ctx)
        except ToolExecutionError as exc:
            if exc.details.get('failure_kind') == 'timeout':
                # 只抑制已确认的超时，不把权限、取消或未知错误当作站点故障。
                fetch_timeouts[key] = time.monotonic() + 60.0
            raise

    def web_search_to_content(args: Any, value: Any) -> list[TextBlock]:
        if not value["results"]:
            return [TextBlock(text=tr('python.web_tools.4215c5738afa', p0=f"{value['query']!r}"))]
        lines = [tr('python.web_tools.b0246c68c9f6', p0=f"{value['query']!r}", p1=f"{value['engine']}", p2=f"{value['count']}"), ""]
        for index, item in enumerate(value["results"], start=1):
            lines.append(f"{index}. {item['title'] or tr('python.web_tools.415ac660f51d')}")
            lines.append(f"   {item['url']}")
            if item["snippet"]:
                lines.append(f"   {item['snippet']}")
        return [TextBlock(text="\n".join(lines))]

    def web_search_display(args: Any, value: Any) -> dict[str, Any]:
        return {"query": value["query"], "count": value["count"], "engine": value["engine"]}

    return [
        ToolDefinition(
            schema={
                "description_key": 'python.web_tools.86bd2a0a8f8d',
                "parameter_keys": {
                    ('properties', 'url', 'description'): 'python.web_tools.85ce19d7f60c',
                    ('properties', 'max_length', 'description'): ('python.web_tools.5f24e2b3794e', {'p0': f'{MAX_CONTENT_CHARS}'}),
                    ('properties', 'raw', 'description'): 'python.web_tools.0b9c0788a370',
                    ('properties', 'timeout', 'description'): ('python.web_tools.9c4c120875f7', {'p0': f'{web_timeout}'}),
                },
                "name": "web_fetch",
                "description": (
                    tr('python.web_tools.86bd2a0a8f8d')
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": tr('python.web_tools.85ce19d7f60c')},
                        "max_length": {
                            "type": "integer",
                            "description": tr('python.web_tools.5f24e2b3794e', p0=f'{MAX_CONTENT_CHARS}'),
                        },
                        "raw": {
                            "type": "boolean",
                            "description": tr('python.web_tools.0b9c0788a370'),
                        },
                        "timeout": {"type": "number", "description": tr('python.web_tools.9c4c120875f7', p0=f'{web_timeout}')},
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
            },
            # 内置实现仅读取网页或检索结果，不改变目标资源。
            effect="read",
            execute=web_fetch,
            to_content=web_fetch_to_content,
            describe=web_fetch_display,
            timeout=timeout + 20,
        ),
        ToolDefinition(
            schema={
                "description_key": 'python.web_tools.186d126bfe59',
                "parameter_keys": {
                    ('properties', 'query', 'description'): 'python.web_tools.c00a21fdf784',
                    ('properties', 'max_results', 'description'): 'python.web_tools.237f207e5130',
                    ('properties', 'engine', 'description'): 'python.web_tools.511db89fc3a6',
                    ('properties', 'timeout', 'description'): ('python.web_tools.dca45875eab2', {'p0': f'{web_timeout}'}),
                },
                "name": "web_search",
                "description": (
                    tr('python.web_tools.186d126bfe59')
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": tr('python.web_tools.c00a21fdf784')},
                        "max_results": {"type": "integer", "description": tr('python.web_tools.237f207e5130')},
                        "engine": {
                            "type": "string",
                            "enum": ["auto", "duckduckgo", "bing", "openrouter"],
                            "description": tr('python.web_tools.511db89fc3a6'),
                        },
                        "timeout": {"type": "number", "description": tr('python.web_tools.dca45875eab2', p0=f'{web_timeout}')},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
            effect="read",
            execute=web_search,
            to_content=web_search_to_content,
            describe=web_search_display,
            timeout=timeout + 20,
        ),
    ]


#: 匹配 DuckDuckGo HTML 结果条目
_DDG_RESULT_RE = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_DDG_SNIPPET_RE = re.compile(
    r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(?P<snippet>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)

#: Bing 结果分隔符：每个 ``<li class="b_algo">`` 是一条结果。
#: 分隔后再分别取标题链接与摘要，避免用一条大正则被页面层的
#: ``<link>`` / ``<script>`` 标签隔开后合并失败。
_BING_BLOCK_SPLIT_RE = re.compile(r'<li class="b_algo"', re.IGNORECASE)
#: 标题链接：Bing 会在 ``<h2>`` 上加 ``class=""``，且 ``href`` 可能在 ``target`` 之前或之后。
_BING_LINK_RE = re.compile(
    r'<h2[^>]*>\s*<a[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
#: 摘要：取第一个非空 ``<p>``，避免匹配到导航栏里的描述。
_BING_SNIPPET_RE = re.compile(r'<p[^>]*>(.*?)</p>', re.IGNORECASE | re.DOTALL)


def _parse_duckduckgo(source: str, limit: int) -> list[dict[str, str]]:
    """从 DuckDuckGo 的 HTML 结果页解析结果列表。"""
    results: list[dict[str, str]] = []
    snippets = [
        html.unescape(_TAG_RE.sub("", m.group("snippet"))).strip()
        for m in _DDG_SNIPPET_RE.finditer(source)
    ]
    for index, match in enumerate(_DDG_RESULT_RE.finditer(source)):
        href = html.unescape(match.group("href"))
        # DuckDuckGo 会把真实地址包在 /l/?uddg=... 里
        parsed = urllib.parse.urlparse(href)
        if parsed.path.startswith("/l/"):
            target = urllib.parse.parse_qs(parsed.query).get("uddg", [""])[0]
            href = urllib.parse.unquote(target) or href
        if not urllib.parse.urlparse(href).scheme.startswith("http"):
            continue
        title = html.unescape(_TAG_RE.sub("", match.group("title"))).strip()
        results.append(
            {
                "title": title,
                "url": href,
                "snippet": snippets[index] if index < len(snippets) else "",
            }
        )
        if len(results) >= limit:
            break
    return results


def _parse_bing(source: str, limit: int) -> list[dict[str, str]]:
    """从 Bing 的 HTML 结果页解析结果列表。"""
    results: list[dict[str, str]] = []
    for block in _BING_BLOCK_SPLIT_RE.split(source)[1:]:
        match = _BING_LINK_RE.search(block)
        if match is None:
            continue
        href = html.unescape(match.group("href")).strip()
        if not href.startswith("http"):
            continue
        title = html.unescape(_TAG_RE.sub("", match.group("title"))).strip()
        snippet_match = _BING_SNIPPET_RE.search(block)
        snippet = ""
        if snippet_match is not None:
            snippet = html.unescape(_TAG_RE.sub("", snippet_match.group(1))).strip()
        results.append({"title": title, "url": href, "snippet": snippet})
        if len(results) >= limit:
            break
    return results


#: 引擎名称 -> 解析函数。
_SEARCH_PARSERS = {
    "duckduckgo": _parse_duckduckgo,
    "bing": _parse_bing,
}


__all__ = ["build_web_tools", "html_to_text"]
