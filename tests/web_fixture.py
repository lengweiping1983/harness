"""为浏览器回归启动隔离的真实 Harness 服务，模型传输保持确定性。"""
import asyncio
import json
import signal
import tempfile
import threading
from dataclasses import fields
from pathlib import Path

from harness import Harness, HarnessConfig
from harness.app.session_titles import MAX_TITLE_CHARS
from harness.i18n import resources
from harness.llm.transport import Transport
from harness.web.server import serve, stop

FIXTURE_REPLY = 'fixture response verified'
# 桩模型不产出可用标题：返回超过自动标题上限的候选，让产品保留按用户消息截断的临时标题。
# 若把正文原样当标题返回，会话标题会和回复正文同名，按正文定位回复的用例会同时命中标题、会话标签和消息正文。
FIXTURE_TITLE = 'fixture title request keeps the message digest instead of this candidate'
# 新建对话的标题是一次与正文无关的独立补全。桩模型若把正文原样当标题返回，会话标题就会和回复正文同名，
# 按正文定位回复的用例会同时命中标题、会话标签和消息正文，定位不再唯一。
TITLE_PROMPTS = frozenset(resources(language)['session.title.prompt'].format(limit=MAX_TITLE_CHARS)
                         for language in ('zh-CN', 'en'))


def is_title_request(messages):
    """只认标题提示词的请求；其他系统提示词仍按正文处理。"""
    return any(message.get('role') == 'system' and str(message.get('content', '')) in TITLE_PROMPTS
               for message in messages)


class FixtureTransport(Transport):
    async def post_stream(self, url, *, headers, body, timeout):
        """延迟首个片段，允许浏览器观察并断言真实活动运行。"""
        await asyncio.sleep(0.5)
        content = FIXTURE_TITLE if is_title_request(json.loads(body)['messages']) else FIXTURE_REPLY
        chunks = [
            {'choices': [{'delta': {'role': 'assistant', 'content': content}, 'finish_reason': None}]},
            {'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 20, 'completion_tokens': 4}},
        ]
        for chunk in chunks:
            yield ('data: ' + json.dumps(chunk) + '\n\n').encode()
        yield b'data: [DONE]\n\n'


def main(*, transport=None, enabled=(), max_steps=2, prepare=None):
    """使用临时目录和系统分配端口，不读取用户配置或凭据。"""
    with tempfile.TemporaryDirectory(prefix='harness-browser-test-') as directory:
        root = Path(directory)
        if prepare is not None:
            prepare(root)
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        flags.update({name: True for name in enabled})
        app = Harness(HarnessConfig(api_key='fixture-only', home=root/'state', cwd=root, language='zh-CN',
            model='fixture-model', max_steps=max_steps, max_retries=0, **flags), transport=transport or FixtureTransport())
        server = serve(app, port=0, background=True)
        print(json.dumps({'port': server.server_address[1]}), flush=True)
        done = threading.Event()
        signal.signal(signal.SIGTERM, lambda *args: done.set())
        signal.signal(signal.SIGINT, lambda *args: done.set())
        try:
            done.wait()
        finally:
            asyncio.run_coroutine_threadsafe(app.close(), server._harness_loop).result(timeout=15)
            stop(server)


if __name__ == '__main__':
    main()
