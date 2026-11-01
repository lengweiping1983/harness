"""空闲整理的Agent语言与资源上下文一致，不泄漏调用方语言。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from harness.app.runtime import RunController
from harness.core.errors import HarnessError
from harness.core.events import EventBus
from harness.core.language import current_language
from harness.i18n import tr
from harness.session.session import Session


class IdleCompactionLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_control_uses_selected_language_for_operation_and_error(self):
        for language, caller in [('en', 'zh-CN'), ('zh-CN', 'en'), ('en', 'zh-CN')]:
            for failed in (False, True):
                with tempfile.TemporaryDirectory() as directory, current_language.set(caller):
                    observed = []
                    session = Session()
                    async def compact(*args, **kwargs):
                        observed.append((current_language.get(), tr('ui.run.compactionCancelled')))
                        if failed:
                            raise HarnessError(tr('context.control.busy'), code='fixture/compact')
                    agent = SimpleNamespace(reset=Mock(), language_preference=lambda: language,
                        _build_messages=lambda *args, **kwargs: [], _active_tool_schemas=lambda: [],
                        _maybe_compact=compact, config=None)
                    host = SimpleNamespace(config=SimpleNamespace(home=Path(directory), token_budget=None),
                        get_session=lambda _: session, _agent_for=lambda _: agent, bus=EventBus())
                    controller = RunController(host)
                    try:
                        record = await controller.submit_compaction(session.id, 'fixture')
                        await record.task
                        with current_language.set(language):
                            self.assertEqual(observed, [(language, tr('ui.run.compactionCancelled'))])
                            if failed:
                                self.assertEqual(record.error['message'], tr('context.control.busy'))
                        self.assertEqual(record.status, 'failed' if failed else 'completed')
                        self.assertEqual(current_language.get(), caller)
                        self.assertEqual(controller.active, {})
                    finally:
                        await controller.close()
