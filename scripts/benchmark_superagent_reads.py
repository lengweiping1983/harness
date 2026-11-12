"""固定八个 Work 节点、两个系统节点和十六条标准的隔离账本读取基准，不调用模型或读取用户状态。"""
from __future__ import annotations

import hashlib
import json
import platform
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.app.superagent_coordinator_store import CoordinatorStore
from harness.app.superagent_status import snapshot


def benchmark(store_type=CoordinatorStore):
    """调用计数与耗时分别测量，预热后记录同输入中位数、p95 和分配峰值。"""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        store = store_type(root / 'superagent.sqlite3', instance_id='benchmark', identity={'suite':'reads'})
        with store.unit_of_work(actor={'kind':'benchmark'}) as uow:
            ticket = uow.tickets.create({'ticket_id':'ticket', 'mandate_id':'mandate', 'origin_trigger_id':'trigger', 'title':'Benchmark'})
            mission = uow.missions.create({'mission_id':'mission', 'ticket_id':ticket['ticket_id'], 'sequence':1,
                'repo':str(root), 'base_commit':'a' * 40})
            uow.plans.create(mission['mission_id'], expected_mission_revision=mission['aggregate_revision'],
                plan={'nodes':[{'node_id':f'work-{index}', 'role':'work', 'profile_id':'inspect', 'dependencies':[]} for index in range(8)]},
                acceptance={'kind':'artifact_review', 'criteria':[{'id':f'check-{index}', 'kind':'inspection'} for index in range(16)]},
                created_by_kind='host', created_by_ref='benchmark', system_profiles={'integrate':'integrate', 'review':'review'})
        counts = {'store_connections': 0}
        connect = store._connect
        def counted_connect():
            counts['store_connections'] += 1
            return connect()
        store._connect = counted_connect
        try:
            snapshot(store)
        finally:
            store._connect = connect
        for _ in range(20):
            snapshot(store)
        durations = []
        for _ in range(100):
            started = time.perf_counter()
            rows = snapshot(store)
            durations.append((time.perf_counter() - started) * 1000)
            jobs = rows['missions']['recent'][0]['jobs']
            if len(jobs) != 10 or any(row['state'] != 'pending' for row in jobs):
                raise ValueError('benchmark/invalid-result')
        tracemalloc.start()
        try:
            snapshot(store)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        source = Path(sys.modules[store_type.__module__].__file__).read_bytes()
        return {'python': sys.version.split()[0], 'platform': platform.system(),
                'input': {'work_nodes': 8, 'system_nodes': 2, 'jobs': 10, 'checks': 16, 'samples': 100, 'warmup': 20},
                'source_sha256': hashlib.sha256(source).hexdigest(),
                'snapshot_sha256': hashlib.sha256((ROOT/'harness/app/superagent_status.py').read_bytes()).hexdigest(),
                'benchmark_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'counts_per_read': counts,
                'median_ms': round(statistics.median(durations), 4), 'p95_ms': round(sorted(durations)[94], 4), 'peak_bytes': peak}


if __name__ == '__main__':
    from python_bootstrap import configure_utf8_process
    configure_utf8_process()
    print(json.dumps(benchmark(), ensure_ascii=False, indent=2))
