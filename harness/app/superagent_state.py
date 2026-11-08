"""同一协调账本的本机访问和来源／策略装配，不创建执行宿主。"""
from __future__ import annotations

from pathlib import Path
import sqlite3

from .superagent_common import _error
from .superagent_config import config_identity, local_identity
from .superagent_coordinator_host import _legacy_state_present
from .superagent_coordinator_store import CoordinatorStore
from .superagent_authorization import CoordinatorAuthorizationEngine
from .superagent_source_registry import SourceRegistry


def open_state(spec, *, initialize=False, read_only=False):
    root = Path(spec['state_dir'])
    if root.is_symlink() or root.is_junction() or _legacy_state_present(root):
        raise _error('conflict')
    path = root / 'superagent.sqlite3'
    if initialize and not path.exists():
        store = CoordinatorStore(path, instance_id=spec['instance_id'], identity=config_identity(spec))
        configure(store, spec)
        return store
    return CoordinatorStore.open_existing(path, instance_id=spec['instance_id'],
                                           identity=config_identity(spec), read_only=read_only)


def configure(store, spec, *, secret_resolver=None):
    auth = CoordinatorAuthorizationEngine(store)
    sources = SourceRegistry(store, ingest=auth.receive_normalized,
        on_config_changed=auth.revalidate_source_mandates_in_transaction,
        on_kind_changed=auth.revalidate_source_kind_mandates_in_transaction,
        local_identity_verifier=lambda event: local_identity(spec, event), secret_resolver=secret_resolver)
    for source_id, source in spec['sources'].items():
        with store.transaction() as db:
            exists = db.execute('SELECT 1 FROM source_instances WHERE source_instance_id=?', (source_id,)).fetchone()
        if exists is None:
            try:
                sources.create_instance(source_id, kind=source['kind'], config=source['config'], enabled=source['enabled'])
            except sqlite3.IntegrityError:
                current = sources.get_instance(source_id)
                if current.kind != source['kind'] or sources._kinds[current.kind].identity(current.config) != sources._kinds[current.kind].identity(source['config']):
                    raise _error('conflict')
        else:
            current = sources.get_instance(source_id)
            if current.kind != source['kind'] or sources._kinds[current.kind].identity(current.config) != sources._kinds[current.kind].identity(source['config']):
                raise _error('conflict')
    if auth.current_policy() is None:
        auth.publish_policy(spec['policy'], actor={'kind': 'human', 'id': 'local-config'})
    return auth, sources
