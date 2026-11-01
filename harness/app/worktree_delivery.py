"""以临时索引冻结成员交付，不提交或改动成员真实暂存区。"""
from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Callable

from harness.i18n import tr
from ..core.errors import HarnessError


def record_failure(save: Callable[[], None]) -> dict:
    """仅尝试一次保存失败现场；次级持久化失败随原错误返回，不掩盖原始失败。"""
    try:
        save()
    except Exception as exc:
        return {'recording_error': exc.to_dict() if isinstance(exc, HarnessError) else {
            'code': 'git/failure-recording', 'type': type(exc).__name__, 'message': str(exc)}}
    return {}


def snapshot_tree(service, row: dict) -> tuple[str, str]:
    """读取当前文件树，使用临时索引且不发布交付身份。"""
    target = service._target(row)
    if row['status'] != 'active':
        raise HarnessError(tr('python.worktrees.3b34e0c29769'), code='git/archived')
    return snapshot_repository(service, target)


def snapshot_repository(service, target: Path) -> tuple[str, str]:
    """冻结可信仓库根目录的当前内容，保留真实索引与提交。"""
    target = Path(target).resolve(strict=True)
    root = Path(service.git(target, 'rev-parse', '--show-toplevel').strip()).resolve(strict=True)
    if target != root:
        # 必须由调用方提供完整仓库，避免子目录快照遗漏其他改动。
        raise HarnessError(tr('ui.git.error.invalid'), code='git/snapshot-root')
    if service._git_bytes(target, 'ls-files', '--unmerged', '-z'):
        raise HarnessError(tr('ui.git.error.conflicts'), code='git/conflicts')
    head = service.git(target, 'rev-parse', 'HEAD').strip()
    index = service._git_bytes(target, 'ls-files', '--stage', '-z')
    with tempfile.TemporaryDirectory(prefix='delivery-', dir=service.root) as temporary:
        environment = {'GIT_INDEX_FILE': str(Path(temporary) / 'index')}

        def git(*args):
            return service.run_git(target, *args, env=environment).stdout

        def tree():
            # 从当前提交重建临时索引，删除、二进制及未忽略新文件均进入交付。
            git('read-tree', head)
            git('add', '-A', '--', '.')
            return git('write-tree').decode('ascii').strip()

        frozen = tree()
        # 再次读取检测交付期间的编辑；真实索引和 HEAD 变化也要求重试。
        if frozen != tree() or service.git(target, 'rev-parse', 'HEAD').strip() != head or service._git_bytes(target, 'ls-files', '--stage', '-z') != index:
            raise HarnessError(tr('ui.git.error.fileChanged'), code='git/file-changed')
    return head, frozen


def commit_tree(service, target: Path, tree: str, parents: list[str]) -> str:
    """建立内部交付谱系对象，不移动用户分支、HEAD 或改 Git 身份配置。"""
    environment = {'GIT_AUTHOR_NAME': 'Harness Delivery',
                   'GIT_AUTHOR_EMAIL': 'delivery@harness.invalid',
                   'GIT_COMMITTER_NAME': 'Harness Delivery', 'GIT_COMMITTER_EMAIL': 'delivery@harness.invalid'}
    args = ['-c', 'commit.gpgsign=false', 'commit-tree', tree]
    for parent in dict.fromkeys(parents):
        args.extend(['-p', parent])
    result = service.run_git(target, *args, env=environment, input=b'Harness delivery lineage\n')
    return result.stdout.decode('ascii').strip()


def capture(service, row: dict) -> dict:
    """冻结当前文件树并以独立引用保留，供后续依赖成员读取。"""
    target = service._target(row)
    head, frozen = snapshot_tree(service, row)
    reference = 'refs/harness/deliveries/' + row['id'] + '/' + uuid.uuid4().hex
    parent = lineage_parent(row)
    commit = commit_tree(service, target, frozen, [parent])
    service.git(target, 'update-ref', reference, commit, '0' * len(commit))
    delivery = {'commit': commit, 'tree': frozen, 'ref': reference, 'head': head, 'base_commit': row['base_commit']}
    try:
        service._save([{**item, 'delivery': delivery} if item['id'] == row['id'] else item for item in service.list()])
    except Exception as exc:
        # 只撤回本次引用；不删除对象，也不覆盖其他并发引用。
        try:
            service.git(target, 'update-ref', '-d', reference, commit)
        except Exception as cleanup:
            detail = cleanup.to_dict() if isinstance(cleanup, HarnessError) else {
                'type': type(cleanup).__name__, 'message': str(cleanup)}
            raise HarnessError(tr('git.delivery_uncertain'), code='git/delivery-uncertain',
                               worktree_id=row['id'], ref=reference, cleanup_error=detail) from exc
        raise
    return delivery


def lineage_parent(row: dict) -> str:
    """只有完成初始化的源快照才能成为交付共同祖先。"""
    base = row.get('base_commit')
    if not isinstance(base, str) or not base:
        raise HarnessError(tr('ui.git.error.invalid'), code='git/delivery-invalid')
    source = row.get('source_snapshot')
    if source is not None and not isinstance(source, dict):
        raise HarnessError(tr('ui.git.error.invalid'), code='git/source-invalid')
    if source and source.get('status') != 'ready':
        raise HarnessError(tr('ui.git.error.fileChanged'), code='git/source-incomplete')
    for snapshot in (row.get('delivery_inputs'), source):
        if snapshot is not None:
            if not isinstance(snapshot, dict) or not isinstance(snapshot.get('commit'), str) or not snapshot['commit']:
                raise HarnessError(tr('ui.git.error.invalid'), code='git/delivery-invalid')
            return snapshot['commit']
    return base


def plan_merge(service, target_row: dict, sources: list[str]) -> dict:
    """按共同基线组合依赖交付，冲突不写入用户目录或真实索引。"""
    invalid = lambda: HarnessError(tr('ui.git.error.invalid'), code='git/delivery-invalid')
    if not isinstance(sources, list) or not 1 <= len(sources) <= 8 or any(not isinstance(wid, str) for wid in sources) or len(set(sources)) != len(sources) or target_row['id'] in sources:
        raise invalid()
    identity = ('repo', 'base_commit', 'team', 'owner_session_id', 'team_run_id')
    if any(not target_row.get(key) for key in identity):
        raise invalid()
    rows = [service.get(wid) for wid in sources]
    if any(any(row.get(key) != target_row[key] for key in identity) for row in rows):
        raise invalid()
    target = service._target(target_row)
    base = service.git(target, 'rev-parse', '--verify', target_row['base_commit'] + '^{commit}').strip()
    deliveries = []
    for row in rows:
        delivery = row.get('delivery')
        if not isinstance(delivery, dict) or delivery.get('base_commit') != target_row['base_commit'] or not isinstance(delivery.get('ref'), str) or not delivery['ref'].startswith('refs/harness/deliveries/' + row['id'] + '/'):
            raise invalid()
        tree = service.git(target, 'rev-parse', '--verify', delivery['ref'] + '^{tree}').strip()
        if tree != delivery.get('tree'):
            raise invalid()
        if not isinstance(delivery.get('commit'), str) or not delivery['commit']:
            raise invalid()
        commit = service.git(target, 'rev-parse', '--verify', delivery['ref'] + '^{commit}').strip()
        if commit != delivery['commit']:
            raise invalid()
        deliveries.append({'id': row['id'], 'tree': tree, 'ref': delivery['ref'], 'commit': commit})
    head, current = snapshot_tree(service, target_row)
    parent = lineage_parent(target_row)
    current_commit = commit_tree(service, target, current, [parent])
    result = {'clean': True, 'tree': current, 'head': head, 'base_commit': target_row['base_commit'],
              'target_tree': current, 'sources': deliveries, 'conflicts': [], 'conflicted_source': None}
    for delivery in deliveries:
        # 交付父提交保留继承历史，由 Git 选择共同祖先及递归虚拟基线。
        merged = service.run_git(target, 'merge-tree', '--write-tree', '--name-only', '-z', '--no-messages',
                                 current_commit, delivery['commit'], allowed_returncodes=(0, 1))
        fields = merged.stdout.split(b'\0')
        tree = fields[0].decode('ascii').strip()
        if merged.returncode == 1:
            # 退出状态决定成功；某些目录冲突可能没有逐文件条目。
            result.update(clean=False, tree=None, conflict_tree=tree, conflicted_source=delivery['id'],
                          conflicts=[value.decode('utf-8') for value in fields[1:] if value])
            return result
        current = tree
        current_commit = commit_tree(service, target, current, [current_commit, delivery['commit']])
    result['commit'] = current_commit
    result['tree'] = current
    return result


def apply_merge(service, row: dict, sources: list[str], *, expected_target_tree: str, expected_tree: str) -> dict:
    """复查完整预检版本后导入工作区，保持真实索引与提交不变。"""
    plan = plan_merge(service, row, sources)
    if not plan['clean']:
        raise HarnessError(tr('ui.git.error.conflicts'), code='git/delivery-conflicts')
    if plan['target_tree'] != expected_target_tree or plan['tree'] != expected_tree:
        raise HarnessError(tr('ui.git.error.fileChanged'), code='git/file-changed')
    target = service._target(row)
    before_index = service._git_bytes(target, 'ls-files', '--stage', '-z')
    patch = service._git_bytes(target, 'diff', '--binary', '--full-index', '--no-ext-diff',
                               '--no-textconv', '--no-color', '--no-renames',
                               '--src-prefix=a/', '--dst-prefix=b/', plan['target_tree'], plan['tree'], '--')
    if patch:
        # 使用工作区模式；不启用会隐式修改索引的 --3way 或 --index。
        service._git_bytes(target, 'apply', '--check', '--binary', '--whitespace=nowarn', '-', input=patch)
    if snapshot_tree(service, row) != (plan['head'], plan['target_tree']):
        raise HarnessError(tr('ui.git.error.fileChanged'), code='git/file-changed')
    try:
        if patch:
            service._git_bytes(target, 'apply', '--binary', '--whitespace=nowarn', '-', input=patch)
        if snapshot_tree(service, row) != (plan['head'], plan['tree']) or service._git_bytes(target, 'ls-files', '--stage', '-z') != before_index:
            raise HarnessError(tr('ui.git.error.fileChanged'), code='git/file-changed')
        reference = 'refs/harness/imports/' + row['id'] + '/' + uuid.uuid4().hex
        service.git(target, 'update-ref', reference, plan['commit'], '0' * len(plan['commit']))
        imported = {'commit': plan['commit'], 'ref': reference, 'tree': plan['tree'], 'sources': plan['sources'], 'target_tree': plan['target_tree']}
        service._save([{**item, 'delivery_inputs': imported} if item['id'] == row['id'] else item for item in service.list()])
    except Exception as exc:
        # 写入可能已发生；保留文件并要求审查，禁止描述为已回滚或自动重试。
        raise HarnessError(tr('git.delivery_uncertain'), code='git/delivery-uncertain') from exc
    return imported
