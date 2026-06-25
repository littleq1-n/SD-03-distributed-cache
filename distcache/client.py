"""客户端侧一致性哈希路由(见 design.md / consistent-hashing spec)。

客户端本地维护哈希环,自行计算 key 的归属节点并直连该节点。
使用阻塞 socket + RESP array 请求编码。
"""

import socket
import threading
from typing import List, Optional, Tuple

from . import protocol
from .hashring import ConsistentHashRing


class CacheError(Exception):
    pass


def _b(x) -> bytes:
    if isinstance(x, bytes):
        return x
    if isinstance(x, str):
        return x.encode()
    return str(x).encode()


class _Conn:
    """与单个节点的阻塞连接,带接收缓冲(用于读取 RESP 回复)。"""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._sock: Optional[socket.socket] = None
        self._buf = bytearray()

    def _connect(self):
        if self._sock is None:
            self._sock = socket.create_connection((self.host, self.port))

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
                self._buf.clear()

    def _read_line(self) -> bytes:
        while b"\r\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("connection closed by server")
            self._buf.extend(chunk)
        idx = self._buf.find(b"\r\n")
        line = bytes(self._buf[:idx])
        del self._buf[: idx + 2]
        return line

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("connection closed by server")
            self._buf.extend(chunk)
        data = bytes(self._buf[:n])
        del self._buf[:n]
        return data

    def request(self, *args):
        self._connect()
        self._sock.sendall(protocol.encode_command(*args))
        return self._read_reply()

    def _read_reply(self):
        line = self._read_line()
        tag = line[0:1]
        body = line[1:]
        if tag == b"+":
            return body
        if tag == b"-":
            raise CacheError(body.decode(errors="replace"))
        if tag == b"$":
            n = int(body)
            if n == -1:
                return None
            data = self._read_exact(n)
            self._read_exact(2)  # 末尾 CRLF
            return data
        raise protocol.ProtocolError("bad reply: %r" % line)


class DistributedCacheClient:
    """多节点分片客户端:基于一致性哈希做客户端侧路由。

    可选 etcd 服务发现:传入 ``etcd_endpoints`` 时,客户端自动从 etcd 拉取节点
    列表并 watch 前缀变更;`add_node`/`remove_node` 由 watcher 后台线程回调。
    """

    def __init__(self,
                 nodes: Optional[List[Tuple[str, int]]] = None,
                 vnodes: int = 150,
                 etcd_endpoints: Optional[List[str]] = None,
                 etcd_prefix: str = "/distcache/nodes/"):
        self._addr_lock = threading.Lock()
        self._addr: dict = {}
        self.ring = ConsistentHashRing([], vnodes=vnodes)
        self._conns: dict = {}
        self._watcher = None

        if etcd_endpoints:
            from . import discovery  # 局部 import:核心模块不强依赖 etcd3
            self._watcher = discovery.EtcdWatcher(etcd_endpoints, prefix=etcd_prefix)
            for host, port in self._watcher.list_nodes():
                self._add_node_locked(host, port)
            self._watcher.watch(on_add=self.add_node, on_remove=self.remove_node)
        elif nodes:
            for (h, p) in nodes:
                self._add_node_locked(h, p)

    def _add_node_locked(self, host: str, port: int) -> None:
        node = "%s:%d" % (host, port)
        with self._addr_lock:
            if node in self._addr:
                return
            self._addr[node] = (host, port)
        self.ring.add_node(node)

    def _conn_for(self, key) -> Tuple[_Conn, str]:
        node = self.ring.get_node(key)
        if node is None:
            raise CacheError("no nodes in ring")
        with self._addr_lock:
            addr = self._addr.get(node)
        if addr is None:
            raise CacheError("node %s no longer registered" % node)
        conn = self._conns.get(node)
        if conn is None:
            h, p = addr
            conn = _Conn(h, p)
            self._conns[node] = conn
        return conn, node

    def route(self, key) -> Optional[str]:
        """返回 key 的归属节点标识(host:port),不发起网络请求。"""
        return self.ring.get_node(key)

    def add_node(self, host: str, port: int) -> None:
        self._add_node_locked(host, port)

    def remove_node(self, host: str, port: int) -> None:
        node = "%s:%d" % (host, port)
        self.ring.remove_node(node)
        with self._addr_lock:
            self._addr.pop(node, None)
        conn = self._conns.pop(node, None)
        if conn is not None:
            conn.close()

    def set(self, key, value):
        conn, _ = self._conn_for(key)
        return conn.request(b"SET", _b(key), _b(value))

    def get(self, key):
        conn, _ = self._conn_for(key)
        return conn.request(b"GET", _b(key))

    def delete(self, key):
        conn, _ = self._conn_for(key)
        return conn.request(b"DEL", _b(key))

    def close(self) -> None:
        for conn in self._conns.values():
            conn.close()
        self._conns.clear()
        if self._watcher is not None:
            try:
                self._watcher.stop()
            except Exception:
                pass
            self._watcher = None
