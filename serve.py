"""启动单个缓存节点(用于交互式 nc/telnet 演示与多终端主从演示)。

示例:
  # 启动一个 master(端口 7001)
  python3 serve.py --port 7001

  # 启动一个 slave,跟随上面的 master
  python3 serve.py --port 7002 --role slave --master 127.0.0.1:7001
"""

import argparse
import asyncio

from distcache.server import Node


async def main():
    ap = argparse.ArgumentParser(description="启动一个 distcache 节点")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7001)
    ap.add_argument("--maxsize", type=int, default=1024)
    ap.add_argument("--role", choices=["master", "slave"], default="master")
    ap.add_argument("--master", default=None,
                    help="跟随的 master 地址 host:port(role=slave 时使用)")
    args = ap.parse_args()

    node = Node(host=args.host, port=args.port, maxsize=args.maxsize, role=args.role)
    if args.master:
        mh, mp = args.master.rsplit(":", 1)
        node.follow(mh, int(mp))
    await node.start()
    print("[distcache] %s 节点已监听 %s:%d  maxsize=%d%s" % (
        args.role, node.host, node.port, args.maxsize,
        ("  following %s" % args.master) if args.master else ""), flush=True)
    print("[distcache] 用 `nc %s %d` 连接,输入如:SET foo bar / GET foo / DEL foo"
          % (node.host, node.port), flush=True)
    try:
        await asyncio.Event().wait()  # 持续运行直到 Ctrl-C
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await node.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
