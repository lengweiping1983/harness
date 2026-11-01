"""独立 Agent 命令行入口，不要求 harness 模型或 API key。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from ..i18n import tr
from .client import AcpClient, AcpError
from .runners import discover_runners, runner_config


async def _read_choice() -> str:
    """轮询终端输入，取消时不遗留阻塞 input 的线程。"""
    if os.name == 'nt':
        import msvcrt
        characters = []
        while True:
            if msvcrt.kbhit():
                char = msvcrt.getwch()
                if char in ('\r', '\n'):
                    return ''.join(characters)
                if char == '\x03':
                    raise asyncio.CancelledError
                if char == '\b':
                    characters = characters[:-1]
                elif char.isprintable():
                    characters.append(char)
            await asyncio.sleep(.05)
    import select
    while not select.select([sys.stdin], [], [], 0)[0]:
        await asyncio.sleep(.05)
    return sys.stdin.readline()


def add_arguments(parser) -> None:
    """给现有 CLI 添加独立入口，不改变普通对话的参数语义。"""
    parser.add_argument('--list-external-agents', action='store_true', help=tr('acp.cli.list_help'))
    parser.add_argument('--external-agent', choices=('opencode', 'codex', 'claude', 'custom'), help=tr('acp.cli.run_help'))
    parser.add_argument('--agent-command', help=tr('acp.cli.command_help'))
    parser.add_argument('--agent-arg', action='append', default=None, help=tr('acp.cli.arg_help'))
    parser.add_argument('--agent-timeout', type=float, default=600, help=tr('acp.cli.timeout_help'))


def handle(args) -> int:
    """检测或执行外部 Agent；所有失败返回非零状态。"""
    if args.list_external_agents:
        print(json.dumps({'runners': discover_runners()}, ensure_ascii=False, indent=2))
        return 0
    text = ' '.join(args.prompt).strip()
    if not text:
        print(tr('acp.cli.task_required'), file=sys.stderr)
        return 2
    try:
        config = runner_config(args.external_agent, command=args.agent_command,
                               args=args.agent_arg, timeout=args.agent_timeout)
        cwd = Path(args.cwd or Path.cwd()).expanduser().resolve(strict=True)
        return asyncio.run(run(config, cwd, text))
    except KeyboardInterrupt:
        print(tr('acp.cli.interrupted'), file=sys.stderr)
        return 130
    except (AcpError, OSError, ValueError, TimeoutError) as exc:
        print(tr('acp.cli.failed', error=str(exc) or type(exc).__name__), file=sys.stderr)
        return 1


async def run(config, cwd: Path, text: str) -> int:
    """流式输出正文；TTY 明确选择权限选项，无交互输入则取消。"""
    denied = False

    async def update(params):
        change = params.get('update', {})
        if change.get('sessionUpdate') == 'agent_message_chunk':
            content = change.get('content', {})
            if content.get('type') == 'text':
                print(content.get('text', ''), end='', flush=True)
        elif change.get('sessionUpdate') == 'tool_call':
            print(tr('acp.cli.tool', title=change.get('title', change.get('toolCallId', ''))), file=sys.stderr, flush=True)

    async def permission(params):
        nonlocal denied
        if not sys.stdin.isatty():
            denied = True
            print(tr('acp.cli.permission_cancelled'), file=sys.stderr, flush=True)
            return None
        print(tr('acp.cli.permission_title', title=params.get('toolCall', {}).get('title', '')), file=sys.stderr)
        options = [option for option in params.get('options', []) if isinstance(option, dict) and isinstance(option.get('optionId'), str)]
        for index, option in enumerate(options, 1):
            print(f"  {index}. {option.get('name', option['optionId'])}", file=sys.stderr)
        print(tr('acp.cli.permission_choose'), file=sys.stderr, end='', flush=True)
        try:
            answer = await _read_choice()
            selected = int(answer.strip()) - 1
            if 0 <= selected < len(options):
                return options[selected]['optionId']
        except ValueError:
            pass
        denied = True
        return None

    client = AcpClient(config, on_update=update, on_permission=permission)
    try:
        await client.start(cwd)
        session = await client.new_session(cwd)
        result = await client.prompt(session, text)
        print()
        reason = result.get('stopReason')
        if denied or reason != 'end_turn':
            print(tr('acp.cli.incomplete', reason=reason or 'unknown'), file=sys.stderr)
            return 1
        return 0
    finally:
        await client.close()
