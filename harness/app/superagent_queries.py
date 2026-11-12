"""统一协调账本的分页查询与任务详情；读取不推进执行或恢复。"""
from __future__ import annotations

import base64
import binascii

from .superagent_common import _error, _json as strict_loads, _page, _text
from .superagent_coordinator_store import PlanRevisionRepository, _hash, _json


# 关系、列与排序均由宿主固定；外部参数只作为绑定值传入。
_PAGES = {
    'devices': ('remote_devices d','d','d.device_id','d.device_id,d.identity_hash,d.endpoint,d.capacity,d.created_at'),
    'leases': ('remote_mission_leases l','l','l.mission_id',
        "l.mission_id,l.device_id,l.generation,l.expires_at,l.created_at,l.updated_at,CASE WHEN l.state='active' AND l.expires_at<=unixepoch() THEN 'expired' ELSE l.state END AS state"),
    'audit': ('audit_events a','a','a.aggregate_id','a.event_id,a.aggregate_type,a.aggregate_id,a.event_type,a.payload_json,a.actor_json,a.occurred_at'),
    'mandates': ('mandates d JOIN tickets t ON t.ticket_id=d.ticket_id','d','d.ticket_id',
        'd.mandate_id,d.ticket_id,d.source_instance_id,d.policy_version,d.strength,d.scope_json,t.state AS ticket_state,'
        "coalesce((SELECT event_type FROM mandate_events e WHERE e.mandate_id=d.mandate_id ORDER BY e.rowid DESC LIMIT 1),'granted') AS state"),
    'events': ('triggers t','t','t.primary_thread_id','t.trigger_id,t.source_instance_id,t.external_event_id,t.event_kind,t.actor_json,t.occurred_at,t.body_json,t.metadata_json'),
    'evaluations': ('evaluations e','e','e.thread_id','e.evaluation_id,e.thread_id,e.state,e.retry_count,e.started_at,e.outcome_json'),
    'channels': ('channels c JOIN source_instances s ON s.source_instance_id=c.source_instance_id LEFT JOIN channel_reply_bindings b ON b.channel_id=c.channel_id',
        'c','c.source_instance_id','c.channel_id,c.source_instance_id,c.thread_id,s.kind,c.address_json,s.config_revision AS source_revision,'
        "CASE WHEN s.enabled=1 AND ((b.enabled=1 AND b.source_revision=s.config_revision) OR (b.channel_id IS NULL AND json_extract(c.capabilities_json,'$.reply')=1)) THEN 1 ELSE 0 END AS reply_enabled"),
    'sources': ('source_instances s','s','s.source_instance_id','s.source_instance_id,s.kind,s.enabled,s.config_revision,s.config_json,s.health_json'),
    'effects': ('effects e','e','e.aggregate_id','e.effect_id,e.kind,e.aggregate_id,e.idempotency_key,e.state,e.generation,e.attempts,e.created_at,e.last_error'),
    'attentions': ('attentions a','a','a.mission_id','a.*'),
    'inputs': ('ticket_inputs i JOIN triggers t ON t.trigger_id=i.trigger_id JOIN missions m ON m.ticket_id=i.ticket_id','i','m.mission_id',
        'i.input_id,i.trigger_id,i.state,i.context_only,t.source_instance_id,t.event_kind,t.body_json,t.metadata_json'),
    'outbox': ('outbox_messages o JOIN missions m ON m.ticket_id=o.ticket_id','o','m.mission_id',
        'o.message_id,o.ticket_id,o.channel_id,o.kind,o.state,o.attempts,o.body_hash,o.sent_at'),
    'missions': ('missions m JOIN tickets t ON t.ticket_id=m.ticket_id', 'm', 'm.mission_id',
        'm.*,t.title AS title'),
    'jobs': ('jobs j', 'j', 'j.mission_id', 'j.*'),
    'assignments': ('assignments a JOIN jobs j ON j.job_id=a.job_id', 'a', 'j.mission_id',
        'a.assignment_id,a.job_id,a.attempt_no,a.worker_id,a.session_id,a.workspace_id,a.run_id,'
        'a.state,a.started_at,a.ended_at,a.error_ref,a.aggregate_revision,j.mission_id,j.plan_revision'),
    'artifacts': ('artifacts a JOIN assignments s ON s.assignment_id=a.assignment_id JOIN jobs j ON j.job_id=s.job_id',
        'a', 'j.mission_id', 'a.artifact_id,a.assignment_id,a.workspace_id,a.repo,a.base_commit,'
        'a.commit_id,a.tree_hash,a.manifest_hash,a.created_at'),
    'reviews': ('reviews r', 'r', 'r.mission_id',
        'r.review_id,r.mission_id,r.reviewer_assignment_id,r.candidate_artifact_id,r.plan_revision,'
        'r.acceptance_hash,r.evidence_set_hash,r.state,r.created_at'),
}
GLOBAL_RESOURCES = ('missions','devices','leases','audit','mandates','channels','sources','effects','attentions','events','evaluations')
QUERY_RESOURCES = tuple(_PAGES)


class CoordinatorQueries:
    """可信应用消费者共用的只读查询端口，不暴露原始报告或凭据。"""

    def __init__(self, store):
        self._store = store
        self._scope = _hash(_json([store.instance_id, store.identity_hash, str(store.path.resolve())]))

    def _cursor(self, resource, mission_id, cursor):
        if cursor is None:
            return None
        try:
            _text(cursor, 'cursor', limit=2048)
            value = strict_loads(base64.b64decode(cursor, altchars=b'-_', validate=True).decode('utf-8'))
            if (not isinstance(value, dict) or set(value) != {'v','scope','resource','mission_id','fence','after'}
                    or type(value['v']) is not int or value['v'] != 1
                    or value['scope'] != self._scope or value['resource'] != resource or value['mission_id'] != mission_id
                    or type(value['fence']) is not int or not 0 < value['fence'] < 9223372036854775807
                    or type(value['after']) is not int or not 0 < value['after'] <= value['fence']
                    or self._encode(value) != cursor):
                raise ValueError
            return value
        except (ValueError, TypeError, UnicodeError, RecursionError, binascii.Error) as exc:
            raise _error('query.cursor') from exc

    @staticmethod
    def _encode(value):
        return base64.urlsafe_b64encode(_json(value).encode('utf-8')).decode('ascii')

    @staticmethod
    def _mission(db, mission_id):
        row = db.execute('SELECT m.*,t.title,t.state AS ticket_state FROM missions m '
            'JOIN tickets t ON t.ticket_id=m.ticket_id WHERE m.mission_id=?', (mission_id,)).fetchone()
        if row is None:
            raise _error('query.missing')
        return dict(row)

    def page(self, resource='missions', *, mission_id=None, limit=32, cursor=None):
        """每页使用一个读事务；游标固定实例、查询、范围和首次插入水位。"""
        if not isinstance(resource, str) or resource not in _PAGES:
            raise _error('invalid', field='resource')
        _page(0, limit)
        if mission_id is not None:
            _text(mission_id, 'mission_id')
            if resource in {'devices','audit','mandates','channels','sources','effects','events','evaluations'}:
                raise _error('invalid', field='mission_id')
        elif resource not in GLOBAL_RESOURCES:
            raise _error('invalid', field='mission_id')
        continuation = self._cursor(resource, mission_id, cursor)
        relation, alias, scope, columns = _PAGES[resource]
        where, params = (scope + '=?', [mission_id]) if mission_id is not None else ('1=1', [])
        if resource == 'effects':
            where += " AND e.state='unknown'"
        with self._store.transaction() as db:
            if mission_id is not None:
                self._mission(db, mission_id)
            fence = (continuation['fence'] if continuation else
                db.execute(f'SELECT coalesce(max({alias}.rowid),0) FROM {relation} WHERE {where}', params).fetchone()[0])
            after = continuation['after'] if continuation else fence + 1
            total = db.execute(f'SELECT count(*) FROM {relation} WHERE {where} AND {alias}.rowid<=?', (*params,fence)).fetchone()[0]
            rows = db.execute(f'SELECT {alias}.rowid AS _position,{columns} FROM {relation} WHERE {where} '
                f'AND {alias}.rowid<=? AND {alias}.rowid<? ORDER BY {alias}.rowid DESC LIMIT ?',
                (*params,fence,after,limit + 1)).fetchall()
            items = []
            for row in rows[:limit]:
                item = dict(row)
                item.pop('_position')
                items.append(item)
            next_cursor = None
            if len(rows) > limit:
                next_cursor = self._encode({'v':1, 'scope':self._scope, 'resource':resource,
                    'mission_id':mission_id, 'fence':fence, 'after':rows[limit-1]['_position']})
        return {'instance_id':self._store.instance_id, 'resource':resource, 'mission_id':mission_id,
                'items':items, 'total':total, 'has_more':next_cursor is not None, 'next_cursor':next_cursor}

    def mission(self, mission_id):
        """详情与计数同事务读取，冻结计划沿用现有完整校验。"""
        _text(mission_id, 'mission_id')
        with self._store.transaction() as db:
            mission = self._mission(db, mission_id)
            plan = PlanRevisionRepository.read(db, mission_id, mission['plan_revision']) if mission['plan_revision'] else None
            if mission['plan_revision'] and plan is None:
                raise _error('corrupt')
            counts = {}
            for resource, (relation, _, scope, _) in _PAGES.items():
                if resource in {'jobs','assignments','artifacts','reviews'}:
                    counts[resource] = db.execute(f'SELECT count(*) FROM {relation} WHERE {scope}=?', (mission_id,)).fetchone()[0]
            control = db.execute('SELECT * FROM mission_controls WHERE mission_id=?', (mission_id,)).fetchone()
            watchdog = db.execute('SELECT mission_id,state,last_progress_at,observed_at FROM watchdog_snapshots WHERE mission_id=?', (mission_id,)).fetchone()
        return {'instance_id':self._store.instance_id, 'mission':mission, 'counts':counts,
            'control':dict(control) if control else None, 'watchdog':dict(watchdog) if watchdog else None,
            'plan':{key:plan[key] for key in ('plan_revision','plan_hash','acceptance_hash','acceptance_json','created_at')} if plan else None}
