"""固定八节点、十六条标准的隔离账本读取基准，不调用模型或读取用户状态。"""
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

from harness.app.superagent_inbox import SuperagentInbox
from harness.app.superagent_mission_store import MissionStore, digest, packed


def benchmark(store_type=MissionStore):
    """调用计数与耗时分别测量，预热后记录同输入中位数、p95 和分配峰值。"""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        inbox = SuperagentInbox(root / 'inbox.sqlite3', instance_id='benchmark')
        inbox.configure_source('local', mode='requests', actors=['operator'], threads=['project'])
        event = inbox.receive(source_id='local', event_id='objective', actor_id='operator', text='Inspect eight independent files.',
                              thread_ids=['project'], human=True, explicit=True)
        profiles = {'inspect': {'repo': str(root), 'description': 'Inspect project files.', 'sources': ['local'], 'tools': ['read']}}
        limits = {'max_steps': 8, 'max_attempts': 3, 'max_missions': 16, 'integrator_profile': 'inspect', 'reviewer_profile': 'inspect'}
        store = store_type(root / 'missions.sqlite3', inbox, identity='benchmark', profiles=profiles, limits=limits)
        plan = {'steps': [{'id': 'step-' + str(index), 'profile_id': 'inspect', 'instruction': 'Inspect the input file.', 'depends_on': []}
                           for index in range(8)],
                'acceptance': [{'id': 'evidence-' + str(index), 'criterion': 'Every input file has been inspected.', 'kind': 'inspection'}
                               for index in range(16)]}
        identity = store.mission_id(event['id'])
        with store.db(write=True) as db:
            db.execute('''INSERT INTO missions(id,instance,source_id,event_id,trigger_id,revision,state,round,plan,plan_hash,base_commit,repo)
                VALUES(?,?,?,?,?,1,'running',1,?,?,?,?)''', (identity, inbox.instance_id, 'local', 'objective', event['id'], packed(plan),
                digest(packed(plan)), 'a' * 40, str(root)))
        mission = store.mission(identity)
        for step in plan['steps']:
            store.add_job(mission, role='work', node=step['id'], profile='inspect')
        counts = {'store_connections': 0, 'inbox_events': 0}
        database, original_event = store.db, inbox.event
        def counted_db(**kwargs):
            counts['store_connections'] += 1
            return database(**kwargs)
        def counted_event(**kwargs):
            counts['inbox_events'] += 1
            return original_event(**kwargs)
        store.db, inbox.event = counted_db, counted_event
        try:
            store.jobs(identity)
        finally:
            store.db, inbox.event = database, original_event
        for _ in range(20):
            store.jobs(identity)
        durations = []
        for _ in range(100):
            started = time.perf_counter()
            rows = store.jobs(identity)
            durations.append((time.perf_counter() - started) * 1000)
            if len(rows) != 8 or any(row['state'] != 'pending' for row in rows):
                raise ValueError('benchmark/invalid-result')
        tracemalloc.start()
        try:
            store.jobs(identity)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        source = Path(sys.modules[store_type.__module__].__file__).read_bytes()
        return {'python': sys.version.split()[0], 'platform': platform.system(),
                'input': {'jobs': 8, 'checks': 16, 'samples': 100, 'warmup': 20},
                'source_sha256': hashlib.sha256(source).hexdigest(),
                'benchmark_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'counts_per_read': counts,
                'median_ms': round(statistics.median(durations), 4), 'p95_ms': round(sorted(durations)[94], 4), 'peak_bytes': peak}


if __name__ == '__main__':
    from python_bootstrap import configure_utf8_process
    configure_utf8_process()
    print(json.dumps(benchmark(), ensure_ascii=False, indent=2))
