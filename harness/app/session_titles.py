"""新建网页对话的独立标题生成，不占用执行轮次。"""
from __future__ import annotations

import asyncio
import logging
import re

from ..core.cleanup import join_cleanup, preserve_cleanup_errors, raise_cleanup_errors
from ..core.language import current_language, resolve_language
from ..i18n import tr
from ..llm.types import SystemMessage, UserMessage
from ..session.events import user_message_text
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
                metadata={'auto_title': 'generating' if text.strip() else 'fallback'})
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

    def publish(self, session, expected, title, state):
        """在头锁内比较标题及操作归属，手动改名始终优先。"""
        store = self.harness.sessions
        with store._header_lock(session.id):
            if (not store.has(session.id) or store.get(session.id) is not session
                    or session.header.title != expected
                    or session.header.metadata.get('auto_title') != 'generating'):
                return
            store.update_header(session.id, title=title, metadata={'auto_title': state})

    async def generate(self, session, text, fallback, language):
        token = current_language.set(language)
        title, state = fallback, 'fallback'
        primary = None
        try:
            if self.harness.has_model_credentials:
                config = self.harness._session_llm_config(session).merged(max_tokens=256, reasoning_effort=None)
                async with asyncio.timeout(self.timeout):
                    response = await self.harness.adapter.complete(
                        [SystemMessage(tr('session.title.prompt', limit=MAX_TITLE_CHARS)), UserMessage(text[:12000])],
                        config=config, tools=[])
                candidate = response.text.strip()
                if (not response.has_tool_use and candidate and '\n' not in candidate
                        and '\r' not in candidate and len(candidate) <= MAX_TITLE_CHARS):
                    title, state = candidate, 'generated'
        except (asyncio.CancelledError, ProcessCleanupError, BaseExceptionGroup) as exc:
            primary = exc
        except Exception:
            logger.warning(tr('session.title.failed'), exc_info=True)
        finally:
            try:
                with preserve_cleanup_errors([primary] if primary is not None else []):
                    self.publish(session, fallback, title, state)
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
