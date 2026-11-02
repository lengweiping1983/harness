"""通过确定性的真实工具调用，复现中途进入计划和审批流程。"""
import asyncio
import json

from harness.i18n import tr
from tests.web_fixture import FixtureTransport, is_title_request, main


class PlanModeTransport(FixtureTransport):
    async def post_stream(self, url, *, headers, body, timeout):
        """普通消息直接应答；计划任务先澄清，再审阅，批准后应答。"""
        messages = json.loads(body)['messages']
        # 标题请求复用原始用户消息，不能按计划任务处理，否则标题会带上计划工具调用。
        if is_title_request(messages):
            async for chunk in super().post_stream(url, headers=headers, body=body, timeout=timeout):
                yield chunk
            return
        user_index = max(index for index, message in enumerate(messages) if message['role'] == 'user')
        if 'plan fixture request' not in str(messages[user_index]['content']):
            async for chunk in super().post_stream(url, headers=headers, body=body, timeout=timeout):
                yield chunk
            return
        calls = [call for message in messages[user_index + 1:] for call in message.get('tool_calls', [])]
        last_name = calls[-1]['function']['name'] if calls else None
        result_text = next((str(message.get('content', '')) for message in reversed(messages)
                            if message['role'] == 'tool'), '')
        if last_name == 'exit_plan_mode' and tr('owned.plan_tools.c2f73319c24a') in result_text:
            delta, finish = {'content': 'plan implementation verified'}, 'stop'
        else:
            name = 'ask_user_question' if not calls else 'exit_plan_mode'
            arguments = {'questions': [{'id': 'scope', 'header': '规划范围', 'question': 'Fixture scope clarification',
                'options': [{'label': 'Current scope', 'description': '保留当前范围'}, {'label': 'Expand scope', 'description': '扩大范围'}]}]} if not calls else {
                'plan': f'# Fixture implementation plan {len(calls)}\n\n核对范围，完成实施，验证结果。\n\n' + '\n'.join(
                    f'{index}. 核对现有执行路径，保留用户数据，完成实施后验证实际结果。' for index in range(1, 25))}
            delta = {'tool_calls': [{'index': 0, 'id': f'fixture-plan-{len(calls)}', 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(arguments)}}]}
            finish = 'tool_calls'
        await asyncio.sleep(0.5)
        for chunk in [
            {'choices': [{'delta': {'role': 'assistant', **delta}, 'finish_reason': None}]},
            {'choices': [{'delta': {}, 'finish_reason': finish}], 'usage': {'prompt_tokens': 20, 'completion_tokens': 4}},
        ]:
            yield ('data: ' + json.dumps(chunk) + '\n\n').encode()
        yield b'data: [DONE]\n\n'


if __name__ == '__main__':
    main(transport=PlanModeTransport(), enabled=('enable_plan_mode', 'enable_interaction'), max_steps=8)
