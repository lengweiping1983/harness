"""受已注册工作区边界约束的项目任务板接口。"""
from harness.project.error_messages import board_error_message
from harness.project.board import BoardError, TaskBoard
from harness.project.git_evidence import GitEvidence
from harness.project.run_status import project_runs
from harness.core.errors import HarnessError
from ..errors import BadRequest, Conflict
from ..responses import json_response
from ..router import Router
from .workspace import _resolved_root


def build_router(ctx):
    """管理任务合同和正式团队运行，提交与合并由现有执行流程负责。"""
    router = Router()

    def guarded(operation):
        try:
            return json_response(operation())
        except BoardError as exc:
            error = Conflict if str(exc) in {'board/stale-revision', 'board/project-busy', 'board/working-tree-changed', 'board/execution-mismatch', 'board/execution-cancelled'} else BadRequest
            raise error(board_error_message(str(exc)), code=str(exc)) from exc
        except HarnessError as exc:
            raise BadRequest(exc.message, code=exc.code) from exc
        except OSError as exc:
            raise BadRequest(board_error_message('board/storage-error'), code='board/storage-error') from exc

    def body(request):
        payload = request.json()
        if set(payload) != {'revision', 'task'} or not isinstance(payload['revision'], str) or not isinstance(payload['task'], dict):
            raise BadRequest(board_error_message('board/invalid-request'), code='board/invalid-request')
        return payload

    @router.get('/api/v1/project-board')
    def read(request):
        board = TaskBoard(_resolved_root(ctx, request.get('root')), verify_artifacts=ctx.harness.artifact_review.verify)
        return guarded(board.read)

    @router.get('/api/v1/project-board/runs')
    def runs(request):
        root = _resolved_root(ctx, request.get('root'))
        async def snapshot():
            return project_runs(ctx.harness, root)
        return guarded(lambda: ctx.call(snapshot()))

    @router.get('/api/v1/project-board/git')
    def git_snapshot(request):
        root = _resolved_root(ctx, request.get('root'))
        if not ctx.harness.config.enable_shell:
            return json_response({'available': False, 'reason': 'board/git-disabled'})
        return guarded(GitEvidence(root).snapshot)

    @router.post('/api/v1/project-board/evidence')
    def git_evidence(request):
        payload = request.json()
        if set(payload) != {'task'} or not isinstance(payload['task'], dict):
            raise BadRequest(board_error_message('board/invalid-request'), code='board/invalid-request')
        root = _resolved_root(ctx, request.get('root'))
        if not ctx.harness.config.enable_shell:
            raise BadRequest(board_error_message('board/git-disabled'), code='board/git-disabled')
        return guarded(lambda: GitEvidence(root).verify(payload['task']))

    @router.post('/api/v1/project-board/generate')
    def generate(request):
        payload = request.json()
        if set(payload) - {'revision', 'key', 'goal', 'source_session_id'} or any(
                not isinstance(payload.get(key), str) or not payload[key] or len(payload[key]) > limit
                for key, limit in (('revision', 128), ('key', 128), ('goal', 10000))):
            raise BadRequest(board_error_message('board/invalid-request'), code='board/invalid-request')
        source = payload.get('source_session_id')
        if source is not None and (not isinstance(source, str) or not source or len(source) > 128):
            raise BadRequest(board_error_message('board/invalid-request'), code='board/invalid-request')
        root = _resolved_root(ctx, request.get('root'))
        return guarded(lambda: ctx.call(ctx.harness.project_planning.generate(root, payload['revision'], payload['goal'], payload['key'], source), timeout=40))

    @router.post('/api/v1/project-board/tasks/{task_id}/launch')
    def launch(request, task_id):
        payload = request.json()
        if set(payload) - {'revision', 'key', 'source_session_id'} or any(
                not isinstance(payload.get(key), str) or not payload[key] or len(payload[key]) > 128 for key in ('revision', 'key')):
            raise BadRequest(board_error_message('board/invalid-request'), code='board/invalid-request')
        source = payload.get('source_session_id')
        if source is not None and (not isinstance(source, str) or not source or len(source) > 128):
            raise BadRequest(board_error_message('board/invalid-request'), code='board/invalid-request')
        root = _resolved_root(ctx, request.get('root'))
        return guarded(lambda: ctx.call(ctx.harness.project_execution.launch(root, task_id, payload['revision'], payload['key'], source), timeout=40))

    @router.post('/api/v1/project-board/tasks')
    def create(request):
        payload = body(request)
        board = TaskBoard(_resolved_root(ctx, request.get('root')), verify_artifacts=ctx.harness.artifact_review.verify)
        return guarded(lambda: board.update(payload.get('revision'), payload.get('task'), create=True))

    @router.patch('/api/v1/project-board/tasks/{task_id}')
    def update(request, task_id):
        payload = body(request)
        task = payload.get('task')
        if not isinstance(task, dict) or ('id' in task and task['id'] != task_id):
            raise BadRequest(board_error_message('board/invalid-task'), code='board/invalid-task')
        board = TaskBoard(_resolved_root(ctx, request.get('root')), verify_artifacts=ctx.harness.artifact_review.verify)
        return guarded(lambda: board.update(payload.get('revision'), {**task, 'id': task_id}))

    return router
