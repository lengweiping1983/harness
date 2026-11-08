"""统一协调账本的分页查询与任务详情；读取不推进执行或恢复。"""
from __future__ import annotations

import base64
import binascii

from .superagent_common import _error, _json as strict_loads, _page, _text
from .superagent_coordinator_store import PlanRevisionRepository, _hash, _json


# 关系、列与排序均由宿主固定；外部参数只作为绑定值传入。
_PAGES = {
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
        elif resource != 'missions':
            raise _error('invalid', field='mission_id')
        continuation = self._cursor(resource, mission_id, cursor)
        relation, alias, scope, columns = _PAGES[resource]
        where, params = (scope + '=?', [mission_id]) if mission_id is not None else ('1=1', [])
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
                if resource != 'missions':
                    counts[resource] = db.execute(f'SELECT count(*) FROM {relation} WHERE {scope}=?', (mission_id,)).fetchone()[0]
            control = db.execute('SELECT * FROM mission_controls WHERE mission_id=?', (mission_id,)).fetchone()
            watchdog = db.execute('SELECT mission_id,state,last_progress_at,observed_at FROM watchdog_snapshots WHERE mission_id=?', (mission_id,)).fetchone()
        return {'instance_id':self._store.instance_id, 'mission':mission, 'counts':counts,
            'control':dict(control) if control else None, 'watchdog':dict(watchdog) if watchdog else None,
            'plan':{key:plan[key] for key in ('plan_revision','plan_hash','acceptance_hash','acceptance_json','created_at')} if plan else None}
