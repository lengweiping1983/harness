"""在新的受管理工作树中整合冻结交付，保留源目录与失败现场。"""
from __future__ import annotations

from pathlib import Path
from harness.i18n import tr
from ..core.errors import HarnessError
from .worktree_delivery import record_failure


def create_integration(service, sources: list[str]) -> dict:
    """整合明确选择且已冻结的同轮交付，不改写源工作区。"""
    def invalid(): return HarnessError(tr('ui.git.error.invalid'), code='git/delivery-invalid')
    if not isinstance(sources, list) or not 1 <= len(sources) <= 8 or any(not isinstance(wid, str) or not wid for wid in sources) or len(set(sources)) != len(sources):
        raise invalid()
    members = [service.get(wid) for wid in sources]
    first = members[0]
    keys = ('repo', 'base_commit', 'team', 'owner_session_id', 'team_run_id')
    if any(not first.get(key) for key in keys) or any(any(row.get(key) != first[key] for key in keys) for row in members):
        raise invalid()
    repository = Path(first['repo'])
    # 创建资源前验证全部冻结引用，不能把成员当前编辑冒充原始交付。
    for row in members:
        delivery = row.get('delivery')
        if not isinstance(delivery, dict) or delivery.get('base_commit') != first['base_commit'] or not isinstance(delivery.get('ref'), str) or not delivery['ref'].startswith('refs/harness/deliveries/' + row['id'] + '/'):
            raise invalid()
        if service.git(repository, 'rev-parse', '--verify', delivery['ref'] + '^{tree}').strip() != delivery.get('tree'):
            raise invalid()
        if delivery.get('commit') and service.git(repository, 'rev-parse', '--verify', delivery['ref'] + '^{commit}').strip() != delivery['commit']:
            raise invalid()
    row = service.create(str(repository), ref=first['base_commit'])
    try:
        row.update({key: first[key] for key in keys})
        row.update(team_member='integration', integration=True, integration_sources=list(sources), integration_status='preparing')
        service._save([row if item['id'] == row['id'] else item for item in service.list()])
        plan = service.plan_delivery_merge(row['id'], sources)
        if not plan['clean']:
            raise HarnessError(tr('ui.git.error.conflicts'), code='git/delivery-conflicts', conflicts=plan['conflicts'], source=plan['conflicted_source'])
        service.apply_delivery_merge(row['id'], sources, expected_target_tree=plan['target_tree'], expected_tree=plan['tree'])
        service.capture_delivery(row['id'])
        service._save([{**item, 'integration_status': 'ready'} if item['id'] == row['id'] else item for item in service.list()])
        return service.get(row['id'])
    except Exception as exc:
        # 失败可能已写文件：始终保留受管理工作树，不强制删除或伪报回滚。
        error = exc.to_dict() if isinstance(exc, HarnessError) else {'code': 'git/integration-storage', 'message': str(exc)}
        recording = record_failure(lambda: service._save([
            {**item, 'integration_status': 'failed', 'integration_error': error} if item['id'] == row['id'] else item
            for item in service.list()]))
        raise HarnessError(tr('worktree.integration_failed', id=row['id']), code='git/integration-failed',
                           worktree_id=row['id'], path=row['path'], cause=error, **recording) from exc
