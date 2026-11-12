"""开发重载仅观察产品源码和资源，测试、样例与缓存变更不触发后端重启。"""
import errno
import os
import signal
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from scripts.dev import check_ports, listener_pids, main, release_port, snapshot


class DevReloadTests(unittest.TestCase):
    def setUp(self):
        """地区探测使用替身，设置读取限制在隔离运行根。"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        gateway = Path(directory.name) / 'gateway'
        gateway.mkdir()
        (gateway / 'codex-gateway.json').write_text('{"enabled": false}', encoding='utf-8')
        region = patch('harness.core.language.detect_region', return_value=('US', 'fixture'))
        region.start()
        self.addCleanup(region.stop)
        home = patch.dict('os.environ', {'HARNESS_HOME': directory.name, 'HARNESS_CODEX_GATEWAY_HOME': str(gateway)})
        home.start()
        self.addCleanup(home.stop)

    def test_test_changes_do_not_change_runtime_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'harness' / 'core' / 'agent.py'
            source.parent.mkdir(parents=True)
            source.write_text('value = 1\n')
            before = snapshot(root)
            for name in ('tests/test_agent.py', 'harness/tests/helper.py',
                         'harness/core/test_agent.py', 'harness/core/agent_test.py',
                         'harness/core/agent.test.json', 'harness/__tests__/data.md',
                         'harness/core/__pycache__/agent.py', 'examples/task.md',
                         'desktop/package.json', 'desktop/package-lock.json',
                         'docs/development.md', 'web/src/main.ts'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('测试变更\n')
            self.assertEqual(snapshot(root), before)

    def test_runtime_edit_addition_and_deletion_change_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'harness' / 'core' / 'agent.py'
            source.parent.mkdir(parents=True)
            source.write_text('value = 1\n')
            before = snapshot(root)
            source.write_text('value = 100\n')
            self.assertNotEqual(snapshot(root), before)
            before = snapshot(root)
            resource = root / 'harness' / 'prompt.md'
            resource.write_text('当前运行提示词')
            self.assertNotEqual(snapshot(root), before)
            before = snapshot(root)
            resource.unlink()
            self.assertNotEqual(snapshot(root), before)

    def test_launcher_messages_and_restarted_backend_follow_explicit_language(self):
        """真实入口解析语言，重启命令、提示和调用方上下文保持同一归属。"""
        from harness.core.language import current_language
        from harness.i18n import tr
        original = current_language.get()
        for language in ('en', 'zh-CN', 'en'):
            backend = Mock(returncode=7)
            backend.poll.return_value = 7
            frontend = Mock(returncode=0)
            frontend.poll.side_effect = [None, None, 0]
            replacement = Mock(returncode=9)
            replacement.poll.return_value = 9
            with (patch('scripts.dev.sys.argv', ['dev.py', '--language', language, '--share', 'http://192.168.1.14:8766']),
                  patch('scripts.dev.check_ports'),
                  patch('scripts.dev.shutil.which', return_value='/fixture/node'),
                  patch('scripts.dev.Path.is_file', return_value=True),
                  patch('scripts.dev.signal.signal'), patch('scripts.dev.time.sleep'),
                  patch('scripts.dev.snapshot', side_effect=[{}, {}, {'source': (1, 1)}, {'source': (1, 1)}]),
                  patch('scripts.dev.subprocess.Popen', side_effect=[backend, frontend, replacement]) as spawn,
                  patch('scripts.dev.stop') as stop, patch('builtins.print') as output):
                self.assertEqual(main(), 0)
                for index in (0, 2):
                    command = spawn.call_args_list[index].args[0]
                    self.assertEqual(command[command.index('--language') + 1], language)
                    self.assertIn('--share', command)
                with current_language.set(language):
                    messages = [call.args[0] for call in output.call_args_list]
                    self.assertEqual(messages, [
                        tr('runtime.dev.addresses', web='http://127.0.0.1:5273', backend='http://127.0.0.1:8765'),
                        tr('runtime.dev.sharedAddress', url='http://192.168.1.14:8766/shared'),
                        tr('runtime.dev.reloadNotice'), tr('runtime.dev.backendExited', code=7),
                        tr('runtime.dev.restarting'), tr('runtime.dev.backendExited', code=9),
                    ])
                self.assertEqual(spawn.call_count, 3)
                # 未启用网关的 stop(None) 保留为无操作，实际进程的收尾顺序仍逐项核对。
                self.assertEqual([call.args[0] for call in stop.call_args_list], [backend, None, frontend, replacement, None])
            self.assertEqual(current_language.get(), original)

    def test_port_error_uses_current_language_before_process_creation(self):
        """端口冲突按调用语言拒绝，原系统诊断保留且不启动子进程。"""
        from harness.core.language import current_language
        from harness.i18n import tr
        for language in ('en', 'zh-CN', 'en'):
            with (patch('scripts.dev.sys.argv', ['dev.py', '--language', language]),
                  patch('scripts.dev.shutil.which', return_value='/fixture/node'),
                  patch('scripts.dev.Path.is_file', return_value=True),
                  patch('scripts.dev.check_ports', side_effect=OSError('Original diagnostic')),
                  patch('scripts.dev.subprocess.Popen') as spawn,
                  patch('sys.stderr') as stderr):
                with self.assertRaises(SystemExit) as caught:
                    main()
                self.assertEqual(caught.exception.code, 2)
                with current_language.set(language):
                    self.assertIn(tr('runtime.dev.portUnavailable', error='Original diagnostic'),
                                  ''.join(call.args[0] for call in stderr.write.call_args_list))
                spawn.assert_not_called()


    def test_invalid_ports_never_stop_processes(self):
        for ports in ((8765, 8765), (8765, 0), (8765, 65536), (-1, 5273)):
            with patch('scripts.dev.release_port') as release:
                with self.assertRaises(ValueError):
                    check_ports(*ports)
                release.assert_not_called()

    def test_only_address_in_use_triggers_release_and_all_ports_are_rechecked(self):
        conflict = OSError(errno.EADDRINUSE, 'occupied')
        with (patch('scripts.dev.bind_port', side_effect=[conflict, None, None, None]) as bind,
              patch('scripts.dev.release_port') as release):
            check_ports(8765, 5273)
            release.assert_called_once_with(8765)
            self.assertEqual([call.args[0] for call in bind.call_args_list], [8765, 5273, 8765, 5273])
        with (patch('scripts.dev.bind_port', side_effect=PermissionError('denied')),
              patch('scripts.dev.release_port') as release):
            with self.assertRaises(PermissionError):
                check_ports(8765, 5273)
            release.assert_not_called()

    def test_listener_query_filters_tcp_listeners_on_both_platforms(self):
        for platform in ('darwin', 'win32'):
            with (patch('scripts.dev.sys.platform', platform),
                  patch('scripts.dev.subprocess.run', return_value=Mock(returncode=0, stdout='123\n123\n456\n', stderr='')) as run):
                self.assertEqual(listener_pids(8765), {123, 456})
                command = run.call_args.args[0]
                if platform == 'win32':
                    self.assertIn('Get-NetTCPConnection -State Listen', command[-1])
                    self.assertIn('$_.LocalPort -eq 8765', command[-1])
                else:
                    self.assertIn('-iTCP:8765', command)
                    self.assertIn('-sTCP:LISTEN', command)

    def test_stop_notice_resolves_language_and_timeout_does_not_repeat_signals(self):
        """每阶段信号只发一次，强制停止前重新核对端口归属。"""
        from harness.core.language import current_language
        from harness.i18n import tr
        for language in ('en', 'zh-CN', 'en'):
            with (current_language.set(language),
                  patch('scripts.dev.listener_pids', return_value={987654}),
                  patch('scripts.dev.os.name', 'posix'),
                  patch('scripts.dev.signal.SIGKILL', 9, create=True),
                  patch('scripts.dev.os.kill') as kill,
                  patch('scripts.dev.wait_port', side_effect=[False, False]),
                  patch('builtins.print') as output):
                with self.assertRaises(OSError) as caught:
                    release_port(8765)
                self.assertIn(tr('runtime.dev.portReleaseFailed', port=8765), str(caught.exception))
                self.assertEqual([call.args for call in kill.call_args_list],
                                 [(987654, signal.SIGTERM), (987654, signal.SIGKILL)])
                self.assertEqual([call.args[0] for call in output.call_args_list], [
                    tr('runtime.dev.stoppingPort', port=8765, pid=987654),
                    tr('runtime.dev.forceStoppingPort', port=8765, pid=987654)])

    def test_port_probe_uses_posix_reuse_without_enabling_windows_reuse(self):
        from scripts.dev import bind_port
        for platform in ('posix', 'nt'):
            listener = Mock()
            context = Mock()
            context.__enter__ = Mock(return_value=listener)
            context.__exit__ = Mock(return_value=False)
            with patch('scripts.dev.socket.socket', return_value=context), patch('scripts.dev.os.name', platform):
                bind_port(8765)
            listener.bind.assert_called_once_with(('127.0.0.1', 8765))
            listener.listen.assert_called_once_with()
            if platform == 'posix':
                listener.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            else:
                listener.setsockopt.assert_not_called()

    def test_new_listener_is_not_force_killed(self):
        with (patch('scripts.dev.listener_pids', side_effect=[{987654}, {987655}]),
              patch('scripts.dev.os.kill') as kill,
              patch('scripts.dev.wait_port', side_effect=[False, False])):
            with self.assertRaises(OSError):
                release_port(8765)
            kill.assert_called_once_with(987654, signal.SIGTERM)

    def test_exited_listener_and_windows_termination_are_rechecked(self):
        with (patch('scripts.dev.listener_pids', return_value=set()),
              patch('scripts.dev.wait_port', return_value=True),
              patch('scripts.dev.os.kill') as kill):
            release_port(8765)
            kill.assert_not_called()
        with (patch('scripts.dev.listener_pids', return_value={987654}),
              patch('scripts.dev.os.name', 'nt'),
              patch('scripts.dev.wait_port', side_effect=[False, True]),
              patch('scripts.dev.os.kill') as kill):
            release_port(8765)
            kill.assert_called_once_with(987654, signal.SIGTERM)

    def test_unknown_self_and_parent_listeners_are_not_killed(self):
        for pids in (set(), {0}, {1}, {os.getpid()}, {os.getppid()}):
            with (patch('scripts.dev.listener_pids', return_value=pids),
                  patch('scripts.dev.os.kill') as kill,
                  patch('scripts.dev.wait_port', return_value=False)):
                with self.assertRaises(OSError):
                    release_port(8765)
                kill.assert_not_called()

    @unittest.skipIf(sys.platform == 'win32', '真实 POSIX 监听进程回归；Windows 查询分支另用替身覆盖')
    def test_real_occupied_port_is_released_without_stopping_other_listener(self):
        """仅使用临时随机端口和本用例创建的进程，不触及用户服务。"""
        code = ('import socket,time; s=socket.socket(); s.bind(("127.0.0.1",0)); '
                's.listen(); print(s.getsockname()[1],flush=True); time.sleep(60)')
        child = subprocess.Popen([sys.executable, '-u', '-c', code], stdout=subprocess.PIPE, text=True)
        try:
            port = int(child.stdout.readline())
            with socket.socket() as other:
                other.bind(('127.0.0.1', 0))
                other.listen()
                check_ports(port)
                child.wait(timeout=5)
                self.assertIsNotNone(child.returncode)
                with self.assertRaises(OSError):
                    from scripts.dev import bind_port
                    bind_port(other.getsockname()[1])
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()
            child.stdout.close()


    @unittest.skipIf(sys.platform == 'win32', '真实 POSIX 信号与连接等待回归')
    def test_real_listener_ignoring_term_is_force_stopped(self):
        """原实现只发 SIGTERM，此用例会超时失败；修复后必须回收真实进程。"""
        code = ('import socket,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
                's=socket.socket(); s.bind(("127.0.0.1",0)); s.listen(); '
                'print(s.getsockname()[1],flush=True); time.sleep(60)')
        child = subprocess.Popen([sys.executable, '-u', '-c', code], stdout=subprocess.PIPE, text=True)
        try:
            port = int(child.stdout.readline())
            check_ports(port)
            self.assertEqual(child.wait(timeout=5), -signal.SIGKILL)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
            child.stdout.close()

    @unittest.skipIf(sys.platform == 'win32', '真实 POSIX 地址复用回归')
    def test_closed_http_connection_does_not_require_killing_a_listener(self):
        """服务主动关闭已建立连接后，端口复用探测仍可成功。"""
        from scripts.dev import bind_port
        with socket.socket() as server, socket.socket() as client:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(('127.0.0.1', 0))
            server.listen()
            port = server.getsockname()[1]
            client.connect(('127.0.0.1', port))
            connection, _ = server.accept()
            connection.sendall(b'preview')
            connection.close()
            while client.recv(1024):
                pass
        with patch('scripts.dev.listener_pids') as query, patch('scripts.dev.os.kill') as kill:
            check_ports(port)
            query.assert_not_called()
            kill.assert_not_called()
        bind_port(port)
