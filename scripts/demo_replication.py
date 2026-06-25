"""演示主从复制 + 复制 lag(用于 Grafana 复制 lag 面板截图)。

为了让 lag 在 Prometheus / Grafana 上可见,master 和 slave **必须跑在两个不同的进程**
里(prometheus_client 的 Gauge 是进程级单例,同进程会互相覆盖)。所以本脚本用
subprocess 起两个 serve.py 子进程。

用法:
  python3 scripts/demo_replication.py
  python3 scripts/demo_replication.py --total 200000

  # 演示完想在 Grafana 看到曲线,临时把这两个 metrics 端口加进 prometheus.yml:
  #   - host.docker.internal:9201
  #   - host.docker.internal:9202
  # 然后 docker compose restart prometheus
"""

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def fetch_metrics(port: int) -> dict:
    """从 /metrics 抓 master.offset / slave.applied / slave.lag。"""
    out = {"master_offset": None, "slave_applied": None, "lag": None}
    try:
        text = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/metrics", timeout=1.0).read().decode()
    except Exception:
        return out
    for line in text.splitlines():
        if line.startswith('distcache_replication_offset{role="master"}'):
            out["master_offset"] = float(line.rsplit(maxsplit=1)[-1])
        elif line.startswith('distcache_replication_offset{role="slave"}'):
            out["slave_applied"] = float(line.rsplit(maxsplit=1)[-1])
        elif line.startswith("distcache_replication_lag "):
            out["lag"] = float(line.rsplit(maxsplit=1)[-1])
    return out


def wait_port(port: int, timeout: float = 5.0) -> bool:
    """轮询 /metrics 直到可达。"""
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=0.3)
            return True
        except Exception:
            time.sleep(0.1)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--master-port", type=int, default=7101)
    ap.add_argument("--slave-port", type=int, default=7102)
    ap.add_argument("--master-metrics", type=int, default=9201)
    ap.add_argument("--slave-metrics", type=int, default=9202)
    ap.add_argument("--total", type=int, default=200000,
                    help="阶段 2 写入条数")
    args = ap.parse_args()

    serve_py = os.path.join(ROOT, "serve.py")
    procs = []

    def spawn(cmd):
        p = subprocess.Popen(cmd, cwd=ROOT,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             preexec_fn=os.setsid)
        procs.append(p)
        return p

    print("[demo] 启动 master + slave 两个独立进程 …")
    master = spawn([sys.executable, serve_py,
                    "--port", str(args.master_port),
                    "--metrics-port", str(args.master_metrics),
                    "--maxsize", "300000"])
    if not wait_port(args.master_metrics):
        print(f"[demo] master 启动失败,看看 /tmp/n{args.master_port}.log"); sys.exit(1)

    slave = spawn([sys.executable, serve_py,
                   "--port", str(args.slave_port),
                   "--metrics-port", str(args.slave_metrics),
                   "--role", "slave",
                   "--master", f"127.0.0.1:{args.master_port}",
                   "--maxsize", "500000"])
    if not wait_port(args.slave_metrics):
        print(f"[demo] slave 启动失败"); sys.exit(1)
    time.sleep(0.5)  # slave 完成首次同步

    print(f"[demo] master pid={master.pid}  port={args.master_port}  /metrics={args.master_metrics}")
    print(f"[demo] slave  pid={slave.pid}   port={args.slave_port}  /metrics={args.slave_metrics}")
    print()
    print("    time     master.offset   slave.applied    lag(slave 侧)")
    print("    ----     -------------   -------------    -------------")

    def poll_and_print(tag=""):
        m = fetch_metrics(args.master_metrics)
        s = fetch_metrics(args.slave_metrics)
        ts = time.strftime("%H:%M:%S")
        mo = "%.0f" % (m["master_offset"] or 0)
        sa = "%.0f" % (s["slave_applied"] or 0)
        lag = "%.0f" % (s["lag"] or 0)
        print(f"  {ts}   {mo:>13}   {sa:>13}    {lag:>13}   {tag}")
        return m, s

    # 后台启 metrics 轮询线程
    import threading
    stop_evt = threading.Event()

    def poll_loop():
        while not stop_evt.is_set():
            poll_and_print()
            stop_evt.wait(0.5)
    threading.Thread(target=poll_loop, daemon=True).start()

    try:
        time.sleep(2)

        # 用 RESP 直接打 master(避免客户端额外开销)
        from distcache import protocol
        import socket

        def burst(n, pause_between=0):
            sock = socket.create_connection(("127.0.0.1", args.master_port))
            try:
                buf = bytearray()
                for i in range(n):
                    buf += protocol.encode_command(
                        b"SET", f"k:{i}".encode(), b"x" * 32)
                    if len(buf) >= 64 * 1024:
                        sock.sendall(bytes(buf))
                        buf.clear()
                        if pause_between:
                            time.sleep(pause_between)
                if buf:
                    sock.sendall(bytes(buf))
                # 读回复(把 socket 排空)
                sock.settimeout(2)
                try:
                    while sock.recv(65536):
                        pass
                except (socket.timeout, OSError):
                    pass
            finally:
                sock.close()

        print("\n  >>> 阶段 1: 慢速写 1000 条")
        burst(1000, pause_between=0.005)
        time.sleep(2)

        print(f"\n  >>> 阶段 2: 高速灌 {args.total} 条(lag 应该飙升)")
        burst(args.total, pause_between=0)

        print("\n  >>> 阶段 3: 停写,等 slave 追平")
        time.sleep(8)

    finally:
        stop_evt.set()
        time.sleep(0.5)
        print("\n[demo] 关闭子进程 …")
        for p in procs:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except Exception:
                pass
        for p in procs:
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()
        print("[demo] done")


if __name__ == "__main__":
    main()
