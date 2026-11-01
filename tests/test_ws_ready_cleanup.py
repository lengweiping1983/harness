"""首帧初始化失败仍须在所属循环结束连接和订阅。"""
import asyncio
import socket
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.web.hub import ConnectionHub
from harness.web.server import Handler, WebSocketConnection


class WsReadyCleanupTests(unittest.TestCase):
    def test_initialization_failures_and_normal_disconnect_close_owned_connection(self):
        for stage in ('snapshot', 'queue', 'send', 'normal'):
            with self.subTest(stage=stage):
                loop = asyncio.new_event_loop()
                started = threading.Event()
                loop.call_soon(started.set)
                thread = threading.Thread(target=loop.run_forever)
                thread.start()
                server_sock, peer = socket.socketpair()
                hub = ConnectionHub()
                unsubscribe_threads = []
                unsubscribe = hub.unsubscribe
                def owned_unsubscribe(*args):
                    unsubscribe_threads.append(threading.get_ident())
                    return unsubscribe(*args)
                hub.unsubscribe = owned_unsubscribe
                connections = []
                failure = OSError('Original ' + stage)
                def connect(sock):
                    conn = WebSocketConnection(sock)
                    if stage == 'send':
                        conn.send_json = AsyncMock(side_effect=failure)
                    connections.append(conn)
                    return conn
                runtime = SimpleNamespace(queue=Mock(side_effect=failure if stage == 'queue' else None, return_value=[]),
                    queue_control=Mock(return_value={}), active={})
                handler = SimpleNamespace(headers={'Sec-WebSocket-Key':'dGhlIHNhbXBsZSBub25jZQ=='},
                    send_response=Mock(), send_header=Mock(), end_headers=Mock(), connection=server_sock,
                    ctx=SimpleNamespace(shared_access=None, hub=hub), harness=SimpleNamespace(runtime=runtime),
                    ws_clients=set(), loop=loop)
                try:
                    self.assertTrue(started.wait(2))
                    if stage == 'normal':
                        peer.shutdown(socket.SHUT_WR)
                    with patch('harness.web.server.WebSocketConnection', side_effect=connect), \
                            patch('harness.web.views.session_detail', side_effect=failure if stage == 'snapshot' else None, return_value={}):
                        if stage == 'normal':
                            Handler.do_GET_ws(handler, 'fixture')
                        else:
                            with self.assertRaises(OSError) as caught:
                                Handler.do_GET_ws(handler, 'fixture')
                            self.assertIs(caught.exception, failure)
                    self.assertEqual(len(connections), 1)
                    self.assertTrue(connections[0].closed)
                    self.assertFalse(connections[0]._socket_tasks)
                    self.assertFalse(handler.ws_clients)
                    self.assertEqual(hub.observer_count(), 0)
                    self.assertEqual(unsubscribe_threads, [thread.ident])
                    # HTTP调用方负责释放描述符；关闭前已确认所有原生I/O任务结束。
                    server_sock.close()
                    peer.settimeout(2)
                    while peer.recv(65536):
                        pass
                finally:
                    server_sock.close()
                    peer.close()
                    loop.call_soon_threadsafe(loop.stop)
                    thread.join(2)
                    if not thread.is_alive():
                        loop.run_until_complete(loop.shutdown_asyncgens())
                        loop.close()
