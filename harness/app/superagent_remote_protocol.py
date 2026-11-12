"""远程 Executor 的固定机器协议、身份、签名和有界帧。"""
from __future__ import annotations

import hashlib
import hmac
import re
import time
import uuid
from urllib.parse import urlsplit

from .superagent_common import _error, _json as loads, _text
from .superagent_coordinator_store import _hash, _json
from .superagent_http import api_root

MAX_FRAME = 8 * 1024 * 1024
READS = frozenset({'identity','find_worker','reconcile','inspect','snapshot_workspace','artifact_manifest',
    'reconcile_input','reconcile_instruction','instruction_turn','operation_receipt','heartbeat'})
WRITES = frozenset({'create_workspace','create_session','submit','capture_workspace','plan_artifact_delivery',
    'apply_artifact_delivery','deliver_input','deliver_instruction','stop','revoke'})
OPERATIONS = READS | WRITES


def encode(value):
    raw = _json(value).encode('utf-8')
    if len(raw)>MAX_FRAME: raise _error('remote.frame')
    return raw


def decode(raw):
    if not isinstance(raw,bytes) or not 0<len(raw)<=MAX_FRAME: raise _error('remote.frame')
    try:
        value=loads(raw.decode('utf-8'))
        if not isinstance(value,dict): raise ValueError
        return value
    except (ValueError,UnicodeError,RecursionError) as exc: raise _error('remote.frame') from exc


def signature(key, raw):
    return hmac.new(key,raw,hashlib.sha256).hexdigest()


def frame(*,instance_id,device_id,identity_hash,operation,params,lease=None,operation_id=None):
    if operation not in OPERATIONS or not isinstance(params,dict): raise _error('remote.frame')
    return {'version':1,'instance_id':instance_id,'device_id':device_id,'identity_hash':identity_hash,
        'operation':operation,'operation_id':operation_id or uuid.uuid4().hex,'params':params,
        'lease':lease,'nonce':uuid.uuid4().hex,'issued_at':int(time.time())}


def validate(value, *, instance_id, device_id, identity_hash):
    fields={'version','instance_id','device_id','identity_hash','operation','operation_id','params','lease','nonce','issued_at'}
    if (set(value)!=fields or type(value['version']) is not int or value['version']!=1
        or value['instance_id']!=instance_id or value['device_id']!=device_id or value['identity_hash']!=identity_hash
        or not isinstance(value['operation'],str) or value['operation'] not in OPERATIONS
        or not isinstance(value['params'],dict) or type(value['issued_at']) is not int or abs(time.time()-value['issued_at'])>60):
        raise _error('remote.identity')
    _text(value['operation_id'],'operation_id',limit=256)
    if not isinstance(value['nonce'],str) or not re.fullmatch('[0-9a-f]{32}',value['nonce']): raise _error('remote.identity')
    if value['operation'] not in {'identity','operation_receipt'}:
        lease=value['lease']
        if (not isinstance(lease,dict) or set(lease)!={'mission_id','device_id','generation','expires_at'}
            or lease['device_id']!=device_id or type(lease['generation']) is not int or lease['generation']<1
            or type(lease['expires_at']) is not int): raise _error('remote.lease')
        _text(lease['mission_id'],'mission_id')
    return value


def execution_config(value, secrets):
    if not isinstance(value,dict) or set(value)!={'devices','lease_seconds'}: raise _error('invalid',field='execution')
    if type(value['lease_seconds']) is not int or not 15<=value['lease_seconds']<=300: raise _error('invalid',field='lease_seconds')
    if not isinstance(value['devices'],list) or not 1<=len(value['devices'])<=32: raise _error('invalid',field='devices')
    seen=set();keys=set();endpoints=set()
    for device in value['devices']:
        if (not isinstance(device,dict) or set(device)-{'device_id','url','key_ref','capacity','runner'}
            or not {'device_id','url','key_ref','capacity'}<=set(device)
            or device.get('runner','docker') not in ('native','docker')): raise _error('invalid',field='device')
        _text(device['device_id'],'device_id',limit=128)
        if device['device_id'] in seen: raise _error('invalid',field='device_id')
        seen.add(device['device_id']); device['url']=api_root(device['url'])
        if device['key_ref'] in keys or device['url'] in endpoints:raise _error('invalid',field='device.identity')
        keys.add(device['key_ref']);endpoints.add(device['url'])
        if urlsplit(device['url']).path: raise _error('invalid',field='device.url')
        if device['key_ref'] not in {'secret://'+key for key in secrets}: raise _error('invalid',field='device.key_ref')
        if type(device['capacity']) is not int or not 1<=device['capacity']<=256: raise _error('invalid',field='device.capacity')
    return value


def operation_key(operation,worker,params):
    """写入身份由原生资源和冻结参数导出；绝不因连接重建而更换身份。"""
    return 'remote_'+_hash(_json([operation,worker['worker_id'],params]))


def worker_openapi():
    """远程网关的独立公共协议产物，字段和操作枚举来自实际 Port 源。"""
    text={'type':'string'}
    lease={'type':['object','null'],'additionalProperties':False,'properties':{
        'mission_id':text,'device_id':text,'generation':{'type':'integer','minimum':1},'expires_at':{'type':'integer'}},
        'required':['mission_id','device_id','generation','expires_at']}
    request={'type':'object','additionalProperties':False,'properties':{
        'version':{'type':'integer','const':1},'instance_id':text,'device_id':text,'identity_hash':text,
        'operation':{'type':'string','enum':sorted(OPERATIONS)},'operation_id':text,
        'params':{'type':'object','additionalProperties':True},'lease':lease,
        'nonce':{'type':'string','pattern':'^[0-9a-f]{32}$'},'issued_at':{'type':'integer'}},
        'required':['version','instance_id','device_id','identity_hash','operation','operation_id','params','lease','nonce','issued_at']}
    receipt={'type':'object','additionalProperties':False,'properties':{
        'version':{'type':'integer','const':1},'device_id':text,'nonce':text,'operation_id':text,
        'result':{},'error':{'type':['string','null']}},'required':['version','device_id','nonce','operation_id','result','error']}
    identity={'type':'object','additionalProperties':False,'properties':{
        'protocol':{'type':'integer','const':1},'profiles':{'type':'object','additionalProperties':text},
        'runner':{'type':'string','enum':['native','docker']},'sandbox':{'type':'string','enum':['none','docker']},
        'runtime':{'type':'object','additionalProperties':True}},'required':['protocol','profiles','runner','sandbox','runtime']}
    return {'openapi':'3.1.0','info':{'title':'Harness Worker Executor','version':'1'},'paths':{'/executor/v1':{'post':{
        'parameters':[{'in':'header','name':'X-Harness-Signature','required':True,'schema':text}],
        'requestBody':{'required':True,'content':{'application/json':{'schema':{'$ref':'#/components/schemas/WorkerFrame'}}}},
        'responses':{'200':{'description':'Signed receipt','headers':{'X-Harness-Signature':{'schema':text}},
            'content':{'application/json':{'schema':{'$ref':'#/components/schemas/WorkerReceipt'}}}},
            'default':{'description':'Request rejected before admission','content':{'text/html':{'schema':text}}}}}}},
        'components':{'schemas':{'WorkerFrame':request,'WorkerReceipt':receipt,'WorkerIdentity':identity}}}
