"""原消息资源的显式有界获取、不可变保存和按来源复核的本机读取。"""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import tempfile
from urllib.parse import quote

from PIL import Image

from .superagent_common import _error, _text, _json as strict_loads
from .superagent_coordinator_store import _hash, _now
from .superagent_bootstrap import provider_configuration
from .superagent_source_registry import SourceRegistry
from .superagent_media_content import feishu_content


class SourceMedia:
    def __init__(self,store,spec,*,clients=None):
        self.store,self.spec,self.clients = store,spec,clients
        self.root = Path(spec['state_dir'])/'media'

    def _source(self,trigger_id,resource_key):
        _text(trigger_id,'trigger_id'); _text(resource_key,'resource_key')
        with self.store.transaction() as db:
            trigger = db.execute('SELECT * FROM triggers WHERE trigger_id=?',(trigger_id,)).fetchone()
            if trigger is None or trigger['verification_state']!='verified':
                raise _error('source-unavailable')
            row = db.execute('SELECT * FROM source_instances WHERE source_instance_id=?',(trigger['source_instance_id'],)).fetchone()
            thread = db.execute('SELECT metadata_json FROM threads WHERE thread_id=?',(trigger['primary_thread_id'],)).fetchone()
        source = SourceRegistry._view(row,installed=True)
        body,metadata = strict_loads(trigger['body_json']),strict_loads(trigger['metadata_json'])
        if _hash(trigger['body_json'])!=trigger['body_hash'] or _hash(trigger['metadata_json'])!=trigger['meta_hash']:
            raise _error('corrupt')
        if source.kind!='feishu' or not source.enabled or metadata.get('chat_id') not in source.config['chats']:
            raise _error('source-unavailable')
        resources = [item for item in body.get('attachments',[]) if item.get('resource_key')==resource_key]
        if len(resources)!=1:
            raise _error('media.scope')
        return source,metadata,strict_loads(thread['metadata_json']),resources[0]

    def _path(self,media_id):
        if not media_id.startswith('media_') or len(media_id)!=70 or any(c not in '0123456789abcdef' for c in media_id[6:]):
            raise _error('invalid',field='media_id')
        if self.root.is_symlink() or self.root.is_junction():
            raise _error('conflict')
        return self.root/media_id

    def cached(self,media_id):
        path = self._path(media_id)
        with self.store.transaction() as db:
            row = db.execute('SELECT * FROM source_media WHERE media_id=?',(media_id,)).fetchone()
        if row is None:
            raise _error('query.missing')
        _,_,_,resource = self._source(row['trigger_id'],row['resource_key'])
        if path.is_symlink() or not path.is_file():
            raise _error('corrupt')
        with path.open('rb') as stream:
            raw = stream.read(33554433)
        if len(raw)!=row['byte_size'] or hashlib.sha256(raw).hexdigest()!=row['digest']:
            raise _error('corrupt')
        return dict(row),raw,resource

    async def fetch(self,trigger_id,resource_key):
        source,metadata,thread,resource = self._source(trigger_id,resource_key)
        media_id = 'media_'+_hash(trigger_id+'\0'+resource_key)
        with self.store.transaction() as db:
            prior = db.execute('SELECT media_id FROM source_media WHERE media_id=?',(media_id,)).fetchone()
        if prior:
            return self.cached(media_id)[0]
        clients = self.clients if self.clients is not None else provider_configuration(self.spec,self.store)[0]
        client = clients.get(source.source_instance_id)
        if client is None:
            raise _error('credentials-required')
        bot = await client.request('GET','/open-apis/bot/v3/info/')
        if not isinstance(bot,dict) or (bot.get('bot') or {}).get('open_id')!=source.config['bot_open_id']:
            raise _error('media.scope')
        message_id = metadata['message_id']
        original = await client.request('GET','/open-apis/im/v1/messages/'+quote(message_id,safe='')+'?user_id_type=open_id')
        items = (original.get('data') or {}).get('items') if isinstance(original,dict) else None
        if not isinstance(items,list) or len(items)!=1:
            raise _error('media.scope')
        item = items[0]
        if (item.get('message_id')!=message_id or item.get('chat_id')!=metadata['chat_id'] or item.get('deleted') is True
                or (item.get('root_id') or item.get('message_id'))!=thread['root_id']):
            raise _error('media.scope')
        confirmed,_ = feishu_content(item.get('msg_type'),strict_loads(item['body']['content']))
        if not any(entry['resource_key']==resource_key for entry in confirmed.get('attachments',[])):
            raise _error('media.scope')
        def guard():
            current,_,_,_ = self._source(trigger_id,resource_key)
            if current.config_revision!=source.config_revision:
                raise _error('media.scope')
        wire_type = 'image' if resource['type']=='image' else 'file'
        value = await client.request_bytes('/open-apis/im/v1/messages/'+quote(message_id,safe='')+'/resources/'+quote(resource_key,safe='')+'?type='+wire_type,
            before_request=guard)
        raw = value['data']
        if not isinstance(raw,bytes) or not 0<len(raw)<=33554432:
            raise _error('media.invalid')
        content_type = 'application/octet-stream'
        if resource['type']=='image':
            try:
                with Image.open(io.BytesIO(raw)) as image:
                    if image.width*image.height>25000000 or getattr(image,'n_frames',1)>200:
                        raise ValueError
                    content_type = {'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp','GIF':'image/gif'}[image.format]
                    image.verify()
            except (OSError,ValueError,KeyError,Image.DecompressionBombError) as exc:
                raise _error('media.invalid') from exc
        elif value.get('content_type')=='application/json':
            error = strict_loads(raw.decode('utf-8'))
            if isinstance(error,dict) and type(error.get('code')) is int and error['code']!=0:
                raise _error('feishu-api',status=error['code'])
        guard()
        path = self._path(media_id)
        self.root.mkdir(parents=True,exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root,delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            guard()
            self._path(media_id)
            try:
                os.link(temporary,path)
            except FileExistsError:
                if path.is_symlink() or not path.is_file():
                    raise _error('conflict')
                with path.open('rb') as existing:
                    if existing.read(33554433)!=raw:
                        raise _error('conflict')
        finally:
            if temporary is not None:
                temporary.unlink()
        with self.store.transaction(write=True) as db:
            guard()
            db.execute('INSERT OR IGNORE INTO source_media VALUES(?,?,?,?,?,?,?,?,?)',
                (media_id,trigger_id,source.source_instance_id,resource_key,resource['type'],hashlib.sha256(raw).hexdigest(),len(raw),content_type,_now()))
        return self.cached(media_id)[0]
