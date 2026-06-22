"""端到端演示:分片 + 主从复制 + 手动故障切换。

运行: python3 demo.py
"""

import asyncio

from distcache import protocol
from distcache.client import DistributedCacheClient
from distcache.server import Node


async def _send(host, port, *args):
    reader, writer = await asyncio.open_connection(host, port)
    writer.write(protocol.encode_command(*args))
    await writer.drain()
    line = await reader.readline()
    tag, body = line[0:1], line[1:].rstrip(b"\r\n")
    out = body
    if tag == b"$":
        n = int(body)
        if n == -1:
            out = None
        else:
            out = await reader.readexactly(n)
            await reader.readexactly(2)
    writer.close()
    return out


async def _wait(pred, timeout=3.0):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        await asyncio.sleep(0.02)
    return pred()


def line(title):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


async def demo_sharding():
    line("1) 一致性哈希分片:客户端把不同 key 路由到不同节点")
    nodes = [Node(port=0) for _ in range(3)]
    for n in nodes:
        await n.start()
    addrs = [(n.host, n.port) for n in nodes]
    by_addr = {"%s:%d" % (n.host, n.port): n for n in nodes}

    def ops():
        client = DistributedCacheClient(addrs, vnodes=100)
        try:
            for i in range(12):
                client.set("user:%d" % i, "data-%d" % i)
            placement = {}
            for i in range(12):
                node = client.route("user:%d" % i)
                placement.setdefault(node, []).append(i)
            return placement
        finally:
            client.close()

    placement = await asyncio.to_thread(ops)
    for node, keys in placement.items():
        print("  节点 %s 承载 %d 个 key: %s" % (node, len(keys),
              ", ".join("user:%d" % k for k in keys)))
    print("  -> 每个节点本地各自存了一部分,合起来才是全量")
    for n in nodes:
        await n.stop()


async def demo_replication_and_failover():
    line("2) 主从复制 + 淘汰传播 + 手动故障切换")
    master = Node(port=0, maxsize=3)
    await master.start()
    slave = Node(port=0, role="slave", maxsize=10)
    slave.follow(master.host, master.port)
    await slave.start()
    await _wait(lambda: slave.replica_client.synced_event.is_set())
    print("  master=%d  slave=%d  (slave 已完成全量同步)" % (master.port, slave.port))

    print("\n  [写主] SET a/b/c …")
    for k in (b"a", b"b", b"c"):
        await _send(master.host, master.port, b"SET", k, b"v-" + k)
    await _wait(lambda: slave.store.get(b"c", now=0) == b"v-c")
    print("  slave 现有 keys:", sorted(slave.store.keys()))

    print("\n  [触发淘汰] master maxsize=3,SET d → 淘汰最久未用的 a,并以 DEL 传播")
    await _send(master.host, master.port, b"SET", b"d", b"v-d")
    await _wait(lambda: slave.store.get(b"a", now=0) is None)
    print("  master keys:", sorted(master.store.keys()))
    print("  slave  keys:", sorted(slave.store.keys()), "(a 已被 DEL 同步删除)")
    print("  master.offset=%d  slave.applied_offset=%d (一致)" % (
        master.replicator.offset, slave.replica_client.applied_offset))

    print("\n  [从拒绝写] 向 slave 发 SET 应返回 READONLY:")
    rep = await _send(slave.host, slave.port, b"SET", b"x", b"1")
    print("   ", rep.decode(errors="replace"))

    print("\n  [手动故障切换] 模拟 master 宕机,提升 slave 为新 master")
    await master.stop()
    slave.promote()
    await asyncio.sleep(0.1)
    rep = await _send(slave.host, slave.port, b"SET", b"x", b"1")
    print("    提升后向新 master 写 SET x 1 ->", rep.decode(errors="replace"))
    print("    GET x ->", (await _send(slave.host, slave.port, b"GET", b"x")))
    await slave.stop()


async def main():
    print("distcache 端到端演示 (mini Redis/Memcached Cluster)")
    await demo_sharding()
    await demo_replication_and_failover()
    print("\n演示结束。")


if __name__ == "__main__":
    asyncio.run(main())
