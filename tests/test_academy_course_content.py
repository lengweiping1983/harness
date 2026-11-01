"""发布课程的循序渐进、双语内容和隔离运行回归。"""
import ast
import copy
import json
import tempfile
import unittest
from pathlib import Path

from harness.academy.catalog import AcademyCatalog
from harness.academy.files import MAX_FILES, read_files
from harness.academy.runtime import AcademyRuntime
from harness.academy.service import AcademyService
from harness.academy.errors import AcademyError
from harness.academy.protocol import SCHEMAS
from harness.core.language import current_language
from harness.tools.schema import validate
from tests.test_academy import write_engine_fixture


class AcademyCourseContentTests(unittest.TestCase):
    def setUp(self):
        self.catalog = AcademyCatalog()
        self.course_id = 'harness-foundations'
        self.raw = self.catalog.raw(self.course_id)

    def test_thirty_lessons_are_independent_complete_and_bounded(self):
        lessons = self.raw['lessons']
        self.assertEqual(len(lessons), 30)
        self.assertEqual([lesson['order'] for lesson in lessons], list(range(1, 31)))
        self.assertEqual(lessons[0]['id'], 'day-01-input-output')
        self.assertEqual(lessons[9]['id'], 'agent-loop')
        self.assertEqual([lesson['order'] for lesson in lessons if lesson['milestone']], [5, 10, 15, 20, 25, 30])
        sources = set()
        checkers = set()
        for index, lesson in enumerate(lessons):
            with self.subTest(lesson=lesson['id']):
                self.assertEqual(lesson['prerequisites'], [] if index == 0 else [lessons[index - 1]['id']])
                self.assertEqual(lesson['run_command'], ['python', 'main.py'])
                starter = self.catalog.starter(self.course_id, lesson['id'])
                self.assertEqual(set(starter), {'main.py'})
                source = starter['main.py']
                ast.parse(source)
                core_lines = [line for line in source.splitlines()
                              if line.strip() and not line.lstrip().startswith(('#', '"""'))]
                self.assertLessEqual(len(core_lines), 25)
                sources.add(source)
                checker = self.catalog.checker(self.course_id, lesson['id'])
                self.assertNotIn('starter', checker.parts)
                checkers.add(checker.read_text(encoding='utf-8'))
                for reference in lesson['references']:
                    self.assertIn(reference['symbol'], self.catalog.reference(self.course_id, lesson['id'], reference['path']))
        self.assertEqual(len(sources), 30)
        self.assertEqual(len(checkers), 30)
        self.assertLessEqual(len(read_files(self.catalog.directory(self.course_id))), MAX_FILES)
        self.assertTrue(self.catalog.pin(self.course_id))

    def test_first_lessons_introduce_one_python_structure_at_a_time(self):
        trees = [ast.parse(self.catalog.starter(self.course_id, lesson['id'])['main.py'])
                 for lesson in self.raw['lessons'][:5]]
        for tree in trees[:2]:
            self.assertFalse(any(isinstance(node, (ast.FunctionDef, ast.Dict, ast.List, ast.For, ast.While, ast.If))
                                 for node in ast.walk(tree)))
        self.assertTrue(any(isinstance(node, ast.FunctionDef) for node in ast.walk(trees[2])))
        self.assertFalse(any(isinstance(node, (ast.Dict, ast.List, ast.For, ast.While)) for node in ast.walk(trees[2])))
        self.assertTrue(any(isinstance(node, ast.Dict) for node in ast.walk(trees[3])))
        self.assertFalse(any(isinstance(node, (ast.FunctionDef, ast.List, ast.For, ast.While)) for node in ast.walk(trees[3])))
        self.assertTrue(any(isinstance(node, ast.List) for node in ast.walk(trees[4])))
        self.assertFalse(self.raw['lessons'][0]['references'])

    def test_content_consumes_english_chinese_english_resources(self):
        views = []
        for language in ('en', 'zh-CN', 'en'):
            token = current_language.set(language)
            try:
                views.append(self.catalog.course(self.course_id))
            finally:
                current_language.reset(token)
        self.assertEqual(views[0], views[2])
        for english, chinese in zip(views[0]['lessons'], views[1]['lessons']):
            with self.subTest(lesson=english['id']):
                for field in ('title', 'goal', 'reading', 'exercise', 'rubric', 'stage'):
                    self.assertTrue(english[field].strip())
                    self.assertTrue(chinese[field].strip())
                    self.assertNotEqual(english[field], chinese[field])
                self.assertIn('\n\n', english['reading'])
                self.assertIn('\n\n', chinese['reading'])
                self.assertGreaterEqual(english['duration_minutes'], 10)
                self.assertLessEqual(english['duration_minutes'], 30)
        self.assertIn('fixed reply', views[0]['lessons'][0]['reading'])
        self.assertIn('固定回复', views[1]['lessons'][0]['reading'])
        self.assertEqual(len({lesson['exercise'] for lesson in views[0]['lessons']}), 30)
        self.assertEqual(len({lesson['goal'] for lesson in views[1]['lessons']}), 30)

    def test_knowledge_checks_cover_entry_and_every_stage_exit_in_both_languages(self):
        expected = {lesson['id'] for lesson in self.raw['lessons'] if lesson['milestone'] or lesson['order'] == 1}
        self.assertEqual({lesson['id'] for lesson in self.raw['lessons'] if lesson.get('knowledge_checks')}, expected)
        views = []
        for language in ('en', 'zh-CN', 'en'):
            token = current_language.set(language)
            try:
                course = self.catalog.course(self.course_id)
                check_schema = copy.deepcopy(SCHEMAS['AcademyKnowledgeCheck'])
                check_schema['properties']['options']['items'] = SCHEMAS['AcademyKnowledgeOption']
                for lesson in course['lessons']:
                    for check in lesson.get('knowledge_checks', []):
                        self.assertEqual(validate(check, check_schema), [])
                views.append({lesson['id']: lesson['knowledge_checks'] for lesson in course['lessons'] if lesson.get('knowledge_checks')})
            finally:
                current_language.reset(token)
        self.assertEqual(views[0], views[2])
        for identity in expected:
            english, chinese = views[0][identity][0], views[1][identity][0]
            self.assertEqual(english['answer_id'], chinese['answer_id'])
            self.assertNotEqual(english['prompt'], chinese['prompt'])
            self.assertEqual(len(english['hints']), 2)
            for en, zh in zip(english['options'], chinese['options']):
                self.assertEqual(en['id'], zh['id'])
                self.assertNotEqual(en['label'], zh['label'])
                self.assertNotEqual(en['feedback'], zh['feedback'])

    def test_course_authors_can_add_checks_and_invalid_checks_preserve_the_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_engine_fixture(root)
            catalog = AcademyCatalog(root)
            manifest = root / self.course_id / 'course.json'
            raw = json.loads(manifest.read_text(encoding='utf-8'))
            check = {'id': 'question-1', 'prompt_key': 'goal', 'answer_id': 'a', 'hint_keys': ['reading'],
                     'options': [{'id': value, 'label_key': 'lesson', 'feedback_key': 'description'} for value in ('a', 'b')]}
            raw['lessons'][0]['knowledge_checks'] = [check]
            manifest.write_text(json.dumps(raw), encoding='utf-8')
            self.assertEqual(catalog.course(self.course_id)['lessons'][0]['knowledge_checks'][0]['answer_id'], 'a')
            invalid = [
                {**check, 'answer_id': 'missing'}, {**check, 'id': '../outside'},
                {**check, 'options': [check['options'][0]]},
                {**check, 'options': [check['options'][0]] * 2},
                {**check, 'options': check['options'] * 4},
                {**check, 'hint_keys': ['reading'] * 4},
                {**check, 'hint_keys': [True]}, {**check, 'prompt_key': 'missing'},
            ]
            for checks in [[value] for value in invalid] + [[check, check], [check] * 9, []]:
                with self.subTest(checks=checks):
                    changed = copy.deepcopy(raw)
                    changed['lessons'][0]['knowledge_checks'] = checks
                    payload = json.dumps(changed).encode('utf-8')
                    manifest.write_bytes(payload)
                    with self.assertRaisesRegex(AcademyError, 'catalog-invalid'):
                        catalog.course(self.course_id)
                    self.assertEqual(manifest.read_bytes(), payload)
            manifest.write_text(json.dumps(raw), encoding='utf-8')
            locale = root / self.course_id / 'locales' / 'zh-CN.json'
            resources = json.loads(locale.read_text(encoding='utf-8'))
            resources['goal'] = '  '
            locale.write_text(json.dumps(resources), encoding='utf-8')
            with self.assertRaisesRegex(AcademyError, 'catalog-invalid'):
                catalog.course(self.course_id)

    @unittest.skipIf(AcademyRuntime.backend() == 'unavailable', '系统隔离器未安装')
    def test_all_shipped_examples_and_checkers_run_in_actual_sandbox(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = AcademyRuntime(root)
            for lesson in self.raw['lessons']:
                with self.subTest(lesson=lesson['id']):
                    source = self.catalog.starter(self.course_id, lesson['id'])['main.py']
                    (root / 'main.py').write_text(source, encoding='utf-8')
                    result = runtime.run(lesson['run_command'])
                    self.assertEqual(result['status'], 'passed', result)
                    self.assertTrue(result['stdout'].strip(), result)
                    checked = runtime.run(lesson['run_command'], check_script=self.catalog.checker(self.course_id, lesson['id']))
                    self.assertEqual(checked['status'], 'passed', checked)
                    self.assertIn('CHECK PASS', checked['stdout'])

    @unittest.skipIf(AcademyRuntime.backend() == 'unavailable', '系统隔离器未安装')
    def test_first_checker_accepts_personal_reply_and_rejects_missing_output(self):
        lesson = self.raw['lessons'][0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = AcademyRuntime(root)
            checker = self.catalog.checker(self.course_id, lesson['id'])
            (root / 'main.py').write_text('question = "我的问题"\nreply = "我的回答"\nprint(question, reply)\n', encoding='utf-8')
            self.assertEqual(runtime.run(lesson['run_command'], check_script=checker)['status'], 'passed')
            (root / 'main.py').write_text('question = "我的问题"\nreply = "我的回答"\nprint(question)\n', encoding='utf-8')
            result = runtime.run(lesson['run_command'], check_script=checker)
            self.assertEqual(result['status'], 'failed', result)
            self.assertNotIn('CHECK PASS', result['stdout'])

    @unittest.skipIf(AcademyRuntime.backend() == 'unavailable', '系统隔离器未安装')
    def test_trace_examples_export_runtime_events_and_consume_localized_labels(self):
        expected = {
            'event-trace': ['request/start', 'tool/result', 'request/end'],
            'mini-harness': ['model/request', 'tool/result', 'model/request', 'turn/end'],
        }
        with tempfile.TemporaryDirectory() as directory:
            service = AcademyService(Path(directory))
            project = service.create_project('trace-create', self.course_id)['result']['project']
            for lesson_id, events in expected.items():
                with self.subTest(lesson=lesson_id):
                    project = service.start_lesson(project['id'], f'start-{lesson_id}', lesson_id,
                        project['revision'], project['fingerprint'])['result']['project']
                    run = service.execute(project['id'], f'run-{lesson_id}', lesson_id,
                        project['fingerprint'])['result']['run']
                    self.assertEqual(run['result']['status'], 'passed', run['result'])
                    self.assertEqual(json.loads(run['result']['stdout'])['events'], events)
                    fixture_events = [event['type'] for event in service.trace(project['id'], run['id'])
                                      if event['source'] == 'fixture']
                    self.assertEqual(fixture_events, events)
                    labels = []
                    for language in ('en', 'zh-CN', 'en'):
                        token = current_language.set(language)
                        try:
                            lesson = next(item for item in service.course(self.course_id)['lessons']
                                          if item['id'] == lesson_id)
                            labels.append([lesson['trace_labels'][event] for event in fixture_events])
                        finally:
                            current_language.reset(token)
                    self.assertEqual(labels[0], labels[2])
                    self.assertTrue(all(labels[0]))
                    self.assertTrue(all(labels[1]))
                    self.assertNotEqual(labels[0], labels[1])
                    project = service.project(project['id'])

    @unittest.skipIf(AcademyRuntime.backend() == 'unavailable', '系统隔离器未安装')
    def test_final_checker_rejects_a_loop_that_exceeds_the_taught_request_budget(self):
        lesson_id = 'mini-harness'
        source = self.catalog.starter(self.course_id, lesson_id)['main.py']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # 普通任务仍在第二次请求结束；只有持续索要工具时才暴露多做的一步。
            (root / 'main.py').write_text(source.replace('range(2)', 'range(3)'), encoding='utf-8')
            result = AcademyRuntime(root).run(['python', 'main.py'],
                check_script=self.catalog.checker(self.course_id, lesson_id))
            self.assertEqual(result['status'], 'failed', result)
            self.assertIn('exactly the allowed number', result['stderr'])
            self.assertNotIn('CHECK PASS', result['stdout'])


if __name__ == '__main__':
    unittest.main()
