"""无需模型或服务进程的显式三层归档备份命令。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

from harness.i18n import tr
from .app.config import DEFAULT_HOME
from .app.layered_memory import LayeredMemory
from .app.memory import ProjectMemory
from .app.memory_archive import MemoryArchive
from .core.errors import HarnessError


def add_arguments(parser):
    """复用主CLI，三个文件动作互斥，作用域不能隐式猜测会话身份。"""
    group = parser.add_mutually_exclusive_group()
    for action in ('export', 'verify', 'import'):
        group.add_argument('--memory-' + action, metavar='FILE', help=tr('cli.memory.' + action))
    parser.add_argument('--memory-scope', choices=('user', 'project', 'session'), help=tr('cli.memory.scope'))
    parser.add_argument('--memory-session', metavar='SESSION_ID', help=tr('cli.memory.session'))


def handle(args, parser):
    """文件动作成功返回JSON回执；失败不重放、不清理产物。"""
    action = next((name for name in ('export', 'verify', 'import') if getattr(args, 'memory_' + name)), None)
    if action is None:
        if args.memory_scope is not None or args.memory_session is not None:
            parser.error(tr('cli.memory.action_required'))
        return None
    if (args.prompt or args.ui or args.status or args.list_sessions or args.no_shell or args.no_fs
            or args.model or args.reasoning or args.dist or args.host != '127.0.0.1' or args.port != 8765):
        parser.error(tr('cli.memory.conflict'))
    scope = args.memory_scope or 'project'
    if (scope == 'session' and (not args.memory_session or not args.memory_session.strip())
            or scope != 'session' and args.memory_session is not None):
        parser.error(tr('cli.memory.session_required'))
    try:
        cwd = Path(args.cwd or Path.cwd()).expanduser().resolve()
        home = Path(os.environ.get('HARNESS_HOME', str(DEFAULT_HOME))).expanduser()
        memory = LayeredMemory(home, ProjectMemory()).store(scope, session_id=args.memory_session or '')
        archive = MemoryArchive(memory)
        path = Path(getattr(args, 'memory_' + action)).expanduser().absolute()
        if action == 'export':
            with path.open('x', encoding='utf-8', newline='') as stream:
                result = archive.export_jsonl(cwd, stream)
                stream.flush()
                os.fsync(stream.fileno())
        else:
            with path.open(encoding='utf-8', newline='') as stream:
                result = archive.verify_jsonl(stream) if action == 'verify' else archive.import_jsonl(cwd, stream)
        print(json.dumps({'operation': action, 'scope': scope, 'file': str(path), **result}, ensure_ascii=False))
        return 0
    except (HarnessError, OSError, sqlite3.Error) as exc:
        print(tr('python.cli.063abdaa463d', p0=str(exc)), file=sys.stderr)
        return 2
