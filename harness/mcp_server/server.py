"""官方 MCP SDK 协议适配，支持 HTTP 与本地 stdio。"""
from __future__ import annotations

from contextlib import asynccontextmanager
import json
import html
from .consent import page as consent_page, headers as consent_headers
from urllib.parse import parse_qs
from urllib.parse import urlsplit

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings
import mcp.types as types
from starlette.responses import HTMLResponse, RedirectResponse, JSONResponse
from starlette.routing import Route

from ..execution import ExecutionRuntime
from ..execution.catalog import definitions
from ..i18n import tr, resources
from ..core.language import current_language, resolve_language
from .auth import LocalOAuthProvider, SCOPES




class RequestLanguageMiddleware:
    """每个 HTTP 请求独立绑定服务配置语言，覆盖授权 SDK 路由。"""
    def __init__(self, app, language):
        self.app = app
        self.language = language

    async def __call__(self, scope, receive, send):
        with current_language.set(self.language):
            await self.app(scope, receive, send)


def required_scope(name):
    if name in {"harness_exec", "harness_job_cancel", "harness_verify", "harness_verification_register", "harness_pipeline_start", "harness_pipeline_control"}:
        return "command.run"
    if name in {"harness_apply_patch", "harness_finalize", "harness_image_request", "harness_image_deliver"}:
        return "project.write"
    return "project.read"


def create_server(config, *, provider: LocalOAuthProvider | None = None, local_principal="local", runtime_holder=None, manage_runtime=True):
    """运行层生命周期属于服务，不属于短暂的 MCP 连接。"""
    holder = runtime_holder if runtime_holder is not None else {}
    language = resolve_language(config.language).effective_language

    def localized(handler):
        async def invoke(*args, **kwargs):
            with current_language.set(language):
                return await handler(*args, **kwargs)
        return invoke

    @asynccontextmanager
    async def lifespan(server):
        if not manage_runtime:
            yield holder['runtime']
            return
        async with ExecutionRuntime(config) as runtime:
            holder["runtime"] = runtime
            yield runtime

    async def list_tools(ctx, params):
        tools = []
        registry = holder["runtime"].registry
        descriptions = {name: description for name, description, *_ in definitions()}
        for name in registry.names():
            definition = registry.get(name)
            scope = required_scope(definition.schema.name)
            tools.append(types.Tool(name=definition.schema.name, description=descriptions.get(name, definition.schema.description),
                input_schema=definition.schema.parameters,
                annotations=types.ToolAnnotations(read_only_hint=definition.effect == "read", destructive_hint=definition.effect != "read",
                                                  idempotent_hint=definition.idempotent, open_world_hint=definition.effect == "execute" or definition.schema.name == "harness_image_deliver"),
                meta={**({"securitySchemes": [{"type": "oauth2", "scopes": [scope]}]} if provider else {}),
                      **({"openai/fileParams": ["file"]} if definition.schema.name == "harness_image_deliver" else {})}))
        return types.ListToolsResult(tools=tools)

    async def call_tool(ctx, params):
        token = get_access_token()
        if provider:
            if token is None or not token.subject or required_scope(params.name) not in token.scopes:
                return types.CallToolResult(is_error=True, content=[types.TextContent(type="text", text=tr('execution.mcp.scope_required'))],
                    meta={"mcp/www_authenticate": f'Bearer scope="{required_scope(params.name)}", resource_metadata="{provider.public_url}/.well-known/oauth-protected-resource/mcp"'})
            if params.name == "harness_finalize" and (params.arguments or {}).get("commit_message") and "git.commit" not in token.scopes:
                return types.CallToolResult(is_error=True, content=[types.TextContent(type="text", text=tr('execution.mcp.commit_scope_required'))])
            principal = token.subject
        else:
            principal = local_principal
        value = await holder["runtime"].call(principal, params.name, params.arguments or {})
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(value, ensure_ascii=False))],
                                    structured_content=value, is_error=value["is_error"])

    return Server("harness-executor", version="1.0.0", instructions=resources(language)["execution.mcp.instructions"], lifespan=lifespan,
                  on_list_tools=localized(list_tools), on_call_tool=localized(call_tool))


def create_http_app(config, *, public_url=None, principal="local", runtime_holder=None, manage_runtime=True):
    """默认仅本机访问；公网模式要求完整 OAuth 流程。"""
    language = resolve_language(config.language).effective_language
    with current_language.set(language):
        provider = LocalOAuthProvider(config.home, public_url, principal) if public_url else None
    server = create_server(config, provider=provider, local_principal=principal,
                           runtime_holder=runtime_holder, manage_runtime=manage_runtime)
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    auth = None
    routes = [Route("/healthz", lambda request: JSONResponse({"ok": True, "mode": "executor"}))]
    if provider:
        public = urlsplit(provider.public_url)
        hosts.append(public.netloc)
        origins.append(provider.public_url)
        auth = AuthSettings(issuer_url=provider.public_url, resource_server_url=provider.resource,
            validate_token_resource=True, required_scopes=["project.read"],
            client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=SCOPES, default_scopes=SCOPES),
            revocation_options=RevocationOptions(enabled=True))

        async def consent(request):
            ticket = request.query_params.get("ticket", "")
            # 普通表单在 no-referrer 下会发送 Origin: null；同源策略保留
            # 登录 POST 的真实来源，同时不向跨站 OAuth 回调泄露票据。
            headers = consent_headers()
            try:
                if provider.password_enabled:
                    nonce, params = provider.login_form(ticket)
                    if request.method == "POST":
                        if request.headers.get("origin") not in {None, provider.public_url}:
                            raise ValueError(tr("execution.consent.origin"))
                        body = bytearray()
                        async for chunk in request.stream():
                            body.extend(chunk)
                            if len(body) > 4096:
                                raise ValueError(tr("execution.consent.large"))
                        fields = parse_qs(body.decode("utf-8"), keep_blank_values=True)
                        if set(fields) != {"password", "csrf"} or any(len(value) != 1 for value in fields.values()):
                            raise ValueError(tr("execution.consent.invalid"))
                        provider.password_approve(ticket, fields["password"][0], fields["csrf"][0])
                    else:
                        return HTMLResponse(consent_page(ticket, nonce, params, all_projects=bool(config.workspace_registry)), headers=headers)
                elif request.method == "POST":
                    raise ValueError(tr("execution.consent.disabled"))
                redirect = provider.consent_redirect(ticket)
            except (ValueError, UnicodeError) as exc:
                try:
                    retry_nonce, retry_params = provider.login_form(ticket)
                except ValueError:
                    retry_nonce, retry_params = '', None
                return HTMLResponse(consent_page(ticket, retry_nonce, retry_params, error=str(exc),
                    all_projects=bool(config.workspace_registry)), status_code=400, headers=headers)
            if redirect:
                return RedirectResponse(redirect, status_code=302, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
            return HTMLResponse(f'<!doctype html><html lang="{language}"><meta charset=utf-8><meta http-equiv=refresh content=3><title>{html.escape(tr("execution.consent.waiting_title"))}</title><p>{html.escape(tr("execution.consent.waiting"))}</p></html>', headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "Content-Security-Policy": "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"})

        routes.append(Route("/consent", consent, methods=["GET", "POST"]))
    app = server.streamable_http_app(stateless_http=True, json_response=True, auth=auth,
        auth_server_provider=provider, token_verifier=provider, custom_starlette_routes=routes,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins))
    app.add_middleware(RequestLanguageMiddleware, language=language)
    app.state.oauth_provider = provider
    if provider:
        # 发现信息仅包含公开端点和协议能力，允许跨域浏览器客户端读取。
        # 此处的通配 CORS 不适用于工具调用或授权操作，不能在响应中加入凭据或项目数据。
        async def metadata(request):
            return JSONResponse({"issuer": provider.public_url, "authorization_endpoint": provider.public_url + "/authorize",
                "token_endpoint": provider.public_url + "/token", "registration_endpoint": provider.public_url + "/register",
                "revocation_endpoint": provider.public_url + "/revoke", "scopes_supported": SCOPES,
                "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
                "revocation_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
                "resource_indicators_supported": True},
                headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"})

        async def resource_metadata(request):
            return JSONResponse({"resource": provider.resource, "authorization_servers": [provider.public_url],
                "scopes_supported": SCOPES, "bearer_methods_supported": ["header"], "resource_name": "Harness Executor"},
                headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"})

        # 显式覆盖 SDK 遗漏公开客户端方式的发现信息，不改动第三方 SDK。
        app.router.routes[0:0] = [Route("/.well-known/oauth-authorization-server", metadata),
            Route("/.well-known/oauth-protected-resource/mcp", resource_metadata),
            Route("/.well-known/oauth-protected-resource", resource_metadata)]
    return app
