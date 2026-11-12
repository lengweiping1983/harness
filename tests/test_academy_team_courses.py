"""团队 Python 与大模型课程的递进、双语消费和实际学习证据。"""
import ast
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness.academy.catalog import AcademyCatalog
from harness.academy.files import MAX_FILES, read_files
from harness.academy.runtime import AcademyRuntime
from harness.academy.service import AcademyService
from harness.core.language import current_language


COURSES = {
    'python-foundations': {'count': 12, 'stages': 4,
        'practice': {'clean-text', 'sequences', 'validation', 'study-plan'}},
    'llm-foundations': {'count': 9, 'stages': 3,
        'practice': {'structured-response', 'evidence-assistant'}},
}


class AcademyTeamCourseTests(unittest.TestCase):
    def setUp(self):
        self.catalog = AcademyCatalog()

    def test_team_courses_are_discovered_with_complete_progressive_bilingual_content(self):
        self.assertTrue({'harness-foundations', *COURSES} <= {course['id'] for course in self.catalog.courses()})
        for identity, expected in COURSES.items():
            with self.subTest(course=identity):
                views = []
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language):
                        views.append(self.catalog.course(identity))
                english, chinese, repeated = views
                self.assertEqual(english, repeated)
                self.assertEqual(english['origin'], 'provided')
                self.assertNotEqual(english['title'], chinese['title'])
                self.assertNotEqual(english['assumptions'], chinese['assumptions'])
                self.assertEqual(len(english['lessons']), expected['count'])
                self.assertEqual(len({lesson['stage'] for lesson in english['lessons']}), expected['stages'])
                self.assertEqual(sum(lesson['milestone'] for lesson in english['lessons']), expected['stages'])
                self.assertEqual({next(index for index, option in enumerate(check['options']) if option['id'] == check['answer_id'])
                                  for lesson in english['lessons'] for check in lesson['knowledge_checks']}, {0, 1})
                self.assertLessEqual(len(read_files(self.catalog.directory(identity))), MAX_FILES)
                for index, (en, zh) in enumerate(zip(english['lessons'], chinese['lessons'])):
                    self.assertEqual(en['prerequisites'], [] if index == 0 else [english['lessons'][index - 1]['id']])
                    for field in ('title', 'goal', 'reading', 'exercise', 'rubric', 'stage', 'stage_goal'):
                        self.assertTrue(en[field].strip())
                        self.assertTrue(zh[field].strip())
                        self.assertNotEqual(en[field], zh[field])
                    self.assertGreater(len(en['reading']), 300)
                    self.assertGreater(len(zh['reading']), 200)
                    for a, b in zip(en['knowledge_checks'], zh['knowledge_checks']):
                        self.assertEqual(a['answer_id'], b['answer_id'])
                        self.assertNotEqual(a['prompt'], b['prompt'])
                        self.assertEqual(len(a['hints']), 2)
                        for option_en, option_zh in zip(a['options'], b['options']):
                            if option_en['label'].isdecimal():
                                self.assertEqual(option_en['label'], option_zh['label'])
                            else:
                                self.assertNotEqual(option_en['label'], option_zh['label'])
                            self.assertNotEqual(option_en['feedback'], option_zh['feedback'])

    def test_python_entry_does_not_require_untaught_structures_and_llm_reading_needs_no_code(self):
        lessons = self.catalog.course('python-foundations')['lessons']
        for lesson in lessons[:3]:
            tree = ast.parse(self.catalog.starter('python-foundations', lesson['id'])['main.py'])
            self.assertFalse(any(isinstance(node, (ast.FunctionDef, ast.Dict, ast.List, ast.For, ast.While, ast.If, ast.Import))
                                 for node in ast.walk(tree)))
        llm = self.catalog.course('llm-foundations')
        self.assertEqual([lesson['mode'] for lesson in llm['lessons']], ['study', 'model', 'model'] + ['python'] * 6)
        for lesson in llm['lessons'][:3]:
            self.assertEqual(self.catalog.starter(llm['id'], lesson['id']), {})
            self.assertEqual(lesson['run_command'], [])
        for identity in COURSES:
            for lesson in self.catalog.course(identity)['lessons']:
                if lesson['mode'] == 'python':
                    tree = ast.parse(self.catalog.starter(identity, lesson['id'])['main.py'])
                    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
                    self.assertLessEqual(imported, {'json'})

    def test_shipped_reading_activities_complete_with_answers_and_reflection_without_running(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(AcademyRuntime, 'run', side_effect=AssertionError('阅读不得执行代码')):
            service = AcademyService(Path(directory))
            project = service.create_project('create-llm', 'llm-foundations')['result']['project']
            for lesson in service.course('llm-foundations')['lessons']:
                if lesson['mode'] != 'study':
                    continue
                if project['active_lesson_id'] != lesson['id']:
                    project = service.start_lesson(project['id'], 'open-' + lesson['id'], lesson['id'],
                        project['revision'], project['fingerprint'])['result']['project']
                answers = [{'check_id': check['id'], 'option_id': check['answer_id']} for check in lesson['knowledge_checks']]
                rejected = service.complete_study(project['id'], 'empty-' + lesson['id'], lesson['id'], project['fingerprint'], '', answers)
                self.assertEqual(rejected['result']['error_code'], 'invalid-request')
                wrong = [{'check_id': check['id'], 'option_id': 'guess'} for check in lesson['knowledge_checks']]
                rejected = service.complete_study(project['id'], 'wrong-' + lesson['id'], lesson['id'], project['fingerprint'],
                    'My own explanation.', wrong)
                self.assertEqual(rejected['result']['error_code'], 'study-incomplete')
                project = service.complete_study(project['id'], 'complete-' + lesson['id'], lesson['id'], project['fingerprint'],
                    'My own example separates the stated fact from the missing information.', answers)['result']['project']
            self.assertEqual([row['state'] for row in project['progress']], ['completed'])
            self.assertEqual(service.runs(project['id']), [])

    def test_default_project_names_use_the_creation_language_and_preserve_existing_names(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AcademyService(Path(directory))
            created = []
            for index, (language, expected) in enumerate((('en', 'My learning lab'), ('zh-CN', '我的学习实验'), ('en', 'My learning lab'))):
                with current_language.set(language):
                    project = service.create_project('name-' + str(index), 'llm-foundations')['result']['project']
                self.assertEqual(project['name'], expected)
                created.append(project)
            for project in created:
                with current_language.set('zh-CN'):
                    self.assertEqual(service.project(project['id'])['name'], project['name'])
            personal = service.create_project('custom-name', 'llm-foundations', '原文 / My own name')['result']['project']
            self.assertEqual(personal['name'], '原文 / My own name')

    @unittest.skipIf(AcademyRuntime.backend() == 'unavailable', '系统隔离器未安装')
    def test_examples_run_transfer_starters_need_work_and_reference_implementations_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = AcademyRuntime(root)
            for identity, expected in COURSES.items():
                for lesson in self.catalog.course(identity)['lessons']:
                    if lesson['mode'] != 'python':
                        continue
                    with self.subTest(course=identity, lesson=lesson['id']):
                        (root / 'main.py').write_text(self.catalog.starter(identity, lesson['id'])['main.py'], encoding='utf-8')
                        example = runtime.run(lesson['run_command'])
                        self.assertEqual(example['status'], 'passed', example)
                        self.assertTrue(example['stdout'].strip(), example)
                        checker = self.catalog.checker(identity, lesson['id'])
                        checked = runtime.run(lesson['run_command'], check_script=checker)
                        self.assertEqual(checked['status'], 'failed' if lesson['id'] in expected['practice'] else 'passed', checked)
                        reference = lesson['references'][0]
                        source = self.catalog.reference(identity, lesson['id'], reference['path'])
                        self.assertIn(reference['symbol'], source)
                        (root / 'main.py').write_text(source, encoding='utf-8')
                        solution = runtime.run(lesson['run_command'], check_script=checker)
                        self.assertEqual(solution['status'], 'passed', solution)
                        self.assertIn('CHECK PASS', solution['stdout'])

    @unittest.skipIf(AcademyRuntime.backend() == 'unavailable', '系统隔离器未安装')
    def test_fixed_checks_reject_specific_semantic_regressions(self):
        cases = [
            ('python-foundations', 'decisions', 'minutes >= 20', 'minutes > 20'),
            ('python-foundations', 'sequences', 'if tasks:', 'if True:'),
            ('python-foundations', 'validation', 'type(value) is not int', 'not isinstance(value, int)'),
            ('python-foundations', 'study-plan', 'minutes = validate_minutes(task["minutes"])', 'minutes = task["minutes"]'),
            ('llm-foundations', 'context-selection', 'selected.append(text)', 'selected.append(text[:1])'),
            ('llm-foundations', 'grounded-answer', 'if field in document:', 'if document.get(field):'),
            ('llm-foundations', 'structured-response', 'type(value["answer"]) is type(document[field]) and ', ''),
            ('llm-foundations', 'tool-ownership', 'type(arguments["a"]) is not int', 'not isinstance(arguments["a"], int)'),
            ('llm-foundations', 'comparison-eval', '"total": len(cases)', '"total": len(predictions)'),
            ('llm-foundations', 'evidence-assistant', 'if field in document:', 'if document.get(field):'),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = AcademyRuntime(root)
            for identity, lesson_id, original, mutation in cases:
                with self.subTest(course=identity, lesson=lesson_id):
                    lesson = self.catalog.lesson(identity, lesson_id)
                    source = self.catalog.reference(identity, lesson_id, lesson['references'][0]['path'])
                    self.assertIn(original, source)
                    (root / 'main.py').write_text(source.replace(original, mutation), encoding='utf-8')
                    checked = runtime.run(lesson['run_command'], check_script=self.catalog.checker(identity, lesson_id))
                    self.assertEqual(checked['status'], 'failed', checked)
                    self.assertNotIn('CHECK PASS', checked['stdout'])


if __name__ == '__main__':
    unittest.main()
