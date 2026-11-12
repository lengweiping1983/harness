"""计划模式工具。

``exit_plan_mode`` **永远注册**，哪怕计划模式当前没开 —— 这是刻意的设计：
切换模式时发给模型的工具目录必须一字不变，否则 provider 侧的 prompt cache
会在每次模式切换时全部失效。工具在非计划模式下调用会直接报错。
"""

from __future__ import annotations

from harness.i18n import tr
from typing import Any
import asyncio

from ...app import plans

from ...app.plan_mode import (
    PLAN_TITLE_RE,
    PlanModeController,
    contains_sensitive_literal,
    first_heading,
)
from ...core.errors import ToolExecutionError
from ...interaction.questions import QuestionBroker, QuestionError, MAX_ASK_TIMEOUT
from ...llm.types import TextBlock
from ..registry import ToolDefinition, ToolRunContext, ToolResult

MAX_PLAN_CHARS = 64000


def build_exit_plan_mode_tool(
    controller: PlanModeController,
    questions: QuestionBroker | None,
) -> ToolDefinition:
    """构造 ``exit_plan_mode``。"""

    async def exit_plan_mode(args: Any, ctx: ToolRunContext) -> ToolResult:
        session = ctx.services.get("session") or controller.session_of(ctx.session_id)
        if session is None:
            raise ToolExecutionError(tr('python.plan_tools.bc41823fb625', p0=f'{ctx.session_id}'))
        if not controller.is_active(session):
            raise ToolExecutionError(
                tr('python.plan_tools.4c4abe9d0637')
            )

        if not isinstance(args, dict) or not isinstance(args.get("plan"), str):
            raise ToolExecutionError(tr("plan.arguments.planString"))
        plan = args["plan"].strip()
        if not plan:
            raise ToolExecutionError(tr('python.plan_tools.b04501287dad'))
        if len(plan) > MAX_PLAN_CHARS:
            raise ToolExecutionError(tr('python.plan_tools.8c3d1e553d3c'))
        if not PLAN_TITLE_RE.match(plan):
            raise ToolExecutionError(
                tr('python.plan_tools.2f5eb9fc5ef5')
            )
        if contains_sensitive_literal(plan):
            raise ToolExecutionError(tr('python.plan_tools.sensitive_plan'))
        if questions is None:
            await session.append('note', {'kind': 'plan_review_unavailable', 'turn': ctx.turn})
            raise ToolExecutionError(
                tr('python.plan_tools.a5d12155be2d')
            )

        approve_label = tr('ui.components.chat.blocks.QuestionCard.b168fdafe0f4')
        keep_label = tr('ui.components.chat.blocks.QuestionCard.b367cdfb55b5')
        title = first_heading(plan)
        async with controller.review_lock(session.id):
            plan_id = await plans.submit(session, plan, ctx.call_id)
            epoch = controller.review_epoch(session.id)
        async def notify():
            await session.bus.emit("plan/review", {"session_id": session.id, "plan_id": plan_id})
        async def close_review(reason):
            async with controller.review_lock(session.id):
                if any(record["id"] == plan_id and record["status"] == "pending" for record in plans.versions(session)):
                    await plans.decide(session, plan_id, "closed", reason)
            await notify()
        await notify()
        try:
            answers = await questions.ask(
                questions=[
                    {
                        "id": "plan-review",
                        "header": tr('python.plan_tools.738c5a68904e', p0=f'{title}')[:120],
                        "question": plan,
                        "options": [
                            {"id": "approve", "label": approve_label, "description": tr('python.plan_tools.0e48f19080f1')},
                            {"id": "keep-planning", "label": keep_label, "description": tr('python.plan_tools.968a086a588e')},
                        ],
                    }
                ],
                session_id=ctx.session_id,
                call_id=ctx.call_id,
                max_question_chars=MAX_PLAN_CHARS,
                timeout=MAX_ASK_TIMEOUT,
            )
        except asyncio.CancelledError:
            await close_review(tr('python.plan_tools.dd53f137985c'))
            raise
        except QuestionError as exc:
            await close_review(str(exc))
            # 超时 / 会话被取消 / 用户关掉了卡片：语义是"审批通道不可用"，
            # 必须留在计划模式，且绝不能被当成批准。
            raise ToolExecutionError(
                tr('python.plan_tools.fc30dc40d7c8', p0=f'{exc}')
            ) from exc

        async with controller.review_lock(session.id):
            answer = answers[0] if answers else {}
            selected = [str(s) for s in (answer.get("selected") or [])]
            selected_ids = [str(s) for s in (answer.get("selected_ids") or [])]
            custom = str(answer.get("custom") or "").strip()
            approved = (selected_ids == ["approve"] if selected_ids else selected == [approve_label]) and not custom
            current = plans.versions(session)[-1]
            if current["id"] != plan_id or current["status"] != "pending":
                return ToolResult(content=[TextBlock(text=tr('python.plan_tools.99487e2e17b3'))],
                                  data={"approved": False, "plan_id": plan_id, "feedback": tr('python.plan_tools.ad1559946536')})
            if epoch != controller.review_epoch(session.id) or not controller.is_active(session) or controller._parent_active(session.id):
                await plans.decide(session, plan_id, "closed", tr('python.plan_tools.cf0b8320ba17'))
                await notify()
                return ToolResult(content=[TextBlock(text=tr('python.plan_tools.6311f8f1707d'))],
                                  data={"approved": False, "plan_id": plan_id})
            if not approved:
                detail = custom or (tr('python.plan_tools.919c9760e29f')
                                    if ("keep-planning" in selected_ids or keep_label in selected)
                                    else tr('python.plan_tools.e3bc12801881'))
                text = (tr('python.plan_tools.0ac425b2b05a', p0=f'{detail}'))
                await plans.decide(session, plan_id, "revision_requested", detail)
                await notify()
                payload = {"approved": False, "plan_id": plan_id, "title": title,
                           "content_hash": current["content_hash"], "feedback": detail,
                           "turn": ctx.turn, "step": ctx.step}
                return ToolResult(content=[TextBlock(text=text)], display=payload, data=payload)

            await plans.decide(session, plan_id, "approved")
            await notify()
            # 审核锁覆盖验证、持久化决定和退出模式。
            await controller._set(session, False, narrate=False)
            text = tr('owned.plan_tools.c2f73319c24a')
            payload = {"approved": True, "plan_id": plan_id, "title": title,
                       "content_hash": current["content_hash"], "turn": ctx.turn, "step": ctx.step}
            return ToolResult(content=[TextBlock(text=text)], display=payload, data=payload)

    return ToolDefinition(
        schema={
                "description_key": 'python.plan_tools.ed87ba8de03b',
                "parameter_keys": {
                    ('properties', 'plan', 'description'): 'python.plan_tools.c99efc22e37a',
                },
            "name": "exit_plan_mode",
            "description": tr('python.plan_tools.ed87ba8de03b'),
            "parameters": {
                "type": "object",
                "properties": {
                    "plan": {
                        "type": "string",
                        "description": tr('python.plan_tools.c99efc22e37a'),
                    }
                },
                "required": ["plan"],
                "additionalProperties": False,
            },
        },
        execute=exit_plan_mode,
        concurrency_safe=False,
        timeout=None,
    )


__all__ = ["build_exit_plan_mode_tool"]
