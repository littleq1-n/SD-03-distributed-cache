## ADDED Requirements

### Requirement: docker-compose 一键启动监控栈

仓库 SHALL 在 `deploy/` 目录下提供 `docker-compose.yml`,执行 `docker compose up -d` MUST 同时启动 `etcd`、`prometheus`、`grafana` 三个服务,且每个服务 MUST 暴露固定端口:`etcd:2379`、`prometheus:9090`、`grafana:3000`;镜像版本 MUST 在 compose 文件中固定(不使用 `latest`),保证可复现。

#### Scenario: 一键起栈三容器健康

- **GIVEN** 宿主机已安装 Docker 与 Docker Compose,且 `2379/9090/3000` 三个端口空闲
- **WHEN** 执行 `cd deploy && docker compose up -d`,等待 30s
- **THEN** `docker compose ps` MUST 显示 3 个 service 都处于 `running`,`curl -s http://localhost:2379/version` 返回 etcd 版本号,`curl -sf http://localhost:9090/-/ready` 与 `curl -sf http://localhost:3000/api/health` 都返回 200

#### Scenario: 端口冲突时合理失败(异常路径)

- **GIVEN** 宿主机已有进程占用 `3000` 端口
- **WHEN** 执行 `docker compose up -d`
- **THEN** Grafana 容器 MUST 启动失败,且 `docker compose logs grafana` MUST 包含端口绑定错误信息;其余两个容器不受影响

### Requirement: Prometheus 自动抓取节点指标

`deploy/prometheus.yml` SHALL 配置 `job_name: distcache`,`scrape_interval` MUST ≤ 10s,`static_configs.targets` MUST 至少列出 3 个节点目标(默认 `host.docker.internal:9101`、`:9102`、`:9103`);Prometheus 启动后 MUST 能在 `/targets` 页面看到这些目标,UP 状态由实际节点是否运行决定。

#### Scenario: 三节点运行时 Prometheus 全部 UP

- **GIVEN** 监控栈已通过 docker compose 启动,且 3 个 `distcache` 节点已在宿主机 7001/7002/7003 (metrics 端口 9101/9102/9103) 启动
- **WHEN** 浏览器打开 `http://localhost:9090/targets`
- **THEN** `distcache` job 下 MUST 显示 3 个 target,`State` 列 MUST 全部为 `UP`

#### Scenario: 节点离线后 Prometheus 标记 DOWN(异常路径)

- **GIVEN** 上述三节点全部 UP
- **WHEN** 关闭其中一个节点
- **THEN** ≤ 15s 内 Prometheus `/targets` 页面 MUST 把该 target 状态变为 `DOWN`,且 `up{job="distcache",instance="..."}` 指标 MUST 变为 `0`

### Requirement: Grafana 自动 provisioning 数据源与仪表盘

仓库 SHALL 通过 Grafana provisioning 机制在容器启动时自动完成:

- **数据源**:`deploy/grafana/provisioning/datasources/ds.yml` 声明一个默认 Prometheus 数据源,`url=http://prometheus:9090`。
- **仪表盘加载**:`deploy/grafana/provisioning/dashboards/dash.yml` 声明把 `/var/lib/grafana/dashboards` 目录作为 file provider 自动加载。
- **首版仪表盘**:`deploy/grafana/dashboards/distcache.json` MUST 至少包含 4 个面板,语义对应:
  1. **QPS by cmd** — `sum by (cmd) (rate(distcache_ops_total[1m]))`
  2. **GET 命中率** — `rate(distcache_ops_total{cmd="GET",result="hit"}[1m]) / clamp_min(rate(distcache_ops_total{cmd="GET"}[1m]), 1e-9)`
  3. **P99 延迟** — `histogram_quantile(0.99, sum by (le, cmd) (rate(distcache_op_latency_seconds_bucket[1m])))`
  4. **复制 lag** — `distcache_replication_lag`

#### Scenario: 默认登录后即可看到仪表盘

- **GIVEN** 监控栈刚启动,Grafana 首次起来
- **WHEN** 浏览器打开 `http://localhost:3000`,用 `admin/admin` 登录(初始密码可由环境变量改)
- **THEN** 不需要手动添加数据源,左侧菜单 Dashboards 下 MUST 出现一个名为 `distcache` 的仪表盘,打开后 4 个面板 MUST 加载成功(即使数值为 0 也算成功)

#### Scenario: 节点写入压力下面板数据实时更新

- **GIVEN** 仪表盘已打开,3 个节点已被 Prometheus 抓取
- **WHEN** 用客户端以 1000 QPS 持续写入 30s
- **THEN** QPS 面板 MUST 显示对应曲线随时间上升;P99 延迟面板 MUST 有非零数值;若同时有 slave,复制 lag 面板 MUST 可观察到短期峰值

#### Scenario: Grafana 配置不完整时降级(异常路径)

- **GIVEN** `dashboards/distcache.json` 文件被意外删除
- **WHEN** Grafana 容器启动
- **THEN** Grafana 服务 MUST 正常启动(不因仪表盘缺失而 crash);Dashboards 页面 MUST 显示空列表,而非 500 错误页;数据源仍 MUST 可用

### Requirement: 不依赖监控栈即可单独运行节点

`distcache/server.py`、`distcache/client.py`、`distcache/lru.py`、`distcache/protocol.py`、`distcache/hashring.py`、`distcache/replication.py`、`serve.py`、`demo.py` SHALL 在 **不启动监控栈** 的情况下保持与 v2 完全一致的行为;监控栈仅是 `deploy/` 下的可选附属物。

#### Scenario: 不起 docker compose 也能跑 demo

- **GIVEN** Docker 未安装,或未运行 `docker compose up`
- **WHEN** 直接 `python3 demo.py`(不传任何 etcd / metrics 参数)
- **THEN** demo MUST 完整跑通(分片 + 主从 + 手动故障切换全部演示成功),与 v2 输出一致
