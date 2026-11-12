"""实时浏览器控制和临时 JPEG 画面，限于已有会话。"""

from harness.i18n import tr
import hashlib
import os
import secrets
from ..errors import BadRequest, Conflict, NotFound
from ..router import Router, Response
from ..responses import json_response


def build_router(ctx):
    router = Router()

    @router.get('/api/v1/desktop/health')
    def desktop_health(request):
        # 仅用于桌面宿主识别其自己启动的服务，不向页面公开凭据。
        token = os.environ.get('HARNESS_DESKTOP_TOKEN', '')
        if not token or not secrets.compare_digest((request.header('Authorization') or '').encode(), ('Bearer ' + token).encode()):
            raise NotFound(tr('python.web.validation.notFound'))
        return json_response({'ready': True}).with_header('Cache-Control', 'no-store')

    def check(session_id, *, enabled=True):
        ctx.harness.get_session(session_id)
        if enabled and not ctx.harness.config.enable_browser:
            raise BadRequest(tr('python.browser.1bd33e38920b'), code='browser/disabled')

    @router.get('/api/v1/sessions/{session_id}/browser')
    def state(request, session_id):
        check(session_id, enabled=False)
        result = ctx.call(ctx.harness.browser.state(session_id))
        return json_response({'enabled': ctx.harness.config.enable_browser, **result}).with_header('Cache-Control', 'no-store')

    @router.get('/api/v1/sessions/{session_id}/browser/frame')
    def frame(request, session_id):
        check(session_id)
        revision = request.get_int('control_revision')
        if request.get('control_revision') is not None and revision is None:
            raise BadRequest(tr('python.browser.a8cfe30108d5'), code='browser/control-invalid')
        body = ctx.call(ctx.harness.browser.frame(session_id, request.get('page_id') or '', expected_revision=revision), timeout=5)
        etag = '"' + hashlib.sha256(body).hexdigest() + '"'
        headers = {'Content-Type': 'image/jpeg', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'ETag': etag}
        return Response(body=b'' if request.header('If-None-Match') == etag else body,
                        status=304 if request.header('If-None-Match') == etag else 200, headers=headers)

    @router.post('/api/v1/sessions/{session_id}/browser')
    def execute(request, session_id):
        check(session_id)
        return json_response(ctx.call(ctx.harness.browser.execute(session_id, request.json(), actor='user'), timeout=45))

    @router.post('/api/v1/sessions/{session_id}/browser/control')
    def control(request, session_id):
        check(session_id)
        args = request.json()
        return json_response(ctx.call(ctx.harness.browser.control(session_id, args.get('owner'), args.get('expected_revision')), timeout=45))

    @router.delete('/api/v1/sessions/{session_id}/browser')
    def close(request, session_id):
        check(session_id)
        args = request.json()
        async def run():
            if type(args.get('control_revision')) is not int:
                raise BadRequest(tr('python.browser.f5d9524bef61'), code='browser/control-invalid')
            await ctx.harness.browser.close_session(session_id, expected_revision=args['control_revision'])
            return {'closed': True}
        return json_response(ctx.call(run(), timeout=45))

    @router.get('/api/v1/sessions/{session_id}/browser/diagnostics')
    def diagnostics(request, session_id):
        check(session_id)
        return json_response(ctx.call(ctx.harness.browser.diagnostics(session_id, request.get('tab_id')))).with_header('Cache-Control', 'no-store')

    @router.delete('/api/v1/sessions/{session_id}/browser/diagnostics')
    def clear_diagnostics(request, session_id):
        check(session_id)
        args = request.json()
        if not isinstance(args.get('tab_id'), str) or type(args.get('expected_revision')) is not int:
            raise BadRequest(tr('python.browser.7957ce1e5ac0'), code='browser/diagnostics-invalid')
        from ...core.errors import HarnessError
        try:
            result = ctx.call(ctx.harness.browser.diagnostics(session_id, args['tab_id'], expected_revision=args['expected_revision']))
        except HarnessError as exc:
            if exc.code == 'browser/diagnostics-changed':
                raise Conflict(exc.message, code=exc.code) from exc
            raise
        return json_response(result)

    return router
