"""真实供应商桥接、工具纠错与可视化事件的隔离夹具，不访问在线账号。"""
import asyncio
import json
import tempfile
import threading
from dataclasses import fields
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from harness import Harness, HarnessConfig
from harness.core.jsonutil import to_jsonable
from tests.test_provider_connections import provider_fixture

EXAMPLES = Path(__file__).resolve().parents[1] / 'examples' / 'visualizations'
SVG = (EXAMPLES / 'request-flow.svg').read_text(encoding='utf-8').strip()
HTML = (EXAMPLES / 'flow-lab.html').read_text(encoding='utf-8').strip()



async def visual_fixture():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            """不输出凭据或请求内容。"""
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            index = len(requests)
            if index == 1:
                arguments = '{"title":"Broken","widget_code":"<div>unfinished'
            else:
                arguments = json.dumps({'title': 'SVG diagram' if index == 2 else 'HTML demo', 'widget_code': SVG if index == 2 else HTML})
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            if index <= 3:
                values = [{'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'id': f'widget-{index}', 'function': {'name': 'show_widget', 'arguments': ''}}]}, 'finish_reason': None}]}]
                for offset in range(0, len(arguments), 35):
                    values.append({'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': arguments[offset:offset+35]}}]}, 'finish_reason': None}]})
                values.append({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'length' if index == 1 else 'tool_calls'}]})
            else:
                values = [{'choices': [{'index': 0, 'delta': {'content': '完成'}, 'finish_reason': 'stop'}]}]
            for value in values:
                self.wfile.write(('data: ' + json.dumps(value) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as folder:
            flags = {f.name: False for f in fields(HarnessConfig) if f.name.startswith('enable_')}
            app = Harness(HarnessConfig(api_key='fixture', home=Path(folder)/'state', cwd=folder,
                model='harness/supplier/endpoint/model-a', max_steps=6, max_retries=0, **flags), codex_gateway_home=Path(folder)/'gateway')
            try:
                provider = provider_fixture(f'http://127.0.0.1:{server.server_port}/v1')
                provider['type'] = 'qwen-token-plan-cn'
                connection = provider['connections'][0]
                connection['keys'] = connection['keys'][:1]
                connection['models'] = connection['models'][:1]
                connection['models'][0]['model'] = 'deepseek-v4.1-flash-1017'
                connection['model_catalog'] = [{'id': 'deepseek-v4.1-flash-1017', 'api': 'openai-completions', 'reasoning': True}]
                app.provider_connections.save([provider], app.provider_connections.revision)
                session = app.create_session()
                result = await app.run_async(session, '展示 SVG 与 HTML 可视化')
                tools = [event for event in session.events if event.type == 'tool/result']
                return {'reason': result.reason, 'error': result.error, 'requests': requests,
                        'tools': [event.data for event in tools],
                        'assistant': [event.data for event in session.events if event.type == 'assistant/message'],
                        'open_turn': session.open_turn, 'svg': SVG, 'html': HTML}
            finally:
                await app.close()
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join()


if __name__ == '__main__':
    print(json.dumps(to_jsonable(asyncio.run(visual_fixture())), ensure_ascii=False))
