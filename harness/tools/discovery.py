"""会话内的工具定义按需加载；发现不改变执行权限。"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping

from ..core.errors import SessionInvariantError, ToolError
from ..i18n import tr
from .registry import ToolDefinition

DEFER_SCHEMA_CHARS = 16000


class ToolDiscovery:
    """只缓存当前会话的已用名称，实际定义始终取自当前注册表。"""

    def __init__(self):
        self.session = None
        self._generation = None
        self._offset = 0
        self.loaded: set[str] = set()
        self._calls = defaultdict(set)
        self._activated = {}
        self._summarized = set()

    def bind(self, session):
        if self.session is not session or self._generation != session.history_generation:
            self.session = session
            self._generation = session.history_generation
            self._offset = 0
            self.loaded = set()
            self._calls = defaultdict(set)
            self._activated = {}
            self._summarized = set()

    def sync(self):
        if self.session is None:
            return
        self.bind(self.session)
        if self._offset == self.session.event_count:
            return
        for event in self.session.events[self._offset:]:
            if event.type == 'note' and event.data.get('kind') == 'tool-discovery/activated':
                names = event.data.get('tools')
                if not isinstance(names, (list, tuple)) or any(not isinstance(name, str) or not name for name in names):
                    raise SessionInvariantError(tr('tool.discovery.corrupt'), seq=event.seq)
                self.loaded.update(names)
                self._activated.update((name, event.seq) for name in names)
            elif event.type == 'assistant/message':
                for block in event.data.get('content', ()):
                    if isinstance(block, Mapping) and block.get('type') == 'tool_use' and isinstance(block.get('name'), str):
                        self.loaded.add(block['name'])
                        self._calls[block['name']].add(event.seq)
            elif event.type == 'user/message' and event.data.get('source') == 'compaction' and event.source_event_seqs:
                # 只依据已提交的摘要替换，失败／准备日志和普通编辑不能释放定义。
                self._summarized.update(event.source_event_seqs)
        active = {event.seq for event in self.session.surface}
        for name, calls in self._calls.items():
            if (calls <= self._summarized and not calls & active
                    and self._activated.get(name, -1) <= max(calls)):
                self.loaded.discard(name)
        self._offset = self.session.event_count

    def split(self, schemas, registry, mode):
        """自动模式延迟维护工具和大目录候选；全量或无发现入口时直接提供。"""
        if mode == 'all' or not any(schema.name == 'search_tools' for schema in schemas):
            return schemas, []
        candidates = [schema for schema in schemas if registry.get(schema.name).defer_loading]
        size = 0
        for schema in candidates:
            size += len(json.dumps(schema.to_dict(), ensure_ascii=False))
            if size > DEFER_SCHEMA_CHARS:
                break
        if size <= DEFER_SCHEMA_CHARS:
            candidates = [schema for schema in candidates if registry.get(schema.name).defer_always]
        self.sync()
        deferred = [schema for schema in candidates if schema.name not in self.loaded]
        names = {schema.name for schema in deferred}
        return [schema for schema in schemas if schema.name not in names], deferred

    def catalog(self, deferred, registry):
        if not deferred:
            return ''
        groups = defaultdict(list)
        for schema in deferred:
            groups[registry.get(schema.name).discovery_namespace or 'tools'].append(schema.name)
        rows, size = [], 0
        for namespace, names in sorted(groups.items()):
            row = json.dumps({'namespace': namespace, 'count': len(names), 'examples': [name[:100] for name in names[:3]]}, ensure_ascii=False)
            if size + len(row) > 3500:
                break
            rows.append(row)
            size += len(row)
        return tr('tool.discovery.catalog', count=len(deferred), namespaces=len(groups)) + '\n' + '\n'.join(rows)

    async def search(self, args, schemas, registry):
        """对当前可见范围做确定性文本查询并持久化选中名称，不执行目标工具。"""
        if self.session is None:
            raise ToolError(tr('tool.discovery.no_session'), code='tool-discovery/no-session')
        query, limit, offset = args.get('query', ''), args.get('limit', 5), args.get('offset', 0)
        if not isinstance(query, str) or len(query) > 1000 or type(limit) is not int or not 1 <= limit <= 10 or type(offset) is not int or offset < 0:
            raise ToolError(tr('tool.discovery.invalid'), code='tool-discovery/invalid')
        normalized_query = query.casefold()
        tokens = re.findall(r'[\w]+', normalized_query)
        exact = next((schema.name for schema in schemas if schema.name.casefold() == query.strip().casefold()), None)
        named_matches = {schema.name for schema in schemas if re.search(
            r'(?<![\w-])' + re.escape(schema.name.casefold()) + r'(?![\w-])', normalized_query)}
        ranked = []
        for schema in schemas:
            definition = registry.get(schema.name)
            if exact is not None and schema.name != exact:
                continue
            if named_matches and schema.name not in named_matches:
                continue
            name = schema.name.casefold()
            namespace = (definition.discovery_namespace or '').casefold()
            description = schema.description.casefold()
            scores = [10 * (word in name) + 5 * (word in namespace) + (word in description) for word in tokens]
            named = schema.name in named_matches
            if tokens and not named and not all(scores):
                continue
            ranked.append((sum(scores) + (100 if named else 0), schema.name, schema, definition))
        ranked.sort(key=lambda row: (-row[0], row[1]))
        selected = ranked[offset:offset + limit]
        self.sync()
        new = [name for _, name, _, definition in selected if definition.defer_loading and name not in self.loaded]
        if new:
            await self.session.append('note', {'kind': 'tool-discovery/activated', 'tools': new}, ignorable=True)
            self.loaded.update(new)
        return {'tools': [{'name': name, 'description': schema.description[:240],
                          'namespace': definition.discovery_namespace, 'effect': definition.effect}
                         for _, name, schema, definition in selected],
                'total_matches': len(ranked), 'next_offset': offset + limit if offset + limit < len(ranked) else None,
                'definitions_available_next_request': True}


def build_tool_search():
    """构建普通函数工具，不要求模型端支持原生 tool_search 协议。"""
    async def search(args, ctx):
        callback = ctx.services.get('tool_search')
        if callback is None:
            raise ToolError(tr('tool.discovery.no_session'), code='tool-discovery/no-session')
        return await callback(args)

    return ToolDefinition(schema={'name': 'search_tools', 'description_key': 'tool.discovery.description',
        'parameter_keys': {('properties', 'query', 'description'): 'tool.discovery.query'},
        'description': tr('tool.discovery.description'),
        'parameters': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'maxLength': 1000, 'description': tr('tool.discovery.query')},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 10, 'default': 5},
            'offset': {'type': 'integer', 'minimum': 0, 'default': 0}},
            'required': ['query'], 'additionalProperties': False}}, execute=search,
        effect='read', concurrency_safe=False)
