"""项目任务边界提供双语消息，错误码与封存的审阅身份保持稳定。"""
import ast
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from harness.core.language import current_language
from harness.core.errors import ToolExecutionError
from harness.i18n import resources
from harness.project.artifact_review import ArtifactReview
from harness.project.board import BoardError, TaskBoard
from harness.project.error_messages import board_error_message
from harness.tools.builtin.project_tools import build_project_tools
from harness.web.api.project_board import build_router
from harness.web.errors import BadRequest, Conflict
from harness.web.router import Request


class ProjectLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_and_http_boundaries_keep_code_and_conflict_status(self):
        tool = next(t for t in build_project_tools(enable_git=False) if t.schema.name == 'project_board_read')
        ctx = SimpleNamespace(harness=SimpleNamespace(artifact_review=SimpleNamespace(verify=None)))
        route, _ = build_router(ctx).resolve('GET', '/api/v1/project-board')
        request = Request('GET', '/api/v1/project-board', {}, {}, b'')
        with TemporaryDirectory() as directory, patch('harness.web.api.project_board._resolved_root', return_value=Path(directory)):
            for language, word in [('en', 'task board'), ('zh-CN', '任务板'), ('en', 'task board')]:
                for code, error in [('board/invalid-file', BadRequest), ('board/stale-revision', Conflict)]:
                    with self.subTest(language=language, code=code), current_language.set(language), patch.object(TaskBoard, 'read', side_effect=BoardError(code)):
                        with self.assertRaises(ToolExecutionError) as caught:
                            await tool.execute({}, SimpleNamespace(cwd=directory))
                        self.assertEqual(caught.exception.code, code)
                        self.assertIn(word, caught.exception.message.lower())
                        with self.assertRaises(error) as http:
                            route(request)
                        self.assertEqual(http.exception.code, code)
                        self.assertEqual(http.exception.message, caught.exception.message)
                        self.assertEqual(http.exception.status, 409 if error is Conflict else 400)

    def test_current_static_board_errors_have_bilingual_messages(self):
        codes = set()
        for path in Path('harness/project').glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'BoardError' and node.args and isinstance(node.args[0], ast.Constant):
                    codes.add(node.args[0].value)
        for language in ('en', 'zh-CN'):
            with current_language.set(language):
                for code in codes:
                    self.assertIn('project.board.error.' + code.removeprefix('board/'), resources(language))
                    self.assertIn(code, board_error_message(code))
                self.assertIn('external detail', board_error_message('external detail'))

    def test_review_prompt_uses_captured_language_after_switch(self):
        review = ArtifactReview(None)
        task = {'id': 't', 'title': 'User 原文', 'artifacts': [], 'allowed_paths': ['notes.md'], 'acceptance': ['Criterion 原文']}
        for language, other, phrase in [('en', 'zh-CN', 'independent read-only reviewer'), ('zh-CN', 'en', '独立只读审阅者')]:
            with TemporaryDirectory() as directory, current_language.set(language):
                marker = review.prepare(Path(directory), task)
                original = review.prompt(task, marker)
                self.assertIn(phrase, original)
                self.assertIn('User 原文', original)
                self.assertIn('Criterion 原文', original)
                self.assertEqual(marker['language'], language)
                payload = json.loads(original[original.index('\n{') + 1:])
                self.assertEqual(payload['review'], marker)
                with current_language.set(other):
                    self.assertEqual(review.prompt(task, marker), original)
                for invalid in (None, 'auto', 'fr'):
                    with self.assertRaisesRegex(BoardError, 'board/review-mismatch'):
                        review.prompt(task, {**marker, 'language': invalid})


class ProjectSubmittedPromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_planning_and_execution_submit_current_language_with_original_goal(self):
        from harness.project.planning import ProjectPlanning
        from harness.project.execution import ProjectExecution
        for language, planning_phrase, execution_phrase in (
            ('en', 'Generate a task board', 'Execute only the saved project task'),
            ('zh-CN', '为当前项目生成任务板', '执行已保存的唯一项目任务'),
            ('en', 'Generate a task board', 'Execute only the saved project task'),
        ):
            with self.subTest(language=language), TemporaryDirectory() as directory, current_language.set(language):
                root = Path(directory).resolve()
                record = SimpleNamespace(id='run', session_id='session', status='queued', task=None, to_dict=lambda: {'id': 'run'})
                session = SimpleNamespace(id='session', header=SimpleNamespace(cwd=str(root), metadata={}))
                harness = SimpleNamespace(
                    config=SimpleNamespace(enable_fs=True, enable_shell=False, api_key='fixture', api_keys=[], model_routes={}),
                    agent_mount=None,
                    sessions=SimpleNamespace(list_sessions=lambda: [], update_header=Mock()),
                    runtime=SimpleNamespace(records={}, live_records=lambda: [], submit=Mock(return_value=record), set_queue_paused=AsyncMock()),
                    workspace_preparations=SimpleNamespace(claim=Mock(return_value='claim'), release=Mock()),
                    create_session=Mock(return_value=session), artifact_review=SimpleNamespace(verify=None),
                )
                board = TaskBoard(root)
                await ProjectPlanning(harness).generate(root, board.read()['revision'], 'User goal 原文', 'planning-key')
                planning_text = harness.runtime.submit.call_args.args[1]
                self.assertIn(planning_phrase, planning_text)
                self.assertIn('User goal 原文', planning_text)
                self.assertEqual(harness.runtime.submit.call_count, 1)
                harness.runtime.submit.reset_mock()
                snapshot = board.update(board.read()['revision'], {
                    'id': 'task', 'title': 'Deliverable 原文', 'status': 'READY',
                    'verification_policy': 'artifact', 'artifacts': ['notes.md'],
                    'allowed_paths': ['notes.md'], 'acceptance': ['Criterion 原文'],
                }, create=True)
                await ProjectExecution(harness).launch(root, 'task', snapshot['revision'], 'execution-key')
                execution_text = harness.runtime.submit.call_args.args[1]
                self.assertIn(execution_phrase, execution_text)
                contract = json.loads(execution_text[execution_text.index('\n{') + 1:])
                self.assertEqual(contract['title'], 'Deliverable 原文')
                self.assertEqual(contract['acceptance'], ['Criterion 原文'])
                self.assertEqual(board.read()['tasks'][0]['status'], 'IN_PROGRESS')
                self.assertEqual(harness.runtime.submit.call_count, 1)
