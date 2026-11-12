"""以持久原生 Run 执行 Assessor 和 Planner，不在协调事务内调用模型。"""
from __future__ import annotations

import asyncio

from ..core.cleanup import join_cleanup
from ..i18n import tr
from ..core.language import current_language, resolve_language
from .superagent_common import _error, _json as strict_loads
from .superagent_coordinator_store import _hash, _json, _now


class CoordinatorModels:
    """模型只能提出结构化提案；重复请求回查原 Run，未知提交不重放。"""

    def __init__(self, harness, profiles):
        self.harness, self.profiles = harness, profiles
        self.store = None
        self._locks = {}

    def bind(self, host):
        self.store = host._store

    def session(self, key):
        marker = {'key': key, 'instance_id': self.store.instance_id}
        headers = [h for h in self.harness.sessions.list_sessions() if h.metadata.get('superagent_coordinator_role') == marker]
        if len(headers) > 1:
            raise _error('conflict')
        if not headers:
            self.harness.create_session(metadata={'superagent_coordinator_role': marker})
            headers = [h for h in self.harness.sessions.list_sessions() if h.metadata.get('superagent_coordinator_role') == marker]
        if len(headers) != 1:
            raise _error('conflict')
        session = self.harness.get_session(headers[0].id)
        self.harness._agent_for(session).tools = self.harness.tools.restricted([])
        return session.id

    async def _call(self, role, key, frame):
        async with self._locks.setdefault(key, asyncio.Lock()):
            with self.store.transaction() as db:
                row = db.execute('SELECT * FROM coordinator_model_calls WHERE call_key=?', (key,)).fetchone()
            if row is None:
                with current_language.set(resolve_language(self.harness.config.language).effective_language):
                    prompt = tr({'assess':'superagent.coordinator.assess', 'plan':'superagent.coordinator.plan'}[role], frame=_json(frame))
                session_id = self.session(key)
                with self.store.transaction(write=True) as db:
                    db.execute('INSERT INTO coordinator_model_calls VALUES(?,?,?,?,?,\'prepared\',NULL,NULL,?,?)',
                               (key, role, session_id, prompt, _hash(prompt), _now(), _now()))
                    row = db.execute('SELECT * FROM coordinator_model_calls WHERE call_key=?', (key,)).fetchone()
            if row['role'] != role or _hash(row['prompt']) != row['prompt_hash']:
                raise _error('corrupt')
            if row['state'] == 'completed':
                return strict_loads(row['output_json'])
            self.harness._agent_for(self.harness.get_session(row['session_id'])).tools = self.harness.tools.restricted([])
            record = self.harness.runtime.find_submission(row['session_id'], key)
            if record is None and row['state'] == 'prepared':
                with self.store.transaction(write=True) as db:
                    claimed = db.execute("UPDATE coordinator_model_calls SET state='submitting',updated_at=? WHERE call_key=? AND state='prepared'", (_now(), key)).rowcount
                if claimed:
                    try:
                        record = self.harness.runtime.submit(row['session_id'], row['prompt'], key=key, request_id=key)
                    except BaseException:
                        record = self.harness.runtime.find_submission(row['session_id'], key)
                        if record is None:
                            with self.store.transaction(write=True) as db:
                                db.execute("UPDATE coordinator_model_calls SET state='unknown',updated_at=? WHERE call_key=?", (_now(), key))
                            raise
            if record is None:
                raise _error('coordinator.unknown')
            if (record.session_id != row['session_id'] or record.text != row['prompt'] or record.request_id != key
                    or row['run_id'] is not None and record.id != row['run_id']):
                raise _error('conflict')
            with self.store.transaction(write=True) as db:
                db.execute("UPDATE coordinator_model_calls SET state='accepted',run_id=?,updated_at=? WHERE call_key=?", (record.id, _now(), key))
            if record.task is not None:
                await join_cleanup(record.task)
            if not record.settled or record.status != 'completed':
                raise _error('coordinator.unknown')
            if not isinstance(record.output, str) or len(record.output.encode('utf-8')) > 131072:
                raise _error('coordinator.output')
            value = strict_loads(record.output)
            if not isinstance(value, dict):
                raise _error('coordinator.output')
            with self.store.transaction(write=True) as db:
                db.execute("UPDATE coordinator_model_calls SET state='completed',output_json=?,updated_at=? WHERE call_key=? AND run_id=?", (_json(value), _now(), key, record.id))
            return value

    async def assess(self, candidate):
        return await self._call('assess', 'assess:' + candidate['evaluation_id'], candidate)

    async def plan(self, *, trigger_id, profile, prompt):
        frame = {'trigger_id': trigger_id, 'default_profile': profile.profile_id, 'request': prompt,
                 'profiles': {p.profile_id: {'description': p.description, 'tools': list(p.tools)} for p in self.profiles}}
        proposal = await self._call('plan', 'plan:' + trigger_id, frame)
        nodes = (proposal.get('plan') or {}).get('nodes')
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 16:
            raise _error('coordinator.output')
        normalized = []
        for node in nodes:
            if not isinstance(node, dict) or not isinstance(node.get('node_id'), str) or not isinstance(node.get('instruction'), str) or not node['instruction'].strip():
                raise _error('coordinator.output')
            instruction = node['instruction']
            if node.get('instruction_hash') not in (None, _hash(instruction)):
                raise _error('coordinator.output')
            normalized.append({**node, 'instruction_ref':'trigger:' + trigger_id + ':' + node['node_id'],
                               'instruction_hash':_hash(instruction)})
        return {**proposal, 'plan': {**proposal['plan'], 'nodes':normalized}}
