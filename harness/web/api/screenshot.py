"""屏幕捕获是本地界面操作，不作为模型工具或远程桌面接口。"""
from __future__ import annotations

from harness.i18n import tr

import base64
import ipaddress
from typing import Any
from urllib.parse import urlsplit

from .. import screenshot
from ..errors import BadRequest
from ..responses import json_response
from ..router import Router


def _local(request: Any) -> bool:
    try:
        host = urlsplit('//' + request.header('Host')).hostname or ''
        return ipaddress.ip_address(request.client_ip or '').is_loopback and (
            host == 'localhost' or ipaddress.ip_address(host).is_loopback)
    except ValueError:
        return False


def build_router(ctx: Any) -> Router:
    router = Router()

    @router.get('/api/v1/screenshot/capabilities')
    def capabilities(request: Any) -> Any:
        available = _local(request) and screenshot.native_available()
        return json_response({'native': available, 'hideWindow': available,
                              'reason': None if available else {'key': 'ui.screenshot.nativeUnavailable'}})

    @router.post('/api/v1/screenshot')
    def capture(request: Any) -> Any:
        if not _local(request):
            raise BadRequest(tr('python.screenshot.c2e81a579ee7'),
                             status=403, code='screenshot/local-only')
        payload = request.json()
        hide, token = payload.get('hideWindow', False), payload.get('windowToken', '')
        if type(hide) is not bool or not isinstance(token, str):
            raise BadRequest(tr('python.screenshot.0eca27c5f409'), code='screenshot/invalid-payload')
        data = screenshot.capture_region(hide_window=hide, window_token=token)
        response = json_response({'cancelled': True} if data is None else {
            'cancelled': False, 'data': base64.b64encode(data).decode('ascii')})
        response.headers['Cache-Control'] = 'no-store'
        return response

    return router
