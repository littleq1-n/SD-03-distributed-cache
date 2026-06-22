"""基于 asyncio 的缓存节点(见 design.md Decision 1/5/8)。

一个 Node:
- 监听 TCP,每连接一协程,接受普通客户端连接;
- 同一端口也接受 slave 的 `SYNC` 握手(劫持该连接转入复制推送);
- role=master 接受写并把 effect batch 推给 slave;role=slave 拒绝客户端写(READONLY)
  并通过 ReplicaClient 跟随 master;
- promote() 实现手动故障切换(slave 升 master)。
"""

import asyncio
import time

from . import protocol
from .lru import LRUCache
from .replication import MasterReplicator, ReplicaClient


def _b(x) -> bytes:
    if isinstance(x, bytes):
        return x
    if isinstance(x, str):
        return x.encode()
    return str(x).encode()


class Node:
    def __init__(self, host: str = "127.0.0.1", port: int = 7001,
                 maxsize: int = 1024, role: str = "master",
                 time_func=time.time, expire_interval: float = 1.0):
        self.host = host
        self.port = port
        self.time = time_func
        self.expire_interval = expire_interval
        self.role = role
        mode = LRUCache.AUTHORITATIVE if role == "master" else LRUCache.LOGICAL
        self.store = LRUCache(maxsize=maxsize, mode=mode, time_func=time_func)
        self.replicator = MasterReplicator(self.store)
        self.replica_client = None
        self._server = None
        self._expire_task = None

    @property
    def is_master(self) -> bool:
        return self.role == "master"

    def follow(self, master_host: str, master_port: int) -> None:
        """声明本节点跟随某 master(在 start 时启动复制)。"""
        self.replica_client = ReplicaClient(master_host, master_port, self.store)

    async def start(self):
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port)
        # port=0 时取实际绑定端口
        self.port = self._server.sockets[0].getsockname()[1]
        if self.is_master:
            self._expire_task = asyncio.ensure_future(self._active_expire_loop())
        if self.replica_client is not None:
            self.replica_client.start()
        return self._server

    async def stop(self):
        if self._expire_task is not None:
            self._expire_task.cancel()
            try:
                await self._expire_task
            except asyncio.CancelledError:
                pass
            self._expire_task = None
        if self.replica_client is not None:
            await self.replica_client.stop()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    def promote(self) -> None:
        """手动故障切换:把本 slave 提升为 master。"""
        self.role = "master"
        self.store.mode = LRUCache.AUTHORITATIVE
        if self.replica_client is not None:
            asyncio.ensure_future(self.replica_client.stop())
            self.replica_client = None
        if self._expire_task is None:
            self._expire_task = asyncio.ensure_future(self._active_expire_loop())

    async def _active_expire_loop(self):
        """authoritative 主动采样过期清理;删除作为 DEL 进入复制流。"""
        try:
            while True:
                await asyncio.sleep(self.expire_interval)
                removed = self.store.sample_expired(now=self.time())
                for k in removed:
                    self.replicator.append_batch([("DEL", _b(k))])
        except asyncio.CancelledError:
            pass

    async def _handle_client(self, reader, writer):
        parser = protocol.Parser()
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                parser.feed(data)
                while True:
                    try:
                        args = parser.parse_one()
                    except protocol.ProtocolError as e:
                        writer.write(protocol.encode_error("ERR %s" % e))
                        await writer.drain()
                        parser.buf.clear()
                        break
                    if args is None:
                        break
                    if not args:
                        continue
                    if args[0].upper() == b"SYNC":
                        # slave 握手:劫持本连接转入复制推送(不再返回普通响应)
                        await self.replicator.handle_replica(reader, writer)
                        return
                    resp = self._dispatch(args)
                    writer.write(resp)
                    await writer.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    def _dispatch(self, args) -> bytes:
        """同步分发。对写命令,本地应用与 effect 入队在此连续完成、中间无 await。"""
        cmd = args[0].upper()
        if cmd == b"GET":
            if len(args) != 2:
                return protocol.encode_error("ERR wrong number of arguments for 'GET'")
            value = self.store.get(args[1], now=self.time())
            return protocol.encode_bulk(value)
        if cmd == b"SET":
            return self._do_set(args)
        if cmd == b"SETEX":
            return self._do_setex(args)
        if cmd == b"EXPIRE":
            return self._do_expire(args)
        if cmd == b"DEL":
            return self._do_del(args)
        if cmd == b"PING":
            return protocol.encode_simple("PONG")
        if cmd == b"ROLE":
            return protocol.encode_simple(self.role.upper())
        return protocol.encode_error(
            "ERR unknown command '%s'" % args[0].decode(errors="replace"))

    def _readonly(self) -> bytes:
        return protocol.encode_error("ERR READONLY this node is a replica")

    def _do_set(self, args) -> bytes:
        if len(args) < 3:
            return protocol.encode_error("ERR wrong number of arguments for 'SET'")
        if not self.is_master:
            return self._readonly()
        key, value = args[1], args[2]
        expire_at = None
        ms = 0
        if len(args) >= 5 and args[3].upper() == b"EX":
            try:
                secs = int(args[4])
            except ValueError:
                return protocol.encode_error("ERR value is not an integer or out of range")
            expire_at = self.time() + secs
            ms = int(expire_at * 1000)
        # --- 临界区开始:本地应用(含淘汰)+ effect batch 入队,中间无 await ---
        evicted = self.store.set(key, value, expire_at)
        effects = [("SET", key, value, ms)]
        if evicted is not None:
            effects.append(("DEL", _b(evicted)))
        self.replicator.append_batch(effects)
        # --- 临界区结束 ---
        return protocol.encode_simple("OK")

    def _do_setex(self, args) -> bytes:
        if len(args) != 4:
            return protocol.encode_error("ERR wrong number of arguments for 'SETEX'")
        if not self.is_master:
            return self._readonly()
        try:
            secs = int(args[2])
        except ValueError:
            return protocol.encode_error("ERR value is not an integer or out of range")
        key, value = args[1], args[3]
        expire_at = self.time() + secs
        ms = int(expire_at * 1000)
        evicted = self.store.set(key, value, expire_at)
        effects = [("SET", key, value, ms)]
        if evicted is not None:
            effects.append(("DEL", _b(evicted)))
        self.replicator.append_batch(effects)
        return protocol.encode_simple("OK")

    def _do_expire(self, args) -> bytes:
        if len(args) != 3:
            return protocol.encode_error("ERR wrong number of arguments for 'EXPIRE'")
        if not self.is_master:
            return self._readonly()
        try:
            secs = int(args[2])
        except ValueError:
            return protocol.encode_error("ERR value is not an integer or out of range")
        key = args[1]
        expire_at = self.time() + secs
        ms = int(expire_at * 1000)
        ok = self.store.set_expire(key, expire_at)
        if not ok:
            return protocol.encode_error("ERR no such key")
        # 把相对 EXPIRE 改写为绝对 PEXPIREAT 再复制
        self.replicator.append_batch([("PEXPIREAT", key, ms)])
        return protocol.encode_simple("OK")

    def _do_del(self, args) -> bytes:
        if len(args) != 2:
            return protocol.encode_error("ERR wrong number of arguments for 'DEL'")
        if not self.is_master:
            return self._readonly()
        existed = self.store.delete(args[1])
        if existed:
            self.replicator.append_batch([("DEL", args[1])])
        return protocol.encode_simple("OK")
