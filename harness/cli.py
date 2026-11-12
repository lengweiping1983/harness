"""命令行入口。

用法::

    # 一次性问答（headless）
    python -m harness.cli "统计当前目录有多少个 .py 文件"

    # 交互式对话
    python -m harness.cli

    # 启动 Web UI
    python -m harness.cli --ui --port 8765

    # 只看状态
    python -m harness.cli --status

配置依次读取环境变量、已保存的运行设置，再应用显式命令行参数；
具体字段与优先级见 :class:`~harness.app.config.HarnessConfig`。
"""

from __future__ import annotations

from harness.i18n import tr

import argparse
import asyncio
import os
import sys
from pathlib import Path

from .app.config import HarnessConfig
from .app.harness import Harness
from .core.errors import HarnessError


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="harness",
        description=tr('python.cli.a90821253476'),
    )
    parser.add_argument("prompt", nargs="*", help=tr('python.cli.f4f744976963'))
    parser.add_argument("--ui", action="store_true", help=tr('python.cli.620569ee284e'))
    parser.add_argument("--share", metavar="HTTP_LAN_ORIGIN", help=tr("cli.share"))
    parser.add_argument("--host", default="127.0.0.1", help=tr('python.cli.8292722eebb8'))
    parser.add_argument("--port", type=int, default=8765, help=tr('python.cli.7ab9f8f8c661'))
    parser.add_argument(
        "--dist",
        default=None,
        help=tr('python.cli.f35783c2d0eb'),
    )
    parser.add_argument("--cwd", default=None, help=tr('python.cli.a8300a9ae388'))
    parser.add_argument("--language", choices=("auto", "zh-CN", "en"), default=None, help=tr('python.cli.233ee738c524'))
    parser.add_argument("--model", default=None, help=tr('python.cli.c2307d6f6950'))
    parser.add_argument("--reasoning", default=None, help=tr('python.cli.850b63ec9f03'))
    parser.add_argument("--status", action="store_true", help=tr('python.cli.8211512503c4'))
    parser.add_argument("--list-sessions", action="store_true", help=tr('python.cli.184337c05957'))
    parser.add_argument("--no-shell", action="store_true", help=tr('python.cli.942ecedb716c'))
    parser.add_argument("--no-fs", action="store_true", help=tr('python.cli.711897442fc5'))
    from .memory_cli import add_arguments as add_memory_arguments
    add_memory_arguments(parser)
    from .acp.cli import add_arguments
    add_arguments(parser)
    from .superagent_cli import add_arguments as add_superagent_arguments
    add_superagent_arguments(parser)
    return parser


def make_harness(args: argparse.Namespace) -> Harness:
    """按命令行参数构造 harness。"""
    overrides = {"enable_setup": bool(args.ui)}
    if args.cwd:
        overrides["cwd"] = Path(args.cwd).expanduser().resolve()
    if getattr(args, "language", None):
        overrides["language"] = args.language
    if args.model:
        overrides["model"] = args.model
    if args.reasoning:
        overrides["reasoning_effort"] = args.reasoning
    if args.no_shell:
        overrides["enable_shell"] = False
    if args.no_fs:
        overrides["enable_fs"] = False
    config = HarnessConfig.from_env(**overrides)
    return Harness(config)


async def run_once(harness: Harness, text: str) -> int:
    """执行一次性任务，并把流式进度打印到终端。"""
    session = harness.create_session()
    print(tr('python.cli.ee2d51c395ff', p0=f'{session.id}'))
    print(tr('python.cli.b1ae0718b591', p0=f'{harness.config.model}'))

    last_type = ""
    async for event in harness.stream(session, text):
        kind = event["type"]
        if kind == "assistant/delta":
            if event.get("delta_type") == "thinking":
                if last_type != "thinking":
                    print(tr('python.cli.20aa574b9f7f'), end="", flush=True)
                    last_type = "thinking"
                print(event.get("delta", ""), end="", flush=True)
            else:
                if last_type == "thinking":
                    print("\n")
                print(event.get("delta", ""), end="", flush=True)
                last_type = "text"
        elif kind == "tool/start":
            if last_type == "thinking":
                print("\n")
            args = event.get("input", {})
            preview = ", ".join(f"{k}={v!r}" for k, v in list(args.items())[:3])
            print(tr('python.cli.abe6c4ad7856', p0=f"{event['name']}", p1=f'{preview}'), flush=True)
            last_type = "tool"
        elif kind == "tool/output":
            if event.get("stdout"):
                print(event["stdout"], end="", flush=True)
            if event.get("stderr"):
                print(event["stderr"], end="", file=sys.stderr, flush=True)
        elif kind == "tool/end":
            status = tr('python.cli.28384d7afd2e') if event.get("is_error") else tr('python.cli.c0b3fbff51cc')
            content = (event.get("content") or "")[:400]
            print(f"[{status}] {event['name']} ({event.get('duration_ms', 0)}ms)")
            if content:
                print("  " + content.replace("\n", "\n  "), flush=True)
            last_type = "tool"
        elif kind == "todo/write":
            todos = event["todos"]
            if todos:
                done = sum(1 for t in todos if t["status"] == "completed")
                print(tr('python.cli.8bd3e163f491', p0=f'{done}', p1=f'{len(todos)}'))
                for t in todos:
                    mark = {"completed": "✔", "in_progress": "→", "pending": "·"}[t["status"]]
                    print(f"  {mark} {t['content']}")
                last_type = "todo"
        elif kind == "skill/loaded":
            print(tr('python.cli.e094bc6b2490', p0=f"{event.get('name')}"))
            last_type = "skill"
        elif kind == "error":
            print(tr('python.cli.e7b9ae548824', p0=f"{event.get('message')}"), file=sys.stderr)

    if last_type == "thinking":
        print("\n")
    print("\n" + "─" * 60)
    return 0


async def run_interactive(harness: Harness) -> int:
    """交互式对话循环。"""
    session = harness.create_session()
    print(tr('python.cli.4e113ccb6b5c', p0=f'{harness.config.model}', p1=f'{session.id}'))
    print(tr('python.cli.78cfbf5a0051'))

    while True:
        try:
            text = (await asyncio.to_thread(input, tr('python.cli.13192bada7ea'))).strip()
        except (EOFError, KeyboardInterrupt):
            print(tr('python.cli.f04dcd72d2a4'))
            return 0
        if not text:
            continue
        if text.lower() in ("exit", "quit", ":q"):
            print(tr('python.cli.06263c2d6ffc'))
            return 0
        print()
        await run_once_with_session(harness, session, text)
        print()


async def run_once_with_session(harness: Harness, session, text: str) -> None:
    """在已有会话上执行一轮，并把流式输出打印到终端。"""
    async for event in harness.stream(session, text):
        kind = event["type"]
        if kind == "assistant/delta" and event.get("delta_type") != "thinking":
            print(event.get("delta", ""), end="", flush=True)
        elif kind == "tool/start":
            print(tr('python.cli.6754285c3504', p0=f"{event['name']}"), flush=True)
        elif kind == "tool/output":
            if event.get("stdout"):
                print(event["stdout"], end="", flush=True)
            if event.get("stderr"):
                print(event["stderr"], end="", file=sys.stderr, flush=True)
        elif kind == "turn/end":
            print()


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。"""
    from harness.core.text_encoding import configure_cli_streams
    configure_cli_streams()
    from .app.config import DEFAULT_HOME
    from .core.language import current_language, resolve_language, saved_language

    preliminary = argparse.ArgumentParser(add_help=False)
    preliminary.add_argument('--language')
    known, _ = preliminary.parse_known_args(argv)
    preference = (known.language or saved_language(Path(os.environ.get('HARNESS_HOME', str(DEFAULT_HOME))).expanduser())
                  or os.environ.get('HARNESS_LANGUAGE', 'auto'))
    try:
        language = resolve_language(preference).effective_language
    except ValueError:
        language = resolve_language().effective_language
    token = current_language.set(language)
    try:
        return _main(argv)
    finally:
        current_language.reset(token)


def _main(argv: list[str] | None = None) -> int:
    """在选定语言上下文中解析参数并运行。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    from .superagent_cli import handle as handle_superagent
    superagent_result = handle_superagent(args, parser)
    if superagent_result is not None:
        return superagent_result
    if args.share:
        if not args.ui:
            parser.error(tr('cli.share_requires_ui'))
        from .web.lan import validate_lan_origin
        try:
            validate_lan_origin(args.share)
            import ipaddress
            if args.host != 'localhost' and not ipaddress.ip_address(args.host).is_loopback:
                raise ValueError(tr('web.lan.loopback_required'))
        except ValueError as exc:
            parser.error(str(exc))
    if args.list_external_agents or args.external_agent:
        if args.memory_export or args.memory_verify or args.memory_import or args.memory_scope or args.memory_session:
            parser.error(tr('cli.memory.conflict'))
        if args.ui or args.status or args.list_sessions or args.no_shell or args.no_fs or args.model or args.reasoning:
            parser.error(tr('acp.cli.conflicting_options'))
        from .acp.cli import handle
        return handle(args)
    if args.agent_command is not None or args.agent_arg is not None or args.agent_timeout != 600:
        parser.error(tr('acp.cli.agent_required'))
    from .memory_cli import handle as handle_memory
    memory_result = handle_memory(args, parser)
    if memory_result is not None:
        return memory_result
    try:
        harness = make_harness(args)
    except HarnessError as exc:
        print(tr('python.cli.063abdaa463d', p0=f'{exc}'), file=sys.stderr)
        return 2

    if args.status:
        import json

        print(json.dumps(harness.status(), ensure_ascii=False, indent=2))
        return 0

    if args.list_sessions:
        import json

        print(json.dumps(harness.list_sessions(), ensure_ascii=False, indent=2))
        return 0

    if args.ui:
        from .web.server import serve

        if args.share:
            from .web.lan import serve_lan
            serve_lan(harness, origin=args.share, host=args.host, port=args.port, dist=args.dist)
        else:
            serve(harness, host=args.host, port=args.port, dist=args.dist)
        return 0

    text = " ".join(args.prompt).strip()
    if text:
        return asyncio.run(run_once(harness, text))
    return asyncio.run(run_interactive(harness))


if __name__ == "__main__":
    raise SystemExit(main())
