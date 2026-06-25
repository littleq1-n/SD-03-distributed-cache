"""实时观察客户端哈希环成员(演示 etcd 服务发现用)。

用法:
  python3 scripts/watch_ring.py
  python3 scripts/watch_ring.py --etcd 127.0.0.1:2379 --interval 0.5

会每 interval 秒打印一行:时间戳 + 当前环中节点列表。
当节点上线/下线时,你能直接在屏幕上看到环成员的增减。
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from distcache.client import DistributedCacheClient


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--etcd", default="127.0.0.1:2379")
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args()

    c = DistributedCacheClient(etcd_endpoints=args.etcd.split(","))
    last = None
    try:
        while True:
            now = sorted(c.ring.nodes)
            ts = time.strftime("%H:%M:%S")
            marker = "" if now == last else "  *** changed"
            print(f"[{ts}] nodes({len(now)}): {now}{marker}", flush=True)
            last = now
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        c.close()


if __name__ == "__main__":
    main()
