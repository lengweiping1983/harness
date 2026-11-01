"""验签的 GitHub 事件与具备精确回查依据的评论发件箱。"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from urllib.parse import urlsplit

from ..core.cleanup import raise_cleanup_errors
from ..core.errors import HarnessError
from ..core.sqlite import prepare_database
from .superagent_github_http import GitHubClient, GitHubWebhookServer, repository_path
from .superagent_inbox import _error, _json, _text


SCHEMA = '''
CREATE TABLE channels (instance TEXT PRIMARY KEY, identity TEXT NOT NULL);
CREATE TABLE origins (
    instance TEXT NOT NULL, event_id TEXT NOT NULL, issue INTEGER NOT NULL,
    normalized TEXT NOT NULL, normalized_hash TEXT NOT NULL, body_hash TEXT NOT NULL, delivery_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('verified','accepted')),
    PRIMARY KEY(instance,event_id));
CREATE TABLE outbound (
    seq INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, instance TEXT NOT NULL,
    event_id TEXT NOT NULL, issue INTEGER NOT NULL, revision INTEGER NOT NULL,
    body TEXT NOT NULL, state TEXT NOT NULL
    CHECK(state IN ('pending','sending','sent','unknown','suppressed')), remote_id INTEGER,
    next_check REAL NOT NULL DEFAULT 0);
CREATE TABLE cursors (instance TEXT PRIMARY KEY, after INTEGER NOT NULL, scan INTEGER NOT NULL);
PRAGMA user_version=1;
'''


def validate_github(value, sources):
    """配置固定仓库和发言账号；消息正文不能选择 API、仓库或权限。"""
    required = {'source_id','repository','repository_id','bot_login','bot_user_id',
                'secret_env','token_env','api_url','webhook_port','reply_mode'}
    if not isinstance(value,dict) or set(value) != required:
        raise _error('invalid',field='github')
    value = dict(value)
    _text(value['source_id'],'source_id')
    if value['source_id'] not in sources:
        raise _error('invalid',field='github.source_id')
    if not isinstance(value['repository'],str) or not re.fullmatch('[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',value['repository']):
        raise _error('invalid',field='repository')
    if any(part in ('.','..') for part in value['repository'].split('/')):
        raise _error('invalid',field='repository')
    if not isinstance(value['bot_login'],str) or not re.fullmatch(r'[A-Za-z0-9_-]+(?:\[bot\])?',value['bot_login']):
        raise _error('invalid',field='bot_login')
    for key in ('repository_id','bot_user_id'):
        if type(value[key]) is not int or not 1 <= value[key] <= 9223372036854775807:
            raise _error('invalid',field=key)
    if type(value['webhook_port']) is not int or not 0 <= value['webhook_port'] <= 65535:
        raise _error('invalid',field='webhook_port')
    if value['reply_mode'] not in ('disabled','requested'):
        raise _error('invalid',field='reply_mode')
    for key in ('secret_env','token_env'):
        if not isinstance(value[key],str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,127}',value[key]):
            raise _error('invalid',field=key)
    url = urlsplit(_text(value['api_url'],'api_url',limit=4096))
    try:
        url.port
    except ValueError as exc:
        raise _error('invalid',field='api_url') from exc
    try:
        loopback = url.hostname == 'localhost' or ipaddress.ip_address(url.hostname).is_loopback
    except ValueError:
        loopback = False
    if (not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment
            or url.scheme != 'https' and not (url.scheme == 'http' and loopback)):
        raise _error('invalid',field='api_url')
    value['api_url'] = value['api_url'].rstrip('/')
    return value


def github_credentials(config):
    secret = os.environ.get(config['secret_env'],'')
    token = os.environ.get(config['token_env'],'') if config['reply_mode'] == 'requested' else ''
    if (not 16 <= len(secret.encode('utf-8')) <= 4096 or '\0' in secret
            or config['reply_mode'] == 'requested' and not re.fullmatch('[!-~]{1,4096}',token)):
        raise _error('github-credentials')
    return secret.encode('utf-8'),token


class SuperagentGitHub:
    """接收与发送各自保留事实；未知写入只回查，不自动重发。"""

    def __init__(self, service, config, secret, token):
        self.service, self.inbox, self.results = service,service.inbox,service.results
        self.config, self.secret = dict(config),secret
        self.closed = False
        self.path = service.root / 'github.sqlite3'
        self.repo_path = repository_path(config['repository'])
        self.client = GitHubClient(config['api_url'],token) if config['reply_mode'] == 'requested' else None
        self.server = GitHubWebhookServer(self,config['webhook_port'],service.spec['language'])
        with self._db(initialize=True,write=True) as db:
            identity = json.dumps(config,sort_keys=True)
            old = db.execute('SELECT identity FROM channels WHERE instance=?',(self.inbox.instance_id,)).fetchone()
            if old is None:
                db.execute('INSERT INTO channels VALUES(?,?)',(self.inbox.instance_id,identity))
                db.execute('INSERT INTO cursors VALUES(?,0,0)',(self.inbox.instance_id,))
            elif old[0] != identity:
                raise _error('conflict')

    @contextmanager
    def _db(self, *, initialize=False,write=False):
        db = sqlite3.connect(self.path,timeout=1)
        db.row_factory = sqlite3.Row
        try:
            with db:
                if write:
                    db.execute('BEGIN IMMEDIATE')
                prepare_database(db,SCHEMA,initialize=initialize)
                yield db
        finally:
            db.close()

    @staticmethod
    def _number(value):
        if type(value) is not int or not 1 <= value <= 9223372036854775807:
            raise _error('github-request')
        return value

    def receive(self, headers, raw, *, queued=False):
        if self.closed:
            raise _error('closed')
        if not isinstance(raw,bytes) or not 1 <= len(raw) <= 1048576:
            raise _error('github-request')
        expected = 'sha256=' + hmac.new(self.secret,raw,hashlib.sha256).hexdigest()
        signature = headers.get('x-hub-signature-256','')
        if (not isinstance(signature,str) or not re.fullmatch('sha256=[0-9a-f]{64}',signature)
                or not hmac.compare_digest(expected,signature)):
            raise _error('github-signature')
        try:
            data = _json(raw.decode('utf-8'))
            kind = headers.get('x-github-event')
            delivery = _text(headers.get('x-github-delivery'),'delivery_id')
            if not isinstance(data,dict):
                raise ValueError
            if kind == 'ping':
                return {'ignored':True}
            if kind not in ('issues','issue_comment'):
                return {'ignored':True}
            if data.get('action') != ('opened' if kind == 'issues' else 'created'):
                return {'ignored':True}
            repo, issue, sender = data['repository'],data['issue'],data['sender']
            if (self._number(repo['id']) != self.config['repository_id']
                    or repo['full_name'].casefold() != self.config['repository'].casefold()):
                raise _error('github-scope')
            entity = data['comment'] if kind == 'issue_comment' else issue
            if kind == 'issues' and 'comment' in data:
                raise ValueError
            author = entity['user']
            actor = self._number(author['id'])
            if (actor != self._number(sender['id']) or author['login'] != sender['login']
                    or author['type'] != sender['type'] or author['type'] not in ('User','Bot')):
                raise ValueError
            number = self._number(issue['number'])
            self._number(issue['id'])
            remote = 'github:' + ('comment:' if kind == 'issue_comment' else 'issue:') + str(self._number(entity['id']))
            text = _text(entity['body'] if entity.get('body') is not None else '', 'body',limit=65536,empty=True)
            human = author['type'] == 'User' and actor != self.config['bot_user_id']
            first = next((line.strip() for line in text.splitlines() if line.strip()),'')
            explicit = human and bool(re.match('@'+re.escape(self.config['bot_login'])+r'(?=$|[\s,:])',first,re.I|re.ASCII))
            event = {'source_id':self.config['source_id'],'event_id':remote,
                'actor_id':'github:'+('user:' if author['type'] == 'User' else 'bot:')+str(actor),
                'text':text,'thread_ids':['issue/'+str(number)],'human':human,'explicit':explicit}
        except (ValueError,TypeError,KeyError,AttributeError,UnicodeError,RecursionError) as exc:
            raise _error('github-request') from exc
        normalized = json.dumps(event,sort_keys=True,ensure_ascii=False)
        with self._db(write=True) as db:
            old = db.execute('SELECT * FROM origins WHERE instance=? AND event_id=?',
                             (self.inbox.instance_id,remote)).fetchone()
            if old is None:
                if db.execute("SELECT count(*) FROM origins WHERE instance=? AND state='verified'",(self.inbox.instance_id,)).fetchone()[0] >= 1024:
                    raise _error('github-capacity')
                db.execute("INSERT INTO origins VALUES(?,?,?,?,?,?,?,'verified')",
                    (self.inbox.instance_id,remote,number,normalized,hashlib.sha256(normalized.encode('utf-8')).hexdigest(),
                     hashlib.sha256(raw).hexdigest(),delivery))
            elif old['normalized'] != normalized or old['issue'] != number:
                raise _error('event-conflict')
        if queued:
            return {'event_id':remote,'state':old['state'] if old is not None else 'verified'}
        receipt = self.inbox.receive(**event)
        with self._db(write=True) as db:
            db.execute("UPDATE origins SET state='accepted' WHERE instance=? AND event_id=?",(self.inbox.instance_id,remote))
        return receipt

    def _origin(self, row):
        try:
            original = _json(row['normalized'])
            expected = {'source_id','event_id','actor_id','text','thread_ids','human','explicit'}
            if (row['normalized_hash'] != hashlib.sha256(row['normalized'].encode('utf-8')).hexdigest()
                    or not isinstance(original,dict) or set(original) != expected
                    or original['source_id'] != self.config['source_id'] or original['event_id'] != row['event_id']
                    or original['thread_ids'] != ['issue/'+str(self._number(row['issue']))]):
                raise ValueError
            return original
        except (ValueError,TypeError,KeyError,RecursionError,HarnessError) as exc:
            raise _error('corrupt') from exc

    def drain(self):
        """推进已持久验签事件，不重放工作运行或外部写入。"""
        if self.closed:
            return
        with self._db() as db:
            rows = db.execute("SELECT * FROM origins WHERE instance=? AND state='verified' ORDER BY rowid LIMIT 16",
                              (self.inbox.instance_id,)).fetchall()
        for row in rows:
            self.inbox.receive(**self._origin(row))
            with self._db(write=True) as db:
                db.execute("UPDATE origins SET state='accepted' WHERE instance=? AND event_id=?",(self.inbox.instance_id,row['event_id']))

    def _event(self, event_id):
        with self._db() as db:
            row = db.execute("SELECT * FROM origins WHERE instance=? AND event_id=? AND state='accepted'",
                             (self.inbox.instance_id,event_id)).fetchone()
        if row is None:
            return None
        event = self.inbox.event(source_id=self.config['source_id'],event_id=event_id)
        try:
            original = self._origin(row)
            if any(event.get(key) != value for key,value in original.items()):
                raise ValueError
        except (ValueError,TypeError,RecursionError) as exc:
            raise _error('corrupt') from exc
        return event,row['issue']

    def _allowed(self, event, revision):
        source = self.inbox.source(event['source_id'])
        return (event['disposition'] == 'accepted' and event['human']
            and (event['explicit'] or any(route['kind'] == 'worker' and not route['context_only'] for route in event['routes']))
            and source['mode'] != 'ignore' and source['revision'] == revision
            and event['actor_id'] in source['actors'] and all(thread in source['threads'] for thread in event['thread_ids']))

    def _collect(self):
        with self._db(write=True) as db:
            cursor = db.execute('SELECT after FROM cursors WHERE instance=?',(self.inbox.instance_id,)).fetchone()[0]
            drafts = self.results.drafts(after=cursor,limit=16)
            for draft in drafts:
                origin = self._event(draft['event_id']) if draft['source_id'] == self.config['source_id'] else None
                if origin is None:
                    continue
                event,issue = origin
                revision = self.results._get(draft['id'])['revision']
                if not self._allowed(event,revision):
                    continue
                body = draft['reply']+'\n\n<!-- harness-superagent:'+draft['id']+' -->'
                db.execute("""INSERT OR IGNORE INTO outbound(id,instance,event_id,issue,revision,body,state)
                    VALUES(?,?,?,?,?,?,'pending')""",(draft['id'],self.inbox.instance_id,draft['event_id'],issue,revision,body))
            if drafts:
                db.execute('UPDATE cursors SET after=? WHERE instance=?',(drafts[-1]['seq'],self.inbox.instance_id))

    def _get(self, outbound_id):
        with self._db() as db:
            row = db.execute('SELECT * FROM outbound WHERE instance=? AND id=?',(self.inbox.instance_id,outbound_id)).fetchone()
        if row is None:
            raise _error('event-unavailable')
        origin = self._event(row['event_id'])
        report = self.results._get(row['id'])
        if (origin is None or origin[1] != row['issue'] or report['source_id'] != self.config['source_id']
                or report['event_id'] != row['event_id'] or report['state'] != 'draft' or row['revision'] != report['revision']
                or row['body'] != report['reply']+'\n\n<!-- harness-superagent:'+row['id']+' -->'
                or row['state'] == 'sent' and (type(row['remote_id']) is not int or row['remote_id'] < 1)):
            raise _error('corrupt')
        return dict(row),origin[0]

    def _settle(self,row,state,remote_id=None):
        with self._db(write=True) as db:
            old = db.execute('SELECT state,remote_id FROM outbound WHERE id=?',(row['id'],)).fetchone()
            if old['state'] == 'sent':
                if state in ('unknown','suppressed'):
                    return
                if state != 'sent' or remote_id != old['remote_id']:
                    raise _error('conflict')
                return
            allowed = ('pending',) if state == 'suppressed' else ('sending','unknown')
            db.execute('UPDATE outbound SET state=?,remote_id=?,next_check=? WHERE id=? AND state IN ('
                       + ','.join('?' for _ in allowed) + ')',
                       (state,remote_id,time.time()+30 if state == 'unknown' else 0,row['id'],*allowed))

    def _comment(self, value, row):
        try:
            target = urlsplit(value['issue_url'])
            base = urlsplit(self.config['api_url'])
            prefix = base.path.rstrip('/')+'/repos/'
            parts = target.path[len(prefix):].split('/') if target.path.startswith(prefix) else []
            target_port = target.port or (443 if target.scheme == 'https' else 80)
            base_port = base.port or (443 if base.scheme == 'https' else 80)
            if (not isinstance(value,dict) or self._number(value['id']) < 1 or value['body'] != row['body']
                    or self._number(value['user']['id']) != self.config['bot_user_id']
                    or target.scheme != base.scheme or target.hostname != base.hostname or target_port != base_port
                    or target.username is not None or target.password is not None or target.query or target.fragment
                    or len(parts) != 4 or '/'.join(parts[:2]).casefold() != self.config['repository'].casefold()
                    or parts[2:] != ['issues',str(row['issue'])]):
                raise ValueError
        except (ValueError,TypeError,KeyError,AttributeError,HarnessError) as exc:
            raise _error('github-response') from exc
        return value['id']

    async def _reconcile(self,row):
        matches = []
        for page in range(1,11):
            comments = await self.client.request('GET',self.repo_path+'/issues/'+str(row['issue'])+'/comments?per_page=100&page='+str(page))
            if not isinstance(comments,list) or len(comments) > 100:
                raise _error('github-response')
            for comment in comments:
                if (isinstance(comment,dict) and comment.get('body') == row['body']
                        and isinstance(comment.get('user'),dict) and comment['user'].get('id') == self.config['bot_user_id']):
                    matches.append(self._comment(comment,row))
            if len(comments) < 100:
                break
        if len(matches) > 1:
            raise _error('conflict')
        self._settle(row,'sent' if matches else 'unknown',matches[0] if matches else None)

    async def tick(self):
        if self.closed or self.client is None:
            return
        self._collect()
        with self._db(write=True) as db:
            scan = db.execute('SELECT scan FROM cursors WHERE instance=?',(self.inbox.instance_id,)).fetchone()[0]
            query = "SELECT id,seq FROM outbound WHERE instance=? AND state IN ('pending','sending','unknown') AND next_check<=?"
            candidates = db.execute(query+' AND seq>? ORDER BY seq LIMIT 1',(self.inbox.instance_id,time.time(),scan)).fetchall()
            if not candidates:
                candidates = db.execute(query+' ORDER BY seq LIMIT 1',(self.inbox.instance_id,time.time())).fetchall()
            if candidates:
                db.execute('UPDATE cursors SET scan=? WHERE instance=?',(candidates[0]['seq'],self.inbox.instance_id))
        for item in candidates:
            row,event = self._get(item['id'])
            if row['state'] != 'pending':
                await self._reconcile(row)
                continue
            if not self._allowed(event,row['revision']):
                self._settle(row,'suppressed')
                continue
            user = await self.client.request('GET','/user')
            if self.closed:
                return
            repo = await self.client.request('GET',self.repo_path)
            if (not isinstance(user,dict) or self._number(user.get('id')) != self.config['bot_user_id']
                    or user.get('login','').casefold() != self.config['bot_login'].casefold()
                    or not isinstance(repo,dict) or self._number(repo.get('id')) != self.config['repository_id']):
                raise _error('github-scope')
            if self.closed:
                return
            if not self._allowed(event,row['revision']):
                self._settle(row,'suppressed')
                continue
            with self._db(write=True) as db:
                claimed = db.execute("UPDATE outbound SET state='sending' WHERE id=? AND state='pending'",(row['id'],)).rowcount
            if not claimed:
                continue
            try:
                value = await self.client.request('POST',self.repo_path+'/issues/'+str(row['issue'])+'/comments',{'body':row['body']})
                self._settle(row,'sent',self._comment(value,row))
            except BaseException as original:
                errors = [original]
                try:
                    self._settle(row,'unknown')
                    await self._reconcile(row)
                except BaseException as recovery:
                    if recovery is not original:
                        errors.append(recovery)
                raise_cleanup_errors(errors)

    def stop(self):
        self.closed = True
        self.server.stop()

    def status(self):
        with self._db() as db:
            counts = dict(db.execute('SELECT state,count(*) FROM outbound WHERE instance=? GROUP BY state',(self.inbox.instance_id,)))
            queued = db.execute("SELECT count(*) FROM origins WHERE instance=? AND state='verified'",(self.inbox.instance_id,)).fetchone()[0]
        return {'webhook_port':self.server.port,'outbound':counts,'queued':queued,'reply_mode':self.config['reply_mode']}
