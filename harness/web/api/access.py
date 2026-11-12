"""显式共享服务的浏览器凭据交换；默认本机服务不开放登录入口。"""

from ...app.collaboration_errors import collaboration_error_message
from ..errors import NotFound
from ..router import Router
from ..shared_access import BROWSER_ROUTES, BROWSER_STATUS
from ..responses import json_response


def build_router(ctx):
    router = Router()

    def exchange(request):
        if ctx.shared_access is None:
            raise NotFound(collaboration_error_message('access/browser-disabled'), code='access/browser-disabled')
        return ctx.call(ctx.shared_access.exchange(request))

    @router.get(BROWSER_STATUS)
    def status(request):
        if ctx.shared_access is None:
            return json_response({'enabled': False, 'authenticated': False, 'subject': None,
                                  'session_ids': [], 'credential_id': None, 'read_only': True, 'capabilities': {}})
        async def snapshot():
            return ctx.shared_access.browser_status(request)
        return json_response(ctx.call(snapshot()))

    for path in BROWSER_ROUTES:
        router.add('POST', path, exchange)
    return router
