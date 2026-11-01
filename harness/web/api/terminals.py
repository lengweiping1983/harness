"""会话级用户终端控制，模型工具共享同一组句柄。"""
from __future__ import annotations

from harness.i18n import tr

import os
import sys

from ..errors import BadRequest, Conflict, NotFound
from ..responses import json_response
from ..router import Router
from ...core.errors import SessionNotFoundError
from ...core.text_encoding import output_encoding
from ...tools.builtin.terminal_tools import TerminalError


def build_router(ctx):
    router = Router()
    opening: dict[str, int] = {}

    def session(owner):
        try:
            return ctx.harness.get_session(owner)
        except SessionNotFoundError as exc:
            raise NotFound(tr('python.terminals.19da31bd3666'), code='terminal/session-not-found') from exc

    def owned(owner, terminal_id):
        session(owner)
        try:
            return ctx.harness.terminals.expect_owned(owner, terminal_id)
        except LookupError as exc:
            raise NotFound(tr('python.terminals.ffe33c81e311'), code='terminal/not-found') from exc

    def body(request, allowed):
        value = request.json()
        if not isinstance(value, dict) or set(value) - set(allowed):
            raise BadRequest(tr('python.terminals.86e89a153446'), code='terminal/invalid')
        return value

    def integer(value, low, high):
        if type(value) is not int or not low <= value <= high:
            raise BadRequest(tr('python.terminals.e81adb3d5356', p0=f'{low}', p1=f'{high}'), code='terminal/invalid')
        return value

    async def audit(owner, action, **data):
        try:
            await session(owner).append('note', {'kind': 'terminal/control', 'source': 'user', 'action': action, **data}, ignorable=True)
        except Exception:
            return tr('ui.audit.terminalActionIncomplete')
        return None

    def setup_call(operation):
        from ...core.errors import HarnessError
        try:
            return ctx.call(operation, timeout=20)
        except HarnessError as exc:
            error = NotFound if exc.code == 'terminal/install-missing' else Conflict if exc.code in {'terminal/install-busy', 'terminal/install-changed'} else BadRequest
            raise error(exc.message, code=exc.code) from exc

    @router.get('/api/v1/terminal/setup')
    def setup(request):
        return json_response(setup_call(ctx.harness.terminal_setup.inspect())).with_header('Cache-Control', 'no-store')

    @router.get('/api/v1/terminal/setup/install')
    def installation(request):
        async def read():
            return {'installation': ctx.harness.terminal_setup.snapshot(request.get('id'))}
        return json_response(setup_call(read())).with_header('Cache-Control', 'no-store')

    @router.post('/api/v1/terminal/setup/install')
    def install(request):
        if not ctx.harness.config.enable_shell:
            raise Conflict(tr('python.terminals.b595e61fc612'), code='terminal/disabled')
        return json_response({'installation': setup_call(ctx.harness.terminal_setup.start(request.json()))})

    @router.delete('/api/v1/terminal/setup/install')
    def cancel_install(request):
        job_id = None
        try:
            args = body(request, ['id'])
            if set(args) == {'id'} and isinstance(args['id'], str):
                job_id = args['id']
        except BadRequest:
            pass
        if job_id is None:
            job_id = request.get('id')
        if not isinstance(job_id, str) or not job_id:
            raise BadRequest(tr('python.terminal_setup.invalid'), code='terminal/install-invalid')
        return json_response({'installation': setup_call(ctx.harness.terminal_setup.cancel(job_id))})

    def terminal_types():
        service = ctx.harness.terminals
        return {'backends': sorted(service.backends),
                'enabled': ctx.harness.config.enable_shell, 'managed_runtime': sys.platform == 'win32',
                'default_type': 'cmd' if sys.platform == 'win32' else 'zsh' if 'zsh' in service.backends else 'bash'}

    @router.get('/api/v1/terminal/types')
    def types(request):
        async def read():
            return terminal_types()
        return json_response(ctx.call(read())).with_header('Cache-Control', 'no-store')

    @router.get('/api/v1/sessions/{session_id}/terminals')
    def list_terminals(request, session_id):
        async def read():
            session(session_id)
            service = ctx.harness.terminals
            return {'terminals': service.list(session_id), **terminal_types()}
        return json_response(ctx.call(read()))

    @router.post('/api/v1/sessions/{session_id}/terminals')
    def open_terminal(request, session_id):
        value = body(request, ['type', 'output_encoding'])
        try:
            encoding = output_encoding(value.get('output_encoding', 'utf-8'))
        except ValueError as exc:
            raise BadRequest(str(exc), code='terminal/invalid') from exc
        kind = value.get('type')
        if kind is not None and (not isinstance(kind, str) or len(kind) > 32):
            raise BadRequest(tr('python.terminals.0118bbdfa5c6'), code='terminal/invalid')
        async def create():
            chat = session(session_id)
            if not ctx.harness.config.enable_shell:
                raise Conflict(tr('python.terminals.b595e61fc612'), code='terminal/disabled')
            service = ctx.harness.terminals
            selected = kind or ('cmd' if sys.platform == 'win32' else 'zsh' if 'zsh' in service.backends else 'bash')
            # 界面只选择已配置的 Shell，不接受任意可执行文件
            # 或用户传入的工作目录；代理工具保留既有契约。
            if selected not in service.backends:
                raise BadRequest(tr('python.terminals.cbac6e9e9548'), code='terminal/invalid')
            if opening.get(session_id, 0) + sum(t['status']['kind'] == 'running' for t in service.list(session_id)) >= 16:
                raise Conflict(tr('python.terminals.c536f7120a7b'), code='terminal/limit')
            opening[session_id] = opening.get(session_id, 0) + 1
            try:
                terminal = await service.spawn(owner=session_id, kind=selected, cwd=chat.header.cwd or ctx.workspace_root(), output_encoding=encoding)
            except TerminalError as exc:
                raise BadRequest(str(exc), code='terminal/open-failed') from exc
            finally:
                opening[session_id] -= 1
                if not opening[session_id]:
                    del opening[session_id]
            error = await audit(session_id, 'open', terminal_id=terminal['sessionId'], type=selected, cwd=terminal['cwd'])
            return {'terminal': terminal, **({'audit_error': error} if error else {})}
        return json_response(ctx.call(create()))

    @router.get('/api/v1/sessions/{session_id}/terminals/{terminal_id}/stream')
    def stream(request, session_id, terminal_id):
        try:
            cursor = int(request.get('cursor') or '0')
            wait_ms = int(request.get('wait_ms') or '1000')
        except ValueError as exc:
            raise BadRequest(tr('python.terminals.fee85d342596'), code='terminal/invalid') from exc
        integer(cursor, 0, 2**53 - 1)
        integer(wait_ms, 0, 1000)
        async def read():
            return await owned(session_id, terminal_id).read_stream(cursor, wait_ms)
        return json_response(ctx.call(read()), accept_encoding=request.header('Accept-Encoding'))

    @router.post('/api/v1/sessions/{session_id}/terminals/{terminal_id}/input')
    def send_input(request, session_id, terminal_id):
        value = body(request, ['text', 'submit'])
        text, submit = value.get('text'), value.get('submit', False)
        if not isinstance(text, str) or not 1 <= len(text) <= 16384 or type(submit) is not bool:
            raise BadRequest(tr('python.terminals.9cfba780c8d0'), code='terminal/invalid')
        try:
            text.encode('utf-8')
        except UnicodeEncodeError as exc:
            raise BadRequest(tr('python.terminals.0e1784b74d00'), code='terminal/invalid') from exc
        async def write():
            terminal = owned(session_id, terminal_id)
            try:
                payload = terminal._payload_for_stdin(text, submit)
                if submit:
                    payload += ('\r' if hasattr(terminal.proc, 'resize') else '\r\n') if os.name == 'nt' else '\n'
                await terminal.write_input(payload)
            except TerminalError as exc:
                raise Conflict(str(exc), code='terminal/input-unconfirmed') from exc
            # 不记录按键或密码，只记录已接受的输入长度。
            error = await audit(session_id, 'input', terminal_id=terminal_id, chars=len(text), submit=submit)
            return {'terminal': terminal.snapshot(), **({'audit_error': error} if error else {})}
        return json_response(ctx.call(write()))

    @router.post('/api/v1/sessions/{session_id}/terminals/{terminal_id}/resize')
    def resize(request, session_id, terminal_id):
        value = body(request, ['cols', 'rows'])
        cols, rows = integer(value.get('cols'), 2, 500), integer(value.get('rows'), 1, 300)
        async def update():
            terminal = owned(session_id, terminal_id)
            try:
                terminal.resize(cols, rows)
            except TerminalError as exc:
                raise Conflict(str(exc), code='terminal/resize-failed') from exc
            return {'terminal': terminal.snapshot()}
        return json_response(ctx.call(update()))

    return router
