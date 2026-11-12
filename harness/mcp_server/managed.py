"""设置页面启动的独立执行器；父进程关闭管道时正常退出。"""
from __future__ import annotations

import argparse
import asyncio
import json
import threading
import sys


def main():
    """密码只写私密文件，标准输出及日志不输出凭据。"""
    from ..execution.config import ExecutionConfig
    from ..core.language import current_language, resolve_language

    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--public-url', required=True)
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--local-port', required=True, type=int)
    args = parser.parse_args()
    config = ExecutionConfig.load(args.config)
    with current_language.set(resolve_language(config.language).effective_language):
        _run(config, args)


def _run(config, args):
    """启动与运行共享配置语言；退出恢复调用方上下文。"""
    from .auth import LocalOAuthProvider
    from .server import create_http_app
    from ..i18n import tr
    import uvicorn

    provider = LocalOAuthProvider(config.home, args.public_url)
    secret = config.home.parent / 'password.json'
    if not provider.password_enabled:
        password = provider.initialize_login()
        with secret.open('x', encoding='utf-8') as file:
            json.dump({'password': password}, file)
        secret.chmod(0o600)
    if not secret.exists():
        raise RuntimeError(tr('execution.managed.password_missing'))
    holder = {}
    server = uvicorn.Server(uvicorn.Config(create_http_app(config, public_url=args.public_url, runtime_holder=holder),
        host='127.0.0.1', port=args.port, log_level='warning'))
    local = uvicorn.Server(uvicorn.Config(create_http_app(config, runtime_holder=holder, manage_runtime=False),
        host='127.0.0.1', port=args.local_port, log_level='warning'))

    def watch_parent():
        """只消费监督管道，不把任何输入当作运行命令。"""
        while sys.stdin.buffer.read(1024):
            pass
        server.should_exit = True
        local.should_exit = True

    threading.Thread(target=watch_parent, daemon=True).start()
    async def serve():
        """公网入口拥有运行层，本地入口复用同一会话、锁和证据。"""
        public_task = asyncio.create_task(server.serve())
        while not server.started and not public_task.done():
            await asyncio.sleep(.05)
        if public_task.done():
            await public_task
            return
        local_task = asyncio.create_task(local.serve())
        done, pending = await asyncio.wait((public_task, local_task), return_when=asyncio.FIRST_COMPLETED)
        server.should_exit = local.should_exit = True
        await asyncio.gather(*done, *pending)

    asyncio.run(serve())


if __name__ == '__main__':
    main()
