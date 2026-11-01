"""日志模板按调用语言解析，保留外部参数和故障后的业务行为。"""
import os
import re
import unittest
from unittest.mock import patch
from harness.core.events import EventBus
from harness.core.language import current_language
from harness.connectors.store import interpolate
from harness.i18n import resources


class LogLanguageTests(unittest.IsolatedAsyncioTestCase):
    def test_log_resource_percent_contract_and_language(self):
        en, zh = resources('en'), resources('zh-CN')
        keys = [key for key in en if key.startswith('log.')]
        self.assertTrue(keys)
        for key in keys:
            with self.subTest(key=key):
                patterns = [re.findall(r'%(?:\([^)]+\))?[-+#0 ]*\d*(?:\.\d+)?[srdif]', catalog[key]) for catalog in (en, zh)]
                self.assertEqual(patterns[0], patterns[1])
                args = tuple(7 if token[-1] in 'dif' else 'Original 原文' for token in patterns[0])
                self.assertNotRegex(en[key], r'[\u4e00-\u9fff]')
                for catalog in (en, zh):
                    message = catalog[key] % args
                    for token in patterns[0]:
                        if token[-1] in 'sr':
                            self.assertIn('Original 原文', message)

    def test_missing_environment_warning_is_localized_without_changing_substitution(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), patch.dict(os.environ, {}, clear=True), self.assertLogs('harness.connectors.store', level='WARNING') as logs:
                self.assertEqual(interpolate('prefix:${FIXTURE_MISSING}:suffix'), 'prefix::suffix')
            record = logs.records[0]
            self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in record.getMessage()), language == 'zh-CN')
            self.assertEqual(record.args, ('FIXTURE_MISSING',))

    async def test_failed_listener_logs_current_language_and_does_not_skip_next_listener(self):
        bus = EventBus()
        delivered = []
        def fail():
            raise ValueError('External 原文')
        bus.on('fixture', fail)
        bus.on('fixture', lambda: delivered.append('ok'))
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), self.assertLogs('harness.core.events', level='ERROR') as logs:
                await bus.emit('fixture')
            record = logs.records[0]
            self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in record.getMessage()), language == 'zh-CN')
            self.assertEqual(record.args, ('fixture',))
            self.assertIsInstance(record.exc_info[1], ValueError)
            self.assertEqual(str(record.exc_info[1]), 'External 原文')
        self.assertEqual(delivered, ['ok', 'ok', 'ok'])
