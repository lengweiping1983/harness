"""冻结团队共同源工作副本，并在成员执行前导入可信快照。"""
from __future__ import annotations

import uuid
from pathlib import Path

from harness.i18n import tr
from ..core.errors import HarnessError
from .worktree_delivery import commit_tree, record_failure, snapshot_repository, snapshot_tree


def freeze_source(service, repo: str, *, team: str, owner_session_id: str, run_id: str) -> dict:
    """以共同内部提交保存当前文件内容，不移动用户分支或索引。"""
    if any(not isinstance(value, str) or not value.strip() or len(value) > 128
           for value in (team, owner_session_id, run_id)):
        raise HarnessError(tr('worktree.team.identity_invalid'), code='git/team-identity')
    root = Path(service.git(Path(repo).resolve(), 'rev-parse', '--show-toplevel').strip()).resolve()
    head, tree = snapshot_repository(service, root)
    commit = commit_tree(service, root, tree, [head])
    reference = 'refs/harness/seeds/' + uuid.uuid4().hex
    service.git(root, 'update-ref', reference, commit, '0' * len(commit))
    return {'repo': str(root), 'team': team, 'owner_session_id': owner_session_id,
            'team_run_id': run_id, 'base_commit': head, 'tree': tree,
            'commit': commit, 'ref': reference}


def initialize_source(service, row: dict, snapshot: dict) -> dict:
    """仅导入尚未编辑的成员目录，失败保留文件及初始化记录。"""
    invalid = lambda: HarnessError(tr('ui.git.error.invalid'), code='git/source-invalid')
    keys = ('repo', 'team', 'owner_session_id', 'team_run_id', 'base_commit')
    if not isinstance(snapshot, dict) or any(not row.get(key) or row.get(key) != snapshot.get(key) for key in keys):
        raise invalid()
    reference = snapshot.get('ref')
    if not isinstance(reference, str) or not reference.startswith('refs/harness/seeds/'):
        raise invalid()
    target = service._target(row)
    if service.git(target, 'rev-parse', '--verify', reference + '^{commit}').strip() != snapshot.get('commit'):
        raise invalid()
    if service.git(target, 'rev-parse', '--verify', reference + '^{tree}').strip() != snapshot.get('tree'):
        raise invalid()
    if service.git(target, 'rev-parse', snapshot['commit'] + '^').strip() != row['base_commit']:
        raise invalid()
    current = snapshot_tree(service, row)
    existing = row.get('source_snapshot')
    if existing:
        # 成功后的重复调用只复用身份，不能覆盖成员后续编辑。
        if existing.get('status') == 'ready' and all(existing.get(key) == snapshot.get(key) for key in (*keys, 'tree', 'commit', 'ref')):
            return existing
        raise invalid()
    base_tree = service.git(target, 'rev-parse', row['base_commit'] + '^{tree}').strip()
    before_index = service._git_bytes(target, 'ls-files', '--stage', '-z')
    if current != (row['base_commit'], base_tree) or service._git_bytes(target, 'diff', '--cached', '--raw', '--no-ext-diff', row['base_commit'], '--') or row.get('delivery') or row.get('delivery_inputs'):
        raise HarnessError(tr('ui.git.error.fileChanged'), code='git/file-changed')
    patch = service._git_bytes(target, 'diff', '--binary', '--full-index', '--no-ext-diff',
                               '--no-textconv', '--no-color', '--no-renames',
                               '--src-prefix=a/', '--dst-prefix=b/', base_tree, snapshot['tree'], '--')
    if patch:
        service._git_bytes(target, 'apply', '--check', '--binary', '--whitespace=nowarn', '-', input=patch)
    if snapshot_tree(service, row) != current or service._git_bytes(target, 'ls-files', '--stage', '-z') != before_index:
        raise HarnessError(tr('ui.git.error.fileChanged'), code='git/file-changed')
    def save(value):
        service._save([{**item, 'source_snapshot': value} if item['id'] == row['id'] else item for item in service.list()])
    pending = {**snapshot, 'status': 'preparing'}
    save(pending)
    try:
        if patch:
            service._git_bytes(target, 'apply', '--binary', '--whitespace=nowarn', '-', input=patch)
        if snapshot_tree(service, row) != (row['base_commit'], snapshot['tree']) or service._git_bytes(target, 'ls-files', '--stage', '-z') != before_index:
            raise HarnessError(tr('ui.git.error.fileChanged'), code='git/file-changed')
        ready = {**snapshot, 'status': 'ready'}
        save(ready)
        return ready
    except Exception as exc:
        # 记录可能已经应用的现场；不强制回滚，不自动重试覆盖内容。
        recording = record_failure(lambda: save({**pending, 'status': 'failed'}))
        raise HarnessError(tr('git.delivery_uncertain'), code='git/source-uncertain',
                           worktree_id=row['id'], path=row['path'], ref=reference, **recording) from exc
