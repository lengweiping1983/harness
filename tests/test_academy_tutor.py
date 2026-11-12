"""原生带练的真实模型工具循环、隔离运行与持久回执回归。"""
from __future__ import annotations

import asyncio
from contextlib import closing
from concurrent.futures import Future
import copy
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.academy.errors import AcademyError
from harness.academy.runtime import AcademyRuntime
from harness.academy.service import AcademyService
from harness.core.language import current_language
from harness.i18n import tr
from harness.llm.adapter import OpenRouterAdapter
from harness.llm.transport import Transport
from harness.llm.types import LlmCallConfig
from harness.web.api.academy import build_router
from harness.web.router import Request
from harness.web.errors import ApiError
from types import SimpleNamespace
from tests.test_academy import write_engine_fixture


class TutorTransport(Transport):
    """确定性模型会读代码、发起真实工具调用，再引用宿主返回的输出。"""

    def __init__(self, *, repeat=False):
        self.requests = []
        self.repeat = repeat

    @staticmethod
    def response(request, repeat=False):
        tools = {tool['function']['name'] for tool in request.get('tools', [])}
        results = [message for message in request['messages'] if message.get('role') == 'tool']
        context = next(json.loads(message['content']) for message in request['messages'] if message.get('role') == 'user')
        history = context.get('history', [])
        if history and history[-1].get('context') and not any(context.get(key) for key in ('knowledge_check', 'evidence_run_id', 'proposal_path')):
            # 夹具只核对历史材料的实际消费，不代表在线模型的语义判断质量。
            previous = history[-1]['context']
            context = {**context, **{key: previous[key] for key in ('knowledge_check', 'model_run', 'comparison_model_run', 'runs', 'evidence_run_id', 'comparison_run_id') if key in previous}}
        if context.get('knowledge_check'):
            check = context['knowledge_check']
            return {'content': 'Selected self-check / 本次自测：' + check['prompt'] + '\n\nYour choice / 你的选择：' + check['selected']['label'] + '\n\nWhat observation would you check before choosing again?'}
        if context.get('comparison_model_run'):
            primary, previous = context['model_run'], context['comparison_model_run']
            return {'content': 'Recorded model comparison / 模型实验对照：' + previous['id'] + ' → ' + primary['id'] + '\n\nEarlier prompt / 之前提示：' + previous['prompt'] + '\n\nEarlier response / 之前回答：' + previous['response'] + '\n\nSelected prompt / 当前提示：' + primary['prompt'] + '\n\nSelected response / 当前回答：' + primary['response']}
        if context.get('model_run'):
            evidence = context['model_run']
            return {'content': 'Recorded model experiment / 已记录的模型实验：' + evidence['id'] + '\n\nPrompt / 提示：\n' + evidence['prompt'] + '\n\nResponse / 回答：\n' + evidence['response']}
        if context.get('comparison_run_id'):
            primary = next(run for run in context['runs'] if run['id'] == context['evidence_run_id'])
            baseline = next(run for run in context['runs'] if run['id'] == context['comparison_run_id'])
            return {'content': 'Recorded comparison / 已记录的对照：' + baseline['id'] + ' → ' + primary['id'] + '\n\nBefore:\n```text\n' + baseline['result']['stdout'] + baseline['result']['stderr'] + '```\n\nAfter:\n```text\n' + primary['result']['stdout'] + primary['result']['stderr'] + '```\n\nWhat single input would you hold fixed in your next experiment?'}
        if context.get('evidence_run_id'):
            evidence = next(run for run in context['runs'] if run['id'] == context['evidence_run_id'])
            return {'content': 'Recorded result / 已记录的结果：' + evidence['id'] + '\n\n```text\n' + evidence['result']['stdout'] + evidence['result']['stderr'] + '```\n\nWhich line would you inspect next?'}
        if context.get('proposal_path') and results:
            if any('draft_only' in message['content'] for message in results):
                return {'content': 'Here is one small proposed change / 一处小改动。Review it, predict the output, then try it yourself.'}
            name, args = 'propose_edit', {'path': context['proposal_path'], 'old_text': 'value = 1', 'new_text': 'value = 2'}
            return {'tool_calls': [{'index': 0, 'id': 'fixture-proposal', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}
        if not results and 'inspect_file' in tools:
            name, args = 'inspect_file', {'path': context.get('proposal_path') or context['files'][0]}
        elif 'run_example' in tools and (not any('"kind": "run"' in message['content'] for message in results) or repeat and len(results) < 3):
            name, args = 'run_example', {}
        else:
            output = next((message['content'] for message in reversed(results) if '"kind": "run"' in message['content']), '')
            if output:
                result = json.loads(output)['result']
                return {'content': 'Native tutor / 原生带练。Here is the actual output:\n\n```text\n' + result['stdout'] + result['stderr'] + '```\n\nWhat would you change next?'}
            return {'content': 'Native tutor / 原生带练。Look at the value assigned before printing. What do you expect to see?'}
        return {'tool_calls': [{'index': 0, 'id': 'fixture-' + str(len(results)), 'type': 'function',
                               'function': {'name': name, 'arguments': json.dumps(args)}}]}

    async def post_stream(self, url, *, headers, body, timeout):
        request = json.loads(body)
        self.requests.append(request)
        delta = self.response(request, self.repeat)
        for chunk in [{'model': 'native-fixture-model', 'choices': [{'delta': delta, 'finish_reason': None}]},
                      {'choices': [{'delta': {}, 'finish_reason': 'tool_calls' if 'tool_calls' in delta else 'stop'}],
                       'usage': {'prompt_tokens': 20, 'completion_tokens': 10}}]:
            yield ('data: ' + json.dumps(chunk) + '\n\n').encode('utf-8')
        yield b'data: [DONE]\n\n'


class StreamingTutorTransport(TutorTransport):
    """先发出工具前言，再挂起最终正文，用真实增量验证临时投影边界。"""

    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def post_stream(self, url, *, headers, body, timeout):
        request = json.loads(body)
        self.requests.append(request)
        reading = not any(message.get('role') == 'tool' for message in request['messages'])
        deltas = [{'reasoning': 'PRIVATE_THINKING'}, {'content': 'TOOL_PREFACE' if reading else '先看第一段 / first part'}]
        if reading:
            deltas.append(self.response(request))
        for delta in deltas:
            yield ('data: ' + json.dumps({'model': 'streaming-fixture', 'choices': [{'delta': delta, 'finish_reason': None}]}) + '\n\n').encode('utf-8')
        if not reading:
            self.entered.set()
            await self.release.wait()
            yield ('data: ' + json.dumps({'choices': [{'delta': {'content': '，再看第二段 / second part'}, 'finish_reason': None}]}) + '\n\n').encode('utf-8')
        yield ('data: ' + json.dumps({'choices': [{'delta': {}, 'finish_reason': 'tool_calls' if reading else 'stop'}]}) + '\n\n').encode('utf-8')
        yield b'data: [DONE]\n\n'


class ConciseTutorTransport(TutorTransport):
    """材料完整送入真实循环，简短回复避免把测试证据重复写进历史正文。"""

    @staticmethod
    def response(request, repeat=False):
        return {'content': 'One small observation / 一条观察。What would you check next?'}


class AcademyTutorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='academy-tutor-')
        self.root = Path(self.temporary.name)
        write_engine_fixture(self.root / 'library')
        self.service = AcademyService(self.root / 'home', self.root / 'library')
        self.project = self.service.create_project('create', 'harness-foundations')['result']['project']
        self.pid = self.project['id']
        self.futures = []
        self.config = LlmCallConfig(model='configured-model')

    async def asyncTearDown(self):
        for future in self.futures:
            if not future.done():
                future.cancel()
        await asyncio.gather(*self.futures, return_exceptions=True)
        self.temporary.cleanup()

    def schedule(self, identity):
        future = asyncio.get_running_loop().create_future()
        self.futures.append(future)
        return future

    def start(self, request='ask', *, allow=False, prompt='Help me understand this code / 用户问题 {code}', **kwargs):
        return self.service.tutor.start(self.pid, request, 'agent-loop', self.service.project(self.pid)['fingerprint'],
            prompt, allow, model=self.config.model, schedule=self.schedule, **kwargs)

    async def test_followups_retain_selected_old_model_evidence_as_flat_history_context(self):
        original = self.model_evidence('selected', response='ORIGINAL_SELECTED_RESPONSE')
        self.service.store.put('model-run', original)
        self.service.store.put('model-run', self.model_evidence('newer', response='NEWER_RESPONSE'))
        await self.evaluate(self.start_model_question('selected-question', model_run_id='selected'), ConciseTutorTransport())
        self.service.store.put('model-run', {**original, 'response': 'LATER_STORE_CHANGE'})
        for index in range(7):
            receipt = self.start_model_question('follow-' + str(index), prompt='Why? / 为什么？')
            turn, transport = await self.evaluate(receipt, ConciseTutorTransport())
            context = next(json.loads(item['content']) for item in transport.requests[0]['messages'] if item.get('role') == 'user')
            self.assertLessEqual(len(context['history']), 4)
            material = context['history'][-1]['context']
            self.assertEqual(material['model_run'], original)
            self.assertEqual(material['turn_id'], context['history'][-1]['id'])
            self.assertEqual(context['model_run']['id'], 'newer')
            self.assertEqual(sum('context' in entry for entry in context['history']), 1)
            self.assertNotIn('history', material)
            self.assertNotIn('allow_execution', material)
            self.assertFalse(turn['allow_execution'])
            self.assertEqual(turn['actions'], [])
            self.assertEqual(len(transport.requests), 1)
        self.assertEqual(self.service.runs(self.pid), [])

    async def test_identical_model_material_is_not_duplicated_across_followup_history(self):
        evidence = self.model_evidence(response='UNIQUE_MODEL_PAYLOAD' + 'x' * 4000)
        self.service.store.put('model-run', evidence)
        await self.evaluate(self.start_model_question('initial'), ConciseTutorTransport())
        for index in range(5):
            receipt = self.start_model_question('why-' + str(index), prompt='Why?')
            turn, transport = await self.evaluate(receipt, ConciseTutorTransport())
            context = next(json.loads(item['content']) for item in transport.requests[0]['messages'] if item.get('role') == 'user')
            self.assertNotIn('model_run', context)
            self.assertEqual(context['history'][-1]['context']['model_run'], evidence)
            self.assertEqual(json.dumps(context).count('UNIQUE_MODEL_PAYLOAD'), 1)
            self.assertEqual(turn['status'], 'completed')

    async def test_followup_self_check_and_run_comparison_keep_their_original_evidence(self):
        await self.evaluate(self.start_check_question('quiz'), ConciseTutorTransport())
        turn, transport = await self.evaluate(self.start('why-quiz', prompt='Why?'), ConciseTutorTransport())
        context = next(json.loads(item['content']) for item in transport.requests[0]['messages'] if item.get('role') == 'user')
        self.assertEqual(context['history'][-1]['context']['knowledge_check']['selected']['id'], 'guess')
        self.assertNotIn('knowledge_check', context)
        baseline, current = self.evidence('baseline'), self.evidence('current')
        await self.evaluate(self.start('compare', evidence_run_id='current', comparison_run_id='baseline'), ConciseTutorTransport())
        turn, transport = await self.evaluate(self.start('why-comparison', prompt='Why?'), ConciseTutorTransport())
        context = next(json.loads(item['content']) for item in transport.requests[0]['messages'] if item.get('role') == 'user')
        material = context['history'][-1]['context']
        self.assertEqual([run['id'] for run in material['runs']], [current['id'], baseline['id']])
        self.assertEqual(material['runs'][0]['result'], current['result'])
        self.assertEqual(context['runs'], [])
        names = {tool['function']['name'] for tool in transport.requests[0].get('tools', [])}
        self.assertFalse(names & {'run_example', 'check_lesson', 'propose_edit'})
        self.assertEqual(turn['actions'], [])

    async def test_invalid_history_material_is_rejected_before_provider_or_file_writes(self):
        await self.evaluate(self.start_check_question('quiz'), ConciseTutorTransport())
        receipt = self.start('why')
        identity = receipt['result']['tutor_turn']['id']
        value = self.service.store.get(self.service.tutor._kind(self.pid), identity)
        valid = copy.deepcopy(value)
        for patch in ({'project_id': 'foreign'}, {'lesson_id': 'foreign'}, {'allow_execution': True}, {'turn_id': 'other'}):
            value = copy.deepcopy(valid)
            value['snapshot']['history'][-1]['context'].update(patch)
            self.service.store.put(self.service.tutor._kind(self.pid), value)
            transport = ConciseTutorTransport()
            with self.assertRaisesRegex(AcademyError, 'store-invalid'):
                await self.evaluate(receipt, transport)
            self.assertEqual(transport.requests, [])

    async def test_history_material_over_total_budget_is_explicitly_rejected_without_replay(self):
        self.service.store.put('model-run', self.model_evidence('selected', response='a' * 4000))
        await self.evaluate(self.start_model_question('selected', model_run_id='selected'), ConciseTutorTransport())
        (Path(self.project['root']) / 'tools.py').write_text('#' + 'x' * 33000, encoding='utf-8')
        self.service.store.put('model-run', self.model_evidence('newer', response='b' * 62000))
        receipt = self.start_model_question('too-large', prompt='Why?')
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(receipt['result']['error_code'], 'tutor-too-large')

    async def evaluate(self, receipt, transport=None):
        transport = transport or TutorTransport()
        adapter = OpenRouterAdapter('fixture-only', model=self.config.model, transport=transport, max_retries=0)
        await self.service.tutor.evaluate(receipt['result']['tutor_turn']['id'], adapter, self.config)
        return self.service.tutor.turns(self.pid)[0], transport

    def model_evidence(self, identity='model-evidence', **changes):
        """模型证据保留原文和身份，时间戳不代替持久记录顺序。"""
        return {'id': identity, 'request_id': identity, 'project_id': self.pid, 'lesson_id': 'agent-loop',
                'input_fingerprint': 'a' * 64, 'system': 'SOURCE_SYSTEM', 'prompt': 'SOURCE_PROMPT / 原提示',
                'response': 'SOURCE_RESPONSE / 原回答', 'model': 'recorded-model', 'language': 'zh-CN',
                'status': 'completed', 'created_at': 1, 'completed_at': 2, **changes}

    def start_model_question(self, request='ask', **kwargs):
        """只替换课程模式，仍经过真实带练受理、快照和原生循环。"""
        course = self.service.course('harness-foundations')
        course['lessons'][0]['mode'] = 'model'
        with patch.object(self.service, 'course', return_value=course):
            return self.start(request, **kwargs)

    def start_check_question(self, request='ask', **kwargs):
        """题目来自当前课程，由带练受理冻结，不把页面文字当成完成证据。"""
        course = self.service.course('harness-foundations')
        course['lessons'][0]['knowledge_checks'] = [{'id': 'check-one', 'prompt': 'Which observation supports the claim? / 用什么观察核对？',
            'options': [{'id': 'guess', 'label': 'Guess without evidence / 没有依据就猜', 'feedback': 'Inspect one observed result / 先看一个实际结果'},
                        {'id': 'evidence', 'label': 'Use the recorded result', 'feedback': 'Check the observation'}],
            'answer_id': 'evidence', 'hints': []}]
        with patch.object(self.service, 'course', return_value=course):
            return self.start(request, **({'knowledge_check': {'check_id': 'check-one', 'option_id': 'guess'}} | kwargs))

    async def test_self_check_question_freezes_selected_choice_without_executing_or_grading(self):
        receipt = self.start_check_question()
        selection = {'check_id': 'check-one', 'option_id': 'guess'}
        self.assertEqual(receipt['result']['tutor_turn']['knowledge_check'], selection)
        turn, transport = await self.evaluate(receipt)
        context = next(json.loads(item['content']) for item in transport.requests[0]['messages'] if item.get('role') == 'user')
        self.assertEqual(context['knowledge_check']['id'], 'check-one')
        self.assertEqual(context['knowledge_check']['selected']['id'], 'guess')
        self.assertNotIn('answer_id', context['knowledge_check'])
        self.assertNotIn('options', context['knowledge_check'])
        self.assertIn(context['knowledge_check']['selected']['label'], turn['response'])
        self.assertEqual(turn['actions'], [])
        self.assertEqual(self.service.runs(self.pid), [])
        self.assertTrue(all(row['state'] != 'completed' for row in self.service.project(self.pid)['progress']))
        self.assertEqual(self.start_check_question()['result']['tutor_turn']['id'], turn['id'])
        with self.assertRaisesRegex(AcademyError, 'request-conflict'):
            self.start_check_question(knowledge_check={'check_id': 'check-one', 'option_id': 'evidence'})
        course = self.service.course('harness-foundations')
        course['lessons'][0]['mode'] = 'model'
        evidence = self.model_evidence()
        self.service.store.put('model-run', evidence)
        with patch.object(self.service, 'course', return_value=course):
            model_receipt = self.start_check_question('model-check')
        model_turn, model_transport = await self.evaluate(model_receipt)
        model_context = next(json.loads(item['content']) for item in model_transport.requests[0]['messages'] if item.get('role') == 'user')
        self.assertEqual(model_context['model_run']['id'], evidence['id'])
        self.assertEqual(model_context['knowledge_check']['selected']['id'], 'guess')
        self.assertEqual(model_turn['actions'], [])

    async def test_self_check_scope_rejects_unknown_choices_execution_and_mismatched_snapshot(self):
        for index, selection in enumerate(({'check_id': 'foreign', 'option_id': 'guess'}, {'check_id': 'check-one', 'option_id': 'missing'})):
            receipt = self.start_check_question('missing-' + str(index), knowledge_check=selection)
            self.assertEqual(receipt['result']['error_code'], 'invalid-request')
        for kwargs in ({'knowledge_check': {}}, {'knowledge_check': 'invalid'}, {'allow': True}, {'require_execution': True},
                       {'model_run_id': 'model'}, {'evidence_run_id': 'run'}, {'proposal_path': 'tools.py'},
                       {'source': {'path': 'tools.py', 'content': 'changed', 'expected_version': 'a' * 64}}):
            with self.assertRaisesRegex(AcademyError, 'invalid-request'):
                self.start_check_question(**kwargs)
        receipt = self.start_check_question('corrupted')
        identity = receipt['result']['tutor_turn']['id']
        value = self.service.store.get(self.service.tutor._kind(self.pid), identity)
        value['snapshot']['knowledge_check']['selected']['id'] = 'evidence'
        self.service.store.put(self.service.tutor._kind(self.pid), value)
        transport = TutorTransport()
        with self.assertRaisesRegex(AcademyError, 'store-invalid'):
            await self.evaluate(receipt, transport)
        self.assertEqual(transport.requests, [])

    async def test_selected_model_experiment_binds_frozen_record_and_receipt(self):
        selected = self.model_evidence('selected', prompt='SELECTED_PROMPT', response='SELECTED_RESPONSE')
        self.service.store.put('model-run', selected)
        self.service.store.put('model-run', self.model_evidence('latest'))
        receipt = self.start_model_question(model_run_id='selected')
        self.assertEqual(receipt['result']['tutor_turn']['model_run_id'], 'selected')
        self.service.store.put('model-run', {**selected, 'response': 'CHANGED_AFTER_ACCEPTANCE'})
        turn, transport = await self.evaluate(receipt)
        context = next(json.loads(item['content']) for item in transport.requests[0]['messages'] if item.get('role') == 'user')
        self.assertEqual(context['model_run'], selected)
        self.assertIn('SELECTED_RESPONSE', turn['response'])
        self.assertNotIn('CHANGED_AFTER_ACCEPTANCE', turn['response'])
        self.assertEqual(turn['actions'], [])
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(self.service.runs(self.pid), [])
        self.assertEqual(self.start_model_question(model_run_id='selected')['result']['tutor_turn']['id'], turn['id'])
        with self.assertRaisesRegex(AcademyError, 'request-conflict'):
            self.start_model_question(model_run_id='latest')
        corrupted = self.start_model_question('mismatched-snapshot', model_run_id='selected')
        identity = corrupted['result']['tutor_turn']['id']
        value = self.service.store.get(self.service.tutor._kind(self.pid), identity)
        value['snapshot']['model_run']['id'] = 'latest'
        self.service.store.put(self.service.tutor._kind(self.pid), value)
        transport = TutorTransport()
        with self.assertRaisesRegex(AcademyError, 'store-invalid'):
            await self.evaluate(corrupted, transport)
        self.assertEqual(transport.requests, [])

    async def test_model_comparison_freezes_both_inputs_and_preserves_the_pair_in_followups(self):
        earlier = self.model_evidence('earlier', prompt='EARLIER_PROMPT', response='EARLIER_RESPONSE', created_at=9999)
        primary = self.model_evidence('primary', prompt='PRIMARY_PROMPT', response='PRIMARY_RESPONSE', created_at=1)
        self.service.store.put('model-run', earlier)
        self.service.store.put('model-run', primary)
        receipt = self.start_model_question(model_run_id='primary', comparison_model_run_id='earlier')
        self.assertEqual(receipt['result']['tutor_turn']['comparison_model_run_id'], 'earlier')
        self.service.store.put('model-run', {**earlier, 'response': 'CHANGED_LATER'})
        turn, transport = await self.evaluate(receipt)
        context = next(json.loads(item['content']) for item in transport.requests[0]['messages'] if item.get('role') == 'user')
        self.assertEqual(context['model_run'], primary)
        self.assertEqual(context['comparison_model_run'], earlier)
        self.assertIn('EARLIER_RESPONSE', turn['response'])
        self.assertNotIn('CHANGED_LATER', turn['response'])
        self.assertEqual(turn['actions'], [])
        self.assertEqual(self.service.runs(self.pid), [])
        self.assertEqual(self.start_model_question(model_run_id='primary', comparison_model_run_id='earlier')['result']['tutor_turn']['id'], turn['id'])
        with self.assertRaisesRegex(AcademyError, 'request-conflict'):
            self.start_model_question(model_run_id='primary')
        followup, followup_transport = await self.evaluate(self.start_model_question('why-pair', prompt='Why?'))
        followup_context = next(json.loads(item['content']) for item in followup_transport.requests[0]['messages'] if item.get('role') == 'user')
        material = followup_context['history'][-1]['context']
        self.assertEqual(material['model_run'], primary)
        self.assertEqual(material['comparison_model_run'], earlier)
        self.assertIn('EARLIER_RESPONSE', followup['response'])
        self.assertEqual(len(followup_transport.requests), 1)

    async def test_model_comparison_rejects_wrong_order_foreign_incomplete_or_mixed_scope(self):
        earlier, primary = self.model_evidence('earlier'), self.model_evidence('primary')
        self.service.store.put('model-run', earlier)
        self.service.store.put('model-run', primary)
        reversed_pair = self.start_model_question('reverse', model_run_id='earlier', comparison_model_run_id='primary')
        self.assertEqual(reversed_pair['result']['error_code'], 'invalid-request')
        for index, changes in enumerate(({'project_id': 'foreign'}, {'lesson_id': 'foreign'}, {'status': 'failed', 'error_code': 'model-failed'})):
            self.service.store.put('model-run', {**earlier, **changes})
            receipt = self.start_model_question('bad-pair-' + str(index), model_run_id='primary', comparison_model_run_id='earlier')
            self.assertEqual(receipt['result']['error_code'], 'run-not-found')
        for kwargs in ({'model_run_id': None}, {'comparison_model_run_id': 'primary'}, {'comparison_model_run_id': ''},
                       {'comparison_model_run_id': 1}, {'comparison_model_run_id': 'x' * 129}, {'comparison_model_run_id': chr(0xd800)},
                       {'allow': True}, {'evidence_run_id': 'run'}, {'proposal_path': 'tools.py'}):
            with self.assertRaisesRegex(AcademyError, 'invalid-request'):
                self.start_model_question(**({'model_run_id': 'primary', 'comparison_model_run_id': 'earlier'} | kwargs))

    async def test_model_comparison_rejects_mismatched_frozen_pair_before_provider(self):
        for identity in ('earlier', 'primary'):
            self.service.store.put('model-run', self.model_evidence(identity))
        receipt = self.start_model_question(model_run_id='primary', comparison_model_run_id='earlier')
        identity = receipt['result']['tutor_turn']['id']
        value = self.service.store.get(self.service.tutor._kind(self.pid), identity)
        value['snapshot']['comparison_model_run']['id'] = 'wrong'
        self.service.store.put(self.service.tutor._kind(self.pid), value)
        transport = TutorTransport()
        with self.assertRaisesRegex(AcademyError, 'store-invalid'):
            await self.evaluate(receipt, transport)
        self.assertEqual(transport.requests, [])

    async def test_selected_model_evidence_rejects_foreign_incomplete_or_execution_scope(self):
        for index, evidence in enumerate((self.model_evidence(project_id='foreign'), self.model_evidence(lesson_id='other'),
                                         self.model_evidence(status='failed', error_code='model-failed'))):
            self.service.store.put('model-run', evidence)
            self.assertEqual(self.start_model_question('bad-' + str(index), model_run_id=evidence['id'])['result']['error_code'], 'run-not-found')
        self.assertEqual(self.start_model_question('missing', model_run_id='missing')['result']['error_code'], 'run-not-found')
        for kwargs in ({'model_run_id': ''}, {'model_run_id': 1}, {'model_run_id': 'x' * 129}, {'model_run_id': chr(0xd800)},
                       {'allow': True}, {'require_execution': True}, {'evidence_run_id': 'run'}, {'proposal_path': 'tools.py'},
                       {'source': {'path': 'tools.py', 'content': 'changed', 'expected_version': 'a' * 64}}):
            with self.assertRaisesRegex(AcademyError, 'invalid-request'):
                self.start_model_question(**({'model_run_id': 'selected'} | kwargs))
        self.assertEqual(self.start('wrong-mode', model_run_id='selected')['result']['error_code'], 'lesson-not-runnable')

    async def test_model_question_freezes_latest_completed_owned_experiment_without_replay(self):
        old = self.model_evidence('old', created_at=9999)
        selected = self.model_evidence('latest')
        for value in (old, selected, self.model_evidence('foreign', project_id='other'),
                      self.model_evidence('other-lesson', lesson_id='other'),
                      self.model_evidence('failed', status='failed', error_code='model-failed')):
            self.service.store.put('model-run', value)
        receipt = self.start_model_question()
        self.assertNotIn('snapshot', receipt['result']['tutor_turn'])
        self.service.store.put('model-run', {**selected, 'response': 'LATER_CHANGE'})
        self.service.store.put('model-run', self.model_evidence('after-acceptance'))
        turn, transport = await self.evaluate(receipt)
        self.assertEqual(turn['status'], 'completed')
        context = next(json.loads(item['content']) for item in transport.requests[0]['messages'] if item.get('role') == 'user')
        self.assertEqual(context['model_run'], selected)
        self.assertNotEqual(context['model_run']['input_fingerprint'], context['input_fingerprint'])
        self.assertIn(selected['response'], turn['response'])
        self.assertNotIn('LATER_CHANGE', turn['response'])
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(turn['actions'], [])
        self.assertEqual(self.service.runs(self.pid), [])
        self.assertEqual(self.start_model_question()['result']['tutor_turn']['id'], turn['id'])

    async def test_model_question_without_experiment_and_invalid_frozen_evidence(self):
        receipt = self.start_model_question('empty')
        turn, transport = await self.evaluate(receipt)
        context = next(json.loads(item['content']) for item in transport.requests[0]['messages'] if item.get('role') == 'user')
        self.assertNotIn('model_run', context)
        self.assertEqual(turn['status'], 'completed')
        receipt = self.start_model_question('invalid')
        identity = receipt['result']['tutor_turn']['id']
        for evidence in (None, self.model_evidence(project_id='foreign'), self.model_evidence(lesson_id='other'),
                         self.model_evidence(status='failed', error_code='model-failed')):
            value = self.service.store.get(self.service.tutor._kind(self.pid), identity)
            value['snapshot']['model_run'] = evidence
            self.service.store.put(self.service.tutor._kind(self.pid), value)
            transport = TutorTransport()
            with self.assertRaisesRegex(AcademyError, 'store-invalid'):
                await self.evaluate(receipt, transport)
            self.assertEqual(transport.requests, [])

    async def test_stop_stream_uses_owned_task_and_original_receipt_without_replay(self):
        transport = StreamingTutorTransport()
        adapter = OpenRouterAdapter('fixture-only', model=self.config.model, transport=transport, max_retries=0)
        def schedule(identity):
            task = asyncio.create_task(self.service.tutor.evaluate(identity, adapter, self.config))
            self.futures.append(task)
            return task
        with patch.object(self, 'schedule', side_effect=schedule):
            receipt = self.start()
        identity = receipt['result']['tutor_turn']['id']
        await asyncio.wait_for(transport.entered.wait(), 3)
        other = self.service.create_project('other-project', 'harness-foundations')['result']['project']
        denied = self.service.tutor.stop(other['id'], identity, 'foreign-stop')
        self.assertEqual(denied['status'], 'failed')
        self.assertFalse(self.futures[0].done())
        stopped = self.service.tutor.stop(self.pid, identity, 'stop-one')
        self.assertEqual(stopped['status'], 'ready')
        self.assertTrue(stopped['result']['tutor_turn']['stopping'])
        self.assertEqual(stopped['result']['tutor_turn']['request_id'], 'ask')
        self.assertEqual(self.service.tutor.stop(self.pid, identity, 'stop-one'), stopped)
        result = (await asyncio.gather(self.futures[0], return_exceptions=True))[0]
        self.assertIsInstance(result, asyncio.CancelledError)
        observed = self.service.tutor.turns(self.pid)[0]
        self.assertEqual(observed['status'], 'unknown')
        self.assertNotIn('stopping', observed)
        self.assertNotIn('partial_response', observed)
        self.assertEqual(self.service.tutor._scheduled, {})
        self.assertEqual(self.service.tutor._stopping, set())
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(self.service.runs(self.pid), [])
        self.assertEqual(self.service.tutor.stop(self.pid, identity, 'stop-terminal')['result']['tutor_turn']['status'], 'unknown')

    @unittest.skipIf(AcademyRuntime.backend() == 'unavailable', '系统隔离器未安装')
    async def test_stop_waits_for_accepted_tool_and_repeated_stop_never_replays_it(self):
        entered, release = threading.Event(), threading.Event()
        execute = self.service.execute
        calls = []
        def blocked(*args, **kwargs):
            calls.append(args)
            entered.set()
            if not release.wait(5):
                raise RuntimeError('回归屏障未释放')
            return execute(*args, **kwargs)
        adapter = OpenRouterAdapter('fixture-only', model=self.config.model, transport=TutorTransport(), max_retries=0)
        def schedule(identity):
            task = asyncio.create_task(self.service.tutor.evaluate(identity, adapter, self.config))
            self.futures.append(task)
            return task
        with patch.object(self, 'schedule', side_effect=schedule), patch.object(self.service, 'execute', side_effect=blocked):
            receipt = self.start(allow=True)
            identity = receipt['result']['tutor_turn']['id']
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                self.assertEqual(self.service.tutor.stop(self.pid, identity, 'stop-tool')['status'], 'ready')
                await asyncio.sleep(0)
                self.assertFalse(self.futures[0].done())
                self.assertTrue(self.service.tutor.turns(self.pid)[0]['stopping'])
                self.assertEqual(self.service.tutor.stop(self.pid, identity, 'stop-tool-again')['status'], 'ready')
                self.assertEqual(self.start('still-busy')['result']['error_code'], 'tutor-busy')
            finally:
                release.set()
                await asyncio.gather(self.futures[0], return_exceptions=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.service.runs(self.pid)), 1)
        observed = self.service.tutor.turns(self.pid)[0]
        self.assertEqual(observed['status'], 'unknown')
        self.assertEqual(observed['actions'][-1]['status'], 'completed')

    async def test_streaming_reply_is_request_scoped_ephemeral_and_replaced_by_final_answer(self):
        receipt = self.start()
        identity = receipt['result']['tutor_turn']['id']
        transport = StreamingTutorTransport()
        adapter = OpenRouterAdapter('fixture-only', model=self.config.model, transport=transport, max_retries=0)
        task = asyncio.create_task(self.service.tutor.evaluate(identity, adapter, self.config))
        try:
            await asyncio.wait_for(transport.entered.wait(), 3)
            observed = self.service.tutor.turns(self.pid)[0]
            self.assertEqual(observed['status'], 'running')
            self.assertEqual(observed['response'], '')
            self.assertEqual(observed['partial_response'], '先看第一段 / first part')
            self.assertNotIn('partial_response', self.service.store.get(self.service.tutor._kind(self.pid), identity))
            reopened = AcademyService(self.root / 'home', self.root / 'library')
            interrupted = reopened.tutor.turns(self.pid)[0]
            self.assertEqual(interrupted['status'], 'unknown')
            self.assertNotIn('partial_response', interrupted)
        finally:
            transport.release.set()
            await task
        observed = self.service.tutor.turns(self.pid)[0]
        self.assertEqual(observed['status'], 'completed')
        self.assertEqual(observed['response'], '先看第一段 / first part，再看第二段 / second part')
        self.assertNotIn('partial_response', observed)
        self.assertNotIn(identity, self.service.tutor._drafts)
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(self.service.runs(self.pid), [])

    async def test_cancelled_stream_discards_ephemeral_text_without_replay(self):
        receipt = self.start()
        identity = receipt['result']['tutor_turn']['id']
        transport = StreamingTutorTransport()
        adapter = OpenRouterAdapter('fixture-only', model=self.config.model, transport=transport, max_retries=0)
        task = asyncio.create_task(self.service.tutor.evaluate(identity, adapter, self.config))
        try:
            await asyncio.wait_for(transport.entered.wait(), 3)
            self.assertIn('partial_response', self.service.tutor.turns(self.pid)[0])
            task.cancel()
        finally:
            transport.release.set()
            result = (await asyncio.gather(task, return_exceptions=True))[0]
        self.assertIsInstance(result, asyncio.CancelledError)
        observed = self.service.tutor.turns(self.pid)[0]
        self.assertEqual(observed['status'], 'unknown')
        self.assertNotIn('partial_response', observed)
        self.assertNotIn(identity, self.service.tutor._drafts)
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(self.service.runs(self.pid), [])

    @unittest.skipIf(AcademyRuntime.backend() == 'unavailable', '系统隔离器未安装')
    async def test_native_loop_saves_then_runs_and_repeated_tools_reuse_real_evidence(self):
        source = self.service.source(self.pid, 'tools.py')
        edited = {'path': source['path'], 'content': 'def add(a, b):\n    return 7\n', 'expected_version': source['version']}
        original_fingerprint = self.project['fingerprint']
        receipt = self.start(allow=True, require_execution=True, source=edited)
        self.assertEqual(receipt['result']['source']['content'], edited['content'])
        turn, transport = await self.evaluate(receipt, TutorTransport(repeat=True))
        self.assertEqual(turn['status'], 'completed', turn)
        self.assertEqual(turn['model'], self.config.model)
        self.assertNotEqual(turn['input_fingerprint'], original_fingerprint)
        runs = self.service.runs(self.pid)
        self.assertEqual(len(runs), 1, '重复模型工具调用不能重放课程执行')
        self.assertEqual(runs[0]['input_fingerprint'], turn['input_fingerprint'])
        self.assertIn('"answer": 7', runs[0]['result']['stdout'])
        self.assertIn('answer', turn['response'])
        self.assertEqual([action['tool'] for action in turn['actions']], ['inspect_file', 'run_example'])
        self.assertEqual(turn['actions'][1]['run_id'], runs[0]['id'])
        self.assertEqual(len(transport.requests), 4)
        self.assertNotIn('snapshot', turn)
        self.assertTrue(all(row['state'] == 'in_progress' for row in self.service.project(self.pid)['progress']))
        reopened = AcademyService(self.root / 'home', self.root / 'library')
        self.assertEqual(reopened.tutor.turns(self.pid)[0], turn)
        self.assertEqual(reopened.receipt('ask'), receipt)
        with self.assertRaisesRegex(AcademyError, 'tutor-interrupted'):
            await self.service.tutor.evaluate(turn['id'], object(), self.config)
        self.assertEqual(len(self.service.runs(self.pid)), 1)

    async def test_hint_has_no_execution_tools_and_freezes_actual_language_per_interaction(self):
        for index, language in enumerate(('en', 'zh-CN', 'en')):
            with current_language.set(language):
                receipt = self.start('hint-' + str(index))
                self.assertEqual(self.start('hint-' + str(index)), receipt)
                with patch.object(AcademyRuntime, 'run', side_effect=AssertionError('提示不能执行代码')):
                    turn, transport = await self.evaluate(receipt)
                self.assertEqual(turn['status'], 'completed', turn)
                self.assertEqual(turn['language'], language)
                schemas = transport.requests[0]['tools']
                self.assertEqual([tool['function']['name'] for tool in schemas], ['inspect_file'])
                self.assertEqual(schemas[0]['function']['description'], tr('academy.tutor.tool.inspect_file'))
                self.assertIn(tr('academy.tutor.instructions'), str(transport.requests[0]['messages']))
        self.assertEqual(self.service.runs(self.pid), [])
        self.assertEqual(len(self.futures), 3)

    async def test_stale_input_busy_and_restarted_intent_do_not_call_model(self):
        receipt = self.start()
        self.assertEqual(self.start('busy')['result']['error_code'], 'tutor-busy')
        reopened = AcademyService(self.root / 'home', self.root / 'library')
        self.assertEqual(reopened.tutor.turns(self.pid)[0]['status'], 'unknown')
        with self.assertRaisesRegex(AcademyError, 'tutor-interrupted'):
            await reopened.tutor.evaluate(receipt['result']['tutor_turn']['id'], object(), self.config)
        source = self.service.source(self.pid, 'tools.py')
        self.service.write_file(self.pid, 'edit', source['path'], source['content'] + '\n# 新输入\n', source['version'], self.project['fingerprint'])
        failed = reopened.tutor.start(self.pid, 'stale', 'agent-loop', self.project['fingerprint'], 'question', True)
        self.assertEqual(failed['result']['error_code'], 'stale-project')
        self.assertEqual(self.service.runs(self.pid), [])

    async def test_code_changed_during_model_work_is_rejected_before_execution(self):
        receipt = self.start(allow=True)
        source = self.service.source(self.pid, 'tools.py')
        self.service.write_file(self.pid, 'new-code', source['path'], 'def add(a,b):\n    return 9\n', source['version'], self.project['fingerprint'])
        with patch.object(AcademyRuntime, 'run', side_effect=AssertionError('不能执行新版本代码')):
            turn, _ = await self.evaluate(receipt)
        self.assertEqual(self.service.runs(self.pid), [])
        self.assertEqual(turn['actions'][1]['status'], 'failed')
        self.assertEqual(turn['actions'][1]['output'], 'stale-project')

    async def test_schedule_rejection_keeps_unknown_intent_and_saved_source(self):
        source = self.service.source(self.pid, 'tools.py')
        def reject(identity):
            raise RuntimeError('scheduler closed')
        with self.assertRaisesRegex(RuntimeError, 'scheduler closed'):
            self.service.tutor.start(self.pid, 'rejected', 'agent-loop', self.project['fingerprint'], 'question', False,
                source={'path': source['path'], 'content': source['content'] + '\n# 已保存\n', 'expected_version': source['version']}, schedule=reject)
        self.assertEqual(self.service.receipt('rejected')['status'], 'unknown')
        self.assertEqual(self.service.tutor.turns(self.pid)[0]['status'], 'unknown')
        self.assertIn('已保存', self.service.source(self.pid, 'tools.py')['content'])

    @unittest.skipIf(AcademyRuntime.backend() == 'unavailable', '系统隔离器未安装')
    async def test_repeated_cancellation_waits_for_accepted_execution_and_never_replays(self):
        receipt = self.start(allow=True)
        entered, release = threading.Event(), threading.Event()
        execute = self.service.execute
        calls = []
        def blocked(*args, **kwargs):
            calls.append(args)
            entered.set()
            if not release.wait(5):
                raise RuntimeError('回归屏障未释放')
            return execute(*args, **kwargs)
        transport = TutorTransport()
        adapter = OpenRouterAdapter('fixture-only', model=self.config.model, transport=transport, max_retries=0)
        identity = receipt['result']['tutor_turn']['id']
        with patch.object(self.service, 'execute', side_effect=blocked):
            task = asyncio.create_task(self.service.tutor.evaluate(identity, adapter, self.config))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                observed = self.service.tutor.turns(self.pid)[0]
                self.assertEqual(observed['status'], 'running')
                self.assertEqual(observed['actions'][-1]['tool'], 'run_example')
                self.assertEqual(observed['actions'][-1]['status'], 'running')
                self.futures[0].cancel()
                await asyncio.sleep(0)
                self.assertEqual(self.start('still-owned')['result']['error_code'], 'tutor-busy')
                for _ in range(3):
                    task.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(task.done())
            finally:
                release.set()
                result = (await asyncio.gather(task, return_exceptions=True))[0]
        self.assertIsInstance(result, asyncio.CancelledError)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.service.runs(self.pid)), 1)
        turn = self.service.tutor.turns(self.pid)[0]
        self.assertEqual(turn['status'], 'unknown')
        self.assertEqual(turn['actions'][1]['status'], 'completed')
        with self.assertRaisesRegex(AcademyError, 'tutor-interrupted'):
            await self.service.tutor.evaluate(identity, adapter, self.config)

    async def test_unknown_execution_disables_all_further_execution_and_survives_reopen(self):
        receipt = self.start(allow=True)
        with patch.object(self.service, 'execute', side_effect=RuntimeError('execution outcome unknown')) as execute:
            turn, _ = await self.evaluate(receipt, TutorTransport(repeat=True))
        self.assertEqual(turn['status'], 'unknown')
        self.assertEqual(turn['actions'][1]['status'], 'unknown')
        self.assertEqual(execute.call_count, 1)
        reopened = AcademyService(self.root / 'home', self.root / 'library')
        self.assertEqual(reopened.tutor.turns(self.pid)[0], turn)

    async def test_history_is_project_scoped_bounded_and_rejects_oversized_originals(self):
        turn, _ = await self.evaluate(self.start())
        kind = self.service.tutor._kind(self.pid)
        original = self.service.store.get(kind, turn['id'])
        for index in range(25):
            value = copy.deepcopy(original)
            value['id'] = 'tutor-record-' + str(index)
            self.service.store.put(kind, value)
        history = self.service.tutor.turns(self.pid)
        self.assertEqual(len(history), 20)
        self.assertEqual(history[0]['id'], 'tutor-record-24')
        other = self.service.create_project('other', 'harness-foundations')['result']['project']
        self.assertEqual(self.service.tutor.turns(other['id']), [])
        damaged = json.dumps({**original, 'id': history[0]['id'], 'response': 'x' * (1024 * 1024)})
        with closing(sqlite3.connect(self.service.store.path)) as db, db:
            db.execute('UPDATE records SET body=? WHERE kind=? AND id=?', (damaged, kind, history[0]['id']))
        with self.assertRaisesRegex(AcademyError, 'store-invalid'):
            self.service.tutor.turns(self.pid)
        with closing(sqlite3.connect(self.service.store.path)) as db:
            self.assertEqual(db.execute('SELECT body FROM records WHERE kind=? AND id=?', (kind, history[0]['id'])).fetchone()[0], damaged)
        for invalid in (True, -1, 0, '20'):
            with self.assertRaisesRegex(AcademyError, 'invalid-request'):
                self.service.store.list(kind, limit=invalid)

    async def test_local_route_rejects_foreign_access_and_invalid_execution_flags(self):
        router = build_router(SimpleNamespace(harness=SimpleNamespace(config=SimpleNamespace(home=self.root / 'route-home'))))
        create, _ = router.resolve('POST', '/api/v1/academy/projects')
        created = json.loads(create(Request('POST', '/api/v1/academy/projects', {}, {}, json.dumps({
            'request_id': 'route-create', 'course_id': 'harness-foundations'}).encode(), client_ip='127.0.0.1')).body)['result']['project']
        path = '/api/v1/academy/projects/' + created['id'] + '/tutor-turns'
        handler, params = router.resolve('POST', path)
        body = {'request_id': 'route-ask', 'lesson_id': created['active_lesson_id'], 'expected_fingerprint': created['fingerprint'],
                'prompt': 'Explain the lesson', 'allow_execution': False, 'require_execution': False}
        for host, subject in [('203.0.113.2', None), ('127.0.0.1', 'guest')]:
            with self.assertRaises(ApiError) as denied:
                handler(Request('POST', path, {}, {}, json.dumps(body).encode(), client_ip=host, subject=subject), **params)
            self.assertEqual(denied.exception.status, 403)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), self.assertRaises(ApiError) as invalid:
                handler(Request('POST', path, {}, {}, json.dumps({**body, 'allow_execution': 'false'}).encode(), client_ip='127.0.0.1'), **params)
            self.assertEqual(invalid.exception.code, 'academy/invalid-request')
        receipt = json.loads(handler(Request('POST', path, {}, {}, json.dumps(body).encode(), client_ip='127.0.0.1'), **params).body)
        self.assertEqual(receipt['result']['tutor_turn']['error_code'], 'tutor-unavailable')

    async def test_required_run_cannot_complete_from_a_model_claim_without_execution(self):
        class ClaimsOnly(TutorTransport):
            @staticmethod
            def response(request, repeat=False):
                return {'content': 'I ran the code and it passed. 未经核实的模型声明。'}
        receipt = self.start(allow=True, require_execution=True)
        with patch.object(AcademyRuntime, 'run', side_effect=AssertionError('模型没有调用工具')):
            turn, transport = await self.evaluate(receipt, ClaimsOnly())
        self.assertEqual(turn['status'], 'failed')
        self.assertEqual(turn['error_code'], 'tutor-execution-required')
        self.assertEqual(turn['actions'], [])
        self.assertEqual(self.service.runs(self.pid), [])
        self.assertEqual(len(transport.requests), 4)

    async def test_corrupt_duplicate_fields_do_not_replace_tutor_identity_or_status(self):
        turn, _ = await self.evaluate(self.start())
        kind = self.service.tutor._kind(self.pid)
        raw = json.dumps(self.service.store.get(kind, turn['id']))
        damaged = raw.replace('"status": "completed"', '"status": "failed", "status": "completed"', 1)
        self.assertNotEqual(raw, damaged)
        with closing(sqlite3.connect(self.service.store.path)) as db, db:
            db.execute('UPDATE records SET body=? WHERE kind=? AND id=?', (damaged, kind, turn['id']))
        with self.assertRaisesRegex(AcademyError, 'store-invalid'):
            self.service.tutor.turns(self.pid)
        with closing(sqlite3.connect(self.service.store.path)) as db:
            self.assertEqual(db.execute('SELECT body FROM records WHERE kind=? AND id=?', (kind, turn['id'])).fetchone()[0], damaged)

    async def test_corrupt_recent_run_is_not_silently_used_as_tutoring_evidence(self):
        damaged = {'id': 'invalid-run', 'project_id': self.pid}
        self.service.store.put('run', damaged)
        receipt = self.start()
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(receipt['result']['error_code'], 'store-invalid')
        self.assertEqual(self.futures, [])
        self.assertEqual(self.service.store.get('run', 'invalid-run'), damaged)

    async def test_already_closed_scheduler_returns_unknown_without_inventing_end_time(self):
        def closed(identity):
            future = Future()
            future.cancel()
            return future
        receipt = self.service.tutor.start(self.pid, 'closed-scheduler', 'agent-loop', self.project['fingerprint'], 'question', False, schedule=closed)
        self.assertEqual(receipt['status'], 'ready')
        self.assertEqual(receipt['result']['tutor_turn']['status'], 'unknown')
        self.assertNotIn('completed_at', receipt['result']['tutor_turn'])
        self.assertEqual(self.service.tutor.turns(self.pid)[0]['status'], 'unknown')

    async def test_cancelled_claim_wait_releases_only_after_the_claim_thread_finishes(self):
        receipt = self.start()
        identity = receipt['result']['tutor_turn']['id']
        entered, release = threading.Event(), threading.Event()
        claim = self.service.tutor._claim
        def blocked(value):
            result = claim(value)
            entered.set()
            if not release.wait(5):
                raise RuntimeError('认领回归屏障未释放')
            return result
        with patch.object(self.service.tutor, '_claim', side_effect=blocked):
            task = asyncio.create_task(self.service.tutor.evaluate(identity, object(), self.config))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                self.assertEqual(self.start('claim-busy')['result']['error_code'], 'tutor-busy')
            finally:
                release.set()
                result = (await asyncio.gather(task, return_exceptions=True))[0]
        self.assertIsInstance(result, asyncio.CancelledError)
        self.assertEqual(self.service.tutor.turns(self.pid)[0]['status'], 'unknown')
        self.assertEqual(self.start('after-claim')['status'], 'ready')

    def evidence(self, identity='selected-run', **changes):
        """构造已持久化的失败证据，用于检验选择和隔离而非模拟执行成功。"""
        run = {'id': identity, 'project_id': self.pid, 'lesson_id': 'agent-loop', 'request_id': identity,
               'input_fingerprint': 'a' * 64, 'input_changed': False, 'kind': 'run', 'created_at': 1,
               'result': {'status': 'failed', 'exit_code': 1, 'stdout': '', 'stderr': 'NameError: selected_original_error\n',
                          'truncated': False, 'duration_ms': 1, 'sandbox': 'fixture', 'memory_limited': False, 'peak_memory_kib': 0}, 'events': []}
        run.update(changes)
        self.service.store.put('run', run)
        return run

    async def test_selected_result_freezes_exact_old_evidence_without_new_execution(self):
        evidence = self.evidence()
        for index in range(45):
            self.evidence('other-run-' + str(index), project_id='other-project')
        receipt = self.start(evidence_run_id=evidence['id'])
        self.assertEqual(receipt['result']['tutor_turn']['evidence_run_id'], evidence['id'])
        # 接受后原记录变化不能替换已冻结的选中结果。
        self.evidence(result={**evidence['result'], 'stderr': 'later record'})
        with patch.object(AcademyRuntime, 'run', side_effect=AssertionError('分析结果不能重跑')):
            turn, transport = await self.evaluate(receipt)
        context = json.loads(next(message['content'] for message in transport.requests[0]['messages'] if message['role'] == 'user'))
        self.assertEqual(context['evidence_run_id'], evidence['id'])
        self.assertEqual(context['runs'][0]['result'], evidence['result'])
        self.assertEqual(len(context['runs']), 1)
        self.assertNotEqual(context['runs'][0]['input_fingerprint'], context['input_fingerprint'])
        self.assertNotIn('run_example', str(transport.requests[0].get('tools', [])))
        self.assertEqual(turn['actions'], [])
        self.assertIn('selected_original_error', turn['response'])
        self.assertEqual(turn['status'], 'completed')
        self.assertEqual(self.start(evidence_run_id=evidence['id'])['result']['tutor_turn']['id'], turn['id'])
        with self.assertRaisesRegex(AcademyError, 'request-conflict'):
            self.start(evidence_run_id='other-run-0')
        self.assertEqual(len(self.service.runs(self.pid)), 1)

    async def test_selected_result_rejects_foreign_missing_and_corrupt_evidence(self):
        for index, changes in enumerate(({'project_id': 'another'}, {'lesson_id': 'another'})):
            self.evidence(**changes)
            receipt = self.start('foreign-' + str(index), evidence_run_id='selected-run')
            self.assertEqual(receipt['result']['error_code'], 'run-not-found')
        self.assertEqual(self.start('missing', evidence_run_id='missing')['result']['error_code'], 'run-not-found')
        damaged = self.evidence(result={'status': 'failed'})
        self.assertEqual(self.start('corrupt', evidence_run_id='selected-run')['result']['error_code'], 'store-invalid')
        self.assertEqual(self.service.store.get('run', 'selected-run'), damaged)
        self.assertEqual(self.futures, [])

    async def test_selected_result_cannot_gain_execution_or_mutate_source(self):
        source = self.service.source(self.pid, 'tools.py')
        for kwargs in ({'allow': True}, {'require_execution': True},
                       {'source': {'path': source['path'], 'content': 'changed', 'expected_version': source['version']}},
                       {'evidence_run_id': ''}, {'evidence_run_id': 'x' * 129}, {'evidence_run_id': 1}, {'evidence_run_id': chr(0xd800)}):
            with self.assertRaisesRegex(AcademyError, 'invalid-request'):
                self.start(**({'evidence_run_id': 'selected-run'} | kwargs))
        self.assertEqual(self.service.source(self.pid, 'tools.py'), source)
        self.assertEqual(self.futures, [])

    async def test_native_proposal_prepares_reviewable_draft_without_mutation_or_execution(self):
        source = self.service.source(self.pid, 'tools.py')
        edited = {'path': 'tools.py', 'content': 'value = 1\nprint(value)\n', 'expected_version': source['version']}
        receipt = self.start(source=edited, proposal_path='tools.py')
        saved = self.service.source(self.pid, 'tools.py')
        self.assertEqual(saved['content'], edited['content'])
        with patch.object(AcademyRuntime, 'run', side_effect=AssertionError('提案不能执行代码')):
            turn, transport = await self.evaluate(receipt)
        self.assertEqual(turn['status'], 'completed', turn)
        proposal = next(action['proposal'] for action in turn['actions'] if action.get('proposal'))
        self.assertEqual(proposal['before'], edited['content'])
        self.assertEqual(proposal['after'], 'value = 2\nprint(value)\n')
        self.assertEqual(proposal['expected_version'], saved['version'])
        self.assertIn('-value = 1\n+value = 2', proposal['diff'])
        self.assertEqual(self.service.source(self.pid, 'tools.py'), saved)
        self.assertEqual(self.service.runs(self.pid), [])
        self.assertEqual([action['tool'] for action in turn['actions']], ['inspect_file', 'propose_edit'])
        self.assertNotIn('run_example', str(transport.requests[0].get('tools', [])))
        for index, language in enumerate(('en', 'zh-CN', 'en')):
            with current_language.set(language):
                localized, localized_transport = await self.evaluate(self.start('proposal-locale-' + str(index), proposal_path='tools.py'))
                self.assertEqual(localized['language'], language)
                schema = next(tool['function'] for tool in localized_transport.requests[0]['tools'] if tool['function']['name'] == 'propose_edit')
                self.assertEqual(schema['description'], tr('academy.tutor.tool.propose_edit'))
                self.assertEqual(schema['parameters']['properties']['old_text']['description'], tr('academy.tutor.tool.old_text'))
        repeated = self.service.tutor.start(self.pid, 'ask', 'agent-loop', self.project['fingerprint'],
            'Help me understand this code / 用户问题 {code}', False, source=edited, proposal_path='tools.py', model=self.config.model, schedule=self.schedule)
        self.assertEqual(repeated['result']['tutor_turn']['id'], turn['id'])

    async def test_proposal_requires_opt_in_unique_small_text_and_honest_diff(self):
        receipt = self.start(proposal_path='tools.py')
        identity = receipt['result']['tutor_turn']['id']
        value = self.service.store.get(self.service.tutor._kind(self.pid), identity)
        original = self.service.source(self.pid, 'tools.py')
        args = {'path': 'tools.py', 'old_text': 'return a + b', 'new_text': 'return 7'}
        for changes in ({'path': 'main.py'}, {'old_text': ''}, {'old_text': 'missing'}, {'old_text': ' '}, {'new_text': chr(0xd800)}, {'new_text': 'x' * 4001}, {'new_text': '\n'.join(['x'] * 21)}):
            fresh = copy.deepcopy(value)
            with self.assertRaisesRegex(AcademyError, 'invalid-request'):
                self.service.tutor._invoke(fresh, 'propose_edit', args | changes)
        fresh = copy.deepcopy(value)
        output = self.service.tutor._invoke(fresh, 'propose_edit', args)
        self.assertEqual(self.service.tutor._invoke(fresh, 'propose_edit', args | {'new_text': 'return 99'}), output)
        self.assertEqual(len(fresh['actions']), 1)
        fresh['actions'][0]['proposal']['after'] = 'different from displayed diff'
        with self.assertRaisesRegex(AcademyError, 'store-invalid'):
            self.service.tutor._view(fresh)
        self.assertEqual(self.service.source(self.pid, 'tools.py'), original)
        plain = {**value, 'proposal_path': None, 'actions': []}
        with self.assertRaisesRegex(AcademyError, 'invalid-request'):
            self.service.tutor._invoke(plain, 'propose_edit', args)

    async def test_proposal_model_claim_without_tool_does_not_count_as_prepared(self):
        class ClaimTransport(TutorTransport):
            @staticmethod
            def response(request, repeat=False):
                return {'content': 'I prepared and applied a fix.'}
        turn, _ = await self.evaluate(self.start(proposal_path='tools.py'), ClaimTransport())
        self.assertEqual(turn['status'], 'failed')
        self.assertEqual(turn['error_code'], 'tutor-proposal-required')
        self.assertEqual(turn['actions'], [])
        for kwargs in ({'allow': True}, {'require_execution': True}, {'evidence_run_id': 'run'}, {'proposal_path': ''}, {'proposal_path': 1}):
            with self.assertRaisesRegex(AcademyError, 'invalid-request'):
                self.start('invalid-proposal', **({'proposal_path': 'tools.py'} | kwargs))

    async def test_comparison_freezes_two_exact_records_in_persistent_order_without_execution(self):
        baseline = self.evidence('baseline', created_at=9999)
        primary = self.evidence('primary', created_at=1, input_fingerprint='b' * 64,
            result={**baseline['result'], 'stdout': 'changed output\n', 'stderr': ''})
        for index in range(45):
            self.evidence('unrelated-' + str(index), project_id='other')
        receipt = self.start(evidence_run_id='primary', comparison_run_id='baseline')
        self.assertEqual(receipt['status'], 'ready')
        self.evidence('baseline', result={**baseline['result'], 'stderr': 'overwritten after acceptance'})
        with patch.object(AcademyRuntime, 'run', side_effect=AssertionError('比较证据不能重新执行')):
            turn, transport = await self.evaluate(receipt)
        context = json.loads(next(message['content'] for message in transport.requests[0]['messages'] if message['role'] == 'user'))
        self.assertEqual([run['id'] for run in context['runs']], ['primary', 'baseline'])
        self.assertEqual(context['runs'][1]['result'], baseline['result'])
        self.assertEqual(context['runs'][0]['input_fingerprint'], primary['input_fingerprint'])
        self.assertEqual(turn['comparison_run_id'], 'baseline')
        self.assertEqual(turn['status'], 'completed')
        self.assertEqual(turn['actions'], [])
        self.assertIn('selected_original_error', turn['response'])
        self.assertIn('changed output', turn['response'])
        self.assertEqual(len(self.service.runs(self.pid)), 2)
        self.assertEqual(self.start(evidence_run_id='primary', comparison_run_id='baseline')['result']['tutor_turn']['id'], turn['id'])
        with self.assertRaisesRegex(AcademyError, 'request-conflict'):
            self.start(evidence_run_id='primary')
        self.assertFalse(self.service.store.is_earlier('run', 'missing', 'primary'))
        self.assertFalse(self.service.store.is_earlier('run', 'primary', 'primary'))

    async def test_comparison_rejects_foreign_lesson_project_kind_and_reversed_order(self):
        self.evidence('baseline')
        self.evidence('primary')
        for index, (changes, code) in enumerate((({'project_id': 'other'}, 'run-not-found'),
                ({'lesson_id': 'other'}, 'run-not-found'), ({'kind': 'check'}, 'invalid-request'))):
            self.evidence('baseline', **changes)
            receipt = self.start('invalid-pair-' + str(index), evidence_run_id='primary', comparison_run_id='baseline')
            self.assertEqual(receipt['result']['error_code'], code)
        self.evidence('baseline')
        receipt = self.start('reversed', evidence_run_id='baseline', comparison_run_id='primary')
        self.assertEqual(receipt['result']['error_code'], 'invalid-request')
        self.assertEqual(self.start('missing-peer', evidence_run_id='primary', comparison_run_id='missing')['result']['error_code'], 'run-not-found')
        damaged = self.evidence('baseline', result={'status': 'failed'})
        self.assertEqual(self.start('corrupt-peer', evidence_run_id='primary', comparison_run_id='baseline')['result']['error_code'], 'store-invalid')
        self.assertEqual(self.service.store.get('run', 'baseline'), damaged)
        self.assertEqual(self.futures, [])

    async def test_comparison_cannot_escape_read_only_scope_or_lose_its_frozen_pair(self):
        for kwargs in ({'evidence_run_id': None}, {'comparison_run_id': 'primary'}, {'comparison_run_id': ''},
                       {'comparison_run_id': 1}, {'comparison_run_id': chr(0xd800)}, {'allow': True}, {'proposal_path': 'tools.py'}):
            with self.assertRaisesRegex(AcademyError, 'invalid-request'):
                self.start(**({'evidence_run_id': 'primary', 'comparison_run_id': 'baseline'} | kwargs))
        self.evidence('baseline')
        self.evidence('primary')
        receipt = self.start(evidence_run_id='primary', comparison_run_id='baseline')
        identity = receipt['result']['tutor_turn']['id']
        value = self.service.store.get(self.service.tutor._kind(self.pid), identity)
        value['snapshot']['runs'].reverse()
        self.service.store.put(self.service.tutor._kind(self.pid), value)
        with self.assertRaisesRegex(AcademyError, 'store-invalid'):
            await self.evaluate(receipt)
