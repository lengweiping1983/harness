"""用真实目标工具和文件读取证据验收目标输入生命周期。"""
import asyncio
import json

from harness.i18n import tr
from tests.web_fixture import FixtureTransport, is_title_request, main


class GoalModeTransport(FixtureTransport):
    async def post_stream(self, url, *, headers, body, timeout):
        """按当前用户请求运行等待、阻塞或带证据完成的目标。"""
        messages = json.loads(body)['messages']
        # 标题请求复用原始用户消息，不能触发目标工具链。
        if is_title_request(messages):
            async for chunk in super().post_stream(url, headers=headers, body=body, timeout=timeout):
                yield chunk
            return
        user_index = max(index for index, message in enumerate(messages) if message['role'] == 'user')
        user = str(messages[user_index]['content'])
        if tr('python.goal_runtime.48cf5c7b3b57') in user:
            user += next((str(message['content']) for message in reversed(messages)
                          if message['role'] in ('developer', 'system')), '')
        calls = [call for message in messages[user_index + 1:] for call in message.get('tool_calls', [])]
        results = [str(message['content']) for message in messages[user_index + 1:] if message['role'] == 'tool']
        name, arguments = None, {}
        if 'goal fixture complete' in user:
            if not calls:
                name, arguments = 'read', {'path': 'goal-proof.txt'}
            elif calls[-1]['function']['name'] == 'read':
                name = 'get_goal'
            elif calls[-1]['function']['name'] == 'get_goal':
                value = json.JSONDecoder().raw_decode(results[-1][results[-1].index('{'):])[0]
                goal = value['goal']
                name, arguments = 'update_goal', {'goal_id': goal['id'], 'revision': goal['revision'], 'action': 'complete',
                    'checks': [{'criterion': 1, 'summary': '已读取并核对临时业务结果文件',
                                'evidence_seqs': [value['evidenceCandidates'][0]['sourceSeq']]}]}
        elif 'goal fixture waiting' in user and not calls:
            name, arguments = 'ask_user_question', {'questions': [{'id': 'goal-wait', 'header': '目标等待',
                'question': 'Goal fixture is waiting', 'options': [{'label': 'Continue', 'description': '继续当前轮'},
                    {'label': 'Finish turn', 'description': '结束当前轮'}]}]}
        elif 'goal fixture blocked' in user:
            if not calls:
                name = 'get_goal'
            elif calls[-1]['function']['name'] == 'get_goal':
                value = json.JSONDecoder().raw_decode(results[-1][results[-1].index('{'):])[0]
                goal = value['goal']
                name, arguments = 'update_goal', {'goal_id': goal['id'], 'revision': goal['revision'],
                    'action': 'blocked', 'blocked_reason': 'fixture external dependency'}
        else:
            async for chunk in super().post_stream(url, headers=headers, body=body, timeout=timeout):
                yield chunk
            return
        delta = {'tool_calls': [{'index': 0, 'id': f'goal-fixture-{len(calls)}', 'type': 'function',
                  'function': {'name': name, 'arguments': json.dumps(arguments)}}]} if name else {'content': 'goal fixture turn verified'}
        await asyncio.sleep(0.3)
        for chunk in [
            {'choices': [{'delta': {'role': 'assistant', **delta}, 'finish_reason': None}]},
            {'choices': [{'delta': {}, 'finish_reason': 'tool_calls' if name else 'stop'}], 'usage': {'prompt_tokens': 20, 'completion_tokens': 4}},
        ]:
            yield ('data: ' + json.dumps(chunk) + '\n\n').encode()
        yield b'data: [DONE]\n\n'


def prepare(root):
    """仅在隔离夹具中写入业务证据，不接触用户文件。"""
    (root / 'goal-proof.txt').write_text('goal fixture business result verified', encoding='utf-8')


if __name__ == '__main__':
    main(transport=GoalModeTransport(), enabled=('enable_goal', 'enable_plan_mode', 'enable_interaction', 'enable_fs'),
         max_steps=6, prepare=prepare)
