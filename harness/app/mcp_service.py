"""管理设置页面所属的 MCP 和临时隧道，不接管手工启动的实例。"""
from __future__ import annotations

from harness.i18n import tr

import atexit
from contextvars import copy_context
import errno
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import tempfile
import time
from urllib import request as urllib_request
from urllib.request import ProxyHandler, Request, build_opener

from ..core.cleanup import raise_cleanup_errors
from ..core.language import current_language
from ..execution.config import ExecutionConfig, ProjectConfig
from ..core.errors import ConfigError
from ..llm.routes import mask_api_key
from ..tools.process_env import execution_env
from .cloudflared import resolve_cloudflared
from .mcp_errors import McpServiceError
from .quick_tunnel import QuickTunnelService
from .mcp_tunnel import DEFAULT_TUNNEL, public_tunnel, read_tunnel, validate_tunnel
from .chat_companion import ChatCompanionService, validate_settings as validate_companion


def _error_details(error):
    """自有错误保持资源键，外部诊断保持原文，组合不冻结源语言。"""
    if isinstance(error, BaseExceptionGroup):
        return [part for item in error.exceptions for part in _error_details(item)]
    if isinstance(error, McpServiceError):
        return [{'key': error.key, 'params': dict(error.values)}]
    return [str(error)]


def _error_text(error):
    """组合诊断保留原异常，读取时才解析自有错误的当前语言。"""
    if isinstance(error, BaseExceptionGroup):
        parts = [_error_text(item) for item in error.exceptions]
        text = parts[0] if parts else ''
        for part in parts[1:]:
            text = tr('ui.common.feedbackPair', first=text, second=part)
        return text
    return str(error)


def _tunnel_environment():
    """图形入口补充系统代理；显式环境配置始终优先。"""
    env = execution_env()
    if sys.platform in ('darwin', 'win32'):
        lookup = 'getproxies_macosx_sysconf' if sys.platform == 'darwin' else 'getproxies_registry'
        proxies = getattr(urllib_request, lookup, lambda: {})()
        for scheme in ('http', 'https', 'no'):
            name = scheme + '_proxy'
            if name not in env and name.upper() not in env and proxies.get(scheme):
                env[name.upper()] = proxies[scheme]
    return env


def _connector_ready(url):
    """回环健康检查不经过系统代理，不向公网发起探测。"""
    try:
        with build_opener(ProxyHandler({})).open(Request(url), timeout=.3) as response:
            return response.status == 200
    except OSError:
        return False


class _TunnelOutput:
    """持续排空字节流，只在内存保存有界尾部供失败诊断。"""

    def __init__(self, named_url='', secret=''):
        self.named_url, self.secret = named_url, secret
        self.ready = threading.Event()
        self.lock = threading.Lock()
        self.tail = b''
        self.url = ''

    def consume(self, chunk):
        """跨读取块识别官方域名，完整域名边界到达后才发布。"""
        with self.lock:
            self.tail = (self.tail + chunk)[-8192:]
            if self.named_url and b'Registered tunnel connection' in self.tail:
                self.url = self.named_url
                self.ready.set()
            match = re.search(rb'https://[a-z0-9-]+\.trycloudflare\.com(?=[\s/])', self.tail)
            if match and not self.url:
                self.url = match.group().decode('ascii')
                self.ready.set()

    def diagnostic(self):
        """外部输出严格解码并移除本次令牌，非法字节保留原始表示。"""
        with self.lock:
            try:
                detail = self.tail.decode('utf-8').strip()
            except UnicodeDecodeError:
                detail = repr(self.tail)
        if self.secret:
            detail = detail.replace(self.secret, '[redacted]')
        return detail

    def failure(self, returncode):
        """已确认限流使用独立资源身份，不冻结第三方诊断。"""
        detail = self.diagnostic()
        if re.search(r'(?i)(?:status\s+429|error code:\s*1015)', detail):
            return McpServiceError('ui.mcpService.error.rateLimited')
        return McpServiceError('ui.mcpService.error.tunnelStartDetails',
                               code=str(returncode) if returncode is not None else 'timeout', detail=detail)


class McpService:
    """启动在后台进行；状态接口不返回密码，只控制本实例的进程。"""

    def __init__(self, home):
        self.home = Path(home) / 'mcp-service'
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._thread = None
        self._drain_thread = None
        self._process = None
        self._tunnel = None
        self._quick_service = None
        self._state = {'status': 'stopped', 'local_url': '', 'public_url': '', 'error': '', 'project_root': ''}
        self.source = Path(__file__).resolve().parents[2]
        self.companion = ChatCompanionService(self.home, self.source)
        self._exit_registered = False
        self._quick_retry_at = 0
        self._metrics_url = ''

    def snapshot(self):
        """意外退出立即变成失败状态，不显示失效地址。"""
        with self._lock:
            if self._state['status'] == 'running' and any(p is None or p.poll() is not None for p in (self._process, self._tunnel)):
                self._state.update(status='failed', error=McpServiceError('ui.mcpService.error.exited'), local_url='', public_url='')
                self._cancel.set()
                self._cleanup()
            error = self._state['error']
            copy = {'key': error.key, 'params': dict(error.values)} if isinstance(error, McpServiceError) else None
            reconnecting = bool(self._state['status'] == 'running' and self._metrics_url
                                and not _connector_ready(self._metrics_url))
            try:
                settings, _ = read_tunnel(self.home / 'tunnel.json')
            except McpServiceError as exc:
                settings = getattr(self, '_launch_tunnel', dict(DEFAULT_TUNNEL))
                if not error:
                    error = exc
                    copy = {'key': exc.key, 'params': dict(exc.values)}
            password_mask = ''
            if self._state['status'] == 'running':
                password_path = self.home / 'password.json'
                if password_path.exists():
                    password_mask = mask_api_key(json.loads(password_path.read_text(encoding='utf-8'))['password'])
            return {**self._state, 'error': _error_text(error), 'error_copy': copy, 'password_mask': password_mask,
                    'companion': self.companion.snapshot(self._state['status'] == 'running'),
                    'tunnel': public_tunnel(settings), 'quick_retry_at': self._quick_retry_at, 'reconnecting': reconnecting,
                    'tunnel_diagnostic': getattr(self, '_tunnel_diagnostic', ''),
                    **({'error_details': _error_details(error)} if isinstance(error, BaseExceptionGroup) else {})}

    def start(self, root, project_id='project', *, workspace_registry=None, tunnel=None):
        """重复点击复用启动操作；明确登记所选项目标识与真实目录。"""
        with self._lock:
            self.snapshot()
            if self._state['status'] in {'starting', 'running', 'stopping'}:
                return self.snapshot()
            if self._thread is not None and self._thread.is_alive():
                raise McpServiceError('ui.mcpService.error.cleanupPending')
            if self._process is not None or self._tunnel is not None or self._quick_service is not None or self._drain_thread is not None or self.companion.server is not None:
                raise McpServiceError('ui.mcpService.error.cleanupPending')
            if not isinstance(project_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', project_id):
                raise ValueError(tr('python.catalog.validation.mcpProjectId'))
            root = Path(root).resolve(strict=True)
            if not root.is_dir():
                raise ValueError(tr('python.catalog.validation.projectMissing'))
            if workspace_registry is None and self.home.resolve().is_relative_to(root):
                raise ValueError(tr('python.catalog.validation.privateStorageLocation'))
            settings, raw = read_tunnel(self.home / 'tunnel.json')
            if tunnel is not None:
                settings = validate_tunnel(tunnel, settings)
                self._private_home()
                self._save_config(self.home / 'tunnel.json', settings, raw)
            self._launch_tunnel = settings
            if settings['mode'] == 'quick' and time.time() < self._quick_retry_at:
                self._state.update(status='failed', error=McpServiceError('ui.mcpService.error.rateLimited'))
                return self.snapshot()
            self._tunnel_diagnostic = ''
            self._cancel.clear()
            self._state.update(status='starting', error='', local_url='', public_url='', project_root=str(root))
            self._state['project_id'] = project_id
            self._state['project_scope'] = 'all' if workspace_registry else 'selected'
            self._thread = threading.Thread(target=copy_context().run, args=(self._launch, root, project_id, workspace_registry), daemon=True, name='harness-mcp-start')
            if not self._exit_registered:
                atexit.register(self.stop)
                self._exit_registered = True
            try:
                self._thread.start()
            except RuntimeError as exc:
                self._thread = None
                self._cancel.set()
                self._state.update(status='failed', error=str(exc))
                raise
            return self.snapshot()

    def start_all(self, registry, *, tunnel=None):
        """登记一次全部项目策略；以后由执行器实时跟随本机注册表。"""
        return self.start(self.source, workspace_registry=str(Path(registry).resolve()), tunnel=tunnel)

    def _private_home(self):
        """仅收紧新建的私密目录，绝不修改项目目录的权限。"""
        self.home.mkdir(parents=True, exist_ok=True)
        self.home.chmod(0o700)
        if os.name == 'nt':
            flags = subprocess.CREATE_NO_WINDOW
            sid = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], creationflags=flags, timeout=10)
            match = re.search(rb'S-1-5-[0-9-]+', sid)
            if not match:
                raise McpServiceError('ui.mcpService.error.windowsUser')
            subprocess.run(['icacls', str(self.home), '/inheritance:r', '/grant:r',
                f'*{match.group().decode("ascii")}:(OI)(CI)F', '*S-1-5-18:(OI)(CI)F'], check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, timeout=10)

    def _python(self):
        """使用当前宿主的必需依赖；缺失时明确失败，不切换解释器。"""
        if importlib.util.find_spec('mcp') and importlib.util.find_spec('uvicorn'):
            return sys.executable
        raise McpServiceError('ui.mcpService.error.dependencies')

    def _cloudflared(self):
        """复用官方隧道程序，不更改其安装实例的配置。"""
        return resolve_cloudflared(self.home, self._cancel)

    def _spawn(self, argv, **options):
        """不打开终端窗口；使用参数数组，不拼接 Shell。"""
        return subprocess.Popen(argv, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0, **options)

    def _save_config(self, path: Path, data: dict, previous: bytes | None) -> None:
        """完整同步本次临时配置后发布；已观察到的外部修改保持原件。"""
        descriptor, filename = tempfile.mkstemp(prefix='.mcp-config-', dir=path.parent)
        temporary = Path(filename)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))
                stream.flush()
                os.fsync(stream.fileno())
            try:
                current = path.read_bytes()
            except FileNotFoundError:
                current = None
            if current != previous:
                raise McpServiceError('ui.mcpService.error.configChanged')
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _launch(self, root, project_id, workspace_registry):
        """先取得临时域名，再用同一域名初始化 OAuth 发行者。"""
        primary = None
        try:
            config = self.home / 'config.json'
            previous_data = {}
            previous_bytes = None
            verification = {}
            if config.exists():
                try:
                    previous_bytes = config.read_bytes()
                    previous_data = json.loads(previous_bytes.decode('utf-8'))
                    previous = ExecutionConfig.from_dict(previous_data, base=self.home)
                except (ConfigError, ValueError, TypeError) as exc:
                    raise McpServiceError('ui.mcpService.error.invalidSavedConfig') from exc
                verification = next((project.verification for project in previous.projects.values()
                                     if project.root == root), {})
            ProjectConfig(root=root, writable=True, commands=True, commits=True, verification=verification)
            python = self._python()
            self._private_home()
            settings = getattr(self, '_launch_tunnel', dict(DEFAULT_TUNNEL))
            named = settings['mode'] == 'named'
            with socket.socket() as sock, socket.socket() as local_sock, socket.socket() as metrics_sock:
                try:
                    # POSIX 与实际服务器保持地址复用语义，避免已关闭连接导致重启误报。
                    # Windows 保留独占检测，不能通过地址复用与其他服务重叠监听。
                    if sys.platform != 'win32':
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    sock.bind(('127.0.0.1', settings['origin_port'] if named else 0))
                    sock.listen()
                except OSError as exc:
                    if named and exc.errno == errno.EADDRINUSE:
                        raise McpServiceError('ui.mcpService.error.originPortBusy', port=settings['origin_port']) from exc
                    raise
                port = sock.getsockname()[1]
                local_sock.bind(('127.0.0.1', 0))
                local_port = local_sock.getsockname()[1]
                metrics_sock.bind(('127.0.0.1', 0))
                metrics_port = metrics_sock.getsockname()[1]
            cloudflared = self._cloudflared()
            upstream = f'http://127.0.0.1:{port}'
            local = f'http://127.0.0.1:{local_port}'
            isolated = self.home / 'cloudflared.yml'
            isolated.write_text('{}\n', encoding='utf-8')
            data = {'home': str(self.home / 'state'), 'skill_home': str(self.home.parent.resolve()), 'language': current_language.get(), 'projects': {
                project_id: {'root': str(root), 'writable': True, 'commands': True, 'commits': True,
                    'verification': verification}}}
            if workspace_registry:
                data['workspace_registry'] = workspace_registry
                backup = self.home / 'config.before-all-projects.json'
                if previous_bytes is not None and not previous_data.get('workspace_registry') and not backup.exists():
                    backup.write_bytes(previous_bytes)
                data['projects'] = previous_data.get('projects', {})
            self._save_config(config, data, previous_bytes)
            with self._lock:
                if self._cancel.is_set():
                    return
                env = _tunnel_environment()
                for name in ('TUNNEL_TOKEN', 'TUNNEL_TOKEN_FILE', 'TUNNEL_URL', 'TUNNEL_METRICS', 'TUNNEL_QUICK_SERVICE'):
                    env.pop(name, None)
                argv = [cloudflared, '--config', str(isolated), 'tunnel',
                        '--protocol', 'http2', '--no-autoupdate', '--metrics', f'127.0.0.1:{metrics_port}']
                self._metrics_url = f'http://127.0.0.1:{metrics_port}/ready'
                if named:
                    env['TUNNEL_TOKEN'] = settings['token']
                    argv.append('run')
                else:
                    argv.extend(['--url', upstream])
                proxy = env.get('https_proxy', env.get('HTTPS_PROXY', ''))
                if not named and proxy:
                    # 固定版本的申请 HTTP Transport 不读取代理环境；只接管域名申请。
                    self._quick_service = QuickTunnelService(proxy)
                    argv.extend(['--quick-service', self._quick_service.start()])
                self._tunnel = self._spawn(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                tunnel = self._tunnel
            output = _TunnelOutput(settings['public_url'] if named else '', settings['token'])

            def drain():
                """持续排空日志，失败时保留有界的第三方诊断。"""
                try:
                    while chunk := tunnel.stdout.read1(4096):
                        output.consume(chunk)
                except (ValueError, OSError):
                    pass

            with self._lock:
                drain_thread = threading.Thread(target=drain, daemon=True, name='harness-mcp-tunnel')
                self._drain_thread = drain_thread
                try:
                    self._drain_thread.start()
                except BaseException:
                    self._drain_thread = None
                    raise
            deadline = time.monotonic() + 90
            while True:
                if self._cancel.wait(.2):
                    return
                if tunnel.poll() is None and (output.ready.is_set() or named) and _connector_ready(self._metrics_url):
                    if named:
                        output.url = settings['public_url']
                    break
                if tunnel.poll() is not None or time.monotonic() > deadline:
                    if tunnel.poll() is not None:
                        drain_thread.join(timeout=1)
                    self._tunnel_diagnostic = output.diagnostic()
                    failure = output.failure(tunnel.poll())
                    if not named and failure.key == 'ui.mcpService.error.rateLimited':
                        self._quick_retry_at = int(time.time()) + 300
                    raise failure
            env = execution_env()
            env['PYTHONPATH'] = str(self.source) + os.pathsep + env.get('PYTHONPATH', '')
            with self._lock:
                if self._cancel.is_set():
                    return
                self._process = self._spawn([python, '-m', 'harness.mcp_server.managed', '--config', str(config),
                    '--port', str(port), '--local-port', str(local_port), '--public-url', output.url], cwd=str(self.source), env=env,
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            health = build_opener(ProxyHandler({}))
            deadline = time.monotonic() + 30
            while not self._cancel.wait(.2):
                if self._process.poll() is not None:
                    raise McpServiceError('ui.mcpService.error.start')
                if tunnel.poll() is not None:
                    self._tunnel_diagnostic = output.diagnostic()
                    raise output.failure(tunnel.poll())
                try:
                    with health.open(local + '/healthz', timeout=1) as response, health.open(upstream + '/healthz', timeout=1) as public_response:
                        if response.status == public_response.status == 200:
                            break
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise McpServiceError('ui.mcpService.error.startTimeout')
            with self._lock:
                if not self._cancel.is_set():
                    self._state.update(status='running', local_url=local + '/mcp', public_url=output.url + '/mcp')
                    self.companion.start()
        except Exception as exc:
            primary = exc
            with self._lock:
                if not self._cancel.is_set():
                    self._state.update(status='failed', error=exc if isinstance(exc, McpServiceError) else str(exc), local_url='', public_url='')
        finally:
            with self._lock:
                cleanup_error = None
                if self._state['status'] != 'running' or self._cancel.is_set():
                    try:
                        self._cleanup()
                    except Exception as exc:
                        cleanup_error = exc
                        error = BaseExceptionGroup(tr('cleanup.failed'), [primary, exc]) if primary is not None else exc
                        self._state.update(status='failed', error=error, local_url='', public_url='')
                if cleanup_error is None and self._cancel.is_set() and self._state['status'] == 'stopping':
                    self._state.update(status='stopped', error='')

    def password(self):
        """由专门的本机 POST 接口按需读取，不放进状态或日志。"""
        with self._lock:
            if self.snapshot()['status'] != 'running':
                raise ValueError(tr('python.catalog.validation.mcpNotStarted'))
            return json.loads((self.home / 'password.json').read_text(encoding='utf-8'))['password']

    def configure_companion(self, value):
        """保存界面明确配置；运行中的 MCP 无需重启即可启停接续桥。"""
        with self._lock:
            settings = validate_companion(value)
            _, previous = self.companion.settings()
            self._private_home()
            self._save_config(self.home / 'companion.json', settings, previous)
            self.companion.stop()
            if self._state['status'] == 'running':
                self.companion.start()
            return self.snapshot()

    def companion_token(self):
        """配对码只在显式本机 POST 请求后返回，不进入常规状态响应。"""
        with self._lock:
            if self._state['status'] != 'running' or self.companion.snapshot(True)['status'] != 'running':
                raise McpServiceError('ui.companion.notRunning')
            return self.companion.token()

    def _cleanup(self):
        """逐项回收所属进程；失败句柄保留，只有成功项释放引用。"""
        errors = []
        try:
            self.companion.stop()
        except BaseException as exc:
            errors.append(exc)
        for attribute in ('_process', '_tunnel'):
            process = getattr(self, attribute)
            if process is None:
                continue
            try:
                if process.poll() is None:
                    if attribute == '_process' and process.stdin:
                        process.stdin.close()
                    else:
                        process.terminate()
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                if process.stdout:
                    process.stdout.close()
            except Exception as exc:
                errors.append(exc)
            else:
                setattr(self, attribute, None)
        quick_service = self._quick_service
        if quick_service is not None:
            try:
                quick_service.stop()
            except BaseException as exc:
                errors.append(exc)
            else:
                self._quick_service = None
        drain = self._drain_thread
        if drain is not None and drain is not threading.current_thread():
            try:
                drain.join(timeout=3)
                if drain.is_alive():
                    raise McpServiceError('ui.mcpService.error.drainAlive')
            except BaseException as exc:
                errors.append(exc)
            else:
                self._drain_thread = None
        raise_cleanup_errors(errors)

    def stop(self):
        """逐项停止进程并等待启动线程，失败不跳过其他所属收尾。"""
        errors = []
        with self._lock:
            self._cancel.set()
            self._state.update(status='stopping', local_url='', public_url='')
            thread = self._thread
            try:
                self._cleanup()
            except BaseException as exc:
                errors.append(exc)
        if thread and thread is not threading.current_thread():
            try:
                thread.join(timeout=3)
            except BaseException as exc:
                errors.append(exc)
        with self._lock:
            drain = self._drain_thread
            pending = bool(thread and thread.is_alive() or drain and drain.is_alive() or self._process is not None or self._tunnel is not None or self._quick_service is not None or self.companion.server is not None)
            self._state.update(status='stopping' if pending or errors else 'stopped')
            if not errors:
                self._state['error'] = ''
            if not pending and not errors and self._exit_registered:
                atexit.unregister(self.stop)
                self._exit_registered = False
            result = self.snapshot()
        raise_cleanup_errors(errors)
        return result
