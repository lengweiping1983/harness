"""Agent 人格模板、四维风格推荐与带版本检查的聊天覆盖。"""
from __future__ import annotations

from typing import Any
from harness.i18n import tr
from ...agents.personality import profiles, recommend
from ...agents.personality_library import PersonalityLibrary
from ...agents.personality_selection import PersonalitySelection
from ...core.errors import HarnessError, SessionNotFoundError
from ..errors import BadRequest, Conflict, NotFound, ServiceUnavailable
from ..responses import json_response
from ..router import Router


def build_router(ctx: Any) -> Router:
    router = Router()
    def library():
        return PersonalityLibrary(ctx.harness.config.home)

    @router.get("/api/v1/personalities")
    def catalog(request):
        result = {"profiles": [{**row, "id": row["code"], "source": "builtin"} for row in profiles()]}
        try:
            result["profiles"] += library().list()
        except HarnessError as exc:
            if not exc.code.startswith('personality/storage-'):
                raise
            result['warning'] = exc.message
        return json_response(result)

    @router.get("/api/v1/personality-selection")
    def selection(request):
        try:
            return json_response(PersonalitySelection(ctx.harness.config.home).read())
        except HarnessError as exc:
            raise ServiceUnavailable(exc.message, code=exc.code) from exc

    @router.patch("/api/v1/personality-selection")
    def select(request):
        body = request.json()
        if 'profile_id' not in body or not isinstance(body.get('expected_revision'), str):
            raise BadRequest(tr('python.personality.invalid'), code='personality/invalid')
        try:
            identity = body['profile_id']
            profile = None
            if identity is not None:
                catalog = [{**row, 'id': row['code'], 'source': 'builtin'} for row in profiles()]
                profile = next((row for row in catalog if row['id'] == identity), None)
                if profile is None:
                    profile = next((row for row in library().list() if row['id'] == identity), None)
                if profile is None:
                    raise HarnessError(tr('python.personality.changed'), code='personality/changed')
            return json_response(PersonalitySelection(ctx.harness.config.home).select(profile, body['expected_revision']))
        except HarnessError as exc:
            error = Conflict if exc.code == 'personality/changed' else ServiceUnavailable
            raise error(exc.message, code=exc.code) from exc

    @router.post("/api/v1/personalities")
    def save_profile(request):
        try:
            return json_response(library().save(request.json()))
        except HarnessError as exc:
            error = ServiceUnavailable if (exc.code == 'personality/busy' or exc.code.startswith('personality/storage-')) else Conflict if exc.code == 'personality/changed' else BadRequest
            raise error(exc.message, code=exc.code) from exc

    @router.delete("/api/v1/personalities/{profile_id}")
    def delete_profile(request, profile_id):
        try:
            library().delete(profile_id, request.json().get('revision'))
            return json_response({'deleted': True})
        except HarnessError as exc:
            error = ServiceUnavailable if (exc.code == 'personality/busy' or exc.code.startswith('personality/storage-')) else Conflict
            raise error(exc.message, code=exc.code) from exc

    @router.post("/api/v1/personalities/recommend")
    def suggestion(request):
        try:
            return json_response({"code": recommend(request.json().get("choices"))})
        except HarnessError as exc:
            raise BadRequest(exc.message, code=exc.code) from exc

    def read(session_id):
        try:
            ctx.harness.get_session(session_id)
            return ctx.harness.personalities.snapshot(session_id)
        except SessionNotFoundError as exc:
            raise NotFound(str(exc), code="personality/session-not-found") from exc

    @router.get("/api/v1/sessions/{session_id}/personality")
    def current(request, session_id):
        async def get():
            return read(session_id)
        return json_response(ctx.call(get()))

    @router.patch("/api/v1/sessions/{session_id}/personality")
    def update(request, session_id):
        body = request.json()
        if "code" not in body:
            raise BadRequest(tr("python.personality.invalid"), code="personality/invalid")
        async def apply():
            read(session_id)
            try:
                return await ctx.harness.personalities.set(session_id, body["code"], body.get("expected_version"), **({"prompt": body["prompt"]} if "prompt" in body else {}))
            except HarnessError as exc:
                error = Conflict if exc.code == "personality/changed" else BadRequest
                raise error(exc.message, code=exc.code) from exc
        return json_response(ctx.call(apply()))

    return router
