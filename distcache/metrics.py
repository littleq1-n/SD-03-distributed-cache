"""Prometheus 指标定义与暴露(可选依赖)。

设计要点(见 design.md):
- 安装了 ``prometheus_client`` → 真正暴露指标,``start(port)`` 启动 HTTP server。
- 未安装 → 全部 metric 对象降级为 no-op,埋点站点的 ``.labels(...)``、``.inc()``、
  ``.set()``、``.observe()``、``.time()`` 调用照常工作但不做任何事。
- 调用方代码因此**不需要任何 ``if metrics_enabled:`` 分支**,埋点与启用是正交的。

为保证启用与降级两条路径的接口完全一致,no-op 实现也支持 ``.labels`` 链式调用
返回同一个对象,并把 ``.time()`` 实现成 contextmanager。
"""

import contextlib
import sys

try:
    from prometheus_client import (  # type: ignore[import-not-found]
        Counter as _Counter,
        Gauge as _Gauge,
        Histogram as _Histogram,
        start_http_server as _start_http_server,
    )
    ENABLED = True
except ImportError:
    ENABLED = False

    class _Noop:
        """no-op 指标对象。.labels(...) 返回自身,支持所有方法静默无作为。"""

        def labels(self, *args, **kwargs):
            return self

        def inc(self, *args, **kwargs):
            pass

        def dec(self, *args, **kwargs):
            pass

        def set(self, *args, **kwargs):
            pass

        def observe(self, *args, **kwargs):
            pass

        @contextlib.contextmanager
        def time(self):
            yield

    def _Counter(*args, **kwargs):  # type: ignore[misc]
        return _Noop()

    def _Gauge(*args, **kwargs):  # type: ignore[misc]
        return _Noop()

    def _Histogram(*args, **kwargs):  # type: ignore[misc]
        return _Noop()

    def _start_http_server(*args, **kwargs):  # type: ignore[misc]
        return None


_LATENCY_BUCKETS = (0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0)

ops_total = _Counter(
    "distcache_ops_total",
    "Number of commands processed by this node.",
    ["cmd", "result"],
)

op_latency = _Histogram(
    "distcache_op_latency_seconds",
    "Per-command processing latency in seconds.",
    ["cmd"],
    buckets=_LATENCY_BUCKETS,
)

cache_size = _Gauge(
    "distcache_cache_size",
    "Current number of keys stored on this node.",
)

cache_maxsize = _Gauge(
    "distcache_cache_maxsize",
    "Configured maxsize (capacity upper bound) for this node.",
)

evictions_total = _Counter(
    "distcache_evictions_total",
    "Total number of LRU evictions on this node.",
)

repl_offset = _Gauge(
    "distcache_replication_offset",
    "Master.offset (role=master) / applied_offset (role=slave).",
    ["role"],
)

repl_lag = _Gauge(
    "distcache_replication_lag",
    "Replication lag in offsets; 0 on master, master_offset-applied on slave.",
)

role_gauge = _Gauge(
    "distcache_role",
    "1 if this node is master, 0 if slave.",
)


def start(port: int) -> bool:
    """启动 Prometheus HTTP 端口。

    - ``port == 0``: 显式禁用,不监听,返回 False。
    - 未安装 ``prometheus_client``: 打印一条 warning 到 stderr,返回 False。
    - 其他: 调 ``prometheus_client.start_http_server(port)``,返回 True。
    """
    if port == 0:
        return False
    if not ENABLED:
        print(
            "[distcache] WARNING: prometheus_client not installed; /metrics disabled.",
            file=sys.stderr,
            flush=True,
        )
        return False
    _start_http_server(port)
    return True


__all__ = [
    "ENABLED",
    "ops_total",
    "op_latency",
    "cache_size",
    "cache_maxsize",
    "evictions_total",
    "repl_offset",
    "repl_lag",
    "role_gauge",
    "start",
]
