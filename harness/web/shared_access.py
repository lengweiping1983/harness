"""显式共享服务的身份与资源边界；不接受正文或查询串声明身份。"""
from __future__ import annotations

from ..app.collaboration_errors import collaboration_error_message

import hashlib
import ipaddress
import json
import re
from http.cookies import SimpleCookie, CookieError
from urllib.parse import urlsplit

from ..core.errors import HarnessError
from ..app.access_identity import AccessIdentity
from .errors import ApiError, NotFound
from .responses import json_response


BROWSER_ROUTES = ('/api/v1/access/login', '/api/v1/access/logout')
BROWSER_STATUS = '/api/v1/access/status'


def private_http_host(host):
    """HTTP 测试入口仅接受回环或明确的私有地址，不接受任意 DNS 名或公网 IP。"""
    if host == 'localhost':
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or any(address in ipaddress.ip_network(network) for network in
        ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7'))



class SharedAccess:
    """复用会话授权及凭据交换；只开放明确授权的共享读取与排队提交。"""

    def __init__(self, sessions, authenticate, *, credentials=None, browser_origin=None):
        if not callable(authenticate):
            raise TypeError(collaboration_error_message('access/authenticator-required'))
        self.sessions = sessions
        self.authenticate = authenticate
        self.credentials = credentials
        self.browser_origin = None
        if browser_origin is not None:
            parts = urlsplit(browser_origin)
            if (parts.geturl() != browser_origin or parts.scheme not in {'http', 'https'} or not parts.netloc or not parts.hostname
                    or parts.path or parts.query or parts.fragment or parts.username or parts.password
                    or parts.scheme == 'http' and not private_http_host(parts.hostname)):
                raise ValueError(collaboration_error_message('access/invalid-browser-origin'))
            parts.port
            if credentials is None:
                raise ValueError(collaboration_error_message('access/browser-credentials-required'))
            self.browser_origin = browser_origin
            self.browser_host = parts.netloc
            self.cookie_secure = parts.scheme == 'https'
            prefix = '__Host-' if self.cookie_secure else ''
            self.cookie_name = prefix + 'harness-share-' + hashlib.sha256(browser_origin.encode()).hexdigest()[:12]

    @staticmethod
    def browser_endpoint(request):
        from .api.collaboration import PUBLIC_INVITATION_PATHS
        if request.method == 'POST' and request.path in PUBLIC_INVITATION_PATHS:
            return True
        if request.method == 'GET' and request.path == BROWSER_STATUS:
            return True
        return request.method == 'POST' and request.path in BROWSER_ROUTES

    def origin_allowed(self, headers):
        """浏览器凭据启用时不接受开发端口等宽松来源例外。"""
        return headers.get('Host') == self.browser_host and headers.get('Origin', '') in ('', self.browser_origin)

    def _cookie_token(self, headers):
        if self.browser_origin is None:
            return None
        raw = '; '.join(headers.get_all('Cookie', [])) if hasattr(headers, 'get_all') else headers.get('Cookie', '')
        if not raw:
            return None
        count = sum(part.partition('=')[0].strip() == self.cookie_name for part in raw.split(';'))
        if count > 1:
            raise ApiError(collaboration_error_message('access/ambiguous-credential'), code='access/ambiguous-credential', status=401)
        try:
            cookies = SimpleCookie()
            cookies.load(raw)
        except CookieError:
            raise ApiError(collaboration_error_message('access/unauthenticated'), code='access/unauthenticated', status=401) from None
        value = cookies.get(self.cookie_name)
        if count and value is None:
            raise ApiError(collaboration_error_message('access/unauthenticated'), code='access/unauthenticated', status=401)
        return value.value if value is not None else None

    def _set_cookie(self, token, *, clear=False):
        cookie = SimpleCookie()
        cookie[self.cookie_name] = token
        value = cookie[self.cookie_name]
        value['path'], value['httponly'], value['samesite'] = '/', True, 'Strict'
        value['max-age'] = 0 if clear else 3600
        if self.cookie_secure:
            value['secure'] = True
        return cookie.output(header='').strip()

    def browser_status(self, request):
        """只读回查实际 Cookie 身份，供未知交换结果确认；不回传明文凭据。"""
        result = {'enabled': self.browser_origin is not None, 'authenticated': False,
                  'subject': None, 'session_ids': [], 'credential_id': None, 'read_only': True, 'capabilities': {}}
        if self.browser_origin is not None:
            try:
                identity = self.identity(request.headers)
                token = self._cookie_token(request.headers)
                result.update(authenticated=True, subject=identity.subject,
                              session_ids=sorted(self.visible_sessions(identity)),
                              credential_id=hashlib.sha256(token.encode()).hexdigest() if token else None,
                              capabilities={sid: self.sessions.require(sid, identity.subject, 'read')['members'][identity.subject]
                                            for sid in self.visible_sessions(identity)})
                result['read_only'] = not any('contribute' in caps for caps in result['capabilities'].values())
            except ApiError:
                pass
        return result

    async def exchange(self, request):
        """同源显式交换与退出，不创建另一份登录数据库。"""
        if self.browser_origin is None:
            raise NotFound(collaboration_error_message('access/browser-disabled'), code='access/browser-disabled')
        if (request.header('Origin') != self.browser_origin or request.header('Host') != self.browser_host
                or request.header('Content-Type').split(';')[0].strip().lower() != 'application/json'):
            raise ApiError(collaboration_error_message('access/origin'), code='access/origin', status=403)
        if request.header('Authorization'):
            raise ApiError(collaboration_error_message('access/ambiguous-credential'), code='access/ambiguous-credential', status=400)
        body = request.json()
        if request.path.endswith('/logout'):
            if body:
                raise ApiError(collaboration_error_message('access/invalid-exchange'), code='access/invalid-exchange', status=400)
            token = self._cookie_token(request.headers)
            if token:
                await self.credentials.relinquish({'Authorization': 'Bearer ' + token})
            return json_response({'signed_out': True}, headers={'Set-Cookie': self._set_cookie('', clear=True)})
        if set(body) != {'token'} or not isinstance(body['token'], str) or not 0 < len(body['token']) <= 4096:
            raise ApiError(collaboration_error_message('access/invalid-exchange'), code='access/invalid-exchange', status=400)
        identity = self.credentials.authenticate({'Authorization': 'Bearer ' + body['token']})
        if identity is None:
            raise ApiError(collaboration_error_message('access/unauthenticated'), code='access/unauthenticated', status=401)
        visible = self.visible_sessions(identity)
        return json_response({'subject': identity.subject, 'session_ids': sorted(visible), 'read_only': not any('contribute' in self.sessions.require(sid, identity.subject, 'read')['members'][identity.subject] for sid in visible)},
                             headers={'Set-Cookie': self._set_cookie(body['token'])})

    def identity(self, headers):
        """验证器处理凭据的过期与撤销；结果必须是已验证的稳定主体。"""
        try:
            cookie = self._cookie_token(headers)
            if cookie is not None:
                if headers.get('Authorization'):
                    raise ValueError(collaboration_error_message('access/ambiguous-credential'))
                identity = self.credentials.authenticate({'Authorization': 'Bearer ' + cookie})
            else:
                identity = self.authenticate(headers)
        except Exception:
            raise ApiError(collaboration_error_message('access/unauthenticated'), code='access/unauthenticated', status=401) from None
        if not isinstance(identity, AccessIdentity):
            raise ApiError(collaboration_error_message('access/unauthenticated'), code='access/unauthenticated', status=401)
        return identity

    def require_read(self, session_id, identity):
        """无权和未知会话都不披露存在性，坏账本同样不放行。"""
        try:
            if identity.session_ids is not None and session_id not in identity.session_ids:
                raise ValueError(collaboration_error_message('access/resource'))
            self.sessions.require(session_id, identity.subject, 'read')
        except (HarnessError, ValueError):
            raise ApiError(collaboration_error_message('access/denied'), code='access/denied', status=403) from None

    def visible_sessions(self, identity):
        """同一权限账本筛选目录，未授权和坏记录不进入会话投影。"""
        visible = set()
        for header in self.sessions.sessions.list_sessions():
            try:
                self.require_read(header.id, identity)
            except ApiError:
                continue
            visible.add(header.id)
        return frozenset(visible)

    def catalog_revision(self, visible):
        """仅对可见会话头求稳定指纹，不暴露宿主全局目录变化。"""
        headers = [header.to_dict() for header in self.sessions.sessions.list_sessions() if header.id in visible]
        headers.sort(key=lambda row: row['id'])
        encoded = json.dumps(headers, sort_keys=True, ensure_ascii=False).encode('utf-8')
        return int(hashlib.sha256(encoded).hexdigest()[:13], 16)

    def authorize_http(self, request, params):
        identity = self.identity(request.headers)
        session_id = params.get('session_id')
        if request.method == 'GET' and request.path in ('/api/v1/sessions', '/api/v1/sessions/activity'):
            return identity, self.visible_sessions(identity)
        if session_id and re.fullmatch(r'/api/v1/sessions/[^/]+/(?:collaboration|invitations(?:/[0-9a-f]{32}/(?:approve|revoke))?|members/[A-Za-z0-9:_.@-]+)', request.path):
            if identity.session_ids is not None and session_id not in identity.session_ids:
                raise ApiError(collaboration_error_message('access/denied'), code='access/denied', status=403)
            capability = 'read' if request.method == 'GET' and request.path.endswith('/collaboration') else 'manage'
            try:
                self.sessions.require(session_id, identity.subject, capability)
            except HarnessError:
                raise ApiError(collaboration_error_message('access/denied'), code='access/denied', status=403) from None
            return identity, None
        decision = bool(re.fullmatch(r'/api/v1/sessions/[^/]+/questions/[^/]+/answer', request.path)) and request.method == 'POST'
        decision_read = request.method == 'GET' and bool(re.fullmatch(r'/api/v1/sessions/[^/]+/(?:questions|decisions/[A-Za-z0-9_-]{16,128})', request.path))
        if session_id and (decision or decision_read):
            self.require_read(session_id, identity)
            if decision and (self.browser_origin is None or request.header('Origin') != self.browser_origin):
                raise ApiError(collaboration_error_message('access/origin'), code='access/origin', status=403)
            if decision or '/decisions/' in request.path:
                try:
                    self.sessions.require(session_id, identity.subject, 'control')
                except HarnessError:
                    raise ApiError(collaboration_error_message('access/denied'), code='access/denied', status=403) from None
            return identity, None
        submission = bool(re.fullmatch(r'/api/v1/sessions/[^/]+/submissions/[A-Za-z0-9_-]{16,128}', request.path))
        submit = request.method == 'POST' and bool(re.fullmatch(r'/api/v1/sessions/[^/]+/runs', request.path))
        queue_item = bool(re.fullmatch(r'/api/v1/sessions/[^/]+/runs/[^/]+', request.path)) and request.method in {'GET', 'PATCH', 'DELETE'}
        if session_id and (submission and request.method == 'GET' or submit or queue_item):
            self.require_read(session_id, identity)
            if submit or queue_item and request.method != 'GET':
                if self.browser_origin is None or request.header('Origin') != self.browser_origin:
                    raise ApiError(collaboration_error_message('access/origin'), code='access/origin', status=403)
                try:
                    self.sessions.require(session_id, identity.subject, 'contribute')
                except HarnessError:
                    raise ApiError(collaboration_error_message('access/denied'), code='access/denied', status=403) from None
            return identity, None
        # 未完成资源授权的其他全局接口和写操作保持关闭。
        readable = bool(re.fullmatch(r'/api/v1/sessions/[^/]+(?:/(?:conversation|queue|search|messages/[0-9]+|live-events|export))?', request.path))
        if request.method != 'GET' or not session_id or not readable or request.get('agent_id'):
            raise ApiError(collaboration_error_message('access/denied'), code='access/denied', status=403)
        self.require_read(session_id, identity)
        return identity, None
