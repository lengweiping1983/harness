"""框架文本边界：显式编码、严格增量解码及独立入口标准流。"""
from __future__ import annotations

import codecs
import os
import sys
from collections.abc import Mapping

from harness.i18n import tr


def output_encoding(value: object = 'utf-8') -> str:
    """只接受可用于文本增量解码的已登记编码，启动前完成校验。"""
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError(tr('encoding.invalid', encoding=value))
    try:
        codec = codecs.lookup(value)
        if not codec._is_text_encoding:
            raise LookupError(value)
        codecs.getincrementaldecoder(codec.name)('strict')
        return codec.name
    except (LookupError, TypeError) as exc:
        raise ValueError(tr('encoding.invalid', encoding=value)) from exc


def utf8_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """仅构造所属子进程环境，不修改当前进程或宿主全局状态。"""
    return {**(os.environ if source is None else source), 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}


def configure_cli_streams() -> None:
    """独立命令行入口显式配置标准流，SDK 导入不调用此函数。"""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is not None:
            reconfigure(encoding='utf-8', errors='strict')


class TextDecoder:
    """保留首个非法字节之前的原文；记录错误后继续排空所属管道。"""

    def __init__(self, encoding: str = 'utf-8') -> None:
        self.encoding = output_encoding(encoding)
        self.decoder = codecs.getincrementaldecoder(self.encoding)('strict')
        self.error: dict | None = None
        self.received = 0

    def decode(self, data: bytes, final: bool = False) -> str:
        if self.error is not None:
            self.received += len(data)
            return ''
        state = self.decoder.getstate()
        offset = self.received - len(state[0])
        self.received += len(data)
        try:
            return self.decoder.decode(data, final)
        except UnicodeDecodeError as exc:
            self.error = {'encoding': self.encoding, 'offset': offset + exc.start, 'reason': exc.reason}
            self.decoder.setstate(state)
            # start 包含上次缓存的半字符，不能重复向解码器喂入。
            size = max(0, exc.start - len(state[0]))
            return self.decoder.decode(data[:size], final=False) if size else ''


def decode_output(stdout: bytes, stderr: bytes, encoding: str = 'utf-8') -> dict:
    """最终结果与实时预览共用解码规则，执行状态和解码完整性分别记录。"""
    result = {'output_encoding': output_encoding(encoding)}
    errors = []
    for channel, raw in (('stdout', stdout), ('stderr', stderr)):
        decoder = TextDecoder(result['output_encoding'])
        result[channel] = decoder.decode(raw, final=True)
        if decoder.error:
            errors.append({'channel': channel, **decoder.error})
    if errors:
        result['encoding_errors'] = errors
    return result


def utf8_prefix(data: bytes, *, truncated: bool = False) -> str:
    """只容忍已知截断处的半字符，正文内部非法字节仍明确拒绝。"""
    return codecs.getincrementaldecoder('utf-8')('strict').decode(data, final=not truncated)


def diagnostic_text(data: bytes, *, truncated: bool = False) -> str:
    """诊断也不替换原字节，错误后给出明确的不完整提示。"""
    decoder = TextDecoder()
    text = decoder.decode(data, final=not truncated)
    if decoder.error:
        text += '\n' + tr('encoding.failure', channel='diagnostic',
                          encoding=decoder.encoding, offset=decoder.error['offset'])
    return text
