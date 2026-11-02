"""运行时版本探测：有界输出、专属进程组与明确收尾。"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import tempfile

from ..core.cleanup import join_cleanup
from .process_env import stdio_env
from .processes import start_process, stop_process

OUTPUT_LIMIT = 8192
# 监督进程保留到宿主完成回收，不能因版本命令先退出而丢失后代归属。
_SUPERVISOR = r'''
import base64,json,subprocess,sys,threading,time
limit=int(sys.argv[1])
process=subprocess.Popen(json.loads(sys.argv[2]),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
output={}
def drain(name):
    value=bytearray()
    overflow=False
    while True:
        chunk=getattr(process,name).read(4096)
        if not chunk: break
        remaining=limit-len(value)
        value.extend(chunk[:remaining])
        overflow=overflow or len(chunk)>remaining
    output[name]=base64.b64encode(value).decode('ascii')
    output[name+'_overflow']=overflow
threads=[threading.Thread(target=drain,args=(name,)) for name in ('stdout','stderr')]
for thread in threads: thread.start()
output['exit_code']=process.wait()
for thread in threads: thread.join()
print(json.dumps(output),flush=True)
while True: time.sleep(3600)
'''


async def probe_version(argv: list[str], timeout: float) -> str:
    """只返回完整且合法的第一行，超时或输出超限不能伪造版本。"""
    # 包管理器版本查询不应读取项目配置、自动固定版本或下载运行时。
    with tempfile.TemporaryDirectory(prefix='harness-runtime-probe-') as directory:
        return await _probe_version(argv, timeout, directory)


async def _probe_version(argv: list[str], timeout: float, directory: str) -> str:
    """临时目录在所属进程树回收后才释放，保留原有输出与取消边界。"""
    process = await start_process(lambda: asyncio.create_subprocess_exec(
        sys.executable, '-I', '-X', 'utf8', '-c', _SUPERVISOR, str(OUTPUT_LIMIT), json.dumps(argv),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        cwd=directory, env=stdio_env({'COREPACK_ENABLE_NETWORK': '0', 'COREPACK_ENABLE_AUTO_PIN': '0',
                                    'COREPACK_ENABLE_PROJECT_SPEC': '0'}),
        limit=65536, start_new_session=os.name != 'nt',
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0), stop=stop_process)
    try:
        try:
            line = await asyncio.wait_for(process.stdout.readline(), timeout)
            value = json.loads(line.decode('utf-8'))
            if value['exit_code'] != 0 or value['stdout_overflow'] or value['stderr_overflow']:
                return ''
            raw = base64.b64decode(value['stdout'], validate=True) or base64.b64decode(value['stderr'], validate=True)
            output = raw.decode('utf-8')
            return output.splitlines()[0].strip() if output else ''
        except (TimeoutError, ValueError, KeyError, UnicodeError):
            return ''
    finally:
        await join_cleanup(asyncio.create_task(stop_process(process)))
