# Tasks: 接入 etcd / Prometheus / Grafana

## Phase 1: 基础架构

- [ ] Task 1.1: 在 `requirements.txt` 中追加可选依赖 `etcd3>=0.12`、`prometheus_client>=0.20`,并在注释中标明"未安装时降级运行"。
- [ ] Task 1.2: 新增 `deploy/docker-compose.yml`,定义 `etcd`、`prometheus`、`grafana` 三个 service,统一暴露端口(2379 / 9090 / 3000)。
- [ ] Task 1.3: 新增 `deploy/prometheus.yml`,`scrape_interval=5s`,`static_configs` 列 `host.docker.internal:9101/9102/9103` 三个目标,job_name=`distcache`。
- [ ] Task 1.4: 新增 `deploy/grafana/provisioning/datasources/ds.yml`,声明默认 Prometheus 数据源 `http://prometheus:9090`。
- [ ] Task 1.5: 新增 `deploy/grafana/provisioning/dashboards/dash.yml`,把 `/var/lib/grafana/dashboards` 设为自动加载目录。
- [ ] Task 1.6: 验收:`cd deploy && docker compose up -d`,三个容器 healthy,浏览器访问 `:9090` 与 `:3000` 均能登录。

## Phase 2: 核心功能

### 2.A 指标暴露(Prometheus)

- [ ] Task 2.1: 新增 `distcache/metrics.py`,定义全部指标对象(`ops_total`、`op_latency`、`cache_size`、`cache_maxsize`、`evictions_total`、`repl_offset`、`repl_lag`、`role_gauge`)。
- [ ] Task 2.2: 在 `metrics.py` 顶层用 `try: from prometheus_client import ... except ImportError:` 提供 no-op Counter/Gauge/Histogram 实现(支持 `.labels`/`.inc`/`.set`/`.observe`/`.time()` 上下文管理器)。
- [ ] Task 2.3: 实现 `metrics.start(port)`,真启用时调 `prometheus_client.start_http_server(port)`,no-op 时为空函数。
- [ ] Task 2.4: 在 `distcache/server.py` 的 `_dispatch(args)` 外层包 `with op_latency.labels(cmd).time():`,末尾按返回值类型 `ops_total.labels(cmd, result).inc()`(注意 `result` 要从返回的 RESP 字节判断,`+`→ok / `-`→error/readonly / `$-1`→miss / `$<n>`→hit)。
- [ ] Task 2.5: 在 `_do_set` 的 `evicted is not None` 分支补 `evictions_total.inc()`;`promote()` 末尾 `role_gauge.set(1)`。
- [ ] Task 2.6: 在 `Node.start()` 末尾起 `_metrics_sample_loop()`,每秒更新 `cache_size`、`repl_offset{role}`、`repl_lag`、`role_gauge`、`cache_maxsize`(maxsize 启动时 set 一次即可)。
- [ ] Task 2.7: 在 `serve.py` 加 `--metrics-port`(默认 9101,`0` 禁用),启动时调用 `metrics.start(...)`。
- [ ] Task 2.8: 手动验收:启动 3 个节点(端口 7001/7002/7003,metrics 端口 9101/9102/9103),用 `curl http://127.0.0.1:9101/metrics` 看到全部指标家族。

### 2.B 服务发现(etcd)

- [ ] Task 2.9: 新增 `distcache/discovery.py`,实现 `EtcdRegistry`:`register(host, port, meta, ttl=10)`(申请 lease + put + 后台线程 keepalive)、`deregister()`(revoke lease)。
- [ ] Task 2.10: 在同文件实现 `EtcdWatcher`:`list_nodes()`(get_prefix 全量)、`watch(on_add, on_remove)`(watch_prefix 后台线程,收到 PUT/DELETE 解析 key 并回调)、`stop()`。
- [ ] Task 2.11: `discovery.py` 在 `import etcd3` 失败时不报错,但实例化时给出清晰错误("install etcd3 to enable discovery");核心模块不感知该依赖。
- [ ] Task 2.12: 修改 `serve.py`:加 `--etcd <endpoints>`、`--shard <id>` 两个参数;`Node.start()` 之后若 `--etcd` 给定则 `EtcdRegistry.register(...)`;`stop()` 前 `deregister()`。
- [ ] Task 2.13: 修改 `distcache/client.py`:`DistributedCacheClient.__init__` 新增 `etcd_endpoints: list[str] | None = None`,给定时构造 `EtcdWatcher`、先 `list_nodes()` 初始化哈希环、再 `watch(on_add=self.add_node, on_remove=self.remove_node)`;`close()` 关闭 watcher。
- [ ] Task 2.14: 在 `ConsistentHashRing.add_node/remove_node` 加 `threading.Lock`(回调来自 watcher 后台线程,需线程安全)。
- [ ] Task 2.15: 手动验收:`etcdctl get --prefix /distcache/nodes/` 在三节点起来后能看到三条记录;`kill -9` 其一,≤10s 后该记录消失;客户端写入的新 key 不再路由到被 kill 的节点。

## Phase 3: 测试与优化

- [ ] Task 3.1: 编写 `tests/test_metrics.py`,验证 no-op 路径:不安装 `prometheus_client` 时,`_dispatch` / `_do_set` / `promote` 全部调用不报错,且 `metrics.start(0)` 是空操作。
- [ ] Task 3.2: 编写 `tests/test_metrics.py`(真启用路径,标记 `@pytest.mark.skipif(prometheus_client 未安装, reason=...)`):起一个 `Node`,执行若干 `SET/GET`,从 `prometheus_client.REGISTRY` 读出 `ops_total` 值,断言计数正确。
- [ ] Task 3.3: 编写 `tests/test_discovery.py`,用 `etcd3` 的 mock 或本地起 etcd 容器(`@pytest.mark.skipif(无 etcd, reason=...)`),验证 `EtcdRegistry` 注册后 `EtcdWatcher.list_nodes()` 能拉到、`deregister()` 后消失、watch 回调按 PUT/DELETE 触发 add/remove。
- [ ] Task 3.4: 在 Grafana UI 手画首版仪表盘(4 面板:QPS by cmd / 命中率 / P99 延迟 / 复制 lag),导出 JSON 落到 `deploy/grafana/dashboards/distcache.json`,提交版本控制。
- [ ] Task 3.5: 写一段端到端演练脚本 `demo_observability.py`:起 master+slave、客户端开 10 线程混合 SET/GET 压测 30s,期间 sleep 5s 后调用 `slave.promote()`、再让客户端继续写——目标是在 Grafana 上看到清晰的"复制 lag 上升 → promote 后归零"曲线。
- [ ] Task 3.6: 在 `README.md` 顶部"运行方式"小节后新增一节《可观测性 & 服务发现快速开始》:5 条命令把整套链路跑起来,附 Grafana 截图占位。
- [ ] Task 3.7: 跑 `pytest -q` 全套(含 v2 历史 41 个用例)在"未装可选依赖"和"装齐可选依赖"两种环境下都通过——证明向后兼容性。
- [ ] Task 3.8: 在 `openspec/specs/` 把本变更新增的 capability(`etcd-discovery`、`metrics-exporter`、`observability-stack`)归档到顶层 spec 目录(变更落地后由 OpenSpec 流程执行)。
