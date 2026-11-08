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

    def test_oversized_integers_are_rejected_as_invalid_schedule_values(self):
        oversized = 10 ** 4000
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for kind, field, label in (('once', 'at', 'Execution time'), ('interval', 'seconds', 'Cycle')):
                    with self.subTest(language=language, kind=kind):
                        with self.assertRaises(HarnessError) as caught:
                            validate_schedule({'schedule': {'kind': kind, field: oversized}})
                        self.assertEqual(caught.exception.code, 'automation/invalid')
                        expected = (label + ' must be a finite number' if language == 'en'
                                    else ('执行时间' if kind == 'once' else '周期') + '必须是有限数值')
                        self.assertEqual(caught.exception.message, expected)

    def test_current_schedule_kinds_keep_explicit_values_and_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AutomationService(Path(directory) / 'automations.db', SimpleNamespace(get_session=Mock()))
            schedules = [{'kind': 'once', 'at': 1800000000}, {'kind': 'interval', 'seconds': 60},
                         {'kind': 'daily', 'time': '09:00'}, {'kind': 'weekly', 'time': '09:00', 'days': [0, 4]},
                         {'kind': 'monthly', 'time': '09:00', 'monthdays': [1, 15]},
                         {'kind': 'yearly', 'time': '09:00', 'months': [1, 7], 'monthdays': [1]}]
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

    def test_calendar_schedule_validation_rejects_bad_days_and_months(self):
        from harness.app.schedules import next_due
        cases = [
            ({'kind': 'monthly', 'time': '09:00'}, 'python.schedules.monthdaysEmpty'),
            ({'kind': 'monthly', 'time': '09:00', 'monthdays': []}, 'python.schedules.monthdaysEmpty'),
            ({'kind': 'monthly', 'time': '09:00', 'monthdays': [0, 15]}, 'python.schedules.monthdaysRange'),
            ({'kind': 'monthly', 'time': '09:00', 'monthdays': [32]}, 'python.schedules.monthdaysRange'),
            ({'kind': 'yearly', 'time': '09:00', 'monthdays': [1]}, 'python.schedules.monthsEmpty'),
            ({'kind': 'yearly', 'time': '09:00', 'months': [13], 'monthdays': [1]}, 'python.schedules.monthsRange'),
            ({'kind': 'yearly', 'time': '09:00', 'months': [1], 'monthdays': [0]}, 'python.schedules.monthdaysRange'),
        ]
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for schedule, key in cases:
                    with self.subTest(language=language, schedule=schedule):
                        with self.assertRaises(HarnessError) as caught:
                            validate_schedule({'schedule': schedule})
                        self.assertEqual(caught.exception.code, 'automation/invalid')
                        self.assertEqual(str(caught.exception), '[automation/invalid] ' + tr(key))

    def test_calendar_next_due_skips_missing_dates_and_keeps_order(self):
        from harness.app.schedules import next_due
        from datetime import datetime, timezone
        monthly = {'kind': 'monthly', 'time': '09:00', 'monthdays': [31]}
        # 2 月没有 31 日：1 月 31 日之后应跳到 3 月 31 日。
        after = datetime(2026, 2, 1, tzinfo=timezone.utc).timestamp()
        self.assertEqual(datetime.fromtimestamp(next_due(monthly, 'UTC', after), timezone.utc),
                         datetime(2026, 3, 31, 9, 0, tzinfo=timezone.utc))
        # 同一天已到点的候选不能作为下一次执行时间。
        after = datetime(2026, 3, 31, 10, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual(datetime.fromtimestamp(next_due(monthly, 'UTC', after), timezone.utc),
                         datetime(2026, 5, 31, 9, 0, tzinfo=timezone.utc))
        # 每月 1 日与 15 日按日期顺序触发。
        twice = {'kind': 'monthly', 'time': '09:00', 'monthdays': [1, 15]}
        after = datetime(2026, 3, 15, 8, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual(datetime.fromtimestamp(next_due(twice, 'UTC', after), timezone.utc),
                         datetime(2026, 3, 15, 9, 0, tzinfo=timezone.utc))
        # 年度计划同时受月份与日期约束：7 月 1 日之后进入次年 1 月 1 日。
        yearly = {'kind': 'yearly', 'time': '09:00', 'months': [1, 7], 'monthdays': [1]}
        after = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual(datetime.fromtimestamp(next_due(yearly, 'UTC', after), timezone.utc),
                         datetime(2027, 1, 1, 9, 0, tzinfo=timezone.utc))
        # 闰日：2 月 29 日在平年跳过，直到下一个闰年。
        leap = {'kind': 'yearly', 'time': '09:00', 'months': [2], 'monthdays': [29]}
        after = datetime(2027, 3, 1, tzinfo=timezone.utc).timestamp()
        self.assertEqual(datetime.fromtimestamp(next_due(leap, 'UTC', after), timezone.utc),
                         datetime(2028, 2, 29, 9, 0, tzinfo=timezone.utc))
        # 每日、每周与每月共用同一时区语义：Asia/Shanghai 的 09:00 对应 UTC 01:00。
        shanghai = {'kind': 'monthly', 'time': '09:00', 'monthdays': [1]}
        after = datetime(2026, 5, 31, 18, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual(datetime.fromtimestamp(next_due(shanghai, 'Asia/Shanghai', after), timezone.utc),
                         datetime(2026, 6, 1, 1, 0, tzinfo=timezone.utc))
