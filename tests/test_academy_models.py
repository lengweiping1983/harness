"""模型实验的单次执行、真实证据、语言与中断恢复回归。"""
from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import shutil
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from harness.academy.catalog import AcademyCatalog
from harness.academy.errors import AcademyError
from harness.academy.evaluation import DIMENSIONS, parse_assessment
from harness.academy.runtime import AcademyRuntime
from harness.academy.service import AcademyService
from harness.core.language import current_language
from harness.llm.types import AssistantMessage, LlmCallConfig, StreamEvent, TextBlock, ToolUseBlock
from harness.llm.adapter import OpenRouterAdapter
from harness.llm.transport import Transport
from harness.web.api.academy import build_router
from harness.web.context import WebContext
from harness.web.errors import ApiError
from harness.web.router import Request


class ModelAdapter:
    """记录实际请求边界，返回可比较的原始文本。"""

    def __init__(self, response='The library opens at 09:00 on Monday. Weekend hours are unknown.', failure=None):
        self.calls = []
        self.response = response
        self.failure = failure

    async def complete(self, messages, *, config, tools, on_event=None):
        self.calls.append((messages, config, tools, current_language.get()))
        if self.failure:
            raise self.failure
        return AssistantMessage(content=(TextBlock(text=self.response),), model='actual-model')


class AcademyModelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='academy-model-')
        self.root = Path(self.temporary.name)
        self.library = self.root / 'library'
        shutil.copytree(AcademyCatalog().directory('llm-foundations'), self.library / 'llm-foundations')
        self.service = AcademyService(self.root / 'home', self.library)
        project = self.service.create_project('create', 'llm-foundations')['result']['project']
        self.pid = project['id']
        self.lesson = 'task-contract'
        self.project = self.service.start_lesson(self.pid, 'open', self.lesson, project['revision'], project['fingerprint'])['result']['project']
        self.config = LlmCallConfig(model='configured-model')
        self.futures = []

    async def asyncTearDown(self):
        for future in self.futures:
            if not future.done():
                future.cancel()
        await asyncio.gather(*self.futures, return_exceptions=True)
        self.temporary.cleanup()

    def schedule(self, identity):
        """测试显式驱动实验协程，登记只读回查使用的运行所有权。"""
        future = asyncio.get_running_loop().create_future()
        self.futures.append(future)
        return future

    async def test_native_model_stream_is_ephemeral_and_final_answer_remains_authoritative(self):
        class PausedStream(Transport):
            """真实适配器收到前缀后等待，以核对中间投影和取消边界。"""
            def __init__(adapter):
                adapter.entered = asyncio.Event()
                adapter.release = asyncio.Event()
                adapter.calls = 0
                adapter.prefix = '第一段 / first part'

            async def post_stream(adapter, url, *, headers, body, timeout):
                adapter.calls += 1
                yield ('data: ' + json.dumps({'choices': [{'delta': {'reasoning': 'PRIVATE_THINKING', 'content': adapter.prefix}, 'finish_reason': None}]}) + '\n\n').encode('utf-8')
                adapter.entered.set()
                await adapter.release.wait()
                yield ('data: ' + json.dumps({'model': 'stream-model', 'choices': [{'delta': {'content': '，第二段 / second part'}, 'finish_reason': 'stop'}]}) + '\n\n').encode('utf-8')
                yield b'data: [DONE]\n\n'

        for cancelled in (False, True):
            receipt = self.start('stream-' + str(cancelled))
            identity = receipt['result']['model_run']['id']
            transport = PausedStream()
            if cancelled:
                transport.prefix = '中' * 20000
            adapter = OpenRouterAdapter('fixture-only', model=self.config.model, transport=transport, max_retries=0)
            task = asyncio.create_task(self.service.experiments.evaluate(identity, adapter, self.config))
            try:
                await asyncio.wait_for(transport.entered.wait(), 3)
                observed = self.service.experiments.runs(self.pid)[0]
                self.assertEqual(observed['status'], 'running')
                self.assertEqual(observed['response'], '')
                self.assertEqual(observed['partial_response'], transport.prefix[:16000])
                self.service.experiments._stream('foreign', StreamEvent(kind='text', text='FOREIGN_TEXT'))
                self.assertNotIn('foreign', self.service.experiments._drafts)
                self.assertNotIn('partial_response', self.service.store.get('model-run', identity))
                reopened = AcademyService(self.root / 'home', self.library)
                recovered = reopened.experiments.runs(self.pid)[0]
                self.assertEqual(recovered['status'], 'unknown')
                self.assertNotIn('partial_response', recovered)
                self.assertTrue(all(row['state'] != 'completed' for row in self.service.project(self.pid)['progress']))
                if cancelled:
                    task.cancel()
            finally:
                transport.release.set()
                result = (await asyncio.gather(task, return_exceptions=True))[0]
            if cancelled:
                self.assertIsInstance(result, asyncio.CancelledError)
            elif isinstance(result, BaseException):
                raise result
            final = self.service.experiments.runs(self.pid)[0]
            self.assertEqual(final['status'], 'unknown' if cancelled else 'completed')
            self.assertEqual(final['response'], '' if cancelled else '第一段 / first part，第二段 / second part')
            self.assertNotIn('partial_response', final)
            self.assertNotIn(identity, self.service.experiments._drafts)
            self.service.experiments._stream(identity, StreamEvent(kind='text', text='LATE_TEXT'))
            self.assertNotIn(identity, self.service.experiments._drafts)
            self.assertNotIn('partial_response', self.service.store.get('model-run', identity))
            self.assertEqual(transport.calls, 1)
            self.assertEqual(self.service.runs(self.pid), [])

    def start(self, request='experiment', prompt='Use only the material. 周末未知。', **kwargs):
        return self.service.experiments.start(self.pid, request, self.lesson, self.project['fingerprint'], prompt,
            model=self.config.model, schedule=self.schedule, **kwargs)

    async def test_experiment_calls_once_with_frozen_input_and_preserves_raw_output(self):
        with patch.object(AcademyRuntime, 'run', side_effect=AssertionError('模型实验不得执行代码')):
            receipt = self.start()
            self.assertEqual(self.start(), receipt)
            identity = receipt['result']['model_run']['id']
            self.assertTrue(receipt['result']['model_run']['system'].strip())
            adapter = ModelAdapter(response='原始回答 / original {response}')
            await self.service.experiments.evaluate(identity, adapter, self.config)
            with self.assertRaisesRegex(AcademyError, 'model-interrupted'):
                await self.service.experiments.evaluate(identity, adapter, self.config)
        run = self.service.experiments.runs(self.pid)[0]
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(run['response'], '原始回答 / original {response}')
        self.assertEqual(run['model'], 'actual-model')
        self.assertEqual(len(adapter.calls), 1)
        messages, config, tools, _ = adapter.calls[0]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[-1].text, 'Use only the material. 周末未知。')
        self.assertEqual(messages[0].text, run['system'])
        self.assertEqual(config.max_tokens, 4000)
        self.assertEqual(tools, [])
        self.assertEqual(self.service.files(self.pid), [])
        self.assertEqual(self.service.runs(self.pid), [])
        self.assertTrue(all(row['state'] == 'in_progress' for row in self.service.project(self.pid)['progress']))
        self.assertEqual(self.service.receipt('experiment'), receipt)
        with self.assertRaisesRegex(AcademyError, 'request-conflict'):
            self.start(prompt='Changed input')

    async def test_stop_owned_model_request_keeps_receipts_and_discards_only_transient_answer(self):
        entered = asyncio.Event()
        class Waiting(ModelAdapter):
            async def complete(adapter, *args, **kwargs):
                adapter.calls.append(args)
                kwargs['on_event'](StreamEvent(kind='text', text='VISIBLE_PARTIAL'))
                entered.set()
                await asyncio.Event().wait()
        adapter = Waiting()
        def schedule(identity):
            task = asyncio.create_task(self.service.experiments.evaluate(identity, adapter, self.config))
            self.futures.append(task)
            return task
        with patch.object(self, 'schedule', side_effect=schedule):
            receipt = self.start()
        identity = receipt['result']['model_run']['id']
        await asyncio.wait_for(entered.wait(), 3)
        other = self.service.create_project('other-project', 'llm-foundations')['result']['project']
        foreign = self.service.experiments.stop(other['id'], identity, 'foreign-stop')
        self.assertEqual(foreign['result']['error_code'], 'run-not-found')
        self.assertFalse(self.futures[-1].done())
        stopped = self.service.experiments.stop(self.pid, identity, 'stop')
        self.assertEqual(stopped['request_id'], 'stop')
        self.assertEqual(stopped['result']['model_run']['request_id'], 'experiment')
        self.assertTrue(stopped['result']['model_run']['stopping'])
        self.assertNotIn('partial_response', stopped['result']['model_run'])
        self.assertEqual(self.service.experiments.stop(self.pid, identity, 'stop'), stopped)
        await asyncio.gather(self.futures[-1], return_exceptions=True)
        observed = self.service.experiments.runs(self.pid)[0]
        self.assertEqual((observed['status'], observed['error_code']), ('unknown', 'model-interrupted'))
        self.assertEqual(observed['response'], '')
        self.assertNotIn('partial_response', observed)
        self.assertEqual(len(adapter.calls), 1)
        self.assertFalse(self.service.experiments._active)
        self.assertFalse(self.service.experiments._scheduled)
        self.assertFalse(self.service.experiments._stopping)
        terminal = self.service.experiments.stop(self.pid, identity, 'stop-again')
        self.assertEqual(terminal['result']['model_run']['status'], 'unknown')
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(self.service.runs(self.pid), [])

    async def test_stop_during_model_claim_waits_for_owned_cleanup(self):
        entered, release = threading.Event(), threading.Event()
        original = self.service.experiments._claim
        def paused_claim(identity):
            value = original(identity)
            entered.set()
            if not release.wait(3):
                raise RuntimeError('claim gate timed out')
            return value
        adapter = ModelAdapter()
        def schedule(identity):
            task = asyncio.create_task(self.service.experiments.evaluate(identity, adapter, self.config))
            self.futures.append(task)
            return task
        with patch.object(self.service.experiments, '_claim', side_effect=paused_claim), patch.object(self, 'schedule', side_effect=schedule):
            receipt = self.start()
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                self.service.experiments.stop(self.pid, receipt['result']['model_run']['id'], 'stop-claim')
                self.assertTrue(self.service.experiments._active)
            finally:
                release.set()
                await asyncio.gather(self.futures[-1], return_exceptions=True)
        self.assertEqual(adapter.calls, [])
        self.assertFalse(self.service.experiments._active)
        self.assertEqual(self.service.experiments.runs(self.pid)[0]['status'], 'unknown')

    async def test_running_experiment_keeps_project_busy_until_persisted(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class Paused(ModelAdapter):
            async def complete(adapter, *args, **kwargs):
                entered.set()
                await release.wait()
                return await super().complete(*args, **kwargs)
        identity = self.start()['result']['model_run']['id']
        adapter = Paused()
        task = asyncio.create_task(self.service.experiments.evaluate(identity, adapter, self.config))
        self.futures.append(task)
        await asyncio.wait_for(entered.wait(), 2)
        self.assertEqual(self.start('second')['result']['error_code'], 'model-busy')
        self.assertEqual(self.service.experiments.runs(self.pid)[0]['status'], 'running')
        release.set()
        await asyncio.wait_for(task, 2)
        self.assertIn('model_run', self.start('third')['result'])

    async def test_global_capacity_and_completion_evidence_remain_project_scoped(self):
        projects = []
        for index in range(2):
            project = self.service.create_project('project-' + str(index), 'llm-foundations')['result']['project']
            projects.append(self.service.start_lesson(project['id'], 'open-' + str(index), self.lesson,
                project['revision'], project['fingerprint'])['result']['project'])
        first = self.start()['result']['model_run']['id']
        second, third = projects
        def start(project, request):
            return self.service.experiments.start(project['id'], request, self.lesson, project['fingerprint'], 'prompt', schedule=self.schedule)
        self.assertEqual(start(second, 'second')['result']['model_run']['status'], 'running')
        self.assertEqual(start(third, 'third')['result']['error_code'], 'model-busy')
        await self.service.experiments.evaluate(first, ModelAdapter(), self.config)
        self.assertEqual(start(third, 'third-explicit')['result']['model_run']['status'], 'running')
        self.assertEqual([run['id'] for run in self.service.experiments.runs(self.pid)], [first])
        lesson = self.service.course('llm-foundations')['lessons'][1]
        answers = [{'check_id': check['id'], 'option_id': check['answer_id']} for check in lesson['knowledge_checks']]
        rejected = self.service.complete_study(third['id'], 'foreign-evidence', self.lesson,
            third['fingerprint'], 'Only the first project received a response.', answers)
        self.assertEqual(rejected['result']['error_code'], 'model-incomplete')

    async def test_each_language_freezes_actual_system_and_user_input(self):
        systems = []
        for index, language in enumerate(('en', 'zh-CN', 'en')):
            with current_language.set(language):
                lesson = self.service.course('llm-foundations')['lessons'][1]
                receipt = self.start('language-' + str(index), lesson['model_prompt'])
                systems.append(lesson['model_system'])
            with current_language.set('zh-CN' if language == 'en' else 'en'):
                adapter = ModelAdapter()
                await self.service.experiments.evaluate(receipt['result']['model_run']['id'], adapter, self.config)
            self.assertEqual(adapter.calls[0][0][0].text, systems[-1])
            self.assertEqual(adapter.calls[0][0][-1].text, lesson['model_prompt'])
            self.assertEqual(adapter.calls[0][3], language)
        self.assertEqual(systems[0], systems[2])
        self.assertNotEqual(systems[0], systems[1])

    async def test_completion_requires_model_evidence_and_submission_freezes_that_evidence(self):
        lesson = self.service.course('llm-foundations')['lessons'][1]
        answers = [{'check_id': check['id'], 'option_id': check['answer_id']} for check in lesson['knowledge_checks']]
        def complete(request):
            return self.service.complete_study(self.pid, request, self.lesson, self.project['fingerprint'], 'I compared the supported fact and unknown weekend hours.', answers)
        self.assertEqual(complete('before')['result']['error_code'], 'model-incomplete')
        identity = self.start()['result']['model_run']['id']
        self.assertEqual(complete('during')['result']['error_code'], 'model-incomplete')
        await self.service.experiments.evaluate(identity, ModelAdapter(), self.config)
        result = complete('after')
        row = next(row for row in result['result']['project']['progress'] if row['lesson_id'] == self.lesson)
        self.assertEqual(row['state'], 'completed')
        evidence = self.service.store.get('study-completion', row['evidence_ids'][0])
        self.assertEqual(evidence['model_run_id'], identity)
        receipt = self.service.submit(self.pid, 'submit', self.lesson, self.project['fingerprint'], 'My reflection', schedule=self.schedule)
        sources = self.service.store.get('submission', receipt['result']['submission']['id'])['snapshot']['sources']
        self.assertEqual(sources['model:' + identity + ':response'], ModelAdapter().response)
        await self.service.experiments.evaluate(self.start('new')['result']['model_run']['id'], ModelAdapter('Later response'), self.config)
        self.assertNotIn('Later response', json.dumps(sources))

    async def test_feedback_can_quote_multiline_model_output_verbatim(self):
        text = 'Original "quoted value"\n第二行 / {literal}'
        identity = self.start()['result']['model_run']['id']
        await self.service.experiments.evaluate(identity, ModelAdapter(text), self.config)
        receipt = self.service.submit(self.pid, 'quote-output', self.lesson, self.project['fingerprint'], 'I checked both lines.', schedule=self.schedule)
        sources = self.service.store.get('submission', receipt['result']['submission']['id'])['snapshot']['sources']
        matches = [key for key, value in sources.items() if key.startswith('model:' + identity + ':') and value == text]
        self.assertEqual(len(matches), 1)
        key = matches[0]
        value = {'summary': 'The quoted output is present.', 'dimensions': [
            {'id': dimension, 'score': 3, 'reason': 'This quotation is present in the frozen output.',
             'evidence': [{'source': key, 'quote': text}], 'next_step': 'Compare the result with the prompt.'}
            for dimension in DIMENSIONS], 'next_steps': ['Explain one changed requirement.']}
        self.assertEqual(parse_assessment(json.dumps(value), sources)['dimensions'][0]['evidence'][0]['quote'], text)

    async def test_reopen_projects_and_receipts_do_not_replay_unconfirmed_calls(self):
        receipt = self.start()
        reopened = AcademyService(self.root / 'home', self.library)
        self.assertEqual(reopened.receipt('experiment'), receipt)
        self.assertEqual(reopened.experiments.runs(self.pid)[0]['status'], 'unknown')
        with self.assertRaisesRegex(AcademyError, 'model-interrupted'):
            await reopened.experiments.evaluate(receipt['result']['model_run']['id'], ModelAdapter(), self.config)
        self.assertEqual(reopened.experiments.start(self.pid, 'experiment', self.lesson,
            self.project['fingerprint'], 'Use only the material. 周末未知。', schedule=lambda _: self.fail('重启不得重新调度')), receipt)

    async def test_failures_keep_original_input_and_do_not_grant_completion(self):
        cases = [('empty', ModelAdapter(''), 'failed', 'model-invalid'),
                 ('large', ModelAdapter('中' * 30000), 'failed', 'model-invalid'),
                 ('timeout', ModelAdapter(failure=TimeoutError()), 'unknown', 'model-timeout'),
                 ('provider', ModelAdapter(failure=RuntimeError('private credential')), 'failed', 'model-failed')]
        for name, adapter, status, code in cases:
            with self.subTest(name=name):
                identity = self.start(name)['result']['model_run']['id']
                await self.service.experiments.evaluate(identity, adapter, self.config)
                run = self.service.experiments.runs(self.pid)[0]
                self.assertEqual((run['status'], run['error_code']), (status, code))
                self.assertEqual(run['prompt'], 'Use only the material. 周末未知。')
                self.assertNotIn('private credential', json.dumps(run))
                self.assertEqual(len(adapter.calls), 1)
        class ToolAdapter(ModelAdapter):
            async def complete(adapter, *args, **kwargs):
                return AssistantMessage(content=(ToolUseBlock(id='call', name='read', input={'path': 'private'}),))
        identity = self.start('tool')['result']['model_run']['id']
        await self.service.experiments.evaluate(identity, ToolAdapter(), self.config)
        self.assertEqual(self.service.experiments.runs(self.pid)[0]['error_code'], 'model-invalid')

    async def test_cancelled_experiment_persists_unknown_and_never_retries(self):
        entered = asyncio.Event()
        class Waiting(ModelAdapter):
            async def complete(adapter, *args, **kwargs):
                entered.set()
                await asyncio.Future()
        identity = self.start()['result']['model_run']['id']
        task = asyncio.create_task(self.service.experiments.evaluate(identity, Waiting(), self.config))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.service.experiments.runs(self.pid)[0]['status'], 'unknown')

    async def test_output_limit_keeps_partial_response_without_completing_the_experiment(self):
        for reason in ('length', 'max_tokens'):
            class Limited(ModelAdapter):
                async def complete(adapter, *args, **kwargs):
                    return replace(await super().complete(*args, **kwargs), stop_reason=reason)
            identity = self.start('limited-' + reason)['result']['model_run']['id']
            adapter = Limited('Partial response / 未完成的回答')
            await self.service.experiments.evaluate(identity, adapter, self.config)
            run = self.service.experiments.runs(self.pid)[0]
            self.assertEqual((run['status'], run['error_code']), ('failed', 'model-truncated'))
            self.assertEqual(run['response'], 'Partial response / 未完成的回答')
            lesson = self.service.course('llm-foundations')['lessons'][1]
            answers = [{'check_id': check['id'], 'option_id': check['answer_id']} for check in lesson['knowledge_checks']]
            rejected = self.service.complete_study(self.pid, 'complete-' + reason, self.lesson,
                self.project['fingerprint'], 'The response stopped before finishing.', answers)
            self.assertEqual(rejected['result']['error_code'], 'model-incomplete')
            self.assertEqual(len(adapter.calls), 1)

    async def test_repeated_cancellation_waits_for_owned_result_persistence(self):
        identity = self.start()['result']['model_run']['id']
        entered, release = threading.Event(), threading.Event()
        finish = self.service.experiments._finish
        def blocked_finish(value):
            entered.set()
            if not release.wait(3):
                raise AssertionError('持久收尾屏障未释放')
            finish(value)
        with patch.object(self.service.experiments, '_finish', side_effect=blocked_finish):
            task = asyncio.create_task(self.service.experiments.evaluate(identity, ModelAdapter(), self.config))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done(), '取消等待不能遗落已经开始的持久收尾')
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.service.experiments.runs(self.pid)[0]['status'], 'completed')

    async def test_persistence_failure_preserves_unknown_and_original_error(self):
        identity = self.start()['result']['model_run']['id']
        original = self.service.store.put
        failure = OSError('结果写盘失败')
        def fail_result(kind, value):
            if kind == 'model-run' and value['status'] == 'completed':
                raise failure
            return original(kind, value)
        adapter = ModelAdapter()
        with patch.object(self.service.store, 'put', side_effect=fail_result):
            with self.assertRaises(OSError) as caught:
                await self.service.experiments.evaluate(identity, adapter, self.config)
        self.assertIs(caught.exception, failure)
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(self.service.experiments.runs(self.pid)[0]['status'], 'unknown')
        with self.assertRaisesRegex(AcademyError, 'model-interrupted'):
            await self.service.experiments.evaluate(identity, adapter, self.config)
        self.assertEqual(len(adapter.calls), 1)

    async def test_cancellation_and_persistence_failure_preserve_both_original_errors(self):
        entered = asyncio.Event()
        class Waiting(ModelAdapter):
            async def complete(adapter, *args, **kwargs):
                entered.set()
                await asyncio.Future()
        identity = self.start()['result']['model_run']['id']
        task = asyncio.create_task(self.service.experiments.evaluate(identity, Waiting(), self.config))
        await asyncio.wait_for(entered.wait(), 2)
        failure = OSError('取消结果写盘失败')
        with patch.object(self.service.store, 'put', side_effect=failure):
            task.cancel()
            with self.assertRaises(BaseExceptionGroup) as caught:
                await task
        self.assertEqual(len(caught.exception.exceptions), 2)
        self.assertIsInstance(caught.exception.exceptions[0], asyncio.CancelledError)
        self.assertIs(caught.exception.exceptions[1], failure)
        self.assertEqual(self.service.experiments.runs(self.pid)[0]['status'], 'unknown')
        adapter = ModelAdapter()
        with self.assertRaisesRegex(AcademyError, 'model-interrupted'):
            await self.service.experiments.evaluate(identity, adapter, self.config)
        self.assertEqual(adapter.calls, [])

    async def test_invalid_prompt_stale_course_and_cross_lesson_input_are_rejected_before_scheduling(self):
        for index, prompt in enumerate((None, False, 3, {}, '', '  ', 'x' * 8001)):
            self.assertEqual(self.start('invalid-' + str(index), prompt)['result']['error_code'], 'invalid-request')
        stale = self.service.experiments.start(self.pid, 'stale', self.lesson, 'a' * 64, 'prompt', schedule=self.schedule)
        self.assertEqual(stale['result']['error_code'], 'stale-project')
        wrong = self.service.experiments.start(self.pid, 'wrong', 'instruction-data', self.project['fingerprint'], 'prompt', schedule=self.schedule)
        self.assertEqual(wrong['result']['error_code'], 'lesson-not-active')
        self.assertEqual(self.futures, [])
        self.assertEqual(self.service.experiments.runs(self.pid), [])

    async def test_unavailable_model_and_corrupt_records_are_explicit(self):
        receipt = self.service.experiments.start(self.pid, 'unavailable', self.lesson, self.project['fingerprint'], 'prompt')
        value = receipt['result']['model_run']
        self.assertEqual(value['error_code'], 'model-unavailable')
        original = self.service.store.get('model-run', value['id'])
        self.service.store.put('model-run', {**original, 'response': None})
        with self.assertRaisesRegex(AcademyError, 'store-invalid'):
            self.service.experiments.runs(self.pid)
        self.assertIsNone(self.service.store.get('model-run', value['id'])['response'])

    async def test_scheduler_rejection_releases_false_running_state_and_never_replays(self):
        failure = RuntimeError('调度器已关闭')
        calls = []
        def reject(identity):
            calls.append(identity)
            raise failure
        with self.assertRaises(RuntimeError) as caught:
            self.service.experiments.start(self.pid, 'rejected-schedule', self.lesson, self.project['fingerprint'], 'prompt', schedule=reject)
        self.assertIs(caught.exception, failure)
        self.assertEqual(self.service.receipt('rejected-schedule')['status'], 'unknown')
        run = self.service.experiments.runs(self.pid)[0]
        self.assertEqual((run['status'], run['error_code']), ('unknown', 'model-interrupted'))
        with self.assertRaisesRegex(AcademyError, 'outcome-unknown'):
            self.service.experiments.start(self.pid, 'rejected-schedule', self.lesson, self.project['fingerprint'], 'prompt', schedule=reject)
        self.assertEqual(calls, [run['id']])
        self.assertEqual(self.start('new-explicit-request')['result']['model_run']['status'], 'running')

    async def test_host_shutdown_closes_unscheduled_coroutine_and_reports_unknown(self):
        adapter = ModelAdapter()
        harness = SimpleNamespace(config=SimpleNamespace(home=self.root / 'home'), adapter=adapter,
            agent=SimpleNamespace(config=self.config))
        ctx = WebContext(harness, asyncio.get_running_loop(), shutting_down=True)
        router = build_router(ctx)
        path = '/api/v1/academy/projects/' + self.pid + '/model-runs'
        post, params = router.resolve('POST', path)
        body = {'request_id': 'host-shutdown', 'lesson_id': self.lesson, 'expected_fingerprint': self.project['fingerprint'], 'prompt': 'prompt'}
        with self.assertRaises(ApiError) as caught:
            post(Request('POST', path, {}, {}, json.dumps(body).encode('utf-8'), client_ip='127.0.0.1'), **params)
        self.assertEqual(caught.exception.code, 'server/unavailable')
        get, params = router.resolve('GET', path)
        response = get(Request('GET', path, {}, {}, b'', client_ip='127.0.0.1'), **params)
        self.assertEqual(json.loads(response.body)['model_runs'][0]['status'], 'unknown')
        self.assertEqual(adapter.calls, [])

    async def test_corrupt_running_input_is_rejected_before_requesting_the_model(self):
        receipt = self.start()
        identity = receipt['result']['model_run']['id']
        original = self.service.store.get('model-run', identity)
        for mutation in ({'system': 'x' * 8001}, {'response': 'Unexpected stored output'},
                         {'error_code': 'model-failed'}, {'completed_at': 1}):
            damaged = {**original, **mutation}
            self.service.store.put('model-run', damaged)
            adapter = ModelAdapter()
            with self.assertRaisesRegex(AcademyError, 'store-invalid'):
                await self.service.experiments.evaluate(identity, adapter, self.config)
            self.assertEqual(adapter.calls, [])
            self.assertEqual(self.service.store.get('model-run', identity), damaged)

    async def test_corrupt_cached_receipt_is_never_returned_as_a_success_or_replayed(self):
        receipt = self.start()
        receipt['result']['model_run']['project_id'] = 'another-project'
        with closing(self.service.store._db()) as db, db:
            db.execute('UPDATE calls SET body=? WHERE id=?', (json.dumps(receipt), 'experiment'))
        with self.assertRaisesRegex(AcademyError, 'store-invalid'):
            self.start()
        self.assertEqual(len(self.futures), 1)
        with closing(self.service.store._db()) as db:
            self.assertEqual(json.loads(db.execute('SELECT body FROM calls WHERE id=?', ('experiment',)).fetchone()[0]), receipt)

    async def test_catalog_rejects_missing_blank_or_misplaced_model_resources(self):
        path = self.library / 'llm-foundations' / 'course.json'
        original = path.read_text(encoding='utf-8')
        for mutation in ('missing', 'wrong-mode', 'code-entry'):
            manifest = json.loads(original)
            lesson = manifest['lessons'][1]
            if mutation == 'missing':
                del lesson['model_system_key']
            elif mutation == 'wrong-mode':
                lesson['mode'] = 'study'
            else:
                lesson['starter'] = 'starter'
            path.write_text(json.dumps(manifest), encoding='utf-8')
            with self.assertRaisesRegex(AcademyError, 'catalog-invalid'):
                self.service.course('llm-foundations')
        path.write_text(original, encoding='utf-8')
        resource = self.library / 'llm-foundations' / 'locales' / 'en.json'
        values = json.loads(resource.read_text(encoding='utf-8'))
        for value in ('   ', 'x' * 8001):
            values['lesson.task-contract.model_system'] = value
            resource.write_text(json.dumps(values), encoding='utf-8')
            with self.assertRaisesRegex(AcademyError, 'catalog-invalid'):
                self.service.course('llm-foundations')

    async def test_model_routes_enforce_local_access_and_schedule_the_actual_host_adapter(self):
        adapter, tasks = ModelAdapter(), []
        def spawn(coroutine):
            task = asyncio.create_task(coroutine)
            tasks.append(task)
            return task
        ctx = SimpleNamespace(harness=SimpleNamespace(config=SimpleNamespace(home=self.root / 'home'),
            adapter=adapter, agent=SimpleNamespace(config=self.config)), spawn=spawn)
        router = build_router(ctx)
        path = '/api/v1/academy/projects/' + self.pid + '/model-runs'
        handler, params = router.resolve('POST', path)
        body = {'request_id': 'http-model', 'lesson_id': self.lesson, 'expected_fingerprint': self.project['fingerprint'], 'prompt': '原文 prompt'}
        for address, subject in [('203.0.113.1', None), ('127.0.0.1', 'guest')]:
            with self.assertRaises(ApiError) as caught:
                handler(Request('POST', path, {}, {}, json.dumps(body).encode('utf-8'), client_ip=address, subject=subject), **params)
            self.assertEqual(caught.exception.status, 403)
        response = handler(Request('POST', path, {}, {}, json.dumps(body).encode('utf-8'), client_ip='127.0.0.1'), **params)
        self.assertEqual(json.loads(response.body)['result']['model_run']['prompt'], '原文 prompt')
        await tasks[0]
        self.assertEqual(len(adapter.calls), 1)
        get, params = router.resolve('GET', path)
        result = get(Request('GET', path, {}, {}, b'', client_ip='127.0.0.1'), **params)
        self.assertEqual(json.loads(result.body)['model_runs'][0]['model'], 'actual-model')
