"""联网读取拒绝本地和组播地址，保留公网及合法隧道地址。"""
import ipaddress
import socket
import unittest
from unittest.mock import Mock, patch

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.web_tools import _is_blocked_address, _check_url, _http_get
from harness.tools.builtin.image_tools import read_image_url


def teredo(server, client):
    server = int(ipaddress.IPv4Address(server))
    client = int(ipaddress.IPv4Address(client)) ^ 0xffffffff
    return ipaddress.IPv6Address(f'2001:0:{server >> 16:x}:{server & 65535:x}:8000:63bf:{client >> 16:x}:{client & 65535:x}')


class WebAddressBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def test_local_multicast_and_embedded_addresses_are_classified(self):
        blocked = ['127.0.0.1', '10.0.0.1', '100.64.0.1', '224.0.0.1', '239.255.255.250',
                   '::', '::1', 'fe80::1', 'fc00::1', 'fd00::1', 'fec0::1', 'ff02::1', 'ff0e::1',
                   '2001:db8::1', '::ffff:10.0.0.1', '2002:0a00:1::']
        for address in blocked:
            with self.subTest(address=address):
                self.assertTrue(_is_blocked_address(ipaddress.ip_address(address)))
        self.assertTrue(_is_blocked_address(teredo('10.0.0.1', '8.8.8.8')))
        self.assertTrue(_is_blocked_address(teredo('65.54.227.120', '10.0.0.1')))
        allowed = ['8.8.8.8', '1.1.1.1', '2606:4700:4700::1111', '2001:4860:4860::8888',
                   '::ffff:8.8.8.8', '2002:0808:0808::']
        for address in allowed:
            with self.subTest(address=address):
                self.assertFalse(_is_blocked_address(ipaddress.ip_address(address)))
        self.assertFalse(_is_blocked_address(teredo('65.54.227.120', '8.8.8.8')))

    async def test_dns_refusal_is_bilingual_and_both_readers_stop_before_http(self):
        host = 'fixture.example.test'
        url = 'https://' + host + '/Original.png'
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for address in ('fd00::1', 'ff02::1', 'fec0::1'):
                    addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443)),
                                 (socket.AF_INET6, socket.SOCK_STREAM, 6, '', (address, 443, 0, 0))]
                    with patch('harness.tools.builtin.web_tools.socket.getaddrinfo', return_value=addresses), \
                         patch('harness.tools.builtin.web_tools.ipv4_preferred_get', Mock()) as web_get, \
                         patch('harness.tools.builtin.image_tools.ipv4_preferred_get', Mock()) as image_get:
                        with self.assertRaises(ToolExecutionError) as error:
                            _check_url(url)
                        self.assertEqual(error.exception.message, tr('python.web_tools.b8e05a0e2f2b', p0=host, p1=address))
                        with self.assertRaises(ToolExecutionError):
                            await _http_get(url, timeout=1, max_bytes=100, accept='*/*')
                        with self.assertRaises(ToolExecutionError):
                            await read_image_url(url)
                        web_get.assert_not_called()
                        image_get.assert_not_called()
