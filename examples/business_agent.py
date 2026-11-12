"""业务 Agent 集成示例：把 Harness 变成一个“代码审查助手”。

这个文件演示三种典型扩展点：

1. **注册业务工具** —— 让 agent 能查 git 历史、跑测试；
2. **替换人格** —— 定义它是谁、怎么说话；
3. **追加领域规则** —— 注入团队规范。

运行：

.. code-block:: sh

    export OPENROUTER_API_KEY=sk-or-v1-...
    python examples/business_agent.py /path/to/your/project
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# 允许在没有安装包的情况下直接运行本文件
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import Harness, HarnessConfig
from harness.app.git_process import run_git
from harness.core.cleanup import join_cleanup
from harness.core.language import current_language, resolve_language
from harness.i18n import tr
from harness.tools.owned_process import spawn_owned
from harness.tools.process_output import cleanup_process, collect_process

async def main(project_dir: str) -> int:
    """构造并运行一个代码审查 Agent。"""
    root = Path(project_dir).expanduser().resolve()

    # -- 1) 装配 harness -------------------------------------------------
    config = HarnessConfig.from_env(cwd=root, enable_shell=True, max_retries=3)
    token = current_language.set(resolve_language(config.language).effective_language)
    try:
        config.agent_name = tr("example.review.name")
        config.agent_description = tr("example.review.description")
        harness = Harness(config)
        try:
            return await _review(harness, root)
        finally:
            await harness.close()
    finally:
        current_language.reset(token)


async def _command(argv: list[str], ctx) -> tuple[bytes, bytes, int]:
    """使用所属进程执行参数数组，取消或超时仍完成资源回收。"""
    process = await spawn_owned(argv, cwd=str(ctx.cwd), env=dict(os.environ))
    stdout, stderr, code, timed_out = await collect_process(process, 110, ctx)
    if timed_out:
        await cleanup_process(process, ctx)
        raise TimeoutError(argv[0])
    return stdout, stderr, code


async def _review(harness: Harness, root: Path) -> int:
    """注册当前语言的审查工具并消费当前流事件。"""
    # -- 2) 替换人格（complete 片段会覆盖默认系统提示） -------------------
    harness.set_persona(
        tr("example.review.persona")
    )

    # -- 3) 追加领域规则 -------------------------------------------------
    harness.add_prompt(
        "review:team-rules",
        tr("example.review.rules"),
        order=1500,
    )

    # -- 4) 注册业务工具 -------------------------------------------------
    @harness.tool(
        "git_log",
        tr("example.review.git.description"),
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": tr("example.review.git.path")},
                "count": {"type": "integer", "minimum": 1, "maximum": 50, "description": tr("example.review.git.count")},
            },
            "required": ["path"],
        },
        effect="read",
    )
    async def git_log(args: dict, ctx) -> dict:
        """包装 git log，把结果转成结构化数据。"""
        count = args.get("count", 10)
        result = await join_cleanup(asyncio.create_task(asyncio.to_thread(
            run_git, Path(ctx.cwd), "--literal-pathspecs", "log", f"-{count}",
            "--format=%h|%an|%ad|%s", "--date=short", "--", args["path"],
        )))
        if result.returncode != 0:
            return {"error": tr("example.review.git.failed"), "detail": result.stderr.decode(errors="replace")[:500]}
        commits = []
        for line in result.stdout.decode(errors="replace").splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append({"hash": parts[0][:8], "author": parts[1], "date": parts[2], "subject": parts[3]})
        return {"path": args["path"], "count": len(commits), "commits": commits}

    @harness.tool(
        "run_tests",
        tr("example.review.tests.description"),
        {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": tr("example.review.tests.target")},
            },
        },
        effect="execute",
        approval_required=True,
    )
    async def run_tests(args: dict, ctx) -> dict:
        """跑测试（这里用 pytest；换成你自己的命令）。"""
        target = args.get("target") or "."
        stdout, stderr, exit_code = await _command([sys.executable, "-m", "pytest", "-q", "--", target], ctx)
        output = "\n".join((stdout + stderr).decode(errors="replace").splitlines()[-30:])
        return {"target": target, "exit_code": exit_code, "output": output}

    # -- 5) 跑一轮对话 ---------------------------------------------------
    print(tr("example.review.project", root=root))
    print(tr("example.review.tools", names=", ".join(harness.status()["tools"])) + "\n")

    session = harness.create_session(title=tr("example.review.session", name=root.name))

    question = tr("example.review.question")

    failed = False
    async for event in harness.stream(session, question):
        kind = event["type"]
        if kind == "assistant/delta":
            if event.get("delta_type") == "thinking":
                print(f"\n\033[90m[{tr('example.review.thinking')}] {event['delta']}\033[0m", end="", flush=True)
            else:
                print(event["delta"], end="", flush=True)
        elif kind == "tool/start":
            print(f"\n\033[36m[{tr('example.review.tool')}] {event['name']}({event.get('input')})\033[0m", flush=True)
        elif kind == "tool/end":
            mark = "✗" if event.get("is_error") else "✓"
            print(f"\033[90m  {mark} {event['name']} ({event.get('duration_ms', 0)}ms)\033[0m", flush=True)
        elif kind == "todo/write":
            todos = event["todos"]
            done = sum(1 for t in todos if t["status"] == "completed")
            print(f"\n\033[33m[{tr('example.review.tasks')}] {done}/{len(todos)}\033[0m")
            for t in todos:
                icon = {"completed": "✔", "in_progress": "→", "pending": "·"}[t["status"]]
                print(f"  {icon} {t['content']}")
        elif kind == "error":
            failed = True
            print(f"\n\033[31m[{tr('example.review.error')}] {event.get('message')}\033[0m")

    print("\n\n" + tr("example.review.saved", id=session.id))
    print("python -m harness.cli --list-sessions")
    return int(failed)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        current_language.set(resolve_language(os.environ.get("HARNESS_LANGUAGE", "auto")).effective_language)
        print(tr("example.review.usage"))
        raise SystemExit(1)
    raise SystemExit(asyncio.run(main(sys.argv[1])))
