"""为显式在线验收启动临时服务；密钥仅从环境读取，不保存用户配置。"""
import asyncio
import json
import os
import signal
import tempfile
import threading
from dataclasses import fields
from pathlib import Path

from harness import Harness, HarnessConfig
from harness.web.server import serve, stop


def main():
    """隔离运行目录、项目和工具；退出时关闭本次服务。"""
    key = os.environ.get('HARNESS_LIVE_API_KEY')
    if not key:
        raise SystemExit('需要显式设置 HARNESS_LIVE_API_KEY 才会调用在线模型')
    with tempfile.TemporaryDirectory(prefix='harness-live-ui-') as directory:
        root = Path(directory)
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        flags.update(enable_fs=True, enable_shell=True, enable_interaction=True, enable_plan_mode=True)
        flags['enable_goal'] = os.environ.get('HARNESS_LIVE_GOAL') == '1'
        app = Harness(HarnessConfig(api_key=key,
            base_url=os.environ.get('HARNESS_LIVE_BASE_URL', 'https://opencode.ai/zen/v1'),
            model=os.environ.get('HARNESS_LIVE_MODEL', 'minimax-m3.1-flash-free'),
            home=root/'state', cwd=root, language='zh-CN', permission_policy='full_access',
            max_steps=8, max_retries=0, network_timeout=60, timeout=120, **flags))
        server = serve(app, port=0, background=True)
        print(json.dumps({'port': server.server_address[1], 'workspace': str(root)}), flush=True)
        done = threading.Event()
        signal.signal(signal.SIGTERM, lambda *args: done.set())
        signal.signal(signal.SIGINT, lambda *args: done.set())
        try:
            done.wait()
        finally:
            asyncio.run_coroutine_threadsafe(app.close(), server._harness_loop).result(timeout=20)
            stop(server)


if __name__ == '__main__':
    main()
