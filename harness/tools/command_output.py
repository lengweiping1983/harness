"""限制命令结果大小，同时保留最终诊断和完整日志文件。"""
from __future__ import annotations

from harness.i18n import tr

import os
import hashlib
import tempfile
from typing import Any

STDOUT_CHARS = 8192
STDERR_CHARS = 4096


def _preview(text: str, limit: int) -> str:
    if type(limit) is not int or limit < 0:
        raise ValueError(tr('runtime.validation.e36af12268a9'))
    if len(text) <= limit:
        return text
    marker = tr('python.command_output.56615b9bda98', p0=f'{len(text)}')
    if len(marker) >= limit:
        return text[-limit:] if limit else ''
    available = max(0, limit - len(marker))
    head = available // 4
    tail = available - head
    return text[:head] + marker + (text[-tail:] if tail else '')


def summarize_command_output(stdout: str, stderr: str, *, prefix: str = 'harness-',
                             stdout_limit: int = STDOUT_CHARS, stderr_limit: int = STDERR_CHARS) -> dict[str, Any]:
    """短输出完整保留，长输出保留四分之一头部与四分之三尾部。

    文件保存完整解码输出流，包括原始换行符。日志溢写失败不能将
    已完成命令变成诱导重跑的工具错误；应随实际结果一起报告日志丢失。
    """
    result: dict[str, Any] = {'spilled_to': None, 'stderr_spilled_to': None, 'truncated': False}
    errors = []
    fingerprint = hashlib.sha256()
    for name, text, limit, path_key in [('stdout', stdout, stdout_limit, 'spilled_to'),
                                         ('stderr', stderr, stderr_limit, 'stderr_spilled_to')]:
        # 对完整解码输出流式计算摘要，不只针对预览；
        # 即使被截断的中间诊断发生变化，也是真实变化。
        fingerprint.update(f'{name}:{len(text)}:'.encode('ascii'))
        for start in range(0, len(text), 65536):
            fingerprint.update(text[start:start + 65536].encode('utf-8'))
        result[name] = _preview(text, limit)
        if len(text) <= limit:
            continue
        result['truncated'] = True
        path = None
        try:
            fd, path = tempfile.mkstemp(prefix=prefix + name + '-', suffix='.log')
            with os.fdopen(fd, 'w', encoding='utf-8', newline='') as handle:
                handle.write(text)
            result[path_key] = path
        except OSError as exc:
            if path is not None:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            errors.append(tr('python.command_output.189440e4e94b', p0=f'{name}', p1=f'{str(exc)[:240]}'))
    if errors:
        result['output_save_errors'] = errors
    result['_output_fingerprint'] = fingerprint.hexdigest()
    return result


def exited_command_result(decoded: dict[str, Any], returncode: int, *, prefix: str = 'harness-',
                          stdout_limit: int = STDOUT_CHARS, stderr_limit: int = STDERR_CHARS) -> dict[str, Any]:
    """正常退出共用结果结构，保留解码失败与进程退出码的独立事实。"""
    return {
        **summarize_command_output(decoded['stdout'], decoded['stderr'], prefix=prefix,
                                   stdout_limit=stdout_limit, stderr_limit=stderr_limit),
        'exit_code': returncode,
        'execution_state': 'exited',
        **{key: value for key, value in decoded.items() if key not in ('stdout', 'stderr')},
        'error': returncode != 0 or bool(decoded.get('encoding_errors')),
    }


def command_progress_key(args: Any, value: Any) -> Any:
    """使用命令证据检测重复结果，不使用新日志文件名。

    启动或后台句柄不生成此键，其生命周期仍通过任务、终端工具
    和运行时确认的进展进行观察。
    """
    fingerprint = value.get('_output_fingerprint')
    if not fingerprint:
        return None
    # 这些内置结构中，只有 description 纯粹用于展示。
    # 保留命令、代码、工作目录、语言及超时参数，
    # 避免不同执行仅因输出相同而被视为相同。
    execution_args = {key: item for key, item in args.items() if key != 'description'}
    return [execution_args, value.get('exit_code'), bool(value.get('timed_out')), bool(value.get('error')), fingerprint]


def command_display(args: Any, value: Any) -> dict[str, Any]:
    """界面回执使用执行字段，不解析面向模型的说明文字。"""
    display = {key: value[key] for key in ('execution_state', 'exit_code', 'timed_out', 'background', 'promoted', 'job_id', 'language',
                                         'truncated', 'spilled_to', 'stderr_spilled_to', 'output_encoding', 'encoding_errors') if key in value and value[key] is not None}
    if value.get('timed_out') or value.get('execution_state') == 'not-started':
        display.pop('exit_code', None)
    return display


def render_command_output(value: dict[str, Any], *, label: str | None = None) -> str:
    """退出状态和产物引用在详细日志之前展示。"""
    if label is None:
        label = tr('python.command_output.928f87d4507b')
    if value.get('execution_state') == 'not-started':
        heading = tr('python.command_output.3c6eae49b374', p0=f'{label}')
    elif value.get('timed_out'):
        heading = tr('python.command_output.47a7b6e92f2a', p0=f'{label}')
    else:
        heading = tr('python.command_output.469cbc7e68d2', p0=f'{label}', p1=f"{(tr('common.failed') if value.get('exit_code') != 0 else tr('common.completed'))}", p2=f"{value['exit_code']}")
    parts = [heading]
    for error in value.get('encoding_errors', []):
        parts.append(tr('encoding.failure', **{key: error[key] for key in ('channel', 'encoding', 'offset')}))
    if value.get('timeout_note'):
        parts.append(value['timeout_note'])
    paths = [('stdout', value.get('spilled_to')), ('stderr', value.get('stderr_spilled_to'))]
    for name, path in paths:
        if path:
            parts.append(tr('python.command_output.5970e668c34b', p0=f'{name}', p1=f'{path}'))
    if any(path for _, path in paths):
        parts.append(tr('python.command_output.2dc5e8b4841d'))
    parts.extend(value.get('output_save_errors') or [])
    if value.get('stdout'):
        parts.append(value['stdout'])
    if value.get('stderr'):
        parts.append('[stderr]\n' + value['stderr'])
    if not value.get('stdout') and not value.get('stderr') and not value.get('truncated'):
        parts.append(tr('python.command_output.8ae64c2c8ce5'))
    encoding = value.get('output_encoding')
    if encoding and encoding.lower().replace('-', '') != 'utf8':
        parts.append(tr('python.command_output.5896b13ddbbe', p0=f'{encoding}'))
    return '\n'.join(parts)
