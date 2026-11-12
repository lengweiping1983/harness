"""Worker 的短期模型能力服务；长期凭据不进入协议帧或原生事件。"""
from __future__ import annotations

import asyncio
from collections import deque
import json
import base64
from contextvars import ContextVar
import os
from pathlib import Path
import secrets
import socket
import socketserver
from socketserver import StreamRequestHandler
import threading
import time

from ..net.http import ipv4_preferred_get
from ..llm.transport import Transport
from ..core.cleanup import join_cleanup
from .superagent_common import _error
from .superagent_remote_protocol import decode,encode,MAX_FRAME
from .superagent_coordinator_store import _hash

BROKER_SCOPE=ContextVar('superagent_broker_scope',default=None)


class ModelBroker:
    """共享能力与审计规则；原生执行不需要 Docker 或 Unix Socket。"""
    def __init__(self,*,upstream,api_key,models,lease_valid,forward=None,audit_path=None):
        self.upstream,self.api_key,self.models,self.lease_valid=upstream,api_key,set(models),lease_valid
        self.closed=False
        self.capabilities={};self.lock=threading.Lock();self._audit=deque(maxlen=256);self.forward=forward or self._forward
        self.audit_stream=None
        if audit_path is not None:
            path=Path(audit_path)
            if path.is_symlink() or path.exists() and not path.is_file():raise _error('corrupt')
            if path.exists():
                with path.open('rb') as existing:
                    while line:=existing.readline(8193):
                        try:
                            if len(line)>8192 or not isinstance(json.loads(line),dict):raise ValueError
                        except (ValueError,UnicodeError) as exc:raise _error('corrupt') from exc
            descriptor=os.open(path,os.O_WRONLY|os.O_APPEND|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
            self.audit_stream=os.fdopen(descriptor,'ab',buffering=0)

    @property
    def audit(self):
        return list(self._audit)

    def _record(self,event):
        event={**event,'at':int(time.time())}
        if self.audit_stream is not None:
            self.audit_stream.write(encode(event)+b'\n');os.fsync(self.audit_stream.fileno())
        self._audit.append(event)

    def grant(self,*,mission_id,generation,model,max_requests,expires_at):
        if model not in self.models or type(max_requests) is not int or not 1<=max_requests<=6400 or expires_at<=time.time():raise _error('remote.capability')
        token=secrets.token_urlsafe(32)
        with self.lock:
            if self.closed or len(self.capabilities)>=4096:raise _error('capacity')
            self.capabilities[_hash(token)]={'mission_id':mission_id,'generation':generation,'model':model,'remaining':max_requests,'expires_at':expires_at}
            self._record({'event':'capability.issued','mission_id':mission_id,'generation':generation,'model':model})
        return token

    def renew(self,lease):
        with self.lock:
            for cap in self.capabilities.values():
                if cap['mission_id']==lease['mission_id'] and cap['generation']==lease['generation']:
                    cap['expires_at']=max(cap['expires_at'],lease['expires_at'])

    def request(self,value):
        if set(value)!={'token','body'} or not isinstance(value['body'],dict):raise _error('remote.capability')
        token=value['token']
        if not isinstance(token,str) or len(token)>128:raise _error('remote.capability')
        with self.lock:
            cap=self.capabilities.get(_hash(token))
            if (self.closed or not cap or cap['expires_at']<=time.time() or cap['remaining']<1
                    or value['body'].get('model')!=cap['model'] or not self.lease_valid(cap['mission_id'],cap['generation'])):
                raise _error('remote.capability')
            cap['remaining']-=1
            self._record({'event':'model.request','mission_id':cap['mission_id'],'generation':cap['generation'],'body_hash':_hash(encode(value['body']).decode())})
        raw=self.forward(value['body'])
        if not isinstance(raw,bytes) or len(raw)>4*1024*1024:raise _error('remote.frame')
        return {'data':base64.b64encode(raw).decode('ascii')}

    def _forward(self,body):
        def no_redirect(_):raise _error('remote.capability')
        with ipv4_preferred_get(self.upstream+'/chat/completions',method='POST',data=encode(body),
                headers={'Content-Type':'application/json','Authorization':'Bearer '+self.api_key},timeout=60,url_validator=no_redirect) as response:
            raw=response.read(4*1024*1024+1)
            if response.status!=200 or len(raw)>4*1024*1024:raise _error('remote.operation')
            return raw

    async def close(self):
        with self.lock:
            self.closed=True;self.capabilities.clear()
            if self.audit_stream is not None:self.audit_stream.close()


class SecretBroker(ModelBroker):
    """容器模式在共享能力服务外持有所属 Unix Socket。"""
    def __init__(self,path,*,upstream,api_key,models,lease_valid,forward=None,audit_path=None):
        self.path=Path(path)
        if self.path.exists() or self.path.is_symlink():raise _error('conflict')
        if not hasattr(socketserver,'ThreadingUnixStreamServer'):raise _error('remote.sandbox-unavailable')
        super().__init__(upstream=upstream,api_key=api_key,models=models,lease_valid=lease_valid,forward=forward,audit_path=audit_path)
        outer=self
        class Handler(StreamRequestHandler):
            def handle(self):
                self.request.settimeout(65)
                raw=self.rfile.readline(MAX_FRAME+1)
                try:result=outer.request(decode(raw))
                except Exception as exc:result={'error':getattr(exc,'code','broker/operation')}
                self.wfile.write(encode(result)+b'\n')
        class Server(socketserver.ThreadingUnixStreamServer):
            daemon_threads=False
            block_on_close=True
        self.closed=False;self._close_task=None;self.server=None;self.identity=None
        try:
            self.server=Server(str(self.path),Handler);self.identity=self.path.stat();os.chmod(self.path,0o600)
            self.thread=threading.Thread(target=self.server.serve_forever,name='superagent-secret-broker',daemon=False);self.thread.start()
        except BaseException:
            if self.server is not None:self.server.server_close()
            if self.identity is not None and self.path.exists() and self.path.stat().st_ino==self.identity.st_ino:self.path.unlink()
            if self.audit_stream is not None:self.audit_stream.close()
            raise

    async def close(self):
        if self._close_task is None:self._close_task=asyncio.create_task(self._close())
        await join_cleanup(self._close_task)

    async def _close(self):
        with self.lock:self.closed=True;self.capabilities.clear()
        await asyncio.to_thread(self.server.shutdown)
        await asyncio.to_thread(self.server.server_close)
        await asyncio.to_thread(self.thread.join)
        # 只移除本次创建且身份未被替换的临时套接字。
        if self.path.exists() and self.path.stat().st_ino==self.identity.st_ino:self.path.unlink()
        if self.audit_stream is not None:self.audit_stream.close()


class BrokerTransport(Transport):
    def __init__(self,path):self.path=str(path)
    async def post_stream(self,url,*,headers,body,timeout):
        token=BROKER_SCOPE.get()
        def invoke():
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                client.settimeout(min(float(timeout),65));client.connect(self.path)
                stream=client.makefile('rwb');stream.write(encode({'token':token,'body':decode(body)})+b'\n');stream.flush()
                value=decode(stream.readline(MAX_FRAME+1))
            if set(value)!={'data'}:raise _error('remote.capability')
            return base64.b64decode(value['data'],validate=True)
        raw=await join_cleanup(asyncio.create_task(asyncio.to_thread(invoke)))
        yield raw


class NativeBrokerTransport(Transport):
    """可信宿主内复用同一短期能力服务，不创建本机网络监听或 Unix Socket。"""
    def __init__(self,broker):self.broker=broker
    async def post_stream(self,url,*,headers,body,timeout):
        value={'token':BROKER_SCOPE.get(),'body':decode(body)}
        result=await join_cleanup(asyncio.create_task(asyncio.to_thread(self.broker.request,value)))
        yield base64.b64decode(result['data'],validate=True)
