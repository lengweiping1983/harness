"""形成性点评的快照、证据、幂等与异步收尾业务回归。"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from harness.academy.errors import AcademyError
from harness.academy.evaluation import DIMENSIONS, MAX_SUBMISSION_BYTES, parse_assessment
from harness.academy.files import fingerprint, read_files
from harness.academy.runtime import AcademyRuntime
from harness.academy.service import AcademyService
from harness.core.language import current_language
from harness.i18n import tr
from harness.llm.types import AssistantMessage, LlmCallConfig, TextBlock, ToolUseBlock


def assessment(sources, *, summary='The frozen submission is ready to review.'):
    """由真实来源构造完整量规，避免无证据分数冒充点评。"""
    source = 'note' if sources.get('note') else next(key for key, value in sources.items() if value)
    quote = sources[source][:80]
    return {'summary': summary, 'dimensions': [
        {'id': identity, 'score': index, 'reason': 'This quotation is present in the submission.',
         'evidence': [{'source': source, 'quote': quote}], 'next_step': 'Explain one observable change.'}
        for index, identity in enumerate(DIMENSIONS)], 'next_steps': ['Compare input and output.']}


class AssessmentAdapter:
    """记录模型实际收到的快照和语言，返回确定性点评。"""

    def __init__(self, transform=None, failure=None):
        self.calls = []
        self.transform = transform
        self.failure = failure

    async def complete(self, messages, *, config, tools):
        payload = json.loads(messages[-1].text)
        self.calls.append({'payload': payload, 'system': messages[0].text,
                           'language': current_language.get(), 'config': config, 'tools': tools})
        if self.failure is not None:
            raise self.failure
        value = assessment(payload['sources'])
        if self.transform is not None:
            value = self.transform(value)
        text = value if isinstance(value, str) else json.dumps(value)
        return AssistantMessage(content=(TextBlock(text=text),), model='assessment-fixture')


class AcademyAssessmentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='academy-assessment-')
        self.root = Path(self.temporary.name)
        course = self.root / 'content' / 'assessment-lab'
        (course / 'starter').mkdir(parents=True)
        (course / 'starter/main.py').write_text('question = "What is a Harness?"\nreply = "A task runtime."\n', encoding='utf-8')
        (course / 'check.py').write_text('raise AssertionError("checks must be explicit")\n', encoding='utf-8')
        (course / 'locales').mkdir()
        for language in ('en', 'zh-CN'):
            values = {'title': 'Assessment lab', 'description': 'One frozen submission.',
                      'lesson.title': 'Observe input and output' if language == 'en' else '观察输入输出',
                      'lesson.goal': 'Explain the result.', 'lesson.reading': 'Read, change, explain.',
                      'lesson.exercise': 'Change the reply.', 'lesson.rubric': 'Cite your evidence.'}
            (course / 'locales' / (language + '.json')).write_text(json.dumps(values), encoding='utf-8')
        manifest = {'schema': 1, 'id': 'assessment-lab', 'version': '1', 'source_revision': 'fixture',
                    'title_key': 'title', 'description_key': 'description', 'references': [], 'lessons': [
                        {'id': 'day-01-input-output', 'order': 1, 'milestone': False,
                         'title_key': 'lesson.title', 'goal_key': 'lesson.goal', 'reading_key': 'lesson.reading',
                         'exercise_key': 'lesson.exercise', 'rubric_key': 'lesson.rubric',
                         'starter': 'starter', 'check': 'check.py', 'run_command': ['python', 'main.py'],
                         'source_manifest': [{'path': 'main.py', 'symbol': ''}], 'references': [], 'prerequisites': []}]}
        (course / 'course.json').write_text(json.dumps(manifest), encoding='utf-8')
        self.service = AcademyService(self.root / 'home', self.root / 'content')
        self.project = self.service.create_project('create', 'assessment-lab')['result']['project']
        self.pid = self.project['id']
        self.lesson = self.project['active_lesson_id']
        self.config = LlmCallConfig(model='requested-model')
        self.futures = []

    async def asyncTearDown(self):
        for future in self.futures:
            if not future.done():
                future.cancel()
        await asyncio.sleep(0)
        self.temporary.cleanup()

    def schedule(self, identity):
        """登记可控等待句柄；测试显式调用评估，不自动启动网络。"""
        future = asyncio.get_running_loop().create_future()
        self.futures.append(future)
        return future

    def submit(self, request_id='submit', *, note='I changed the fixed reply and compared the output.', schedule=None, fingerprint=None):
        project = self.service.project(self.pid)
        return self.service.submit(self.pid, request_id, self.lesson, fingerprint or project['fingerprint'], note,
                                   model=self.config.model, schedule=self.schedule if schedule is None else schedule)

    def write(self, text):
        project = self.service.project(self.pid)
        source = self.service.source(self.pid, 'main.py')
        result = self.service.write_file(self.pid, 'write-' + str(project['revision']), 'main.py', text,
                                         source['version'], project['fingerprint'])
        self.assertEqual(result['status'], 'ready', result)

    async def test_broken_syntax_is_submitted_without_running_and_snapshot_is_frozen(self):
        broken = 'def unfinished(:\n    # 学生尚未修好语法\n'
        self.write(broken)
        before = self.service.project(self.pid)
        with patch.object(AcademyRuntime, 'run', side_effect=AssertionError('submission executed code')):
            receipt = self.submit()
            identity = receipt['result']['submission']['id']
            self.write('print("later edit")\n')
            adapter = AssessmentAdapter()
            await self.service.evaluate_submission(identity, adapter, self.config)
        submitted = self.service.submission(self.pid, identity)
        self.assertEqual(submitted['status'], 'completed')
        self.assertEqual(adapter.calls[0]['payload']['sources']['file:main.py'], broken)
        self.assertNotIn('later edit', json.dumps(adapter.calls[0]['payload']))
        self.assertEqual(adapter.calls[0]['tools'], [])
        self.assertEqual(adapter.calls[0]['config'].max_tokens, 6000)
        self.assertNotIn('snapshot', submitted)
        self.assertEqual(submitted['input_fingerprint'], before['fingerprint'])
        self.assertEqual(self.service.runs(self.pid), [])
        self.assertEqual(self.service.project(self.pid)['progress'][0]['state'], 'in_progress')
        self.assertEqual(self.service.project(self.pid)['progress'][0]['evidence_ids'], [])

    async def test_same_request_schedules_and_calls_model_once(self):
        adapter = AssessmentAdapter()
        tasks = []
        def schedule(identity):
            task = asyncio.create_task(self.service.evaluate_submission(identity, adapter, self.config))
            tasks.append(task)
            return task
        first = self.submit(schedule=schedule)
        second = self.submit(schedule=schedule)
        self.assertEqual(first, second)
        await tasks[0]
        self.assertEqual(self.submit(schedule=schedule), first)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(len(self.service.submissions(self.pid)), 1)
        with self.assertRaisesRegex(AcademyError, 'request-conflict'):
            self.submit(note='A different explanation.', schedule=schedule)

    async def test_snapshot_fingerprint_uses_the_same_file_capture(self):
        project_root = Path(self.project['root'])
        original = read_files(project_root)
        captures = []
        def external_edit_after_capture(root):
            captured = read_files(root)
            if root == project_root:
                captures.append(captured)
                if len(captures) == 1:
                    (root / 'main.py').write_text('print("changed outside Harness")\n', encoding='utf-8')
            return captured
        with patch('harness.academy.service.read_files', side_effect=external_edit_after_capture):
            receipt = self.service.submit(self.pid, 'single-capture', self.lesson, self.project['fingerprint'],
                                          'The editor may change after this capture.', schedule=self.schedule)
        submitted = receipt['result']['submission']
        value = self.service.store.get('submission', submitted['id'])
        frozen = {key.removeprefix('file:'): text for key, text in value['snapshot']['sources'].items() if key.startswith('file:')}
        self.assertEqual(frozen, original)
        self.assertEqual(fingerprint(frozen), submitted['input_fingerprint'])
        self.assertNotEqual(self.service.project(self.pid)['fingerprint'], submitted['input_fingerprint'])

    async def test_evaluation_store_wait_does_not_block_the_host_event_loop(self):
        identity = self.submit('locked-store')['result']['submission']['id']
        held = threading.Event()
        release = threading.Event()
        def own_store_lock():
            with self.service.store.lock:
                held.set()
                release.wait(1)
        thread = threading.Thread(target=own_store_lock)
        thread.start()
        self.assertTrue(await asyncio.to_thread(held.wait, 1))
        adapter = AssessmentAdapter()
        task = asyncio.create_task(self.service.evaluate_submission(identity, adapter, self.config))
        pulse = asyncio.Event()
        asyncio.get_running_loop().call_soon(pulse.set)
        started = time.monotonic()
        try:
            await asyncio.wait_for(pulse.wait(), .5)
            self.assertLess(time.monotonic() - started, .5, 'A course database lock blocked the host event loop')
            self.assertEqual(adapter.calls, [])
        finally:
            release.set()
            await asyncio.to_thread(thread.join, 2)
        await asyncio.wait_for(task, 2)
        self.assertEqual(self.service.submission(self.pid, identity)['status'], 'completed')

    async def test_evaluation_result_persistence_does_not_block_the_host_event_loop(self):
        identity = self.submit('locked-result')['result']['submission']['id']
        entered = asyncio.Event()
        continue_response = asyncio.Event()
        class PausedAdapter(AssessmentAdapter):
            async def complete(adapter, messages, *, config, tools):
                entered.set()
                await continue_response.wait()
                return await super().complete(messages, config=config, tools=tools)
        task = asyncio.create_task(self.service.evaluate_submission(identity, PausedAdapter(), self.config))
        await asyncio.wait_for(entered.wait(), 2)
        held = threading.Event()
        release = threading.Event()
        def own_store_lock():
            with self.service.store.lock:
                held.set()
                release.wait(1)
        thread = threading.Thread(target=own_store_lock)
        thread.start()
        self.assertTrue(await asyncio.to_thread(held.wait, 1))
        pulse = asyncio.Event()
        continue_response.set()
        asyncio.get_running_loop().call_soon(pulse.set)
        started = time.monotonic()
        try:
            await asyncio.wait_for(pulse.wait(), .5)
            self.assertLess(time.monotonic() - started, .5, 'Saving a course assessment blocked the host event loop')
            self.assertFalse(task.done())
        finally:
            release.set()
            await asyncio.to_thread(thread.join, 2)
        await asyncio.wait_for(task, 2)
        self.assertEqual(self.service.submission(self.pid, identity)['status'], 'completed')

    async def test_current_language_is_frozen_for_each_submission(self):
        for index, language in enumerate(('en', 'zh-CN', 'en')):
            token = current_language.set(language)
            try:
                expected_system = tr('academy.assessment.system')
                expected_title = self.service.course('assessment-lab')['lessons'][0]['title']
                receipt = self.submit('language-' + str(index))
            finally:
                current_language.reset(token)
            identity = receipt['result']['submission']['id']
            other = current_language.set('zh-CN' if language == 'en' else 'en')
            try:
                adapter = AssessmentAdapter()
                await self.service.evaluate_submission(identity, adapter, self.config)
                self.assertEqual(current_language.get(), 'zh-CN' if language == 'en' else 'en')
                value = self.service.submission(self.pid, identity)
            finally:
                current_language.reset(other)
            self.assertEqual(value['language'], language)
            self.assertEqual(adapter.calls[0]['language'], language)
            self.assertEqual(adapter.calls[0]['system'], expected_system)
            self.assertEqual(adapter.calls[0]['payload']['lesson']['title'], expected_title)

    async def test_submission_reads_are_scoped_to_project(self):
        identity = self.submit()['result']['submission']['id']
        other = self.service.create_project('other-project', 'assessment-lab')['result']['project']['id']
        with self.assertRaisesRegex(AcademyError, 'submission-not-found'):
            self.service.submission(other, identity)
        self.assertEqual(self.service.submissions(other), [])

    async def test_restart_exposes_unknown_without_scheduling_again(self):
        receipt = self.submit()
        identity = receipt['result']['submission']['id']
        self.assertEqual(self.service.submission(self.pid, identity)['status'], 'evaluating')
        reopened = AcademyService(self.root / 'home', self.root / 'content')
        value = reopened.submission(self.pid, identity)
        self.assertEqual((value['status'], value['error_code']), ('unknown', 'assessment-interrupted'))
        def forbidden_schedule(unused):
            self.fail('A restart replayed the model request')
        replay = reopened.submit(self.pid, 'submit', self.lesson, self.project['fingerprint'],
                                 'I changed the fixed reply and compared the output.', schedule=forbidden_schedule)
        self.assertEqual(replay['result']['submission']['id'], identity)
        self.assertEqual(len(reopened.submissions(self.pid)), 1)

    async def test_timeout_and_cancellation_have_explicit_terminal_states(self):
        timed = self.submit('timeout')['result']['submission']['id']
        adapter = AssessmentAdapter(failure=TimeoutError('fixture timeout'))
        await self.service.evaluate_submission(timed, adapter, self.config)
        value = self.service.submission(self.pid, timed)
        self.assertEqual((value['status'], value['error_code']), ('failed', 'assessment-timeout'))
        entered = asyncio.Event()
        class WaitingAdapter:
            async def complete(self, messages, *, config, tools):
                entered.set()
                await asyncio.Event().wait()
        cancelled = self.submit('cancel')['result']['submission']['id']
        task = asyncio.create_task(self.service.evaluate_submission(cancelled, WaitingAdapter(), self.config))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        value = self.service.submission(self.pid, cancelled)
        self.assertEqual((value['status'], value['error_code']), ('unknown', 'assessment-interrupted'))
        self.assertNotIn(cancelled, self.service._evaluating)

    async def test_stale_fingerprint_and_48_kib_limit_reject_before_scheduling(self):
        scheduled = []
        def forbidden_schedule(identity):
            scheduled.append(identity)
            return self.schedule(identity)
        stale = self.submit('stale', fingerprint='0' * 64, schedule=forbidden_schedule)
        self.assertEqual(stale['result']['error_code'], 'stale-project')
        self.write('x' * MAX_SUBMISSION_BYTES)
        too_large = self.submit('oversize', schedule=forbidden_schedule)
        self.assertEqual(too_large['result']['error_code'], 'submission-too-large')
        self.assertEqual(scheduled, [])
        self.assertEqual(self.service.submissions(self.pid), [])

    async def test_invalid_model_output_is_failed_without_passing_course(self):
        identity = self.submit('invalid-output')['result']['submission']['id']
        await self.service.evaluate_submission(identity, AssessmentAdapter(transform=lambda value: 'not json'), self.config)
        value = self.service.submission(self.pid, identity)
        self.assertEqual((value['status'], value['error_code']), ('failed', 'assessment-invalid'))
        self.assertNotIn('assessment', value)
        self.assertEqual(self.service.project(self.pid)['progress'][0]['state'], 'in_progress')

    async def test_model_tool_use_is_rejected(self):
        class ToolAdapter:
            async def complete(self, messages, *, config, tools):
                return AssistantMessage(content=(ToolUseBlock(id='forbidden', name='bash', input={'command': 'echo executed'}),))
        identity = self.submit('tool-output')['result']['submission']['id']
        await self.service.evaluate_submission(identity, ToolAdapter(), self.config)
        self.assertEqual(self.service.submission(self.pid, identity)['error_code'], 'assessment-invalid')


class AcademyAssessmentEvidenceTests(unittest.TestCase):
    def test_real_file_note_and_run_quotes_are_accepted_and_sorted(self):
        sources = {'file:main.py': 'question = "Why?"', 'note': 'I compared both outputs.',
                   'run:run_fixture': '{"status": "failed", "stderr": "SyntaxError"}'}
        value = assessment(sources)
        value['dimensions'][0]['evidence'] = [{'source': 'file:main.py', 'quote': 'question ='}]
        value['dimensions'][1]['evidence'] = [{'source': 'run:run_fixture', 'quote': 'SyntaxError'}]
        value['dimensions'][2]['score'] = None
        value['dimensions'][2]['evidence'] = []
        value['dimensions'].reverse()
        parsed = parse_assessment(json.dumps(value), sources)
        self.assertEqual([item['id'] for item in parsed['dimensions']], list(DIMENSIONS))
        self.assertIsNone(parsed['dimensions'][2]['score'])

    def test_forged_missing_duplicate_or_badly_typed_evidence_is_rejected(self):
        sources = {'file:main.py': 'print("actual")', 'note': 'I changed one reply.'}
        cases = {}
        cases['unknown-source'] = lambda value: value['dimensions'][0]['evidence'][0].update(source='file:other.py')
        cases['forged-quote'] = lambda value: value['dimensions'][0]['evidence'][0].update(quote='not in the frozen submission')
        cases['scored-without-evidence'] = lambda value: value['dimensions'][0].update(evidence=[])
        cases['duplicate-dimension'] = lambda value: value['dimensions'][1].update(id='concept')
        cases['boolean-score'] = lambda value: value['dimensions'][0].update(score=True)
        cases['oversized-score'] = lambda value: value['dimensions'][0].update(score=6)
        cases['unknown-dimension'] = lambda value: value['dimensions'][0].update(id='completion')
        cases['missing-dimension'] = lambda value: value['dimensions'].pop()
        cases['blank-reason'] = lambda value: value['dimensions'][0].update(reason=' ')
        cases['blank-next-step'] = lambda value: value['dimensions'][0].update(next_step=' ')
        cases['extra-field'] = lambda value: value.update(passed=True)
        for label, mutate in cases.items():
            with self.subTest(label=label):
                value = deepcopy(assessment(sources))
                mutate(value)
                with self.assertRaisesRegex(AcademyError, 'assessment-invalid'):
                    parse_assessment(json.dumps(value), sources)
        for raw in ('not json', 'null', '{}', '[]'):
            with self.subTest(raw=raw), self.assertRaisesRegex(AcademyError, 'assessment-invalid'):
                parse_assessment(raw, sources)


if __name__ == '__main__':
    unittest.main()
