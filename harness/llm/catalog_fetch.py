"""只读模型目录在独占辅助进程中读取，总超时后关闭其全部网络句柄。"""
import asyncio
import json
import os
import sys

from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..i18n import tr
from ..tools.owned_process import spawn_owned
from ..tools.processes import ProcessCleanupError
from ..tools.process_env import stdio_env

_READER = r'''
import json,sys,urllib.error,urllib.request
value=json.loads(sys.stdin.buffer.read())
request=urllib.request.Request(value['url'],method='GET',headers={'Accept':'application/json'})
if value['key']:request.add_header('Authorization','Bearer '+value['key'])
try:
    with urllib.request.urlopen(request,timeout=value['timeout']) as response:
        raw=response.read(value['limit']+1)
    sys.stdout.buffer.write(raw)
except Exception as error:
    # 不输出请求头或凭据，也不读取远端错误正文。
    detail=str(error.reason) if isinstance(error,urllib.error.URLError) else type(error).__name__
    sys.stderr.write(json.dumps({'type':type(error).__name__,'detail':detail[:2048]}))
    sys.exit(1)
'''


async def _bounded_output(stream, limit):
    """持续排空所属输出，只保留协议所需的有界前缀。"""
    value = bytearray()
    while chunk := await stream.read(4096):
        value.extend(chunk[:max(0, limit - len(value))])
    return bytes(value)


async def _read_catalog(url, key, *, timeout, limit):
    """总预算包含创建与读取；超时先回收整个所属组，再等待管道收尾。"""
    payload = json.dumps({'url': url, 'key': key, 'timeout': timeout, 'limit': limit},
                         ensure_ascii=False).encode('utf-8')
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        async with asyncio.timeout(timeout):
            process = await spawn_owned([sys.executable, '-I', '-X', 'utf8', '-c', _READER],
                cwd=os.getcwd(), env=stdio_env({}), stdin=True)
    except TimeoutError:
        raise TimeoutError(tr('tools.models.catalogTimeout')) from None
    io = asyncio.gather(process.write_input(payload), _bounded_output(process.stdout, limit + 1),
                        _bounded_output(process.stderr, 8192), process.wait(), return_exceptions=True)
    primary = None
    try:
        done, _ = await asyncio.wait((io,), timeout=max(0, deadline - loop.time()))
        if not done:
            raise TimeoutError(tr('tools.models.catalogTimeout'))
    except BaseException as error:
        primary = error

    async def finish():
        await process.stop_owned()
        values = await io
        process.close()
        return values

    try:
        values = await join_cleanup(asyncio.create_task(finish()))
    except Exception as error:
        # 未确认回收不能退化成普通目录不可用；原错误与截止时间保留为原因。
        if isinstance(error, ProcessCleanupError):
            if primary is not None:
                raise error from primary
            raise
        failure = ProcessCleanupError(tr('cleanup.failed'), pid=process.pid)
        originals = ([primary] if primary is not None else []) + [error]
        cause = originals[0] if len(originals) == 1 else BaseExceptionGroup(tr('cleanup.failed'), originals)
        raise failure from cause
    except BaseException as error:
        raise_cleanup_errors(([primary] if primary is not None else []) + [error])
    errors = [value for value in values if isinstance(value, BaseException)]
    if primary is not None:
        if errors:
            cause = errors[0] if len(errors) == 1 else BaseExceptionGroup(tr('cleanup.failed'), errors)
            raise primary from cause
        raise primary
    raise_cleanup_errors(errors)
    _, stdout, stderr, code = values
    if code:
        try:
            failure = json.loads(stderr.decode('utf-8'))
            reason = failure['type'] + ': ' + failure['detail']
        except (ValueError, KeyError, TypeError, UnicodeError):
            reason = tr('tools.models.catalogInvalid')
        raise OSError(reason)
    return stdout


def read_catalog(url: str, key: str, *, timeout: float, limit: int) -> bytes:
    """同步线程入口；凭据只走匿名输入，结束前确认所属进程和连接释放。"""
    return asyncio.run(_read_catalog(url, key, timeout=timeout, limit=limit))
