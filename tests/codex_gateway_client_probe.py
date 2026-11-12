"""显式客户端协议验收：真实 Codex 可执行文件，额度和模型上游均为替身。

此入口不会授权真实账号，不能证明跨账号加密状态可被真实后端接受，
也不能替代桌面界面或真实长等待验收。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import tempfile
import time
from pathlib import Path

import uvicorn

from harness.codex_gateway.accounts import AccountPool
from harness.codex_gateway.proxy import ResponsesProxy
from harness.codex_gateway.quota import parse_quota
from harness.codex_gateway.server import create_app
from harness.llm.chatgpt_auth import ChatGPTAccount
from harness.tools.processes import start_process, stop_process
from harness.core.cleanup import join_cleanup
from tests.test_codex_gateway import authorize, event


def registered_command(tools, prefix=''):
    """只选择客户端实际声明的命令工具，在隔离目录执行固定只读命令。"""
    for tool in tools:
        if tool.get('type') == 'namespace':
            result = registered_command(tool.get('tools', []), prefix + tool['name'] + '.')
            if result is not None:
                return result
        if tool.get('type') != 'function' or tool.get('name', '').split('.')[-1] not in {'exec_command', 'shell_command', 'shell'}:
            continue
        properties = tool.get('parameters', {}).get('properties', {})
        for field in ('cmd', 'command'):
            if field in properties:
                value = ['/bin/sh', '-c', 'cat fixture.txt'] if properties[field].get('type') == 'array' else 'cat fixture.txt'
                return prefix + tool['name'], {field: value}
    return None


async def probe(executable):
    with tempfile.TemporaryDirectory(prefix='harness-codex-client-') as directory:
        root = Path(directory)
        labels, used, reset_times = {}, {}, {}
        restored_at = None
        requests = []
        command_tool = None

        async def quota(account, epoch):
            identity = account.credentials['email']
            current = time.time()
            value = used[identity]
            resets = reset_times[identity]
            if identity == 'a@example.invalid' and restored_at is not None:
                value = 0 if current >= restored_at else 95
                resets = int(current) + 3600 if current >= restored_at else restored_at
            return parse_quota({'rateLimitsByLimitId': {'codex': {'primary': {
                'usedPercent': value, 'windowDurationMins': 300, 'resetsAt': resets}}}}, epoch=epoch)

        pool = AccountPool(root / 'gateway', ChatGPTAccount(root / 'gateway'), reader=quota)
        pool.configure(True)
        for rank, label in enumerate(('a', 'b', 'c', 'd')):
            slot = pool.slot('primary' if rank == 0 else pool.create())
            authorize(slot.account, label)
            labels[slot.account.credentials['access']] = label
            used[label + '@example.invalid'] = 10
            reset_times[label + '@example.invalid'] = int(time.time()) + 3600 + rank * 10

        class FixtureUpstream:
            async def post_stream(self, url, *, headers, body, timeout):
                nonlocal restored_at, command_tool
                document = json.loads(body.decode('utf-8'))
                label = labels[headers['Authorization'].removeprefix('Bearer ')]
                requests.append((label, document))
                sequence = len(requests)
                if sequence == 1:
                    command_tool = registered_command(document.get('tools', []))
                    if command_tool is None:
                        raise AssertionError('客户端未声明可验证的只读命令工具')
                if sequence <= 4:
                    name, arguments = command_tool
                    item = {'type': 'function_call', 'id': f'fc_fixture_{sequence}',
                            'call_id': f'call_fixture_{sequence}', 'name': name,
                            'arguments': json.dumps(arguments)}
                    reasoning = {'type': 'reasoning', 'id': f'rs_fixture_{sequence}', 'summary': [],
                                 'encrypted_content': f'opaque-fixture-state-{sequence}'}
                    output = [reasoning, item]
                    used[label + '@example.invalid'] = 95
                    if sequence == 4:
                        restored_at = int(time.time()) + 2
                    # 替身主动更新额度；不等待真实账号耗尽，也不修改生产阈值。
                    for slot in pool.slots.values():
                        slot.checked = float('-inf')
                else:
                    output = [{'type': 'message', 'id': 'msg_fixture', 'role': 'assistant', 'status': 'completed',
                               'content': [{'type': 'output_text', 'text': 'gateway-fixture-ok', 'annotations': []}]}]
                yield event('response.created', response={'id': f'resp_{sequence}', 'status': 'in_progress', 'output': []})
                for index, item in enumerate(output):
                    yield event('response.output_item.added', output_index=index, item=item)
                    yield event('response.output_item.done', output_index=index, item=item)
                yield event('response.completed', response={'id': f'resp_{sequence}', 'object': 'response',
                    'status': 'completed', 'output': output, 'usage': {'input_tokens': 10, 'output_tokens': 3, 'total_tokens': 13}})

        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        sock.listen()
        port = sock.getsockname()[1]
        app = create_app(pool, proxy=ResponsesProxy(pool, transport=FixtureUpstream(), heartbeat=.1))
        server = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False, proxy_headers=False))
        serving = asyncio.create_task(server.serve(sockets=[sock]))
        process = None
        readers = []
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if serving.done():
                        await serving
                        raise AssertionError('隔离网关启动未完成')
                    await asyncio.sleep(.01)
            workspace = root / 'workspace'
            workspace.mkdir()
            (workspace / 'fixture.txt').write_text('tool-fixture-ok\n', encoding='utf-8')
            codex_home = root / 'codex'
            codex_home.mkdir()
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith(('OPENAI_', 'CODEX_'))}
            environment.update(CODEX_HOME=str(codex_home), HARNESS_GATEWAY_FIXTURE_KEY=pool.credential())
            command = [executable, 'exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'read-only', '--json']
            settings = {'model': 'fixture-model', 'model_provider': 'harness-fixture',
                        'model_providers.harness-fixture.base_url': f'http://127.0.0.1:{port}/api/v1/codex',
                        'model_providers.harness-fixture.name': 'Harness fixture',
                        'model_providers.harness-fixture.wire_api': 'responses',
                        'model_providers.harness-fixture.env_key': 'HARNESS_GATEWAY_FIXTURE_KEY',
                        'model_providers.harness-fixture.supports_websockets': False,
                        'model_providers.harness-fixture.request_max_retries': 0,
                        'model_providers.harness-fixture.stream_max_retries': 0}
            for key, value in settings.items():
                command.extend(['-c', key + '=' + json.dumps(value)])
            command.append('Read fixture.txt when requested, then finish with gateway-fixture-ok.')
            process = await start_process(lambda: asyncio.create_subprocess_exec(
                *command, cwd=workspace, env=environment, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=os.name != 'nt'), stop=stop_process)

            async def capture(stream):
                chunks = bytearray()
                exceeded = False
                while chunk := await stream.read(8192):
                    if len(chunks) + len(chunk) > 8 * 1024 * 1024:
                        exceeded = True
                    elif not exceeded:
                        chunks.extend(chunk)
                if exceeded:
                    raise AssertionError('客户端输出超过验收上限')
                return chunks.decode('utf-8')

            readers = [asyncio.create_task(capture(process.stdout)), asyncio.create_task(capture(process.stderr))]
            async with asyncio.timeout(120):
                code = await process.wait()
                stdout, stderr = await asyncio.gather(*readers)
            if code != 0:
                raise AssertionError(f'客户端验收失败，退出码 {code}：{stderr[-2000:]}')
            if [label for label, _ in requests] != ['a', 'b', 'c', 'd', 'a']:
                raise AssertionError('客户端请求没有按 A→B→C→D→A 完成')
            for index, (_, document) in enumerate(requests[1:], 1):
                previous = [item for item in document['input'] if item.get('type') == 'function_call_output'
                            and item.get('call_id') == f'call_fixture_{index}']
                if len(previous) != 1 or 'tool-fixture-ok' not in json.dumps(previous[0]):
                    raise AssertionError('工具结果丢失、身份错配或只读命令未成功执行')
                reasoning = [item for item in document['input'] if item.get('type') == 'reasoning'
                             and item.get('encrypted_content') == f'opaque-fixture-state-{index}']
                if len(reasoning) != 1:
                    raise AssertionError('不透明推理状态未完整接续')
            events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
            if not any(item.get('type') == 'turn.completed' for item in events):
                raise AssertionError('客户端没有确认轮次完成')
            if not any(item.get('item', {}).get('text') == 'gateway-fixture-ok' for item in events):
                raise AssertionError('客户端没有接收到最终正文')
            return {'client_protocol': 'passed', 'account_route': ['a', 'b', 'c', 'd', 'a'],
                    'paired_tool_results': 4, 'opaque_states_preserved': 4,
                    'real_subscription_backend': 'not_tested', 'desktop_ui': 'not_tested',
                    'long_quota_wait': 'not_tested'}
        finally:
            if process is not None:
                await join_cleanup(asyncio.create_task(stop_process(process)))
            if readers:
                await join_cleanup(asyncio.gather(*readers, return_exceptions=True))
            server.should_exit = True
            try:
                await asyncio.wait_for(serving, 10)
            finally:
                sock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(probe(args.codex)), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
