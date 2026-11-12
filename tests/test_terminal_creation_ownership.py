"""终端创建的名称准入与取消收尾使用屏障，不启动真实 Shell。"""
import asyncio
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.tools.builtin.terminal_tools import TerminalError, TerminalSessionService


def fake_process():
    process = SimpleNamespace(pid=123, returncode=None, stdout=None, _transport=Mock())
    async def stop():
        process.returncode = 0
    process.stop_owned = AsyncMock(side_effect=stop)
    return process


@unittest.skipUnless(os.name == 'posix', 'POSIX 创建分支；Windows 原生创建由独立回归覆盖')
class TerminalCreationOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_name_is_reserved_before_spawn_yields(self):
        service = TerminalSessionService(backends={'fixture':['fixture']})
        service.handoff_grace = 0
        entered, release = asyncio.Event(), asyncio.Event()
        async def create(*args, **kwargs):
            entered.set()
            await release.wait()
            return fake_process()
        with patch('harness.tools.pty_process.spawn_pty', side_effect=create) as create_mock:
            first = asyncio.create_task(service.spawn(owner='a', kind='fixture', name='same'))
            await entered.wait()
            second = asyncio.create_task(service.spawn(owner='a', kind='fixture', name='same'))
            try:
                await asyncio.sleep(0)
                self.assertEqual(create_mock.call_count, 1)
            finally:
                release.set()
                results = await asyncio.gather(first, second, return_exceptions=True)
                await service.close_all()
            self.assertIsInstance(results[1], TerminalError)
            self.assertEqual(len(service.list('a')), 0)

    async def test_failed_or_cancelled_creation_releases_name_and_owners_are_independent(self):
        service = TerminalSessionService(backends={'fixture':['fixture']})
        service.handoff_grace = 0
        for failure in (OSError('fixture'), asyncio.CancelledError()):
            with patch('harness.tools.pty_process.spawn_pty', side_effect=failure):
                with self.assertRaises(TerminalError if isinstance(failure, OSError) else asyncio.CancelledError):
                    await service.spawn(owner='a', kind='fixture', name='same')
            self.assertNotIn('same', service._reserved.get('a', set()))
        with patch('harness.tools.pty_process.spawn_pty', side_effect=lambda *a, **k: fake_process()):
            try:
                await service.spawn(owner='a', kind='fixture', name='same')
                await service.spawn(owner='b', kind='fixture', name='same')
                self.assertEqual(len(service.list('a')), 1)
                self.assertEqual(len(service.list('b')), 1)
            finally:
                await service.close_all()

    async def test_repeated_cancel_waits_for_unpublished_terminal_cleanup(self):
        service = TerminalSessionService(backends={'fixture':['fixture']})
        motd, stopping, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        process = fake_process()
        async def collect(session):
            motd.set()
            await asyncio.Event().wait()
        async def stop():
            stopping.set()
            await release.wait()
            process.returncode = 0
        process.stop_owned.side_effect = stop
        with patch('harness.tools.pty_process.spawn_pty', AsyncMock(return_value=process)), patch.object(service, '_collect_motd', side_effect=collect):
            task = asyncio.create_task(service.spawn(owner='a', kind='fixture', name='same'))
            await motd.wait()
            task.cancel()
            await stopping.wait()
            try:
                for _ in range(2):
                    task.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(task.done())
                    process._transport.close.assert_not_called()
                    self.assertIn('same', service._reserved['a'])
            finally:
                release.set()
                result = (await asyncio.gather(task, return_exceptions=True))[0]
                await service.close_all()
            self.assertIsInstance(result, asyncio.CancelledError)
            process._transport.close.assert_called_once()
            process.stop_owned.assert_awaited_once()

    async def test_pty_creation_repeated_cancel_keeps_late_process_owned(self):
        from harness.tools.pty_process import spawn_pty
        import pty
        entered, release = asyncio.Event(), asyncio.Event()
        process = SimpleNamespace(kill=Mock(), wait=AsyncMock(return_value=0))
        descriptors = []
        original_open = pty.openpty
        def open_pair():
            pair = original_open()
            descriptors.extend(pair)
            return pair
        async def create(*args, **kwargs):
            entered.set()
            await release.wait()
            return process
        with patch('pty.openpty', side_effect=open_pair), patch('harness.tools.pty_process.asyncio.create_subprocess_exec', side_effect=create):
            task = asyncio.create_task(spawn_pty(['fixture'], cwd='.', env={}))
            await entered.wait()
            try:
                for _ in range(3):
                    task.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(task.done())
            finally:
                release.set()
                result = (await asyncio.gather(task, return_exceptions=True))[0]
                leaked = []
                for descriptor in descriptors:
                    try:
                        os.fstat(descriptor)
                    except OSError:
                        continue
                    leaked.append(descriptor)
                    os.close(descriptor)
            self.assertIsInstance(result, asyncio.CancelledError)
            self.assertEqual(leaked, [])
            process.kill.assert_called_once()
            process.wait.assert_awaited_once()

    async def test_real_pty_helper_starts_and_releases_transport(self):
        from harness.tools.pty_process import spawn_pty
        with tempfile.TemporaryDirectory() as directory:
            process = await spawn_pty([sys.executable, '-c', "print('PTY_FIXTURE_READY')"], cwd=directory, env=dict(os.environ))
            try:
                async with asyncio.timeout(5):
                    output = bytearray()
                    while chunk := await process.stdout.read(4096):
                        output.extend(chunk)
                    self.assertEqual(await process.wait(), 0)
                    self.assertIn(b'PTY_FIXTURE_READY', output)
            finally:
                await process.stop_owned()
                process.close()
            self.assertEqual(process.fd, -1)


@unittest.skipUnless(os.name == 'posix', 'POSIX 创建屏障；不模拟跨平台实机验收')
class TerminalShutdownOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_waits_for_accepted_creation_and_blocks_overlapping_creation(self):
        for all_owners in (False, True):
            with self.subTest(all_owners=all_owners):
                service = TerminalSessionService(backends={'fixture': ['fixture']})
                service.handoff_grace = 0
                entered, release, closing = asyncio.Event(), asyncio.Event(), asyncio.Event()
                process = fake_process()
                original_close = service._close_owner

                async def create(*args, **kwargs):
                    entered.set()
                    await release.wait()
                    return process

                async def close_owner(owner):
                    closing.set()
                    await original_close(owner)

                with patch('harness.tools.pty_process.spawn_pty', side_effect=create), patch.object(service, '_close_owner', side_effect=close_owner):
                    opening = asyncio.create_task(service.spawn(owner='a', kind='fixture', name='same'))
                    await entered.wait()
                    close = asyncio.create_task(service.close_all() if all_owners else service.close_owner('a'))
                    await closing.wait()
                    try:
                        self.assertFalse(close.done())
                        from harness.core.language import current_language
                        for language in ('en', 'zh-CN', 'en'):
                            with current_language.set(language), self.assertRaises(TerminalError) as caught:
                                await service.spawn(owner='a', kind='fixture', name='another')
                            self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in str(caught.exception)),
                                             language == 'zh-CN')
                    finally:
                        release.set()
                        await asyncio.gather(opening, close)
                self.assertEqual(service.list('a'), [])
                self.assertNotIn('a', service._reserved)
                process.stop_owned.assert_awaited_once()
                process._transport.close.assert_called_once()
                # 单个 owner 关闭不永久禁用服务，也不污染其他 owner。
                with patch('harness.tools.pty_process.spawn_pty', side_effect=lambda *a, **k: fake_process()):
                    await service.spawn(owner='b', kind='fixture', name='same')
                    self.assertEqual(len(service.list('b')), 1)
                    await service.close_all()

    async def test_repeated_cancel_and_concurrent_closes_keep_pending_process_owned(self):
        service = TerminalSessionService(backends={'fixture': ['fixture']})
        service.handoff_grace = 0
        entered, release, stopping, stopped = [asyncio.Event() for _ in range(4)]
        process = fake_process()

        async def create(*args, **kwargs):
            entered.set()
            await release.wait()
            return process

        async def stop():
            stopping.set()
            await stopped.wait()
            process.returncode = 0

        process.stop_owned.side_effect = stop
        with patch('harness.tools.pty_process.spawn_pty', side_effect=create):
            opening = asyncio.create_task(service.spawn(owner='a', kind='fixture', name='same'))
            await entered.wait()
            first = asyncio.create_task(service.close_owner('a'))
            other = asyncio.create_task(service.close_all())
            release.set()
            await stopping.wait()
            try:
                for _ in range(2):
                    first.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(first.done())
                    self.assertFalse(other.done())
                    process._transport.close.assert_not_called()
            finally:
                stopped.set()
                results = await asyncio.gather(opening, first, other, return_exceptions=True)
        self.assertIsInstance(results[1], asyncio.CancelledError)
        self.assertIsNone(results[2])
        self.assertEqual(service.list_all(), [])
        process.stop_owned.assert_awaited_once()
        process._transport.close.assert_called_once()

    async def test_failed_owner_does_not_skip_other_owners_and_can_be_retried(self):
        service = TerminalSessionService(backends={'fixture': ['fixture']})
        service.handoff_grace = 0
        failed, healthy = fake_process(), fake_process()
        failed.stop_owned.side_effect = OSError('injected process cleanup failure')
        with patch('harness.tools.pty_process.spawn_pty', side_effect=[failed, healthy]):
            await service.spawn(owner='a', kind='fixture', name='same')
            await service.spawn(owner='b', kind='fixture', name='same')
        try:
            with self.assertRaisesRegex(RuntimeError, 'injected process cleanup failure'):
                await service.close_all()
            self.assertEqual(len(service.list('a')), 1)
            self.assertEqual(service.list('b'), [])
            self.assertIn('same', service._reserved['a'])
            failed._transport.close.assert_not_called()
            healthy._transport.close.assert_called_once()
        finally:
            async def stop():
                failed.returncode = 0
            failed.stop_owned.side_effect = stop
            await service.close_all()
        self.assertEqual(service.list_all(), [])
