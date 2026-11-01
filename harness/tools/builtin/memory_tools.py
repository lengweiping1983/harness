"""按操作收敛记忆工具，保留可信作用域绑定和独立用户写权限。"""
from __future__ import annotations

import asyncio

from harness.i18n import tr
from ..registry import ToolDefinition
from ...app.memory_sources import capture_sources
from ...app.memory_archive import MemoryArchive
from ...app.memory_search import rank
from ...core.errors import HarnessError


SCOPES = ('session', 'project', 'user')
WRITES = ('save', 'forget', 'consolidate', 'archive', 'restore')
COMMON = {'memory_recall', 'memory_save', 'memory_forget', 'user_memory_save', 'user_memory_forget'}


def build_layered_memory_tools(memory, *, candidates=None):
    """只读入口共享，项目／会话写与用户写保持不同的静态副作用。"""
    def invalid():
        raise HarnessError(tr('python.memory.invalid_scope'), code='memory/invalid')

    def root(ctx):
        return ctx.cwd or str(memory.home)

    def bound(scope, ctx):
        if scope not in SCOPES or scope == 'project' and not ctx.cwd or scope == 'session' and not ctx.session_id:
            invalid()
        return memory.store(scope, session_id=ctx.session_id)

    def selected(args, ctx, user=False):
        # 用户写不能通过参数降格为项目写；其他入口必须明确提交作用域。
        scope = 'user' if user else args.get('scope')
        if user and 'scope' in args:
            invalid()
        return scope, bound(scope, ctx)

    def recall(args, ctx):
        query, queries, limit = args.get('query', ''), args.get('queries'), args.get('limit', 20)
        if (not isinstance(query, str) or len(query) > 500 or type(limit) is not int or not 1 <= limit <= 100
                or 'query' in args and 'queries' in args
                or 'queries' in args and (not isinstance(queries, list) or not 1 <= len(queries) <= 4
                    or any(not isinstance(item, str) or not item.strip() or len(item) > 500 for item in queries))):
            raise HarnessError(tr('python.memory.invalid_query'), code='memory/invalid')
        scopes = args.get('scopes', [scope for scope in SCOPES
                         if scope == 'user' or scope == 'project' and ctx.cwd or scope == 'session' and ctx.session_id])
        if (not isinstance(scopes, list) or not 1 <= len(scopes) <= 3
                or any(not isinstance(scope, str) or scope not in SCOPES for scope in scopes)
                or len(set(scopes)) != len(scopes)):
            invalid()
        # 先验证全部绑定，再读取；同名编号不能覆盖另一层的候选。
        services = [(scope, bound(scope, ctx)) for scope in scopes]
        rows = [{**row, 'scope': scope} for scope, service in services
                for row in service.recall(root(ctx), query, queries=queries, limit=100)]
        if len(services) == 1:
            # 单层继续使用原快照排名，不对融合结果再次排名而改变已有权重。
            return {'memories': rows[:limit]}
        if queries is None:
            rows = rank(rows, query)
        else:
            scores, matches, seen = {}, {}, set()
            for index, text in enumerate(queries):
                normalized = text.strip().casefold()
                if normalized in seen:
                    continue
                seen.add(normalized)
                for position, row in enumerate(rank(rows, text), 1):
                    key = (row['scope'], row['id'])
                    scores[key] = scores.get(key, 0) + 1 / (60 + position)
                    matches.setdefault(key, []).append(index)
            rows = sorted((row for row in rows if (row['scope'], row['id']) in scores),
                          key=lambda row: scores[(row['scope'], row['id'])], reverse=True)
            rows = [{**row, 'query_matches': matches[(row['scope'], row['id'])]} for row in rows]
        return {'memories': rows[:limit]}

    fields = {
        'content': {'type': 'string', 'minLength': 1, 'maxLength': 4000},
        'topic': {'type': 'string', 'minLength': 1, 'maxLength': 100},
        'pinned': {'type': 'boolean'},
        'evidence': {'type': 'string', 'maxLength': 1000},
        'memory_id': {'type': 'string', 'minLength': 1, 'maxLength': 100},
        'expected_revision': {'type': 'integer', 'minimum': 1},
    }
    reference = {'type': 'object', 'properties': {'id': fields['memory_id'],
                  'revision': fields['expected_revision']},
                 'required': ['id', 'revision'], 'additionalProperties': False}
    schemas = {
        'recall': ({'query': {'type': 'string', 'maxLength': 500},
                    'queries': {'type': 'array', 'minItems': 1, 'maxItems': 4,
                                'items': {'type': 'string', 'minLength': 1, 'maxLength': 500}},
                    'scopes': {'type': 'array', 'minItems': 1, 'maxItems': 3, 'uniqueItems': True,
                               'items': {'type': 'string', 'enum': list(SCOPES)}},
                    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}, []),
        'check': ({'references': {'type': 'array', 'minItems': 1, 'maxItems': 6, 'items': {
                    **reference, 'properties': {**reference['properties'], 'excerpt': {
                        'type': 'string', 'minLength': 1, 'maxLength': 1000}},
                    'required': ['id', 'revision', 'excerpt']}}}, ['references']),
        'save': (dict(fields), ['content']),
        'forget': ({key: fields[key] for key in ('memory_id', 'expected_revision')},
                   ['memory_id', 'expected_revision']),
        'consolidate': ({**{key: fields[key] for key in ('content', 'topic', 'pinned', 'evidence')},
                         'inputs': {'type': 'array', 'minItems': 2, 'maxItems': 6, 'items': reference}},
                        ['content', 'inputs']),
        'archive': ({'inputs': {'type': 'array', 'minItems': 1, 'maxItems': 6, 'items': {
                        **reference, 'properties': {**reference['properties'], 'revision': {
                            'type': 'integer', 'minimum': 1, 'maximum': 9223372036854775806}}}}}, ['inputs']),
        'archives': ({'before': {'type': 'integer', 'minimum': 1, 'maximum': 9223372036854775807},
                      'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
                      'memory_id': fields['memory_id']}, []),
        'archive_search': ({'query': {'type': 'string', 'minLength': 1, 'maxLength': 500},
                            'before': {'type': 'integer', 'minimum': 1, 'maximum': 9223372036854775807}}, ['query']),
        'restore': ({'memory_id': fields['memory_id'], 'archived_revision': {
                     'type': 'integer', 'minimum': 1, 'maximum': 9223372036854775806}},
                    ['memory_id', 'archived_revision']),
    }
    if candidates is not None:
        schemas['candidates'] = ({'query': {'type': 'string', 'minLength': 1, 'maxLength': 500},
                                  'offset': {'type': 'integer', 'minimum': 0, 'maximum': 999},
                                  'snapshot': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
                                  'limit': {'type': 'integer', 'minimum': 1, 'maximum': 10}}, ['query'])

    def make(action, user=False):
        name = ('user_memory_' if user else 'memory_') + action
        properties, required = schemas[action]
        properties, required = dict(properties), list(required)
        write = action in WRITES
        if action != 'recall' and not user:
            properties['scope'] = {'type': 'string', 'enum': ['session', 'project'] if write else list(SCOPES)}
            required.append('scope')
        if action == 'save' and not user:
            properties['source_files'] = {'type': 'array', 'minItems': 1, 'maxItems': 3,
                                         'items': {'type': 'string', 'minLength': 1, 'maxLength': 500}}

        def target(args, ctx):
            scope, service = selected(args, ctx, user)
            if not user and scope == 'user':
                invalid()
            paths = [service.path(root(ctx))]
            if action in ('archive', 'restore'):
                paths.append(MemoryArchive(service).path(root(ctx)))
            return paths

        def execute(args, ctx):
            scope, service = selected(args, ctx, user)
            if write and not user and scope == 'user':
                invalid()
            cwd = root(ctx)
            if action == 'save':
                evidence = args.get('evidence', '')
                if 'source_files' in args:
                    if not service.check_sources:
                        invalid()
                    evidence = capture_sources(cwd, args['source_files'], evidence,
                                               ctx.services.get('session'), ctx.session_id)
                result = service.save(cwd, args['content'], topic=args.get('topic', 'project'),
                    pinned=args.get('pinned', False), evidence=evidence, source='chat:' + ctx.session_id,
                    memory_id=args.get('memory_id'), expected_revision=args.get('expected_revision'))
            elif action == 'forget':
                service.delete(cwd, args['memory_id'], args['expected_revision'])
                result = {'deleted': args['memory_id']}
            elif action == 'check':
                result = {'checks': [{**row, 'scope': scope} for row in service.check(cwd, args['references'])],
                          'meaning': 'record-excerpt-only'}
            elif action == 'consolidate':
                result = service.consolidate(cwd, args['inputs'], args['content'],
                    topic=args.get('topic', 'project'), pinned=args.get('pinned', True),
                    evidence=args.get('evidence', ''), source='chat:' + ctx.session_id)
            elif action == 'archive':
                result = MemoryArchive(service).archive(cwd, args['inputs'])
            elif action == 'archives':
                result = MemoryArchive(service).list(cwd, before=args.get('before'), limit=args.get('limit', 20),
                                                     memory_id=args.get('memory_id'))
            elif action == 'archive_search':
                result = MemoryArchive(service).search(cwd, args['query'], before=args.get('before'))
            else:
                result = MemoryArchive(service).restore(cwd, args['memory_id'], args['archived_revision'])
            return {**result, 'scope': scope}

        async def semantic(args, ctx):
            scope, service = selected(args, ctx)
            asynchronous = getattr(candidates, 'search_async', None)
            kwargs = {'limit': args.get('limit', 5), 'offset': args.get('offset', 0), 'snapshot': args.get('snapshot')}
            if asynchronous is not None:
                result = await asynchronous(service, root(ctx), args['query'], **kwargs)
            else:
                result = await asyncio.to_thread(candidates.search, service, root(ctx), args['query'], **kwargs)
            return {**result, 'scope': scope, 'candidates': [
                {**row, 'scope': scope} for row in result.get('candidates', [])]}

        key = f'python.memory.user_memory_{action}_tool' if user else f'python.memory.{action}_tool'
        return ToolDefinition(schema={'name': name, 'description_key': key, 'description': tr(key), 'parameters': {
            'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}},
            execute=recall if action == 'recall' else semantic if action == 'candidates' else execute,
            effect=('external_write' if user else 'workspace_write') if write else 'read',
            category='session', concurrency_safe=not write, write_paths=target if write else None,
            defer_loading=name not in COMMON, defer_always=name not in COMMON,
            discovery_namespace='builtin:memory')

    return [*(make(action) for action in schemas), *(make(action, True) for action in WRITES)]
