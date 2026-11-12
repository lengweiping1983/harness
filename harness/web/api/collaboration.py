"""邀请与成员接入；主持人操作复用原授权，公开领取不获得会话读取权。"""

from ...app.collaboration_errors import collaboration_error_message
from ...app.session_invitations import ROLES
from ...core.errors import HarnessError
from ...interaction.questions import QuestionError
from ..errors import ApiError
from ..router import Router
from ..responses import json_response

PUBLIC_INVITATION_PATHS = ('/api/v1/access/invitations/claim', '/api/v1/access/invitations/status',
                           '/api/v1/access/invitations/enter')


def build_router(ctx):
    router = Router()

    def actor(request, session_id):
        if request.subject:
            return request.subject
        if ctx.shared_access is not None:
            raise ApiError(collaboration_error_message('access/unauthenticated'), code='access/unauthenticated', status=401)
        state = ctx.harness.session_access.snapshot(session_id)
        if not state['enabled']:
            raise ApiError(collaboration_error_message('access/not-enabled'), code='access/not-enabled', status=409)
        return state['owner']

    def execute(operation):
        async def translated():
            try:
                return await operation()
            except HarnessError as exc:
                conflicts = {'access/stale-revision', 'invitation/unavailable', 'invitation/claim-conflict',
                             'decision/stale-revision', 'decision/request-conflict', 'decision/closed'}
                status = 409 if exc.code in conflicts else 400 if exc.code.startswith('decision/invalid-') else 403
                raise ApiError(collaboration_error_message(exc.code), code=exc.code, status=status) from None
        return ctx.call(translated())

    @router.post('/api/v1/sessions/{session_id}/collaboration/enable')
    def enable(request, session_id):
        if ctx.shared_access is not None or request.json():
            raise ApiError(collaboration_error_message('access/denied'), code='access/denied', status=403)
        return json_response(execute(lambda: ctx.harness.session_access.initialize(session_id, 'local-owner')))

    @router.get('/api/v1/sessions/{session_id}/collaboration')
    def snapshot(request, session_id):
        async def read():
            subject = actor(request, session_id)
            state = ctx.harness.session_access.require(session_id, subject, 'read')
            return {'origin': ctx.shared_access.browser_origin if ctx.shared_access is not None else getattr(ctx.harness, 'shared_origin', None), 'access': {key: state[key] for key in ('session_id', 'owner', 'revision', 'members', 'profiles')},
                    'invitations': ctx.harness.session_invitations.list(session_id, subject)
                    if 'manage' in state['members'].get(subject, ()) else []}
        return json_response(execute(read))

    @router.post('/api/v1/sessions/{session_id}/invitations')
    def create(request, session_id):
        origin = ctx.shared_access.browser_origin if ctx.shared_access is not None else getattr(ctx.harness, 'shared_origin', None)
        if not origin:
            raise ApiError(collaboration_error_message('invitation/sharing-not-running'), code='invitation/sharing-not-running', status=409)
        body = request.json()
        if set(body) - {'role', 'expires_in'}:
            raise ApiError(collaboration_error_message('invitation/invalid-options'), code='invitation/invalid-options')
        return json_response(execute(lambda: ctx.harness.session_invitations.create(session_id,
            actor=actor(request, session_id), **body)))

    @router.post('/api/v1/sessions/{session_id}/invitations/{invitation_id}/approve')
    def approve(request, session_id, invitation_id):
        body = request.json()
        if set(body) != {'expected_revision'}:
            raise ApiError(collaboration_error_message('access/invalid-options'), code='access/invalid-options')
        return json_response(execute(lambda: ctx.harness.session_invitations.approve(session_id,
            invitation_id, actor=actor(request, session_id), **body)))

    @router.post('/api/v1/sessions/{session_id}/invitations/{invitation_id}/revoke')
    def revoke(request, session_id, invitation_id):
        if request.json():
            raise ApiError(collaboration_error_message('access/invalid-options'), code='access/invalid-options')
        async def run():
            return {'revoked': await ctx.harness.session_invitations.revoke(session_id,
                invitation_id, actor=actor(request, session_id))}
        return json_response(execute(run))

    @router.post('/api/v1/sessions/{session_id}/members/{subject}')
    def member(request, session_id, subject):
        body = request.json()
        if set(body) != {'role', 'expected_revision'} or not isinstance(body['role'], str) or body['role'] not in (*ROLES, 'removed'):
            raise ApiError(collaboration_error_message('access/invalid-options'), code='access/invalid-options')
        return json_response(execute(lambda: ctx.harness.session_access.set_capabilities(session_id,
            actor=actor(request, session_id), subject=subject,
            capabilities=ROLES.get(body['role'], ()), expected_revision=body['expected_revision'])))

    @router.get('/api/v1/sessions/{session_id}/questions')
    def questions(request, session_id):
        return json_response({'questions': execute_read(lambda: ctx.harness.session_decisions.pending(
            session_id, actor(request, session_id)))})

    def execute_read(operation):
        async def read():
            return operation()
        return execute(read)

    @router.get('/api/v1/sessions/{session_id}/decisions/{request_id}')
    def receipt(request, session_id, request_id):
        row = execute_read(lambda: ctx.harness.session_decisions.receipt(session_id,
            actor(request, session_id), request_id))
        return json_response({'receipt': {key: value for key, value in row.items() if key != 'request_hash'} if row else None})

    @router.post('/api/v1/sessions/{session_id}/questions/{question_id}/answer')
    def answer(request, session_id, question_id):
        body = request.json()
        if set(body) != {'request_id', 'expected_revision', 'answers'}:
            raise ApiError(collaboration_error_message('decision/invalid-options'), code='decision/invalid-options')
        try:
            return json_response(execute(lambda: ctx.harness.session_decisions.answer(session_id, question_id,
                actor=actor(request, session_id), **body)))
        except QuestionError as exc:
            raise ApiError(str(exc), code='question/invalid-answer') from exc

    def public(request):
        shared = ctx.shared_access
        if (shared is None or shared.browser_origin is None or request.header('Origin') != shared.browser_origin
                or request.header('Host') != shared.browser_host or request.header('Authorization')
                or request.header('Content-Type').split(';')[0].strip() != 'application/json'):
            raise ApiError(collaboration_error_message('access/origin'), code='access/origin', status=403)
        return shared

    @router.post(PUBLIC_INVITATION_PATHS[0])
    def claim(request):
        public(request)
        body = request.json()
        if set(body) != {'token', 'display_name', 'claim_secret'}:
            raise ApiError(collaboration_error_message('invitation/invalid-claim'), code='invitation/invalid-claim')
        token = body.pop('token')
        return json_response(execute(lambda: ctx.harness.session_invitations.claim(token, **body)))

    async def lookup(body):
        if set(body) != {'token', 'claim_secret'}:
            raise ApiError(collaboration_error_message('invitation/invalid-claim'), code='invitation/invalid-claim')
        service = ctx.harness.session_invitations
        session_id, identifier = service.parse(body['token'], 'hi1')
        # 邀请与领取两个秘密都须匹配，不能只用可猜的申请编号查询。
        from ...app.session_invitations import digest
        import secrets
        row = service.claim_status(session_id, identifier, body['claim_secret'])
        if not secrets.compare_digest(row['digest'], digest(body['token'])):
            raise ApiError(collaboration_error_message('invitation/invalid-token'), code='invitation/invalid-token', status=403)
        return session_id, row

    @router.post(PUBLIC_INVITATION_PATHS[1])
    def status(request):
        public(request)
        async def read():
            _, row = await lookup(request.json())
            return {'id': row['id'], 'status': row['status'],
                    **({'subject': row['subject']} if row['status'] == 'approved' else {})}
        return json_response(execute(read))

    @router.post(PUBLIC_INVITATION_PATHS[2])
    def enter(request):
        shared = public(request)
        async def exchange():
            session_id, row = await lookup(request.json())
            if row['status'] != 'approved':
                raise ApiError(collaboration_error_message('invitation/not-approved'), code='invitation/not-approved', status=403)
            state = ctx.harness.session_access.snapshot(session_id)
            issued = await ctx.harness.session_credentials.issue(session_id, actor=state['owner'],
                subject=row['subject'], expires_in=3600)
            return issued
        issued = execute(exchange)
        return json_response({'subject': issued['subject'], 'session_ids': [issued['session_id']], 'read_only': 'contribute' not in ctx.harness.session_access.require(issued['session_id'], issued['subject'], 'read')['members'][issued['subject']]},
            headers={'Set-Cookie': shared._set_cookie(issued['token'])})

    return router
