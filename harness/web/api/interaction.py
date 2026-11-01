"""交互类接口：向 agent 提问的补答通道 + 计划模式开关。

运行中的问答可通过 HTTP 或 WebSocket 应答；模型执行不占用消息读循环。
刷新、重连后从待回答列表恢复卡片，问题生命周期事件触发实时更新。
"""

from __future__ import annotations

from harness.i18n import tr

from typing import Any

from ...core.errors import HarnessError, PersistenceError, SessionNotFoundError
from ...interaction.questions import QuestionError, QuestionUnavailableError
from ..errors import ApiError, BadRequest, Conflict
from ..responses import json_response
from ..router import Router


def build_router(ctx: Any) -> Router:
    router = Router()

    @router.get("/api/v1/questions")
    def questions(request: Any) -> Any:
        """列出尚未收到答案的提问（``session_id`` 可选，缺省列全部）。"""
        session_id = request.get("session_id")
        pending = ctx.harness.questions.pending(session_id)
        return json_response({"questions": pending})

    @router.post("/api/v1/questions/answer")
    def answer(request: Any) -> Any:
        payload = request.json()
        question_id = payload.get("question_id")
        if not isinstance(question_id, str) or not question_id.strip():
            raise BadRequest(tr('python.interaction.e25920e0b3d5'), code="question/missing-id")
        question_id = question_id.strip()
        try:
            result = ctx.call(
                ctx.harness.questions.answer_and_notify(
                    question_id, payload.get("answers")
                ),
                timeout=10.0,
            )
        except QuestionUnavailableError as exc:
            # 未知 / 过期 / 已回答 —— 都是"再来也没用"的冲突，而不是服务端故障。
            raise Conflict(str(exc), code="question/conflict") from exc
        except QuestionError as exc:
            raise BadRequest(str(exc), code='question/invalid-answer') from exc
        except ApiError:
            raise
        return json_response(result)

    @router.get("/api/v1/plan-mode")
    def plan_mode_get(request: Any) -> Any:
        session_id = request.get("session_id") or ""
        state = ctx.harness.plan_mode_state(session_id)
        return json_response({"plan_mode": state})

    @router.post("/api/v1/plan-mode")
    def plan_mode_post(request: Any) -> Any:
        payload = request.json()
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            raise BadRequest(tr('python.interaction.666aaba8bc94'), code="plan-mode/missing-session")
        active = payload.get("active")
        if not isinstance(active, bool):
            raise BadRequest(tr('python.interaction.1f2b7a2fd995'), code="plan-mode/bad-active")
        try:
            state = ctx.call(ctx.harness.set_plan_mode(session_id, active), timeout=10.0)
        except (PersistenceError, SessionNotFoundError):
            raise
        except HarnessError as exc:
            raise Conflict(exc.message, code="plan-mode/conflict") from exc
        return json_response(state)

    return router


__all__ = ["build_router"]
