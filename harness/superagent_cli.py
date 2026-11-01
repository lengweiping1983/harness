"""显式 Superagent 本地文件信道入口，不加载普通对话宿主。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from .app.superagent_service import SuperagentService
from .core.cleanup import raise_cleanup_errors
from .i18n import tr


def add_arguments(parser):
    parser.add_argument('--superagent', metavar='CONFIG', help=tr('superagent.cli.help'))
    parser.add_argument('--superagent-once', action='store_true', help=tr('superagent.cli.once'))


def handle(args, parser):
    if args.superagent is None:
        if args.superagent_once:
            parser.error(tr('superagent.cli.config_required'))
        return None
    if (args.prompt or args.ui or args.share or args.status or args.list_sessions or args.cwd or args.model
            or args.reasoning or args.no_shell or args.no_fs or args.dist or args.host != '127.0.0.1' or args.port != 8765
            or args.list_external_agents or args.external_agent or args.agent_command is not None
            or args.agent_arg is not None or args.agent_timeout != 600
            or any(getattr(args,'memory_' + key) for key in ('export','verify','import','scope','session'))):
        parser.error(tr('superagent.cli.conflict'))
    try:
        return asyncio.run(run(Path(args.superagent), once=args.superagent_once))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(tr('python.cli.063abdaa463d', p0=str(exc)), file=sys.stderr)
        return 2


async def run(config_path: Path, *, once=False):
    service = await SuperagentService.open(config_path)
    errors = []
    result = 2
    try:
        print(tr('superagent.cli.started', path=str(service.root)), file=sys.stderr, flush=True)
        if service.github is not None:
            print(tr('superagent.cli.github',port=service.github.server.port),file=sys.stderr,flush=True)
        if service.feishu is not None:
            print(tr('superagent.cli.feishu',port=service.feishu.server.port),file=sys.stderr,flush=True)
        result = await service.serve(asyncio.Event(), once=once)
    except BaseException as exc:
        errors.append(exc)
    try:
        await service.aclose()
    except BaseException as exc:
        if not any(exc is previous for previous in errors):
            errors.append(exc)
    raise_cleanup_errors(errors)
    return result
