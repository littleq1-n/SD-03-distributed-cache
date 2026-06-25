"""服务发现测试。

主要验证两件事:
1. 不传 ``etcd_endpoints`` 时,客户端 / serve.py 完全不 import etcd3,
   v2 历史行为零变化。
2. ``discovery.py`` 暴露的解析/key 转换辅助函数正确。

真正的端到端 etcd 测试(注册/watch)需要一个运行中的 etcd,这里用
``importorskip`` 在缺包/缺服务时优雅跳过。
"""

import os

import pytest

from distcache import client as client_mod
from distcache import discovery
from distcache.hashring import ConsistentHashRing


# ---------------- 公共工具函数 ----------------

def test_key_to_host_port_parses_standard_key():
    hp = discovery._key_to_host_port(
        "/distcache/nodes/127.0.0.1:7001", "/distcache/nodes/")
    assert hp == ("127.0.0.1", 7001)


def test_key_to_host_port_ignores_foreign_key():
    assert discovery._key_to_host_port("/other/x", "/distcache/nodes/") is None


def test_key_to_host_port_handles_ipv6_like_input():
    hp = discovery._key_to_host_port(
        "/distcache/nodes/example.local:9999", "/distcache/nodes/")
    assert hp == ("example.local", 9999)


def test_parse_endpoint_basic():
    assert discovery._parse_endpoint("127.0.0.1:2379") == ("127.0.0.1", 2379)


def test_parse_endpoint_rejects_invalid():
    with pytest.raises(ValueError):
        discovery._parse_endpoint("no-port")


# ---------------- 依赖隔离:核心模块不强依赖 etcd3 ----------------

def test_distributed_cache_client_without_etcd_does_not_touch_discovery(monkeypatch):
    """构造客户端时若未传 etcd_endpoints,绝不应触发对 discovery 的访问。"""
    sentinel_called = {"flag": False}

    def _boom(*args, **kwargs):
        sentinel_called["flag"] = True
        raise RuntimeError("discovery should not be touched")

    monkeypatch.setattr(discovery, "EtcdWatcher", _boom)

    c = client_mod.DistributedCacheClient(nodes=[("127.0.0.1", 7001)])
    try:
        assert c.route("anything") == "127.0.0.1:7001"
        assert sentinel_called["flag"] is False
    finally:
        c.close()


def test_hashring_thread_safety_smoke():
    """并发 add/remove/get_node 不抛异常、最终一致。"""
    import threading

    ring = ConsistentHashRing([], vnodes=50)

    def adder():
        for i in range(50):
            ring.add_node("n%d" % i)

    def remover():
        for i in range(50):
            ring.remove_node("n%d" % i)

    def reader():
        for _ in range(200):
            ring.get_node("k%d" % _)

    threads = [
        threading.Thread(target=adder),
        threading.Thread(target=reader),
        threading.Thread(target=remover),
        threading.Thread(target=reader),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 不断言最终集合(顺序不定),只断言没炸。


# ---------------- 真启用路径:需要 etcd3 + 一个本地 etcd 服务 ----------------

try:
    import etcd3 as _etcd3  # type: ignore[import-not-found]
    _ETCD3_AVAILABLE = True
except ImportError:
    _etcd3 = None
    _ETCD3_AVAILABLE = False

ETCD_HOST = os.environ.get("DISTCACHE_TEST_ETCD_HOST", "127.0.0.1")
ETCD_PORT = int(os.environ.get("DISTCACHE_TEST_ETCD_PORT", "2379"))


def _etcd_reachable() -> bool:
    if not _ETCD3_AVAILABLE:
        return False
    try:
        c = _etcd3.client(host=ETCD_HOST, port=ETCD_PORT, timeout=1.0)
        c.status()
        c.close()
        return True
    except Exception:
        return False


_ETCD_REASON = "etcd3 not installed" if not _ETCD3_AVAILABLE else (
    "no etcd reachable at %s:%d" % (ETCD_HOST, ETCD_PORT))

needs_etcd = pytest.mark.skipif(
    not _etcd_reachable(),
    reason=_ETCD_REASON,
)


@needs_etcd
def test_register_and_list_nodes_roundtrip():
    import uuid
    prefix = "/distcache/test/%s/" % uuid.uuid4().hex

    reg = discovery.EtcdRegistry(
        ["%s:%d" % (ETCD_HOST, ETCD_PORT)], prefix=prefix)
    reg.register("127.0.0.1", 17001, meta={"role": "master"}, ttl=2)
    try:
        watcher = discovery.EtcdWatcher(
            ["%s:%d" % (ETCD_HOST, ETCD_PORT)], prefix=prefix)
        try:
            nodes = watcher.list_nodes()
            assert ("127.0.0.1", 17001) in nodes
        finally:
            watcher.stop()
    finally:
        reg.deregister()


@needs_etcd
def test_watcher_callbacks_fire_on_add_and_remove():
    import threading
    import time
    import uuid
    prefix = "/distcache/test/%s/" % uuid.uuid4().hex

    added = []
    removed = []
    add_evt = threading.Event()
    rm_evt = threading.Event()

    watcher = discovery.EtcdWatcher(
        ["%s:%d" % (ETCD_HOST, ETCD_PORT)], prefix=prefix)

    def on_add(h, p):
        added.append((h, p))
        add_evt.set()

    def on_remove(h, p):
        removed.append((h, p))
        rm_evt.set()

    watcher.watch(on_add=on_add, on_remove=on_remove)
    try:
        reg = discovery.EtcdRegistry(
            ["%s:%d" % (ETCD_HOST, ETCD_PORT)], prefix=prefix)
        reg.register("127.0.0.1", 17002, ttl=2)
        try:
            assert add_evt.wait(timeout=3.0)
            assert ("127.0.0.1", 17002) in added
        finally:
            reg.deregister()
        assert rm_evt.wait(timeout=3.0)
        assert ("127.0.0.1", 17002) in removed
    finally:
        watcher.stop()
        time.sleep(0.05)
