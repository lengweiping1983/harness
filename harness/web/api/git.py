"""聊天范围内的 Git 暂存、审查和本地提交接口。"""
from harness.core.errors import HarnessError
from harness.i18n import tr
from ..errors import BadRequest, Conflict
from ..router import Router
from ..responses import json_response


def build_router(ctx):
    router=Router()
    def scope(session_id):
        async def resolve():
            session=ctx.harness.get_session(session_id)
            return session.header.cwd or str(ctx.workspace_root())
        service=ctx.harness.worktrees
        if service is None:raise BadRequest(tr('python.v1.4d7f49bfcc8f'),code='git/disabled')
        return service,ctx.call(resolve())
    def guarded(operation):
        try:return operation()
        except HarnessError as exc:
            error=Conflict if exc.code in {'git/index-changed','git/file-changed','git/conflicts','git/directory-changed'} else BadRequest
            raise error(exc.message,code=exc.code) from exc
    def body(request,fields):
        value=request.json()
        if not isinstance(value,dict) or set(value)!=set(fields):
            raise BadRequest(tr('ui.git.error.invalid'),code='git/invalid')
        return value
    @router.post('/api/v1/sessions/{session_id}/git/init')
    def initialize(request,session_id):
        service,cwd=scope(session_id);value=body(request,('expected_cwd',))
        return json_response(guarded(lambda:service.initialize(cwd,value['expected_cwd'])))
    @router.post('/api/v1/sessions/{session_id}/git/stage')
    def stage(request,session_id):
        service,cwd=scope(session_id);value=body(request,('paths','expected_revision'))
        return json_response(guarded(lambda:service.stage(cwd,value['paths'],value['expected_revision'])))
    @router.post('/api/v1/sessions/{session_id}/git/unstage')
    def unstage(request,session_id):
        service,cwd=scope(session_id);value=body(request,('paths','expected_revision'))
        return json_response(guarded(lambda:service.stage(cwd,value['paths'],value['expected_revision'],unstage=True)))
    @router.post('/api/v1/sessions/{session_id}/git/commit')
    def commit(request,session_id):
        service,cwd=scope(session_id);value=body(request,('message','expected_revision'))
        return json_response(guarded(lambda:service.commit(cwd,value['message'],value['expected_revision'])))
    @router.get('/api/v1/sessions/{session_id}/git/staged')
    def staged(request,session_id):
        service,cwd=scope(session_id)
        return json_response(guarded(lambda:service.staged_patch(cwd,request.get('path',''),request.get('expected_revision',''))))
    return router
