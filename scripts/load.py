"""distcache 负载生成器(用于 Grafana 截图演示)。

可调参数:
  --duration  持续秒数(默认 60)
  --qps       目标 QPS(默认 不限速,尽量打)
  --hit       hit 比例(0~1,默认 0.6) - get 已写过的 key
  --miss      miss 比例(0~1,默认 0.2) - get 没写过的 key
  --set       set 比例(剩余) -- 这三个加起来 = 1
  --keyspace  hit key 池大小(默认 1000)
  --etcd      etcd endpoints(默认 127.0.0.1:2379)
  --nodes     直接指定 host:port,逗号分隔(优先于 --etcd)

示例:
  python3 scripts/load.py --duration 60 --hit 0.6 --miss 0.2 --set 0.2
  python3 scripts/load.py --duration 30 --qps 500
"""

import argparse
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from distcache.client import DistributedCacheClient


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=60)
    ap.add_argument("--qps", type=float, default=0,
                    help="目标 QPS, 0 = 不限速")
    ap.add_argument("--hit", type=float, default=0.6)
    ap.add_argument("--miss", type=float, default=0.2)
    ap.add_argument("--set", dest="set_ratio", type=float, default=0.2)
    ap.add_argument("--keyspace", type=int, default=1000)
    ap.add_argument("--etcd", default="127.0.0.1:2379")
    ap.add_argument("--nodes", default=None,
                    help="逗号分隔的 host:port 列表,优先于 etcd")
    args = ap.parse_args()

    total = args.hit + args.miss + args.set_ratio
    if abs(total - 1.0) > 0.01:
        print(f"warning: hit+miss+set = {total:.2f}, 重新归一化", file=sys.stderr)
        args.hit /= total
        args.miss /= total
        args.set_ratio /= total

    if args.nodes:
        nodes = []
        for hp in args.nodes.split(","):
            h, p = hp.strip().rsplit(":", 1)
            nodes.append((h, int(p)))
        c = DistributedCacheClient(nodes=nodes)
        src = f"static {nodes}"
    else:
        c = DistributedCacheClient(etcd_endpoints=args.etcd.split(","))
        src = f"etcd {args.etcd}"

    # 预热:先 set 一半 keyspace 进去,让 hit 真的能命中
    for i in range(args.keyspace // 2):
        c.set(f"k:{i}", f"warmup-{i}")

    print(f"[load] discovery={src}  ring={sorted(c.ring.nodes)}")
    print(f"[load] duration={args.duration}s  qps={args.qps or 'unlimited'}")
    print(f"[load] mix: hit={args.hit:.2f} miss={args.miss:.2f} set={args.set_ratio:.2f}")

    end = time.time() + args.duration
    ops = 0
    err = 0
    next_report = time.time() + 5
    interval = (1.0 / args.qps) if args.qps > 0 else 0

    while time.time() < end:
        r = random.random()
        try:
            if r < args.hit:
                c.get(f"k:{random.randint(0, args.keyspace // 2 - 1)}")
            elif r < args.hit + args.miss:
                c.get(f"miss:{random.randint(0, 10**8)}")
            else:
                k = random.randint(0, args.keyspace - 1)
                c.set(f"k:{k}", f"v-{ops}")
        except Exception:
            err += 1
        ops += 1
        if interval:
            time.sleep(interval)
        if time.time() >= next_report:
            elapsed = args.duration - (end - time.time())
            print(f"[load] t={elapsed:5.1f}s  ops={ops:8d}  err={err:4d}  "
                  f"rate={ops/elapsed:7.1f}/s")
            next_report = time.time() + 5

    c.close()
    print(f"[load] done. total_ops={ops}  errors={err}")


def _silence_etcd3_thread_noise():
    """抑制 etcd3 0.12 watcher 守护线程在 Python 3.11 下的噪音。

    两类噪音:
    1) 运行期间: gRPC watch stream 偶发 RpcError -> 未捕获 ->
       threading.excepthook 把 traceback 打到 stderr (含 'etcd3_watch_xxx')。
    2) 退出 finalize 阶段: 守护线程与 stderr 锁竞态 ->
       "Fatal Python error: _enter_buffered_busy ... Aborted (core dumped)"。

    解决:
    - 装一个自定义 threading.excepthook,只吞 etcd3_watch 线程的异常,
      其他线程异常照常打印。
    - 业务结束后用 os._exit 跳过 Python finalize,彻底消除 Aborted。
    """
    import threading

    _orig_hook = threading.excepthook

    def _hook(args):
        if args.thread is not None and args.thread.name.startswith("etcd3_watch"):
            return  # 静默
        _orig_hook(args)

    threading.excepthook = _hook


if __name__ == "__main__":
    _silence_etcd3_thread_noise()
    try:
        main()
    except KeyboardInterrupt:
        print("\n[load] interrupted")
    finally:
        import os
        import sys
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)
