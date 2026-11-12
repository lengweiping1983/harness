"""按账号读取官方 App Server 的额度，不启动模型轮次或执行工具。"""
from __future__ import annotations

import asyncio
import json
import math
import os
import tempfile
import time
from dataclasses import dataclass

from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..i18n import tr
from ..llm.chatgpt_auth import ChatGPTAccountError
from ..tools.process_env import stdio_env
from ..tools.processes import start_process, stop_process


class QuotaError(ValueError):
    """只发布资源身份，不将子进程诊断或凭据写入产品错误。"""

    def __init__(self, key='ui.codexGateway.quotaInvalid'):
        self.key = key
        super().__init__(key)

    def __str__(self):
        return tr(self.key)


@dataclass(frozen=True)
class Window:
    used: float
    minutes: int
    resets: int

    def public(self):
        return {'used_percent': self.used, 'window_minutes': self.minutes, 'resets_at': self.resets}


@dataclass(frozen=True)
class Quota:
    windows: tuple[Window, ...]
    blocked: bool
    observed: float
    epoch: int

    @property
    def five_hour(self):
        return next(window for window in self.windows if window.minutes == 300)

    def fresh(self, monotonic, wall):
        return 0 <= monotonic - self.observed < 30 and all(window.resets > wall for window in self.windows)

    def available(self, threshold=95):
        return not self.blocked and self.five_hour.used < threshold and all(window.used < 100 for window in self.windows)


def parse_quota(result, *, epoch, monotonic=None):
    """窗口由长度识别，未知窗口和损坏百分比不能变成可用余额。"""
    try:
        if not isinstance(result, dict):
            raise QuotaError()
        if result.get('rateLimitsByLimitId') is not None:
            buckets = result['rateLimitsByLimitId']
            if not isinstance(buckets, dict):
                raise QuotaError()
            bucket = buckets.get('codex')
        else:
            bucket = result.get('rateLimits')
        if not isinstance(bucket, dict) or bucket.get('limitId') not in (None, 'codex'):
            raise QuotaError()
        windows = []
        for name in ('primary', 'secondary'):
            value = bucket.get(name)
            if value is None:
                continue
            if not isinstance(value, dict):
                raise QuotaError()
            used, minutes, resets = (value.get(key) for key in ('usedPercent', 'windowDurationMins', 'resetsAt'))
            if (type(used) not in (int, float) or not math.isfinite(used) or used < 0
                    or type(minutes) is not int or minutes <= 0
                    or type(resets) is not int or not 0 < resets <= 2**53 - 1):
                raise QuotaError()
            windows.append(Window(used, minutes, resets))
        if sum(window.minutes == 300 for window in windows) != 1:
            raise QuotaError('ui.codexGateway.windowUnknown')
        spend = bucket.get('spendControlReached')
        if spend is not None and type(spend) is not bool:
            raise QuotaError()
        reached = bucket.get('rateLimitReachedType')
        if reached is not None and (not isinstance(reached, str) or not reached):
            raise QuotaError()
        return Quota(tuple(windows), spend is True or reached is not None,
                     time.monotonic() if monotonic is None else monotonic, epoch)
    except (TypeError, KeyError, OverflowError):
        raise QuotaError() from None


class AppServerQuotaReader:
    """每次只读查询使用隔离进程和临时 CODEX_HOME，不复用桌面认证缓存。"""

    def __init__(self, executable='codex'):
        self.executable = executable

    async def __call__(self, account, epoch):
        errors = []
        process = None
        with tempfile.TemporaryDirectory(prefix='harness-codex-quota-') as directory:
            env = stdio_env({'CODEX_HOME': directory})
            for key in tuple(env):
                if key.startswith(('OPENAI_', 'CODEX_')) and key != 'CODEX_HOME':
                    del env[key]
            try:
                process = await start_process(lambda: asyncio.create_subprocess_exec(
                    self.executable, 'app-server', '-c', 'cli_auth_credentials_store="ephemeral"',
                    cwd=directory, env=env, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                    limit=2 * 1024 * 1024, start_new_session=os.name != 'nt'), stop=stop_process)

                async def send(value):
                    process.stdin.write((json.dumps(value, ensure_ascii=False) + '\n').encode('utf-8'))
                    await process.stdin.drain()

                async def tokens():
                    headers = await asyncio.to_thread(account.headers, epoch)
                    return {'accessToken': headers['Authorization'].removeprefix('Bearer '),
                            'chatgptAccountId': headers['ChatGPT-Account-Id']}

                async def request(identity, method, params):
                    await send({'id': identity, 'method': method, 'params': params})
                    while True:
                        line = await process.stdout.readline()
                        if not line:
                            raise QuotaError('ui.codexGateway.quotaUnavailable')
                        value = json.loads(line.decode('utf-8'))
                        if not isinstance(value, dict):
                            raise QuotaError()
                        if value.get('method') == 'account/chatgptAuthTokens/refresh' and 'id' in value:
                            previous = value.get('params', {}).get('previousAccountId')
                            fresh = await tokens()
                            if previous is not None and previous != fresh['chatgptAccountId']:
                                raise QuotaError()
                            await send({'id': value['id'], 'result': fresh})
                        elif type(value.get('id')) is int and value['id'] == identity:
                            if 'error' in value or 'result' not in value:
                                raise QuotaError('ui.codexGateway.quotaUnavailable')
                            return value['result']

                async with asyncio.timeout(30):
                    await request(1, 'initialize', {'clientInfo': {'name': 'harness-quota', 'version': '1.0.0'},
                                                   'capabilities': {'experimentalApi': True}})
                    await send({'method': 'initialized', 'params': {}})
                    await request(2, 'account/login/start', {'type': 'chatgptAuthTokens', **await tokens()})
                    result = await request(3, 'account/rateLimits/read', {'excludeResetCreditDetails': True})
                    quota = parse_quota(result, epoch=epoch)
            except ChatGPTAccountError as exc:
                errors.append(exc)
            except (OSError, ValueError, TimeoutError) as exc:
                errors.append(exc if isinstance(exc, QuotaError) else QuotaError('ui.codexGateway.quotaUnavailable'))
            except BaseException as exc:
                errors.append(exc)
            finally:
                if process is not None:
                    try:
                        await join_cleanup(asyncio.create_task(stop_process(process)))
                    except BaseException as exc:
                        errors.append(exc)
            raise_cleanup_errors(errors)
            return quota
