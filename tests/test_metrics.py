"""metrics 模块测试:no-op 路径 + 真启用路径(后者在缺包时自动 skip)。"""

import asyncio

import pytest

from distcache import metrics
from distcache.server import Node
from tests.helpers import cmd


def run(coro):
    return asyncio.run(coro)


# ---------------- no-op 兼容性 ----------------

def test_metric_objects_support_full_api_regardless_of_backend():
    """关键:埋点站点的调用必须在两种 backend 下都不抛异常。"""
    metrics.ops_total.labels(cmd="GET", result="hit").inc()
    metrics.ops_total.labels(cmd="SET", result="ok").inc(2)
    metrics.cache_size.set(0)
    metrics.cache_maxsize.set(1024)
    metrics.evictions_total.inc()
    metrics.repl_offset.labels(role="master").set(100)
    metrics.repl_lag.set(0)
    metrics.role_gauge.set(1)
    with metrics.op_latency.labels(cmd="GET").time():
        pass


def test_start_with_port_zero_is_disabled():
    """显式禁用:start(0) 总是返回 False,不抛异常。"""
    assert metrics.start(0) is False


# ---------------- 真启用路径(条件 skip) ----------------

prom = pytest.importorskip(
    "prometheus_client",
    reason="prometheus_client not installed; only no-op path is tested",
)


def _read_counter(metric, labels: dict) -> float:
    """从 REGISTRY 读出某个 label 组合的 Counter 值;不存在返回 0。"""
    name = metric._name + "_total" if not metric._name.endswith("_total") else metric._name
    for fam in prom.REGISTRY.collect():
        if fam.name != metric._name and fam.name != name and fam.name + "_total" != name:
            continue
        for sample in fam.samples:
            if not sample.name.endswith("_total"):
                continue
            if all(sample.labels.get(k) == v for k, v in labels.items()):
                return sample.value
    return 0.0


def _read_gauge(metric_name: str, labels: dict = None) -> float:
    labels = labels or {}
    for fam in prom.REGISTRY.collect():
        if fam.name != metric_name:
            continue
        for sample in fam.samples:
            if sample.name != metric_name:
                continue
            if all(sample.labels.get(k) == v for k, v in labels.items()):
                return sample.value
    return 0.0


def test_ops_total_increments_for_get_hit_and_miss():
    async def main():
        node = Node(port=0)
        await node.start()
        try:
            before_hit = _read_counter(metrics.ops_total, {"cmd": "GET", "result": "hit"})
            before_miss = _read_counter(metrics.ops_total, {"cmd": "GET", "result": "miss"})

            await cmd(node.host, node.port, b"SET", b"a", b"1")
            assert await cmd(node.host, node.port, b"GET", b"a") == b"1"
            assert await cmd(node.host, node.port, b"GET", b"missing") is None

            after_hit = _read_counter(metrics.ops_total, {"cmd": "GET", "result": "hit"})
            after_miss = _read_counter(metrics.ops_total, {"cmd": "GET", "result": "miss"})
            assert after_hit - before_hit >= 1
            assert after_miss - before_miss >= 1
        finally:
            await node.stop()

    run(main())


def test_set_ok_and_readonly_classified_separately():
    async def main():
        master = Node(port=0)
        slave = Node(port=0, role="slave")
        await master.start()
        await slave.start()
        try:
            before_ok = _read_counter(metrics.ops_total, {"cmd": "SET", "result": "ok"})
            before_ro = _read_counter(metrics.ops_total, {"cmd": "SET", "result": "readonly"})

            await cmd(master.host, master.port, b"SET", b"k", b"v")
            await cmd(slave.host, slave.port, b"SET", b"k", b"v")  # readonly

            after_ok = _read_counter(metrics.ops_total, {"cmd": "SET", "result": "ok"})
            after_ro = _read_counter(metrics.ops_total, {"cmd": "SET", "result": "readonly"})
            assert after_ok - before_ok >= 1
            assert after_ro - before_ro >= 1
        finally:
            await master.stop()
            await slave.stop()

    run(main())


def test_evictions_counter_increments_on_lru_eviction():
    async def main():
        node = Node(port=0, maxsize=3)
        await node.start()
        try:
            before = _read_counter(metrics.evictions_total, {})

            for k in (b"a", b"b", b"c", b"d"):  # 第 4 个触发 1 次淘汰
                await cmd(node.host, node.port, b"SET", k, b"v")

            after = _read_counter(metrics.evictions_total, {})
            assert after - before == 1
        finally:
            await node.stop()

    run(main())


def test_cache_size_gauge_updates_after_writes():
    async def main():
        node = Node(port=0, metrics_sample_interval=0.05)
        await node.start()
        try:
            for i in range(5):
                await cmd(node.host, node.port, b"SET", str(i).encode(), b"v")
            # 等一个采样周期
            await asyncio.sleep(0.15)
            assert _read_gauge("distcache_cache_size") == 5
        finally:
            await node.stop()

    run(main())


def test_role_gauge_flips_on_promote():
    async def main():
        node = Node(port=0, role="slave", metrics_sample_interval=0.05)
        await node.start()
        try:
            await asyncio.sleep(0.1)
            assert _read_gauge("distcache_role") == 0
            node.promote()
            await asyncio.sleep(0.1)
            assert _read_gauge("distcache_role") == 1
        finally:
            await node.stop()

    run(main())


def test_metrics_endpoint_serves_text_format(unused_tcp_port_factory=None):
    """启动 HTTP server,curl 抓到的内容包含 distcache_* 指标家族元行。"""
    import socket
    import urllib.request

    # 找一个空闲端口(prometheus_client 不允许重复 start)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    started = metrics.start(port)
    assert started is True

    async def main():
        node = Node(port=0, metrics_sample_interval=0.05)
        await node.start()
        try:
            await cmd(node.host, node.port, b"SET", b"a", b"1")
            await cmd(node.host, node.port, b"GET", b"a")
            await asyncio.sleep(0.1)
            text = urllib.request.urlopen(
                "http://127.0.0.1:%d/metrics" % port, timeout=2.0
            ).read().decode()
            for fam in (
                "distcache_ops_total",
                "distcache_op_latency_seconds",
                "distcache_cache_size",
                "distcache_cache_maxsize",
                "distcache_evictions_total",
                "distcache_replication_offset",
                "distcache_replication_lag",
                "distcache_role",
            ):
                assert ("# HELP %s " % fam) in text, "missing family: %s" % fam
        finally:
            await node.stop()

    run(main())
