"""etcd 服务发现:节点注册 + 客户端 watch。

设计要点(见 design.md 模块 1):
- 把 ``etcd3`` 这一第三方依赖**只圈在本文件**;`server.py`/`client.py`/`lru.py` 等
  核心模块 MUST NOT 直接 import etcd3。
- 未安装 ``etcd3`` 时,模块仍能正常 ``import``,但实例化 ``EtcdRegistry``/
  ``EtcdWatcher`` 会抛出明确的 ImportError(讯息里指明如何安装)。
- ``EtcdRegistry.register`` 创建 lease 并起后台线程定期 refresh,进程崩溃时 lease
  自然过期,etcd 中条目消失。``deregister`` 主动 revoke lease。
- ``EtcdWatcher`` 后台线程订阅 ``<prefix>`` 前缀,把 PUT/DELETE 转成
  ``on_add(host, port)`` / ``on_remove(host, port)`` 回调。回调在 watcher
  线程中调用,**调用方需要确保回调线程安全**(``ConsistentHashRing`` 已加锁)。

etcd key 约定:
    /distcache/nodes/<host>:<port>   value = JSON {host, port, role, shard, ...}
"""

import json
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

try:
    import etcd3  # type: ignore[import-not-found]
    ENABLED = True
    _IMPORT_ERROR: Optional[Exception] = None
except ImportError as e:
    etcd3 = None  # type: ignore[assignment]
    ENABLED = False
    _IMPORT_ERROR = e


_INSTALL_HINT = (
    "etcd3 is required for service discovery features; "
    "install with: pip install etcd3"
)

_DEFAULT_PREFIX = "/distcache/nodes/"


def _require_etcd3():
    if not ENABLED:
        raise ImportError("%s (original error: %r)" % (_INSTALL_HINT, _IMPORT_ERROR))


def _parse_endpoint(ep: str) -> Tuple[str, int]:
    host, _, port = ep.partition(":")
    if not host or not port:
        raise ValueError("invalid etcd endpoint: %r (expected host:port)" % ep)
    return host, int(port)


def _node_key(prefix: str, host: str, port: int) -> str:
    return "%s%s:%d" % (prefix, host, port)


def _key_to_host_port(key: str, prefix: str) -> Optional[Tuple[str, int]]:
    if not key.startswith(prefix):
        return None
    suffix = key[len(prefix):]
    host, _, port_str = suffix.rpartition(":")
    if not host or not port_str:
        return None
    try:
        return host, int(port_str)
    except ValueError:
        return None


def _connect(endpoints: List[str], timeout: float = 5.0):
    """构造一个 etcd3 client。

    etcd3 v0.12 的 client 不支持 endpoint 池,这里只用 endpoints[0];后续可换
    其他客户端库扩展。为避免实例化阶段长阻塞,显式传入 timeout。
    """
    _require_etcd3()
    if not endpoints:
        raise ValueError("no etcd endpoints given")
    host, port = _parse_endpoint(endpoints[0])
    return etcd3.client(host=host, port=port, timeout=timeout)


class EtcdRegistry:
    """节点侧:把自身注册到 etcd,带 lease 自动失活。"""

    def __init__(self, endpoints: List[str], prefix: str = _DEFAULT_PREFIX,
                 connect_timeout: float = 5.0):
        _require_etcd3()
        self.endpoints = list(endpoints)
        self.prefix = prefix
        self._connect_timeout = connect_timeout
        self._client = None
        self._lease = None
        self._key: Optional[str] = None
        self._keepalive_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def register(self, host: str, port: int, meta: Optional[Dict] = None,
                 ttl: int = 10) -> str:
        """注册节点,返回写入的完整 key。

        - 申请一个 TTL=ttl 秒的 lease;
        - 以 ``<prefix><host>:<port>`` 为 key、JSON(meta) 为 value 写入;
        - 启动后台线程,每 ttl/3 秒 refresh 一次 lease。
        """
        if self._lease is not None:
            raise RuntimeError("EtcdRegistry already registered")
        if ttl <= 1:
            raise ValueError("lease TTL must be >= 2 seconds")

        self._client = _connect(self.endpoints, timeout=self._connect_timeout)
        self._lease = self._client.lease(ttl)
        self._key = _node_key(self.prefix, host, port)
        body = {
            "host": host,
            "port": port,
            "ts": int(time.time()),
        }
        if meta:
            for k, v in meta.items():
                if v is not None:
                    body[k] = v
        self._client.put(self._key, json.dumps(body), lease=self._lease)

        refresh_interval = max(1.0, ttl / 3.0)
        self._stop_event.clear()
        self._keepalive_thread = threading.Thread(
            target=self._keepalive_loop, args=(refresh_interval,),
            name="etcd-keepalive", daemon=True,
        )
        self._keepalive_thread.start()
        return self._key

    def _keepalive_loop(self, interval: float):
        while not self._stop_event.wait(interval):
            try:
                if self._lease is not None:
                    self._lease.refresh()
            except Exception:
                # 一次 refresh 失败不致命,下个周期再试;lease 真过期时 etcd 会
                # 自然删除我们的 key(这就是 lease 的用途)。
                pass

    def deregister(self) -> None:
        """主动撤销 lease,etcd 立即删除注册条目;然后关闭 client。"""
        self._stop_event.set()
        if self._keepalive_thread is not None:
            self._keepalive_thread.join(timeout=2.0)
            self._keepalive_thread = None
        try:
            if self._lease is not None:
                try:
                    self._lease.revoke()
                except Exception:
                    pass
        finally:
            self._lease = None
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    pass
                self._client = None
            self._key = None


class EtcdWatcher:
    """客户端侧:列出全部节点 + 持续 watch 前缀变更。"""

    def __init__(self, endpoints: List[str], prefix: str = _DEFAULT_PREFIX,
                 connect_timeout: float = 5.0):
        _require_etcd3()
        self.endpoints = list(endpoints)
        self.prefix = prefix
        self._connect_timeout = connect_timeout
        self._client = _connect(endpoints, timeout=connect_timeout)
        self._watch_id: Optional[int] = None
        self._lock = threading.Lock()

    def list_nodes(self) -> List[Tuple[str, int]]:
        """全量拉取当前已注册的节点,返回 [(host, port), ...]。"""
        out: List[Tuple[str, int]] = []
        for _value, kv_meta in self._client.get_prefix(self.prefix):
            key = kv_meta.key.decode() if isinstance(kv_meta.key, bytes) else kv_meta.key
            hp = _key_to_host_port(key, self.prefix)
            if hp is not None:
                out.append(hp)
        return out

    def watch(self,
              on_add: Callable[[str, int], None],
              on_remove: Callable[[str, int], None]) -> None:
        """开启持续 watch;回调在 etcd3 后台线程中触发。"""
        with self._lock:
            if self._watch_id is not None:
                raise RuntimeError("EtcdWatcher already watching")

            def _callback(response):
                for event in getattr(response, "events", []) or []:
                    key_bytes = getattr(event, "key", None)
                    if key_bytes is None:
                        kv = getattr(event, "_event", None)
                        key_bytes = getattr(kv, "key", None) if kv is not None else None
                    if isinstance(key_bytes, (bytes, bytearray)):
                        key = bytes(key_bytes).decode()
                    elif isinstance(key_bytes, str):
                        key = key_bytes
                    else:
                        continue
                    hp = _key_to_host_port(key, self.prefix)
                    if hp is None:
                        continue
                    # etcd3 用类名区分 PutEvent / DeleteEvent
                    ev_type = type(event).__name__
                    if "Delete" in ev_type:
                        try:
                            on_remove(hp[0], hp[1])
                        except Exception:
                            pass
                    else:
                        try:
                            on_add(hp[0], hp[1])
                        except Exception:
                            pass

            self._watch_id = self._client.add_watch_prefix_callback(
                self.prefix, _callback)

    def stop(self) -> None:
        """取消 watch 并关闭 etcd client。"""
        with self._lock:
            if self._watch_id is not None:
                try:
                    self._client.cancel_watch(self._watch_id)
                except Exception:
                    pass
                self._watch_id = None
            try:
                self._client.close()
            except Exception:
                pass


__all__ = ["ENABLED", "EtcdRegistry", "EtcdWatcher"]
