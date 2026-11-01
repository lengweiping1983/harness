"""本地用户、项目与会话记忆的作用域绑定和有界注入。"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from harness.i18n import tr
from ..core.errors import HarnessError
from .memory import ProjectMemory
from .memory_context import MemoryContext, select_context
from .memory_search import rank
from .memory_sources import SourceChecker

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MemoryLayerContext:
    """单层选择的状态和诊断，不携带底层异常消息。"""

    scope: str
    status: str
    budget: int
    recall: MemoryContext | None = None
    error_code: str = ''


@dataclass(frozen=True, slots=True)
class LayeredMemoryContext:
    """三层同次计算的注入与解释，不代表模型已经消费这些资料。"""

    text: str
    budget: int
    layers: tuple[MemoryLayerContext, ...]

    def explain(self) -> dict:
        """只返回身份和容量数据，正文由现有记忆读取接口提供。"""
        return {'budget': self.budget, 'used_chars': len(self.text), 'layers': [
            {'scope': layer.scope, 'status': layer.status, 'budget': layer.budget,
             'recall': layer.recall.explain() if layer.recall is not None else None,
             'error_code': layer.error_code} for layer in self.layers]}


class BoundMemory(ProjectMemory):
    """由宿主固定文件位置，调用者不能通过 cwd 改变记忆归属。"""

    def __init__(self, path: Path, *, context_key: str, format: str) -> None:
        super().__init__()
        self._path = path.absolute()
        self.context_key = context_key
        self.format = format
        self.check_sources = context_key != 'python.memory.user_context'

    def path(self, cwd: str | Path) -> Path:
        # 绑定后出现的链接不能把后续读写重定向到其他作用域。
        if self._path.resolve() != self._path:
            raise HarnessError(tr("python.memory.unsafe"), code="memory/unsafe-path")
        return self._path


class LayeredMemory:
    """保留项目库协议，新层独立存储，不迁移任何已有用户文件。"""

    def __init__(self, home: Path, project: ProjectMemory) -> None:
        self.home = home.resolve()
        self.project = project
        self.user = BoundMemory(self.home / 'memory' / 'user.sqlite3',
                                context_key='python.memory.user_context', format='harness-user-memory-v1')

    def store(self, scope: str, *, session_id: str = '') -> ProjectMemory:
        """会话身份只能由可信调用方传入，路径使用摘要避免穿越。"""
        if scope == 'project':
            return self.project
        if scope == 'user':
            return self.user
        if scope != 'session' or not isinstance(session_id, str) or not session_id.strip():
            raise HarnessError(tr('python.memory.invalid'), code='memory/invalid')
        digest = hashlib.sha256(session_id.encode('utf-8')).hexdigest()
        return BoundMemory(self.home / 'memory' / 'sessions' / (digest + '.sqlite3'),
                           context_key='python.memory.session_context', format='harness-session-memory-v1')

    def context(self, cwd: str | Path, *, session_id: str, query: str = '', budget: int = 8000) -> str:
        """三层保留份额并共享空闲容量；单层损坏不阻断健康层。"""
        return self.preview(cwd, session_id=session_id, query=query, budget=budget).text

    def preview(self, cwd: str | Path, *, session_id: str, query: str = '', budget: int = 8000) -> LayeredMemoryContext:
        """按与正式注入相同的路径提供可解释快照，不改变任何存储。"""
        if type(budget) is not int or budget < 0:
            raise HarnessError(tr('python.memory.invalid'), code='memory/invalid')
        chunks: list[str] = []
        layers: list[MemoryLayerContext] = []
        scopes = ('user', 'project', 'session')
        limits = [budget // 4, budget // 2, budget - budget // 4 - budget // 2]
        stores, snapshots, states, codes = {}, {}, {}, {}
        active = []
        root = cwd or str(self.home)
        # 各库只读取一次；分配与正式选择复用相同资料，不能在试算后重读新版本。
        for index, scope in enumerate(scopes):
            if scope == 'project' and not cwd or scope == 'session' and not session_id:
                states[scope] = 'unbound'
                continue
            try:
                store = self.store(scope, session_id=session_id)
                rows = store.list(root)
                stores[scope], snapshots[scope], states[scope] = store, rows, 'ready'
                if any(row['pinned'] for row in rows) or query.strip() and rank(rows, query[:500]):
                    active.append(index)
            except (OSError, ValueError, sqlite3.Error, HarnessError) as exc:
                logger.warning(tr('log.5845d7626ec1'), scope)
                states[scope] = 'unavailable'
                codes[scope] = exc.code if isinstance(exc, HarnessError) else 'memory/storage-unavailable'
        if active:
            # 有候选的层保留原始份额；空层和未绑定层按原权重让出容量。
            idle = [index for index, scope in enumerate(scopes)
                    if index not in active and states[scope] != 'unavailable']
            spare = sum(limits[index] for index in idle)
            for index in idle:
                limits[index] = 0
            weights = (1, 2, 1)
            weight = sum(weights[index] for index in active)
            grants = {index: spare * weights[index] // weight for index in active}
            remainder = spare - sum(grants.values())
            for offset, index in enumerate(active):
                limits[index] += grants[index] + int(offset < remainder)
        for scope, limit in zip(scopes, limits):
            if states[scope] == 'unbound':
                layers.append(MemoryLayerContext(scope, 'unbound', limit))
                continue
            separator = 1 if chunks else 0
            allowance = max(0, limit - separator)
            try:
                if states[scope] == 'unavailable':
                    text = tr('python.memory.layer_unavailable', scope=scope)
                    if len(text) > allowance:
                        text = ''
                    layers.append(MemoryLayerContext(scope, 'unavailable', allowance, error_code=codes[scope]))
                    if text:
                        chunks.append(text)
                    continue
                store = stores[scope]
                report = select_context(snapshots[scope], context_key=store.context_key, query=query, budget=allowance,
                                        checker=SourceChecker(root) if store.check_sources else None)
                text = report.text
                layers.append(MemoryLayerContext(scope, 'ready', allowance, report))
            except (OSError, ValueError, sqlite3.Error, HarnessError) as exc:
                # 日志只写作用域，不把数据库正文或用户偏好带入诊断。
                logger.warning(tr('log.5845d7626ec1'), scope)
                text = tr('python.memory.layer_unavailable', scope=scope)
                if len(text) > allowance:
                    text = ''
                code = exc.code if isinstance(exc, HarnessError) else 'memory/storage-unavailable'
                layers.append(MemoryLayerContext(scope, 'unavailable', allowance, error_code=code))
            if text:
                chunks.append(text)
        return LayeredMemoryContext('\n'.join(chunks), budget, tuple(layers))
