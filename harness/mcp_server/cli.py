"""启动服务或在本机批准 OAuth，不使用大模型 API。"""
from __future__ import annotations

import argparse
import asyncio
import json

from ..execution.config import ExecutionConfig
from ..core.language import current_language, resolve_language
from ..i18n import tr


def main(argv=None):
    from harness.core.text_encoding import configure_cli_streams
    configure_cli_streams()
    parser = argparse.ArgumentParser(description=tr('execution.cli.description'))
    parser.add_argument("action", choices=("serve", "approvals", "approve", "init-login", "companion"), nargs="?", default="serve")
    parser.add_argument("--config", required=True)
    parser.add_argument("--transport", choices=("http", "stdio"), default="http")
    parser.add_argument("--port", type=int, default=8876)
    parser.add_argument("--companion-port", type=int, default=8877)
    parser.add_argument("--public-url", help=tr('execution.cli.public_url'))
    parser.add_argument("--principal", default="local")
    parser.add_argument("--ticket")
    args = parser.parse_args(argv)
    config = ExecutionConfig.load(args.config)
    with current_language.set(resolve_language(config.language).effective_language):
        return _execute(args, config, parser)


def _execute(args, config, parser):
    """配置加载后以已确定的语言执行命令，不泄漏到调用方。"""
    if args.action == 'companion':
        if args.public_url or args.transport != 'http':
            parser.error(tr('execution.companion.local_only'))
        from .companion import serve
        serve(config, args.companion_port, args.principal)
        return 0
    try:
        from .auth import LocalOAuthProvider
        from .server import create_http_app, create_server
    except ImportError as exc:
        parser.error(tr('execution.cli.dependencies', name=exc.name))
    if args.action != "serve":
        if not args.public_url:
            parser.error(tr('execution.cli.same_url'))
        provider = LocalOAuthProvider(config.home, args.public_url, args.principal)
        if args.action == "init-login":
            print(provider.initialize_login())
        elif args.action == "approvals":
            print(json.dumps(provider.pending_requests(), ensure_ascii=False, indent=2))
        else:
            if not args.ticket:
                parser.error(tr('execution.cli.ticket'))
            provider.approve(args.ticket)
            print(tr('execution.cli.approved'))
        return 0
    if args.transport == "stdio":
        if args.public_url:
            parser.error(tr('execution.cli.stdio'))
        from mcp.server.stdio import stdio_server

        async def run():
            server = create_server(config, local_principal=args.principal)
            async with stdio_server() as (reader, writer):
                await server.run(reader, writer, server.create_initialization_options())

        asyncio.run(run())
    else:
        import uvicorn
        uvicorn.run(create_http_app(config, public_url=args.public_url, principal=args.principal), host="127.0.0.1", port=args.port)
    return 0
