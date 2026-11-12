"""同步构造失败不应遗留尚未开始运行的资源。"""
import asyncio
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from harness import Harness, HarnessConfig
from tests.agent_configuration_fixture import RecordingTransport


class HarnessConstructionFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_failure_closes_initial_database_connections_without_starting_services(self):
        """在最后服务装配失败时核对真实数据库连接，无网络或用户数据。"""
        for language in ('en', 'zh-CN'):
            with self.subTest(language=language), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                connections = []
                class Connection(sqlite3.Connection):
                    def __init__(self, *args, **kwargs):
                        super().__init__(*args, **kwargs)
                        self.closed = False
                        connections.append(self)

                    def close(self):
                        try:
                            super().close()
                        finally:
                            self.closed = True

                connect = sqlite3.connect
                def tracked_connect(*args, **kwargs):
                    return connect(*args, **{**kwargs, 'factory': Connection})

                tasks = set(asyncio.all_tasks())
                threads = set(threading.enumerate())
                failure = RuntimeError('Original final assembly failure')
                transport = RecordingTransport()
                with patch('sqlite3.connect', side_effect=tracked_connect), \
                     patch('harness.plugins.service.PackageService', side_effect=failure), \
                     patch('subprocess.Popen', side_effect=AssertionError('Unexpected process start')), \
                     patch('socket.create_connection', side_effect=AssertionError('Unexpected network connection')):
                    with self.assertRaises(RuntimeError) as caught:
                        Harness(HarnessConfig(api_key='fixture', home=root/'state', cwd=root,
                            language=language, enable_automations=True, enable_project_memory=True, memory_model_cache=root/'model-cache'), transport=transport)
                self.assertIs(caught.exception, failure)
                self.assertTrue(connections)
                self.assertTrue(all(connection.closed for connection in connections))
                self.assertEqual(set(asyncio.all_tasks()), tasks)
                self.assertEqual(set(threading.enumerate()), threads)
                self.assertEqual(transport.requests, [])
