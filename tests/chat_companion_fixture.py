"""浏览器扩展回归的隔离执行器与回环桥，不读取用户运行数据。"""
import asyncio
import json
from pathlib import Path
import sys
import threading

from harness.core.language import current_language
from harness.execution.config import ExecutionConfig, ProjectConfig
from harness.execution.runtime import ExecutionRuntime
from harness.mcp_server.companion import create_server


async def main():
    root = Path(sys.argv[1]).resolve()
    project = root / 'project'
    project.mkdir()
    config = ExecutionConfig(root / 'state', {'fixture': ProjectConfig(project, writable=True, commands=True)}, language='en')
    with current_language.set('en'):
        async with ExecutionRuntime(config) as runtime:
            sid = runtime.store.open_session('local', 'fixture', 'Browser fixture', str(project))['session_id']
            with create_server(config, 0) as server:
                thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
                thread.start()
                try:
                    print(json.dumps({'port': server.server_port, 'token': server.companion.token, 'session_id': sid}), flush=True)
                    while True:
                        line = await asyncio.to_thread(sys.stdin.readline)
                        if not line or line.strip() == 'stop':
                            break
                        if line.strip() == 'progress':
                            revision = runtime.store.session(sid, 'local')['revision']
                            result = await runtime.call('local', 'harness_checkpoint', {'session_id': sid,
                                'request_id': 'checkpoint-' + str(revision), 'expected_revision': revision,
                                'summary': 'Confirmed fixture progress', 'next_steps': ['Review next step']})
                            print(json.dumps({'progress': not result['is_error']}), flush=True)
                finally:
                    await asyncio.to_thread(server.shutdown)
                    thread.join(timeout=3)


if __name__ == '__main__':
    from harness.core.text_encoding import configure_cli_streams
    configure_cli_streams()
    asyncio.run(main())
