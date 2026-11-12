"""导入明确选择的忽略环境文件，保留现有内容及失败回执。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath

from harness.i18n import tr
from ..core.errors import HarnessError
from ..session.store import _atomic_write
from .worktree_environment import _regular_file, preview_environment
from .worktree_delivery import record_failure


def _ignored(service, target: Path, names: list[str]) -> set[str]:
    """要求目标实际忽略规则也接受文件，避免配置进入代码交付。"""
    if not names:
        return set()
    result = service.run_git(target, 'check-ignore', '--stdin', '-z',
                            input=('\0'.join(names) + '\0').encode('utf-8'), allowed_returncodes=(0, 1))
    return {name for name in result.stdout.decode('utf-8').split('\0') if name}


def plan_environment(service, row: dict) -> dict:
    """预检受管理目标和源选择，明确显示保留及不能复制的路径。"""
    if row['status'] != 'active':
        raise HarnessError(tr('python.worktrees.3b34e0c29769'), code='git/archived')
    target = service._target(row)
    source = preview_environment(service, row['repo'])
    actual_root = Path(service.git(target, 'rev-parse', '--show-toplevel').strip()).resolve()
    common = lambda root: Path(service.git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir').strip()).resolve()
    if actual_root != target or Path(row['path']).absolute() != target or Path(row['repo']).absolute() != Path(source['repo']) or common(target) != common(Path(source['repo'])):
        raise HarnessError(tr('ui.git.error.invalid'), code='git/environment-invalid')
    ignored = _ignored(service, target, [item['path'] for item in source['files']])
    files = []
    skipped = list(source['skipped'])
    for item in source['files']:
        info, reason = _regular_file(target, item['path'])
        if info is not None or reason != 'missing':
            skipped.append({'path': item['path'], 'reason': 'exists' if info is not None else reason})
        elif item['path'] not in ignored:
            skipped.append({'path': item['path'], 'reason': 'not-ignored'})
        else:
            files.append(item)
    result = {'id': row['id'], 'path': str(target), 'repo': source['repo'],
              'configured': source['configured'], 'source_fingerprint': source['fingerprint'],
              'target_head': service.git(target, 'rev-parse', 'HEAD').strip(),
              'target_index': hashlib.sha256(service._git_bytes(target, 'ls-files', '--stage', '-z')).hexdigest(),
              'files': files, 'skipped': skipped, 'total_bytes': sum(item['size'] for item in files)}
    result['fingerprint'] = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    return result


def _identity(info):
    """比较预览与已打开源文件身份，不依赖文件正文进入预览。"""
    return (info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode))


def _stage(source: Path, item: dict, staged: Path) -> dict:
    """按来源大小上限流式暂存，读取前后复查身份并计算内容摘要。"""
    expected = (item['size'], item['mtime_ns'], item['device'], item['inode'], item['mode'])
    info, reason = _regular_file(source, item['path'])
    if reason or _identity(info) != expected:
        raise HarnessError(tr('ui.git.error.fileChanged'), code='git/file-changed')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    digest = hashlib.sha256()
    size = 0
    with os.fdopen(os.open(source / item['path'], flags), 'rb') as incoming:
        if _identity(os.fstat(incoming.fileno())) != expected:
            raise HarnessError(tr('ui.git.error.fileChanged'), code='git/file-changed')
        # 创建时即使用源权限，临时配置也不扩大源文件读取权限。
        with os.fdopen(os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), item['mode']), 'wb') as outgoing:
            while block := incoming.read(min(1_048_576, item['size'] - size + 1)):
                size += len(block)
                if size > item['size']:
                    raise HarnessError(tr('ui.git.error.fileChanged'), code='git/file-changed')
                outgoing.write(block)
                digest.update(block)
        info, reason = _regular_file(source, item['path'])
        if reason or _identity(info) != expected or _identity(os.fstat(incoming.fileno())) != expected or size != item['size']:
            raise HarnessError(tr('ui.git.error.fileChanged'), code='git/file-changed')
    # 先保持临时文件可由所有者删除，解除临时链接后再恢复目标原权限。
    os.chmod(staged, item['mode'] | stat.S_IWUSR)
    os.utime(staged, ns=(item['mtime_ns'], item['mtime_ns']))
    return {'path': item['path'], 'size': size, 'mode': item['mode'], 'sha256': digest.hexdigest()}


def _parent(target: Path, name: str) -> Path:
    """逐级创建普通目录；已有链接和非目录路径不得作为写入父级。"""
    from .worktree_environment import _linked
    cursor = target
    for part in Path(name).parts[:-1]:
        cursor /= part
        try:
            cursor.mkdir()
        except FileExistsError:
            pass
        info = cursor.lstat()
        if _linked(info) or not cursor.is_dir():
            raise HarnessError(tr('ui.git.error.fileChanged'), code='git/file-changed')
    return target / name


def _read_receipt(path: Path, identifier: str, worktree: str) -> dict:
    """读取当前回执，损坏原件不修补、不迁移、不覆盖。"""
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        fields = {'receipt_id', 'worktree_id', 'fingerprint', 'source_fingerprint',
                  'previous_receipt_id', 'status', 'planned', 'copied', 'skipped'}
        valid = isinstance(value, dict) and set(value) == fields
        valid = valid and value['receipt_id'] == identifier and value['worktree_id'] == worktree
        valid = valid and value['status'] in ('preparing', 'ready', 'failed')
        valid = valid and all(isinstance(value[key], str) and re.fullmatch('[a-f0-9]{64}', value[key])
                              for key in ('fingerprint', 'source_fingerprint'))
        previous = value.get('previous_receipt_id') if isinstance(value, dict) else None
        valid = valid and (previous is None or isinstance(previous, str) and re.fullmatch('[a-f0-9]{32}', previous))
        for field in ('planned', 'copied', 'skipped'):
            items = value.get(field) if isinstance(value, dict) else None
            if not isinstance(items, list):
                valid = False
                continue
            for item in items:
                name = item.get('path') if isinstance(item, dict) else None
                safe = (isinstance(name, str) and bool(name) and '\0' not in name
                        and not PurePosixPath(name).is_absolute() and not PureWindowsPath(name).is_absolute()
                        and '..' not in name.replace('\\', '/').split('/'))
                if not safe:
                    valid = False
                    continue
                if field == 'skipped':
                    valid = valid and isinstance(item.get('reason'), str)
                else:
                    valid = valid and all(type(item.get(key)) is int and item[key] >= 0 for key in ('size', 'mode'))
                    digest = item.get('sha256')
                    valid = valid and isinstance(digest, str) and bool(re.fullmatch('[a-f0-9]{64}', digest))
        if not valid:
            raise ValueError('invalid environment receipt')
        return value
    except (OSError, ValueError, TypeError) as exc:
        raise HarnessError(tr('ui.git.error.invalid'), code='git/environment-invalid') from exc


def copy_environment(service, row: dict, *, expected_fingerprint: str, before_publish=None) -> dict:
    """复查选定版本后独占创建文件，失败保留真实复制回执。"""
    if not isinstance(expected_fingerprint, str) or not re.fullmatch('[a-f0-9]{64}', expected_fingerprint):
        raise HarnessError(tr('ui.git.error.invalid'), code='git/environment-invalid')
    if row['status'] != 'active':
        raise HarnessError(tr('python.worktrees.3b34e0c29769'), code='git/archived')
    receipts = service.root / 'environment-receipts'
    previous = row.get('environment') if isinstance(row.get('environment'), dict) else {}
    previous_id = previous.get('receipt_id')
    if isinstance(previous_id, str) and re.fullmatch('[a-f0-9]{32}', previous_id):
        old = _read_receipt(receipts / (previous_id + '.json'), previous_id, row['id'])
        if old.get('fingerprint') == expected_fingerprint:
            if old.get('status') == 'ready':
                target = service._target(row)
                if all(_regular_file(target, item['path'])[0] is not None for item in old['copied']):
                    return {**old, 'reused': True}
            else:
                raise HarnessError(tr('worktree.environment.uncertain'), code='git/environment-uncertain',
                                   worktree_id=row['id'], receipt_id=previous_id)
    plan = plan_environment(service, row)
    if plan['fingerprint'] != expected_fingerprint:
        raise HarnessError(tr('ui.git.error.fileChanged'), code='git/file-changed')
    target = service._target(row)
    source = Path(plan['repo'])
    with tempfile.TemporaryDirectory(prefix='environment-stage-', dir=service.root) as temporary:
        staged = [(Path(temporary) / str(index), _stage(source, item, Path(temporary) / str(index))) for index, item in enumerate(plan['files'])]
        if plan_environment(service, row)['fingerprint'] != expected_fingerprint:
            raise HarnessError(tr('ui.git.error.fileChanged'), code='git/file-changed')
        # 宿主在暂存完成后复查动态权限；拒绝时尚未发布目标文件。
        if before_publish is not None:
            before_publish()
        receipt_id = uuid.uuid4().hex
        receipt = {'receipt_id': receipt_id, 'worktree_id': row['id'], 'fingerprint': expected_fingerprint,
                   'source_fingerprint': plan['source_fingerprint'], 'previous_receipt_id': previous_id,
                   'status': 'preparing', 'planned': [item for _, item in staged],
                   'copied': [], 'skipped': list(plan['skipped'])}
        receipts.mkdir(exist_ok=True)
        def save():
            _atomic_write(receipts / (receipt_id + '.json'), json.dumps(receipt, ensure_ascii=False))
            summary = {key: receipt[key] for key in ('receipt_id', 'fingerprint', 'status')}
            summary.update(copied_count=len(receipt['copied']), skipped_count=len(receipt['skipped']))
            service._save([{**item, 'environment': summary} if item['id'] == row['id'] else item for item in service.list()])
        try:
            save()
            if plan_environment(service, row)['fingerprint'] != expected_fingerprint:
                raise HarnessError(tr('ui.git.error.fileChanged'), code='git/file-changed')
            selected_paths = [identity['path'] for _, identity in staged]
            if not set(selected_paths).issubset(_ignored(service, target, selected_paths)):
                raise HarnessError(tr('ui.git.error.fileChanged'), code='git/file-changed')
            for file, identity in staged:
                name = identity['path']
                info, reason = _regular_file(target, name)
                if info is not None or reason != 'missing':
                    receipt['skipped'].append({'path': name, 'reason': 'exists' if info is not None else reason})
                    continue
                destination = _parent(target, name)
                try:
                    # 同一管理目录中的硬链接发布不覆盖任何已存在目标。
                    os.link(file, destination)
                except FileExistsError:
                    receipt['skipped'].append({'path': name, 'reason': 'exists'})
                    continue
                receipt['copied'].append(identity)
                file.unlink()
                os.chmod(destination, identity['mode'])
            if service.git(target, 'rev-parse', 'HEAD').strip() != plan['target_head'] or hashlib.sha256(service._git_bytes(target, 'ls-files', '--stage', '-z')).hexdigest() != plan['target_index']:
                raise HarnessError(tr('ui.git.error.fileChanged'), code='git/file-changed')
            if not {item['path'] for item in receipt['copied']}.issubset(_ignored(service, target, [item['path'] for item in receipt['copied']])):
                raise HarnessError(tr('ui.git.error.fileChanged'), code='git/file-changed')
            receipt['status'] = 'ready'
            save()
        except Exception as exc:
            receipt['status'] = 'failed'
            recording = record_failure(save)
            raise HarnessError(tr('worktree.environment.uncertain'), code='git/environment-uncertain',
                               worktree_id=row['id'], path=str(target), receipt_id=receipt_id,
                               copied_count=len(receipt['copied']), **recording) from exc
    return {**receipt, 'reused': False}
