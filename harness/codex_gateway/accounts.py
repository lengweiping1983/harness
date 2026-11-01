"""订阅账号的独立存储、额度快照和请求准入。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..llm.chatgpt_auth import ChatGPTAccount, ChatGPTAccountError, claims
from ..llm.key_pool_lock import state_lock
from ..llm.routes import atomic_json
from .quota import AppServerQuotaReader, Quota, QuotaError
from .protocol import decode_object


def account_identity(account):
    """工作区和用户共同确定身份，不把同一工作区的不同用户合并。"""
    with account.lock:
        credential = account.credentials
        if credential is None:
            return None
        subject = claims(credential['access']).get('sub')
        user = subject if isinstance(subject, str) and subject else credential['email']
        return hashlib.sha256((credential['account_id'] + '\0' + user).encode('utf-8')).hexdigest()


@dataclass
class Slot:
    id: str
    account: ChatGPTAccount = field(repr=False)
    enabled: bool = True
    quota: Quota | None = None
    error_key: str | None = None
    checked: float = float('-inf')
    checked_wall: float = 0
    in_use: bool = False
    rejected_until: float = 0
    auth_failed_epoch: int | None = None


class AccountPool:
    """宿主拥有一个调度实例；磁盘更新通过稳定旁路锁和版本复核。"""

    def __init__(self, home: Path, default: ChatGPTAccount, *, reader=None, clock=time.monotonic, wall=time.time):
        self.home = Path(home)
        self.path = self.home / 'codex-gateway.json'
        self.lock = threading.RLock()
        self.clock, self.wall = clock, wall
        self.reader = reader or AppServerQuotaReader()
        self.refresh_lock = asyncio.Lock()
        self.slots = {'primary': Slot('primary', default)}
        self.enabled = False
        self.current_account = ''
        self.auto_rotate = True
        self.threshold = 95
        self._key = ''
        self._raw = None
        self.closed = False
        self.waiting = 0
        self._load()

    def _load(self):
        try:
            with self.path.open('rb') as stream:
                raw = stream.read(1024 * 1024 + 1)
        except FileNotFoundError:
            return
        try:
            if len(raw) > 1024 * 1024:
                raise ValueError()
            value = decode_object(raw)
            if (not isinstance(value, dict) or set(value) != {'version', 'enabled', 'threshold', 'key', 'accounts', 'current_account', 'auto_rotate'}
                    or type(value['version']) is not int or value['version'] != 2
                    or type(value['enabled']) is not bool or type(value['threshold']) is not int
                    or not 1 <= value['threshold'] <= 99 or not isinstance(value['key'], str)
                    or re.fullmatch(r'[A-Za-z0-9_-]{43}', value['key']) is None
                    or not isinstance(value['current_account'], str) or type(value['auto_rotate']) is not bool
                    or value['current_account'] and value['current_account'] not in value['accounts']
                    or not isinstance(value['accounts'], dict) or 'primary' not in value['accounts']
                    or len(value['accounts']) > 32):
                raise ValueError()
            for identity, enabled in value['accounts'].items():
                if type(enabled) is not bool or identity != 'primary' and re.fullmatch('[a-f0-9]{32}', identity) is None:
                    raise ValueError()
                if identity == 'primary':
                    self.slots[identity].enabled = enabled
                else:
                    self.slots[identity] = Slot(identity, ChatGPTAccount(self.home / 'codex-accounts' / identity), enabled)
            self.enabled, self.threshold, self._key = value['enabled'], value['threshold'], value['key']
            self.current_account, self.auto_rotate = value['current_account'], value['auto_rotate']
            self._raw = raw
        except (ValueError, TypeError, UnicodeError):
            raise QuotaError('ui.codexGateway.stateInvalid') from None

    def _save(self, *, accounts=None, enabled=None, current_account=None, auto_rotate=None):
        """保存失败不发布新配置；外部写入不会被旧内存覆盖。"""
        with state_lock(self.path):
            try:
                with self.path.open('rb') as stream:
                    actual = stream.read(1024 * 1024 + 1)
            except FileNotFoundError:
                actual = None
            if actual != self._raw:
                raise QuotaError('ui.codexGateway.stateChanged')
            key = self._key or secrets.token_urlsafe(32)
            rows = accounts if accounts is not None else {identity: slot.enabled for identity, slot in self.slots.items()}
            value = {'version': 2, 'current_account': self.current_account if current_account is None else current_account,
                     'auto_rotate': self.auto_rotate if auto_rotate is None else auto_rotate, 'enabled': self.enabled if enabled is None else enabled,
                     'threshold': self.threshold, 'key': key, 'accounts': rows}
            atomic_json(self.path, value)
            self._raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
            self._key = key

    def configure(self, enabled):
        if type(enabled) is not bool:
            raise QuotaError('ui.codexGateway.invalidRequest')
        with self.lock:
            if self.closed:
                raise QuotaError('ui.codexGateway.closed')
            self._save(enabled=enabled)
            self.enabled = enabled
        return self.status()

    def create(self):
        with self.lock:
            if self.closed or len(self.slots) >= 32:
                raise QuotaError('ui.codexGateway.accountLimit')
            identity = uuid.uuid4().hex
            slot = Slot(identity, ChatGPTAccount(self.home / 'codex-accounts' / identity))
            rows = {key: item.enabled for key, item in self.slots.items()}
            self._save(accounts={**rows, identity: True})
            self.slots[identity] = slot
            return identity

    def slot(self, identity):
        with self.lock:
            if not isinstance(identity, str) or identity not in self.slots:
                raise QuotaError('ui.codexGateway.accountMissing')
            return self.slots[identity]

    def set_enabled(self, identity, enabled):
        if type(enabled) is not bool:
            raise QuotaError('ui.codexGateway.invalidRequest')
        with self.lock:
            slot = self.slot(identity)
            if slot.in_use:
                raise QuotaError('ui.codexGateway.accountBusy')
            rows = {key: item.enabled for key, item in self.slots.items()}
            self._save(accounts={**rows, identity: enabled})
            slot.enabled = enabled
        return self.status()

    def login(self, identity):
        with self.lock:
            slot = self.slot(identity)
            if self.closed or slot.in_use or any(item.account.pending for item in self.slots.values()):
                raise QuotaError('ui.codexGateway.accountBusy')
            result = slot.account.login()
            slot.quota, slot.error_key = None, None
            slot.checked = float('-inf')
            return {**self.status(), 'authorization_url': result['authorization_url']}

    def cancel_login(self, identity):
        with self.lock:
            self.slot(identity).account.cancel_login()
        return self.status()

    def remove(self, identity):
        with self.lock:
            slot = self.slot(identity)
            if identity == 'primary' or slot.in_use or slot.account.pending:
                raise QuotaError('ui.codexGateway.accountBusy')
            # 先明确退出账号；索引写入失败仍保留已退出状态，不重放退出。
            slot.account.logout()
            self._save(accounts={key: item.enabled for key, item in self.slots.items() if key != identity}, current_account='' if self.current_account == identity else self.current_account)
            if self.current_account == identity:
                self.current_account = ''
            del self.slots[identity]
        return self.status()

    def select(self, current_account, auto_rotate):
        """当前账号与自动轮换独立于本机网关启用状态。"""
        with self.lock:
            if type(auto_rotate) is not bool or not isinstance(current_account, str):
                raise QuotaError('ui.codexGateway.invalidRequest')
            if current_account:
                slot = self.slot(current_account)
                if not slot.enabled or not slot.account.logged_in:
                    raise QuotaError('ui.codexGateway.noAccount')
            self._save(current_account=current_account, auto_rotate=auto_rotate)
            self.current_account, self.auto_rotate = current_account, auto_rotate
        return self.status()

    def authenticate(self, authorization):
        with self.lock:
            return bool(self.enabled and not self.closed and self._key
                        and isinstance(authorization, str)
                        and secrets.compare_digest(authorization.encode('utf-8'), ('Bearer ' + self._key).encode('utf-8')))

    def credential(self):
        """只由本机命令辅助程序消费，不放入 HTTP 状态。"""
        with self.lock:
            if not self.enabled or not self._key:
                raise QuotaError('ui.codexGateway.disabled')
            return self._key

    def _unique(self):
        seen = set()
        rows = []
        for slot in sorted(self.slots.values(), key=lambda item: (item.id != 'primary', item.id)):
            identity = account_identity(slot.account)
            duplicate = identity is not None and identity in seen
            if identity is not None and slot.enabled:
                seen.add(identity)
            rows.append((slot, duplicate))
        return rows

    def status(self):
        with self.lock:
            now, wall = self.clock(), self.wall()
            rows = []
            for slot, duplicate in self._unique():
                status = slot.account.status()
                quota = slot.quota
                fresh = quota is not None and quota.epoch == slot.account.epoch and quota.fresh(now, wall)
                rows.append({'id': slot.id, 'enabled': slot.enabled, 'logged_in': status['logged_in'],
                             'email': status['email'], 'pending': status['pending'], 'duplicate': duplicate,
                             'in_use': slot.in_use, 'fresh': fresh,
                             'error_key': slot.error_key, 'auth_error': status['error'],
                             'windows': [window.public() for window in quota.windows] if quota else [],
                             'available': bool(slot.enabled and status['logged_in'] and not status['pending']
                                               and slot.auth_failed_epoch != slot.account.epoch and not duplicate and fresh
                                               and slot.rejected_until <= wall and quota.available(self.threshold))})
            return {'enabled': self.enabled, 'current_account': self.current_account, 'auto_rotate': self.auto_rotate, 'threshold': self.threshold, 'waiting': self.waiting, 'accounts': rows}

    async def refresh(self, *, force=False):
        async with self.refresh_lock:
            with self.lock:
                if self.closed:
                    raise QuotaError('ui.codexGateway.closed')
                rows = [slot for slot, duplicate in self._unique()
                        if slot.enabled and slot.account.logged_in and not slot.account.pending and not duplicate
                        and slot.auth_failed_epoch != slot.account.epoch
                        and (force or self.clock() - slot.checked >= 30
                             or slot.quota is not None and any(
                                 slot.checked_wall < window.resets <= self.wall() for window in slot.quota.windows))]

            async def read(slot):
                epoch = slot.account.epoch
                try:
                    quota = await self.reader(slot.account, epoch)
                    error = None
                except (QuotaError, ChatGPTAccountError) as exc:
                    quota, error = None, exc.key if isinstance(exc, QuotaError) else 'ui.codexGateway.authUnavailable'
                with self.lock:
                    if self.slots.get(slot.id) is slot and epoch == slot.account.epoch and not self.closed:
                        slot.quota, slot.error_key, slot.checked = quota, error, self.clock()
                        slot.checked_wall = self.wall()

            async with asyncio.TaskGroup() as group:
                for slot in rows:
                    group.create_task(read(slot))
        return self.status()

    @asynccontextmanager
    async def acquire(self, model, *, exclude=frozenset(), harness=False):
        """每次请求租用一个身份；等待者取消不释放其他请求的租约。"""
        slot = None
        with self.lock:
            self.waiting += 1
        try:
            while slot is None:
                with self.lock:
                    if self.closed or not harness and not self.enabled:
                        raise QuotaError('ui.codexGateway.disabled')
                await self.refresh()
                with self.lock:
                    if self.closed or not harness and not self.enabled:
                        raise QuotaError('ui.codexGateway.disabled')
                    candidates = []
                    recoverable = False
                    for item, duplicate in self._unique():
                        if (harness and not self.auto_rotate and self.current_account and item.id != self.current_account
                                or not item.enabled or duplicate or item.id in exclude or not item.account.logged_in
                                or item.account.pending or item.auth_failed_epoch == item.account.epoch
                                or not any(row['id'] == 'chatgpt/' + model for row in item.account.status()['models'])):
                            continue
                        quota = item.quota
                        if quota is None or quota.epoch != item.account.epoch:
                            continue
                        recoverable = True
                        if (not item.in_use and item.rejected_until <= self.wall()
                                and quota.fresh(self.clock(), self.wall()) and quota.available(self.threshold)):
                            candidates.append(item)
                    if candidates:
                        slot = min(candidates, key=lambda item: (
                            item.id != self.current_account if harness else False, item.quota.five_hour.resets, item.quota.five_hour.used, item.id))
                        if harness and slot.id != self.current_account:
                            self._save(current_account=slot.id)
                            self.current_account = slot.id
                        slot.in_use = True
                    elif not recoverable or harness and not any(item.in_use for item, _ in self._unique()):
                        raise QuotaError('ui.codexGateway.noAccount')
                if slot is None:
                    await asyncio.sleep(.25)
            yield slot, slot.account.epoch
        finally:
            with self.lock:
                self.waiting -= 1
                if slot is not None:
                    slot.in_use = False
                    # 下一次请求必须重新读取刚被消耗的账号，不沿用请求前快照。
                    slot.quota = None
                    slot.checked = float('-inf')

    def rejected(self, slot, epoch, status, retry_after=None):
        """仅依据上游明确的 HTTP 拒绝阻断账号，不推断未知请求已经撤回。"""
        with self.lock:
            if self.slots.get(slot.id) is not slot or slot.account.epoch != epoch:
                return
            if status == 401:
                slot.auth_failed_epoch = epoch
                slot.error_key = 'ui.codexGateway.authUnavailable'
            elif status == 429:
                delay = retry_after if type(retry_after) in (int, float) and 0 <= retry_after <= 86400 else 30
                slot.rejected_until = self.wall() + max(30, delay)
            slot.quota = None
            slot.checked = float('-inf')

    async def close(self):
        with self.lock:
            self.closed = True
            rows = list(self.slots.values())
        errors = []
        for slot in rows:
            try:
                await join_cleanup(asyncio.create_task(asyncio.to_thread(slot.account.cancel_login)))
            except BaseException as exc:
                errors.append(exc)
        raise_cleanup_errors(errors)
