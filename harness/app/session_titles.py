"""新建网页对话的独立标题生成，不占用执行轮次。"""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
from uuid import uuid4

from ..core.cleanup import join_cleanup, preserve_cleanup_errors, raise_cleanup_errors
from ..core.language import current_language, resolve_language
from ..i18n import tr
from ..llm.types import SystemMessage, UserMessage
from ..session.events import user_message_text
from ..session.request_traces import request_observer
from ..tools.processes import ProcessCleanupError

logger = logging.getLogger(__name__)
MAX_TITLE_CHARS = 50


def provisional_title(text: str) -> str:
    """模式命令不进入名称，临时标题按码点截短。"""
    text = re.sub(r'^/(?:plan|goal)(?:\s+|$)', '', text.strip())
    return ' '.join(text.split())[:MAX_TITLE_CHARS]


class SessionTitles:
    def __init__(self, harness, *, timeout: float = 30):
        self.harness = harness
        self.timeout = timeout
        self.tasks: dict[str, asyncio.Task] = {}
        self.errors: list[BaseException] = []
        self.closed = False
        harness.bus.on('session/append', self.on_append)

    def on_append(self, session, event):
        """只接受新网页对话的首次人工任务，历史载入不触发请求。"""
        if (self.closed or event.type != 'user/message'
                or event.data.get('source', 'user') not in {'user', 'goal-command'}
                or session.header.title or session.header.metadata.get('auto_title') != 'awaiting'):
            return
        text = user_message_text(event.data)
        fallback = provisional_title(text)
        if not fallback:
            fallback = next((provisional_title(item.get('name', '')) for item in event.data.get('attachments', ())
                             if item.get('name')), '')
        if not fallback:
            return
        with self.harness.sessions._header_lock(session.id):
            if session.header.title or session.header.metadata.get('auto_title') != 'awaiting':
                return
            self.harness.sessions.update_header(session.id, title=fallback,
                metadata={'auto_title': 'generating' if text.strip() else 'fallback',
                          'auto_title_reason': None if text.strip() else 'attachment-only'})
        if not text.strip():
            return
        language = resolve_language(self.harness.config.language).effective_language
        task = asyncio.create_task(self.generate(session, text, fallback, language), name='session-title-' + session.id)
        self.tasks[session.id] = task
        task.add_done_callback(lambda done: self.finished(session.id, done))

    def finished(self, identifier, task):
        self.tasks.pop(identifier, None)
        if not task.cancelled() and (error := task.exception()) is not None:
            self.errors.append(error)

    def publish(self, session, expected, title, state, *, reason=None, request_id=None, error_type=None):
        """在头锁内比较标题及操作归属，手动改名始终优先。"""
        store = self.harness.sessions
        with store._header_lock(session.id):
            if (not store.has(session.id) or store.get(session.id) is not session
                    or session.header.title != expected
                    or session.header.metadata.get('auto_title') != 'generating'):
                return
            store.update_header(session.id, title=title, metadata={'auto_title': state,
                'auto_title_reason': reason, 'auto_title_request_id': request_id,
                'auto_title_error_type': error_type})

    async def generate(self, session, text, fallback, language):
        token = current_language.set(language)
        title, state = fallback, 'fallback'
        reason, request_id, error_type = 'no-credentials', None, None
        primary = None
        try:
            if self.harness.has_model_credentials:
                reason = 'request-error'
                request_id = 'title_' + uuid4().hex
                try:
                    parameters = inspect.signature(self.harness.adapter.complete).parameters
                except (TypeError, ValueError):
                    parameters = {}
                options = {}
                if 'on_request' in parameters or any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
                    observer = request_observer(session, request_id=request_id, purpose='title')
                    async def observe(data):
                        # 删除后的迟到传输不能重新创建该对话的追踪记录。
                        if not self.harness.sessions.has(session.id) or self.harness.sessions.get(session.id) is not session:
                            return None
                        return await observer(data)
                    options['on_request'] = observe
                async with asyncio.timeout(self.timeout):
                    config = self.harness._session_llm_config(session).merged(max_tokens=256, reasoning_effort=None)
                    resolve = getattr(self.harness.adapter, 'resolve_capabilities', None)
                    capabilities = await resolve(config.model) if callable(resolve) else {}
                    if (capabilities.get('supports_thinking') is not False
                            and not capabilities.get('reasoning_mandatory')
                            and 'off' in (capabilities.get('supported_efforts') or ())):
                        # 模型默认不等于关闭推理；命名只对明确支持的模型选择关闭。
                        config = config.merged(reasoning_effort='off', extra_body={
                            key: value for key, value in config.extra_body.items()
                            if key not in {'reasoning', 'reasoning_effort'}})
                    response = await self.harness.adapter.complete(
                        [SystemMessage(tr('session.title.prompt', limit=MAX_TITLE_CHARS)), UserMessage(text[:12000])],
                        config=config, tools=[], **options)
                candidate = response.text.strip()
                reason = ('tool-use' if response.has_tool_use else 'empty-output' if not candidate
                          else 'multiline-output' if '\n' in candidate or '\r' in candidate
                          else 'title-too-long' if len(candidate) > MAX_TITLE_CHARS else None)
                if reason is None:
                    title, state = candidate, 'generated'
        except (asyncio.CancelledError, ProcessCleanupError, BaseExceptionGroup) as exc:
            primary = exc
            reason = 'cancelled' if isinstance(exc, asyncio.CancelledError) else 'request-error'
            error_type = type(exc).__name__
        except Exception as exc:
            reason = ('timeout' if isinstance(exc, TimeoutError) else 'empty-output'
                      if getattr(exc, 'code', None) == 'llm/empty-response' else 'request-error')
            error_type = type(exc).__name__
            logger.warning(tr('session.title.failed'), exc_info=True)
        finally:
            try:
                with preserve_cleanup_errors([primary] if primary is not None else []):
                    self.publish(session, fallback, title, state, reason=reason, request_id=request_id, error_type=error_type)
            finally:
                current_language.reset(token)

    async def close(self):
        """关闭后不再生产，重复取消仍等待原请求清理。"""
        self.closed = True
        self.harness.bus.off('session/append', self.on_append)
        tasks = tuple(self.tasks.values())
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        if tasks:
            await join_cleanup(asyncio.gather(*tasks, return_exceptions=True))
        raise_cleanup_errors(self.errors)
