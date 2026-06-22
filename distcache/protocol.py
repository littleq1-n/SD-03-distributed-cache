"""RESP 风格协议:双模式请求(inline + RESP array)+ RESP 响应 + 显式分帧。

见 design.md Decision 4:
- 请求按首字节区分:`*` 开头按 RESP array;否则按 inline(空白切分,供 nc 调试)。
- 响应统一 RESP:`+OK`、`-ERR`、`$<len>` bulk、`$-1` 空值。
- 解析器维护读缓冲,数据不足时返回 None(等待更多字节),解决半包/粘包。
"""

from typing import List, Optional

CRLF = b"\r\n"


class ProtocolError(Exception):
    pass


# ---- 响应编码 ----
def encode_simple(s) -> bytes:
    if isinstance(s, str):
        s = s.encode()
    return b"+" + s + CRLF


def encode_error(msg) -> bytes:
    if isinstance(msg, str):
        msg = msg.encode()
    return b"-" + msg + CRLF


def encode_bulk(value: Optional[bytes]) -> bytes:
    if value is None:
        return b"$-1" + CRLF
    if isinstance(value, str):
        value = value.encode()
    return b"$" + str(len(value)).encode() + CRLF + value + CRLF


# ---- 请求编码(客户端用):RESP array ----
def encode_command(*args) -> bytes:
    out = [b"*" + str(len(args)).encode() + CRLF]
    for a in args:
        if isinstance(a, str):
            a = a.encode()
        elif isinstance(a, (int, float)):
            a = str(a).encode()
        out.append(b"$" + str(len(a)).encode() + CRLF + bytes(a) + CRLF)
    return b"".join(out)


class Parser:
    """增量请求解析器。parse_one 返回一条命令(list[bytes])或 None(需更多字节)。
    仅在成功解析出完整消息时才消费缓冲区。"""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes) -> None:
        self.buf.extend(data)

    def _find_crlf(self, start: int) -> int:
        return self.buf.find(b"\r\n", start)

    def parse_one(self) -> Optional[List[bytes]]:
        if not self.buf:
            return None
        if self.buf[0:1] == b"*":
            return self._parse_resp()
        return self._parse_inline()

    def _parse_inline(self) -> Optional[List[bytes]]:
        # 同时接受 \n 与 \r\n,方便 nc/telnet
        idx = self.buf.find(b"\n")
        if idx == -1:
            return None
        line = self.buf[:idx]
        consume = idx + 1
        if line.endswith(b"\r"):
            line = line[:-1]
        del self.buf[:consume]
        return [bytes(p) for p in line.split()]

    def _parse_resp(self) -> Optional[List[bytes]]:
        idx = self._find_crlf(0)
        if idx == -1:
            return None
        try:
            n = int(self.buf[1:idx])
        except ValueError:
            raise ProtocolError("invalid multibulk length")
        if n < 0:
            del self.buf[: idx + 2]
            return []
        pos = idx + 2
        args: List[bytes] = []
        for _ in range(n):
            if pos >= len(self.buf):
                return None  # 还没收到下一个参数,等待
            if self.buf[pos:pos + 1] != b"$":
                raise ProtocolError("expected bulk string")
            lidx = self._find_crlf(pos)
            if lidx == -1:
                return None
            try:
                blen = int(self.buf[pos + 1:lidx])
            except ValueError:
                raise ProtocolError("invalid bulk length")
            start = lidx + 2
            end = start + blen
            if len(self.buf) < end + 2:  # 不足 value + CRLF,等待
                return None
            args.append(bytes(self.buf[start:end]))
            pos = end + 2
        del self.buf[:pos]
        return args


def decode_resp_array(payload: bytes) -> List[bytes]:
    """一次性解码一个完整的 RESP array 负载(用于复制流的 effect 帧)。"""
    p = Parser()
    p.feed(payload)
    args = p.parse_one()
    if args is None:
        raise ProtocolError("incomplete RESP array payload")
    return args
