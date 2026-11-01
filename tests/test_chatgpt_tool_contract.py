"""Responses 转换保留可省略参数语义，不改变宿主校验和证据身份。"""
import copy
import unittest
from types import SimpleNamespace

from harness.core.language import current_language
from harness.llm.chatgpt import responses_body
from harness.session.events import AppendOp
from harness.session.store import SessionStore
from harness.subagent.evidence import build_member_evidence_tool
from harness.tools.registry import ToolRegistry, ToolRunContext
from harness.llm.types import ToolUseBlock


class ChatGPTToolContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_optional_evidence_arguments_survive_wire_conversion_and_registry(self):
        session = SessionStore().create()
        await session.append('tool/call', {'call_id': 'actual-call', 'name': 'read',
            'run_id': 'actual-run', 'status': 'running', 'turn': 1,
            'input': {'path': '/fixture/source.md'}, 'effect': 'read'})
        await session.append('tool/result', {'call_id': 'actual-call', 'name': 'read', 'turn': 1,
            'results': [{'tool_use_id': 'actual-call', 'content': 'Original 证据😀', 'is_error': False}]},
            surface_op=AppendOp())
        state = SimpleNamespace(id='actual-run', session_id=session.id, parent_session_id='parent',
            settled=True, session=session, status='completed')
        tool = build_member_evidence_tool(SimpleNamespace(target_for=lambda *_: state, contexts=None))
        registry = ToolRegistry()
        registry.register(tool)
        originals = [event.to_dict() for event in session.events]
        for language in ('en', 'zh-CN', 'en'):
            with self.subTest(language=language), current_language.set(language):
                schema = tool.schema.resolve_description(language).to_dict()
                body = {'model': 'chatgpt/fixture', 'messages': [{'role': 'user', 'content': '读取证据'}],
                    'tools': [{'type': 'function', 'function': schema}]}
                original_body = copy.deepcopy(body)
                wire = responses_body(body)['tools'][0]
                # Responses 默认会尝试严格化；必须显式保留省略字段的工具合同。
                self.assertIs(wire['strict'], False)
                self.assertEqual(wire['parameters'], schema['parameters'])
                self.assertEqual(wire['parameters']['required'], ['agent_id'])
                self.assertEqual(body, original_body)
                ctx = ToolRunContext(call_id='fixture', session_id='parent', cwd=None, turn=1, step=1)
                listing = await registry.execute_call(ToolUseBlock(id='list', name='member_evidence',
                    input={'agent_id': 'actual-run'}), ctx)
                self.assertFalse(listing.is_error, listing.content)
                self.assertEqual(listing.data['tools'][0]['call_id'], 'actual-call')
                result = await registry.execute_call(ToolUseBlock(id='read', name='member_evidence',
                    input={'agent_id': 'actual-run', 'call_id': listing.data['tools'][0]['call_id']}), ctx)
                self.assertFalse(result.is_error, result.content)
                self.assertEqual(result.data['result']['text'], 'Original 证据😀')
                # 非严格模型生成不放宽宿主参数校验或接受虚构调用。
                for args in ({'agent_id': 'actual-run', 'call_id': 'x'},
                             {'agent_id': 'actual-run', 'call_id': None},
                             {'agent_id': 'actual-run', 'offset': True}):
                    rejected = await registry.execute_call(ToolUseBlock(id='invalid', name='member_evidence', input=args), ctx)
                    self.assertTrue(rejected.is_error)
                self.assertEqual([event.to_dict() for event in session.events], originals)

    def test_explicit_strict_choice_and_nested_schema_are_preserved(self):
        function = {'name': 'fixture', 'description': 'Original 原文', 'parameters': {
            'type': 'object', 'properties': {'query': {'type': 'string'}, 'options': {
                'type': 'object', 'properties': {'limit': {'type': 'integer'}},
                'required': [], 'additionalProperties': False}},
            'required': ['query'], 'additionalProperties': False}}
        for strict in (None, False, True):
            declaration = copy.deepcopy(function)
            if strict is not None:
                declaration['strict'] = strict
            body = {'model': 'chatgpt/fixture', 'messages': [],
                'tools': [{'type': 'function', 'function': declaration}]}
            original = copy.deepcopy(body)
            wire = responses_body(body)['tools'][0]
            self.assertIs(wire['strict'], False if strict is None else strict)
            self.assertEqual(wire['parameters'], function['parameters'])
            self.assertEqual(wire['description'], function['description'])
            self.assertEqual(body, original)
