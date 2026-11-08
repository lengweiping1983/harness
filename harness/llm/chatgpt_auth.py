""" OAuth 流程 ChatGPT 订阅登录和模型目录。"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import math
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .routes import atomic_json
from ..core.language import current_language
from ..i18n import tr

CLIENT_ID = 'app_EMoamEEZ73f0CkXaXp7hrann'
TOKEN_URL = 'https://auth.openai.com/oauth/token'
BASE_URL = 'https://chatgpt.com/backend-api/codex'
REDIRECT_URI = 'http://localhost:1455/auth/callback'
CLIENT_VERSION = '0.159.0'


class ChatGPTAccountError(ValueError):
    """宿主拥有的账号错误保留资源身份，在读取状态时解析当前语言。"""

    def __init__(self, key: str, **values):
        self.key, self.values = key, values
        super().__init__(key)

    def __str__(self):
        return tr(self.key, **self.values)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        """授权端点应直接响应，重定向明确报错。"""
        return None


def request_json(url, *, data=None, headers=None):
    """限制授权响应体和等待时间，错误不回显上游凭据。"""
    request = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ChatGPTAccountError('chatgpt.response_large')
        result = json.loads(raw.decode('utf-8'))
        if not isinstance(result, dict):
            raise ChatGPTAccountError('chatgpt.response_object')
        return result
    except ChatGPTAccountError:
        raise
    except urllib.error.HTTPError as exc:
        try:
            failure = http_failure(exc)
        finally:
            exc.close()
        raise failure from None
    except (OSError, ValueError) as exc:
        raise ChatGPTAccountError('chatgpt.network_failed') from None


def http_failure(response):
    """仅保留白名单错误码，不将上游正文或授权材料带入状态和日志。"""
    status = response.code
    if response.headers.get('cf-mitigated') == 'challenge':
        return ChatGPTAccountError('chatgpt.http_challenge', status=status)
    try:
        raw = response.read(16385)
        value = json.loads(raw.decode('utf-8')) if len(raw) <= 16384 else None
        error = value.get('error') if isinstance(value, dict) else None
        code = error.get('code') if isinstance(error, dict) else error
    except (OSError, ValueError):
        code = None
    reasons = {
        'unsupported_country_region_territory': 'chatgpt.http_region',
        'invalid_grant': 'chatgpt.http_grant', 'token_expired': 'chatgpt.http_grant',
        'access_denied': 'chatgpt.http_denied',
        'invalid_client': 'chatgpt.http_client',
    }
    key = reasons.get(code) if isinstance(code, str) else None
    if key:
        return ChatGPTAccountError(key, status=status)
    return ChatGPTAccountError('chatgpt.http_failed', status=status)


def claims(token):
    """JWT 解码只提取账号路由信息，不作为身份校验。"""
    if not isinstance(token, str):
        return {}
    try:
        part = token.split('.')[1]
        value = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)).decode('utf-8'))
        return value if isinstance(value, dict) else {}
    except (ValueError, IndexError, UnicodeError):
        return {}


class ChatGPTAccount:
    """令牌仅保存在 Harness 自有目录，不读取或改写其他客户端账号。"""

    def __init__(self, home: Path, *, request=request_json):
        self.path = home / 'chatgpt-account.json'
        self.request = request
        self.lock = threading.RLock()
        self.lifecycle = threading.RLock()
        self.credentials = None
        self.models = []
        self.selected = ''
        self.pending = None
        self.server = None
        self.login_timer = None
        self.error = ''
        self.epoch = 0
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(raw, dict) or type(raw.get('version')) is not int or raw.get('version') != 1 or set(raw) != {'version', 'credentials', 'models', 'selected'}:
                    raise ValueError()
                credential = raw['credentials']
                if credential is not None and (not isinstance(credential, dict) or
                        not all(isinstance(credential.get(k), str) for k in ('access', 'refresh', 'account_id', 'email')) or
                        not credential['access'] or not credential['account_id'] or
                        type(credential.get('expires')) not in (int, float) or not math.isfinite(credential['expires'])):
                    raise ValueError()
                if not isinstance(raw['models'], list) or not all(
                        isinstance(m, dict) and all(isinstance(m.get(k), str) for k in ('id', 'label', 'provider', 'description'))
                        and m['id'].startswith('chatgpt/') and m['provider'] == 'chatgpt'
                        and type(m.get('supports_reasoning')) is bool
                        and isinstance(m.get('reasoning_efforts'), list) and all(isinstance(e, str) for e in m['reasoning_efforts'])
                        and all(k not in m or type(m[k]) is int and m[k] > 0 for k in ('context_window', 'max_output'))
                        for m in raw['models']):
                    raise ValueError()
                if not isinstance(raw['selected'], str) or (raw['selected'] and raw['selected'] not in {m['id'] for m in raw['models']}):
                    raise ValueError()
                self.credentials, self.models, self.selected = credential, raw['models'], raw['selected']
            except (ValueError, TypeError, KeyError, OverflowError):
                raise ChatGPTAccountError('chatgpt.file_invalid') from None

    @property
    def logged_in(self):
        with self.lock:
            return self.credentials is not None

    def save(self):
        atomic_json(self.path, {'version': 1, 'credentials': self.credentials, 'models': self.models, 'selected': self.selected})

    def status(self):
        with self.lock:
            return {'logged_in': self.logged_in, 'email': (self.credentials or {}).get('email', ''),
                    'pending': self.pending is not None, 'error': str(self.error),
                    'models': deepcopy(self.models), 'selected': self.selected}

    def _token(self, fields):
        data = self.request(TOKEN_URL, data=urllib.parse.urlencode({'client_id': CLIENT_ID, **fields}).encode(),
                            headers={'Content-Type': 'application/x-www-form-urlencoded'})
        access = data.get('access_token')
        if not isinstance(access, str) or not access:
            raise ChatGPTAccountError('chatgpt.access_missing')
        old = (self.credentials or {}) if fields.get('grant_type') == 'refresh_token' else {}
        identity = [claims(data.get('id_token', '')), claims(access)]
        account_ids = set()
        for item in identity:
            namespace = item.get('https://api.openai.com/auth', {})
            for value in (item.get('chatgpt_account_id'), namespace.get('chatgpt_account_id') if isinstance(namespace, dict) else None):
                if isinstance(value, str) and value.strip():
                    account_ids.add(value)
        if len(account_ids) > 1:
            raise ChatGPTAccountError('chatgpt.identity_mismatch')
        account_id = next(iter(account_ids), old.get('account_id', ''))
        if old.get('account_id') and account_id != old['account_id']:
            raise ChatGPTAccountError('chatgpt.refresh_mismatch')
        if not account_id or any(c in account_id for c in '\r\n'):
            raise ChatGPTAccountError('chatgpt.identity_missing')
        expiry = data.get('expires_in', 3600)
        if type(expiry) not in (int, float) or not 0 <= expiry <= 365 * 86400 or not math.isfinite(expiry):
            expiry = 3600
        refresh = data.get('refresh_token', old.get('refresh', ''))
        if not isinstance(refresh, str):
            raise ChatGPTAccountError('chatgpt.refresh_invalid')
        return {'access': access, 'refresh': refresh, 'expires': time.time() + expiry,
                'account_id': account_id, 'email': next((i['email'] for i in identity if isinstance(i.get('email'), str)), old.get('email', ''))}

    def headers(self, expected_epoch=None):
        with self.lock:
            if expected_epoch is not None and expected_epoch != self.epoch:
                raise ChatGPTAccountError('chatgpt.account_changed')
            if not self.credentials:
                raise ChatGPTAccountError('chatgpt.login_required')
            if self.credentials['expires'] <= time.time() + 60:
                if not self.credentials['refresh']:
                    raise ChatGPTAccountError('chatgpt.expired')
                candidate = self._token({'grant_type': 'refresh_token', 'refresh_token': self.credentials['refresh']})
                old = self.credentials
                self.credentials = candidate
                try:
                    self.save()
                except OSError:
                    self.credentials = old
                    raise
            return {'Authorization': 'Bearer ' + self.credentials['access'],
                    'ChatGPT-Account-Id': self.credentials['account_id'], 'originator': 'harness',
                    'User-Agent': f'harness/{CLIENT_VERSION}'}

    def refresh_models(self):
        with self.lock:
            data = self.request(BASE_URL + '/models?' + urllib.parse.urlencode({'client_version': CLIENT_VERSION}), headers=self.headers())
            rows = data.get('models')
            if not isinstance(rows, list):
                raise ChatGPTAccountError('chatgpt.models_missing')
            models = []
            seen = set()
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get('slug'), str) or not row['slug'] or row['slug'] in seen:
                    continue
                if row.get('visibility') == 'hide' or row.get('supported_in_api') is False:
                    continue
                seen.add(row['slug'])
                levels = row.get('supported_reasoning_levels')
                efforts = [v['effort'] for v in levels if isinstance(v, dict) and isinstance(v.get('effort'), str)] if isinstance(levels, list) else []
                model = {'id': 'chatgpt/' + row['slug'], 'label': row['display_name'] if isinstance(row.get('display_name'), str) else row['slug'],
                         'provider': 'chatgpt', 'description': row['description'] if isinstance(row.get('description'), str) else '',
                         'supports_reasoning': bool(efforts), 'reasoning_efforts': efforts}
                for source, target in [('context_window', 'context_window'), ('max_output_tokens', 'max_output')]:
                    if type(row.get(source)) is int and row[source] > 0:
                        model[target] = row[source]
                models.append(model)
            if not models:
                raise ChatGPTAccountError('chatgpt.models_empty')
            old = self.models, self.selected
            self.models = models
            if self.selected not in {m['id'] for m in models}:
                self.selected = models[0]['id']
            try:
                self.save()
            except OSError:
                self.models, self.selected = old
                raise
            self.error = ''
            return self.status()

    def select(self, model):
        """保存模型选择，返回本次是否提交了变更。"""
        with self.lock:
            if not isinstance(model, str) or model not in {m['id'] for m in self.models}:
                raise ChatGPTAccountError('chatgpt.model_unknown')
            if model == self.selected:
                return False
            old = self.selected
            self.selected = model
            try:
                self.save()
            except OSError:
                self.selected = old
                raise
            return True

    def resolve(self, requested=None):
        with self.lock:
            selected = requested if requested and requested.startswith('chatgpt/') else self.selected
            model = next((deepcopy(m) for m in self.models if m['id'] == selected), None)
            if model is None:
                raise ChatGPTAccountError('chatgpt.model_unavailable')
            return model

    def cancel_login(self, expected_server=None):
        with self.lifecycle:
            return self._cancel_login(expected_server)

    def _cancel_login(self, expected_server=None):
        with self.lock:
            if expected_server is not None and self.server is not expected_server:
                return
            self.pending = None
            server, self.server = self.server, None
            timer, self.login_timer = self.login_timer, None
        if timer:
            timer.cancel()
        if server:
            try:
                server.shutdown()
            finally:
                server.server_close()

    def logout(self):
        with self.lifecycle:
            return self._logout()

    def _logout(self):
        self.cancel_login()
        with self.lock:
            old = self.credentials, self.models, self.selected
            self.credentials, self.models, self.selected = None, [], ''
            try:
                self.save()
            except OSError:
                self.credentials, self.models, self.selected = old
                raise
            self.epoch += 1
            self.error = ''
            return self.status()

    def login(self):
        with self.lifecycle:
            return self._login()

    def _login(self):
        self.cancel_login()
        verifier = secrets.token_urlsafe(48)
        state = secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        account = self
        language = current_language.get()

        class Callback(BaseHTTPRequestHandler):
            def log_message(self, *args):
                """回调地址包含授权码，不记录访问日志。"""

            def do_GET(self):
                # 回调由独立 HTTP 线程执行，使用发起此次登录时的语言。
                with current_language.set(language):
                    self._complete_login()

            def _respond(self, status, message, *, success=False):
                from .chatgpt_callback import render_callback
                nonce = secrets.token_urlsafe(24)
                body = render_callback(message, success=success, nonce=nonce).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', f"default-src 'none'; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
                self.end_headers()
                self.wfile.write(body)

            def _complete_login(self):
                parsed = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
                with account.lock:
                    valid = all(len(query.get(key, [])) <= 1 for key in ('state', 'code', 'error')) and parsed.path == '/auth/callback' and account.pending == state and secrets.compare_digest(query.get('state', [''])[0].encode('utf-8'), state.encode('utf-8'))
                    if not valid:
                        self._respond(400, tr('chatgpt.callback_invalid'))
                        return
                    try:
                        code = query.get('code', [''])[0]
                        if not code or query.get('error'):
                            raise ChatGPTAccountError('chatgpt.declined')
                        candidate = account._token({'grant_type': 'authorization_code', 'code': code,
                                                   'redirect_uri': REDIRECT_URI, 'code_verifier': verifier})
                        old = account.credentials, account.models, account.selected
                        account.credentials, account.models, account.selected = candidate, [], ''
                        try:
                            account.save()
                        except OSError:
                            account.credentials, account.models, account.selected = old
                            raise
                        account.epoch += 1
                        account.refresh_models()
                        message = tr('chatgpt.complete')
                        account.error = ''
                    except (ValueError, OSError) as exc:
                        account.error = exc if isinstance(exc, ChatGPTAccountError) else str(exc) if isinstance(exc, ValueError) else ChatGPTAccountError('chatgpt.save_failed')
                        message = str(account.error) if isinstance(account.error, ChatGPTAccountError) else tr('chatgpt.attention')
                    account.pending = None
                self._respond(200, message, success=not account.error)
                threading.Thread(target=lambda: account.cancel_login(server), daemon=True).start()

        try:
            server = ThreadingHTTPServer(('127.0.0.1', 1455), Callback)
        except OSError:
            raise ChatGPTAccountError('chatgpt.port_busy') from None
        with self.lock:
            self.server, self.pending, self.error = server, state, ''
        def expire():
            with self.lock:
                current = self.pending == state
            if current:
                self.cancel_login(server)
        timer = threading.Timer(300, expire)
        timer.daemon = True
        started = False
        try:
            with self.lock:
                self.login_timer = timer
            threading.Thread(target=server.serve_forever, daemon=True).start()
            started = True
            timer.start()
        except BaseException:
            # serve_forever 尚未启动时调用 shutdown 会永久等待；只关闭已绑定端口。
            with self.lock:
                self.server, self.pending, self.login_timer = None, None, None
            timer.cancel()
            try:
                if started:
                    server.shutdown()
            finally:
                server.server_close()
            raise
        params = {'response_type': 'code', 'client_id': CLIENT_ID, 'redirect_uri': REDIRECT_URI,
                  'scope': 'openid profile email offline_access', 'state': state, 'code_challenge': challenge,
                  'code_challenge_method': 'S256', 'codex_cli_simplified_flow': 'true',
                  'id_token_add_organizations': 'true', 'originator': 'harness'}
        return {**self.status(), 'authorization_url': 'https://auth.openai.com/oauth/authorize?' + urllib.parse.urlencode(params)}