"""在事件循环之外执行 Python 正则搜索，支持按归属取消。"""
from __future__ import annotations

from harness.core.text_encoding import diagnostic_text

from harness.i18n import tr

import asyncio
from dataclasses import replace
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import time

from ...core.errors import ToolExecutionError
from ..owned_process import spawn_owned
from ..process_env import execution_env
from ..process_output import cleanup_process, collect_process

SEARCH_SECONDS = 30.0
logger = logging.getLogger(__name__)


def decode_response(stdout: bytes):
    """校验内部协议，避免损坏响应被误报为工具参数或导入故障。"""
    try:
        response = json.loads(stdout)
        if not isinstance(response, dict) or ('result' in response) == ('error' in response):
            raise ValueError(tr('runtime.validation.49fb928c3164'))
        if 'error' in response:
            error = response['error']
            if (not isinstance(error, dict) or not isinstance(error.get('code'), str)
                    or not isinstance(error.get('message_key'), str)
                    or not isinstance(error.get('message_values'), dict)
                    or not isinstance(error.get('details'), dict)):
                raise ValueError(tr('runtime.validation.ce748245b3ee'))
            message = tr(error['message_key'], **error['message_values'])
        else:
            result = response['result']
            if (not isinstance(result, dict) or type(result.get('count')) is not int
                    or not isinstance(result.get('matches'), list) or not isinstance(result.get('pattern'), str)):
                raise ValueError(tr('runtime.validation.05246e0711e8'))
            return result
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ToolExecutionError(tr('fs.search.invalid_response'), code='FS_SEARCH_FAILED',
                                 phase='worker-protocol', detail=str(exc)) from exc
    raise ToolExecutionError(message, code=error['code'], **error['details'])


async def search(root, args, ignores, ctx):
    deadline = time.monotonic() + SEARCH_SECONDS
    worker = Path(__file__).with_name('fs_grep_worker.py')
    fd, name = tempfile.mkstemp(prefix='harness-grep-', suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as request:
            json.dump({'root': str(root), 'args': args, 'ignores': sorted(ignores)}, request, ensure_ascii=False)
        argv = [sys.executable, '-X', 'utf8', '-I', str(worker), name]
        # 使用独立解释器，不执行 Shell 插值或工作区代码。
        # 工作进程使用同一套剪枝遍历器和 Python 正则表达式。
        if os.name == 'nt' or sys.platform.startswith('linux') and hasattr(os, 'WNOWAIT'):
            proc = await spawn_owned(argv, cwd=str(worker.parent), env=execution_env())
        else:
            proc = await asyncio.create_subprocess_exec(*argv, cwd=str(worker.parent),
                env=execution_env(), stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, start_new_session=True)
        # 内部 JSON 是协议，不是用户可见的命令输出流。
        # 保留 ctx 用于记录清理失败的归属，不发布原始 JSON。
        silent_ctx = replace(ctx, services={**ctx.services, 'tool_output': None})
        stdout, stderr, code, timed_out = await collect_process(proc, max(0, deadline - time.monotonic()), silent_ctx,
                                                               kind='grep', label=tr('python.fs_grep.8f1a29426353'))
        if timed_out:
            await cleanup_process(proc, ctx, kind='grep', label=tr('python.fs_grep.8f1a29426353'), stdout=stdout, stderr=stderr)
            raise ToolExecutionError(tr('python.fs_grep.33da8deb5aa0'),
                                     code='FS_SEARCH_TIMEOUT', seconds=SEARCH_SECONDS)
        if code != 0:
            raise ToolExecutionError(tr('python.fs_grep.11a4ba9f45c2'), code='FS_SEARCH_FAILED',
                                     exit_code=code, detail=diagnostic_text(stderr)[-2000:])
        return decode_response(stdout)
    finally:
        try:
            Path(name).unlink(missing_ok=True)
        except OSError as exc:
            # 已完成搜索或所属进程清理失败的结果仍具有权威性，
            # 临时文件清理不能诱导再次查询。
            logger.warning(tr('log.5ce6021e0e6b'), exc)
