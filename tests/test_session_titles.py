"""自动标题通过真实总线和存储验证归属、语言与持久化。"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from harness.app.session_titles import SessionTitles
from harness.core.events import EventBus
from harness.core.language import current_language
from harness.llm.types import LlmCallConfig
from harness.session.events import AppendOp
from harness.session.store import SessionStore
from harness.web.views import session_listing


class SessionTitleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.bus = EventBus()
        self.store = SessionStore(Path(self.directory.name), bus=self.bus)
        self.complete = AsyncMock(return_value=SimpleNamespace(text='实现 Trello 拖拽看板', has_tool_use=False))
        self.app = SimpleNamespace(bus=self.bus, sessions=self.store, config=SimpleNamespace(language='zh-CN'),
            has_model_credentials=True, adapter=SimpleNamespace(complete=self.complete),
            _session_llm_config=lambda session: LlmCallConfig(model=session.header.metadata.get('model', 'selected')),
            list_sessions=lambda: [header.to_dict() for header in self.store.list_sessions()])
        self.titles = SessionTitles(self.app)
        self.addAsyncCleanup(self.titles.close)

    def create(self, **kwargs):
        return self.store.create(metadata={'auto_title': 'awaiting', **kwargs})

    async def append(self, session, text, source='user'):
        return await session.append('user/message', {'text': text, 'source': source}, surface_op=AppendOp())

    async def settle(self):
        await asyncio.gather(*tuple(self.titles.tasks.values()))

    async def test_first_task_generated_without_changing_messages_and_survives_reopen(self):
        session = self.create(model='custom-model')
        text = '/plan 请用单文件原生 HTML/CSS/JS 实现 Trello 看板，支持跨列拖拽、排序和卡片编辑。' * 4
        await self.append(session, text)
        self.assertLessEqual(len(session.header.title), 60)
        self.assertFalse(session.header.title.startswith('/plan'))
        await self.settle()
        self.assertEqual(session.header.title, '实现 Trello 拖拽看板')
        messages, options = self.complete.call_args
        self.assertEqual(messages[0][1].text, text[:12000])
        self.assertEqual(options['config'].model, 'custom-model')
        self.assertEqual(options['tools'], [])
        await self.append(session, '继续增加筛选')
        self.complete.assert_awaited_once()
        self.assertEqual(session_listing(self.app)[0]['title'], session.header.title)
        reopened = SessionStore(Path(self.directory.name)).get(session.id)
        self.assertEqual(reopened.header.title, session.header.title)
        self.assertEqual(reopened.events[0].data['text'], text)
        self.assertEqual(reopened.event_count, 2)

    async def test_late_result_cannot_replace_manual_title_even_same_value(self):
        for same_value in (False, True):
            session = self.create()
            entered, release = asyncio.Event(), asyncio.Event()
            async def delayed(*args, **kwargs):
                entered.set()
                await release.wait()
                return SimpleNamespace(text='Generated title', has_tool_use=False)
            self.complete.side_effect = delayed
            await self.append(session, 'Original task')
            await entered.wait()
            chosen = session.header.title if same_value else '用户手动名称'
            self.store.update_header(session.id, title=chosen)
            release.set()
            await self.settle()
            self.assertEqual(session.header.title, chosen)
            self.assertEqual(session.header.metadata['auto_title'], 'manual')

    async def test_failures_invalid_results_and_no_credentials_keep_stable_fallback(self):
        for response in (RuntimeError('offline'), '', 'a\nb', 'x' * 37):
            session = self.create()
            self.complete.side_effect = response if isinstance(response, Exception) else None
            self.complete.return_value = SimpleNamespace(text=response, has_tool_use=False)
            await self.append(session, '/goal ' + '原始任务内容' * 30)
            fallback = session.header.title
            await self.settle()
            await self.append(session, '后续请求')
            self.assertEqual(session.header.title, fallback)
            self.assertLessEqual(len(fallback), 60)
            self.assertEqual(session.header.metadata['auto_title'], 'fallback')
        self.app.has_model_credentials = False
        self.complete.reset_mock()
        session = self.create()
        await self.append(session, 'Offline task')
        await self.settle()
        self.complete.assert_not_awaited()

    async def test_attachment_only_uses_original_name_without_sending_private_body(self):
        session = self.create()
        await session.append('user/message', {'text': 'Private attachment body', 'original_text': '',
            'attachments': [{'name': '设计原文.txt'}]}, surface_op=AppendOp())
        self.assertEqual(session.header.title, '设计原文.txt')
        self.assertEqual(session.header.metadata['auto_title'], 'fallback')
        self.complete.assert_not_awaited()
        self.assertFalse(self.titles.tasks)

    async def test_explicit_existing_and_internal_messages_do_not_generate(self):
        for session in (self.store.create(title='Manual'), self.store.create()):
            await self.append(session, 'Task')
        session = self.create()
        for source in ('goal', 'goal-control', 'plan-mode', 'steer'):
            await self.append(session, 'Internal instruction', source)
        self.complete.assert_not_awaited()
        self.assertFalse(self.titles.tasks)
        self.assertIsNone(session.header.title)

    async def test_languages_are_resolved_per_request_in_same_instance(self):
        prompts = []
        async def complete(messages, **kwargs):
            prompts.append((current_language.get(), messages[0].text))
            return SimpleNamespace(text='Title 原文', has_tool_use=False)
        self.complete.side_effect = complete
        for language in ('en', 'zh-CN', 'en'):
            self.app.config.language = language
            await self.append(self.create(), 'Task 原文 {x}')
            await self.settle()
        self.assertEqual([language for language, _ in prompts], ['en', 'zh-CN', 'en'])
        self.assertEqual(prompts[0][1], prompts[2][1])
        self.assertNotEqual(prompts[0][1], prompts[1][1])

    async def test_timeout_and_repeated_close_wait_for_request_cleanup(self):
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def complete(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
        self.complete.side_effect = complete
        session = self.create()
        await self.append(session, 'Task')
        await entered.wait()
        first = asyncio.create_task(self.titles.close())
        await cleaning.wait()
        second = asyncio.create_task(self.titles.close())
        first.cancel()
        await asyncio.sleep(0)
        self.assertFalse(first.done())
        self.assertFalse(second.done())
        release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        self.assertIsInstance(results[0], asyncio.CancelledError)
        self.assertIsNone(results[1])
        self.assertEqual(session.header.metadata['auto_title'], 'fallback')
        self.complete.assert_awaited_once()

    async def test_timeout_retains_fallback_without_replaying_model_request(self):
        self.titles.timeout = .001
        async def complete(*args, **kwargs):
            await asyncio.Event().wait()
        self.complete.side_effect = complete
        session = self.create()
        await self.append(session, 'Task')
        await self.settle()
        self.assertEqual(session.header.title, 'Task')
        self.assertEqual(session.header.metadata['auto_title'], 'fallback')
        self.complete.assert_awaited_once()

    async def test_parallel_sessions_delete_and_shutdown_keep_original_ownership(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def complete(*args, **kwargs):
            entered.set()
            await release.wait()
            return SimpleNamespace(text='Generated', has_tool_use=False)
        self.complete.side_effect = complete
        first, second = self.create(), self.create()
        await self.append(first, 'First')
        await self.append(second, 'Second')
        await entered.wait()
        self.store.delete(first.id)
        release.set()
        await self.settle()
        self.assertFalse(self.store.has(first.id))
        self.assertEqual(second.header.title, 'Generated')
        release.clear()
        third = self.create()
        await self.append(third, 'Third')
        await self.titles.close()
        self.assertFalse(self.titles.tasks)
        await self.append(self.create(), 'After close')
        self.assertFalse(self.titles.tasks)
