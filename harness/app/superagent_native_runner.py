"""显式可信宿主 Worker，复用原生端口、租约和模型能力，不依赖容器。"""
from __future__ import annotations

import time


class NativeRunner:
    def __init__(self,node,config,broker):
        self.node,self.config,self.broker=node,config,broker
        self.closed=False

    def lease_valid(self,mission_id,generation):
        if self.closed:return False
        with self.node.ledger.transaction() as db:
            row=db.execute('SELECT generation,expires_at FROM leases WHERE mission_id=?',(mission_id,)).fetchone()
        return bool(row and row['generation']==generation and row['expires_at']>time.time())

    async def dispatch(self,value):
        from .superagent_common import _error
        if self.closed:raise _error('closed')
        value=dict(value);lease=value.get('lease')
        if value['operation']=='submit':
            worker=value['params']['worker'];profile=self.config['profiles'][worker['profile_id']]
            value['broker_capability']=self.broker.grant(mission_id=worker['mission_id'],generation=lease['generation'],
                model=profile.get('model') or self.config['model'],max_requests=profile.get('max_turns',1)*64,expires_at=lease['expires_at'])
        result=await self.node.dispatch(value)
        if lease and self.lease_valid(lease['mission_id'],lease['generation']):self.broker.renew(lease)
        return result

    async def close(self):
        self.closed=True
        await self.node.close()
