"""使用显式配置的本机 OpenCode 运行时发送匿名模型请求。"""
from __future__ import annotations

from harness.core.text_encoding import diagnostic_text

import argparse
import asyncio
from dataclasses import replace
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path
import tempfile

from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..core.errors import LlmError
from ..core.language import current_language
from ..i18n import tr
from .retry import MAX_RETRY_DELAY, parse_retry_after
from .transport import Transport
from ..tools.process_env import stdio_env
from ..tools.processes import stop_process

BASE_URL = 'https://opencode.ai/inference/openai/v1'
MAX_PROFILE_BYTES = 1024 * 1024
MAX_RUNTIME_DIAGNOSTIC_BYTES = 4096
# CLI退出后保留监督进程，宿主回收进程树前不丢失后代归属。
_BOOTSTRAP_SUPERVISOR = r"""
import json,subprocess,sys,time
process=subprocess.Popen(json.loads(sys.argv[1]),stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
print(process.wait(),flush=True)
while True:time.sleep(3600)
"""


async def _stop_stream_process(process):
    """流式助手不派生工具；停止并等待当前请求独占的进程。"""
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    await process.wait()


async def _runtime_diagnostic(stream):
    """持续排空错误管道，仅保留有界前缀，避免阻塞或无界保存诊断。"""
    prefix = bytearray()
    while chunk := await stream.read(4096):
        prefix.extend(chunk[:max(0, MAX_RUNTIME_DIAGNOSTIC_BYTES + 1 - len(prefix))])
    return bytes(prefix)


async def _finish_stream_process(process, diagnostic):
    """逐项完成进程与错误管道收尾，保留全部原始失败。"""
    errors = []
    for operation in (_stop_stream_process(process), diagnostic):
        try:
            await operation
        except BaseException as exc:
            errors.append(exc)
    raise_cleanup_errors(errors)


def _runtime_failure(raw, *, phase, returncode, received, timeout, cause):
    """仅信任当前辅助协议的固定枚举，未知退出不得猜测为可重试。"""
    details = {'phase': phase, 'runtime_exit_code': returncode, 'response_bytes': received}
    key, params, transient = 'ui.llm.opencodeLocalRuntimeFailed', {}, False
    failure = None
    try:
        if len(raw) > MAX_RUNTIME_DIAGNOSTIC_BYTES:
            raise ValueError
        record = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_fields)
        if (not isinstance(record, dict) or set(record) != {'failure', 'phase', 'network_error'}
                or record['phase'] not in ('connect', 'read')
                or record['failure'] not in ('idle_timeout', 'network', 'runtime')
                or record['network_error'] not in (None, 'ConnectionResetError', 'ConnectionRefusedError',
                    'BrokenPipeError', 'TimeoutError', 'gaierror')):
            raise ValueError
        failure = record['failure']
        details.update(phase=record['phase'])
        if record['network_error'] is not None:
            details['network_error'] = record['network_error']
    except (ValueError, TypeError, RecursionError):
        pass
    if isinstance(cause, TimeoutError) or failure == 'idle_timeout':
        key, params, transient = 'ui.llm.opencodeLocalIdleTimeout', {'seconds': timeout}, True
        details['seconds'] = timeout
    elif failure == 'network' and details.get('network_error'):
        key, transient = 'ui.llm.opencodeLocalNetworkFailed', True
    elif returncode is not None and returncode != 0:
        key, params = 'ui.llm.opencodeLocalRuntimeExit', {'exit_code': returncode}
    details['copy'] = {'key': key, 'params': params}
    return LlmError(tr(key, **params), code='llm/opencode-local-runtime', transient=transient, **details)


async def _start_process(*args, stop, start_timeout, **kwargs):
    """启动等待被取消时，仍接管迟到的进程并等待所属清理。"""
    startup = asyncio.create_task(asyncio.create_subprocess_exec(*args, **kwargs))
    try:
        return await asyncio.wait_for(asyncio.shield(startup), start_timeout)
    except (asyncio.CancelledError, TimeoutError):
        async def finish_startup():
            process = await startup
            await stop(process)
        await join_cleanup(asyncio.create_task(finish_startup()))
        raise


def _unique_fields(pairs):
    """资料与辅助协议均拒绝同一对象内重复字段。"""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _profile_json(path: Path) -> dict:
    """所有本机资料入口共用有界、严格UTF-8和唯一字段解析。"""
    with path.open('rb') as handle:
        raw = handle.read(MAX_PROFILE_BYTES + 1)
    if len(raw) > MAX_PROFILE_BYTES:
        raise ValueError
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_fields)
    if not isinstance(value, dict):
        raise ValueError
    _validate_json(value)
    return value


def _validate_json(value):
    """显式限制嵌套层数，不依赖宿主可变的Python递归上限。"""
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 128:
            raise ValueError
        if isinstance(item, str):
            item.encode('utf-8')
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError
        elif isinstance(item, dict):
            pending.extend((child, depth + 1) for child in (*item.keys(), *item.values()))
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)


def _profile_error(reason, path, executable=None, **details):
    """保留可定位的原因与资源身份，不向诊断写入资料正文。"""
    key = {
        'missing': 'ui.llm.opencodeLocalProfileMissing',
        'unreadable': 'ui.llm.opencodeLocalProfileUnreadable',
        'invalid': 'ui.llm.opencodeLocalProfileInvalid',
        'executableUnavailable': 'ui.llm.opencodeLocalProfileExecutableUnavailable',
        'executableChanged': 'ui.llm.opencodeLocalProfileExecutableChanged',
    }[reason]
    params = {'profile': str(path)}
    if executable is not None:
        params['executable'] = str(executable)
    return LlmError(tr(key, **params), code='llm/opencode-local-profile',
        reason=reason, profile_path=str(path), **details, copy={'key': key, 'params': params})


def _read_profile(path):
    """区分资料缺失、不可读取与内容损坏，所有入口继续共用严格解析。"""
    try:
        return _profile_json(path)
    except FileNotFoundError:
        raise _profile_error('missing', path) from None
    except OSError as exc:
        raise _profile_error('unreadable', path, errno=exc.errno) from None
    except (ValueError, TypeError, RecursionError):
        raise _profile_error('invalid', path) from None


def configured_transport(*, required=False):
    """显式本机认证缺少资料时拒绝，不能静默改用普通HTTP。"""
    executable = os.environ.get('HARNESS_OPENCODE_EXECUTABLE')
    explicit_profile = os.environ.get('HARNESS_OPENCODE_PROFILE')
    profile = Path(explicit_profile).expanduser() if explicit_profile else Path.home()/'.harness/opencode-local/profile.json'
    if not executable and not explicit_profile and not profile.exists():
        if required:
            raise _profile_error('missing', profile)
        return None
    if not executable:
        try:
            data = _read_profile(profile)
            executable = data['executable']
            if not isinstance(executable, str) or not Path(executable).is_absolute():
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise _profile_error('invalid', profile) from None
    return OpenCodeLocalTransport(executable, str(profile))


def executable_digest(path: Path) -> str:
    """绑定实际安装的运行时，升级后必须重新获取上下文。"""
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def load_profile(executable: Path, path: Path) -> dict:
    """拒绝缺失、损坏或与本机可执行文件不匹配的配置，不覆盖原件。"""
    data = _read_profile(path)
    try:
        if (not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1
                or not isinstance(data.get('executable_sha256'), str)
                or len(data['executable_sha256']) != 64
                or any(char not in '0123456789abcdef' for char in data['executable_sha256'])
                or not isinstance(data.get('headers'), dict)
                or set(data['headers']) != {'user-agent', 'x-opencode-client', 'x-opencode-project'}
                or not all(isinstance(k, str) and isinstance(v, str) and v and all(32 <= ord(char) <= 126 for char in v)
                           for k, v in data['headers'].items())
                or not isinstance(data.get('system'), list) or not data['system']
                or not all(isinstance(item, dict) and set(item) == {'role', 'content'}
                           and item['role'] == 'system' and isinstance(item['content'], str)
                           and item['content'] for item in data['system'])):
            raise ValueError
    except (ValueError, TypeError, RecursionError):
        raise _profile_error('invalid', path) from None
    try:
        digest = executable_digest(executable)
    except OSError as exc:
        raise _profile_error('executableUnavailable', path, executable, errno=exc.errno) from None
    if data['executable_sha256'] != digest:
        raise _profile_error('executableChanged', path, executable,
            expected_sha256=data['executable_sha256'], actual_sha256=digest)
    return data


class OpenCodeLocalTransport(Transport):
    """每个模型请求独占子进程，流式结果交回 Harness，取消时等待进程退出。"""

    def __init__(self, executable: str, profile: str):
        self.executable = Path(executable).expanduser().resolve()
        self.profile = Path(profile).expanduser().resolve()
        self.context = load_profile(self.executable, self.profile)
        self.context_tokens = sum(len(item['content']) for item in self.context['system']) // 2 + 512

    def prepare_body(self, body):
        """本机上下文进入真实请求和容量预检，Harness历史及工具保持原样。"""
        tools = body.get('tools', [])
        names = {tool['function']['name'] for tool in tools}
        if {'spawn_agent', 'subagent'} <= names:
            raise LlmError(tr('llm.opencode_local.tool_conflict'), code='llm/opencode-local-tools')
        def wire_tool(tool):
            function = tool.get('function', {})
            if function.get('name') == 'spawn_agent':
                return {**tool, 'function': {**function, 'name':'subagent'}}
            return tool
        messages = []
        for message in body['messages']:
            if message.get('role') == 'assistant' and 'tool_calls' in message:
                message = {**message, 'tool_calls':[wire_tool(call) for call in message['tool_calls']]}
            messages.append(message)
        result = {**body, 'messages': [*(dict(item) for item in self.context['system']),
            {'role':'system', 'content':tr('llm.opencode_local.instructions')}, *messages],
            'stream_options': {**body.get('stream_options', {}), 'include_usage': True}}
        if 'tools' in body:
            result['tools'] = [wire_tool(tool) for tool in tools]
        return result

    def restore_event(self, event, tools):
        """仅在本次提供过派生工具时还原名称，参数、调用身份及权限仍归Harness。"""
        if (event.kind == 'tool_use' and event.tool_use.name == 'subagent'
                and any(tool.name == 'spawn_agent' for tool in tools or ())):
            return replace(event, tool_use=replace(event.tool_use, name='spawn_agent'))
        return event

    def request_headers(self, conversation):
        """只把已哈希的对话关联交给本机子进程，不混用其他对话的缓存身份。"""
        from .opencode import ZEN_URL, request_headers
        return {'X-Harness-Conversation': request_headers(ZEN_URL, conversation)['x-opencode-session']}

    async def post_stream(self, url, *, headers, body, timeout):
        if url != BASE_URL + '/chat/completions' or headers.get('Authorization') != 'Bearer public':
            raise LlmError(tr('llm.opencode_local.scope_invalid'), code='llm/opencode-local-scope')
        try:
            if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
                raise ValueError
            request = json.loads(body, object_pairs_hook=_unique_fields)
            _validate_json(request)
            session = headers.get('X-Harness-Conversation')
            if not isinstance(request, dict) or (session is not None and not isinstance(session, str)):
                raise ValueError
            payload = json.dumps({'headers': self.context['headers'], 'body': request,
                'session': session, 'timeout_ms': max(1, int(timeout * 1000))},
                ensure_ascii=False, allow_nan=False).encode('utf-8')
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise LlmError(tr('llm.opencode_local.runtime_failed'), code='llm/opencode-local-runtime') from None
        env = stdio_env({'BUN_BE_BUN': '1'})
        try:
            process = await _start_process(str(self.executable),
                str(Path(__file__).with_suffix('.mjs')), stop=_stop_stream_process, start_timeout=min(60, timeout + 5),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=env)
        except (OSError, TimeoutError):
            key = 'ui.llm.opencodeLocalStartFailed'
            raise LlmError(tr(key), code='llm/opencode-local-runtime', phase='connect', copy={'key': key}) from None
        diagnostic = asyncio.create_task(_runtime_diagnostic(process.stderr))
        cleanup = None
        phase, received = 'connect', 0
        try:
            process.stdin.write(payload)
            await asyncio.wait_for(process.stdin.drain(), timeout=timeout + 5)
            process.stdin.close()
            metadata = json.loads(await asyncio.wait_for(process.stdout.readline(), timeout=timeout + 5),
                                  object_pairs_hook=_unique_fields)
            _validate_json(metadata)
            if not isinstance(metadata, dict):
                raise ValueError
            status = metadata['status']
            if type(status) is not int or not 100 <= status <= 599:
                raise ValueError
            if not 200 <= status < 300:
                error = diagnostic_text(await asyncio.wait_for(process.stdout.read(4096), timeout=timeout + 5))
                # 长冷却的匿名拒绝保留供应商原文，不能只显示通用重试等待上限。
                cooldown = parse_retry_after(metadata.get('retry_after'))
                try:
                    failure = json.loads(error, object_pairs_hook=_unique_fields)
                    rejected = (status == 429 and failure.get('error', {}).get('type') == 'FreeUsageLimitError'
                                and cooldown is not None and cooldown > MAX_RETRY_DELAY)
                except (ValueError, AttributeError, TypeError, RecursionError):
                    rejected = False
                if rejected:
                    raise LlmError(tr('llm.opencode_local.request_rejected', reason=error),
                        code='llm/opencode-local-rejected', status=status,
                        provider_error='FreeUsageLimitError', retry_after=cooldown)
                raise LlmError(tr('llm.opencode_local.http_failed', status=status, reason=error),
                    status=status, transient=status == 429 or status >= 500,
                    retry_after=cooldown)
            phase = 'read'
            while chunk := await asyncio.wait_for(process.stdout.read(65536), timeout=timeout + 5):
                received += len(chunk)
                yield chunk
            if await asyncio.wait_for(process.wait(), timeout=timeout + 5) != 0:
                raise ValueError
        except (TimeoutError, ValueError, KeyError, TypeError, OSError, RecursionError) as exc:
            returncode = process.returncode
            cleanup = asyncio.create_task(_finish_stream_process(process, diagnostic))
            await join_cleanup(cleanup)
            raise _runtime_failure(diagnostic.result(), phase=phase, returncode=returncode,
                received=received, timeout=timeout, cause=exc) from exc
        finally:
            if cleanup is None:
                cleanup = asyncio.create_task(_finish_stream_process(process, diagnostic))
            await join_cleanup(cleanup)


async def bootstrap_profile(executable: Path, destination: Path) -> None:
    """在隔离目录捕获官方系统上下文，捕获后终止，不发送模型请求。"""
    if destination.exists():
        raise LlmError(tr('llm.opencode_local.profile_exists'), code='llm/opencode-local-profile-exists')
    with tempfile.TemporaryDirectory(prefix='harness-opencode-bootstrap-') as directory:
        root = Path(directory)
        plugins = root / '.opencode/plugins'
        plugins.mkdir(parents=True)
        captured = root / 'profile.json'
        plugin = """import {writeFile} from 'node:fs/promises';
export default {id:'harness.local.bootstrap',async setup(ctx){
await ctx.session.hook('http.request',async event=>{
const body=await event.request.clone().json();
if(event.kind==='primary'){
if(event.request.headers.get('authorization')!=='Bearer public')throw Error('private_credential');
const headers=Object.fromEntries([...event.request.headers].filter(([k])=>
['user-agent','x-opencode-client','x-opencode-project'].includes(k)));
await writeFile(__PROFILE_PATH__,JSON.stringify({version:1,headers,
system:body.messages.filter(m=>m.role==='system')}));
}
throw Error('harness_profile_captured');
});
}};
"""
        # 捕获官方请求上下文后明确终止，不向远端发送模型请求。
        (plugins / 'bootstrap.js').write_text(plugin.replace('__PROFILE_PATH__', json.dumps(str(captured))), encoding='utf-8')
        env = stdio_env({})
        env.pop('BUN_BE_BUN', None)
        env.pop('OPENCODE_API_KEY', None)
        # OpenCode的run入口优先读取PWD，只有cwd不足以绑定临时项目及捕获钩子。
        env['PWD'] = str(root)
        # 私有服务与XDG目录隔离用户登录、配置、数据库及固定服务端口。
        for name, part in (('XDG_DATA_HOME', 'data'), ('XDG_STATE_HOME', 'state'),
                           ('XDG_CONFIG_HOME', 'config'), ('XDG_CACHE_HOME', 'cache'),
                           ('OPENCODE_TEST_HOME', 'home'), ('TMPDIR', 'tmp')):
            location = root / part
            location.mkdir()
            env[name] = str(location)
        env['TMP'] = env['TEMP'] = env['TMPDIR']
        for name in ('OPENCODE_CONFIG', 'OPENCODE_CONFIG_DIR', 'OPENCODE_CONFIG_CONTENT',
                     'OPENCODE_SERVER', 'OPENCODE_PASSWORD'):
            env.pop(name, None)
        command = [str(executable), 'run', '--standalone', '--model', 'opencode/big-pickle',
                   '--format', 'json', 'HARNESS_PROFILE_READY']
        try:
            process = await _start_process(sys.executable, '-I', '-X', 'utf8', '-c',
                _BOOTSTRAP_SUPERVISOR, json.dumps(command), stop=stop_process, start_timeout=60, cwd=root, env=env,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, start_new_session=os.name != 'nt',
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except (OSError, TimeoutError):
            raise LlmError(tr('llm.opencode_local.bootstrap_failed'), code='llm/opencode-local-bootstrap') from None
        try:
            # CLI会以捕获Hook的拒绝结束，是否取得合法资料由后续统一校验决定。
            line = await asyncio.wait_for(process.stdout.readline(), timeout=60)
            if not line or not line.strip().lstrip(b'-').isdigit():
                raise ValueError
        except (TimeoutError, ValueError):
            raise LlmError(tr('llm.opencode_local.bootstrap_failed'), code='llm/opencode-local-bootstrap') from None
        finally:
            await join_cleanup(asyncio.create_task(stop_process(process)))
        try:
            data = _profile_json(captured)
            data['executable'] = str(executable)
            data['executable_sha256'] = executable_digest(executable)
            captured.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            load_profile(executable, captured)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open('x', encoding='utf-8') as handle:
                if os.name != 'nt':
                    os.chmod(destination, 0o600)
                json.dump(data, handle, ensure_ascii=False)
        except (OSError, ValueError, RecursionError):
            raise LlmError(tr('llm.opencode_local.bootstrap_failed'), code='llm/opencode-local-bootstrap') from None
    load_profile(executable, destination)


async def refresh_profile(executable: Path, destination: Path, backup: Path) -> None:
    """显式刷新时先完成捕获与验证，再备份原件并原子替换，不自动恢复或重放。"""
    key = 'ui.llm.opencodeLocalProfileRefreshFailed'
    def identity():
        stat = destination.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
    try:
        if destination.is_symlink() or backup.exists() or backup.is_symlink():
            raise ValueError
        original_stat = identity()
        original_digest = executable_digest(destination)
        with tempfile.TemporaryDirectory(prefix='.opencode-refresh-', dir=destination.parent) as directory:
            candidate = Path(directory) / 'profile.json'
            await bootstrap_profile(executable, candidate)
            load_profile(executable, candidate)
            if identity() != original_stat or executable_digest(destination) != original_digest:
                raise ValueError
            # 独占创建备份；失败保留原件与已产生的备份，不覆盖任何既有文件。
            with destination.open('rb') as source, backup.open('xb') as target:
                if os.name != 'nt':
                    os.fchmod(target.fileno(), 0o600)
                shutil.copyfileobj(source, target)
                target.flush()
                os.fsync(target.fileno())
            if (identity() != original_stat or executable_digest(backup) != original_digest
                    or executable_digest(destination) != original_digest):
                raise ValueError
            os.replace(candidate, destination)
    except (OSError, ValueError) as exc:
        params = {'profile': str(destination), 'backup': str(backup)}
        raise LlmError(tr(key, **params), code='llm/opencode-local-refresh',
            copy={'key': key, 'params': params}) from exc


def main():
    """显式准备本机接口资料，不改模型列表、用户登录和已有运行目录。"""
    selector = argparse.ArgumentParser(add_help=False)
    selector.add_argument('--language', choices=('zh-CN', 'en'), default='zh-CN')
    language, _ = selector.parse_known_args()
    current_language.set(language.language)
    parser = argparse.ArgumentParser(parents=[selector], description=tr('llm.opencode_local.help'))
    parser.add_argument('--executable', required=True)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--refresh-backup', help=tr('llm.opencode_local.refresh_help'))
    args = parser.parse_args()
    current_language.set(args.language)
    executable = Path(args.executable).expanduser().resolve()
    profile = Path(args.profile).expanduser().absolute()
    if args.refresh_backup:
        asyncio.run(refresh_profile(executable, profile, Path(args.refresh_backup).expanduser().absolute()))
    else:
        asyncio.run(bootstrap_profile(executable, profile))
    print(tr('llm.opencode_local.ready'))


if __name__ == '__main__':
    main()
