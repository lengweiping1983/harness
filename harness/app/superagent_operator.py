"""可信本机操作者的修订写入和原操作核实，不接受模型提供的授权。"""
from __future__ import annotations

from dataclasses import asdict

from .superagent_common import _error, _text, _json as strict_loads
from .superagent_coordinator_store import _hash, _json, _now
from .superagent_state import configure
from .superagent_effect_recovery import EffectLookupResult
from .superagent_outbox import OutboxEffectService

ACTOR = {'kind':'human','id':'local-workbench'}


class OperatorRequests:
    """操作者请求与业务写入同库；正文和已完成回执不可改写。"""

    @staticmethod
    def prepare(db, request_id, operation, payload):
        _text(request_id, 'request_id', limit=96)
        raw = _json(payload)
        if len(raw.encode('utf-8')) > 262144:
            raise _error('invalid', field='payload')
        row = db.execute('SELECT * FROM coordinator_operator_requests WHERE request_id=?',(request_id,)).fetchone()
        if row:
            if row['operation'] != operation or row['payload_json'] != raw or row['payload_hash'] != _hash(raw):
                raise _error('event-conflict')
            return dict(row)
        db.execute("INSERT INTO coordinator_operator_requests VALUES(?,?,?,?,'pending',NULL,?,?)",
            (request_id,operation,raw,_hash(raw),_now(),_now()))
        return None

    @staticmethod
    def finish(db, request_id, receipt):
        db.execute("UPDATE coordinator_operator_requests SET state='completed',receipt_json=?,updated_at=? WHERE request_id=? AND state='pending'",
            (_json(receipt),_now(),request_id))

    @staticmethod
    def receipt(store, request_id):
        _text(request_id,'request_id',limit=96)
        with store.transaction() as db:
            row = db.execute('SELECT * FROM coordinator_operator_requests WHERE request_id=?',(request_id,)).fetchone()
        if row is None:
            raise _error('query.missing')
        if _hash(row['payload_json']) != row['payload_hash']:
            raise _error('corrupt')
        return {'request_id':request_id,'operation':row['operation'],'state':row['state'],
            'receipt':strict_loads(row['receipt_json']) if row['receipt_json'] else None}


class CoordinatorOperator:
    """来源、策略与人工查回共用现有领域服务和数据库守卫。"""

    def __init__(self, store, spec):
        self.store,self.spec = store,spec
        self.auth,self.sources = configure(store,spec)

    def edit(self, operation, payload, request_id):
        if operation not in {'source','policy','requeue','reply'} or not isinstance(payload,dict):
            raise _error('invalid',field='operation')
        fields = {'source':({'source_id','expected_revision'},{'config','enabled'}),
            'policy':({'policy','expected_revision'},set()),'requeue':({'evaluation_id'},set()),
            'reply':({'channel_id','expected_source_revision','enabled'},set())}
        required,optional = fields[operation]
        if required-set(payload) or set(payload)-required-optional:
            raise _error('invalid',field='payload')
        if operation == 'source':
            if payload['source_id'] not in self.spec['sources']:
                raise _error('source-unavailable')
            config = payload.get('config')
            if config is not None:
                if not isinstance(config,dict):
                    raise _error('invalid',field='config')
                references = {'secret://'+key for key in self.spec['secrets']}
                if any(key.endswith('_ref') and value not in references for key,value in config.items()):
                    raise _error('invalid',field='secret_ref')
        with self.store.transaction(write=True) as db:
            prior = OperatorRequests.prepare(db,request_id,operation,payload)
            if prior:
                return OperatorRequests.receipt(self.store,request_id)
            if operation == 'source':
                result = asdict(self.sources.update_instance_in_transaction(db,payload['source_id'],
                    expected_revision=payload['expected_revision'],config=payload.get('config'),enabled=payload.get('enabled'),actor=ACTOR))
            elif operation == 'reply':
                result = self.sources.set_channel_reply_in_transaction(db,actor=ACTOR,**payload)
            elif operation == 'policy':
                if type(payload['expected_revision']) is not int or payload['expected_revision']<1:
                    raise _error('invalid',field='expected_revision')
                result = self.auth.publish_policy_in_transaction(db,payload['policy'],expected_revision=payload['expected_revision'],actor=ACTOR)
            else:
                result = {'requeued':self.auth.requeue_unavailable_in_transaction(db,payload['evaluation_id'],actor=ACTOR)}
            OperatorRequests.finish(db,request_id,result)
        return OperatorRequests.receipt(self.store,request_id)

    def effect(self, effect_id):
        _text(effect_id,'effect_id')
        with self.store.transaction() as db:
            row = db.execute('SELECT * FROM effects WHERE effect_id=?',(effect_id,)).fetchone()
        if row is None:
            raise _error('query.missing')
        result = dict(row)
        result['payload'] = strict_loads(result.pop('payload_json'))
        result['receipt'] = strict_loads(result.pop('receipt_json')) if row['receipt_json'] else None
        return result

    async def verify(self, payload, request_id, *, runtime=None):
        if (not isinstance(payload,dict) or set(payload) != {'effect_id','generation','note'}
                or type(payload['generation']) is not int or payload['generation']<1):
            raise _error('invalid',field='verification')
        _text(payload['note'],'note',limit=4096)
        original = self.effect(payload['effect_id'])
        if original['generation'] != payload['generation']:
            raise _error('conflict')
        with self.store.transaction(write=True) as db:
            prior = OperatorRequests.prepare(db,request_id,'verify',payload)
            if prior:
                return OperatorRequests.receipt(self.store,request_id)
            self.store.append_audit(db,aggregate_type='effect',aggregate_id=original['effect_id'],
                event_type='effect.operator_check_requested',payload={'request_id':request_id,**payload},actor=ACTOR)
        result = EffectLookupResult('indeterminate',detail='lookup_unavailable')
        if original['state'] == 'unknown' and runtime is not None:
            try:
                result = await runtime._coordinator_effect_recovery.lookup_original(original)
            except Exception as exc:
                result = EffectLookupResult('indeterminate',detail=getattr(exc,'code',type(exc).__name__))
        if not isinstance(result,EffectLookupResult):
            raise _error('corrupt')
        with self.store.transaction(write=True) as db:
            current = db.execute('SELECT * FROM effects WHERE effect_id=?',(original['effect_id'],)).fetchone()
            outcome = 'indeterminate'
            if current['generation'] != original['generation']:
                outcome = 'stale'
            elif current['state'] != 'unknown':
                outcome = 'already_settled'
            elif result.status in {'applied','permanent_rejection'} and result.receipt is not None:
                state = 'completed' if result.status == 'applied' else 'failed'
                if original['kind'] == 'outbox.send':
                    OutboxEffectService(self.store)._finish(db,effect_id=original['effect_id'],generation=original['generation'],
                        target_effect=state,receipt=result.receipt,error=None,due_at=None,owner=None)
                    outcome = state
                else:
                    # 非 Outbox 的原生证明交给各自领域恢复，不以人工陈述补造执行事实。
                    outcome = 'native_reconciliation_required'
            receipt = {'effect_id':original['effect_id'],'generation':original['generation'],'outcome':outcome,
                'lookup':result.status,'evidence':result.receipt,'detail':result.detail}
            self.store.append_audit(db,aggregate_type='effect',aggregate_id=original['effect_id'],
                event_type='effect.operator_checked',payload={'request_id':request_id,**receipt},actor=ACTOR)
            OperatorRequests.finish(db,request_id,receipt)
        return OperatorRequests.receipt(self.store,request_id)
