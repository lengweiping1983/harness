"""可信本机工作台入口；共享连接与跨站请求不能读取或管理实例。"""
from __future__ import annotations

from urllib.parse import urlsplit,quote

from ...core.errors import HarnessError
from ...i18n import tr
from ...app.superagent_media import SourceMedia
from ...app.superagent_state import open_state
from ...app.superagent_common import _json as strict_loads
from ..errors import ApiError
from ..responses import json_response,bytes_response
from ..router import Router
from ..query import read_query
from ..superagent_protocol import READ_QUERY


def build_router(ctx):
    router = Router()

    def local(request):
        try:
            authority = urlsplit('http://'+request.header('Host'))
            authority_port = authority.port or 80
            origin = request.header('Origin')
            source = urlsplit(origin) if origin else None
            denied = (ctx.shared_access is not None or request.subject is not None or request.client_ip not in {'127.0.0.1','::1'}
                or authority.hostname not in {'127.0.0.1','localhost','::1'} or authority.username is not None or authority.password is not None
                or bool(authority.path or authority.query or authority.fragment)
                or any(site.strip().lower() in {'cross-site','same-site'} for site in request.header('Sec-Fetch-Site').split(','))
                or source is not None and (source.scheme not in {'http','https'} or source.hostname!=authority.hostname
                    or source.username is not None or source.password is not None or bool(source.path or source.query or source.fragment)
                    or (source.port or (443 if source.scheme=='https' else 80))!=authority_port))
        except ValueError:
            denied = True
        if denied:
            raise ApiError(tr('ui.sg.error.local'),status=403,code='superagent/local-only',details={'copy':{'key':'ui.sg.error.local'}})

    def payload(request):
        try:
            if not 0<len(request.body)<=1048576:
                raise ValueError
            data = strict_loads(request.body.decode('utf-8'))
            if not isinstance(data,dict):
                raise ValueError
            return data
        except (ValueError,UnicodeError,RecursionError) as exc:
            raise ApiError(tr('ui.sg.error.invalid'),status=400,code='superagent/invalid',details={'copy':{'key':'ui.sg.error.invalid'}}) from exc

    def invoke(request,operation):
        local(request)
        async def run():
            manager = ctx.harness.superagent_workbench
            result = operation(manager)
            import inspect
            return await result if inspect.isawaitable(result) else result
        try:
            return ctx.call(run())
        except (HarnessError,OSError,ValueError,TypeError,RuntimeError,LookupError) as exc:
            code = getattr(exc,'code','superagent/workbench-operation')
            resource = {'superagent/query.cursor':'ui.sg.error.cursor','superagent/query.missing':'ui.sg.error.missing',
                'superagent/credentials-required':'ui.sg.error.credentials','superagent/runtime-busy':'ui.sg.error.busy',
                'superagent/status-unavailable':'ui.sg.error.uninitialized',
                'superagent/invalid':'ui.sg.error.invalid','superagent/workbench.connection':'ui.sg.error.config',
                'superagent/instance-conflict':'ui.sg.error.config'}.get(code,'ui.sg.error.operation')
            status = 404 if isinstance(exc,(FileNotFoundError,LookupError)) else 400 if isinstance(exc,(ValueError,TypeError)) else 409
            raise ApiError(tr(resource),status=status,code=code,details={'copy':{'key':resource}}) from exc

    def response(data):
        result = json_response(data); result.headers['Cache-Control']='no-store'
        return result

    @router.get('/api/v1/superagents')
    def connections(request):
        return response(invoke(request,lambda manager:{'connections':manager.connections()}))

    @router.post('/api/v1/superagents/connect')
    def connect(request):
        local(request)
        data = payload(request)
        if not isinstance(data,dict) or set(data)!={'config_path'}:
            raise ApiError(tr('ui.sg.error.invalid'),status=400,code='superagent/invalid',details={'copy':{'key':'ui.sg.error.invalid'}})
        return response(invoke(request,lambda manager:manager.connect(data['config_path'])))

    @router.get('/api/v1/superagents/{connection_id}')
    def connection(request,connection_id):
        return response(invoke(request,lambda manager:manager.connection(connection_id)))

    @router.get('/api/v1/superagents/{connection_id}/read')
    def read(request,connection_id):
        local(request)
        try:
            values = read_query(request,READ_QUERY)
        except ApiError as exc:
            raise ApiError(tr('ui.sg.error.invalid'),status=400,code='superagent/invalid',details={'copy':{'key':'ui.sg.error.invalid'}}) from exc
        resource = values.pop('resource')
        allowed = {'mission':{'resource','mission_id'},'policy':{'resource'},'status':{'resource'},'local_options':{'resource'},
            'effect':{'resource','identifier'},'receipt':{'resource','identifier'}}.get(resource,{'resource','mission_id','limit','cursor'})
        if set(request.query)-allowed:
            raise ApiError(tr('ui.sg.error.invalid'),status=400,code='superagent/invalid',details={'copy':{'key':'ui.sg.error.invalid'}})
        key = connection_id
        def query(manager):
            entry = manager.connection(key)
            return {'connection_id':key,'instance_id':entry['instance_id'],'resource':resource,'data':manager.read(key,resource,**values)}
        return response(invoke(request,query))

    @router.post('/api/v1/superagents/{connection_id}/commands')
    def command(request,connection_id):
        local(request)
        data = payload(request)
        if not isinstance(data,dict) or set(data)!={'request_id','action','payload'}:
            raise ApiError(tr('ui.sg.error.invalid'),status=400,code='superagent/invalid',details={'copy':{'key':'ui.sg.error.invalid'}})
        key = connection_id
        async def dispatch(manager):
            return {'connection_id':key,**await manager.dispatch(key,**data)}
        return response(invoke(request,dispatch))

    @router.get('/api/v1/superagents/{connection_id}/media/{media_id}')
    def media(request,connection_id,media_id):
        def cached(manager):
            entry = manager._entry(connection_id)
            return SourceMedia(open_state(entry['spec'],read_only=True),entry['spec']).cached(media_id)
        row,raw,resource = invoke(request,cached)
        name = resource['name'] or row['media_id']
        disposition = 'inline' if row['content_type'].startswith('image/') else 'attachment'
        return bytes_response(raw,content_type=row['content_type'],headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
            'Content-Security-Policy':"default-src 'none'; sandbox",'Content-Disposition':disposition+"; filename*=UTF-8''"+quote(name,safe='')})

    return router
