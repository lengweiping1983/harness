"""依据已验证的回合证据显式还原单个文件，并持久保存回执。"""
from __future__ import annotations

from harness.i18n import tr

import asyncio
import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path

from ..core.errors import HarnessError
from ..session.file_evidence import LIMIT
from ..tools.builtin import fs_observation
from ..tools.builtin.file_diff import file_diff
from .turn_review import _events, _operations
from .turn_net_changes import _net_file, _uncertain


class FileRestoreService:
    def __init__(self, harness):
        self.harness = harness
        self._lock = asyncio.Lock()

    def _scope(self, session_id, turn, source_seq, through):
        session = self.harness.get_session(session_id)
        events = _events(session, turn)
        if type(source_seq) is not int or not 0 <= source_seq <= 2**53 - 1 or type(through) is not int or through != events[-1].seq:
            raise HarnessError(tr('python.file_restore.f1e8ccfb97ee'), code='restore/stale-snapshot')
        operations = list(_operations(events))
        first = next((operation for operation in operations if operation['source_seq'] == source_seq), None)
        if first is None:
            raise HarnessError(tr('python.file_restore.d943e6341a1e'), code='restore/not-found')
        key = lambda path: os.path.normcase(path.replace('\\', '/'))
        selected = [operation for operation in operations if key(operation['path']) == key(first['path'])]
        if selected[0]['source_seq'] != source_seq:
            raise HarnessError(tr('python.file_restore.ff7814e9a5cc'), code='restore/not-found')
        return session, events, selected

    def _busy(self):
        # Shell 和后台进程可能写入启动目录之外的位置。
        if any(not task.done() for task in self.harness.runtime.active.values()):
            return tr('python.file_restore.0e05865b99ee')
        if any(not state.settled for state in self.harness.subagents.list_states()):
            return tr('python.file_restore.2533564bce2c')
        if any(job.get('status') not in {'completed', 'killed', 'failed'} or job.get('cleanupError') for job in self.harness.jobs.list_all_jobs()):
            return tr('python.file_restore.620be80324e8')
        if any(terminal.get('status', {}).get('kind') != 'exited' or terminal.get('control') for terminal in self.harness.terminals.list_all()):
            return tr('python.file_restore.2f83d33bc3da')
        return None

    def _target(self, session, path):
        if not session.header.cwd:
            raise ValueError(tr('python.file_restore.b7df2130b7d4'))
        root = Path(session.header.cwd).expanduser().resolve(strict=True)
        target = Path(path)
        if not target.is_absolute():
            raise ValueError(tr('python.file_restore.7bc4a64924ed'))
        # 拒绝路径各层中的符号链接和目录联接，避免跟随
        # 原编辑后目标可能变化的路径别名。
        for candidate in (target, *target.parents):
            if candidate.is_symlink() or getattr(candidate, 'is_junction', lambda: False)():
                raise ValueError(tr('python.file_restore.176b0d36b105'))
        target = target.resolve(strict=True)
        if not target.is_relative_to(root) or not root.is_dir():
            raise ValueError(tr('python.file_restore.dc8597d196e0'))
        info = target.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError(tr('python.file_restore.8346a03e1d26'))
        if not info.st_mode & stat.S_IWUSR:
            raise ValueError(tr('python.file_restore.5b1e9efbb0d7'))
        if info.st_size > LIMIT:
            raise ValueError(tr('python.file_restore.633e71117a37'))
        with target.open('rb') as handle:
            raw = handle.read(LIMIT + 1)
            opened = os.fstat(handle.fileno())
        after = target.stat()
        identity = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns, row.st_mode, row.st_nlink)
        # Windows 路径 stat 与句柄 fstat 的 ctime 语义可能不同。
        # 只比较这些接口共有的字段，在令牌中保留两个时间值。
        shared = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_mode, row.st_nlink)
        if shared(info) != shared(opened) or identity(info) != identity(after) or len(raw) != info.st_size:
            raise ValueError(tr('python.file_restore.03bef11f7ef2'))
        return target, raw, (*identity(info), opened.st_ctime_ns)

    def _notes(self, session, turn, source_seq):
        candidates = [event for event in session.events if event.type == 'note' and isinstance(event.data, Mapping) and event.data.get('kind') in {'file_restore_prepared', 'file_restore_result'}
                      and event.data.get('turn') == turn and event.data.get('source_seq') == source_seq]
        for event in candidates:
            if not isinstance(event.data.get('session_id'), str) or not event.data['session_id']:
                raise HarnessError(tr('python.file_restore.e55dcdbf431e'), code='restore/corrupt')
        own = [event for event in candidates if event.data['session_id'] == session.id]
        for event in own:
            if not isinstance(event.data.get('request_id'), str) or not re.fullmatch('[A-Za-z0-9_-]{8,128}', event.data['request_id']):
                raise HarnessError(tr('python.file_restore.b25617a5475b'), code='restore/corrupt')
        return own

    def _receipt(self, notes, request_id):
        rows = [event for event in notes if event.data.get('request_id') == request_id]
        if not rows:
            return None
        preparations = [event for event in rows if event.data.get('kind') == 'file_restore_prepared']
        if len(preparations) != 1:
            raise HarnessError(tr('python.file_restore.737edd538c37'), code='restore/corrupt')
        prepared = preparations[0].data
        if any(key not in prepared for key in ('session_id', 'turn', 'source_seq', 'path', 'action', 'expected_version', 'through')):
            raise HarnessError(tr('python.file_restore.e55dcdbf431e'), code='restore/corrupt')
        result = next((event.data for event in reversed(rows) if event.data.get('kind') == 'file_restore_result'), None)
        if not isinstance(prepared['path'], str) or prepared['action'] not in {'restore', 'remove'} or not isinstance(prepared['expected_version'], str) or not re.fullmatch('[0-9a-f]{64}', prepared['expected_version']) or type(prepared['through']) is not int:
            raise HarnessError(tr('python.file_restore.2698242a4871'), code='restore/corrupt')
        if result is not None and (result.get('status') not in {'restored', 'rejected', 'unknown'} or not isinstance(result.get('message'), str)):
            raise HarnessError(tr('python.file_restore.12eaf614ff3a'), code='restore/corrupt')
        return {'session_id': prepared['session_id'], 'turn': prepared['turn'], 'source_seq': prepared['source_seq'],
                'request_id': request_id, 'path': prepared['path'], 'action': prepared['action'],
                'status': result['status'] if result else 'unknown',
                'message': result['message'] if result else tr('python.file_restore.882ac97ec97a'),
                'time': rows[-1].time}

    def receipt(self, session_id, turn, source_seq, request_id):
        self._request_id(request_id)
        if type(source_seq) is not int or not 0 <= source_seq <= 2**53 - 1:
            raise HarnessError(tr('python.file_restore.39c136221345'), code='restore/invalid')
        session = self.harness.get_session(session_id)
        _events(session, turn)
        value = self._receipt(self._notes(session, turn, source_seq), request_id)
        if value is None:
            raise HarnessError(tr('python.file_restore.7fe05b6f70cc'), code='restore/not-found')
        return value

    @staticmethod
    def _request_id(value):
        if not isinstance(value, str) or not re.fullmatch('[A-Za-z0-9_-]{8,128}', value):
            raise HarnessError(tr('python.file_restore.80fe59ebf88e'), code='restore/invalid')

    def preview(self, session_id, turn, source_seq, through, *, _pending_request=None):
        session, events, operations = self._scope(session_id, turn, source_seq, through)
        result = {'session_id': session_id, 'turn': turn, 'source_seq': source_seq, 'through': through,
                  'path': operations[0]['path'], 'available': False, 'reason': None, 'action': 'none',
                  'patch': None, 'truncated': False, 'expected_version': None, 'latest_receipt': None}
        notes = self._notes(session, turn, source_seq)
        if notes:
            result['latest_receipt'] = self._receipt(notes, notes[-1].data['request_id'])
            if result['latest_receipt']['path'] != result['path']:
                raise HarnessError(tr('python.file_restore.c7b216a1b0ab'), code='restore/corrupt')
        def blocked(reason):
            return {**result, 'reason': reason}
        if not any(event.type == 'turn/end' for event in events):
            return blocked(tr('python.file_restore.275c5005cafe'))
        net = _net_file(session, operations, _uncertain(events))
        if not net['available']:
            return blocked(net['reason'])
        if net['kind'] == 'unchanged':
            return blocked(tr('python.file_restore.e56a2ab07e11'))
        if session.persistence_error or self.harness.runtime.storage_error or self.harness.runtime.closed:
            return blocked(tr('python.file_restore.6990f8a19718'))
        policy = self.harness.permissions.snapshot(session_id)
        if policy['permission_policy'] == 'read_only':
            return blocked(tr('python.file_restore.f43cb96d563d'))
        if self.harness.plan.is_active_session_id(session_id):
            return blocked(tr('python.file_restore.2b5345e55816'))
        busy = self._busy()
        if busy:
            return blocked(busy)
        if result['latest_receipt'] and result['latest_receipt']['status'] == 'unknown' and result['latest_receipt']['request_id'] != _pending_request:
            return blocked(tr('python.file_restore.b06ff5028d30'))
        before = operations[0]['_display']['snapshot']['before']
        after = operations[-1]['_display']['snapshot']['after']
        try:
            target, raw, identity = self._target(session, result['path'])
            original = session.file_evidence.read(before)
        except OSError:
            return blocked(tr('python.file_restore.e07bfe651bc3'))
        except (ValueError, UnicodeError) as exc:
            return blocked(str(exc))
        if hashlib.sha256(raw).hexdigest() != after['sha256'] or len(raw) != after['bytes']:
            if before['exists'] and hashlib.sha256(raw).hexdigest() == before['sha256'] and len(raw) == before['bytes']:
                return blocked(tr('python.file_restore.0c7938dafa13'))
            return blocked(tr('python.file_restore.47a819f998bd'))
        action = 'restore' if before['exists'] else 'remove'
        diff = file_diff(raw.decode('utf-8'), original, target, changed=True)
        fingerprint = hashlib.sha256(json.dumps({'session': session_id, 'turn': turn, 'source': source_seq, 'through': through,
            'path': str(target), 'root': str(Path(session.header.cwd).resolve()), 'identity': identity,
            'before': dict(before), 'after': dict(after), 'policy': policy['version']}, sort_keys=True).encode()).hexdigest()
        return {**result, 'available': True, 'action': action, 'patch': diff['diff'], 'truncated': bool(diff.get('diff_truncated')), 'expected_version': fingerprint}

    async def apply(self, session_id, turn, source_seq, through, expected_version, request_id):
        owner = self.harness.runtime.claim_loop(asyncio.get_running_loop())
        if owner is not asyncio.get_running_loop():
            future = asyncio.run_coroutine_threadsafe(self.apply(session_id, turn, source_seq, through, expected_version, request_id), owner)
            return await asyncio.wrap_future(future)
        self._request_id(request_id)
        if not isinstance(expected_version, str) or not re.fullmatch('[0-9a-f]{64}', expected_version):
            raise HarnessError(tr('python.file_restore.38e399a21e5c'), code='restore/invalid')
        async with self._lock:
            session, _, operations = self._scope(session_id, turn, source_seq, through)
            notes = self._notes(session, turn, source_seq)
            prior = self._receipt(notes, request_id)
            if prior:
                prepared = next(event.data for event in notes if event.data.get('request_id') == request_id)
                if prepared['expected_version'] != expected_version or prepared['through'] != through:
                    raise HarnessError(tr('python.file_restore.1f80793208b1'), code='restore/request-conflict')
                return prior
            preview = self.preview(session_id, turn, source_seq, through)
            if not preview['available'] or preview['expected_version'] != expected_version:
                raise HarnessError(preview['reason'] or tr('python.file_restore.746c6f8a789e'), code='restore/changed')
            before = operations[0]['_display']['snapshot']['before']
            after = operations[-1]['_display']['snapshot']['after']
            await session.append('note', {'kind': 'file_restore_prepared', 'session_id': session_id, 'turn': turn,
                'source_seq': source_seq, 'through': through, 'request_id': request_id, 'expected_version': expected_version,
                'path': preview['path'], 'action': preview['action'], 'before': dict(before), 'after': dict(after), 'source': 'user'})
            # 追加可能通知异步监听器；结束后重新检查所有执行准入条件。
            status, message = 'rejected', tr('python.file_restore.554380946922')
            # 此内部复核仅忽略刚提交的待完成回执。
            checked = self._checked_after_prepare(session_id, turn, source_seq, through, request_id)
            if not checked['available']:
                message = tr('python.file_restore.e89bfe6220f0') + checked['reason']
            if checked['available'] and checked['expected_version'] == expected_version:
                try:
                    self._write(session, preview['path'], before, expected_version, session_id, turn, source_seq, through, request_id)
                    status, message = 'restored', tr('python.file_restore.7bb030903473') if before['exists'] else tr('python.file_restore.75c62836974a')
                except (OSError, ValueError, UnicodeError):
                    status, message = 'unknown', tr('python.file_restore.78619b6dbcd8')
                    # 原子替换失败后，只有原文件的完整标识仍匹配时，
                    # 才允许用户显式重试。
                    try:
                        unchanged = self._checked_after_prepare(session_id, turn, source_seq, through, request_id)
                        if unchanged['available'] and unchanged['expected_version'] == expected_version:
                            status, message = 'rejected', tr('python.file_restore.140c116d7593')
                    except HarnessError:
                        pass
            await session.append('note', {'kind': 'file_restore_result', 'session_id': session_id, 'turn': turn, 'source_seq': source_seq,
                'request_id': request_id, 'status': status, 'message': message})
            return self._receipt(self._notes(session, turn, source_seq), request_id)

    def _checked_after_prepare(self, session_id, turn, source_seq, through, request_id):
        # 仅同一次获准的 apply 调用可以绕过 preview 的待完成回执检查，
        # 没有任何接口向客户端开放此绕过能力。
        return self.preview(session_id, turn, source_seq, through, _pending_request=request_id)

    def _write(self, session, path, before, expected_version, session_id, turn, source_seq, through, request_id):
        target, _, info = self._target(session, path)
        original = session.file_evidence.read(before).encode('utf-8')
        temporary = None
        try:
            if before['exists']:
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.harness-restore-', delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(original); handle.flush(); os.fsync(handle.fileno())
                os.chmod(temporary, stat.S_IMODE(info[5]))
            current = self._checked_after_prepare(session_id, turn, source_seq, through, request_id)
            if not current['available'] or current['expected_version'] != expected_version:
                raise ValueError(tr('python.file_restore.a434fc02feda'))
            if before['exists']:
                os.replace(temporary, target)
            else:
                target.unlink()
            fs_observation.forget_target(target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
