"""托管 MCP 的本机聊天接续桥；只回收本实例拥有的监听。"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from ..core.language import current_language
from ..execution.config import ExecutionConfig
from ..core.errors import HarnessError
from ..llm.routes import mask_api_key
from ..mcp_server.companion import PROTOCOL_VERSION, create_server
from .mcp_errors import McpServiceError
from .mcp_tunnel import _unique


DEFAULTS = {'enabled': True, 'port': 8877}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """本机凭据不得跟随重定向。"""
        return None


def validate_settings(value):
    """仅接受本机开关和有界端口，不接受公网监听地址。"""
    if (not isinstance(value, dict) or set(value) != set(DEFAULTS) or type(value['enabled']) is not bool
            or type(value['port']) is not int or not 1024 <= value['port'] <= 65535):
        raise McpServiceError('ui.companion.invalidConfig')
    return dict(value)


class ChatCompanionService:
    def __init__(self, home, source):
        self.home, self.source = Path(home), Path(source)
        self.server = self.thread = None
        self.error = None
        self.config = None

    def settings(self):
        """损坏设置原件保留，不用默认值掩盖错误。"""
        path = self.home / 'companion.json'
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return dict(DEFAULTS), None
        try:
            if len(raw) > 4096:
                raise ValueError('configuration size')
            return validate_settings(json.loads(raw, object_pairs_hook=_unique)), raw
        except (ValueError, TypeError, UnicodeError) as exc:
            raise McpServiceError('ui.companion.invalidConfig') from exc

    def _probe(self, port, token):
        """回环检查绕过代理，不转发凭据或跟随其他地址。"""
        if not token:
            return None
        request = Request(f'http://127.0.0.1:{port}/health', headers={'Authorization': 'Bearer ' + token})
        try:
            with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=.3) as response:
                value = json.loads(response.read(4096))
            if (isinstance(value, dict) and value.get('protocol') == 'harness-chat-companion' and value.get('protocol_version') == PROTOCOL_VERSION
                    and value.get('executor') in {'ready', 'offline', 'missing', 'invalid'}):
                return value
        except (HTTPError, URLError, OSError, ValueError, TypeError):
            pass
        return None

    def token(self):
        """完整配对码仅供本机显式揭示接口读取。"""
        from ..mcp_server.companion import read_pairing_token
        config = self.config or ExecutionConfig.load(self.home / 'config.json')
        return read_pairing_token(config.home)

    def snapshot(self, mcp_running=False):
        try:
            settings, _ = self.settings()
        except McpServiceError as exc:
            settings = dict(DEFAULTS)
            self.error = exc
        extension = self.source / 'extensions' / 'chatgpt-companion'
        version = json.loads((extension / 'manifest.json').read_text(encoding='utf-8'))['version'] if (extension / 'manifest.json').exists() else ''
        token, health = '', None
        try:
            token = self.token()
            if settings['enabled'] and mcp_running:
                health = self._probe(settings['port'], token)
        except (OSError, ValueError, HarnessError):
            pass
        return {**settings, 'status': 'failed' if self.error else 'disabled' if not settings['enabled'] else 'running' if health else 'stopped',
                'endpoint': f"http://127.0.0.1:{settings['port']}/", 'executor': health['executor'] if health else 'offline',
                'token_mask': mask_api_key(token) if token else '', 'external': bool(health and self.server is None),
                'error': str(self.error) if self.error else '',
                'error_copy': {'key': self.error.key, 'params': dict(self.error.values)} if isinstance(self.error, McpServiceError) else None,
                'extension_path': str(extension), 'extension_version': version}

    def start(self):
        """MCP 确认运行后复用同配置桥，或启动所属监听线程。"""
        self.error = None
        try:
            settings, _ = self.settings()
            if not settings['enabled']:
                return
            self.config = ExecutionConfig.load(self.home / 'config.json')
            try:
                token = self.token()
            except FileNotFoundError:
                token = ''
            if self._probe(settings['port'], token):
                return
            if self.server is not None:
                raise McpServiceError('ui.companion.stopPending')
            with current_language.set(self.config.language if self.config.language != 'auto' else current_language.get()):
                server = create_server(self.config, settings['port'])
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .1}, name='harness-chat-companion', daemon=True)
            self.server, self.thread = server, thread
            try:
                thread.start()
            except BaseException:
                server.server_close()
                self.server = self.thread = None
                raise
        except McpServiceError as exc:
            self.error = exc
        except (OSError, ValueError, HarnessError, sqlite3.Error, RuntimeError) as exc:
            self.error = McpServiceError('ui.companion.startFailed', detail=str(exc))

    def stop(self):
        """手工启动的外部桥不属于本实例，不能停止。"""
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            if self.thread is not None:
                self.thread.join(timeout=3)
                if self.thread.is_alive():
                    raise McpServiceError('ui.companion.stopPending')
            self.server = self.thread = None
        self.error = None
