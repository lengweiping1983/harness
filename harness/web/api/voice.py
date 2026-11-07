"""本机录音转写与显式原生听写入口。"""
from __future__ import annotations

from typing import Any
import io
import wave

from harness.i18n import tr
from ... import voice
from ...tools.builtin.hex_tools import HexToolError, run_hex_owned
from ..errors import BadRequest, ServiceUnavailable
from ..responses import json_response
from ..router import Router
from .screenshot import _local

MAX_VOICE_BYTES = 2 * 1024 * 1024


def build_router(ctx: Any) -> Router:
    """浏览器转写与原生应用启动分别准入，均拒绝远程及共享身份。"""
    router = Router()

    def require_local(request):
        """原生听写和转写共用本机及非共享身份边界。"""
        if request.subject is not None or not _local(request):
            raise BadRequest(tr('ui.voice.localOnly'), status=403, code='voice/local-only',
                             details={'copy': {'key': 'ui.voice.localOnly'}})

    @router.get('/api/v1/voice/dictation')
    def dictation(request):
        require_local(request)
        return json_response(voice.dictation_capabilities()).with_header('Cache-Control', 'no-store')

    @router.post('/api/v1/voice/dictation/open')
    def open_dictation(request):
        require_local(request)
        capabilities = voice.dictation_capabilities()
        if not capabilities['available']:
            copy = capabilities['reason']
            raise BadRequest(tr(copy['key'], **copy.get('params', {})), code='voice/unavailable',
                             details={'copy': copy})
        async def run():
            return await run_hex_owned(voice.open_dictation)

        try:
            ctx.call(run(), timeout=15)
        except HexToolError as error:
            # 超时异常可能带有启动参数，不将 HEX_* 环境值写入公开诊断。
            reason = str(error.__cause__) if isinstance(error.__cause__, OSError) else str(error)
            raise ServiceUnavailable(str(error), code='voice/open-failed',
                                     details={'copy': error.copy, 'reason': reason}) from error
        return json_response({'requested': True}).with_header('Cache-Control', 'no-store')

    @router.post('/api/v1/voice/transcriptions')
    def transcribe(request):
        require_local(request)
        try:
            if (request.header('Content-Type').split(';')[0].strip() != 'audio/wav'
                    or not 44 < len(request.body) <= MAX_VOICE_BYTES):
                raise ValueError('audio/wav')
            with wave.open(io.BytesIO(request.body), 'rb') as audio:
                if (audio.getnchannels() != 1 or audio.getsampwidth() != 2
                        or audio.getframerate() != 16000 or not 0 < audio.getnframes() <= 960000
                        or len(audio.readframes(audio.getnframes())) != audio.getnframes() * 2):
                    raise ValueError('PCM16/16000/mono')
        except (ValueError, EOFError, wave.Error) as error:
            raise BadRequest(tr('ui.voice.invalid'), code='voice/invalid',
                             details={'copy': {'key': 'ui.voice.invalid'}}) from error

        timings: dict[str, float] = {}

        async def run():
            return await run_hex_owned(ctx.harness.hex_service.transcribe_prepared, request.body, timings=timings)

        try:
            result = ctx.call(run(), timeout=780)
        except HexToolError as error:
            raise ServiceUnavailable(str(error), code='voice/unavailable',
                                     details={'copy': error.copy or {'key': 'ui.voice.engineFailed'}, 'reason': str(error)}) from error
        response = json_response({'transcript': result['transcript']}).with_header('Cache-Control', 'no-store')
        if timings:
            response.with_header('Server-Timing', ', '.join(f'voice_{name};dur={value:.2f}' for name, value in timings.items()))
        return response

    @router.post('/api/v1/voice/preparations')
    def prepare(request):
        require_local(request)
        if request.json():
            raise BadRequest(tr('ui.voice.invalidPreparation'), code='voice/invalid',
                             details={'copy': {'key': 'ui.voice.invalidPreparation'}})

        async def run():
            await run_hex_owned(ctx.harness.hex_service.prepare_transcription)

        try:
            ctx.call(run(), timeout=630)
        except HexToolError as error:
            raise ServiceUnavailable(str(error), code='voice/unavailable',
                                     details={'copy': error.copy or {'key': 'ui.voice.engineFailed'},
                                              'reason': str(error)}) from error
        return json_response({'ready': True}).with_header('Cache-Control', 'no-store')

    return router
