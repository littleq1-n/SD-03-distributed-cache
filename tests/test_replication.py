import asyncio

from distcache.server import Node
from tests.helpers import cmd, wait_until


def run(coro):
    return asyncio.run(coro)


async def _make_master_slave(master_maxsize=1024, slave_maxsize=1024):
    master = Node(port=0, maxsize=master_maxsize)
    await master.start()
    slave = Node(port=0, role="slave", maxsize=slave_maxsize)
    slave.follow(master.host, master.port)
    await slave.start()
    # 等待初次全量同步完成
    await wait_until(lambda: slave.replica_client.synced_event.is_set())
    return master, slave


def test_basic_replication():
    async def main():
        master, slave = await _make_master_slave()
        try:
            await cmd(master.host, master.port, b"SET", b"foo", b"bar")
            ok = await wait_until(lambda: slave.store.get(b"foo", now=0) == b"bar")
            assert ok
            # offset 推进且最终一致
            assert slave.replica_client.applied_offset == master.replicator.offset
        finally:
            await slave.stop()
            await master.stop()

    run(main())


def test_eviction_propagates_as_del():
    async def main():
        # master 容量 2;slave 容量更大,验证 slave 仅按 DEL 删除
        master, slave = await _make_master_slave(master_maxsize=2, slave_maxsize=10)
        try:
            await cmd(master.host, master.port, b"SET", b"a", b"1")
            await cmd(master.host, master.port, b"SET", b"b", b"2")
            await cmd(master.host, master.port, b"SET", b"c", b"3")  # 淘汰 a
            # master 已无 a
            assert master.store.get(b"a", now=0) is None
            # slave 最终也无 a(通过 DEL),但有 b、c
            ok = await wait_until(
                lambda: slave.store.get(b"a", now=0) is None
                and slave.store.get(b"b", now=0) == b"2"
                and slave.store.get(b"c", now=0) == b"3"
            )
            assert ok
        finally:
            await slave.stop()
            await master.stop()

    run(main())


def test_expire_propagates_absolute():
    async def main():
        master, slave = await _make_master_slave()
        try:
            await cmd(master.host, master.port, b"SET", b"k", b"v")
            await cmd(master.host, master.port, b"EXPIRE", b"k", b"1000")
            # 远期过期:slave 应已收到 PEXPIREAT 且当前仍可读
            ok = await wait_until(
                lambda: slave.store.get(b"k", now=0) == b"v"
                and slave.replica_client.applied_offset == master.replicator.offset
            )
            assert ok
            # slave 上该 key 的绝对过期时间已设置(逻辑过期会在远期生效)
            assert slave.store.get(b"k", now=10**12) is None
        finally:
            await slave.stop()
            await master.stop()

    run(main())


def test_fresh_slave_full_sync_of_existing_data():
    async def main():
        master = Node(port=0)
        await master.start()
        try:
            # master 先有数据,slave 后连接 → 全量同步应拿到既有数据
            for i in range(50):
                await cmd(master.host, master.port, b"SET",
                          ("k%d" % i).encode(), ("v%d" % i).encode())
            slave = Node(port=0, role="slave", maxsize=1000)
            slave.follow(master.host, master.port)
            await slave.start()
            try:
                ok = await wait_until(lambda: len(slave.store) == 50)
                assert ok, len(slave.store)
                assert slave.store.get(b"k0", now=0) == b"v0"
                assert slave.store.get(b"k49", now=0) == b"v49"
                assert slave.replica_client.applied_offset == master.replicator.offset
            finally:
                await slave.stop()
        finally:
            await master.stop()

    run(main())


def test_stream_after_snapshot():
    async def main():
        master, slave = await _make_master_slave()
        try:
            # 同步后继续写,验证增量流持续工作
            for i in range(30):
                await cmd(master.host, master.port, b"SET",
                          ("s%d" % i).encode(), b"x")
            ok = await wait_until(lambda: len(slave.store) >= 30)
            assert ok
            assert slave.replica_client.applied_offset == master.replicator.offset
        finally:
            await slave.stop()
            await master.stop()

    run(main())
