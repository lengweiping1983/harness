"""加密飞书事件、原消息回复及未知写入的只读回查。"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from urllib.parse import urlencode

from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..core.errors import HarnessError
from ..core.sqlite import prepare_database
from .superagent_http import JSONClient, WebhookServer, api_root
from .superagent_inbox import _error, _json, _names, _text


SCHEMA = '''
CREATE TABLE channels (instance TEXT PRIMARY KEY, identity TEXT NOT NULL);
CREATE TABLE origins (
    instance TEXT NOT NULL, event_id TEXT NOT NULL, delivery_id TEXT NOT NULL,
    normalized TEXT NOT NULL, target TEXT NOT NULL, fingerprint TEXT NOT NULL, body_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('verified','accepted')), PRIMARY KEY(instance,event_id));
CREATE TABLE outbound (
    seq INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, instance TEXT NOT NULL,
    event_id TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL, request_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','sending','sent','unknown','suppressed')),
    remote_id TEXT, next_check REAL NOT NULL DEFAULT 0);
CREATE TABLE cursors (instance TEXT PRIMARY KEY, after INTEGER NOT NULL, scan INTEGER NOT NULL);
PRAGMA user_version=1;
'''


def identifier(value, prefix):
    if not isinstance(value, str) or not re.fullmatch(re.escape(prefix) + r'[A-Za-z0-9_-]{1,128}', value):
        raise _error('feishu-request')
    return value


def validate_feishu(value, sources):
    required = {'source_id','app_id','tenant_key','bot_open_id','chats','encrypt_key_env',
                'verification_token_env','app_secret_env','api_url','webhook_port','reply_mode'}
    if not isinstance(value, dict) or set(value) != required or value.get('source_id') not in sources:
        raise _error('invalid', field='feishu')
    value = dict(value)
    identifier(value['app_id'], 'cli_')
    identifier(value['bot_open_id'], 'ou_')
    _text(value['tenant_key'], 'tenant_key', limit=128)
    value['chats'] = _names(value['chats'], 'chats', maximum=256, nonempty=True)
    for chat in value['chats']:
        identifier(chat, 'oc_')
    for key in ('encrypt_key_env','verification_token_env','app_secret_env'):
        if not isinstance(value[key], str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,127}', value[key]):
            raise _error('invalid', field=key)
    if (type(value['webhook_port']) is not int or not 0 <= value['webhook_port'] <= 65535
            or value['reply_mode'] not in ('disabled','requested')):
        raise _error('invalid', field='feishu')
    value['api_url'] = api_root(value['api_url'])
    return value


def feishu_credentials(config):
    """启动前加载可选加密依赖和显式环境变量，不读取个人连接器。"""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        raise _error('feishu-dependency') from None
    keys = ('encrypt_key_env','verification_token_env') + (('app_secret_env',) if config['reply_mode'] == 'requested' else ())
    values = [os.environ.get(config[key], '') for key in keys]
    try:
        valid = all(16 <= len(value.encode('utf-8')) <= 4096 and '\0' not in value for value in values)
    except UnicodeError:
        valid = False
    if not valid:
        raise _error('feishu-credentials')
    return {'encrypt_key':values[0], 'verification_token':values[1],
            'app_secret':values[2] if len(values) == 3 else None, 'cipher':(Cipher, algorithms, modes)}


class FeishuClient(JSONClient):
    """自建应用的租户令牌只保留在内存，所有已接受请求归所属线程收尾。"""

    def __init__(self, config, secret):
        super().__init__(config['api_url'], prefix='feishu')
        self.app_id, self.secret = config['app_id'], secret
        self.token, self.expires, self.refresh = None, 0, None

    async def _refresh(self):
        result = await super().request('POST', '/open-apis/auth/v3/tenant_access_token/internal',
                                       {'app_id':self.app_id, 'app_secret':self.secret})
        if (not isinstance(result, dict) or type(result.get('code')) is not int or result['code'] != 0
                or type(result.get('expire')) is not int or not 1 <= result['expire'] <= 86400
                or not isinstance(result.get('tenant_access_token'), str)
                or not re.fullmatch('[!-~]{1,4096}', result['tenant_access_token'])):
            raise _error('feishu-response')
        self.token = result['tenant_access_token']
        self.expires = time.monotonic() + result['expire'] - min(60, result['expire'] / 10)

    async def request(self, method, path, data=None, *, before_request=None):
        if time.monotonic() >= self.expires:
            if self.refresh is None or self.refresh.done():
                self.refresh = asyncio.create_task(self._refresh())
            await join_cleanup(self.refresh)
        value = await super().request(method, path, data, token=self.token, before_request=before_request)
        if not isinstance(value, dict) or type(value.get('code')) is not int:
            raise _error('feishu-response')
        if value['code'] != 0:
            raise _error('feishu-api', status=value['code'])
        return value


class SuperagentFeishu:
    """队列先落盘再确认；固定来源、聊天和原消息限定外部回复目标。"""

    def __init__(self, service, config, credentials):
        self.inbox, self.results, self.config = service.inbox, service.results, dict(config)
        self.credentials, self.closed = credentials, False
        self.path = service.root / 'feishu.sqlite3'
        self.client = FeishuClient(config, credentials['app_secret']) if config['reply_mode'] == 'requested' else None
        self.server = WebhookServer(self, config['webhook_port'], service.spec['language'],
                                    path='/feishu', prefix='feishu', success_status=200)
        with self._db(initialize=True, write=True) as db:
            identity = json.dumps(config, sort_keys=True)
            old = db.execute('SELECT identity FROM channels WHERE instance=?', (self.inbox.instance_id,)).fetchone()
            if old is None:
                db.execute('INSERT INTO channels VALUES(?,?)', (self.inbox.instance_id, identity))
                db.execute('INSERT INTO cursors VALUES(?,0,0)', (self.inbox.instance_id,))
            elif old[0] != identity:
                raise _error('conflict')

    @contextmanager
    def _db(self, *, initialize=False, write=False):
        db = sqlite3.connect(self.path, timeout=1)
        db.row_factory = sqlite3.Row
        try:
            with db:
                if write:
                    db.execute('BEGIN IMMEDIATE')
                prepare_database(db, SCHEMA, initialize=initialize)
                yield db
        finally:
            db.close()

    def _decode(self, headers, raw):
        """普通事件先验签再解密；平台地址验证仅允许令牌核验后的 challenge。"""
        try:
            if not isinstance(raw, bytes) or not 1 <= len(raw) <= 1048576:
                raise ValueError
            timestamp, nonce, signature = [headers.get(key) for key in
                ('x-lark-request-timestamp','x-lark-request-nonce','x-lark-signature')]
            signed = any(value is not None for value in (timestamp, nonce, signature))
            if signed:
                if (not isinstance(timestamp, str) or not re.fullmatch('[0-9]{1,16}', timestamp)
                        or not isinstance(nonce, str) or not 1 <= len(nonce.encode('utf-8')) <= 256
                        or not isinstance(signature, str) or not re.fullmatch('[0-9a-f]{64}', signature)
                        or not -300 <= time.time() - int(timestamp) <= 28800):
                    raise ValueError
                expected = hashlib.sha256((timestamp + nonce + self.credentials['encrypt_key']).encode('utf-8') + raw).hexdigest()
                if not hmac.compare_digest(expected, signature):
                    raise ValueError
            envelope = _json(raw.decode('utf-8'))
            if not isinstance(envelope, dict) or set(envelope) != {'encrypt'} or not isinstance(envelope['encrypt'], str):
                raise ValueError
            encrypted = base64.b64decode(envelope['encrypt'], validate=True)
            if len(encrypted) < 32 or len(encrypted) % 16:
                raise ValueError
            Cipher, algorithms, modes = self.credentials['cipher']
            decoder = Cipher(algorithms.AES(hashlib.sha256(self.credentials['encrypt_key'].encode('utf-8')).digest()),
                             modes.CBC(encrypted[:16])).decryptor()
            plain = decoder.update(encrypted[16:]) + decoder.finalize()
            padding = plain[-1]
            if not 1 <= padding <= 16 or plain[-padding:] != bytes([padding]) * padding:
                raise ValueError
            value = _json(plain[:-padding].decode('utf-8'))
            if not isinstance(value, dict):
                raise ValueError
            challenge = value.get('type') == 'url_verification'
            token = value.get('token') if challenge else value.get('header', {}).get('token')
            if (not isinstance(token, str) or not hmac.compare_digest(token.encode('utf-8'),
                    self.credentials['verification_token'].encode('utf-8')) or not challenge and not signed):
                raise ValueError
            if challenge:
                return {'challenge':_text(value.get('challenge'), 'challenge', limit=1024)}
            return value
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, HarnessError):
            raise _error('feishu-signature') from None

    def receive(self, headers, raw, *, queued=False):
        if self.closed:
            raise _error('closed')
        data = self._decode(headers, raw)
        if set(data) == {'challenge'}:
            return data
        try:
            if data.get('schema') != '2.0':
                raise ValueError
            header = data['header']
            if header['app_id'] != self.config['app_id'] or header['tenant_key'] != self.config['tenant_key']:
                raise _error('feishu-scope')
            if header['event_type'] != 'im.message.receive_v1':
                return {'ignored':True}
            delivery = _text(header['event_id'], 'delivery_id')
            message, sender = data['event']['message'], data['event']['sender']
            if sender['tenant_key'] != self.config['tenant_key'] or message['chat_id'] not in self.config['chats']:
                raise _error('feishu-scope')
            if message['message_type'] != 'text':
                return {'ignored':True}
            if message['chat_type'] not in ('group','p2p') or sender['sender_type'] not in ('user','app'):
                raise ValueError
            chat = identifier(message['chat_id'], 'oc_')
            message_id = identifier(message['message_id'], 'om_')
            root_value = message.get('root_id')
            root = identifier(message_id if root_value in (None, '') else root_value, 'om_')
            actor = identifier(sender['sender_id']['open_id'], 'ou_')
            content = _json(_text(message['content'], 'content', limit=524288))
            text = _text(content['text'], 'text', limit=65536, empty=True)
            human = sender['sender_type'] == 'user' and actor != self.config['bot_open_id']
            mentions = message.get('mentions', [])
            if not isinstance(mentions, list) or len(mentions) > 64:
                raise ValueError
            first = next((line.strip() for line in text.splitlines() if line.strip()), '')
            mentioned = False
            for mention in mentions:
                key = _text(mention['key'], 'mention', limit=64)
                if (mention['id'].get('open_id') == self.config['bot_open_id']
                        and re.fullmatch(r'@_user_[0-9]+', key)
                        and re.match(re.escape(key) + r'(?=$|[\s,:])', first)):
                    mentioned = True
            event = {'source_id':self.config['source_id'], 'event_id':'feishu:message:' + message_id,
                'actor_id':'feishu:' + sender['sender_type'] + ':' + actor, 'text':text,
                'thread_ids':['chat/' + chat + '/root/' + root], 'human':human,
                'explicit':human and (message['chat_type'] == 'p2p' or mentioned)}
            target = {'chat_id':chat, 'message_id':message_id, 'root_id':root}
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError) as exc:
            raise _error('feishu-request') from exc
        normalized, address = json.dumps(event, sort_keys=True, ensure_ascii=False), json.dumps(target, sort_keys=True)
        fingerprint = hashlib.sha256((normalized + '\n' + address).encode('utf-8')).hexdigest()
        with self._db(write=True) as db:
            old = db.execute('SELECT * FROM origins WHERE instance=? AND event_id=?',
                             (self.inbox.instance_id, event['event_id'])).fetchone()
            if old is None:
                if db.execute("SELECT count(*) FROM origins WHERE instance=? AND state='verified'", (self.inbox.instance_id,)).fetchone()[0] >= 1024:
                    raise _error('feishu-capacity')
                db.execute("INSERT INTO origins VALUES(?,?,?,?,?,?,?,'verified')", (self.inbox.instance_id,
                    event['event_id'], delivery, normalized, address, fingerprint, hashlib.sha256(raw).hexdigest()))
            elif old['normalized'] != normalized or old['target'] != address or old['fingerprint'] != fingerprint:
                raise _error('event-conflict')
        if queued:
            return {'event_id':event['event_id'], 'state':old['state'] if old is not None else 'verified'}
        receipt = self.inbox.receive(**event)
        with self._db(write=True) as db:
            db.execute("UPDATE origins SET state='accepted' WHERE instance=? AND event_id=?", (self.inbox.instance_id, event['event_id']))
        return receipt

    def _origin(self, row):
        try:
            event, target = _json(row['normalized']), _json(row['target'])
            if (not isinstance(event, dict) or set(event) != {'source_id','event_id','actor_id','text','thread_ids','human','explicit'}
                    or not isinstance(target, dict) or set(target) != {'chat_id','message_id','root_id'}
                    or event['source_id'] != self.config['source_id'] or event['event_id'] != row['event_id']
                    or target['chat_id'] not in self.config['chats']
                    or event['event_id'] != 'feishu:message:' + identifier(target['message_id'], 'om_')
                    or event['thread_ids'] != ['chat/' + identifier(target['chat_id'], 'oc_') + '/root/' + identifier(target['root_id'], 'om_')]
                    or row['fingerprint'] != hashlib.sha256((row['normalized'] + '\n' + row['target']).encode('utf-8')).hexdigest()):
                raise ValueError
            return event, target
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, HarnessError) as exc:
            raise _error('corrupt') from exc

    def drain(self):
        if self.closed:
            return
        with self._db() as db:
            rows = db.execute("SELECT * FROM origins WHERE instance=? AND state='verified' ORDER BY rowid LIMIT 16",
                              (self.inbox.instance_id,)).fetchall()
        for row in rows:
            event, _ = self._origin(row)
            self.inbox.receive(**event)
            with self._db(write=True) as db:
                db.execute("UPDATE origins SET state='accepted' WHERE instance=? AND event_id=?", (self.inbox.instance_id, row['event_id']))

    def _event(self, event_id):
        with self._db() as db:
            row = db.execute("SELECT * FROM origins WHERE instance=? AND event_id=? AND state='accepted'",
                             (self.inbox.instance_id, event_id)).fetchone()
        if row is None:
            return None
        original, target = self._origin(row)
        event = self.inbox.event(source_id=self.config['source_id'], event_id=event_id)
        if any(event.get(key) != value for key, value in original.items()):
            raise _error('corrupt')
        return event, target

    def _allowed(self, event, revision):
        source = self.inbox.source(event['source_id'])
        return (event['disposition'] == 'accepted' and event['human']
            and (event['explicit'] or any(route['kind'] == 'worker' and not route['context_only'] for route in event['routes']))
            and source['revision'] == revision and source['mode'] != 'ignore'
            and event['actor_id'] in source['actors'] and all(thread in source['threads'] for thread in event['thread_ids']))

    @staticmethod
    def _body(report):
        return report['reply'] + '\n\n[ref:' + report['id'] + ']'

    @staticmethod
    def _request_id(report_id):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, 'harness-superagent:' + report_id))

    def _collect(self):
        with self._db(write=True) as db:
            cursor = db.execute('SELECT after FROM cursors WHERE instance=?', (self.inbox.instance_id,)).fetchone()[0]
            drafts = self.results.drafts(after=cursor, limit=16)
            for draft in drafts:
                origin = self._event(draft['event_id']) if draft['source_id'] == self.config['source_id'] else None
                if origin is None:
                    continue
                revision = self.results._get(draft['id'])['revision']
                if self._allowed(origin[0], revision):
                    body = self._body(draft)
                    if len(re.findall(r'[\u3400-\u9fff]|[^\s\u3400-\u9fff]+', body)) > 100:
                        raise _error('feishu-response')
                    db.execute("""INSERT OR IGNORE INTO outbound(id,instance,event_id,revision,body,request_id,state)
                        VALUES(?,?,?,?,?,?,'pending')""", (draft['id'], self.inbox.instance_id, draft['event_id'], revision,
                        body, self._request_id(draft['id'])))
            if drafts:
                db.execute('UPDATE cursors SET after=? WHERE instance=?', (drafts[-1]['seq'], self.inbox.instance_id))

    def _get(self, report_id):
        with self._db() as db:
            row = db.execute('SELECT * FROM outbound WHERE instance=? AND id=?', (self.inbox.instance_id, report_id)).fetchone()
        if row is None:
            raise _error('event-unavailable')
        report, origin = self.results._get(report_id), self._event(row['event_id'])
        if (origin is None or report['state'] != 'draft' or report['source_id'] != self.config['source_id']
                or report['event_id'] != row['event_id'] or report['revision'] != row['revision']
                or row['body'] != self._body(report) or row['request_id'] != self._request_id(report_id)):
            raise _error('corrupt')
        if row['state'] == 'sent':
            try:
                identifier(row['remote_id'], 'om_')
            except HarnessError as exc:
                raise _error('corrupt') from exc
        return dict(row), *origin

    def _settle(self, row, state, remote_id=None):
        with self._db(write=True) as db:
            old = db.execute('SELECT state,remote_id FROM outbound WHERE id=?', (row['id'],)).fetchone()
            if old['state'] == 'sent':
                if state in ('unknown','suppressed'):
                    return
                if state != 'sent' or old['remote_id'] != remote_id:
                    raise _error('conflict')
                return
            previous = ('pending',) if state == 'suppressed' else ('sending','unknown')
            db.execute('UPDATE outbound SET state=?,remote_id=?,next_check=? WHERE id=? AND state IN ('
                + ','.join('?' for _ in previous) + ')',
                (state, remote_id, time.time() + 30 if state == 'unknown' else 0, row['id'], *previous))

    def _message(self, value, target, *, body=None):
        try:
            root = value.get('root_id')
            if (not isinstance(value, dict) or value['chat_id'] != target['chat_id'] or value.get('deleted', False) is not False
                    or value['msg_type'] != 'text'
                    or identifier(value['message_id'] if root in (None, '') else root, 'om_') != target['root_id']):
                raise ValueError
            remote = identifier(value['message_id'], 'om_')
            if body is None:
                if remote != target['message_id']:
                    raise ValueError
            else:
                sender = value['sender']
                if (value['parent_id'] != target['message_id'] or _json(value['body']['content']) != {'text':body}
                        or sender['sender_type'] != 'app' or sender['id_type'] != 'app_id'
                        or sender['id'] != self.config['app_id'] or sender['tenant_key'] != self.config['tenant_key']):
                    raise ValueError
            return remote
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError, HarnessError) as exc:
            raise _error('feishu-response') from exc

    async def _reconcile(self, row, target):
        matches, token, seen, complete = set(), None, set(), False
        for _ in range(10):
            query = {'container_id_type':'chat', 'container_id':target['chat_id'], 'page_size':'50',
                     'sort_type':'ByCreateTimeDesc', 'only_thread_root_messages':'false'}
            if token:
                query['page_token'] = token
            value = await self.client.request('GET', '/open-apis/im/v1/messages?' + urlencode(query))
            data = value.get('data')
            if (not isinstance(data, dict) or not isinstance(data.get('items', []), list)
                    or len(data.get('items', [])) > 50 or type(data.get('has_more')) is not bool):
                raise _error('feishu-response')
            for item in data.get('items', []):
                if (isinstance(item, dict) and item.get('parent_id') == target['message_id']
                        and isinstance(item.get('body'), dict) and isinstance(item.get('sender'), dict)
                        and item['sender'].get('sender_type') == 'app' and item['sender'].get('id') == self.config['app_id']
                        and item['sender'].get('tenant_key') == self.config['tenant_key']):
                    try:
                        same = _json(item['body'].get('content')) == {'text':row['body']}
                    except (ValueError, TypeError, RecursionError):
                        same = False
                    if same:
                        matches.add(self._message(item, target, body=row['body']))
            if not data['has_more']:
                complete = True
                break
            token = _text(data.get('page_token'), 'page_token', limit=4096)
            if token in seen:
                raise _error('feishu-response')
            seen.add(token)
        if len(matches) > 1:
            raise _error('conflict')
        confirmed = complete and len(matches) == 1
        self._settle(row, 'sent' if confirmed else 'unknown', next(iter(matches)) if confirmed else None)

    async def tick(self):
        if self.closed or self.client is None:
            return
        self._collect()
        with self._db(write=True) as db:
            scan = db.execute('SELECT scan FROM cursors WHERE instance=?', (self.inbox.instance_id,)).fetchone()[0]
            query = "SELECT id,seq FROM outbound WHERE instance=? AND state IN ('pending','sending','unknown') AND next_check<=?"
            rows = db.execute(query + ' AND seq>? ORDER BY seq LIMIT 1', (self.inbox.instance_id, time.time(), scan)).fetchall()
            if not rows:
                rows = db.execute(query + ' ORDER BY seq LIMIT 1', (self.inbox.instance_id, time.time())).fetchall()
            if rows:
                db.execute('UPDATE cursors SET scan=? WHERE instance=?', (rows[0]['seq'], self.inbox.instance_id))
        for candidate in rows:
            row, event, target = self._get(candidate['id'])
            if row['state'] != 'pending':
                await self._reconcile(row, target)
                continue
            if not self._allowed(event, row['revision']):
                self._settle(row, 'suppressed')
                continue
            bot = await self.client.request('GET', '/open-apis/bot/v3/info/')
            if not isinstance(bot.get('bot'), dict) or bot['bot'].get('open_id') != self.config['bot_open_id']:
                raise _error('feishu-scope')
            if self.closed:
                return
            original = await self.client.request('GET', '/open-apis/im/v1/messages/' + target['message_id'] + '?user_id_type=open_id')
            data = original.get('data')
            if not isinstance(data, dict) or not isinstance(data.get('items'), list) or len(data['items']) != 1:
                raise _error('feishu-response')
            self._message(data['items'][0], target)
            if self.closed:
                return
            if not self._allowed(event, row['revision']):
                self._settle(row, 'suppressed')
                continue
            with self._db(write=True) as db:
                claimed = db.execute("UPDATE outbound SET state='sending' WHERE id=? AND state='pending'", (row['id'],)).rowcount
            if not claimed:
                continue
            try:
                value = await self.client.request('POST', '/open-apis/im/v1/messages/' + target['message_id'] + '/reply',
                    {'msg_type':'text', 'content':json.dumps({'text':row['body']}), 'reply_in_thread':True, 'uuid':row['request_id']})
                self._settle(row, 'sent', self._message(value.get('data'), target, body=row['body']))
            except BaseException as original:
                errors = [original]
                try:
                    self._settle(row, 'unknown')
                    await self._reconcile(row, target)
                except BaseException as recovery:
                    if recovery is not original:
                        errors.append(recovery)
                raise_cleanup_errors(errors)

    def stop(self):
        self.closed = True
        self.server.stop()

    def status(self):
        with self._db() as db:
            counts = dict(db.execute('SELECT state,count(*) FROM outbound WHERE instance=? GROUP BY state', (self.inbox.instance_id,)))
            queued = db.execute("SELECT count(*) FROM origins WHERE instance=? AND state='verified'", (self.inbox.instance_id,)).fetchone()[0]
        return {'webhook_port':self.server.port, 'reply_mode':self.config['reply_mode'], 'queued':queued, 'outbound':counts}
