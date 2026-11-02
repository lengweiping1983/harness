"""验证模型定时工具会把请求写入当前对话的自动化目录。"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from dataclasses import fields

from harness import Harness, HarnessConfig
from harness.i18n import tr
from harness.llm.transport import Transport

from harness.app.automations import AutomationService
from harness.tools.builtin.automation_tools import build_automation_tools
from harness.prompt.tool_policies import build_tool_policies
from harness.tools.registry import ToolRunContext


class AutomationToolTests(unittest.TestCase):
    def test_parameter_resources_resolve_on_the_same_schema(self):
        for tool in build_automation_tools():
            schema = tool.schema
            for language in ('en', 'zh-CN', 'en'):
                resolved = schema.resolve_description(language)
                self.assertTrue(resolved.description)
                for path, resource in schema.parameter_keys.items():
                    value = resolved.parameters
                    for part in path:
                        value = value[part]
                    from harness.core.language import current_language
                    with current_language.set(language):
                        self.assertEqual(value, tr(resource))

    def test_policy_exposes_plain_language_scheduling_guidance(self):
        sections = build_tool_policies({'schedule_automation'})
        self.assertTrue(any(section == 'tool:automation' and 'schedule_automation' in provider() for section, _order, provider in sections))

    def test_schedule_automation_uses_current_session_and_sends_prompt(self):
        with tempfile.TemporaryDirectory() as root:
            service = AutomationService(Path(root) / 'automations.db', SimpleNamespace(get_session=Mock()))
            tool = build_automation_tools()[0]
            context = ToolRunContext(
                call_id='call-1', session_id='session-current', cwd=None, turn=1, step=1,
                services={'automations': service},
            )
            result = asyncio.run(tool.execute({
                'name': '继续提醒',
                'prompt': '继续',
                'schedule': {'kind': 'interval', 'seconds': 1800},
            }, context))
            self.assertFalse(result.is_error)
            saved = service.list()[0]
            self.assertEqual(saved['session_id'], 'session-current')
            self.assertEqual(saved['prompt'], '继续')
            self.assertEqual(saved['schedule'], {'kind': 'interval', 'seconds': 1800})
            self.assertTrue(saved['enabled'])

    def test_replayed_tool_call_recovers_the_same_automation(self):
        with tempfile.TemporaryDirectory() as root:
            service = AutomationService(Path(root) / 'automations.db', SimpleNamespace(get_session=Mock()))
            tool = build_automation_tools()[0]
            context = ToolRunContext(
                call_id='same-call', session_id='session-current', cwd=None, turn=1, step=1,
                services={'automations': service},
            )
            args = {'name': '继续提醒', 'prompt': '继续', 'schedule': {'kind': 'interval', 'seconds': 1800}}
            first = asyncio.run(tool.execute(args, context))
            second = asyncio.run(tool.execute(args, context))
            self.assertFalse(first.is_error)
            self.assertFalse(second.is_error)
            self.assertEqual(len(service.list()), 1)

    def test_delete_is_scoped_preserves_history_and_prevents_future_runs(self):
        with tempfile.TemporaryDirectory() as root:
            service = AutomationService(Path(root) / 'automations.db', SimpleNamespace(get_session=Mock()))
            tools = {tool.schema.name: tool for tool in build_automation_tools()}
            context = ToolRunContext(call_id='remove-call', session_id='current', cwd=None, turn=1, step=1, services={'automations': service})
            body = {'name': '同名继续提醒', 'prompt': '继续', 'schedule': {'kind': 'interval', 'seconds': 1800}}
            current = service.save({**body, 'session_id': 'current', 'creation_id': 'a'*32})
            foreign = service.save({**body, 'session_id': 'other', 'creation_id': 'b'*32})
            listed = asyncio.run(tools['list_automations'].execute({}, context))
            self.assertEqual([row['id'] for row in listed.data['automations']], [current['id']])
            denied = asyncio.run(tools['delete_automation'].execute({'automation_id': foreign['id']}, context))
            self.assertTrue(denied.is_error)
            self.assertEqual(len(service.list()), 2)
            with service.db() as db:
                db.execute('UPDATE automations SET running=1 WHERE id=?', (current['id'],))
            running = asyncio.run(tools['delete_automation'].execute({'automation_id': current['id']}, context))
            self.assertTrue(running.is_error)
            with service.db() as db:
                db.execute('UPDATE automations SET running=0,due=0 WHERE id=?', (current['id'],))
                db.execute("INSERT INTO automation_runs(id,automation_id,started,finished,status) VALUES('past',?,1,2,'completed')", (current['id'],))
            result = asyncio.run(tools['delete_automation'].execute({'automation_id': current['id']}, context))
            self.assertFalse(result.is_error)
            self.assertEqual(result.data, {'automation_id': current['id'], 'deleted': True})
            self.assertEqual([row['id'] for row in service.list()], [foreign['id']])
            self.assertEqual(service.history()[0]['id'], 'past')
            self.assertEqual(asyncio.run(service.tick()), 0)
            missing = asyncio.run(tools['delete_automation'].execute({'automation_id': current['id']}, context))
            self.assertTrue(missing.is_error)

    def test_update_automation_changes_only_requested_fields_in_current_session(self):
        with tempfile.TemporaryDirectory() as root:
            service = AutomationService(Path(root) / 'automations.db', SimpleNamespace(get_session=Mock()))
            tools = {tool.schema.name: tool for tool in build_automation_tools()}
            context = ToolRunContext(call_id='update-call', session_id='current', cwd=None, turn=1, step=1, services={'automations': service})
            body = {'name': '继续提醒', 'prompt': '继续', 'schedule': {'kind': 'weekly', 'time': '09:00', 'days': [0]}}
            current = service.save({**body, 'session_id': 'current', 'creation_id': 'e'*32})
            foreign = service.save({**body, 'session_id': 'other', 'creation_id': 'f'*32})

            # 没有提供任何变更字段时拒绝写入。
            empty = asyncio.run(tools['update_automation'].execute({'automation_id': current['id']}, context))
            self.assertTrue(empty.is_error)

            # 只改名称与启用状态，其余字段保持原值。
            paused = asyncio.run(tools['update_automation'].execute(
                {'automation_id': current['id'], 'name': '改名提醒', 'enabled': False}, context))
            self.assertFalse(paused.is_error)
            saved = next(row for row in service.list() if row['id'] == current['id'])
            self.assertEqual(saved['name'], '改名提醒')
            self.assertEqual(saved['prompt'], '继续')
            self.assertEqual(saved['schedule'], {'kind': 'weekly', 'time': '09:00', 'days': [0]})
            self.assertFalse(saved['enabled'])

            # 改为每月计划后按新日程重新计算到期时间。
            changed = asyncio.run(tools['update_automation'].execute(
                {'automation_id': current['id'], 'schedule': {'kind': 'monthly', 'time': '08:30', 'monthdays': [1, 15]}, 'enabled': True}, context))
            self.assertFalse(changed.is_error)
            saved = next(row for row in service.list() if row['id'] == current['id'])
            self.assertEqual(saved['schedule'], {'kind': 'monthly', 'time': '08:30', 'monthdays': [1, 15]})
            self.assertTrue(saved['enabled'])

            # 其他对话的任务不在当前对话的作用域内。
            denied = asyncio.run(tools['update_automation'].execute({'automation_id': foreign['id'], 'name': '越权'}, context))
            self.assertTrue(denied.is_error)
            self.assertEqual(next(row for row in service.list() if row['id'] == foreign['id'])['name'], '继续提醒')

            # 运行中的任务不能修改。
            with service.db() as db:
                db.execute('UPDATE automations SET running=1 WHERE id=?', (current['id'],))
            running = asyncio.run(tools['update_automation'].execute({'automation_id': current['id'], 'name': '运行中'}, context))
            self.assertTrue(running.is_error)

    def test_management_tools_report_disabled_service(self):
        context = ToolRunContext(call_id='disabled', session_id='current', cwd=None, turn=1, step=1, services={})
        for tool in build_automation_tools()[1:]:
            self.assertTrue(asyncio.run(tool.execute({'automation_id': 'anything'}, context)).is_error)


class SchedulingTransport(Transport):
    """确定性模拟模型调用，保留真实提示词、目录和工具执行链。"""

    def __init__(self):
        self.requests = []

    async def post_stream(self, url, *, headers, body, timeout):
        request = json.loads(body)
        self.requests.append(request)
        if len(self.requests) == 1:
            delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'schedule-fixture',
                'type': 'function', 'function': {'name': 'schedule_automation', 'arguments': json.dumps({
                    'name': '每30分钟继续', 'prompt': '继续',
                    'schedule': {'kind': 'interval', 'seconds': 1800}}, ensure_ascii=False)}}]}
            finish = 'tool_calls'
        else:
            delta, finish = {'role': 'assistant', 'content': 'fixture response verified'}, 'stop'
        for chunk in [
            {'choices': [{'delta': delta, 'finish_reason': None}]},
            {'choices': [{'delta': {}, 'finish_reason': finish}], 'usage': {'prompt_tokens': 20, 'completion_tokens': 4}},
        ]:
            yield ('data: ' + json.dumps(chunk) + '\n\n').encode()
        yield b'data: [DONE]\n\n'


class AutomationConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_conversation_lists_then_deletes_its_timer(self):
        class RemovingTransport(SchedulingTransport):
            """固定查询与删除顺序，使用真实工具流水线。"""

            automation_id = None

            async def post_stream(self, url, *, headers, body, timeout):
                self.requests.append(json.loads(body))
                step = len(self.requests)
                if step <= 2:
                    name = 'list_automations' if step == 1 else 'delete_automation'
                    args = {} if step == 1 else {'automation_id': self.automation_id}
                    delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'remove-fixture-'+str(step),
                        'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}
                    finish = 'tool_calls'
                else:
                    delta, finish = {'role': 'assistant', 'content': 'fixture deletion verified'}, 'stop'
                yield ('data: '+json.dumps({'choices': [{'delta': delta, 'finish_reason': None}]})+'\n\n').encode()
                yield ('data: '+json.dumps({'choices': [{'delta': {}, 'finish_reason': finish}], 'usage': {'prompt_tokens': 20, 'completion_tokens': 4}})+'\n\n').encode()
                yield b'data: [DONE]\n\n'

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
            flags['enable_automations'] = True
            transport = RemovingTransport()
            app = Harness(HarnessConfig(api_key='fixture-only', home=root/'state', cwd=root,
                language='zh-CN', model='fixture', max_steps=5, max_retries=0,
                tool_schema_mode='all', **flags), transport=transport)
            try:
                session, other = app.create_session(), app.create_session()
                body = {'name': '每分钟发送继续', 'prompt': '继续', 'schedule': {'kind': 'interval', 'seconds': 60}}
                saved = app.automations.save({**body, 'session_id': session.id, 'creation_id': 'c'*32})
                foreign = app.automations.save({**body, 'session_id': other.id, 'creation_id': 'd'*32})
                transport.automation_id = saved['id']
                result = await app.run_async(session.id, '把当前对话的定时器去掉')
                self.assertEqual(result.reason, 'completed')
                self.assertEqual([row['id'] for row in app.automations.list()], [foreign['id']])
                self.assertIn('"deleted": true', json.dumps(transport.requests, ensure_ascii=False).replace('\\"', '"'))
                self.assertNotIn(foreign['id'], json.dumps(transport.requests))
                schemas = {row['function']['name']: row['function'] for row in transport.requests[0]['tools']}
                self.assertIn('list_automations', schemas)
                self.assertTrue(schemas['delete_automation']['parameters']['properties']['automation_id']['description'])
            finally:
                await app.close()

    async def test_real_conversation_creates_and_delivers_the_reminder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
            flags['enable_automations'] = True
            transport = SchedulingTransport()
            app = Harness(HarnessConfig(api_key='fixture-only', home=root/'state', cwd=root,
                language='en', model='fixture', max_steps=4, max_retries=0,
                tool_schema_mode='all', **flags), transport=transport)
            try:
                session = app.create_session()
                for language in ('en', 'zh-CN', 'en'):
                    assembled = app.prompts.assemble(variables={'language': language, 'session_id': session.id})
                    self.assertIn('schedule_automation', assembled.render())
                result = await app.run_async(session.id, '每30分钟在当前对话发送“继续”')
                self.assertEqual(result.reason, 'completed')
                schema = next(row['function'] for row in transport.requests[0]['tools']
                              if row['function']['name'] == 'schedule_automation')
                self.assertTrue(schema['parameters']['properties']['prompt']['description'])
                saved, = app.automations.list()
                self.assertEqual(saved['session_id'], session.id)
                self.assertEqual(saved['prompt'], '继续')
                # 将隔离数据库的到期点推进，验证发送，无需等待真实的三十分钟。
                with app.automations.db() as db:
                    db.execute('UPDATE automations SET due=0 WHERE id=?', (saved['id'],))
                self.assertEqual(await app.automations.tick(), 1)
                await asyncio.gather(*list(app.automations.tasks))
                messages = [event.data for event in session.events_of_type('user/message')]
                self.assertEqual(messages[-1]['text'], '继续')
                self.assertEqual(messages[-1]['source'], 'automation')
                run, = app.automations.history()
                self.assertEqual(run['status'], 'completed')
            finally:
                await app.close()


if __name__ == '__main__':
    unittest.main()
