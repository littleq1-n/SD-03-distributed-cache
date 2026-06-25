# Proposal: 接入 etcd / Prometheus / Grafana(服务发现 + 可观测性)

## 背景

v2 版本已实现学习导向的分布式缓存核心(O(1) LRU + TTL、RESP 协议与分帧、一致性哈希分片、master→slave 异步复制 + 手动故障切换),但在工程演练上还有两个明显短板:

- **节点列表硬编码**:`DistributedCacheClient(nodes=[...])` 把节点直接传进来,节点上下线需要手工改配置并重启客户端;一致性哈希环不会自己变。
- **没有可观测性**:跑起来命中率多少、复制 lag 多大、有没有触发淘汰,全靠 `print` 看不到。没法在演示里"用图说话"。

这两个洞分别需要服务发现与监控栈来补。生态里最常用、最容易在学习项目里落地的组合就是 **etcd(服务发现)+ Prometheus(指标采集)+ Grafana(可视化)**。本变更目标是把它们接进现有 v2,不动核心算法、不破坏"零运行时依赖也能跑"的特性。

## 目标

- 节点启动时自动注册到 etcd,挂掉后由 lease 过期自动剔除,**不再依赖客户端硬编码节点列表**。
- 客户端通过 etcd `watch` 感知节点增删,**自动 `add_node` / `remove_node`** 维护一致性哈希环。
- 每个 `Node` 暴露 `/metrics` 端点(Prometheus 文本格式),覆盖 QPS、命中率、延迟、LRU 淘汰、复制 lag、角色等核心指标。
- 提供 `deploy/docker-compose.yml` 一键起 etcd + Prometheus + Grafana,Grafana 数据源与首版仪表盘通过 provisioning 自动加载。
- 不破坏现有特性:**没装 etcd / Prometheus 也能照常跑 v2 的所有命令与测试**(可选依赖,优雅降级)。

## 范围

### 包含

- 节点注册:`Node` 启动时把 `host:port` + 元信息(role、shard)注册到 etcd,带 lease 自动续租。
- 节点发现:`DistributedCacheClient` 接受可选 `etcd_endpoints=[...]`,首次拉全量节点 + 持续 watch。
- 指标埋点:`distcache_ops_total`、`distcache_op_latency_seconds`、`distcache_cache_size`、`distcache_evictions_total`、`distcache_replication_offset`、`distcache_replication_lag`、`distcache_role`。
- HTTP 指标端口:复用 `prometheus_client` 自带 `start_http_server`,默认 9101(可配置)。
- 监控栈:`deploy/docker-compose.yml`(etcd + Prometheus + Grafana 三件套),Prometheus 抓取配置 + Grafana 数据源与首版仪表盘自动 provisioning。
- 首版仪表盘:QPS、命中率、P99 延迟、复制 lag 四张图。
- 优雅降级:`distcache/metrics.py` 在导入失败时降级为 no-op,不强制要求安装 `prometheus_client`;`distcache/discovery.py` 在未传 `etcd_endpoints` 时不启用。

### 不包含

- **etcd 选主驱动的自动 failover**:本版仍保留手动 `promote()`;后续可加 `/distcache/shards/<id>/master` campaign。
- TLS / 鉴权:etcd 与 `/metrics` 端口不开 TLS、不配 auth。
- Prometheus HA / 远端存储 / Alertmanager 告警规则。
- Grafana 用户权限、多仪表盘、模板变量等高级用法。
- 数据迁移、slot 重新分配:与原 v2 保持一致(transient miss 由 LRU/TTL 自然消化)。
- 用 Prometheus `etcd_sd_config` 做服务发现的目标抓取:首版用 static_configs;留作后续。

## 验收标准

- [ ] 启动 `serve.py --etcd 127.0.0.1:2379 --port 7001` 后,`etcdctl get --prefix /distcache/nodes/` 能看到该节点条目,kill 进程后 ≤ lease TTL(默认 10s)条目消失。
- [ ] `DistributedCacheClient(etcd_endpoints=[...])` 在不传 `nodes` 的前提下能从 etcd 拉到节点列表并完成 `set/get`;新节点上线后 ≤ 2s 客户端环自动包含它,kill 节点后客户端不再向该节点路由。
- [ ] 访问 `http://127.0.0.1:9101/metrics` 能拿到 Prometheus 文本格式指标,且至少包含本提案列举的 7 个指标家族。
- [ ] 在仅安装标准库 + `pytest` 的环境下,`python3 -m pytest -q` 全部通过(可选依赖未装时所有指标埋点降级为 no-op,功能不受影响)。
- [ ] 执行 `cd deploy && docker compose up -d`,浏览器打开 `http://localhost:3000` 默认仪表盘能看到 QPS、命中率、P99 延迟、复制 lag 四张图随负载实时变化。
- [ ] 现有 v2 集成测试 `tests/test_*.py` 一行不动也全部通过(向后兼容)。

## 技术栈

- 语言:Python 3.9+(与 v2 一致,不升级)。
- 新增运行时依赖(均为可选,不装可降级):
  - `etcd3>=0.12`(同步客户端,跑在后台线程,跟 `client.py` 现有的阻塞 socket 风格一致)
  - `prometheus_client>=0.20`(自带 wsgi/简易 http server,无须额外 web 框架)
- 测试依赖:沿用 `pytest>=7.0`。
- 监控栈(只在容器里跑,Python 代码层不依赖):`quay.io/coreos/etcd:v3.5.13`、`prom/prometheus:v2.54.1`、`grafana/grafana:11.2.0`。
- 运行环境:本地多端口模拟多节点(同 v2);监控栈由 `docker compose` 起一组容器,Python 进程仍跑在宿主机上,通过 `host.docker.internal` 被 Prometheus 抓取。
