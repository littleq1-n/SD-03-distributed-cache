## ADDED Requirements

### Requirement: 节点向 etcd 注册自身

`Node` 启动后 SHALL 能够把自身的 `host:port` 与元信息(`role`、可选 `shard`)以 `/distcache/nodes/<host>:<port>` 为 key 写入 etcd,并 MUST 绑定一个 lease 用于自动失活;节点 `stop()` 时 MUST 主动 revoke 该 lease。

#### Scenario: 正常注册可见

- **GIVEN** etcd 已在 `127.0.0.1:2379` 运行,且 `/distcache/nodes/` 前缀下无任何 key
- **WHEN** 启动 `serve.py --port 7001 --etcd 127.0.0.1:2379 --role master`
- **THEN** `etcdctl get --prefix /distcache/nodes/` 返回恰好一条 key `/distcache/nodes/127.0.0.1:7001`,value JSON 包含 `host=127.0.0.1`、`port=7001`、`role=master`

#### Scenario: lease 续租保活

- **GIVEN** 节点已注册,lease TTL=10s、keepalive 间隔=3s
- **WHEN** 节点持续运行 30s,且未调用 `stop()`
- **THEN** 该 key 在整段时间内 MUST 持续存在(每次 `etcdctl get` 都能命中)

#### Scenario: 进程崩溃后条目自动消失(异常路径)

- **GIVEN** 节点已注册,lease TTL=10s
- **WHEN** 对节点进程发送 `SIGKILL`(无法走 `deregister()`)
- **THEN** 在 lease TTL 到期(≤10s)内,etcd 中该 key MUST 自动消失

#### Scenario: 正常退出立即注销

- **GIVEN** 节点已注册并正常运行
- **WHEN** 进程收到 `SIGINT` 走 `Node.stop()` 路径
- **THEN** `deregister()` MUST 主动 revoke lease,etcd 中该 key 在 1s 内消失,不等待 TTL

### Requirement: 客户端通过 etcd 发现节点并维护哈希环

`DistributedCacheClient` SHALL 接受可选构造参数 `etcd_endpoints: list[str]`。当该参数给定时:

- 客户端 MUST 在初始化阶段从 etcd 拉取 `/distcache/nodes/` 前缀下的全部节点,**用这些节点构建初始哈希环**(若同时传了 `nodes` 静态列表,以 etcd 结果为准)。
- 客户端 MUST 持续 watch 该前缀,新增节点时 MUST 调用 `add_node`,删除节点时 MUST 调用 `remove_node`,以维护一致性哈希环。
- 客户端的 `close()` MUST 关闭 watcher,后台线程能正常退出。
- 当 `etcd_endpoints` 未给定时,客户端行为 MUST 与 v2 完全一致(仅使用 `nodes` 参数,不引入任何 etcd 调用)。

#### Scenario: 启动时从 etcd 加载节点

- **GIVEN** etcd 中 `/distcache/nodes/` 下已存在 3 条 key(端口 7001/7002/7003)
- **WHEN** 实例化 `DistributedCacheClient(etcd_endpoints=["127.0.0.1:2379"])`
- **THEN** `client.ring.nodes` 集合 MUST 恰好等于这 3 个 `host:port`,且能正常 `set`/`get`

#### Scenario: 节点上线后自动加入环

- **GIVEN** 客户端已基于 etcd 启动,当前环包含 2 个节点
- **WHEN** 在 etcd 中新增 `/distcache/nodes/127.0.0.1:7003`
- **THEN** ≤ 2s 内 `client.ring.nodes` MUST 包含 `127.0.0.1:7003`,后续 key 路由可能落到该节点

#### Scenario: 节点离线后自动从环中剔除

- **GIVEN** 客户端已基于 etcd 启动,环含节点 A/B/C
- **WHEN** A 的 lease 过期或被 `deregister()` 撤销,etcd 触发 DELETE 事件
- **THEN** ≤ 2s 内 `client.ring.nodes` MUST 不再包含 A;后续新写 key 不会被路由到 A

#### Scenario: etcd 不可达时初始化失败(异常路径)

- **GIVEN** etcd 服务未启动或地址错误
- **WHEN** 实例化 `DistributedCacheClient(etcd_endpoints=["127.0.0.1:2379"])`
- **THEN** 构造函数 MUST 在合理超时内(≤5s)抛出明确异常,而非长时间阻塞,且 MUST 不进入"半初始化"状态(`close()` 仍能安全调用)

### Requirement: 服务发现的依赖隔离

`distcache/discovery.py` SHALL 是核心模块以外**唯一**`import etcd3` 的位置;`distcache/server.py`、`distcache/client.py`、`distcache/lru.py`、`distcache/protocol.py`、`distcache/hashring.py`、`distcache/replication.py` MUST NOT 直接 `import etcd3`。

#### Scenario: 未安装 etcd3 时核心功能正常

- **GIVEN** 运行环境未安装 `etcd3`,仅有 Python 标准库 + `pytest`
- **WHEN** 不传 `--etcd` 启动 `serve.py`,运行 `python3 demo.py` 与 `pytest -q`
- **THEN** 全部命令 MUST 正常完成,无任何 `ImportError`;demo 与全部 v2 历史测试用例 MUST 全部通过

#### Scenario: 未安装 etcd3 但显式启用时清晰报错(异常路径)

- **GIVEN** 未安装 `etcd3`
- **WHEN** 启动 `serve.py --etcd 127.0.0.1:2379`
- **THEN** 进程 MUST 立即退出并打印明确提示(如 "etcd3 is required for --etcd; install with: pip install etcd3"),MUST NOT 进入 silent 跑下去的状态
