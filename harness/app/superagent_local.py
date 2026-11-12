"""可信本机提交、状态、指令与控制均访问唯一协调账本，不读取模型凭据。"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from .superagent_common import _error, _json as strict_loads, _text
from .superagent_config import read_config
from .superagent_coordinator_store import _hash, _json, _now
from .superagent_coordination import CoordinatorControl
from .superagent_authorization import CoordinatorAuthorizationEngine
from .superagent_state import open_state, configure
from .superagent_status import snapshot


_ACTOR = {'kind': 'human', 'id': 'local-cli'}


def _choose(value, choices, field):
    if value is None and len(choices) == 1:
        value = next(iter(choices))
    if not isinstance(value, str) or value not in choices:
        raise _error('invalid', field=field)
    return value


def _event(spec, text, source_id, actor_id, thread_id, request_id, *, instruction=False):
    _text(text, 'text', limit=65536)
    enabled = {key:source['enabled'] for key,source in spec['sources'].items()}
    if (Path(spec['state_dir'])/'superagent.sqlite3').exists():
        with open_state(spec,read_only=True).transaction() as db:
            enabled.update({row[0]:bool(row[1]) for row in db.execute('SELECT source_instance_id,enabled FROM source_instances')})
    source_id = _choose(source_id, {k for k, s in spec['sources'].items() if s['kind'] == 'local' and enabled[k]}, 'source_id')
    source = spec['sources'][source_id]
    actor_id = _choose(actor_id, source['actors'], 'actor_id')
    thread_id = _choose(thread_id, source['threads'], 'thread_id')
    request_id = uuid4().hex if request_id is None else _text(request_id, 'request_id', limit=96)
    key = _hash(_json([spec['instance_id'], source_id, request_id, 'instruction' if instruction else 'submit']))
    # 事件时间由稳定请求身份的首次接收固定；重复提交不引入新时间。
    return source_id, request_id, {'event_id': 'local_' + key, 'thread_id': thread_id, 'text': text,
        'metadata': {'source_instance_id': source_id, 'actor_id': actor_id, 'instruction_only': instruction}}


def _receive(store, spec, source_id, event):
    _, sources = configure(store, spec)
    raw = _json(event)
    with store.transaction(write=True) as db:
        prior = db.execute('SELECT * FROM coordinator_local_events WHERE source_id=? AND event_id=?', (source_id, event['event_id'])).fetchone()
        if prior is None:
            db.execute('INSERT INTO coordinator_local_events VALUES(?,?,?,?,?)', (source_id, event['event_id'], raw, _hash(raw), _now()))
            prior = db.execute('SELECT * FROM coordinator_local_events WHERE source_id=? AND event_id=?', (source_id, event['event_id'])).fetchone()
        if prior['payload_json'] != raw or prior['payload_hash'] != _hash(raw):
            raise _error('event-conflict')
        original = db.execute('SELECT * FROM triggers WHERE source_instance_id=? AND external_event_id=?', (source_id, event['event_id'])).fetchone()
        if original is not None:
            return {'state': 'duplicate', 'trigger_id': original['trigger_id'], 'source_instance_id': source_id,
                    'external_event_id': event['event_id'], 'thread_ids': [original['primary_thread_id']]}
    event['occurred_at'] = prior['occurred_at']
    return sources.receive_local(source_id, event)


def submit_local(config_path, text, *, source_id=None, actor_id=None, thread_id=None, request_id=None):
    spec = read_config(config_path)
    source_id, request_id, event = _event(spec, text, source_id, actor_id, thread_id, request_id)
    store = open_state(spec, initialize=True)
    result = _receive(store, spec, source_id, event)
    return {'request_id': request_id, **result, 'queued': True}


def answer_local(config_path, text, *, attention_id, source_id=None, actor_id=None, thread_id=None, request_id=None):
    """本机人工答案仍经原来源身份、Thread 与 Mandate 核验，不冒充远端账号。"""
    _text(attention_id, 'attention_id')
    spec = read_config(config_path)
    source_id, request_id, event = _event(spec,text,source_id,actor_id,thread_id,request_id)
    event['metadata']['attention_id'] = attention_id
    store = open_state(spec)
    with store.transaction() as db:
        prior = db.execute('SELECT event_id FROM coordinator_local_events WHERE source_id=? AND event_id=?',
            (source_id,event['event_id'])).fetchone()
        target = db.execute("SELECT a.state,m.state AS mission_state,t.source_instance_id,t.primary_thread_id "
            "FROM attentions a JOIN missions m ON m.mission_id=a.mission_id JOIN triggers t ON t.trigger_id=m.goal_ref "
            "WHERE a.attention_id=?",(attention_id,)).fetchone()
    expected = 'thr_' + _hash(_json([source_id,'local/'+spec['sources'][source_id]['config']['namespace']+'/'+event['thread_id']]))
    if target is None or target['source_instance_id']!=source_id or target['primary_thread_id']!=expected:
        raise _error('guidance-unauthorized')
    if prior is None and (target['state']!='open' or target['mission_state']!='blocked'):
        raise _error('conflict')
    result = _receive(store,spec,source_id,event)
    return {'request_id':request_id,**result,'queued':True,'attention_id':attention_id}


def _runtime(spec):
    path = Path(spec['state_dir']) / 'runtime-status.json'
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise _error('corrupt')
    with path.open('rb') as stream:
        raw = stream.read(2097153)
    try:
        result = strict_loads(raw.decode('utf-8'))
        if (len(raw) > 2097152 or not isinstance(result, dict) or result.get('instance_id') != spec['instance_id']
                or result.get('runtime_state') not in {'running', 'stopped', 'failed'}):
            raise ValueError
        return result
    except (ValueError, TypeError, UnicodeError) as exc:
        raise _error('corrupt') from exc


def read_status(config_path):
    spec = read_config(config_path)
    return snapshot(open_state(spec, read_only=True), runtime=_runtime(spec))


def query_local(config_path, resource='missions', *, mission_id=None, limit=32, cursor=None):
    """分页直接附着现有只读账本，不读取运行观察或构造模型宿主。"""
    from .superagent_queries import CoordinatorQueries
    spec = read_config(config_path)
    return CoordinatorQueries(open_state(spec, read_only=True)).page(resource, mission_id=mission_id, limit=limit, cursor=cursor)


def mission_local(config_path, mission_id):
    """任务详情使用同一查询端口，不触发迁移、执行或恢复。"""
    from .superagent_queries import CoordinatorQueries
    spec = read_config(config_path)
    return CoordinatorQueries(open_state(spec, read_only=True)).mission(mission_id)


def control_local(config_path, action, mission_id, *, request_id=None):
    spec = read_config(config_path)
    store = open_state(spec)
    request_id = uuid4().hex if request_id is None else _text(request_id, 'request_id', limit=96)
    control = CoordinatorControl(store)
    if control.receipt(request_id) is None and action == 'resume':
        with store.transaction() as db:
            mandate = db.execute('SELECT d.mandate_id FROM missions m JOIN mandates d ON d.ticket_id=m.ticket_id WHERE m.mission_id=?', (mission_id,)).fetchone()
        if mandate is None or CoordinatorAuthorizationEngine(store).revalidate_mandate(mandate['mandate_id'], actor=_ACTOR)['status'] not in {'valid', 'affected'}:
            raise _error('mandate.unavailable')
    return control.command(mission_id, action, request_id=request_id, actor=_ACTOR)


def read_control_receipt(config_path, request_id):
    spec = read_config(config_path)
    result = CoordinatorControl(open_state(spec, read_only=True)).receipt(request_id)
    if result is None:
        raise _error('status-unavailable')
    return result


def steer_local(config_path, text, *, job_id=None, source_id=None, actor_id=None, thread_id=None, request_id=None):
    spec = read_config(config_path)
    source_id, request_id, event = _event(spec, text, source_id, actor_id, thread_id, request_id, instruction=True)
    store = open_state(spec)
    with store.transaction() as db:
        old = db.execute('SELECT * FROM coordinator_requests WHERE request_id=?', (request_id,)).fetchone()
    if old is not None:
        payload = strict_loads(old['payload_json'])
        if (payload['text'] != text or payload['source_id'] != source_id or payload['actor_id'] != event['metadata']['actor_id']
                or payload['thread_id'] != event['thread_id'] or job_id is not None and payload['job_id'] != job_id):
            raise _error('event-conflict')
        return read_guidance_receipt(config_path, request_id)
    mission_id = None
    if job_id is not None:
        with store.transaction() as db:
            target = db.execute('SELECT mission_id FROM jobs WHERE job_id=?', (job_id,)).fetchone()
        if target is None:
            raise _error('guidance-unavailable')
        mission_id = target['mission_id']
    current = snapshot(store, runtime=_runtime(spec), mission_id=mission_id)
    targets = []
    for mission in current['missions']['recent']:
        if mission['state'] not in {'running', 'integrating', 'reviewing'} or (mission.get('control') or {}).get('mode') != 'running':
            continue
        for job in mission['jobs']:
            assignment, run = job.get('assignment'), job.get('run')
            if (job['role'] in {'work', 'integrate'} and (job_id is None or job_id == job['job_id'])
                    and assignment and assignment['state'] in {'accepted', 'running'} and run
                    and run.get('run_id') == assignment['run_id'] and type(run.get('turn')) is int and run['turn'] > 0):
                targets.append((mission, job, assignment, run))
    if len(targets) != 1 or job_id is None and (current['missions']['has_more'] or any(m['jobs_has_more'] for m in current['missions']['recent'])):
        raise _error('guidance-unavailable')
    mission, job, assignment, run = targets[0]
    with store.transaction() as db:
        thread = db.execute('SELECT t.primary_thread_id,t.source_instance_id FROM missions m JOIN triggers t ON t.trigger_id=m.goal_ref WHERE m.mission_id=?', (mission['mission_id'],)).fetchone()
    expected_thread = 'thr_' + _hash(_json([source_id, 'local/' + spec['sources'][source_id]['config']['namespace'] + '/' + event['thread_id']]))
    if thread is None or thread['source_instance_id'] != source_id or thread['primary_thread_id'] != expected_thread:
        raise _error('guidance-unauthorized')
    received = _receive(store, spec, source_id, event)
    payload = {'mission_id': mission['mission_id'], 'job_id': job['job_id'], 'assignment_id': assignment['assignment_id'],
        'expected_run_id': assignment['run_id'], 'expected_turn_id': run['turn'], 'trigger_id': received['trigger_id'],
        'source_id': source_id, 'actor_id': event['metadata']['actor_id'], 'thread_id': event['thread_id'], 'text': text}
    raw = _json(payload)
    with store.transaction(write=True) as db:
        if db.execute('SELECT count(*) FROM coordinator_requests').fetchone()[0] >= 1024:
            raise _error('control.capacity')
        db.execute("INSERT OR IGNORE INTO coordinator_requests VALUES(?,?,?,'pending',NULL,?,?)", (request_id, raw, _hash(raw), _now(), _now()))
        original = db.execute('SELECT payload_hash FROM coordinator_requests WHERE request_id=?', (request_id,)).fetchone()
        if original['payload_hash'] != _hash(raw):
            raise _error('event-conflict')
    return read_guidance_receipt(config_path, request_id)


def read_guidance_receipt(config_path, request_id):
    spec = read_config(config_path)
    store = open_state(spec, read_only=True)
    with store.transaction() as db:
        row = db.execute('SELECT * FROM coordinator_requests WHERE request_id=?', (request_id,)).fetchone()
        if row is None:
            raise _error('status-unavailable')
        if _hash(row['payload_json']) != row['payload_hash']:
            raise _error('corrupt')
        native = db.execute('SELECT state,receipt_json FROM mission_instructions WHERE request_id=?', (request_id,)).fetchone()
        return {'request_id': request_id, 'state': row['state'], 'queued': row['state'] == 'pending',
                'receipt': strict_loads(native['receipt_json']) if native else strict_loads(row['receipt_json']) if row['receipt_json'] else None,
                'instruction_state': native['state'] if native else None}
