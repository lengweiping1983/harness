"""用户问答通道。

「向用户提问」被抽象成一个 ``userQuestions`` 服务：工具调用后
**阻塞**等待 UI provider 返回答案，再把答案当作普通工具结果喂回 agent 循环。

本模块是它的 Python 形态：

.. code-block:: text

    ask_user_question 工具 ──> QuestionBroker.ask()  ──> 生成 question_id
                                        │                      │
                                        │                 挂起 Future
                                        │                      │
                                   UI 轮询/WebSocket ──> QuestionBroker.answer()
```

超时、客户端断连、异常应答都收敛到同一个出口：``Future`` 只会被 resolve
一次（正常应答或超时异常），保证工具调用不会永久挂死。
"""

from __future__ import annotations

from harness.i18n import tr

import asyncio
import logging
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..core.events import EventBus
from .permission_operation import normalized_operation

logger = logging.getLogger(__name__)

#: 单次提问默认等待秒数
DEFAULT_ASK_TIMEOUT = 120.0
#: 单次提问最大等待秒数
MAX_ASK_TIMEOUT = 3600.0

#: 问题字段最大长度，防止模型塞超长文本撑爆 UI
MAX_HEADER_CHARS = 120
MAX_QUESTION_CHARS = 4000
MAX_OPTION_CHARS = 200
MAX_QUESTIONS = 10


class QuestionError(RuntimeError):
    """问答通道内部的可预期错误（映射为工具错误结果）。"""


class QuestionUnavailableError(QuestionError):
    """问题已关闭或不存在，不能通过修正答案重试。"""


@dataclass(slots=True)
class UserQuestion:
    """一条待用户回答的问题。"""

    question_id: str
    session_id: str
    call_id: str
    questions: list[dict[str, Any]]
    created_at: float
    future: "asyncio.Future[list[dict[str, Any]]]" = field(repr=False)
    #: 事件路由目标。子代理在私有子会话里提问时指向用户所在的祖先会话，
    #: 使表单渲染到发起任务的会话；与 ``session_id`` 相同时留空。
    parent_session_id: str | None = None
    answer_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    answering: bool = field(default=False, repr=False)

    @property
    def route_session_id(self) -> str:
        """事件广播时应使用的会话标识。

        事件总线的约定是「载荷里的 ``session_id`` 即订阅房间」，父会话仅
        在缺失时才兜底（见 ``web/context.py`` 的事件桥接）：因此子代理提问
        必须把父会话写到 ``session_id`` 才可能送达界面，而不是另挂一个字段。
        """
        return self.parent_session_id or self.session_id

    @property
    def revision(self) -> str:
        """问题实例与不可变内容共同确定审批版本。"""
        value = [self.question_id, self.route_session_id, self.call_id, self.questions]
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """转成 UI 可渲染的载荷。

        ``session_id`` 输出路由目标（子代理提问时即父会话），供事件桥接
        选房间；真正发起提问的会话保留在 ``asker_session_id``，便于诊断
        与同父归属判定。
        """
        payload: dict[str, Any] = {
            "question_id": self.question_id,
            "revision": self.revision,
            "session_id": self.route_session_id,
            "call_id": self.call_id,
            "created_at": self.created_at,
            "questions": self.questions,
        }
        if self.parent_session_id:
            payload["parent_session_id"] = self.parent_session_id
            payload["asker_session_id"] = self.session_id
        return payload


def _normalize_questions(raw: Any, *, max_question_chars: int = MAX_QUESTION_CHARS) -> list[dict[str, Any]]:
    """校验并规范化模型传来的问题列表。

    约束：``id`` / ``question`` 必填，``header`` / ``options`` /
    ``multi_select`` 可选；``additionalProperties: true`` 允许模型透传额外字段。
    """
    if type(max_question_chars) is not int or not 1 <= max_question_chars <= 64000:
        raise QuestionError(tr('python.questions.3397a9bfa929'))
    if not isinstance(raw, list) or not raw:
        raise QuestionError(tr('python.questions.3ad6fb3d1fa1'))
    if len(raw) > MAX_QUESTIONS:
        raise QuestionError(tr('python.questions.ad67b183bbbf', p0=f'{MAX_QUESTIONS}', p1=f'{len(raw)}'))

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise QuestionError(tr('python.questions.4275d52e5d2c', p0=f'{index}'))
        qid = str(entry.get("id", "") or "").strip()
        if not qid:
            raise QuestionError(tr('python.questions.bd80942ce44a', p0=f'{index}'))
        if qid in seen:
            raise QuestionError(tr('python.questions.41e7727751b6', p0=f'{qid!r}'))
        seen.add(qid)

        text = str(entry.get("question", "") or "").strip()
        if not text:
            raise QuestionError(tr('python.questions.35f1d64f24cd', p0=f'{index}'))

        item: dict[str, Any] = {"id": qid, "question": text[:max_question_chars]}
        operation = entry.get('operation')
        if operation is not None:
            normalized_value = normalized_operation(operation)
            if qid != 'permission' or normalized_value is None:
                raise QuestionError(tr('question.invalidOperation'))
            item['operation'] = normalized_value

        header = entry.get("header")
        if header is not None and str(header).strip():
            item["header"] = str(header).strip()[:MAX_HEADER_CHARS]

        options = entry.get("options")
        if options is not None:
            if not isinstance(options, list):
                raise QuestionError(tr('python.questions.dfb3b829500b', p0=f'{index}'))
            norm_options: list[dict[str, Any]] = []
            option_labels: set[str] = set()
            option_ids: set[str] = set()
            for opt_index, option in enumerate(options):
                if not isinstance(option, dict):
                    raise QuestionError(tr('python.questions.2ed22af576cc', p0=f'{index}', p1=f'{opt_index}'))
                label = str(option.get("label", "") or "").strip()
                if not label:
                    raise QuestionError(
                        tr('python.questions.77e7d3939c16', p0=f'{index}', p1=f'{opt_index}')
                    )
                norm_option: dict[str, Any] = {"label": label[:MAX_OPTION_CHARS]}
                if norm_option['label'] in option_labels:
                    raise QuestionError(tr('python.questions.8e07bf75b368', p0=f'{qid!r}'))
                option_labels.add(norm_option['label'])
                if "id" in option:
                    option_id = option["id"]
                    if not isinstance(option_id, str) or not option_id.strip() or len(option_id) > MAX_OPTION_CHARS or option_id in option_ids:
                        raise QuestionError(tr('question.invalidOptionId', p0=index, p1=opt_index, p2=MAX_OPTION_CHARS))
                    norm_option["id"] = option_id
                    option_ids.add(option_id)
                description = option.get("description")
                if description is not None and str(description).strip():
                    norm_option["description"] = str(description).strip()
                norm_options.append(norm_option)
            if norm_options:
                item["options"] = norm_options

        multi = entry.get("multi_select")
        if multi is not None:
            if not isinstance(multi, bool):
                raise QuestionError(tr('python.questions.343f016041b5', p0=f'{index}'))
            item["multi_select"] = multi

        normalized.append(item)
    return normalized


def normalize_answers(
    questions: list[dict[str, Any]], raw: Any
) -> list[dict[str, Any]]:
    """校验 UI 回传的答案，补齐未回答的问题并回显原始 id。

    UI 侧可能只回答了部分问题，未回答的记为空选项，
    即 ``selected: string[]`` 语义（数组可以为空）。
    """
    if not isinstance(raw, list):
        raise QuestionError(tr('python.questions.6000d6cb0e47'))
    known = {q['id']: q for q in questions}
    by_id: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise QuestionError(tr('python.questions.944eaec3c100', p0=f'{index}'))
        raw_id = entry.get('id')
        if not isinstance(raw_id, str) or not raw_id.strip():
            raise QuestionError(tr('python.questions.85941a4ac624', p0=f'{index}'))
        aid = raw_id.strip()
        if aid not in known:
            raise QuestionError(tr('python.questions.01f428413c75', p0=f'{aid}'))
        if aid in by_id:
            raise QuestionError(tr('python.questions.dd1d58cb9e39', p0=f'{aid}'))
        selected = entry.get("selected")
        if not isinstance(selected, list) or not all(isinstance(value, str) for value in selected):
            raise QuestionError(tr('python.questions.69839a7cf60b', p0=f'{aid!r}'))
        if len(set(selected)) != len(selected):
            raise QuestionError(tr('python.questions.cacd3aff4803', p0=f'{aid!r}'))
        question = known[aid]
        if not question.get('multi_select', False) and len(selected) > 1:
            raise QuestionError(tr('python.questions.4b31c6072b8e', p0=f'{aid!r}'))
        options = question.get('options', [])
        labels = {option['label'] for option in options}
        option_ids = {option['id']: option for option in options if isinstance(option.get('id'), str)}
        if any(value not in labels and value not in option_ids for value in selected):
            raise QuestionError(tr('python.questions.235f35846b13', p0=f'{aid!r}'))
        answer: dict[str, Any] = {"id": aid, "selected": list(selected)}
        # 保留 UI 的显示文本，同时提供稳定的机器选项身份；没有 id 的
        # 普通问答继续使用原有答案结构。
        if option_ids:
            answer['selected_ids'] = [
                value if value in option_ids else next(
                    option['id'] for option in options if option['label'] == value and 'id' in option)
                for value in selected
            ]
        custom = entry.get("custom")
        if custom is not None and not isinstance(custom, str):
            raise QuestionError(tr('python.questions.f8de5b33aae8', p0=f'{aid!r}'))
        if custom is not None and custom.strip():
            answer["custom"] = custom
        by_id[aid] = answer

    return [by_id.get(q["id"], {"id": q["id"], "selected": []}) for q in questions]


class QuestionBroker:
    """挂起 / 恢复 ``ask_user_question`` 调用的中转站。

    :param bus: 事件总线；每次提问会广播 ``user_question/ask``，
                每次应答会广播 ``user_question/answered``，供 UI 实时渲染。
    :param default_timeout: 未显式指定时的等待秒数。
    """

    def __init__(self, bus: EventBus | None = None, *, default_timeout: float = DEFAULT_ASK_TIMEOUT) -> None:
        self.bus = bus
        self.default_timeout = default_timeout
        # 问题标识 question_id 到 UserQuestion 的映射
        self._pending: dict[str, UserQuestion] = {}
        self._answer_tasks: set[asyncio.Task] = set()

    # -- 查询 ------------------------------------------------------------
    def pending(self, session_id: str | None = None) -> list[dict[str, Any]]:
        """列出尚未收到答案的问题（UI 断连重连后可据此补渲染）。

        传入会话标识时同时匹配提问会话与事件路由目标：子代理在私有子会话
        里代父会话提问时，路由目标即父会话，界面按父会话查询也能看到这条
        待答问题。
        """
        items = list(self._pending.values())
        if session_id is not None:
            items = [q for q in items if q.session_id == session_id or q.parent_session_id == session_id]
        return [q.to_dict() for q in items]

    # -- 提问 ------------------------------------------------------------
    async def ask(
        self,
        *,
        questions: Any,
        session_id: str,
        parent_session_id: str | None = None,
        call_id: str = "",
        timeout: float | None = None,
        max_question_chars: int = MAX_QUESTION_CHARS,
    ) -> list[dict[str, Any]]:
        """挂起本次提问，阻塞到 UI 应答或超时。

        :param parent_session_id: 提问方在私有子会话中运行时的祖先路由会话标识。
            提供且不同于 ``session_id`` 时，广播载荷的 ``session_id`` 会被
            改写为该父会话（事件总线按载荷里的 ``session_id`` 选订阅房间），
            使表单送达到发起任务的会话；原提问会话保留在 ``asker_session_id``。
        :returns: 与入参 ``questions`` 一一对应的答案列表。
        :raises QuestionError: 参数非法，或等待超时 / 被取消。
        """
        normalized = _normalize_questions(questions, max_question_chars=max_question_chars)
        wait = self.default_timeout if timeout is None else float(timeout)
        wait = max(1.0, min(wait, MAX_ASK_TIMEOUT))

        route = parent_session_id if isinstance(parent_session_id, str) else None
        route = route.strip() if route else None
        if route == session_id:
            route = None

        question_id = f"q_{uuid.uuid4().hex[:12]}"
        loop = asyncio.get_running_loop()
        future: "asyncio.Future[list[dict[str, Any]]]" = loop.create_future()
        record = UserQuestion(
            question_id=question_id,
            session_id=session_id,
            call_id=call_id,
            questions=normalized,
            created_at=loop.time(),
            future=future,
            parent_session_id=route,
        )
        self._pending[question_id] = record

        try:
            # 广播也可能挂起或被取消，必须与等待答案共用清理出口。
            if self.bus is not None:
                await self.bus.emit("user_question/ask", record.to_dict())
            return await asyncio.wait_for(future, timeout=wait)
        except asyncio.TimeoutError:
            raise QuestionError(
                tr('python.questions.013379f6e560', p0=f'{wait:g}')
            ) from None
        finally:
            self._pending.pop(question_id, None)
            if not future.done():
                future.cancel()
            if self.bus is not None:
                try:
                    await self.bus.emit(
                        "user_question/closed",
                        {"question_id": question_id, "session_id": record.route_session_id},
                    )
                except Exception:  # pragma: no cover - 事件总线自身异常
                    logger.exception(tr('log.8dd00348bc17'))

    # -- 应答 ------------------------------------------------------------
    def answer(self, question_id: str, answers: Any) -> dict[str, Any]:
        """提交答案并唤醒对应的挂起调用。

        :returns: ``{"question_id": ..., "resolved": bool}``；``resolved``
                  为 ``False`` 表示该问题已超时/已应答过。
        :raises QuestionError: 问题 id 不存在或答案结构非法。
        """
        record = self._pending.get(str(question_id or "").strip())
        if record is None:
            raise QuestionUnavailableError(
                tr('python.questions.d0ca78bb4b47', p0=f'{question_id!r}')
            )
        try:
            resolved = normalize_answers(record.questions, answers)
        except QuestionError:
            # 非法答案不唤醒调用方，让 UI 有机会重试。
            raise
        if record.future.done() or record.answering:
            return {"question_id": record.question_id, "resolved": False}
        record.future.set_result(resolved)
        return {"question_id": record.question_id, "resolved": True}

    async def answer_and_notify(
        self, question_id: str, answers: Any, *, session_id: str | None = None,
        before_resolve: Callable[[UserQuestion, list[dict[str, Any]]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """串行接受同一问题的答案，持久提交成功后再唤醒工具调用。

        可选提交钩子由业务层完成权限与日志提交。钩子失败保留待答问题；
        等待期间取消或超时不会恢复任务，已记录的决定也不代表工具执行成功。
        会话范围按公开路由匹配，不能凭子会话标识跨越父会话权限边界。
        """
        # HTTP 等待者取消不能中断已经接收的决定提交；问题自身的取消仍有效。
        task = asyncio.create_task(self._commit_answer(question_id, answers,
            session_id=session_id, before_resolve=before_resolve))
        self._answer_tasks.add(task)
        task.add_done_callback(self._answer_finished)
        return await asyncio.shield(task)

    def _answer_finished(self, task: asyncio.Task) -> None:
        """保留运行引用并读取异常，避免断开的请求产生未处理任务告警。"""
        self._answer_tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def _commit_answer(self, question_id, answers, *, session_id, before_resolve):
        """完成一次有独立生命周期的决定提交与广播。"""
        record = self._pending.get(str(question_id or "").strip())
        if record is None or session_id is not None and record.route_session_id != session_id:
            raise QuestionUnavailableError(tr('python.questions.d0ca78bb4b47', p0=f'{question_id!r}'))
        async with record.answer_lock:
            if record.future.done():
                return {"question_id": record.question_id, "resolved": False}
            normalized = normalize_answers(record.questions, answers)
            record.answering = True
            try:
                if before_resolve is not None:
                    await before_resolve(record, normalized)
                if record.future.done():
                    return {"question_id": record.question_id, "resolved": False}
                record.future.set_result(normalized)
            finally:
                record.answering = False
        result = {"question_id": record.question_id, "resolved": True}
        if self.bus is not None:
            payload: dict[str, Any] = {
                "question_id": record.question_id,
                "session_id": record.route_session_id,
                "answers": normalized,
            }
            if record.parent_session_id:
                payload["parent_session_id"] = record.parent_session_id
                payload["asker_session_id"] = record.session_id
            await self.bus.emit("user_question/answered", payload)
        return result

    def cancel_session(self, session_id: str) -> int:
        """中断某会话下所有挂起提问（会话取消 / 删除时调用）。

        同时覆盖在私有子会话里代父会话提问的情况，避免父回合取消后
        子代理的挂起表单仍占着工具槽位等到超时。
        """
        count = 0
        for record in list(self._pending.values()):
            if record.session_id != session_id and record.parent_session_id != session_id:
                continue
            if record.future.done():
                continue
            record.future.set_exception(
                QuestionError(tr('python.questions.bf4e5e7e4699'))
            )
            count += 1
        return count


__all__ = [
    "QuestionBroker",
    "QuestionError",
    "UserQuestion",
    "normalize_answers",
    "DEFAULT_ASK_TIMEOUT",
    "MAX_ASK_TIMEOUT",
]
