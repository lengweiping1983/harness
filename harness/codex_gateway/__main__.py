"""隔离账号授权与桌面接入验收入口，不自动改写桌面端配置。"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from contextlib import nullcontext
from pathlib import Path

from ..core.language import current_language
from ..i18n import tr
from ..llm.chatgpt_auth import ChatGPTAccount, ChatGPTAccountError
from .accounts import AccountPool
from .quota import AppServerQuotaReader, QuotaError
from .lease import runtime_lease


def desktop_config(home, port, model):
    """返回可审查的 TOML，不输出上游令牌或覆盖用户配置。"""
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    root = Path(__file__).resolve().parents[2]
    return '\n'.join([
        'model = ' + quote(model), 'model_provider = "harness-subscriptions"',
        '', '[model_providers.harness-subscriptions]', 'name = ' + quote(tr('ui.codexGateway.providerName')),
        f'base_url = "http://127.0.0.1:{port}/api/v1/codex"', 'wire_api = "responses"',
        'supports_websockets = false', 'request_max_retries = 0', 'stream_max_retries = 0',
        'stream_idle_timeout_ms = 21600000',
        f'model_catalog_url = "http://127.0.0.1:{port}/api/v1/codex/models"',
        '', '[model_providers.harness-subscriptions.auth]',
        'command = ' + quote(sys.executable),
        'args = ' + json.dumps(['-m', 'harness.codex_gateway', '--home', str(Path(home).resolve()), 'token']),
        'cwd = ' + quote(str(root)), 'timeout_ms = 5000', 'refresh_interval_ms = 300000', ''])


def main(argv=None):
    from ..core.text_encoding import configure_cli_streams
    configure_cli_streams()
    preliminary = argparse.ArgumentParser(add_help=False)
    preliminary.add_argument('--language', choices=('en', 'zh-CN'), default='zh-CN')
    known, _ = preliminary.parse_known_args(argv)
    with current_language.set(known.language):
        return run(argv)


def run(argv):
    parser = argparse.ArgumentParser(description=tr('ui.codexGateway.cliDescription'))
    parser.add_argument('--home', required=True, type=Path, help=tr('ui.codexGateway.cliHome'))
    parser.add_argument('--language', choices=('en', 'zh-CN'), default='zh-CN')
    parser.add_argument('--codex', default='codex', help=tr('ui.codexGateway.cliCodex'))
    commands = parser.add_subparsers(dest='action', required=True)
    login = commands.add_parser('login', help=tr('ui.codexGateway.cliLogin'))
    login.add_argument('--account', default=None)
    commands.add_parser('status', help=tr('ui.codexGateway.cliStatus'))
    commands.add_parser('token', help=tr('ui.codexGateway.cliToken'))
    config = commands.add_parser('config', help=tr('ui.codexGateway.cliConfig'))
    config.add_argument('--model', required=True)
    config.add_argument('--port', type=int, default=18765)
    serve = commands.add_parser('serve', help=tr('ui.codexGateway.cliServe'))
    serve.add_argument('--port', type=int, default=18765)
    args = parser.parse_args(argv)
    args.home = args.home.expanduser().resolve()
    try:
        with current_language.set(args.language), (nullcontext() if args.action in ('token', 'config') else runtime_lease(args.home)):
            pool = AccountPool(args.home, ChatGPTAccount(args.home), reader=AppServerQuotaReader(args.codex))
            if args.action == 'config':
                if not 1 <= args.port <= 65535:
                    raise QuotaError('ui.codexGateway.invalidRequest')
                print(desktop_config(args.home, args.port, args.model))
            elif args.action == 'token':
                print(pool.credential())
            elif args.action == 'status':
                print(json.dumps(asyncio.run(pool.refresh(force=True)), ensure_ascii=False, indent=2))
            elif args.action == 'login':
                identity = args.account or ('primary' if not pool.slot('primary').account.logged_in else pool.create())
                account = pool.slot(identity).account
                try:
                    result = pool.login(identity)
                    print(tr('ui.codexGateway.loginUrl', account=identity, url=result['authorization_url']), flush=True)
                    while account.pending is not None:
                        time.sleep(.25)
                    if not account.logged_in or account.error:
                        raise QuotaError('ui.codexGateway.loginIncomplete')
                    print(tr('ui.codexGateway.loginComplete', account=identity), flush=True)
                finally:
                    account.cancel_login()
            else:
                if not 1 <= args.port <= 65535:
                    raise QuotaError('ui.codexGateway.invalidRequest')
                import uvicorn
                from .server import create_app
                pool.configure(True)
                uvicorn.run(create_app(pool), host='127.0.0.1', port=args.port,
                            proxy_headers=False, access_log=False, timeout_graceful_shutdown=10)
    except (QuotaError, ChatGPTAccountError) as exc:
        with current_language.set(args.language):
            print(str(exc), file=sys.stderr)
        return 1
    except OSError:
        print(tr('ui.codexGateway.operationFailed'), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
