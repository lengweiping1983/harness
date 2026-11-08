"""运行时状态查询：常驻终端 / 子代理 / 后台作业。

这三个注册表本来就常驻在 :class:`~harness.app.harness.Harness` 上，并且各自
已经提供了只读快照原语 —— 缺的只是一个 **HTTP 出口**。没有出口的时候，模型
可以 ``terminal_open`` 起一个常驻 PTY、可以 ``subagent`` 派出子代理，但用户
在界面上只能从状态栏瞥见一个"6 子代理"的计数：既看不到谁在跑、跑了多久、卡
在哪一步，也看不到终端里正在滚什么输出。这类"模型能做、人看不见"的状态在
成熟 harness 里一定是独立面板 —— 可观测性本身就是功能，一个不敢给人看的后
台任务，用户就只能靠猜。

两个设计要点：

* **读一律调度到事件循环上**。HTTP 处理器活在 ``ThreadingHTTPServer`` 的
  工作线程，而这些注册表由 loop 上的协程改。跨线程遍历 dict 会在扩容/删除的
  瞬间抛 ``RuntimeError: dictionary changed size during iteration``，而且只在
  高并发下偶发，极难复现。这里统一走 :meth:`WebContext.call` 搭桥，读写因此
  严格串行在同一个线程上。
* **按会话隔离，且会话是可选参数**。终端与作业都以 owner（= 会话 id）分区，
  子代理按父会话过滤。带 ``session_id`` 时返回该会话的运行时状态；不带时返回
  全局汇总，供设置面板总览。
"""

from __future__ import annotations

from harness.i18n import tr

from typing import Any

from ..errors import BadRequest, NotFound
from ..responses import json_response
from ..router import Router
from ..query import RUNTIME_QUERY, TERMINAL_OUTPUT_QUERY, read_query
from ...subagent.archive import subagent_summary


def build_router(ctx: Any) -> Router:
    """注册运行时状态路由。``ctx`` 是 :class:`~harness.web.context.WebContext`。"""
    router = Router()

    def collect(session_id: str | None) -> dict[str, Any]:
        """在事件循环上取一份三类运行时状态的合并快照。"""

        async def _read() -> dict[str, Any]:
            terminals = ctx.harness.terminals
            jobs = ctx.harness.jobs
            subagents = ctx.harness.subagents

            if session_id is None:
                terminal_rows = terminals.list_all()
            else:
                terminal_rows = terminals.list(session_id)
            job_page = await jobs.history_page(session_id)

            page = subagents.history_page(session_id, include_descendants=True)
            rows = {row['agent_id']: row for row in page['agents']}
            # 即使结果归档很大，执行或收尾尚未结束的句柄仍保持可见。
            for state in subagents.list_states():
                if not state.settled and (session_id is None or state.parent_session_id == session_id or state.root_session_id == session_id):
                    rows[state.id] = subagent_summary(state)

            return {
                "sessionId": session_id,
                "terminals": terminal_rows,
                "subagents": list(rows.values()),
                "subagents_has_more": page['has_more'],
                "subagents_next_before": page['next_before'],
                "jobs": job_page['jobs'],
                "jobs_has_more": job_page['has_more'],
                "jobs_next_before": job_page['next_before'],
                **({'jobs_history_error': job_page['history_error']} if job_page.get('history_error') else {}),
            }

        return ctx.call(_read())

    @router.get("/api/v1/runtime")
    def runtime(request: Any) -> Any:
        """一次拉齐常驻终端 / 子代理 / 后台作业。

        刻意合成一个端点而不是三个：右栏三个面板会同时挂载，分三次请求既多
        三次往返，又可能出现"终端已经在了但子代理还没到"的中间态。快照不带
        缓存头 —— 这些状态每秒都在变，ETag 在这里只会制造无谓的 304 往返。
        """
        query = read_query(request, RUNTIME_QUERY)
        return json_response(
            collect(query.get('session_id')),
            accept_encoding=request.header("Accept-Encoding"),
        )

    @router.get("/api/v1/runtime/terminals/{terminal_id}/output")
    def terminal_output(request: Any, terminal_id: str) -> Any:
        """按行窗口读某个常驻终端的滚动缓冲。

        终端输出可能有几 MB，前端不能一次全拿。这里返回 ``totalLines`` /
        ``lineBegin`` / ``lineEnd`` / ``truncated``，前端按同样的游标协议做
        增量拉取 —— 与 terminal_read 工具是同一套语义，前端读到的行号能和
        模型看到的对得上。
        """
        query = read_query(request, TERMINAL_OUTPUT_QUERY)
        session_id = query.get('session_id')
        offset, count = query['offset'], query['count']

        async def _read() -> dict[str, Any]:
            service = ctx.harness.terminals
            owner = session_id
            if owner is None:
                # 没带会话就回退到"唯一拥有者"，把 /output 的调用面收窄到
                # 单会话场景；跨会话时宁可直接报错，也不要猜一个可能读错
                # 别人终端的 owner。
                owners = service.owners()
                if len(owners) != 1:
                    raise BadRequest(
                        tr('python.runtime.7193fcfd72f4'),
                        code="missing-session",
                    )
                resolved = next(iter(owners))
            else:
                resolved = owner

            try:
                terminal = service.expect_owned(resolved, terminal_id)
            except LookupError as exc:
                raise NotFound(
                    tr('python.runtime.4ca641a05293', p0=f'{terminal_id}'), code="terminal-not-found"
                ) from exc

            tail = query['tail']
            window = terminal.peek(count) if tail else terminal.read(offset, count)
            if tail:
                window['terminal'] = terminal.snapshot()
            window["sessionId"] = resolved
            window["session"] = terminal.status()
            return window

        return json_response(
            ctx.call(_read()),
            accept_encoding=request.header("Accept-Encoding"),
        )

    return router


__all__ = ["build_router"]