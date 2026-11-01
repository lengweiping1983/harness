"""自动化只接受显式当前日程，旧参数不能默认生成执行计划。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from harness.app.automations import AutomationService
from harness.app.schedules import validate_schedule
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.i18n import tr


class ScheduleCurrentContractTests(unittest.TestCase):
    def test_missing_or_null_schedule_rejects_legacy_fields_without_writing(self):
        for language in ('en', 'zh-CN', 'en'):
            with self.subTest(language=language), current_language.set(language), tempfile.TemporaryDirectory() as directory:
                service = AutomationService(Path(directory) / 'automations.db', SimpleNamespace(get_session=Mock()))
                for legacy in ({}, {'due': 1800000000}, {'interval_seconds': 60},
                               {'schedule': None}, {'schedule': None, 'due': 1800000000, 'interval_seconds': 60}):
                    with self.subTest(legacy=legacy):
                        for invoke in (lambda: validate_schedule(legacy),
                                       lambda: service.save({'name': 'Original 原文', 'prompt': 'Original prompt 原文', 'session_id': 'fixture', **legacy})):
                            with self.assertRaises(HarnessError) as caught:
                                invoke()
                            self.assertEqual(caught.exception.code, 'automation/invalid')
                            self.assertEqual(str(caught.exception), '[automation/invalid] ' + tr('python.schedules.33fa54e90879'))
                        self.assertEqual(service.list(), [])

    def test_invalid_numbers_keep_label_and_separate_english_words(self):
        for language in ('en', 'zh-CN', 'en'):
            for kind, field, label in (('once', 'at', 'Execution time'), ('interval', 'seconds', 'Cycle')):
                for value in (None, True, '60', float('nan'), float('inf')):
                    with self.subTest(language=language, kind=kind, value=value), current_language.set(language):
                        with self.assertRaises(HarnessError) as caught:
                            validate_schedule({'schedule': {'kind': kind, field: value}})
                        self.assertEqual(caught.exception.code, 'automation/invalid')
                        if language == 'en':
                            self.assertEqual(caught.exception.message, label + ' must be a finite number')
                        else:
                            self.assertEqual(caught.exception.message,
                                ('执行时间' if kind == 'once' else '周期') + '必须是有限数值')

    def test_current_schedule_kinds_keep_explicit_values_and_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AutomationService(Path(directory) / 'automations.db', SimpleNamespace(get_session=Mock()))
            schedules = [{'kind': 'once', 'at': 1800000000}, {'kind': 'interval', 'seconds': 60},
                         {'kind': 'daily', 'time': '09:00'}, {'kind': 'weekly', 'time': '09:00', 'days': [0, 4]}]
            for index, schedule in enumerate(schedules):
                body = {'name': 'Original 原文', 'prompt': 'Original prompt 原文', 'session_id': 'fixture',
                        'timezone': 'UTC', 'enabled': False, 'schedule': schedule, 'creation_id': f'{index:032x}'}
                saved = service.save(body)
                self.assertEqual(saved['schedule'], schedule)
                self.assertEqual(saved['prompt'], body['prompt'])
                self.assertEqual(len(service.list()), index + 1)
                updated = service.save({**{key: value for key, value in body.items() if key != 'creation_id'}, 'id': saved['id'], 'name': 'Updated 原文'})
                self.assertEqual(updated['id'], saved['id'])
                self.assertEqual(updated['name'], 'Updated 原文')
                self.assertEqual(len(service.list()), index + 1)
