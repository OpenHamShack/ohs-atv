"""The smallest WebSocket (RFC 6455) the server needs, on http.server.

Why not a library: the store image is Debian's python3 with nothing from
pip (the shared libraries are unpacked wheels), Debian's python3-websockets
is asyncio-only and http.server is threads, and what is needed is small -
the handshake, unfragmented text and binary frames each way, ping, pong
and close. Fragmented messages from a client are refused (1003), which no
browser or OkHttp sends for messages this size.

Through Caddy the upgrade arrives as-is (the edition's websocket
transport, read_timeout 0: CONTROLLER-CONTRACT 5.3).
"""
from __future__ import annotations

import base64
import hashlib
import struct
import threading

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME = 1 << 20        # a client's frame: a second of TX audio is 24 kB


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def handshake(handler) -> bool:
    """Answer an upgrade on a BaseHTTPRequestHandler; False (and a 400)
    when it is not one."""
    h = handler.headers
    key = h.get("Sec-WebSocket-Key", "")
    if h.get("Upgrade", "").lower() != "websocket" or not key:
        handler.send_error(400, "a WebSocket upgrade is expected here")
        return False
    handler.send_response(101, "Switching Protocols")
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", accept_key(key))
    handler.end_headers()
    handler.wfile.flush()
    handler.close_connection = True
    return True


class Socket:
    def __init__(self, rfile, wfile):
        self.rfile, self.wfile = rfile, wfile
        self.lock = threading.Lock()
        self.closed = False

    def _send(self, opcode: int, payload: bytes) -> None:
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, n)
        elif n < 1 << 16:
            head = struct.pack("!BBH", 0x80 | opcode, 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 127, n)
        with self.lock:
            if self.closed:
                raise ConnectionError("closed")
            self.wfile.write(head + payload)
            self.wfile.flush()

    def send_text(self, text: str) -> None:
        self._send(0x1, text.encode("utf-8"))

    def send_binary(self, data: bytes) -> None:
        self._send(0x2, data)

    def close(self, code: int = 1000) -> None:
        try:
            self._send(0x8, struct.pack("!H", code))
        except (OSError, ConnectionError):
            pass
        self.closed = True

    def _read(self, n: int) -> bytes:
        b = self.rfile.read(n)
        if len(b) < n:
            raise ConnectionError("the client went away")
        return b

    def recv(self) -> tuple[int, bytes] | None:
        """(opcode, payload) of the next data frame, answering pings on the
        way; None when the client closed."""
        while True:
            b0, b1 = self._read(2)
            fin, op = b0 & 0x80, b0 & 0x0F
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._read(8))[0]
            if n > MAX_FRAME:
                self.close(1009)
                return None
            mask = self._read(4) if b1 & 0x80 else b""
            data = self._read(n)
            if mask:
                data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
            if op == 0x8:
                self.close()
                return None
            if op == 0x9:
                self._send(0xA, data)
                continue
            if op == 0xA:
                continue
            if not fin or op == 0x0:
                self.close(1003)
                return None
            return op, data
