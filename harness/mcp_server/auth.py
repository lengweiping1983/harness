"""通过本机批准或网页密码登录完成 OAuth 授权。"""
from __future__ import annotations

from ..core.sqlite import prepare_database
from ..i18n import tr

import json
import hashlib
import hmac
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import urlencode, urlsplit

from mcp.server.auth.provider import AccessToken, AuthorizationCode, RefreshToken, AuthorizeError, RegistrationError, TokenError
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

SCOPES = ["project.read", "project.write", "command.run", "git.commit"]


SCHEMA = """
CREATE TABLE clients(id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE pending(id TEXT PRIMARY KEY, client TEXT NOT NULL,
    params TEXT NOT NULL, expires REAL NOT NULL, approved INTEGER NOT NULL DEFAULT 0);
CREATE TABLE codes(id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE tokens(id TEXT PRIMARY KEY, kind TEXT NOT NULL,
    family TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE login_failures(at REAL NOT NULL);
CREATE TABLE consent_receipts(id TEXT PRIMARY KEY, params TEXT NOT NULL,
    code TEXT NOT NULL, redirect TEXT NOT NULL, expires REAL NOT NULL);
"""


class _ClosingConnection(sqlite3.Connection):
    """原样传播异常，在事务提交或回滚后关闭连接。"""

    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class LocalOAuthProvider:
    """授权请求必须经本机批准或验证用户登录密码。"""

    def __init__(self, home: Path, public_url: str, principal: str = "local"):
        self.path = home / "oauth.sqlite3"
        home.mkdir(parents=True, exist_ok=True)
        self.public_url = public_url.rstrip("/")
        self.resource = self.public_url + "/mcp"
        self.principal = principal
        self.login_path = home / "oauth-login.json"
        parts = urlsplit(self.public_url)
        if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost", "::1"}):
            raise ValueError(tr('execution.oauth.bcbc50913ed1'))
        if parts.path not in {"", "/"} or parts.query or parts.fragment or parts.username:
            raise ValueError(tr('execution.oauth.c6447538ca18'))
        with self.db() as db:
            prepare_database(db, SCHEMA)

    def db(self):
        connection = sqlite3.connect(self.path, timeout=5, factory=_ClosingConnection)
        connection.row_factory = sqlite3.Row
        return connection

    async def get_client(self, client_id):
        with self.db() as db:
            row = db.execute("SELECT data FROM clients WHERE id=?", (client_id,)).fetchone()
        return OAuthClientInformationFull.model_validate_json(row["data"]) if row else None

    async def register_client(self, client_info):
        for uri in client_info.redirect_uris:
            parts = urlsplit(str(uri))
            chatgpt = parts.scheme == "https" and parts.hostname in {"chatgpt.com", "chat.openai.com"} and (parts.path == "/connector_platform_oauth_redirect" or parts.path.startswith(("/oauth/", "/connector/oauth/")))
            loopback = parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost", "::1"}
            if not (chatgpt or loopback) or parts.fragment or parts.username:
                raise RegistrationError(error="invalid_redirect_uri", error_description=tr('execution.oauth.ea8ee131b638'))
        with self.db() as db:
            db.execute("INSERT INTO clients VALUES(?,?)", (client_info.client_id, client_info.model_dump_json()))

    async def authorize(self, client, params):
        if params.resource and str(params.resource).rstrip("/") != self.resource:
            raise AuthorizeError(error="invalid_target", error_description=tr('execution.oauth.27141b575a43'))
        params.resource = self.resource
        params.scopes = params.scopes or (client.scope or "project.read").split()
        if not params.scopes or set(params.scopes) - set(SCOPES):
            raise AuthorizeError(error="invalid_scope", error_description=tr('execution.oauth.789cdebc86d9'))
        ticket = secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute("INSERT INTO pending(id,client,params,expires) VALUES(?,?,?,?)", (ticket, client.client_id, params.model_dump_json(), time.time() + 600))
        return self.public_url + "/consent?" + urlencode({"ticket": ticket})

    def initialize_login(self):
        """首次生成登录密码；持久化文件只保存盐、派生哈希和表单密钥。"""
        password = secrets.token_urlsafe(24)
        salt = secrets.token_bytes(32)
        data = {"salt": salt.hex(), "hash": hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex(),
                "csrf_key": secrets.token_hex(32)}
        with self.login_path.open("x", encoding="utf-8") as file:
            json.dump(data, file)
        return password

    @property
    def password_enabled(self):
        return self.login_path.is_file()

    def login_form(self, ticket):
        """表单令牌绑定未过期的授权请求，并显示其真实回调和权限。"""
        with self.db() as db:
            row = db.execute("SELECT * FROM pending WHERE id=? AND expires>?", (ticket, time.time())).fetchone()
            if row is None:
                row = self._consent_receipt(db, ticket)
        if row is None:
            raise ValueError(tr('execution.oauth.f9822903486e'))
        data = json.loads(self.login_path.read_text(encoding="utf-8"))
        nonce = hmac.new(bytes.fromhex(data["csrf_key"]), ticket.encode(), hashlib.sha256).hexdigest()
        return nonce, json.loads(row["params"])

    def password_approve(self, ticket, password, csrf):
        """验证表单与密码；失败计数跨请求、客户端和进程重启生效。"""
        nonce, _ = self.login_form(ticket)
        if not hmac.compare_digest(nonce, csrf):
            raise ValueError(tr('execution.oauth.8cbc66bb34fa'))
        with self.db() as db:
            db.execute("DELETE FROM login_failures WHERE at<?", (time.time() - 900,))
            if db.execute("SELECT COUNT(*) FROM login_failures").fetchone()[0] >= 10:
                raise ValueError(tr('execution.oauth.9cc5df1a440f'))
            # 先记账，避免并发请求绕过尝试次数限制。
            attempt_id = db.execute("INSERT INTO login_failures VALUES(?)", (time.time(),)).lastrowid
        data = json.loads(self.login_path.read_text(encoding="utf-8"))
        derived = hashlib.scrypt(password.encode(), salt=bytes.fromhex(data["salt"]), n=16384, r=8, p=1).hex()
        if not hmac.compare_digest(derived, data["hash"]):
            raise ValueError(tr('execution.oauth.b7c81cd1746b'))
        with self.db() as db:
            # 成功登录不占用失败配额，重复点击也不应锁定真实用户。
            db.execute("DELETE FROM login_failures WHERE rowid=?", (attempt_id,))
        self.approve(ticket)

    @staticmethod
    def _consent_receipt(db, ticket):
        """只重试尚未兑换且未过期的同一个授权码，不重新发放权限。"""
        return db.execute("SELECT r.* FROM consent_receipts r JOIN codes c ON c.id=r.code WHERE r.id=? AND r.expires>?",
                          (ticket, time.time())).fetchone()

    def pending_requests(self):
        """只从本地 CLI 查看待批准请求，不返回任何登录密钥。"""
        with self.db() as db:
            rows = db.execute("SELECT * FROM pending WHERE expires>? AND approved=0", (time.time(),)).fetchall()
        return [{"ticket": row["id"], "client_id": row["client"], "scopes": json.loads(row["params"])["scopes"],
                 "redirect_uri": json.loads(row["params"])["redirect_uri"], "expires": row["expires"]} for row in rows]

    def approve(self, ticket):
        """调用者必须是能访问本机配置与存储的实际用户。"""
        with self.db() as db:
            changed = db.execute("UPDATE pending SET approved=1 WHERE id=? AND expires>? AND approved=0", (ticket, time.time())).rowcount
            if changed == 0 and self._consent_receipt(db, ticket) is not None:
                return
        if changed != 1:
            raise ValueError(tr('execution.oauth.351fe0d16981'))

    def consent_redirect(self, ticket):
        """批准后发放一次性授权码；普通等待页面不具有批准权限。"""
        with self.db() as db:
            receipt = self._consent_receipt(db, ticket)
            if receipt is not None:
                return receipt["redirect"]
            row = db.execute("SELECT * FROM pending WHERE id=? AND expires>?", (ticket, time.time())).fetchone()
            if row is None:
                raise ValueError(tr('execution.oauth.a79c26f24fd8'))
            if not row["approved"]:
                return None
            params = json.loads(row["params"])
            code = AuthorizationCode(code=secrets.token_urlsafe(32), client_id=row["client"], scopes=params["scopes"],
                expires_at=time.time() + 120, code_challenge=params["code_challenge"], redirect_uri=params["redirect_uri"],
                redirect_uri_provided_explicitly=params["redirect_uri_provided_explicitly"], resource=self.resource, subject=self.principal)
            db.execute("INSERT INTO codes VALUES(?,?)", (code.code, code.model_dump_json()))
            query = {"code": code.code, "iss": self.public_url}
            if params.get("state") is not None:
                query["state"] = params["state"]
            redirect = str(code.redirect_uri)
            redirect += ("&" if "?" in redirect else "?") + urlencode(query)
            db.execute("DELETE FROM consent_receipts WHERE expires<=?", (time.time(),))
            db.execute("INSERT INTO consent_receipts VALUES(?,?,?,?,?)", (ticket, row["params"], code.code, redirect, code.expires_at))
            db.execute("DELETE FROM pending WHERE id=?", (ticket,))
        return redirect

    async def load_authorization_code(self, client, authorization_code):
        with self.db() as db:
            row = db.execute("SELECT data FROM codes WHERE id=?", (authorization_code,)).fetchone()
        if row:
            code = AuthorizationCode.model_validate_json(row["data"])
            if code.client_id == client.client_id and code.expires_at > time.time() and code.resource == self.resource:
                return code
        return None

    def _issue(self, db, client_id, scopes, subject, family=None):
        family = family or secrets.token_urlsafe(24)
        access = AccessToken(token=secrets.token_urlsafe(32), client_id=client_id, scopes=scopes,
                             expires_at=int(time.time()) + 3600, resource=self.resource, subject=subject)
        refresh = RefreshToken(token=secrets.token_urlsafe(32), client_id=client_id, scopes=scopes,
                               expires_at=int(time.time()) + 86400 * 7, resource=self.resource, subject=subject)
        for kind, token in (("access", access), ("refresh", refresh)):
            db.execute("INSERT INTO tokens VALUES(?,?,?,?)", (token.token, kind, family, token.model_dump_json()))
        return OAuthToken(access_token=access.token, refresh_token=refresh.token, token_type="Bearer", expires_in=3600, scope=" ".join(scopes))

    async def exchange_authorization_code(self, client, authorization_code):
        with self.db() as db:
            if db.execute("DELETE FROM codes WHERE id=?", (authorization_code.code,)).rowcount != 1:
                raise TokenError(error="invalid_grant", error_description=tr('execution.oauth.e8340fa211fd'))
            db.execute("DELETE FROM consent_receipts WHERE code=?", (authorization_code.code,))
            return self._issue(db, client.client_id, authorization_code.scopes, authorization_code.subject)

    async def load_refresh_token(self, client, refresh_token):
        with self.db() as db:
            row = db.execute("SELECT data FROM tokens WHERE id=? AND kind='refresh'", (refresh_token,)).fetchone()
        if row:
            token = RefreshToken.model_validate_json(row["data"])
            if token.client_id == client.client_id and token.expires_at > time.time() and token.resource == self.resource:
                return token
        return None

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        if set(scopes) - set(refresh_token.scopes):
            raise TokenError(error="invalid_scope", error_description=tr('execution.oauth.9882d7b67f79'))
        with self.db() as db:
            row = db.execute("SELECT family FROM tokens WHERE id=? AND kind='refresh'", (refresh_token.token,)).fetchone()
            if row is None:
                raise TokenError(error="invalid_grant", error_description=tr('execution.oauth.f414345f52fc'))
            # 读取之后仍需原子消费原凭据，不能由并发旧快照再次轮换整个家族。
            if db.execute("DELETE FROM tokens WHERE id=? AND kind='refresh'", (refresh_token.token,)).rowcount != 1:
                raise TokenError(error="invalid_grant", error_description=tr('execution.oauth.f414345f52fc'))
            db.execute("DELETE FROM tokens WHERE family=?", (row["family"],))
            return self._issue(db, client.client_id, scopes, refresh_token.subject, row["family"])

    async def load_access_token(self, token):
        with self.db() as db:
            row = db.execute("SELECT data FROM tokens WHERE id=? AND kind='access'", (token,)).fetchone()
        if row:
            access = AccessToken.model_validate_json(row["data"])
            if access.expires_at > time.time() and access.resource == self.resource:
                return access
        return None

    async def verify_token(self, token):
        """资源服务器在每次调用前重新检查到期时间、撤销和资源绑定。"""
        return await self.load_access_token(token)

    async def revoke_token(self, token):
        with self.db() as db:
            row = db.execute("SELECT family FROM tokens WHERE id=?", (token.token,)).fetchone()
            if row:
                db.execute("DELETE FROM tokens WHERE family=?", (row["family"],))

    async def exchange_identity_assertion(self, client, params):
        raise TokenError(error="unsupported_grant_type", error_description=tr('execution.oauth.23ee312eae7d'))
