"""启动或只读检查普通聊天接续桥，复用已经登记的执行器配置。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.core.language import current_language
from harness.execution.config import ExecutionConfig
from harness.i18n import tr
from harness.mcp_server.companion import PROTOCOL_VERSION


PROTOCOL = {'protocol': 'harness-chat-companion', 'protocol_version': PROTOCOL_VERSION}


def _token(config: ExecutionConfig) -> str | None:
    """仅读取已有配对码；检查模式和探测模式不创建或改写凭据。"""
    path = config.home / 'companion-key.json'
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(tr('execution.companion.launch.key_invalid')) from exc
    if not isinstance(value, dict) or set(value) != {'token'} or not isinstance(value['token'], str) or not re.fullmatch(r'[0-9a-f]{64}', value['token']):
        raise ValueError(tr('execution.companion.launch.key_invalid'))
    return value['token']


def probe(config: ExecutionConfig, port: int) -> tuple[str, dict | None]:
    """用现有配对码探测端口，不初始化 Companion，避免副作用。"""
    token = _token(config)
    if token is None:
        return 'not_started', None
    request = Request(f'http://127.0.0.1:{port}/health', headers={
        'Authorization': 'Bearer ' + token,
        'Host': f'127.0.0.1:{port}',
    })
    try:
        with urlopen(request, timeout=1.5) as response:
            value = json.loads(response.read().decode('utf-8'))
    except HTTPError as exc:
        if exc.code == 403:
            return 'foreign', None
        return 'offline', None
    except (URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return 'offline', None
    if not isinstance(value, dict) or any(value.get(key) != expected for key, expected in PROTOCOL.items()):
        return 'foreign', None
    return 'ready', value


def _language(config: ExecutionConfig):
    language = config.language if config.language in {'zh-CN', 'en'} else 'zh-CN'
    return current_language.set(language)


def run(config: ExecutionConfig, port: int, principal: str, check: bool, source_path: Path) -> int:
    state, health = probe(config, port)
    if check:
        print(json.dumps({'state': state, 'health': health}, ensure_ascii=False))
        return 0 if state == 'ready' else 1
    if state == 'ready':
        print(tr('execution.companion.launch.already_running', port=port), flush=True)
        return 0
    if state == 'foreign':
        print(tr('execution.companion.launch.port_occupied', port=port), file=sys.stderr, flush=True)
        return 2
    from harness.mcp_server.companion import serve
    print(tr('execution.companion.launch.starting', config=str(source_path), port=port), flush=True)
    serve(config, port, principal)
    return 0


def main(argv: list[str] | None = None) -> int:
    """解析显式配置并启动桥；不替换已有配置或占用端口。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--port', '--companion-port', dest='port', type=int, default=8877)
    parser.add_argument('--principal', default='local')
    parser.add_argument('--check', action='store_true', help='只读检查已有接续桥')
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error('port must be between 1024 and 65535')
    try:
        config = ExecutionConfig.load(args.config)
        token = _language(config)
        try:
            return run(config, args.port, args.principal, args.check, args.config.resolve())
        finally:
            current_language.reset(token)
    except Exception as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
