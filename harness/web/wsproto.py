"""WebSocket 帧编解码（RFC 6455），只用标准库。

处理真实浏览器连接时遵守以下协议约束：

1. **分片消息** —— 一个 text 消息可拆成 ``text(fin=0) + cont(fin=1)``，完整组装后才交付。
2. **控制帧校验** —— 控制帧必须 ``fin=1`` 且载荷 <=125 字节，否则按协议关闭。
3. **载荷上限** —— 单条消息超过上限直接关闭（1009），防止一个畸形帧
   让服务端 ``recv`` 出一个 GB 级 buffer 把内存打爆。

解码器是**增量**的：调用方把任意切分的 TCP 字节喂进 :meth:`FrameDecoder.feed`，
它只在消息完整时吐出控制帧或完整消息，因此天然适配"边收边解析"。
"""

from __future__ import annotations

from harness.core.text_encoding import utf8_prefix

from harness.i18n import tr

import os
import struct
from dataclasses import dataclass
from typing import Iterable

#: 操作码
OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

#: 关闭码
CLOSE_NORMAL = 1000
CLOSE_GOING_AWAY = 1001
CLOSE_PROTOCOL_ERROR = 1002
CLOSE_UNSUPPORTED_DATA = 1003
CLOSE_INVALID_PAYLOAD = 1007
CLOSE_POLICY_VIOLATION = 1008
CLOSE_TOO_LARGE = 1009
CLOSE_INTERNAL_ERROR = 1011
CLOSE_TRY_AGAIN_LATER = 1013

#: 单条消息的最大载荷（4 MiB）。工具结果可能很大，但不该到 64 MiB。
DEFAULT_MAX_MESSAGE_BYTES = 4 * 1024 * 1024

_CONTROL_OPCODES = frozenset({OP_CLOSE, OP_PING, OP_PONG})
_DATA_OPCODES = frozenset({OP_TEXT, OP_BINARY, OP_CONT})

#: 握手 GUID（RFC 6455 §1.3）
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class ProtocolError(Exception):
    """帧格式违反协议：调用方应回 1002 并断开。"""

    def __init__(self, message: str, *, code: int = CLOSE_PROTOCOL_ERROR) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Frame:
    """一条完整的消息帧（已合并分片）或一个控制帧。"""

    opcode: int
    payload: bytes
    fin: bool = True

    @property
    def is_control(self) -> bool:
        return self.opcode in _CONTROL_OPCODES

    def text(self) -> str:
        try:
            return self.payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError(tr('encoding.protocol'), code=CLOSE_INVALID_PAYLOAD) from exc


def accept_key(client_key: str) -> str:
    """由客户端 key 计算握手响应头 ``Sec-WebSocket-Accept``。"""
    import base64
    import hashlib

    digest = hashlib.sha1((client_key + WS_GUID).encode()).digest()
    return base64.b64encode(digest).decode()


def encode_frame(opcode: int, payload: bytes = b"", *, fin: bool = True, mask: bytes | None = None) -> bytes:
    """编码一帧。服务端发帧不加掩码；``mask`` 仅供测试构造客户端帧。"""
    if opcode in _CONTROL_OPCODES:
        if len(payload) > 125:
            raise ProtocolError(tr('python.wsproto.0da3c8bf6ceb'), code=CLOSE_PROTOCOL_ERROR)
        if not fin:
            raise ProtocolError(tr('python.wsproto.5f268fcf920c'), code=CLOSE_PROTOCOL_ERROR)
    header = bytearray([(0x80 if fin else 0x00) | opcode])
    length = len(payload)
    mask_bit = 0x80 if mask is not None else 0x00
    if length < 126:
        header.append(mask_bit | length)
    elif length < 65536:
        header.append(mask_bit | 126)
        header.extend(struct.pack(">H", length))
    else:
        header.append(mask_bit | 127)
        header.extend(struct.pack(">Q", length))
    if mask is None:
        return bytes(header) + payload
    if len(mask) != 4:
        raise ValueError(tr('python.wsproto.eb41388cf9a1'))
    header.extend(mask)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return bytes(header) + masked


def encode_close(code: int = CLOSE_NORMAL, reason: str = "") -> bytes:
    """编码一条关闭帧（载荷是 2 字节状态码 + 可选原因）。"""
    raw = reason.encode("utf-8")[:123]
    raw = raw[:len(utf8_prefix(raw, truncated=True).encode("utf-8"))]
    payload = struct.pack(">H", code) + raw if code else raw
    return encode_frame(OP_CLOSE, payload)


def decode_close_payload(payload: bytes) -> tuple[int | None, str]:
    """从关闭帧载荷解出 ``(状态码, 原因)``。"""
    if not payload:
        return None, ""
    if len(payload) < 2:
        raise ProtocolError(tr('python.wsproto.c42a06261ed6'), code=CLOSE_PROTOCOL_ERROR)
    (code,) = struct.unpack(">H", payload[:2])
    try:
        return code, payload[2:].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError(tr('encoding.protocol'), code=CLOSE_INVALID_PAYLOAD) from exc


class FrameDecoder:
    """增量帧解码器。

    一次 :meth:`feed` 可以喂进任意字节（TCP 粘包/拆包都无所谓）。
    返回值里，控制帧立刻出现；数据消息只在 ``FIN`` 到齐后出现一次，
    且已把分片拼好。
    """

    __slots__ = ("_buffer", "_fragments", "_fragment_opcode", "_max_message_bytes")

    def __init__(self, *, max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES) -> None:
        self._buffer = bytearray()
        self._fragments: list[bytes] = []
        self._fragment_opcode: int | None = None
        self._max_message_bytes = max_message_bytes

    def feed(self, data: bytes) -> list[Frame]:
        """喂入字节，返回本次新得到的完整帧列表。"""
        if data:
            self._buffer.extend(data)
        out: list[Frame] = []
        # 一次循环可能消费掉多帧；每轮先确保缓冲区里有完整的帧头。
        while True:
            frame = self._next_frame()
            if frame is None:
                return out
            if frame.is_control:
                out.append(frame)
                continue
            if frame.opcode == OP_CONT:
                if self._fragment_opcode is None:
                    raise ProtocolError(tr('python.wsproto.da99a6fd6510'), code=CLOSE_PROTOCOL_ERROR)
                self._fragments.append(frame.payload)
            else:
                if self._fragment_opcode is not None:
                    raise ProtocolError(tr('python.wsproto.84a5271c037e'), code=CLOSE_PROTOCOL_ERROR)
                self._fragment_opcode = frame.opcode
                self._fragments = [frame.payload]
            if not frame.fin:
                self._check_size()
                continue
            payload = b"".join(self._fragments)
            opcode = self._fragment_opcode if self._fragment_opcode is not None else frame.opcode
            self._fragments = []
            self._fragment_opcode = None
            self._check_size(payload)
            out.append(Frame(opcode=opcode, payload=payload, fin=True))

    # -- 内部 ------------------------------------------------------------
    def _check_size(self, payload: bytes | None = None) -> None:
        total = sum(len(chunk) for chunk in self._fragments) if payload is None else len(payload)
        if total > self._max_message_bytes:
            raise ProtocolError(
                tr('python.wsproto.daef8aa5ef95', p0=f'{self._max_message_bytes}'), code=CLOSE_TOO_LARGE
            )

    def _next_frame(self) -> Frame | None:
        buffer = self._buffer
        if len(buffer) < 2:
            return None
        first, second = buffer[0], buffer[1]
        fin = bool(first & 0x80)
        if first & 0x70:
            raise ProtocolError(tr('python.wsproto.c5a9a10f8027'), code=CLOSE_PROTOCOL_ERROR)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        if not masked:
            raise ProtocolError(tr('python.audit.client_mask'), code=CLOSE_PROTOCOL_ERROR)
        length = second & 0x7F
        offset = 2
        if length == 126:
            if len(buffer) < offset + 2:
                return None
            (length,) = struct.unpack(">H", buffer[offset : offset + 2])
            offset += 2
        elif length == 127:
            if len(buffer) < offset + 8:
                return None
            (length,) = struct.unpack(">Q", buffer[offset : offset + 8])
            offset += 8
        if opcode not in _CONTROL_OPCODES and opcode not in _DATA_OPCODES:
            raise ProtocolError(tr('python.wsproto.55135bed7692', p0=f'{opcode:x}'), code=CLOSE_PROTOCOL_ERROR)
        if opcode in _CONTROL_OPCODES:
            if not fin:
                raise ProtocolError(tr('python.wsproto.5f268fcf920c'), code=CLOSE_PROTOCOL_ERROR)
            if length > 125:
                raise ProtocolError(tr('python.wsproto.0da3c8bf6ceb'), code=CLOSE_PROTOCOL_ERROR)
        if length > self._max_message_bytes:
            raise ProtocolError(
                tr('python.wsproto.38cc764dbf52', p0=f'{length}', p1=f'{self._max_message_bytes}'), code=CLOSE_TOO_LARGE
            )
        mask = b""
        if masked:
            if len(buffer) < offset + 4:
                return None
            mask = bytes(buffer[offset : offset + 4])
            offset += 4
        end = offset + length
        if len(buffer) < end:
            return None
        payload = bytes(buffer[offset:end])
        del buffer[:end]
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return Frame(opcode=opcode, payload=payload, fin=fin)


def encode_many(frames: Iterable[Frame]) -> bytes:
    """把多帧拼成一个字节串（测试与批量写用）。"""
    return b"".join(encode_frame(frame.opcode, frame.payload, fin=frame.fin) for frame in frames)


def random_mask() -> bytes:
    """随机 4 字节掩码（构造客户端帧时用）。"""
    return os.urandom(4)


__all__ = [
    "OP_CONT",
    "OP_TEXT",
    "OP_BINARY",
    "OP_CLOSE",
    "OP_PING",
    "OP_PONG",
    "CLOSE_NORMAL",
    "CLOSE_GOING_AWAY",
    "CLOSE_PROTOCOL_ERROR",
    "CLOSE_UNSUPPORTED_DATA",
    "CLOSE_INVALID_PAYLOAD",
    "CLOSE_POLICY_VIOLATION",
    "CLOSE_TOO_LARGE",
    "CLOSE_INTERNAL_ERROR",
    "CLOSE_TRY_AGAIN_LATER",
    "DEFAULT_MAX_MESSAGE_BYTES",
    "WS_GUID",
    "ProtocolError",
    "Frame",
    "FrameDecoder",
    "accept_key",
    "encode_frame",
    "encode_close",
    "encode_many",
    "decode_close_payload",
    "random_mask",
]
