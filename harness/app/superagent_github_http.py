"""GitHub 的请求头、成功状态和专用接收路径。"""
from __future__ import annotations

from .superagent_http import JSONClient


class GitHubClient(JSONClient):
    """在共用网络边界上固定 GitHub 协议，不跟随带凭据重定向。"""

    def __init__(self, root, token, *, timeout=10):
        super().__init__(root, prefix='github', timeout=timeout)
        self.token = token

    async def request(self, method, path, data=None, *, before_request=None):
        return await super().request(method, path, data, token=self.token,
            headers={'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2026-03-10'},
            expected_status=201 if method == 'POST' else 200, before_request=before_request)
