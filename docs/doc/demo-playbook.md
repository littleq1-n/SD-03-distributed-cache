# distcache — 可观测性 & 服务发现演示手册

这份文档把每一个能拍成截图的"画面"都拆开列出。每节给出:
1. **前置准备**(本节运行前需要的状态)
2. **执行命令**(可直接复制粘贴)
3. **要看什么**(终端 / 浏览器要观察的元素)
4. **建议截图**(文件名,统一放 `docs/pic/`)

> 所有 URL 都默认 `127.0.0.1`。远程机器上演示时,在你本机做端口转发即可:
> `ssh -L 3000:127.0.0.1:3000 -L 9090:127.0.0.1:9090 root@<host>`

---

## 0. 一次性环境准备

### 0.1 启动监控栈

```bash
cd ~/xinruipeixun/02_cache/deploy
docker compose up -d
docker compose ps
```

期望三个容器都是 `Up`:
- `distcache-etcd`         → 2379
- `distcache-prometheus`   → 9090
- `distcache-grafana`      → 3000

### 0.2 启动三个 distcache 节点

```bash
cd ~/xinruipeixun/02_cache

# 干净起步:杀掉旧 serve.py
pkill -f 'serve.py' 2>/dev/null; sleep 0.5

for i in 1 2 3; do
  python3 serve.py \
    --port 700$i --metrics-port 910$i \
    --etcd 127.0.0.1:2379 --shard $(echo ABC | cut -c$i) \
    > /tmp/n700$i.log 2>&1 &
done

sleep 1
tail -3 /tmp/n7001.log
```

期望每个节点都打印:`已注册到 etcd 127.0.0.1:2379 (lease TTL=10s)`。

---

## 1. 场景:监控栈总览(终端截图)

**画面**:一屏看到栈、节点、etcd 注册、Prometheus 抓取目标四件事都健康。

```bash
{
  echo "=== docker compose ==="
  cd ~/xinruipeixun/02_cache/deploy && docker compose ps
  echo
  echo "=== distcache 节点 ==="
  pgrep -af 'serve.py --port' | sed 's/.*serve.py/python3 serve.py/'
  echo
  echo "=== etcd 注册条目 ==="
  docker exec distcache-etcd etcdctl get --prefix /distcache/nodes/
  echo
  echo "=== Prometheus 抓取目标 ==="
  curl -sG http://127.0.0.1:9090/api/v1/targets | python3 -c "
import json,sys
for t in json.load(sys.stdin)['data']['activeTargets']:
    if t['labels'].get('job')=='distcache':
        print(f\"  {t['labels']['instance']:30s}  health={t['health']}\")
"
}
```

**截图**: `docs/pic/01-stack-overview.png`(终端)

---

## 2. 场景:服务发现 — 节点上下线,客户端环自动收敛

**画面**:三个终端并排;左边一直打印环成员,中间 kill 节点,右边再起新节点。

### 终端 A(常驻):监听环

```bash
cd ~/xinruipeixun/02_cache
python3 scripts/watch_ring.py --interval 0.5
```

输出会一直滚:
```
[04:20:00] nodes(3): ['127.0.0.1:7001', '127.0.0.1:7002', '127.0.0.1:7003']
```

### 终端 B:kill 一个节点

```bash
kill -9 $(pgrep -f 'serve.py --port 7002')
```

回到终端 A,**≤10 秒内**(lease TTL)看到:
```
[04:20:09] nodes(2): ['127.0.0.1:7001', '127.0.0.1:7003']  *** changed
```

### 终端 B:再起一个新节点

```bash
cd ~/xinruipeixun/02_cache
python3 serve.py --port 7004 --metrics-port 9104 \
                 --etcd 127.0.0.1:2379 --shard D \
                 > /tmp/n7004.log 2>&1 &
```

终端 A **≤2 秒内**:
```
[04:20:23] nodes(3): ['127.0.0.1:7001', '127.0.0.1:7003', '127.0.0.1:7004']  *** changed
```

**截图**: `docs/pic/02-discovery.png`(终端 A 包含 `*** changed` 那几行)

恢复原状(把 7002 起回来,kill 掉 7004):
```bash
kill -9 $(pgrep -f 'serve.py --port 7004') 2>/dev/null
cd ~/xinruipeixun/02_cache
python3 serve.py --port 7002 --metrics-port 9102 \
                 --etcd 127.0.0.1:2379 --shard B \
                 > /tmp/n7002.log 2>&1 &
sleep 2
```

---

## 3. 场景:Prometheus `/targets` 页

**画面**:浏览器打开 Prometheus,job=`distcache` 下三个 instance 全部绿色 UP。

```bash
# 远程机器: 本机做端口转发后访问
echo "open: http://127.0.0.1:9090/targets"
```

要看的元素:
- Endpoint 列:`http://host.docker.internal:9101/metrics` 等三条
- State 列:全部 **UP**
- Last Scrape 列:5s 内

**截图**: `docs/pic/03-prom-targets.png`(浏览器,把整页缩放到能完整看到三行)

---

## 4. 场景:Grafana 仪表盘 — 空闲基线

**画面**:打开仪表盘,四个面板都还是几乎平的,说明数据通了但没流量。

```bash
echo "open: http://127.0.0.1:3000   (admin / admin)"
# 左侧菜单 → Dashboards → distcache
```

四个面板:
- **QPS by cmd** — 应该是 0
- **GET 命中率** — 无数据
- **P99 延迟 by cmd** — 0 或无数据
- **复制 lag** — 0

**截图**: `docs/pic/04-grafana-idle.png`(浏览器全屏)

---

## 5. 场景:Grafana 仪表盘 — 60 秒负载下的活跃曲线

**画面**:开始有曲线了。QPS 起飞、命中率稳定在 ~75%、P99 < 1ms、复制 lag 仍为 0(因为没有 slave)。

```bash
cd ~/xinruipeixun/02_cache
python3 scripts/load.py --duration 60 --hit 0.6 --miss 0.2 --set 0.2 --keyspace 1000
```

负载脚本会每 5 秒打印一行进度:
```
[load] t=  5.0s  ops=   61232  err=   0  rate=12246.3/s
```

**截图时机**: 跑到 ~45 秒、曲线已稳定时。

**要看的元素**:
- QPS 面板:`GET` 线 > `SET` 线,符合 6:2 比例
- 命中率面板:稳定在 0.75 左右(0.6 hit / 0.8 = 0.75)
- P99 面板:有数值(几百微秒级)
- 复制 lag:0

**截图**: `docs/pic/05-grafana-loaded.png`

> 想要更"专业"的曲线?跑两次,中间停几秒:
> ```bash
> python3 scripts/load.py --duration 30
> sleep 10
> python3 scripts/load.py --duration 30 --hit 0.8 --miss 0.1 --set 0.1
> ```
> 你会看到命中率出现一个明显的上升台阶,适合做"业务行为变化"演示。

---

## 6. 场景:LRU 淘汰可视化

**画面**:小容量节点 + 大量写 = 大量淘汰,`/metrics` 上的 `distcache_evictions_total` 持续增长。

### 6.1 起一个 maxsize=10 的小节点(不接 etcd,避免干扰主演示)

```bash
cd ~/xinruipeixun/02_cache
python3 serve.py --port 7005 --metrics-port 9105 --maxsize 10 \
                 > /tmp/n7005.log 2>&1 &
sleep 0.5
```

### 6.2 写 1000 个 key → 应该淘汰 990 次

```bash
python3 -c "
from distcache.client import DistributedCacheClient
c = DistributedCacheClient(nodes=[('127.0.0.1', 7005)])
for i in range(1000):
    c.set('k%d' % i, 'v')
c.close()
print('1000 writes done')
"
```

### 6.3 查看指标

```bash
curl -s http://127.0.0.1:9105/metrics | grep -E \
  '^distcache_(evictions_total|cache_size|cache_maxsize) ' 
```

期望:
```
distcache_evictions_total 990.0
distcache_cache_size 10.0
distcache_cache_maxsize 10.0
```

**截图**: `docs/pic/06-evictions.png`(终端,框住这三行)

> 如果想在 Grafana 上看淘汰速率,临时在 Dashboard 加一个面板:
> `rate(distcache_evictions_total[1m])`

清理:
```bash
kill -9 $(pgrep -f 'serve.py --port 7005')
```

---

## 7. 场景:主从复制 + offset 曲线分叉

**画面**:Grafana 的"复制 offsets"面板上,master.offset 和 slave.applied **两条曲线**在写入压力下短暂分叉,然后重新合拢——分叉的距离就是 lag。

> `deploy/prometheus.yml` 已经预配好端口 9201/9202,Grafana 仪表盘也预配好画 master/slave 两条曲线,**不需要任何额外配置**,直接跑下面这条命令即可。

```bash
cd ~/xinruipeixun/02_cache
python3 scripts/demo_replication.py --total 200000
```

脚本会用 `subprocess` 起**两个独立的 serve.py 进程**(必须独立进程,因为
`prometheus_client` 的 Gauge 是进程级单例,同进程会互相覆盖):
- master: 7101 + metrics 9201
- slave : 7102 + metrics 9202(跟随 7101)

终端持续打印三列(每 0.5s 一次):

```
    time     master.offset   slave.applied    lag(slave 侧)
  04:24:53           15287           13223                1
  04:24:54           15287           13223                1
  04:24:55           59649           51583                0
  04:24:56           95946           83847                0   ← master 比 slave 领先 12K offset
  04:24:57          127798          111910                0
  04:24:58          159573          141226                0   ← lag 峰值 ~18K
  04:24:59          159573          167517                0
  04:25:00          195320          195320                0   ← 追平
```

> "lag (slave 侧)"列是 `seen_offset - applied_offset`,反映 **slave 入队但还没回放**的 batch 数。在本机 latency 极低,这个值几乎总是 0;真正的"master 比 slave 领先多少"看 **第 2/3 列的差值**,这才是物理上的复制延迟。

**截图位置 #1**:终端,框住"阶段 2"出现差值的那几行  
→ `docs/pic/07-repl-terminal.png`

**截图位置 #2**:Grafana → distcache 仪表盘 → 右下角"复制 offsets"面板  
→ `docs/pic/07-repl-grafana.png`

> 在 Grafana 上,你会看到 `master.offset host.docker.internal:9201` 和 `slave.applied host.docker.internal:9202` 两条线:阶段 1 平稳低位,阶段 2 出现可见的纵向间隔(就是 lag),阶段 3 重新合拢。
>
> Prometheus 默认 5s 抓一次,本机灌完 200000 条只需要几秒,所以**面板里 lag 表现为一两个点的差值**;想看更明显的曲线,把 `--total` 调到 100 万,负载会持续 ~30 秒,曲线会更"漂亮"。

---

## 8. 场景:`/metrics` 原始输出(给"机器友好"的展示)

**画面**:终端打印 Prometheus 文本格式,所有 distcache 指标家族一目了然。

```bash
curl -s http://127.0.0.1:9101/metrics | grep -E '^(# HELP distcache|distcache_)' | head -40
```

**截图**: `docs/pic/08-metrics-raw.png`(终端,把头部 8 个 `# HELP` 都框进去)

完整 8 个指标家族:
| 指标 | 含义 |
|---|---|
| `distcache_ops_total{cmd,result}` | 命令计数 |
| `distcache_op_latency_seconds{cmd}` | 命令延迟直方图 |
| `distcache_cache_size` | 当前 key 数 |
| `distcache_cache_maxsize` | 容量上限 |
| `distcache_evictions_total` | LRU 淘汰次数 |
| `distcache_replication_offset{role}` | 复制 offset |
| `distcache_replication_lag` | 复制 lag(slave 侧才有意义) |
| `distcache_role` | 1=master, 0=slave |

---

## 9. 场景:Prometheus 表达式浏览器

**画面**:Prometheus UI 的 Graph 页,手输 PromQL 看曲线。

打开 http://127.0.0.1:9090/graph,粘贴下面任意一条:

```promql
# 总 QPS
sum(rate(distcache_ops_total[1m]))

# 按节点拆分的 QPS
sum by (instance) (rate(distcache_ops_total[1m]))

# 命中率
sum(rate(distcache_ops_total{cmd="GET",result="hit"}[1m])) 
  / clamp_min(sum(rate(distcache_ops_total{cmd="GET"}[1m])), 1e-9)

# P99 延迟
histogram_quantile(0.99, sum by (le, cmd) (rate(distcache_op_latency_seconds_bucket[1m])))

# 各节点 key 数
distcache_cache_size

# 角色
distcache_role
```

**截图**: `docs/pic/09-prom-query.png`(浏览器,切到 Graph 标签)

---

## 10. 场景:测试用例全过

**画面**:`pytest` 输出 `56 passed, 2 skipped`,证明改动没破坏原有功能。

```bash
cd ~/xinruipeixun/02_cache
python3 -m pytest -q
```

期望:
```
.......ss.................................................                                 [100%]
56 passed, 2 skipped in 0.90s
```

(`2 skipped` 是 etcd e2e 用例;装了 `etcd3` + 有 etcd 服务时也会自动跑。要全跑:)

```bash
python3 -m pytest -v
```

**截图**: `docs/pic/10-tests.png`(终端,最后那行 `56 passed` 高亮)

---

## 11. 一次性收尾

演示结束后清场:

```bash
# 停 distcache 节点
pkill -f 'serve.py'

# 停监控栈(保留数据 volume)
cd ~/xinruipeixun/02_cache/deploy
docker compose down

# 如要彻底清理 Grafana 数据(下次首次启动会重新 provision):
# docker compose down -v
```

---

## 12. 截图编排建议(配合写 README)

如果只能选 4 张放进 README 顶部,推荐:

| 编号 | 路径 | 讲什么 |
|---|---|---|
| ① | `docs/pic/01-stack-overview.png` | 一屏总览,基础设施都健康 |
| ② | `docs/pic/05-grafana-loaded.png` | 主图,讲"用图说话"的可观测性价值 |
| ③ | `docs/pic/02-discovery.png` | 讲 etcd 服务发现自动收敛 |
| ④ | `docs/pic/10-tests.png` | 讲向后兼容(原 41 + 新增 15 全过) |

剩下的可以做小图、单独章节插图。

---

## 13. 三张关键截图最快路径(15 分钟出图)

如果你只想最快产出三张能放 README 的图,按下面顺序操作。每一步都假设监控栈和三个节点已经按 §0 起来了。

### 图 ①: Grafana 在负载下的仪表盘

```bash
cd ~/xinruipeixun/02_cache
# 终端 A:开 60s 负载
python3 scripts/load.py --duration 60 --hit 0.6 --miss 0.2 --set 0.2

# 浏览器:http://127.0.0.1:3000 → admin/admin → Dashboards → distcache
# 等到第 30 秒,曲线已经稳了,截图。
```

→ `docs/pic/grafana-loaded.png`

### 图 ②: etcd 服务发现 + 节点环动态收敛

```bash
# 终端 A:看环
python3 scripts/watch_ring.py --interval 0.5

# 终端 B:kill 一个节点(等 ≤10s) → 起回来 (等 ≤2s)
kill -9 $(pgrep -f 'serve.py --port 7002')
sleep 12   # 留时间给截图 A
python3 serve.py --port 7002 --metrics-port 9102 \
                 --etcd 127.0.0.1:2379 --shard B \
                 > /tmp/n7002.log 2>&1 &
```

截 A 终端,带出 `*** changed` 这一行。

→ `docs/pic/discovery-ring.png`

### 图 ③: 一屏看到所有组件健康

```bash
{
  echo "========== Docker 监控栈 =========="
  cd ~/xinruipeixun/02_cache/deploy && docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}' | head -5
  echo
  echo "========== distcache 三节点 =========="
  pgrep -af 'serve.py --port' | sed 's/.*serve.py/  serve.py/'
  echo
  echo "========== etcd 注册条目 =========="
  docker exec distcache-etcd etcdctl get --prefix /distcache/nodes/ --keys-only 2>/dev/null | grep -v '^$'
  echo
  echo "========== Prometheus 抓取目标 =========="
  curl -sG http://127.0.0.1:9090/api/v1/targets 2>/dev/null | python3 -c "
import json,sys
for t in json.load(sys.stdin)['data']['activeTargets']:
    if t['labels'].get('job')=='distcache':
        print(f\"  {t['labels']['instance']:35s} health={t['health']}\")
"
  echo
  echo "========== 单节点指标抽样 =========="
  curl -s http://127.0.0.1:9101/metrics | grep -E '^distcache_(ops_total|cache_size|cache_maxsize|role) ' | head -8
}
```

终端截图。

→ `docs/pic/stack-health.png`
