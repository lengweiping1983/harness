"""Superagent 信道共用的有界 HTTP 边界：禁止带凭据重定向，监听器只接收本机代理转发。"""
from __future__ import annotations

import asyncio
import ipaddress
import inspect
import json
import re
import threading
import urllib.error
from urllib.parse import urlsplit

from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..core.language import current_language
from ..core.errors import HarnessError
from ..net.http import ipv4_preferred_get
from ..net.request_control import RequestControl
from .superagent_common import _error, _json, _text


def api_root(value):
    """API 根由维护者固定；仅回环地址允许明文 HTTP。"""
    if not isinstance(value, str) or not re.fullmatch('[!-~]+', value):
        raise _error('invalid', field='api_url')
    url = urlsplit(_text(value, 'api_url', limit=4096))
    try:
        url.port
    except ValueError as exc:
        raise _error('invalid', field='api_url') from exc
    try:
        loopback = url.hostname == 'localhost' or ipaddress.ip_address(url.hostname).is_loopback
    except ValueError:
        loopback = False
    if (not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment
            or url.scheme != 'https' and not (url.scheme == 'http' and loopback)):
        raise _error('invalid', field='api_url')
    return value.rstrip('/')


class JSONClient:
    """仅访问显式 API 根和构造的路径；每个已接受请求等待所属线程完成。"""

    def __init__(self, root, *, prefix, timeout=10):
        self.root, self.prefix, self.timeout = root, prefix, timeout

    async def request(self, method, path, data=None, *, token=None, headers=None, expected_status=200,
                      before_request=None):
        return await self._request(method,path,data,token=token,headers=headers,expected_status=expected_status,before_request=before_request)

    async def request_bytes(self, path, *, token=None, headers=None, before_request=None, maximum=33554432):
        """有界只读二进制传输，与 JSON 共用来源、重定向和所属线程边界。"""
        if type(maximum) is not int or not 1<=maximum<=33554432:
            raise _error('invalid',field='maximum')
        return await self._request('GET',path,token=token,headers=headers,before_request=before_request,binary=True,maximum=maximum)

    async def _request(self,method,path,data=None,*,token=None,headers=None,expected_status=200,
                       before_request=None,binary=False,maximum=4194304):
        def invoke():
            control = RequestControl(threading.Event())
            timer = threading.Timer(self.timeout,control.close)
            timer.daemon = True
            timer.start()
            def no_redirect(_):
                raise _error(self.prefix + '-response')
            try:
                if before_request is not None:
                    checked = before_request()
                    if inspect.isawaitable(checked):
                        if inspect.iscoroutine(checked):
                            checked.close()
                        raise _error('invalid', field='before_request')
                    if checked is not None:
                        raise _error('invalid', field='before_request')
                with control.scope(), ipv4_preferred_get(self.root + path,method=method,
                        data=json.dumps(data).encode('utf-8') if data is not None else None,
                        headers={'Content-Type':'application/json','User-Agent':'Harness-Superagent',
                                 **({'Authorization':'Bearer '+token} if token is not None else {}),
                                 **(headers or {})},timeout=self.timeout,url_validator=no_redirect) as response:
                    raw = response.read(maximum+1)
                    if len(raw) > maximum or response.status != expected_status:
                        raise _error(self.prefix + '-response')
                    if binary:
                        return {'data':raw,'content_type':response.headers.get('Content-Type','application/octet-stream').split(';',1)[0].strip().lower()}
                    return _json(raw.decode('utf-8'))
            except urllib.error.HTTPError as exc:
                code = exc.code
                exc.close()
                raise _error(self.prefix + '-http',status=code) from None
            except (UnicodeError,ValueError,RecursionError) as exc:
                raise _error(self.prefix + '-response') from exc
            finally:
                timer.cancel()
                control.close()
        return await join_cleanup(asyncio.create_task(asyncio.to_thread(invoke)))


class WebhookServer:
    """限制请求头、正文、时间和连接数量；接收确认前不等待模型运行。"""

    def __init__(self, channel, port, language, *, path, prefix, success_status):
        self.channel, self.port, self.language = channel, port, language
        self.path, self.prefix, self.success_status = path, prefix, success_status
        self.server = None
        self.closed = False
        self.tasks, self.writers = set(),set()
        self.errors = []
        self._close_task = None

    async def start(self):
        binding = asyncio.create_task(asyncio.start_server(self._handle,'127.0.0.1',self.port,limit=8192))
        try:
            self.server = await join_cleanup(binding)
        except BaseException as original:
            errors = [original]
            if binding.done() and not binding.cancelled() and binding.exception() is None:
                self.server = binding.result()
                try:
                    await self.aclose()
                except BaseException as cleanup:
                    if cleanup is not original:
                        errors.append(cleanup)
            raise_cleanup_errors(errors)
        self.port = self.server.sockets[0].getsockname()[1]

    def stop(self):
        self.closed = True
        if self.server is not None:
            self.server.close()
        for writer in tuple(self.writers):
            writer.close()

    async def _handle(self, reader, writer):
        if self.closed or self.errors or len(self.tasks) >= 16:
            writer.close()
            await writer.wait_closed()
            return
        task = asyncio.current_task()
        self.tasks.add(task)
        self.writers.add(writer)
        status,value = 400,{'code':'superagent/' + self.prefix + '-request'}
        try:
            with current_language.set(self.language):
                raw = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),3)
                if len(raw) > 8192:
                    raise _error(self.prefix + '-request')
                lines = raw.decode('ascii').split('\r\n')
                if lines[0] != 'POST ' + self.path + ' HTTP/1.1':
                    status = 404
                else:
                    headers = {}
                    for line in lines[1:-2]:
                        key,separator,item = line.partition(':')
                        key = key.lower()
                        if not separator or not re.fullmatch('[a-z0-9-]+',key) or key in headers:
                            raise _error(self.prefix + '-request')
                        headers[key] = item.strip()
                    length = headers.get('content-length','')
                    if ('transfer-encoding' in headers or not re.fullmatch('[0-9]{1,8}',length)
                            or headers.get('content-type','').split(';',1)[0].strip().lower() != 'application/json'):
                        raise _error(self.prefix + '-request')
                    if not 1 <= int(length) <= 1048576:
                        status,value = 413,{'code':'superagent/' + self.prefix + '-request'}
                    else:
                        body = await asyncio.wait_for(reader.readexactly(int(length)),5)
                        accepted = asyncio.create_task(asyncio.to_thread(self.channel.receive,headers,body,queued=True))
                        value = await join_cleanup(accepted)
                        status = self.success_status
        except HarnessError as exc:
            status = 503 if exc.code == 'superagent/' + self.prefix + '-capacity' else 401 if exc.code == 'superagent/' + self.prefix + '-signature' else 403 if exc.code == 'superagent/' + self.prefix + '-scope' else 409 if exc.code in {
                'superagent/event-conflict','superagent/conflict'} else 400
            value = {'code':exc.code,'message':exc.message}
        except (UnicodeError,ValueError,TimeoutError,asyncio.IncompleteReadError,asyncio.LimitOverrunError):
            pass
        except (ConnectionError,OSError):
            return
        except Exception as exc:
            if len(self.errors) < 16:
                self.errors.append(exc)
            status,value = 500,{'code':'superagent/' + self.prefix + '-unavailable'}
        finally:
            try:
                if not writer.is_closing():
                    payload = json.dumps(value,ensure_ascii=False).encode('utf-8')
                    writer.write(('HTTP/1.1 '+str(status)+' Response\r\nContent-Type: application/json; charset=utf-8\r\n'
                        'Content-Length: '+str(len(payload))+'\r\nConnection: close\r\n\r\n').encode('ascii')+payload)
                    await asyncio.wait_for(writer.drain(),2)
            except (ConnectionError,OSError,TimeoutError):
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError,OSError):
                    pass
                self.tasks.discard(task)
                self.writers.discard(writer)

    async def aclose(self):
        self.stop()
        if self._close_task is None:
            async def finish():
                if self.server is not None:
                    await self.server.wait_closed()
                results = await asyncio.gather(*tuple(self.tasks),return_exceptions=True)
                raise_cleanup_errors([*self.errors, *[item for item in results if isinstance(item,BaseException)]])
            self._close_task = asyncio.create_task(finish())
        await join_cleanup(self._close_task)
