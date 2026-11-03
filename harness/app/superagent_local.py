"""可信本机目标提交和只读状态入口，不构造模型宿主或读取凭据。"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import uuid
from pathlib import Path

from ..core.cleanup import raise_cleanup_errors
from ..core.errors import HarnessError
from .superagent_inbox import _error, _json, _text
from .superagent_service import read_config


def submit_local(config_path, text, *, source_id=None, actor_id=None, thread_id=None, request_id=None):
    """同目录准备后独占公开；同一请求身份仅允许完全一致的原文。"""
    spec = read_config(config_path)
    _text(text, 'text', limit=65536)
    remote = {spec[name]['source_id'] for name in ('github', 'feishu') if name in spec}
    sources = {name: value for name, value in spec['sources'].items()
               if name not in remote and value['mode'] != 'ignore'
               and any(name in profile['sources'] for profile in spec['profiles'].values())}

    def choose(value, options, field):
        if value is None and len(options) == 1:
            value = next(iter(options))
        if not isinstance(value, str) or value not in options:
            raise _error('invalid', field=field)
        return value

    source_id = choose(source_id, sources, 'source_id')
    actor_id = choose(actor_id, sources[source_id]['actors'], 'actor_id')
    thread_id = choose(thread_id, sources[source_id]['threads'], 'thread_id')
    request_id = uuid.uuid4().hex if request_id is None else _text(request_id, 'request_id')
    identity = hashlib.sha256(json.dumps([spec['instance_id'], source_id, request_id], ensure_ascii=False).encode('utf-8')).hexdigest()
    value = {'source_id': source_id, 'event_id': 'local_' + identity, 'actor_id': actor_id,
             'thread_ids': [thread_id], 'text': text, 'human': True, 'explicit': True}
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
    directory = Path(spec['state_dir']) / 'incoming'
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink() or directory.is_junction() or not directory.is_dir():
        raise _error('conflict')
    target = directory / (identity + '.json')
    duplicate = _publish(directory, target.name, raw)
    return {'request_id': request_id, 'event_id': value['event_id'], 'path': str(target),
            'duplicate': duplicate, 'queued': True}


def _publish(directory, name, raw):
    """目录句柄绑定准备、发布与收尾；无句柄平台按文件身份保护清理。"""
    owner = None
    if all(operation in os.supports_dir_fd for operation in (os.open, os.unlink, os.link, os.stat)):
        owner = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        temporary = directory / ('.submit-' + uuid.uuid4().hex + '.tmp')
        try:
            descriptor = os.open(temporary.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=owner)
        except BaseException:
            os.close(owner)
            raise
    else:
        descriptor, filename = tempfile.mkstemp(prefix='.submit-', suffix='.tmp', dir=directory)
        temporary = Path(filename)
    identity = os.fstat(descriptor)
    duplicate = False
    errors = []
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            if directory.is_symlink() or directory.is_junction():
                raise _error('conflict')
            if owner is not None:
                current, opened = directory.stat(), os.fstat(owner)
                if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                    raise _error('conflict')
                os.link(temporary.name, name, src_dir_fd=owner, dst_dir_fd=owner, follow_symlinks=False)
            else:
                os.link(temporary, directory / name)
        except FileExistsError:
            try:
                flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
                descriptor = os.open(name, flags, dir_fd=owner) if owner is not None else os.open(directory / name, flags)
                with os.fdopen(descriptor, 'rb') as stream:
                    if (directory / name).is_symlink() or not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise _error('conflict')
                    if stream.read(len(raw) + 1) != raw:
                        raise _error('event-conflict')
            except OSError as exc:
                raise _error('conflict') from exc
            duplicate = True
    except BaseException as exc:
        errors.append(exc)
    finally:
        try:
            try:
                current = (os.stat(temporary.name, dir_fd=owner, follow_symlinks=False)
                           if owner is not None else temporary.stat(follow_symlinks=False))
            except FileNotFoundError:
                pass
            else:
                if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                    if owner is not None:
                        os.unlink(temporary.name, dir_fd=owner)
                    else:
                        temporary.unlink()
        except BaseException as cleanup:
            errors.append(cleanup)
        finally:
            if owner is not None:
                try:
                    os.close(owner)
                except BaseException as cleanup:
                    errors.append(cleanup)
    raise_cleanup_errors(errors)
    return duplicate


def read_status(config_path):
    """读取最后投影，不将旧快照视作进程存活或重新启动运行。"""
    spec = read_config(config_path)
    path = Path(spec['state_dir']) / 'status.json'
    if not path.exists():
        raise _error('status-unavailable')
    if path.is_symlink():
        raise _error('corrupt')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0)
                         | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise _error('corrupt')
        raw = stream.read(2097153)
    try:
        value = _json(raw.decode('utf-8'))
        if (len(raw) > 2097152 or not isinstance(value, dict)
                or value.get('instance_id') != spec['instance_id'] or value.get('runtime_state') not in ('running', 'stopping', 'stopped', 'failed')):
            raise ValueError
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise _error('corrupt') from exc


def _guidance_target(status, job_id, remote):
    """仅从完整合法的当前投影选目标；截断不能作为全局唯一的依据。"""
    from .superagent_inbox import _names
    from .superagent_mission_store import JOB_STATES, MISSION_STATES, TERMINAL
    targets = []
    try:
        missions = status['missions']
        if not isinstance(missions, dict) or type(missions['has_more']) is not bool or not isinstance(missions['recent'], list):
            raise ValueError
        for mission in missions['recent']:
            if (not isinstance(mission, dict) or mission['state'] not in MISSION_STATES
                    or not isinstance(mission['jobs'], list) or type(mission['revision']) is not int or mission['revision'] < 1):
                raise ValueError
            for key in ('id', 'source_id'):
                _text(mission[key], key)
            _names(mission['thread_ids'], 'thread_ids', nonempty=True)
            for job in mission['jobs']:
                if not isinstance(job, dict) or job['role'] not in ('plan', 'work', 'integrate', 'review') or job['state'] not in JOB_STATES:
                    raise ValueError
                _text(job['id'], 'job_id')
                if 'scheduling_halted' in job and type(job['scheduling_halted']) is not bool:
                    raise ValueError
                progress = job.get('run')
                if progress is not None:
                    if not isinstance(progress, dict) or not isinstance(progress['status'], str):
                        raise ValueError
                    _text(progress['id'], 'run_id')
                    if progress['turn'] is not None and (type(progress['turn']) is not int or not 1 <= progress['turn'] <= 2**53 - 1):
                        raise ValueError
                if (job['role'] in ('work', 'integrate') and job['state'] == 'accepted' and progress
                        and progress['status'] == 'running' and progress['turn'] is not None
                        and mission['state'] not in TERMINAL
                        and not job.get('scheduling_halted') and mission['source_id'] not in remote
                        and (job_id is None or job['id'] == job_id)):
                    if job['run_id'] != progress['id']:
                        raise ValueError
                    targets.append((mission, job))
    except (ValueError, TypeError, KeyError, HarnessError) as exc:
        raise _error('corrupt') from exc
    if len(targets) != 1 or job_id is None and missions['has_more']:
        raise _error('guidance-unavailable')
    return targets[0]


def steer_local(config_path, text, *, job_id=None, source_id=None, actor_id=None, thread_id=None, request_id=None):
    """绑定最后投影中的原运行；只排队，不猜测进程存活或把迟到输入投给新轮次。"""
    from .superagent_guidance import command_id, validate_command
    from .superagent_guidance_files import read_file
    spec = read_config(config_path)
    _text(text, 'text', limit=65536)
    request_id = uuid.uuid4().hex if request_id is None else _text(request_id, 'request_id')
    identity = command_id(spec['instance_id'], request_id)
    directory = Path(spec['state_dir']) / 'guidance' / 'incoming'
    if any(path.is_symlink() or path.is_junction() for path in (directory.parent, directory)):
        raise _error('conflict')
    target = directory / (identity + '.json')
    def queued(value, duplicate):
        return {'request_id': request_id, 'job_id': value['job_id'], 'run_id': value['run_id'], 'turn': value['turn'],
                'path': str(target), 'duplicate': duplicate, 'queued': True}
    if target.exists() or target.is_symlink():
        original = validate_command(read_file(target, 524288))
        if (original['instance_id'] != spec['instance_id'] or original['request_id'] != request_id or original['text'] != text
                or any(value is not None and original[key] != value for key, value in
                       (('job_id', job_id), ('source_id', source_id), ('actor_id', actor_id), ('thread_id', thread_id)))):
            raise _error('event-conflict')
        return queued(original, True)
    status = read_status(config_path)
    if 'autonomy' not in spec or status['runtime_state'] != 'running':
        raise _error('guidance-unavailable')
    remote = {spec[name]['source_id'] for name in ('github', 'feishu') if name in spec}
    mission, job = _guidance_target(status, job_id, remote)
    source_id = mission['source_id'] if source_id is None else source_id
    if source_id != mission['source_id'] or source_id not in spec['sources']:
        raise _error('guidance-unauthorized')
    source = spec['sources'][source_id]
    if source['mode'] == 'ignore':
        raise _error('guidance-unauthorized')
    def choose(value, options, field):
        if value is None and len(options) == 1:
            value = next(iter(options))
        if not isinstance(value, str) or value not in options:
            raise _error('invalid', field=field)
        return value
    actor_id = choose(actor_id, source['actors'], 'actor_id')
    thread_id = choose(thread_id, set(source['threads']) & set(mission['thread_ids']), 'thread_id')
    value = validate_command({'version': 1, 'instance_id': spec['instance_id'], 'request_id': request_id,
        'mission_id': mission['id'], 'job_id': job['id'], 'run_id': job['run']['id'], 'turn': job['run']['turn'],
        'source_id': source_id, 'revision': mission['revision'], 'actor_id': actor_id, 'thread_id': thread_id, 'text': text})
    directory.mkdir(parents=True, exist_ok=True)
    if any(path.is_symlink() or path.is_junction() for path in (directory.parent, directory)):
        raise _error('conflict')
    duplicate = _publish(directory, identity + '.json', (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8'))
    return queued(value, duplicate)


def read_guidance_receipt(config_path, request_id):
    """只读单条持久回执；排队、原生受理与应用分别呈现。"""
    from .superagent_guidance import command_id, validate_receipt
    from .superagent_guidance_files import read_file
    spec = read_config(config_path)
    _text(request_id, 'request_id')
    identity = command_id(spec['instance_id'], request_id)
    root = Path(spec['state_dir']) / 'guidance'
    if any(path.is_symlink() or path.is_junction() for path in (root, root / 'receipts')):
        raise _error('conflict')
    path = root / 'receipts' / (identity + '.json')
    if not path.exists():
        raise _error('status-unavailable')
    value = validate_receipt(read_file(path, 65536))
    if value['instance_id'] != spec['instance_id'] or value['id'] != identity or value['request_id'] != request_id:
        raise _error('corrupt')
    return value
