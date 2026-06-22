import asyncio
import time as _time

from distcache import protocol


async def read_reply(reader):
    """读取一条 RESP 回复。错误返回 ('ERR', body)。"""
    line = await reader.readline()
    if not line:
        raise ConnectionError("closed")
    tag = line[0:1]
    body = line[1:].rstrip(b"\r\n")
    if tag == b"+":
        return body
    if tag == b"-":
        return ("ERR", body)
    if tag == b"$":
        n = int(body)
        if n == -1:
            return None
        data = await reader.readexactly(n)
        await reader.readexactly(2)
        return data
    raise AssertionError("bad reply %r" % line)


async def cmd(host, port, *args):
    """一发一收(每次新建连接)。"""
    reader, writer = await asyncio.open_connection(host, port)
    writer.write(protocol.encode_command(*args))
    await writer.drain()
    rep = await read_reply(reader)
    writer.close()
    return rep


async def wait_until(pred, timeout=3.0, interval=0.02):
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(interval)
    return pred()
