"""受签名认证的远程 Executor 网关；只转发固定 Port，不暴露通用执行接口。"""
from __future__ import annotations

import asyncio
import hmac
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import threading
import socket
import time

from .superagent_common import _error
from .superagent_remote_protocol import MAX_FRAME,decode,encode,signature,validate


class WorkerGateway:
    def __init__(self,node,*,instance_id,device_id,identity_hash,key,host='127.0.0.1',port=0,tls=None):
        import ipaddress
        if not ipaddress.ip_address(host).is_loopback and tls is None:raise _error('remote.tls')
        if not isinstance(key,bytes) or len(key)<32:raise _error('remote.identity')
        self.node,self.instance_id,self.device_id,self.identity_hash,self.key=node,instance_id,device_id,identity_hash,key
        self.loop=asyncio.get_running_loop(); self.nonces={}; self.lock=threading.Lock(); self.active=threading.BoundedSemaphore(16)
        self.accepted=set(); self.closing=False
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.close_connection=True
                try:
                    self.connection.settimeout(15)
                    length=self.headers.get_all('Content-Length',[])
                    if (self.path!='/executor/v1' or len(length)!=1 or not length[0].isascii() or not length[0].isdigit()
                            or not 0<int(length[0])<=MAX_FRAME or self.headers.get('Transfer-Encoding') is not None
                            or len(self.headers.get_all('X-Harness-Signature',[]))!=1
                            or self.headers.get('Content-Type')!='application/json'):
                        self.send_error(400);return
                    raw=self.rfile.read(int(length[0]))
                    if not hmac.compare_digest(self.headers['X-Harness-Signature'],signature(outer.key,raw)):
                        self.send_error(403);return
                    value=validate(decode(raw),instance_id=outer.instance_id,device_id=outer.device_id,identity_hash=outer.identity_hash)
                    with outer.lock:
                        outer.nonces={key:stamp for key,stamp in outer.nonces.items() if stamp>time.time()-120}
                        if outer.closing or value['nonce'] in outer.nonces or len(outer.nonces)>=4096:raise _error('remote.identity')
                        outer.nonces[value['nonce']]=time.time()
                    future=asyncio.run_coroutine_threadsafe(outer.invoke(value),outer.loop)
                    try:result,error=future.result(),None
                    except Exception as exc:result,error=None,getattr(exc,'code','remote/operation')
                    response=encode({'version':1,'device_id':outer.device_id,'operation_id':value['operation_id'],
                        'nonce':value['nonce'],'result':result,'error':error})
                    self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(response)))
                    self.send_header('Cache-Control','no-store');self.send_header('X-Harness-Signature',signature(outer.key,response));self.end_headers()
                    self.wfile.write(response)
                except (OSError,ValueError,RuntimeError):
                    self.close_connection=True
                except Exception:
                    self.send_error(403)

        class Server(ThreadingHTTPServer):
            daemon_threads=False
            block_on_close=True
            request_queue_size=16
            address_family=socket.AF_INET6 if ':' in host else socket.AF_INET
            def process_request(self,request,address):
                request.settimeout(15)
                if not outer.active.acquire(blocking=False):
                    self.shutdown_request(request);return
                try:super().process_request(request,address)
                except BaseException:
                    outer.active.release();self.shutdown_request(request);raise
            def process_request_thread(self,request,address):
                try:super().process_request_thread(request,address)
                finally:outer.active.release()
        self.server=Server((host,port),Handler)
        if tls:self.server.socket=tls.wrap_socket(self.server.socket,server_side=True,do_handshake_on_connect=False)
        self.port=self.server.server_port
        self.thread=threading.Thread(target=self.server.serve_forever,name='superagent-worker-gateway',daemon=False)
        self.thread.start(); self._close_task=None

    async def invoke(self,value):
        if self.closing:raise _error('closed')
        task=asyncio.create_task(self.node.dispatch(value));self.accepted.add(task)
        try:
            from ..core.cleanup import join_cleanup
            return await join_cleanup(task)
        finally:self.accepted.discard(task)

    async def close(self):
        from ..core.cleanup import join_cleanup,raise_cleanup_errors
        if self._close_task is None:
            self.closing=True
            async def finish():
                errors=[]
                # 先停准入，保持循环可服务已受理的所属操作，随后才关闭原生宿主。
                await asyncio.to_thread(self.server.shutdown)
                results=await asyncio.gather(*tuple(self.accepted),return_exceptions=True)
                errors.extend(item for item in results if isinstance(item,BaseException))
                try:await self.node.close()
                except BaseException as exc:errors.append(exc)
                await asyncio.to_thread(self.server.server_close)
                await asyncio.to_thread(self.thread.join)
                raise_cleanup_errors(errors)
            self._close_task=asyncio.create_task(finish())
        await join_cleanup(self._close_task)
