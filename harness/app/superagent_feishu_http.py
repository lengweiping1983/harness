"""飞书认证 HTTP 传输，不加载旧信道的事件、结果或业务账本。"""
from __future__ import annotations

import asyncio
import re
import time

from ..core.cleanup import join_cleanup
from .superagent_common import _error
from .superagent_http import JSONClient


class FeishuClient(JSONClient):
    """自建应用的租户令牌只保留在内存，所有已接受请求归所属线程收尾。"""

    def __init__(self, config, secret):
        super().__init__(config['api_url'], prefix='feishu')
        self.app_id, self.secret = config['app_id'], secret
        self.token, self.expires, self.refresh = None, 0, None

    async def _refresh(self):
        result = await super().request('POST', '/open-apis/auth/v3/tenant_access_token/internal',
                                       {'app_id':self.app_id, 'app_secret':self.secret})
        if (not isinstance(result, dict) or type(result.get('code')) is not int or result['code'] != 0
                or type(result.get('expire')) is not int or not 1 <= result['expire'] <= 86400
                or not isinstance(result.get('tenant_access_token'), str)
                or not re.fullmatch('[!-~]{1,4096}', result['tenant_access_token'])):
            raise _error('feishu-response')
        self.token = result['tenant_access_token']
        self.expires = time.monotonic() + result['expire'] - min(60, result['expire'] / 10)

    async def request(self, method, path, data=None, *, before_request=None):
        await self._ready_token()
        value = await super().request(method, path, data, token=self.token, before_request=before_request)
        if not isinstance(value, dict) or type(value.get('code')) is not int:
            raise _error('feishu-response')
        if value['code'] != 0:
            raise _error('feishu-api', status=value['code'])
        return value

    async def _ready_token(self):
        """JSON 和资源读取共用同一所属令牌刷新任务。"""
        if time.monotonic() >= self.expires:
            if self.refresh is None or self.refresh.done():
                self.refresh = asyncio.create_task(self._refresh())
            await join_cleanup(self.refresh)

    async def request_bytes(self,path,*,before_request=None,maximum=33554432):
        await self._ready_token()
        return await super().request_bytes(path,token=self.token,before_request=before_request,maximum=maximum)
