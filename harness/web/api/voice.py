"""本机浏览器录音转写；不接受远程触发宿主麦克风。"""
from __future__ import annotations

from typing import Any
import io
import wave

from harness.i18n import tr
from ...tools.builtin.hex_tools import HexToolError, run_hex_owned
from ..errors import BadRequest, ServiceUnavailable
from ..responses import json_response
from ..router import Router
from .screenshot import _local

MAX_VOICE_BYTES = 2 * 1024 * 1024


def build_router(ctx: Any) -> Router:
    """语音录制发生在调用浏览器，本接口只接收有限的 WAV。"""
    router = Router()

    @router.post('/api/v1/voice/transcriptions')
    def transcribe(request):
        if request.subject is not None or not _local(request):
            raise BadRequest(tr('ui.voice.localOnly'), status=403, code='voice/local-only',
                             details={'copy': {'key': 'ui.voice.localOnly'}})
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

        async def run():
            return await run_hex_owned(ctx.harness.hex_service.transcribe_prepared, request.body)

        try:
            result = ctx.call(run(), timeout=780)
        except HexToolError as error:
            raise ServiceUnavailable(str(error), code='voice/unavailable',
                                     details={'copy': error.copy or {'key': 'ui.voice.engineFailed'}, 'reason': str(error)}) from error
        return json_response({'transcript': result['transcript']}).with_header('Cache-Control', 'no-store')

    return router
