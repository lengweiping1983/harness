"""可信本机引导的有界文件适配；不可变输入与原子更新的回执分别保存。"""
from __future__ import annotations

import json
import os
import stat
from itertools import islice

from ..core.errors import HarnessError
from ..session.store import _atomic_write
from .superagent_guidance import command_id, validate_command, validate_receipt
from .superagent_inbox import _error, _json


def read_file(path, maximum):
    """只读取有界普通文件；不跟随链接或等待管道，原件始终保留。"""
    if path.is_symlink() or path.is_junction():
        raise _error('corrupt')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0)
                         | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise _error('corrupt')
        raw = stream.read(maximum + 1)
    try:
        if len(raw) > maximum:
            raise ValueError
        return _json(raw.decode('utf-8'))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _error('corrupt') from exc


class GuidanceFiles:
    """文件只承载本机操作意图；真正受理及应用事实由协调器查原回执。"""

    def __init__(self, root, guidance):
        self.root, self.guidance = root, guidance
        self.cursor, self.count = 0, 0
        self.errors = {}
        self.selected = []
        self.seen = set()
        self.unscanned = False
        self.watch = set(guidance.identities())
        self.watch_cursor = 0
        self.published = {}
        for directory in (root, root / 'incoming', root / 'receipts'):
            directory.mkdir(exist_ok=True)
            if directory.is_symlink() or directory.is_junction() or not directory.is_dir():
                raise _error('conflict')

    def scan(self):
        directory = self.root / 'incoming'
        if directory.is_symlink() or directory.is_junction():
            raise _error('conflict')
        files = list(islice(directory.glob('*.json'), 1025))
        if len(files) > 1024:
            raise _error('file-capacity')
        files.sort()
        self.count = len(files)
        names = {path.name for path in files}
        self.seen.intersection_update(names)
        self.errors = {name: error for name, error in self.errors.items() if any(path.name == name for path in files)}
        offset = self.cursor % len(files) if files else 0
        selected = (files[offset:] + files[:offset])[:16]
        self.cursor = offset + len(selected)
        self.selected = []
        for path in selected:
            try:
                spec = validate_command(read_file(path, 524288))
                identity = command_id(self.guidance.instance_id, spec['request_id'])
                if path.name != identity + '.json':
                    raise _error('conflict')
                self.guidance.enqueue(spec)
                self.selected.append(identity)
                self.errors.pop(path.name, None)
            except HarnessError as exc:
                if exc.code not in ('superagent/invalid', 'superagent/corrupt', 'superagent/conflict', 'superagent/event-conflict'):
                    raise
                self.errors[path.name] = {'file': path.name, 'code': exc.code}
            self.seen.add(path.name)
        self.unscanned = bool(names - self.seen)

    def publish(self, *, final=False):
        directory = self.root / 'receipts'
        if directory.is_symlink() or directory.is_junction():
            raise _error('conflict')
        changed, self.guidance.changed = self.guidance.changed, set()
        pending = sorted(self.watch)
        offset = self.watch_cursor % len(pending) if pending else 0
        selected = (pending[offset:] + pending[:offset])[:16]
        self.watch_cursor = offset + len(selected)
        identities = self.guidance.identities() if final else sorted(set(self.selected) | changed | set(selected))
        for identity in identities:
            path = directory / (identity + '.json')
            receipt = validate_receipt(self.guidance.receipt(identity))
            raw = json.dumps(receipt, ensure_ascii=False, indent=2)
            if self.published.get(identity) != raw or not path.exists():
                if path.exists() or path.is_symlink():
                    original = validate_receipt(read_file(path, 65536))
                    if any(original[name] != receipt[name] for name in ('instance_id', 'id', 'request_id', 'mission_id', 'job_id', 'run_id', 'turn')):
                        raise _error('conflict')
                _atomic_write(path, raw)
                self.published[identity] = raw
            if receipt['state'] in ('pending', 'submitting') or receipt['application'] == 'pending':
                self.watch.add(identity)
            else:
                self.watch.discard(identity)

    def status(self):
        return {'input_errors': [self.errors[name] for name in sorted(self.errors)]}
