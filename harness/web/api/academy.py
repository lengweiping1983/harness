"""Academy 的本机宿主接口，不向受邀对话开放文件或执行权限。"""
from __future__ import annotations

import ipaddress
import threading
import sqlite3
from pathlib import Path

from harness.academy.errors import AcademyError
from harness.academy.files import version
from harness.academy.protocol import ENDPOINTS, SCHEMAS
from harness.academy.service import AcademyService
from harness.i18n import tr
from harness.llm.types import LlmCallConfig
from harness.tools.schema import validate

from ..errors import ApiError
from ..responses import json_response
from ..router import Router


def build_router(ctx):
    router = Router()
    lock = threading.Lock()
    instance = None

    def service():
        nonlocal instance
        with lock:
            if instance is None:
                instance = AcademyService(Path(ctx.harness.config.home))
        return instance

    def call(request, template, operation, *, query=()):
        try:
            local = ipaddress.ip_address(request.client_ip or '').is_loopback
        except ValueError:
            local = False
        if not local or request.subject is not None:
            raise ApiError(tr('ui.academy.error.local-only'), code='academy/local-only', status=403)
        if set(request.query) != set(query) or any(len(values) != 1 for values in request.query.values()):
            raise ApiError(tr('ui.academy.error.invalid-request'), code='academy/invalid-request')
        input_name = ENDPOINTS[(request.method, template)][0]
        body = request.json() if input_name else {}
        if input_name and validate(body, SCHEMAS[input_name]):
            raise ApiError(tr('ui.academy.error.invalid-request'), code='academy/invalid-request')
        try:
            value = operation(service(), body)
        except sqlite3.DatabaseError as exc:
            raise ApiError(tr('ui.academy.error.store-invalid'), code='academy/store-invalid', status=503) from exc
        except AcademyError as exc:
            status = 404 if exc.code.endswith('not-found') else 409 if exc.code in {'stale-project', 'stale-file', 'restore-conflict', 'request-conflict', 'outcome-unknown'} else 503 if exc.code in {'sandbox-unavailable', 'cleanup-failed', 'store-invalid'} else 400
            raise ApiError(tr('ui.academy.error.' + exc.code), code='academy/' + exc.code, status=status) from exc
        return json_response(value, headers={'Cache-Control': 'no-store'})

    def endpoint(method, path, handler, *, query=()):
        def invoke(request, **params):
            return call(request, path, lambda service, body: handler(service, body, params, request), query=query)
        router.add(method, path, invoke)

    prefix = '/api/v1/academy'
    endpoint('GET', prefix + '/courses', lambda service, body, params, request: {'courses': service.courses()})
    def create_design(service, body, params, request):
        adapter = getattr(ctx.harness, 'adapter', None)
        config = getattr(getattr(ctx.harness, 'agent', None), 'config', LlmCallConfig())
        schedule = (lambda identity: ctx.spawn(service.designer.generate(identity, adapter, config))) if adapter is not None else None
        return service.designer.create(**body, model=config.model, schedule=schedule)
    endpoint('POST', prefix + '/designs', create_design)
    endpoint('GET', prefix + '/designs', lambda service, body, params, request: {'designs': service.designer.designs()})
    endpoint('GET', prefix + '/designs/{design_id}', lambda service, body, params, request: {'design': service.designer.design(params['design_id'])})
    endpoint('GET', prefix + '/courses/{course_id}', lambda service, body, params, request: {'course': service.course(params['course_id'])})
    def reference(service, body, params, request):
        content = service.catalog.reference(params['course_id'], params['lesson_id'], request.get('path'))
        return {'path': request.get('path'), 'content': content, 'version': version(content)}
    endpoint('GET', prefix + '/courses/{course_id}/lessons/{lesson_id}/reference', reference, query=('path',))
    endpoint('GET', prefix + '/projects', lambda service, body, params, request: {'projects': service.projects()})
    endpoint('POST', prefix + '/projects', lambda service, body, params, request: service.create_project(**body))
    endpoint('GET', prefix + '/projects/{project_id}', lambda service, body, params, request: {'project': service.project(**params)})
    endpoint('POST', prefix + '/projects/{project_id}/lessons/{lesson_id}/start', lambda service, body, params, request: service.start_lesson(**params, **body))
    endpoint('POST', prefix + '/projects/{project_id}/complete', lambda service, body, params, request: service.complete_study(**params, **body))
    def model_run(service, body, params, request):
        adapter = getattr(ctx.harness, 'adapter', None)
        config = getattr(getattr(ctx.harness, 'agent', None), 'config', LlmCallConfig())
        schedule = (lambda identity: ctx.spawn(service.experiments.evaluate(identity, adapter, config))) if adapter is not None else None
        return service.experiments.start(**params, **body, model=config.model, schedule=schedule)
    endpoint('POST', prefix + '/projects/{project_id}/model-runs', model_run)
    endpoint('POST', prefix + '/projects/{project_id}/model-runs/{run_id}/stop', lambda service, body, params, request: service.experiments.stop(**params, **body))
    endpoint('GET', prefix + '/projects/{project_id}/model-runs', lambda service, body, params, request: {'model_runs': service.experiments.runs(**params)})
    def tutor_turn(service, body, params, request):
        adapter = getattr(ctx.harness, 'adapter', None)
        config = getattr(getattr(ctx.harness, 'agent', None), 'config', LlmCallConfig())
        schedule = (lambda identity: ctx.spawn(service.tutor.evaluate(identity, adapter, config))) if adapter is not None else None
        return service.tutor.start(**params, **body, model=config.model, schedule=schedule)
    endpoint('POST', prefix + '/projects/{project_id}/tutor-turns', tutor_turn)
    endpoint('GET', prefix + '/projects/{project_id}/tutor-turns', lambda service, body, params, request: {'tutor_turns': service.tutor.turns(**params)})
    endpoint('POST', prefix + '/projects/{project_id}/tutor-turns/{turn_id}/stop', lambda service, body, params, request: service.tutor.stop(**params, **body))
    endpoint('GET', prefix + '/projects/{project_id}/files', lambda service, body, params, request: {'files': service.files(**params)})
    endpoint('GET', prefix + '/projects/{project_id}/source', lambda service, body, params, request: service.source(**params, raw_path=request.get('path')), query=('path',))
    endpoint('POST', prefix + '/projects/{project_id}/files', lambda service, body, params, request: service.write_file(**params, **body))
    for action in ('run', 'check'):
        endpoint('POST', prefix + '/projects/{project_id}/' + action,
                 lambda service, body, params, request, check=action == 'check': service.execute(**params, **body, check=check))
    endpoint('GET', prefix + '/projects/{project_id}/runs', lambda service, body, params, request: {'runs': service.runs(**params)})
    endpoint('GET', prefix + '/projects/{project_id}/trace/{run_id}', lambda service, body, params, request: {'events': service.trace(**params)})
    endpoint('GET', prefix + '/projects/{project_id}/checkpoints', lambda service, body, params, request: {'checkpoints': service.checkpoints(**params)})
    endpoint('POST', prefix + '/projects/{project_id}/checkpoints', lambda service, body, params, request: service.checkpoint(**params, **body))
    endpoint('GET', prefix + '/projects/{project_id}/restore', lambda service, body, params, request: service.restore_preview(**params, checkpoint_id=request.get('checkpoint_id')), query=('checkpoint_id',))
    endpoint('POST', prefix + '/projects/{project_id}/restore', lambda service, body, params, request: service.restore(**params, **body))
    def submit(service, body, params, request):
        adapter = getattr(ctx.harness, 'adapter', None)
        config = getattr(getattr(ctx.harness, 'agent', None), 'config', LlmCallConfig())
        schedule = (lambda identity: ctx.spawn(service.evaluate_submission(identity, adapter, config))) if adapter is not None else None
        return service.submit(**params, **body, model=config.model, schedule=schedule)
    endpoint('POST', prefix + '/projects/{project_id}/submissions', submit)
    endpoint('GET', prefix + '/projects/{project_id}/submissions', lambda service, body, params, request: {'submissions': service.submissions(**params)})
    endpoint('GET', prefix + '/projects/{project_id}/submissions/{submission_id}', lambda service, body, params, request: {'submission': service.submission(**params)})
    endpoint('GET', prefix + '/receipts/{request_id}', lambda service, body, params, request: service.receipt(**params))
    return router
