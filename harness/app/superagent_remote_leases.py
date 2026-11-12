"""同一协调账本的设备登记、按 Mission 分配和只续原设备租约。"""
from __future__ import annotations

import time
from .superagent_common import _error
from .superagent_coordinator_store import _now


class RemoteLeases:
    def __init__(self,store,devices,identity_hash,seconds):
        self.store,self.devices,self.seconds=store,{item['device_id']:item for item in devices},seconds
        with store.transaction(write=True) as db:
            for item in devices:
                row=db.execute('SELECT * FROM remote_devices WHERE device_id=?',(item['device_id'],)).fetchone()
                values=(identity_hash,item['url'],item['capacity'])
                if row and tuple(row[key] for key in ('identity_hash','endpoint','capacity'))!=values:
                    raise _error('remote.identity')
                if row is None:
                    db.execute('INSERT INTO remote_devices VALUES(?,?,?,?,?)',(item['device_id'],*values,_now()))
                    store.append_audit(db,aggregate_type='device',aggregate_id=item['device_id'],event_type='device.registered',
                        payload={'identity_hash':identity_hash,'endpoint':item['url'],'capacity':item['capacity']},actor={'kind':'host'})

    def for_mission(self,mission_id,*,allocate=False,renew=False,original_stop=False):
        now=int(time.time())
        with self.store.transaction(write=allocate or renew) as db:
            row=db.execute('SELECT * FROM remote_mission_leases WHERE mission_id=?',(mission_id,)).fetchone()
            if row is None:
                if not allocate: raise _error('remote.lease')
                choices=[]
                for key,item in self.devices.items():
                    count=db.execute("SELECT count(*) FROM remote_mission_leases l JOIN missions m ON m.mission_id=l.mission_id "
                        "WHERE l.device_id=? AND m.state NOT IN ('completed','cancelled','rejected','suppressed')",(key,)).fetchone()[0]
                    if count<item['capacity']: choices.append((count/item['capacity'],key))
                if not choices: raise _error('capacity')
                device=min(choices)[1]
                db.execute("INSERT INTO remote_mission_leases VALUES(?,?,1,'active',?,?,?)",(mission_id,device,now+self.seconds,_now(),_now()))
                self.store.append_audit(db,aggregate_type='mission',aggregate_id=mission_id,event_type='worker.lease_assigned',
                    payload={'device_id':device,'generation':1},actor={'kind':'host'})
                row=db.execute('SELECT * FROM remote_mission_leases WHERE mission_id=?',(mission_id,)).fetchone()
            if row['device_id'] not in self.devices or (row['state']!='active' and not original_stop): raise _error('remote.lease')
            if renew and row['expires_at']<now+self.seconds//2:
                db.execute('UPDATE remote_mission_leases SET expires_at=?,updated_at=? WHERE mission_id=?',
                    (max(now+self.seconds,row['expires_at']),_now(),mission_id))
                row=db.execute('SELECT * FROM remote_mission_leases WHERE mission_id=?',(mission_id,)).fetchone()
            return {key:row[key] for key in ('mission_id','device_id','generation','expires_at')}

    def check(self,lease,*,original_stop=False):
        with self.store.transaction() as db:
            row=db.execute('SELECT * FROM remote_mission_leases WHERE mission_id=?',(lease['mission_id'],)).fetchone()
        if row is None or (row['state']!='active' and not original_stop) or row['device_id']!=lease['device_id'] or row['generation']!=lease['generation']:
            raise _error('remote.lease')

    def revoke(self,mission_id):
        with self.store.transaction(write=True) as db:
            changed=db.execute("UPDATE remote_mission_leases SET state='revoked',updated_at=? WHERE mission_id=? AND state='active'",(_now(),mission_id)).rowcount
            if changed:self.store.append_audit(db,aggregate_type='mission',aggregate_id=mission_id,event_type='worker.lease_revoked',payload={},actor={'kind':'host'})
