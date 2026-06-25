"""启动单个缓存节点(用于交互式 nc/telnet 演示与多终端主从演示)。

示例:
  # 启动一个 master(端口 7001,metrics 9101)
  python3 serve.py --port 7001 --metrics-port 9101

  # 启动一个 slave,跟随上面的 master
  python3 serve.py --port 7002 --role slave --master 127.0.0.1:7001 \\
                   --metrics-port 9102

  # 接入 etcd 服务发现(节点启动后自动注册,挂掉后由 lease 过期自动剔除)
  python3 serve.py --port 7001 --etcd 127.0.0.1:2379 --shard A
"""

import argparse
import asyncio

from distcache import metrics
from distcache.server import Node


async def main():
    ap = argparse.ArgumentParser(description="启动一个 distcache 节点")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7001)
    ap.add_argument("--maxsize", type=int, default=1024)
    ap.add_argument("--role", choices=["master", "slave"], default="master")
    ap.add_argument("--master", default=None,
                    help="跟随的 master 地址 host:port(role=slave 时使用)")
    ap.add_argument("--metrics-port", type=int, default=9101,
                    help="Prometheus /metrics 端口;0 表示禁用(默认 9101)")
    ap.add_argument("--etcd", default=None,
                    help="逗号分隔的 etcd endpoints(如 127.0.0.1:2379);"
                         "给定则把本节点注册到 etcd 供客户端发现")
    ap.add_argument("--shard", default=None,
                    help="可选 shard 标识,作为节点元信息存入 etcd")
    ap.add_argument("--lease-ttl", type=int, default=10,
                    help="etcd lease TTL(秒),默认 10")
    args = ap.parse_args()

    metrics.start(args.metrics_port)

    node = Node(host=args.host, port=args.port, maxsize=args.maxsize, role=args.role)
    if args.master:
        mh, mp = args.master.rsplit(":", 1)
        node.follow(mh, int(mp))
    await node.start()

    registry = None
    if args.etcd:
        from distcache.discovery import EtcdRegistry
        endpoints = [e.strip() for e in args.etcd.split(",") if e.strip()]
        registry = EtcdRegistry(endpoints)
        registry.register(
            host=node.host, port=node.port,
            meta={"role": args.role, "shard": args.shard,
                  "metrics_port": args.metrics_port},
            ttl=args.lease_ttl,
        )

    print("[distcache] %s 节点已监听 %s:%d  maxsize=%d%s" % (
        args.role, node.host, node.port, args.maxsize,
        ("  following %s" % args.master) if args.master else ""), flush=True)
    if args.metrics_port and metrics.ENABLED:
        print("[distcache] /metrics 已在端口 %d 暴露" % args.metrics_port, flush=True)
    if registry is not None:
        print("[distcache] 已注册到 etcd %s (lease TTL=%ds)"
              % (args.etcd, args.lease_ttl), flush=True)
    print("[distcache] 用 `nc %s %d` 连接,输入如:SET foo bar / GET foo / DEL foo"
          % (node.host, node.port), flush=True)
    try:
        await asyncio.Event().wait()  # 持续运行直到 Ctrl-C
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        if registry is not None:
            try:
                registry.deregister()
            except Exception:
                pass
        await node.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
