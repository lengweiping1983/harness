"""用户提问工具。

「向用户提问」拆成两层：工具负责**阻塞等待**，UI provider 负责
**渲染表单 + 回传答案**。本文件只做第一层——它把参数交给
:class:`~harness.interaction.questions.QuestionBroker`，然后挂起当前
工具调用直到用户在界面上作答。

超时/取消都收敛到 :class:`QuestionError`，由注册表翻译成错误工具结果，
模型会看到一段遵循当前语言的明确提示，知道「用户没回答」而不是「工具坏了」。
"""

from __future__ import annotations

from harness.i18n import tr

from typing import Any

from ...interaction.questions import (
    DEFAULT_ASK_TIMEOUT,
    MAX_ASK_TIMEOUT,
    MAX_QUESTIONS,
    QuestionBroker,
    QuestionError,
)
from ...llm.types import TextBlock
from ..registry import ToolDefinition, ToolRunContext, ToolResult

#: 工具自身允许的最大等待秒数（比 broker 的硬上限略小，留出收尾余量）
MAX_TOOL_TIMEOUT = MAX_ASK_TIMEOUT

def _render_answers(questions: list[dict[str, Any]], answers: list[dict[str, Any]]) -> str:
    """把答案渲染成给模型看的紧凑文本。"""
    lines: list[str] = [tr('python.interaction_tools.2a43604a32e3')]
    by_id = {a.get("id"): a for a in answers}
    for question in questions:
        answer = by_id.get(question.get("id", "").strip(), {})
        selected = answer.get("selected") or []
        custom = (answer.get("custom") or "").strip()
        header = question.get("header") or question.get("question") or question.get("id")
        picked = tr('ui.common.list_separator').join(str(s) for s in selected) if selected else tr('python.interaction_tools.3c5045c728a5')
        if custom:
            picked = tr('python.interaction_tools.5536cab40d03', p0=f'{picked}', p1=f'{custom}')
        lines.append(tr('python.interaction_tools.answer_row', header=header, answer=picked))
    lines.append("")
    lines.append(tr('python.interaction_tools.f3b77dbc610f'))
    return "\n".join(lines)


def validate_question_choices(questions: list[Any]) -> None:
    """在挂起用户回合前要求建议选项，必要事实允许明确申请文本输入。"""
    for index, question in enumerate(questions):
        if not isinstance(question, dict):
            raise QuestionError(tr('python.questions.4275d52e5d2c', p0=index))
        # 模型不能把自述原因作为宿主权限判定依据呈现。
        operation = question.get('operation')
        if isinstance(operation, dict) and 'approval' in operation:
            raise QuestionError(tr('question.invalidOperation'))
        if question.get('input_type', 'choice') == 'text':
            reason = question.get('text_input_reason')
            if not isinstance(reason, str) or not reason.strip():
                raise QuestionError(tr('python.interaction_tools.text_reason_required', index=index + 1))
        elif question.get('input_type', 'choice') != 'choice' or not isinstance(question.get('options'), list) or len(question['options']) < 2:
            raise QuestionError(tr('python.interaction_tools.choices_required', index=index + 1))


def build_ask_user_question_tools(broker: QuestionBroker) -> list[ToolDefinition]:
    """构造 ``ask_user_question``，绑定到给定的 :class:`QuestionBroker`。"""

    async def ask_user_question(args: Any, ctx: ToolRunContext) -> ToolResult:
        raw = args.get("questions")
        if not isinstance(raw, list) or not raw:
            raise QuestionError(tr('python.interaction_tools.3ad6fb3d1fa1'))
        if len(raw) > MAX_QUESTIONS:
            raise QuestionError(tr('python.interaction_tools.0f8643002433', p0=f'{MAX_QUESTIONS}', p1=f'{len(raw)}'))

        validate_question_choices(raw)

        timeout = args.get("timeout")
        if timeout is None:
            wait = broker.default_timeout
        else:
            try:
                wait = float(timeout)
            except (TypeError, ValueError):
                raise QuestionError(tr('python.interaction_tools.e2f7cf3acbe0', p0=f'{timeout!r}')) from None
            wait = max(1.0, min(wait, MAX_TOOL_TIMEOUT))

        # 子代理（如Agent 团队主理人的规划阶段）在私有子会话里运行，而用户
        # 的界面订阅的是父会话房间。这里取运行时绑定的父会话标识作为
        # 事件路由目标，保证提问表单能出现在发起任务的会话中。
        # 多层子代理的直接父会话仍可能是私有会话，优先投递根会话。
        parent_session_id = None
        for key in ('root_session_id', 'parent_session_id'):
            candidate = ctx.services.get(key)
            if isinstance(candidate, str) and candidate.strip():
                parent_session_id = candidate.strip()
                break

        # 同一会话（含父子归属）同时只允许一组表单挂起：两组并存
        # 用户无法分辨先后，也避免父会话已有表单时子代理再挂一组。
        scope = parent_session_id or ctx.session_id
        if broker.pending(scope) or (scope != ctx.session_id and broker.pending(ctx.session_id)):
            raise QuestionError(
                tr('python.interaction_tools.30041d4b5cda')
            )
        if ctx.cancelled:
            raise QuestionError(tr('python.interaction_tools.96f7f225bc8c'))

        answers = await broker.ask(
            questions=raw,
            session_id=ctx.session_id,
            parent_session_id=parent_session_id,
            call_id=ctx.call_id,
            timeout=wait,
        )

        data = {"answers": answers}
        return ToolResult(
            content=[TextBlock(text=_render_answers(raw, answers))],
            display=data,
            data=data,
        )

    return [
        ToolDefinition(
            schema={
                "description_key": 'python.interaction_tools.bf27aec6713e',
                "parameter_keys": {
                    ('properties', 'questions', 'description'): 'python.interaction_tools.9803e9c9344a',
                    ('properties', 'questions', 'items', 'properties', 'id', 'description'): 'python.interaction_tools.3da9094e20fd',
                    ('properties', 'questions', 'items', 'properties', 'question', 'description'): 'python.interaction_tools.51e95b38ad4b',
                    ('properties', 'questions', 'items', 'properties', 'header', 'description'): 'python.interaction_tools.105ceb81f666',
                    ('properties', 'questions', 'items', 'properties', 'options', 'description'): 'python.interaction_tools.09288124c4ca',
                    ('properties', 'questions', 'items', 'properties', 'input_type', 'description'): 'python.interaction_tools.input_type',
                    ('properties', 'questions', 'items', 'properties', 'text_input_reason', 'description'): 'python.interaction_tools.text_reason',
                    ('properties', 'questions', 'items', 'properties', 'multi_select', 'description'): 'python.interaction_tools.4f08ecda5ece',
                    ('properties', 'timeout', 'description'): ('python.interaction_tools.8dbc9d449fef', {'p0': f'{DEFAULT_ASK_TIMEOUT:g}', 'p1': f'{MAX_ASK_TIMEOUT:g}'}),
                },
                "name": "ask_user_question",
                "description": tr('python.interaction_tools.bf27aec6713e'),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "questions": {
                            "type": "array",
                            "description": tr('python.interaction_tools.9803e9c9344a'),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {
                                        "type": "string",
                                        "description": tr('python.interaction_tools.3da9094e20fd'),
                                    },
                                    "question": {
                                        "type": "string",
                                        "description": tr('python.interaction_tools.51e95b38ad4b'),
                                    },
                                    "header": {
                                        "type": "string",
                                        "description": tr('python.interaction_tools.105ceb81f666'),
                                    },
                                    "options": {
                                        "type": "array",
                                        "description": tr('python.interaction_tools.09288124c4ca'),
                                        "minItems": 2,
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "label": {"type": "string"},
                                                "description": {"type": "string"},
                                            },
                                            "required": ["label"],
                                            "additionalProperties": False,
                                        },
                                    },
                                    "input_type": {
                                        "type": "string", "enum": ["choice", "text"],
                                        "description": tr('python.interaction_tools.input_type'),
                                    },
                                    "text_input_reason": {
                                        "type": "string",
                                        "description": tr('python.interaction_tools.text_reason'),
                                    },
                                    "multi_select": {
                                        "type": "boolean",
                                        "description": tr('python.interaction_tools.4f08ecda5ece'),
                                    },
                                },
                                "required": ["id", "question"],
                                "additionalProperties": False,
                            },
                        },
                        "timeout": {
                            "type": "number",
                            "description": (
                                tr('python.interaction_tools.8dbc9d449fef', p0=f'{DEFAULT_ASK_TIMEOUT:g}', p1=f'{MAX_ASK_TIMEOUT:g}')
                            ),
                        },
                    },
                    "required": ["questions"],
                    "additionalProperties": False,
                },
            },
            execute=ask_user_question,
            # 向用户提问只读取用户输入，不写工作区；声明为 read 才能在
            # 只读任务（如Agent 团队主理人的规划/汇总阶段）中继续可用。
            effect="read",
            concurrency_safe=False,
            timeout=MAX_TOOL_TIMEOUT + 30.0,
        )
    ]


__all__ = ["build_ask_user_question_tools", "MAX_TOOL_TIMEOUT"]
