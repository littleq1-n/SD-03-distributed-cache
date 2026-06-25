# Design: 接入 etcd / Prometheus / Grafana

## 架构概览

本次变更在 v2 现有三层(单机存储 → 协议 → 网络)与两条扩展轴(分片、复制)之上,**加挂两条横向轴**:服务发现(etcd)和可观测性(Prometheus + Grafana)。核心模块零改动,仅在 `server.py` 中插入指标埋点(均通过 `metrics` 模块),`client.py` 中加入一个可选构造参数。

```
                          ┌─────────────────┐
                          │     etcd        │  注册中心
                          │ /distcache/nodes│  (lease + watch)
                          └────▲────┬───────┘
                       注册 │ │ │ watch
        ┌──────────────────┘   │   └──────────────┐
        │                       │                   │
   ┌────┴─────┐  Effect Batch   │              ┌────┴──────────────┐
   │ Node 7001│ ───────────────▶│              │  Client (本地哈希环)│
   │ (master) │   异步复制       │              │  set/get 直连节点  │
   │  /metrics│ ◀──────────────┐│              └────────────────────┘
   └────┬─────┘                ││
        │ HTTP scrape          ││
        ▼                      ││
   ┌──────────┐  TSDB         ┌┴┴────────┐
   │Prometheus│ ◀──────────── │ Node 7002│
   │  :9090   │                │  (slave) │
   └────┬─────┘                └──────────┘
        │ query
        ▼
   ┌──────────┐
   │ Grafana  │  仪表盘
   │  :3000   │  (provisioning)
   └──────────┘
```

要点:

- **etcd 与服务发现解耦在 `discovery.py` 一个模块**,核心 LRU / 协议 / 复制不感知 etcd。
- **指标暴露与采集走纯 HTTP**,Prometheus 不需要任何 SDK,只要节点开 `/metrics` 即可被抓取。
- **Grafana 不直接接节点**,它只接 Prometheus 作为数据源。这是标准 Prom 生态三层。
- **没有任何一项是 v2 运行的硬依赖**:`metrics.py` 在缺包时降级为 no-op;`discovery.py` 仅当 `--etcd` / `etcd_endpoints` 显式给出时启用。

## 模块划分

### Module 1: `distcache/discovery.py`(新增)

- 职责:把 etcd 的服务注册/发现"圈"在这一个文件里,核心模块不感知 etcd。
- 关键类:
  - `EtcdRegistry(endpoints, prefix="/distcache/nodes")`
    - `register(host, port, meta: dict, ttl=10)`:申请 lease、`put` 节点条目、起后台 keepalive 协程续租。
    - `deregister()`:撤销 lease,条目立即消失;`Node.stop()` 调用。
  - `EtcdWatcher(endpoints, prefix="/distcache/nodes")`
    - `list_nodes() -> list[tuple[str,int]]`:首次全量拉取,用于初始化哈希环。
    - `watch(on_add, on_remove)`:启动后台线程订阅前缀变更,触发回调。
    - `stop()`:取消 watch 并关闭 client。
- 依赖隔离:`import etcd3` 放在 `try/except ImportError` 内部,缺包时抛清晰错误"未启用 etcd_endpoints 时不应实例化"。

### Module 2: `distcache/metrics.py`(新增)

- 职责:集中定义所有 Prometheus 指标对象,提供 `start(port)` 暴露端口;在 `prometheus_client` 缺失时**降级为 no-op**(保证 v2 零依赖跑通)。
- 接口:
  - 全局对象:`ops_total`(Counter)、`op_latency`(Histogram)、`cache_size`(Gauge)、`cache_maxsize`(Gauge)、`evictions_total`(Counter)、`repl_offset`(Gauge,label=`role`)、`repl_lag`(Gauge)、`role_gauge`(Gauge)。
  - `start(port: int = 9101)`:启动 HTTP server(`prometheus_client.start_http_server`);no-op 时空函数。
- 关键点:Counter/Gauge/Histogram 在 no-op 实现里要支持 `.labels(...)`、`.inc()`、`.set()`、`.observe()`、`.time()` 上下文管理器,**调用站点不需要 `if metrics_enabled` 分支**。

### Module 3: `distcache/server.py`(修改,埋点为加性)

- 改动点(全部围绕已有方法,不改控制流):
  - `start()` 末尾起 `_metrics_sample_loop()`:每秒采样 `cache_size`、`repl_offset`、`repl_lag`、`role_gauge`,写入对应 Gauge。
  - `_dispatch(args)`:用 `with op_latency.labels(cmd).time():` 包裹分发体;末尾按返回值类型 inc `ops_total{cmd, result}`(`result ∈ {hit, miss, ok, error, readonly}`)。
  - `_do_set`:`evicted is not None` 那一支 `evictions_total.inc()`。
  - `promote()`:`role_gauge.set(1)`。
- 不动:LRU / 协议解析 / 复制流契约 / 临界区。

### Module 4: `distcache/client.py`(修改,加性)

- `DistributedCacheClient.__init__` 新增可选 `etcd_endpoints: list[str] | None = None`:
  - 给定时,内部构造 `EtcdWatcher`,先 `list_nodes()` 得到节点列表,再 `watch(on_add=self.add_node, on_remove=self.remove_node)`。
  - 未给定时,行为与 v2 完全一致(从 `nodes` 参数读)。
- `close()` 关闭 watcher。
- 已有 `add_node` / `remove_node` 方法不动,直接复用——这是改动最小、最优雅的接入点。

### Module 5: `serve.py`(修改)

- 新增三个 CLI 参数:
  - `--etcd <endpoints>`(逗号分隔,如 `127.0.0.1:2379`),给定则启用注册。
  - `--metrics-port <int>`,默认 `9101`,`0` 表示禁用。
  - `--shard <id>`,可选,作为 etcd 节点元信息存入,后续用于按分片选主。
- 启动时:`metrics.start(args.metrics_port)` + 可选 `EtcdRegistry.register(...)`,`stop()` 时反向 deregister。

### Module 6: `deploy/`(新增,纯运维配置)

```
deploy/
├── docker-compose.yml                 # etcd + prometheus + grafana 三件套
├── prometheus.yml                     # scrape_configs(static_configs 三个节点端口)
└── grafana/
    ├── provisioning/
    │   ├── datasources/ds.yml         # 接 prometheus:9090
    │   └── dashboards/dash.yml        # 自动加载 dashboards/
    └── dashboards/
        └── distcache.json             # 首版:QPS / 命中率 / P99 / 复制 lag
```

## 数据模型

### etcd 键值约定

| Key | Value (JSON) | 谁写 | 谁读 |
|---|---|---|---|
| `/distcache/nodes/<host>:<port>` | `{"host":"127.0.0.1","port":7001,"role":"master","shard":"A","ts":1719182400}` | `EtcdRegistry.register` | `EtcdWatcher`、Prometheus(将来)|

- key 形如 `127.0.0.1:7001`,直接可解析为 `(host, int(port))` 喂给 `ring.add_node`。
- value 用 JSON,字段小而稳;`shard` 字段为后续选主预留,**首版不消费**。
- 每个 key 绑定一个 lease(默认 TTL=10s,keepalive 间隔 3s),进程崩溃后最多 10s 自动消失。

### Prometheus 指标家族

| 指标 | 类型 | 标签 | 单位 | 含义 |
|---|---|---|---|---|
| `distcache_ops_total` | Counter | `cmd`(GET/SET/DEL/EXPIRE/PING/ROLE), `result`(hit/miss/ok/error/readonly) | 次 | 命令计数 |
| `distcache_op_latency_seconds` | Histogram | `cmd` | 秒 | 单命令处理耗时,buckets `0.0001,0.0005,0.001,0.005,0.01,0.05,0.1` |
| `distcache_cache_size` | Gauge | — | 个 | `len(self.store)` 周期采样 |
| `distcache_cache_maxsize` | Gauge | — | 个 | 启动时 set 一次 |
| `distcache_evictions_total` | Counter | — | 次 | LRU 淘汰次数 |
| `distcache_replication_offset` | Gauge | `role`(master/slave) | offset | master.offset / slave.applied_offset |
| `distcache_replication_lag` | Gauge | — | offset 差 | slave 侧:`master_offset - applied_offset`;master 侧固定 0 |
| `distcache_role` | Gauge | — | 0/1 | 1=master, 0=slave;`promote()` 时翻转 |

### Grafana 首版仪表盘面板

| 面板 | PromQL |
|---|---|
| QPS by cmd | `sum by (cmd) (rate(distcache_ops_total[1m]))` |
| 命中率(GET) | `rate(distcache_ops_total{cmd="GET",result="hit"}[1m]) / clamp_min(rate(distcache_ops_total{cmd="GET"}[1m]), 1e-9)` |
| P99 延迟 | `histogram_quantile(0.99, sum by (le, cmd) (rate(distcache_op_latency_seconds_bucket[1m])))` |
| 复制 lag | `distcache_replication_lag` |

## 技术选型说明

### 1. 为什么选 etcd 而不是 ZooKeeper / Consul / Nacos

- **etcd 的 Python 客户端最简单**:`etcd3.client(host, port).put / get_prefix / watch_prefix` 三个 API 就够本提案的所有需求,不用引入注册中心 SDK 那种重型抽象。
- **lease + keepalive 模式天然契合"进程在=注册在"的语义**,不需要额外的"心跳上报"逻辑。
- Consul 需要 agent 部署、Nacos 偏 Java 生态,ZooKeeper 客户端在 Python 上的体验不如 etcd——为了"不太复杂",etcd 是最直球的选择。

### 2. 为什么选同步 `etcd3` 而不是 `aetcd3`

- 现有 `client.py` 就是阻塞 socket + 在 `to_thread` 里跑。继续用同步客户端 + 后台线程,**心智模型一致**;不引入"哪里是 async、哪里是 sync"的混乱。
- watcher 回调可以直接调用 `ring.add_node`(线程安全:`ConsistentHashRing` 内部数据结构在增删时虽不是原子的,但访问极少冲突,首版加一把 `threading.Lock` 即可)。
- 学习成本 etcd3 > aetcd3,但要在 asyncio 里跨线程调用回调,反而 sync 库更直接。

### 3. 为什么用 `prometheus_client` 自带 HTTP server,不复用 cache server 的 asyncio 端口

- `prometheus_client.start_http_server` 内部跑独立 daemon 线程 + `BaseHTTPServer`,**零配置**。
- 复用 cache server 端口意味着要在 RESP 协议旁路上加一个 HTTP 子协议解析,这违背 v2 "协议干净、可 nc 调试"的设计原则,不值。
- 多开一个 9101 端口在学习项目里完全可接受;Prometheus 抓取目标列表里多写几条而已。

### 4. 为什么 metrics 模块要做"导入失败降级为 no-op"

- v2 的卖点之一是"零运行时依赖,只标准库就能跑"。本次变更要**完全向后兼容**:已写好的测试与 demo 一行不改、不装 `prometheus_client` 也要跑通。
- no-op 实现把所有 metric API 桩成空函数,调用方代码里就**不需要任何 `if metrics_enabled` 分支**,埋点写法跟"真启用"时一模一样——这也是大型项目里常见的 feature flag 处理模式,顺便学一下。

### 5. 为什么 Prometheus 用 `static_configs` 而不是 `etcd_sd_config`

- 学习目标是"先把链路打通",`static_configs` 在 `prometheus.yml` 里列三个 target 是 5 行配置的事。
- `etcd_sd_config` 是更"工业级"的做法,但需要 Prometheus 容器能访问宿主机 etcd 端口、key schema 也要按 Prom 规范来。**留作后续优化**,在 design.md 里登记即可。

### 6. 为什么 Grafana 要用 provisioning

- 让仪表盘进 git。`grafana/dashboards/distcache.json` 是版本化产物,任何人 clone 仓库 `docker compose up` 后看到的图都一致。
- 等价于把"前端可视化产物"也变成代码——这是 Observability as Code 的核心姿势,值得学。

### 7. 为什么 etcd 选主与自动 failover 不在本版

- 自动 failover 需要决定:谁是 master、什么时候切、客户端怎么发现。涉及 `etcd3.Lock` 选主 + 复制流断点 + 客户端 watch master 切换三处协作,**复杂度跳一个量级**。
- v2 已经把"手动 promote"做扎实,先把发现 + 监控接通,把"挂掉 → Grafana 上看到 lag 飙升 → 手动 promote → lag 回落"这条故事讲完,**比上自动 failover 更有学习价值**。
