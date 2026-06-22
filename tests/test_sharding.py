import asyncio

import pytest

from distcache.server import Node
from distcache.client import DistributedCacheClient, CacheError


def run(coro):
    return asyncio.run(coro)


def test_client_with_no_nodes_raises():
    client = DistributedCacheClient([], vnodes=10)
    with pytest.raises(CacheError):
        client.set("k", "v")


def test_client_routes_and_roundtrips():
    async def main():
        nodes = [Node(port=0) for _ in range(3)]
        for n in nodes:
            await n.start()
        addrs = [(n.host, n.port) for n in nodes]
        try:
            def client_ops():
                client = DistributedCacheClient(addrs, vnodes=100)
                try:
                    for i in range(100):
                        assert client.set("key-%d" % i, "val-%d" % i) == b"OK"
                    for i in range(100):
                        assert client.get("key-%d" % i) == ("val-%d" % i).encode()
                    # 路由一致性:同一 key 总落同一节点
                    assert client.route("key-7") == client.route("key-7")
                    return True
                finally:
                    client.close()

            assert await asyncio.to_thread(client_ops)
        finally:
            for n in nodes:
                await n.stop()

    run(main())


def test_key_lands_on_routed_node():
    async def main():
        nodes = [Node(port=0) for _ in range(3)]
        for n in nodes:
            await n.start()
        addrs = [(n.host, n.port) for n in nodes]
        by_addr = {"%s:%d" % (n.host, n.port): n for n in nodes}
        try:
            def client_ops():
                client = DistributedCacheClient(addrs, vnodes=100)
                try:
                    client.set("alpha", "beta")
                    return client.route("alpha")
                finally:
                    client.close()

            routed = await asyncio.to_thread(client_ops)
            # 数据应当真的落在被路由到的那个节点的本地存储里
            assert by_addr[routed].store.get(b"alpha", now=0) == b"beta"
            # 其它节点没有该 key
            others = [n for k, n in by_addr.items() if k != routed]
            assert all(o.store.get(b"alpha", now=0) is None for o in others)
        finally:
            for n in nodes:
                await n.stop()

    run(main())
