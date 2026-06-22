"""master→slave 异步复制(见 design.md Decision 5/6)。

核心契约:复制流流动的是"应用后的效果"(effect),以 batch 为单位,
一个单调递增 offset 对应一组原子效果。

effect 表示(元组):
    ("SET", key:bytes, value:bytes, pexpireat_ms:int)   # ms=0 表示无过期
    ("DEL", key:bytes)
    ("PEXPIREAT", key:bytes, ms:int)

复制线路帧格式(master→slave,均以 \\r\\n 结尾的控制行 + 长度前缀的 effect 帧):
    控制行: `SNAPSHOT <snapshot_offset> <count>` / `BATCH <offset> <count>`
    effect 帧: `E <len>\\r\\n<RESP-array payload>\\r\\n`   (长度前缀,可承载二进制)
slave→master 握手: `SYNC\\r\\n`
"""

import asyncio
from typing import List

from . import protocol


# ---- effect 编解码 ----
def effect_to_payload(effect) -> bytes:
    op = effect[0]
    if op == "SET":
        _, key, value, ms = effect
        return protocol.encode_command(b"SET", key, value, str(ms).encode())
    if op == "DEL":
        return protocol.encode_command(b"DEL", effect[1])
    if op == "PEXPIREAT":
        return protocol.encode_command(b"PEXPIREAT", effect[1], str(effect[2]).encode())
    raise ValueError("unknown effect op: %r" % (op,))


def payload_to_effect(args: List[bytes]):
    op = args[0].upper()
    if op == b"SET":
        ms = int(args[3]) if len(args) > 3 else 0
        return ("SET", args[1], args[2], ms)
    if op == b"DEL":
        return ("DEL", args[1])
    if op == b"PEXPIREAT":
        return ("PEXPIREAT", args[1], int(args[2]))
    raise ValueError("unknown effect op: %r" % (op,))


def _frame_effect(effect) -> bytes:
    payload = effect_to_payload(effect)
    return b"E " + str(len(payload)).encode() + b"\r\n" + payload + b"\r\n"


def _as_bytes(x) -> bytes:
    if isinstance(x, bytes):
        return x
    if isinstance(x, str):
        return x.encode()
    return str(x).encode()


class Batch:
    __slots__ = ("offset", "effects")

    def __init__(self, offset: int, effects: List):
        self.offset = offset
        self.effects = effects


class MasterReplicator:
    """持有复制 offset,并把 effect batch 推送给已连接的 slave。"""

    def __init__(self, store):
        self.store = store
        self.offset = 0
        self._slaves = []  # List[asyncio.Queue]

    def append_batch(self, effects) -> int:
        """关键:同步方法,内部无 await。

        调用方(写路径)在"应用本地存储"之后立即调用本方法入队,
        二者之间不得有 await,以保证本地应用顺序 == 复制顺序(原子临界区)。
        """
        self.offset += 1
        batch = Batch(self.offset, list(effects))
        for q in self._slaves:
            q.put_nowait(batch)
        return batch.offset

    @property
    def slave_count(self) -> int:
        return len(self._slaves)

    async def handle_replica(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter) -> None:
        # ---- 临界区:捕获 snapshot_offset + 快照 + 注册队列,中间无 await ----
        snapshot_offset = self.offset
        snap = self.store.snapshot()
        queue: asyncio.Queue = asyncio.Queue()
        self._slaves.append(queue)
        # ---- 临界区结束:此后产生的 batch offset 必 > snapshot_offset,且已入队 ----
        try:
            writer.write(b"SNAPSHOT %d %d\r\n" % (snapshot_offset, len(snap)))
            for (k, v, expire_at) in snap:
                ms = int(expire_at * 1000) if expire_at else 0
                writer.write(_frame_effect(("SET", _as_bytes(k), _as_bytes(v), ms)))
            await writer.drain()
            # 转入正常复制流:补发快照期间缓冲的 batch,然后持续推送
            while True:
                batch = await queue.get()
                writer.write(b"BATCH %d %d\r\n" % (batch.offset, len(batch.effects)))
                for eff in batch.effects:
                    writer.write(_frame_effect(eff))
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            pass
        finally:
            if queue in self._slaves:
                self._slaves.remove(queue)
            try:
                writer.close()
            except Exception:
                pass


class ReplicaClient:
    """slave 侧:连接 master,先全量同步,再持续回放 batch。
    断线重连后一律重新全量同步(本版不做 partial sync)。"""

    def __init__(self, master_host: str, master_port: int, store,
                 reconnect_delay: float = 0.3):
        self.host = master_host
        self.port = master_port
        self.store = store
        self.reconnect_delay = reconnect_delay
        self.applied_offset = 0
        self._stop = False
        self._task = None
        self.synced_event = asyncio.Event()

    def start(self):
        self._task = asyncio.ensure_future(self.run())
        return self._task

    async def stop(self):
        self._stop = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def run(self):
        while not self._stop:
            try:
                reader, writer = await asyncio.open_connection(self.host, self.port)
                writer.write(b"SYNC\r\n")
                await writer.drain()
                await self._consume(reader)
            except (ConnectionError, asyncio.IncompleteReadError, OSError):
                pass
            if self._stop:
                break
            self.synced_event.clear()
            await asyncio.sleep(self.reconnect_delay)

    async def _read_effect(self, reader: asyncio.StreamReader):
        line = await reader.readline()
        if not line:
            raise asyncio.IncompleteReadError(b"", None)
        parts = line.split()
        if not parts or parts[0] != b"E":
            raise protocol.ProtocolError("expected effect frame, got %r" % line)
        length = int(parts[1])
        payload = await reader.readexactly(length)
        await reader.readexactly(2)  # 末尾 CRLF
        return payload_to_effect(protocol.decode_resp_array(payload))

    def _apply(self, effect) -> None:
        op = effect[0]
        if op == "SET":
            _, key, value, ms = effect
            expire_at = ms / 1000.0 if ms else None
            self.store.apply_set(key, value, expire_at)  # 非淘汰回放
        elif op == "DEL":
            self.store.delete(effect[1])
        elif op == "PEXPIREAT":
            self.store.set_expire(effect[1], effect[2] / 1000.0)

    async def _consume(self, reader: asyncio.StreamReader):
        while not self._stop:
            line = await reader.readline()
            if not line:
                raise asyncio.IncompleteReadError(b"", None)
            parts = line.split()
            tag = parts[0]
            if tag == b"SNAPSHOT":
                snap_off = int(parts[1])
                count = int(parts[2])
                items = []
                for _ in range(count):
                    eff = await self._read_effect(reader)
                    _, key, value, ms = eff
                    expire_at = ms / 1000.0 if ms else None
                    items.append((key, value, expire_at))
                self.store.load_snapshot(items)
                self.applied_offset = snap_off
                self.synced_event.set()
            elif tag == b"BATCH":
                off = int(parts[1])
                n = int(parts[2])
                # 先读齐整个 batch 的所有 effect,再原子应用、最后推进 offset
                effects = [await self._read_effect(reader) for _ in range(n)]
                for eff in effects:
                    self._apply(eff)
                self.applied_offset = off
            else:
                raise protocol.ProtocolError("unknown repl tag: %r" % tag)
