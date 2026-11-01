"""项目任务工具，沿用普通工具的权限、工作区范围和运行记录。"""
from __future__ import annotations

from pathlib import Path
from harness.project.error_messages import board_error_message

from harness.i18n import tr
from harness.project.board import BoardError, STATES, TaskBoard
from harness.project.git_evidence import GitEvidence
from harness.project.contracts import FIELDS
from harness.project.operations import run_owned as _finish_operation
from harness.core.errors import ToolExecutionError
from ..registry import ToolDefinition



def build_project_tools(cwd: str | None = None, *, enable_git: bool = True, verify_artifacts=None) -> list[ToolDefinition]:
    """目标目录来自可信执行上下文，不接受模型传入任意项目根目录。"""
    def board(ctx):
        return TaskBoard(Path(ctx.cwd or cwd or Path.cwd()), verify_artifacts=verify_artifacts)

    def guarded(operation):
        try:
            return operation()
        except BoardError as exc:
            raise ToolExecutionError(board_error_message(str(exc)), code=str(exc)) from exc
        except OSError as exc:
            raise ToolExecutionError(board_error_message('board/storage-error'), code='board/storage-error') from exc

    async def read(args, ctx):
        def operation():
            target = board(ctx)
            original = target.read()
            tasks = original['tasks']
            if args.get('ids'):
                tasks = [task for task in tasks if task['id'] in args['ids']]
            if args.get('statuses'):
                tasks = [task for task in tasks if task['status'] in args['statuses']]
            priority = ('IN_PROGRESS', 'REVIEW', 'BLOCKED', 'READY', 'BACKLOG', 'DONE')
            tasks = sorted(tasks, key=lambda task: priority.index(task['status']))
            offset, limit = args.get('offset', 0), args.get('limit', 20)
            snapshot = {key: original[key] for key in ('schema', 'revision', 'path', 'file_exists')}
            snapshot.update(tasks=tasks[offset:offset + limit], total=len(tasks), offset=offset,
                            limit=limit, has_more=offset + limit < len(tasks))
            if enable_git:
                try:
                    snapshot['git'] = GitEvidence(target.root).snapshot()
                except BoardError as exc:
                    # Git 不可用不影响恢复任务合同，且不能伪装为工作区干净。
                    snapshot['git'] = {'available': False, 'reason': str(exc)}
            return snapshot
        return await _finish_operation(lambda: guarded(operation))

    async def save(args, ctx):
        def operation():
            if not enable_git and args['task'].get('status') == 'DONE' and next((row for row in board(ctx).read()['tasks'] if row['id'] == args['task'].get('id')), {}).get('verification_policy', 'git') == 'git':
                raise BoardError('board/git-disabled')
            saved = board(ctx).update(args['revision'], args['task'], create=args['create'])
            return {'task': next(task for task in saved['tasks'] if task['id'] == args['task']['id']),
                    'revision': saved['revision'], 'path': saved['path'], 'total': len(saved['tasks'])}
        return await _finish_operation(lambda: guarded(operation))

    async def generate(args, ctx):
        def operation():
            saved = board(ctx).append_plan(args['revision'], args['tasks'])
            identifiers = {task['id'] for task in args['tasks']}
            return {'tasks': [task for task in saved['tasks'] if task['id'] in identifiers],
                    'revision': saved['revision'], 'path': saved['path'], 'total': len(saved['tasks'])}
        return await _finish_operation(lambda: guarded(operation))

    async def check(args, ctx):
        return await _finish_operation(lambda: guarded(lambda: GitEvidence(board(ctx).root).verify(args['task'])))

    fields = {key: value for key, value in FIELDS.items() if key != 'verification_result'}
    contract = {'type': 'object', 'properties': fields, 'required': ['id'], 'additionalProperties': False}

    def tool(name, properties, required, execute, *, write=False):
        return ToolDefinition(
            schema={'name': name, 'description_key': 'python.project_tools.' + name,
                    'description': tr('python.project_tools.' + name),
                    'parameters': {'type': 'object', 'properties': properties, 'required': required,
                                   'additionalProperties': False}},
            execute=execute, category='plan', effect='workspace_write' if write else 'read',
            concurrency_safe=not write, timeout=20,
            write_paths=(lambda args, ctx: [board(ctx).path]) if write else None,
            progress=(lambda args, value: value['revision'] != args['revision']) if write else None,
            progress_key=(lambda args, value: {'task': args.get('task'), 'tasks': args.get('tasks'), 'create': args.get('create'),
                          'expected_revision': args['revision'], 'revision': value['revision']}) if write else None,
        )

    tools = [
        tool('project_board_read', {'offset': {'type': 'integer', 'minimum': 0, 'maximum': 1000},
             'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50},
             'ids': {'type': 'array', 'minItems': 1, 'maxItems': 50, 'uniqueItems': True, 'items': fields['id']},
             'statuses': {'type': 'array', 'minItems': 1, 'maxItems': len(STATES), 'uniqueItems': True, 'items': fields['status']}}, [], read),
        tool('project_task_save', {'revision': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'},
                                  'create': {'type': 'boolean'}, 'task': contract},
             ['revision', 'create', 'task'], save, write=True),
    ]
    plan_task = {'type': 'object', 'properties': {key: fields[key] for key in (
        'id', 'title', 'allowed_paths', 'acceptance', 'depends_on', 'verification_policy', 'artifacts')},
        'required': ['id', 'title', 'allowed_paths', 'acceptance'], 'additionalProperties': False}
    tools.append(tool('project_tasks_generate', {'revision': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'},
                      'tasks': {'type': 'array', 'minItems': 1, 'maxItems': 100, 'items': plan_task}},
                      ['revision', 'tasks'], generate, write=True))
    if enable_git:
        evidence = {'type': 'object', 'properties': {key: fields[key] for key in (
            'base_commit', 'commit', 'integration_commit', 'target_branch')},
            'required': ['base_commit', 'commit', 'integration_commit', 'target_branch'], 'additionalProperties': False}
        tools.append(tool('project_git_check', {'task': evidence}, ['task'], check))
    return tools
