"""唯一 Superagent CLI，提交、状态、指令与控制使用同一协调账本。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from .app.superagent_service import SuperagentService
from .app.superagent_queries import QUERY_RESOURCES,GLOBAL_RESOURCES
from .core.cleanup import raise_cleanup_errors
from .i18n import tr


def add_arguments(parser):
    parser.add_argument('--superagent', metavar='CONFIG', help=tr('superagent.cli.help'))
    parser.add_argument('--superagent-once', action='store_true', help=tr('superagent.cli.once'))
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--superagent-submit', metavar='TEXT', help=tr('superagent.cli.submit'))
    actions.add_argument('--superagent-status', action='store_true', help=tr('superagent.cli.status'))
    actions.add_argument('--superagent-list', choices=QUERY_RESOURCES, help=tr('superagent.cli.list'))
    actions.add_argument('--superagent-show', metavar='MISSION_ID', help=tr('superagent.cli.show'))
    actions.add_argument('--superagent-steer', metavar='TEXT', help=tr('superagent.cli.steer'))
    actions.add_argument('--superagent-receipt', metavar='REQUEST_ID', help=tr('superagent.cli.receipt'))
    actions.add_argument('--superagent-control-receipt', metavar='REQUEST_ID', help=tr('superagent.cli.control-receipt'))
    actions.add_argument('--superagent-pause', action='store_true', help=tr('superagent.cli.pause'))
    actions.add_argument('--superagent-resume', action='store_true', help=tr('superagent.cli.resume'))
    actions.add_argument('--superagent-cancel', action='store_true', help=tr('superagent.cli.cancel'))
    parser.add_argument('--superagent-job', metavar='JOB_ID', help=tr('superagent.cli.job'))
    parser.add_argument('--superagent-mission', metavar='MISSION_ID', help=tr('superagent.cli.mission'))
    parser.add_argument('--superagent-limit', type=int, help=tr('superagent.cli.limit'))
    parser.add_argument('--superagent-cursor', help=tr('superagent.cli.cursor'))
    for name in ('source', 'actor', 'thread', 'request-id'):
        parser.add_argument('--superagent-' + name, help=tr('superagent.cli.' + name))


def handle(args, parser):
    selectors = {key: getattr(args, 'superagent_' + key.replace('-', '_'))
                 for key in ('source', 'actor', 'thread', 'request-id')}
    control_action = ('pause' if args.superagent_pause else 'resume' if args.superagent_resume else
                      'cancel' if args.superagent_cancel else None)
    local = (args.superagent_submit is not None or args.superagent_status or args.superagent_steer is not None
             or args.superagent_receipt is not None or args.superagent_control_receipt is not None or control_action is not None
             or args.superagent_list is not None or args.superagent_show is not None)
    if args.superagent is None:
        if (args.superagent_once or local or args.superagent_job is not None or args.superagent_mission is not None
                or args.superagent_limit is not None or args.superagent_cursor is not None
                or any(value is not None for value in selectors.values())):
            parser.error(tr('superagent.cli.config_required'))
        return None
    if ((local and args.superagent_submit is None and control_action is None and args.superagent_once)
            or args.superagent_submit is None and args.superagent_steer is None and control_action is None and any(value is not None for value in selectors.values())
            or args.superagent_job is not None and args.superagent_steer is None
            or args.superagent_mission is not None and control_action is None and args.superagent_list is None
            or (args.superagent_limit is not None or args.superagent_cursor is not None) and args.superagent_list is None
            or args.superagent_list is not None and args.superagent_list not in GLOBAL_RESOURCES and args.superagent_mission is None
            or args.superagent_limit is not None and not 1 <= args.superagent_limit <= 100
            or control_action is not None and (args.superagent_mission is None or args.superagent_job is not None
                                               or any(value is not None for value in (selectors['source'], selectors['actor'], selectors['thread'])))):
        parser.error(tr('superagent.cli.conflict'))
    if (args.prompt or args.ui or args.share or args.status or args.list_sessions or args.cwd or args.model
            or args.reasoning or args.no_shell or args.no_fs or args.dist or args.host != '127.0.0.1' or args.port != 8765
            or args.list_external_agents or args.external_agent or args.agent_command is not None
            or args.agent_arg is not None or args.agent_timeout != 600
            or any(getattr(args,'memory_' + key) for key in ('export','verify','import','scope','session'))):
        parser.error(tr('superagent.cli.conflict'))
    try:
        if local:
            from .app.superagent_local import read_guidance_receipt, read_status, steer_local, submit_local, query_local, mission_local
            if args.superagent_list is not None:
                result = query_local(Path(args.superagent), args.superagent_list, mission_id=args.superagent_mission,
                    limit=args.superagent_limit if args.superagent_limit is not None else 32, cursor=args.superagent_cursor)
            elif args.superagent_show is not None:
                result = mission_local(Path(args.superagent), args.superagent_show)
            elif args.superagent_status:
                result = read_status(Path(args.superagent))
            elif args.superagent_receipt is not None:
                result = read_guidance_receipt(Path(args.superagent), args.superagent_receipt)
            elif args.superagent_control_receipt is not None:
                from .app.superagent_local import read_control_receipt
                result = read_control_receipt(Path(args.superagent), args.superagent_control_receipt)
            elif args.superagent_steer is not None:
                result = steer_local(Path(args.superagent), args.superagent_steer, job_id=args.superagent_job,
                    source_id=selectors['source'], actor_id=selectors['actor'], thread_id=selectors['thread'], request_id=selectors['request-id'])
            elif control_action is not None:
                from .app.superagent_local import control_local
                result = control_local(Path(args.superagent), control_action, args.superagent_mission,
                                       request_id=selectors['request-id'])
            else:
                result = submit_local(Path(args.superagent), args.superagent_submit, source_id=selectors['source'],
                                      actor_id=selectors['actor'], thread_id=selectors['thread'], request_id=selectors['request-id'])
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if (args.superagent_submit is not None or control_action is not None) and args.superagent_once:
                code = asyncio.run(run(Path(args.superagent), once=True))
                if control_action is not None:
                    from .app.superagent_local import read_control_receipt
                    if read_control_receipt(Path(args.superagent), result['request_id'])['state'] != 'applied':
                        return 2
                return code
            return 0
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
        for listener in service.listeners:
            print(tr('superagent.cli.' + listener['kind'], port=listener['server'].port), file=sys.stderr, flush=True)
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
