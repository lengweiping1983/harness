"""新对话的目录预检、分支切换与隔离检出。"""
from pathlib import Path

from harness.core.errors import HarnessError
from harness.i18n import tr


def failure(key: str, code: str, **details) -> HarnessError:
    """保留资源身份，调用方切换语言时无需重放检出。"""
    return HarnessError(tr(key), code=code, copy={'key': key}, **details)


def options(service, cwd: str) -> dict:
    """只读探测目录；普通文件夹与损坏的 Git 元数据保持区别。"""
    target = Path(cwd).resolve(strict=True)
    if not target.is_dir():
        raise ValueError(cwd)
    try:
        repository = service.repository(str(target))
    except HarnessError as exc:
        if exc.code != 'git/not-repository':
            raise
        return {'cwd': str(target), 'is_git': False, 'root': None, 'branch': None,
                'head': None, 'refs': [], 'local_branches': [], 'dirty': False}
    return {'cwd': str(target), 'is_git': True, 'root': repository['root'],
            'branch': repository['branch'], 'head': repository['head'],
            'refs': repository['refs'], 'dirty': bool(repository['files']),
            'local_branches': service.git(target, 'for-each-ref', '--format=%(refname:short)', 'refs/heads').splitlines()}


def prepare(service, cwd: str, selection: dict) -> tuple[str, dict]:
    """在服务锁内复核所见版本，拒绝脏目录切分支，不执行暂存或提交。"""
    current = options(service, cwd)
    mode, ref = selection['mode'], selection.get('ref')
    if not current['is_git']:
        if mode != 'local' or ref is not None:
            raise failure('ui.newConversation.gitRequired', 'session/workspace-invalid')
        return current['cwd'], {}
    if ('expected_head' in selection and selection['expected_head'] != current['head']
            or 'expected_branch' in selection and selection['expected_branch'] != current['branch']):
        raise failure('ui.newConversation.repositoryChanged', 'session/workspace-changed')
    ref = ref or current['branch'] or 'HEAD'
    metadata = {'project_cwd': current['cwd'], 'checkout_mode': mode, 'base_ref': ref}
    if mode == 'local':
        if ref == current['branch'] or ref == 'HEAD':
            return current['cwd'], metadata
        if ref not in current['local_branches']:
            raise failure('ui.newConversation.localBranchRequired', 'session/workspace-invalid')
        if current['dirty']:
            raise failure('ui.newConversation.dirtyBranch', 'session/workspace-dirty')
    elif not current['head'] or ref not in [*current['refs'], 'HEAD']:
        raise failure('ui.newConversation.commitRequired', 'session/workspace-invalid')
    try:
        row = None
        if mode == 'worktree':
            row = service.create(current['cwd'], ref=ref)
            # 子目录项目在新检出中仍定位到相同子目录。
            relative = Path(current['cwd']).relative_to(Path(current['root']))
            target = Path(row['path']) / relative
            if not target.is_dir():
                raise failure('ui.newConversation.subdirectoryMissing', 'session/workspace-directory')
            return str(target), {**metadata, 'worktree_id': row['id']}
        service.git(Path(current['root']), 'switch', '--no-guess', ref)
        if not Path(current['cwd']).is_dir():
            raise failure('ui.newConversation.subdirectoryMissing', 'session/workspace-directory')
        return current['cwd'], metadata
    except Exception as exc:
        # Git 已进入写入阶段；不能以普通拒绝诱导调用方重放。
        raise failure('ui.newConversation.preparationUnconfirmed',
                      'session/workspace-uncertain', cause=str(exc),
                      **({'worktree_id': row['id']} if row is not None else {})) from exc
