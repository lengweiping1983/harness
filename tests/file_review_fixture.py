"""为文件审阅回归产生真实文件写入及执行回执，不调用在线模型。"""
import asyncio
import json

from tests.web_fixture import FixtureTransport, main


class FileReviewTransport(FixtureTransport):
    async def post_stream(self, url, *, headers, body, timeout):
        """首步写入临时文件，随后结束回合供浏览器审阅。"""
        messages = json.loads(body)['messages']
        user_index = max(index for index, message in enumerate(messages) if message['role'] == 'user')
        calls = [call for message in messages[user_index + 1:] for call in message.get('tool_calls', [])]
        if calls:
            delta, finish = {'content': 'file review fixture verified'}, 'stop'
        else:
            path = 'sample-en.txt' if 'fixture request en' in str(messages[user_index]['content']) else 'sample-zh-CN.txt'
            delta = {'tool_calls': [{'index': 0, 'id': 'fixture-file-write', 'type': 'function',
                'function': {'name': 'write', 'arguments': json.dumps({'path': path, 'content': 'first\nsecond\nthird\n'})}}]}
            finish = 'tool_calls'
        await asyncio.sleep(0.1)
        for chunk in [
            {'choices': [{'delta': {'role': 'assistant', **delta}, 'finish_reason': None}]},
            {'choices': [{'delta': {}, 'finish_reason': finish}], 'usage': {'prompt_tokens': 20, 'completion_tokens': 4}},
        ]:
            yield ('data: ' + json.dumps(chunk) + '\n\n').encode()
        yield b'data: [DONE]\n\n'


if __name__ == '__main__':
    main(transport=FileReviewTransport(), enabled=('enable_fs',), max_steps=3)
