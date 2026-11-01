"""有界图片下载与内容校验，禁止访问内网或保存临时凭据。"""
from __future__ import annotations

from ..i18n import tr

import http.client
import io
import ipaddress
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit
import warnings

from PIL import Image, UnidentifiedImageError

from .policy import fail


class _PinnedHTTPS(http.client.HTTPSConnection):
    """连接已检查的地址，TLS 仍验证原始主机名，避免 DNS 重绑定。"""

    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def download_image(url: str, max_bytes: int) -> bytes:
    """仅从公网 HTTPS 下载；每次重定向重新检查，大小和总时间均有界。"""
    deadline = time.monotonic() + 30
    try:
        for _ in range(4):
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                    or parsed.password is not None or parsed.port not in (None, 443) or parsed.fragment):
                fail("execution/image-url-denied", tr('execution.images.1bce4195716b'))
            addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
            if not addresses or any((not ipaddress.ip_address(row[4][0]).is_global or ipaddress.ip_address(row[4][0]).is_multicast) for row in addresses):
                fail("execution/image-url-denied", tr('execution.images.857d67ca3a0a'))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                fail("execution/image-download-failed", tr('execution.images.f487a3c3cda6'))
            connection = _PinnedHTTPS(parsed.hostname, addresses[0][4][0], min(remaining, 10))
            try:
                path = parsed.path or "/"
                if parsed.query:
                    path += "?" + parsed.query
                connection.request("GET", path, headers={"Accept": "image/png,image/jpeg,image/webp"})
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location:
                        fail("execution/image-download-failed", tr('execution.images.cd836ba4a2de'))
                    url = urljoin(url, location)
                    continue
                if response.status != 200:
                    fail("execution/image-download-failed", tr('execution.images.9f6c6d59f388'))
                length = response.getheader("Content-Length")
                if length is not None and (int(length) < 0 or int(length) > max_bytes):
                    fail("execution/file-too-large", tr('execution.images.c1f234089782'))
                content = bytearray()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        fail("execution/image-download-failed", tr('execution.images.f487a3c3cda6'))
                    if connection.sock:
                        connection.sock.settimeout(min(remaining, 10))
                    chunk = response.read1(min(65536, max_bytes + 1 - len(content)))
                    if not chunk:
                        return bytes(content)
                    content.extend(chunk)
                    if len(content) > max_bytes:
                        fail("execution/file-too-large", tr('execution.images.c1f234089782'))
            finally:
                connection.close()
        fail("execution/image-download-failed", tr('execution.images.7ccf1a3d2008'))
    except (OSError, ValueError, http.client.HTTPException):
        # 不暴露临时下载地址、签名参数或远端返回内容。
        fail("execution/image-download-failed", tr('execution.images.10e9c418f891'))


def inspect_image(content: bytes, suffix: str, declared_mime: str | None = None):
    """实际解码图片，拒绝伪装文件、格式不符与超大像素图片。"""
    formats = {"PNG": ("image/png", {".png"}), "JPEG": ("image/jpeg", {".jpg", ".jpeg"}),
               "WEBP": ("image/webp", {".webp"})}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.format not in formats:
                    fail("execution/image-format", tr('execution.images.3e00d7bfc0dc'))
                mime, extensions = formats[image.format]
                if suffix.lower() not in extensions or (declared_mime and declared_mime != mime):
                    fail("execution/image-format", tr('execution.images.801b37206b4a'))
                width, height = image.size
                if width * height > 40_000_000 or getattr(image, "n_frames", 1) != 1:
                    fail("execution/image-format", tr('execution.images.6f2562fd97df'))
                image.verify()
            with Image.open(io.BytesIO(content)) as image:
                image.load()
        return {"mime_type": mime, "width": width, "height": height}
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        fail("execution/image-format", tr('execution.images.84931bcfc89d'))
