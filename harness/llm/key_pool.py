"""OpenRouter API 密钥轮换池。

背景：按天限额的 key 一旦触发 429 / 超时 / 鉴权失败，当天基本就废了。
本组件维护一个**有序** key 列表和「当日不可用」标记，语义与用户约定一致：

1. 正常运行永远使用「当前可用」的 key（从 1 号开始）；
2. 调用报错时把当前 key 标记为不可用，立即切到下一把；
3. 只要新 key 可用就一直用它，绝不主动切回；
4. 全部 key 都报过错后重置所有标记，从 1 号重新开始
   （配合上层有限的轮换预算，不会死循环）；
5. 标记按「天」持久化到本地状态文件：进程重启当天仍然记得，
   跨天自动全部恢复。状态文件只存序号和 key 列表指纹，**不落 key 本身**。

另提供 :meth:`ApiKeyPool.rotate` 软轮换：连接/线路级瞬时故障（如流中途
idle timeout）时只把指针推进到下一把 key、不当日拉黑——换下一条线路重试
可能有收益，但故障未必是 key 本身的问题，不该浪费按日限额的额度。

适配器侧见 :class:`harness.llm.adapter.OpenRouterAdapter` 的 ``key_pool`` 参数。
"""

from __future__ import annotations

from harness.i18n import tr

from contextlib import nullcontext
from functools import wraps
import threading

from .key_pool_lock import state_lock

import asyncio
import logging

from ..core.cleanup import join_cleanup

import hashlib
import json
import re
import tempfile
from datetime import date
from pathlib import Path

#: key 形如 ``sk-or-v1-<hex>``；宽松匹配以兼容其他供应商的 sk- 前缀。
logger = logging.getLogger(__name__)

_KEY_TOKEN = re.compile(r"sk-[A-Za-z0-9_-]{8,}")


def parse_key_list(text: str) -> list[str]:
    """从一段文本里按顺序提取 API key（去重、保序）。

    跳过整行注释，再从每行提取首个 sk- 标记，不解析变量赋值语法：

    - 纯 key 一行：``sk-or-v1-...``
    - 带赋值包装的文本也可提取：``experimental_bearer_token = "sk-or-v1-..."``（变量名无所谓）
    - ``#`` 开头的整行注释直接跳过；行尾注释不影响提取。
    """
    keys: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _KEY_TOKEN.search(line)
        if match:
            keys.append(match.group(0))
    return list(dict.fromkeys(keys))


def _transaction(operation):
    """同步与异步入口在同一文件锁内读最新状态后修改，避免旧实例覆盖。"""
    @wraps(operation)
    def locked(self, *args, **kwargs):
        with self._state_lock:
            try:
                guard = state_lock(self._state_path) if self._state_path is not None else nullcontext()
                guard.__enter__()
            except OSError:
                self.persistence_error = True
                logger.warning(tr('feedback.8e14831a596a'))
                self._persistence_suspended = True
                try:
                    return operation(self, *args, **kwargs)
                finally:
                    self._persistence_suspended = False
            try:
                same_day = self._today == date.today()
                pending = set(self._failed) if self.persistence_error and same_day else set()
                if not same_day:
                    self._today = date.today()
                    self._failed.clear()
                    self._current = 0
                self._load()
                self._failed.update(pending)
                return operation(self, *args, **kwargs)
            finally:
                guard.__exit__(None, None, None)
    return locked


class ApiKeyPool:
    """有序 API key 池，带「当日不可用」标记与换天自愈。

    :param keys: 有序 key 列表，1 号在最前；空列表抛 :class:`ValueError`。
    :param state_path: 可选的状态文件路径（JSON）；保存失败提供诊断状态，
        状态只是优化，绝不拖垮主流程。
    """

    def __init__(self, keys: list[str], *, state_path: Path | str | None = None) -> None:
        normalized = [key.strip() for key in keys if key and key.strip()]
        if not normalized:
            raise ValueError(tr('python.key_pool.eb595d8d5725'))
        self._keys = list(dict.fromkeys(normalized))
        self._state_path = Path(state_path) if state_path else None
        self._today = date.today()
        self._failed: set[int] = set()
        self._current = 0
        self.persistence_error = False
        self._async_lock = asyncio.Lock()
        self._state_lock = threading.RLock()
        self._persistence_suspended = False
        try:
            with state_lock(self._state_path) if self._state_path is not None else nullcontext():
                self._load()
        except OSError:
            self.persistence_error = True
            logger.warning(tr('feedback.8e14831a596a'))

    # -- 只读信息 --------------------------------------------------------
    @property
    def size(self) -> int:
        return len(self._keys)

    def key_at(self, index: int) -> str:
        return self._keys[index]

    @_transaction
    def status(self) -> dict[str, object]:
        """诊断快照：当前槽位、当日不可用序号、状态日期。"""
        self._roll_day()
        return {
            "state_save_failed": self.persistence_error,
            "size": len(self._keys),
            "current": self._current,
            "failed": sorted(self._failed),
            "date": self._today.isoformat(),
        }

    # -- 核心语义 --------------------------------------------------------
    @_transaction
    def current(self) -> tuple[int, str]:
        """返回当前可用 key 的 ``(序号, key)``。

        全部不可用时按约定重置所有标记、回到 1 号。
        """
        self._roll_day()
        if len(self._failed) >= len(self._keys):
            self._failed.clear()
            self._current = 0
            self._save()
        if self._current in self._failed:
            self._current = self._next_available(self._current)
        return self._current, self._keys[self._current]

    @_transaction
    def mark_failed(self, index: int) -> None:
        """把 ``index`` 标记为当日不可用；若它是当前 key，切到下一把可用。"""
        if not 0 <= index < len(self._keys):
            return
        self._roll_day()
        self._failed.add(index)
        if index == self._current and len(self._failed) < len(self._keys):
            self._current = self._next_available(index)
        self._save()

    @_transaction
    def rotate(self) -> tuple[int, str]:
        """软轮换：把当前指针推进到下一把可用 key，但**不**标记当日不可用。

        用于「连接/线路级」瞬时故障（如流式传输中途的 idle timeout）：
        换一个账号线路重试可能有直接收益，但故障未必是 key 本身的问题，
        按日拉黑会让按日限额的 key 白白浪费一把。推进后的指针照常持久化，
        与「新 key 可用就一直用、绝不主动切回」的语义一致。
        """
        self._roll_day()
        self._current = self._next_available(self._current)
        self._save()
        return self._current, self._keys[self._current]

    async def acurrent(self) -> tuple[int, str]:
        """在线程中读取及更新轮换状态，不阻塞模型事件循环。"""
        return await self._async_call(self.current)

    async def amark_failed(self, index: int) -> None:
        """保存失败标记；取消时也等待已开始的保存结束。"""
        await self._async_call(self.mark_failed, index)

    async def arotate(self) -> tuple[int, str]:
        """串行保存软轮换，避免并发请求覆盖状态文件。"""
        return await self._async_call(self.rotate)

    async def _async_call(self, operation, *args):
        async with self._async_lock:
            return await join_cleanup(asyncio.create_task(asyncio.to_thread(operation, *args)))

    # -- 内部 ------------------------------------------------------------
    def _next_available(self, start: int) -> int:
        for offset in range(1, len(self._keys) + 1):
            candidate = (start + offset) % len(self._keys)
            if candidate not in self._failed:
                return candidate
        return 0  # 全部不可用，由 current() 的重置分支兜底

    def _roll_day(self) -> None:
        today = date.today()
        if today != self._today:
            self._today = today
            self._failed.clear()
            self._current = 0
            self._save()

    def _fingerprint(self) -> str:
        return hashlib.sha256("\n".join(self._keys).encode("utf-8")).hexdigest()[:16]

    def _load(self) -> None:
        if self._state_path is None:
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            raise ValueError(tr('llm.state.key_pool_invalid')) from None
        # 先校验当前完整格式；跨天或凭据变化只能使有效状态失效。
        invalid = lambda: ValueError(tr('llm.state.key_pool_invalid'))
        if (not isinstance(data, dict) or set(data) != {'version', 'keys', 'date', 'current', 'failed'}
                or type(data['version']) is not int or data['version'] != 1
                or not isinstance(data['keys'], str) or not re.fullmatch('[0-9a-f]{16}', data['keys'])
                or not isinstance(data['date'], str)
                or type(data['current']) is not int or data['current'] < 0
                or not isinstance(data['failed'], list)
                or any(type(index) is not int or index < 0 for index in data['failed'])
                or len(set(data['failed'])) != len(data['failed'])):
            raise invalid()
        try:
            saved_date = date.fromisoformat(data['date'])
        except ValueError:
            raise invalid() from None
        if saved_date.isoformat() != data['date']:
            raise invalid()
        if data['keys'] != self._fingerprint():
            return
        if data['current'] >= len(self._keys) or any(index >= len(self._keys) for index in data['failed']):
            raise invalid()
        if saved_date != self._today:
            return
        self._failed = set(data['failed'])
        self._current = data['current']

    def _save(self) -> None:
        if self._persistence_suspended:
            self.persistence_error = True
            return
        if self._state_path is None:
            return
        payload = json.dumps(
            {
                "version": 1,
                "keys": self._fingerprint(),
                "date": self._today.isoformat(),
                "current": self._current,
                "failed": sorted(self._failed),
            }
        )
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = None
            try:
                # 只拥有本次独占创建的临时文件，不覆盖其他保存者或用户文件。
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                        dir=self._state_path.parent, prefix='.' + self._state_path.name + '.',
                        suffix='.tmp', delete=False) as handle:
                    tmp = Path(handle.name)
                    handle.write(payload)
                tmp.replace(self._state_path)
            finally:
                if tmp is not None:
                    tmp.unlink(missing_ok=True)
            self.persistence_error = False
        except OSError:
            self.persistence_error = True
            logger.warning(tr('feedback.8e14831a596a'))


__all__ = ["ApiKeyPool", "parse_key_list"]
