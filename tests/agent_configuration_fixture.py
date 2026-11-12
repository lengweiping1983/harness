"""记录模型请求的共享传输夹具，不注册测试用例。"""
import json

from tests.web_fixture import FixtureTransport


class RecordingTransport(FixtureTransport):
    def __init__(self):
        self.requests = []

    async def post_stream(self, url, *, headers, body, timeout):
        self.requests.append(json.loads(body))
        async for chunk in super().post_stream(url, headers=headers, body=body, timeout=timeout):
            yield chunk
