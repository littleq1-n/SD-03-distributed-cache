import asyncio

from distcache import protocol
from distcache.server import Node
from tests.helpers import cmd, read_reply


def run(coro):
    return asyncio.run(coro)


def test_set_then_get():
    async def main():
        node = Node(port=0)
        await node.start()
        try:
            assert await cmd(node.host, node.port, b"SET", b"foo", b"bar") == b"OK"
            assert await cmd(node.host, node.port, b"GET", b"foo") == b"bar"
            assert await cmd(node.host, node.port, b"GET", b"missing") is None
        finally:
            await node.stop()

    run(main())


def test_unknown_command_keeps_connection():
    async def main():
        node = Node(port=0)
        await node.start()
        try:
            reader, writer = await asyncio.open_connection(node.host, node.port)
            writer.write(protocol.encode_command(b"BOGUS", b"x"))
            await writer.drain()
            rep = await read_reply(reader)
            assert isinstance(rep, tuple) and rep[0] == "ERR"
            # 连接仍可用
            writer.write(protocol.encode_command(b"PING"))
            await writer.drain()
            assert await read_reply(reader) == b"PONG"
            writer.close()
        finally:
            await node.stop()

    run(main())


def test_del():
    async def main():
        node = Node(port=0)
        await node.start()
        try:
            await cmd(node.host, node.port, b"SET", b"k", b"v")
            assert await cmd(node.host, node.port, b"DEL", b"k") == b"OK"
            assert await cmd(node.host, node.port, b"GET", b"k") is None
        finally:
            await node.stop()

    run(main())


def test_pipelined_on_one_connection():
    async def main():
        node = Node(port=0)
        await node.start()
        try:
            reader, writer = await asyncio.open_connection(node.host, node.port)
            # 一次性发送两条命令(粘包)
            writer.write(protocol.encode_command(b"SET", b"a", b"1")
                         + protocol.encode_command(b"GET", b"a"))
            await writer.drain()
            assert await read_reply(reader) == b"OK"
            assert await read_reply(reader) == b"1"
            writer.close()
        finally:
            await node.stop()

    run(main())


def test_concurrent_connections():
    async def main():
        node = Node(port=0)
        await node.start()
        try:
            await cmd(node.host, node.port, b"SET", b"shared", b"v")

            async def reader_task(i):
                return await cmd(node.host, node.port, b"GET", b"shared")

            results = await asyncio.gather(*[reader_task(i) for i in range(20)])
            assert all(r == b"v" for r in results)
        finally:
            await node.stop()

    run(main())


def test_replica_rejects_writes():
    async def main():
        node = Node(port=0, role="slave")
        await node.start()
        try:
            rep = await cmd(node.host, node.port, b"SET", b"k", b"v")
            assert isinstance(rep, tuple) and rep[0] == "ERR" and b"READONLY" in rep[1]
        finally:
            await node.stop()

    run(main())
