"""为浏览器回归启动隔离的真实 Harness 服务，模型传输保持确定性。"""
import asyncio
import json
import signal
import tempfile
import threading
from dataclasses import fields
from pathlib import Path

from harness import Harness, HarnessConfig
from harness.llm.transport import Transport
from harness.web.server import serve, stop


class FixtureTransport(Transport):
    async def post_stream(self, url, *, headers, body, timeout):
        """延迟首个片段，允许浏览器观察并断言真实活动运行。"""
        await asyncio.sleep(0.5)
        chunks = [
            {'choices': [{'delta': {'role': 'assistant', 'content': 'fixture response verified'}, 'finish_reason': None}]},
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
