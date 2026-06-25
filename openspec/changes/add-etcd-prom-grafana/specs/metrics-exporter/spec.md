## ADDED Requirements

### Requirement: 节点暴露 Prometheus 文本格式指标端点

每个 `Node` SHALL 通过 HTTP 暴露一个 `/metrics` 端点,响应内容 MUST 符合 Prometheus 文本格式规范(`text/plain; version=0.0.4`),默认端口 `9101`,可通过 `--metrics-port` 配置;传入 `0` MUST 禁用该端点。

#### Scenario: 默认端口可被抓取

- **GIVEN** 启动 `serve.py --port 7001`(未显式指定 metrics 端口)
- **WHEN** 执行 `curl -s http://127.0.0.1:9101/metrics`
- **THEN** HTTP 状态码 MUST 为 200,Content-Type MUST 是 `text/plain; version=0.0.4; charset=utf-8`,响应体中 MUST 出现 `# HELP distcache_ops_total ...` 与 `# TYPE distcache_ops_total counter` 元行

#### Scenario: 自定义端口

- **GIVEN** 启动 `serve.py --port 7001 --metrics-port 9201`
- **WHEN** 访问 `http://127.0.0.1:9201/metrics`
- **THEN** MUST 返回 200 且包含指标;`http://127.0.0.1:9101/metrics` MUST 连接失败

#### Scenario: 显式禁用 metrics(异常/边界)

- **GIVEN** 启动 `serve.py --port 7001 --metrics-port 0`
- **WHEN** 访问 `http://127.0.0.1:9101/metrics`
- **THEN** 连接 MUST 失败(端口未监听),且节点本身的 RESP 服务 MUST 正常工作

### Requirement: 关键指标家族完整暴露

节点 SHALL 暴露以下指标家族,语义如下,且 label 集合 MUST 与表中一致:

- `distcache_ops_total{cmd,result}` (Counter):命令计数;`cmd` ∈ {GET, SET, SETEX, EXPIRE, DEL, PING, ROLE};`result` ∈ {hit, miss, ok, error, readonly}。
- `distcache_op_latency_seconds{cmd}` (Histogram):单命令处理耗时,buckets MUST 至少包含 `[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1]`。
- `distcache_cache_size` (Gauge):当前 key 数;周期采样,采样间隔 SHOULD 为 1s。
- `distcache_cache_maxsize` (Gauge):容量上限;启动时 set 一次。
- `distcache_evictions_total` (Counter):LRU 淘汰次数。
- `distcache_replication_offset{role}` (Gauge):master 侧上报 `master.offset`、slave 侧上报 `applied_offset`。
- `distcache_replication_lag` (Gauge):slave 侧上报 `master_offset - applied_offset`,master 侧固定为 `0`。
- `distcache_role` (Gauge):`1` 表示 master,`0` 表示 slave;`promote()` 时 MUST 即时翻转。

#### Scenario: SET 之后 ops_total 与 cache_size 同步更新

- **GIVEN** 节点已启动,`distcache_ops_total{cmd="SET",result="ok"}` 当前为 `N`,`distcache_cache_size` 为 `S`
- **WHEN** 客户端发起 1 次 `SET key value` 且节点为 master
- **THEN** 在 1s 内,`distcache_ops_total{cmd="SET",result="ok"}` MUST 变为 `N+1`,`distcache_cache_size` MUST ≥ `S`(若 key 不存在则增加 1,若覆盖则不变)

#### Scenario: GET miss 与 hit 分别计数

- **GIVEN** 节点存在 key `a`,不存在 key `b`
- **WHEN** 客户端发起 `GET a` 与 `GET b` 各 1 次
- **THEN** `distcache_ops_total{cmd="GET",result="hit"}` MUST 增加 1,`distcache_ops_total{cmd="GET",result="miss"}` MUST 增加 1

#### Scenario: 触发 LRU 淘汰时 evictions_total 递增

- **GIVEN** master 节点 `maxsize=3`,已存 3 个 key
- **WHEN** 客户端写入第 4 个 key
- **THEN** `distcache_evictions_total` MUST 增加 1,`distcache_cache_size` MUST 仍为 3

#### Scenario: 从角色 SET 返回 readonly 时被正确归类

- **GIVEN** 节点 role=slave 且正在跟随 master
- **WHEN** 客户端向 slave 发送 `SET k v`
- **THEN** `distcache_ops_total{cmd="SET",result="readonly"}` MUST 增加 1,而不计入 `result=ok` 或 `result=error`

#### Scenario: 复制 lag 在持续写入时大于 0,空闲后回归 0

- **GIVEN** master 正在以高频接收 `SET`,slave 在追赶
- **WHEN** 在 master 上每秒发 1000 次 SET 持续 5s,然后停止 3s
- **THEN** 5s 内 `distcache_replication_lag` 在 slave 侧 MUST 可观察到 `> 0`;停止后 3s 内 MUST 回到 `0`

#### Scenario: promote 后 role gauge 立即翻转

- **GIVEN** 节点 role=slave,`distcache_role=0`
- **WHEN** 调用 `node.promote()`
- **THEN** 下一次抓取 MUST 看到 `distcache_role=1`,且 `distcache_replication_lag` MUST 不再继续上报

### Requirement: 缺少可选依赖时的优雅降级

`distcache/metrics.py` 在 `prometheus_client` 未安装的环境下 SHALL 提供 no-op 实现,使所有埋点站点的 API 调用(`.labels(...)`, `.inc()`, `.set()`, `.observe()`, `.time()` 上下文管理器)都不抛异常;`metrics.start(port)` 在 no-op 状态下 SHALL 是空函数。

#### Scenario: 未安装 prometheus_client 时全套测试通过

- **GIVEN** 运行环境只有 Python 3.9+ 标准库 + `pytest`,**未安装** `prometheus_client`
- **WHEN** 执行 `python3 -m pytest -q`
- **THEN** 全部历史测试用例与新增非 metrics 用例 MUST 通过,且 MUST 无任何 `ImportError`

#### Scenario: 未安装 prometheus_client 时 metrics 端口不监听(异常/边界)

- **GIVEN** 未安装 `prometheus_client`
- **WHEN** 启动 `serve.py --port 7001 --metrics-port 9101`
- **THEN** 进程 MUST 正常运行 RESP 服务,但 `:9101/metrics` MUST 连接失败,且 stderr SHOULD 输出一条 warning 提示"prometheus_client not installed; /metrics disabled"
