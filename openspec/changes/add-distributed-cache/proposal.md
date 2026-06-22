## Why

我们需要一个学习导向的分布式缓存系统(mini Redis / mini Memcached Cluster),用来真正理解缓存淘汰算法、分布式哈希、网络协议设计与数据一致性。目标不是生产级系统,而是让每个核心概念都被**真实现**,而非做成跳过精髓的玩具版。

## What Changes

- 新增手写 LRU 缓存:哈希表 + 双向链表实现 O(1) 读写与淘汰(**不使用 `OrderedDict`**),并叠加 TTL(惰性删除 + 定期采样扫描)。
- 新增基于 `asyncio` 的 TCP 服务器:每连接一协程,支持并发客户端。
- 新增 RESP 风格文本协议:正确分帧解决粘包,可用 `nc`/`telnet` 调试。
- 新增一致性哈希环 + 虚拟节点,客户端侧路由(client 自行计算 key 落点)。
- 新增简化版主从复制:异步复制、master 以 effect batch 转发给 slave、重连一律全量同步、手动故障切换。
- 协议为双模式请求(inline 便于 `nc` 调试 + RESP array 承载二进制/含空格值),响应统一 RESP。
- 部署模型:单机多端口模拟多节点。

明确划在范围外(非破坏性,仅声明边界):Raft/Paxos 等共识、自动选主/自动故障切换、持久化(AOF/RDB)、集群内部 gossip、客户端重定向(MOVED)、多进程/`SO_REUSEPORT`、增删节点的数据迁移、**读写分离**、**复制 partial sync / backlog**。

## Capabilities

### New Capabilities
- `lru-cache`: 单机内存 KV 存储,O(1) LRU 淘汰 + TTL 过期语义。
- `cache-protocol`: RESP 风格请求/响应协议的编解码与分帧(粘包/拆包)。
- `cache-server`: 基于 `asyncio` 的 TCP 服务器,接受连接并把命令路由到本地存储。
- `consistent-hashing`: 带虚拟节点的一致性哈希环与客户端路由;增删节点的 transient-miss 语义。
- `replication`: master→slave 异步复制、effect log 语义、重连全量同步、手动切换、可选读写分离。

### Modified Capabilities
<!-- 无:这是全新项目,不存在需要修改的既有 capability。 -->

## Impact

- 新建代码模块(参考结构):`lru.py`、`protocol.py`、`server.py`、`hashring.py`、`client.py`、`replication.py`。
- 依赖:仅标准库(`asyncio`、`socket`),无第三方运行时依赖;测试可选用 `pytest`。
- 运行方式:本地启动多个端口实例(如 `localhost:7001/7002/...`)模拟分片与主从拓扑。
