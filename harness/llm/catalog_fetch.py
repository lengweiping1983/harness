"""只读模型目录在独占辅助进程中读取，总超时后关闭其全部网络句柄。"""
import json
import subprocess
import sys

from ..i18n import tr
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


def read_catalog(url: str, key: str, *, timeout: float, limit: int) -> bytes:
    """凭据只经匿名stdin传递，超时由subprocess回收单用途读取进程。"""
    try:
        result = subprocess.run([sys.executable, '-I', '-X', 'utf8', '-c', _READER],
            input=json.dumps({'url': url, 'key': key, 'timeout': timeout, 'limit': limit}).encode('utf-8'),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
            env=stdio_env({}), check=False)
    except subprocess.TimeoutExpired:
        raise TimeoutError(tr('tools.models.catalogTimeout')) from None
    if result.returncode:
        try:
            failure = json.loads(result.stderr.decode('utf-8'))
            reason = failure['type'] + ': ' + failure['detail']
        except (ValueError, KeyError, TypeError, UnicodeError):
            reason = tr('tools.models.catalogInvalid')
        raise OSError(reason)
    return result.stdout
