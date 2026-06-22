## Context

第一版定位为学习导向的 mini Redis / mini Memcached Cluster。系统分三层正交组合:

```
单机存储层(LRU+TTL) ── 协议层(RESP/分帧) ── 网络层(asyncio TCP)
        │
        ├── 横向:一致性哈希分片(客户端路由)   → 扩容量
        └── 纵向:主从复制(异步)               → 扩可用性
```

约束:Python + `asyncio`,仅标准库;适度简化(每个概念真实现,但不碰生产级共识/持久化/自动 failover);单机多端口模拟多节点。

## Goals / Non-Goals

**Goals:**
- 手写 O(1) LRU(哈希表 + 双向链表)+ TTL 语义,真实现而非 `OrderedDict` 包装。
- RESP 风格协议,强制正确分帧(解决粘包),可 `nc` 调试。
- 一致性哈希 + 虚拟节点,数据分布均匀,增删节点只影响 ~1/N 的 key。
- 主从异步复制语义正确:顺序、淘汰/过期一致、重连可恢复。
- 单机多端口可演示完整分片 + 主从拓扑。

**Non-Goals:**
- 共识算法(Raft/Paxos)、自动选主、自动故障切换。
- 持久化(AOF/RDB)、集群 gossip、MOVED 重定向。
- 多进程 / `SO_REUSEPORT`。
- 增删节点时的数据迁移(明确不做,见 Decision 7)。
- 读写分离(读打 slave):本版不做。
- 复制 partial sync / backlog:本版不做,重连一律 full sync(见 Decision 6)。

## Decisions

### Decision 1 — 并发模型:`asyncio` 每连接一协程
选 `asyncio` 而非线程池。理由:绕开 GIL 在 IO 密集下的竞争,概念接近 goroutine,单线程事件循环让"应用 + 入复制队列"的临界区天然原子(协程仅在 `await` 处让出)。
- 备选:线程 + 锁(被 GIL 拖累,且需显式锁保护 LRU 链表);多进程(范围外)。

### Decision 2 — LRU:哈希表 + 双向链表,O(1)
`dict[key] → node`,`node` 在双向链表中;命中移到表头,淘汰从表尾。**不使用 `OrderedDict`**,因为该数据结构正是本项目核心学习价值。
- 备选:`OrderedDict`(跳过精髓);近似 LRU/分段锁(生产级,范围外)。

### Decision 3 — TTL:绝对过期时间 + 角色化的过期读取
存绝对 deadline(非相对 TTL)。过期读取分两种模式:
- **authoritative(主/单机)**:读到过期 key → miss + 删除(惰性);后台定期随机采样清理冷 key。
- **logical-miss(从)**:读到过期 key → miss 但**不删除**,等 master 的 `DEL`;从不跑采样删除。
- 关键:复制时必须下发**绝对时间**(`EXPIRE`/`SETEX` 在复制流改写为 `PEXPIREAT` 风格),否则 slave 计时起点不同会分叉。

### Decision 4 — 协议:双模式请求 + RESP 响应 + 显式分帧
请求双模式,解析器按首字节区分:`*` 开头 → RESP array(`*argc` + 每参数 `$len\r\n<bytes>`,承载二进制/含空格值,客户端库用);否则 → inline(空白切分,供 `nc`/`telnet` 人工调试,不承载二进制)。响应统一 RESP(`+`/`-`/`$len`/`$-1`)。解析器维护读缓冲,数据不足时等待(解决粘包/半包)。
- 备选:仅 `split(' ')`(无法处理含空格/二进制值);仅纯二进制(不可 `nc` 调试)。双模式同时满足可调试与正确性。

### Decision 5 — 复制核心契约:effect batch(灵魂决策)
复制流里流动的不是"用户命令",而是 master **应用后的效果**,且以 **batch** 为单位(一个 offset 对应一组原子效果):
- 写命令 → 转发;
- LRU 淘汰 → 合成 `DEL <key>`(与触发它的写**同属一个 batch**);
- TTL 过期删除 → 合成 `DEL <key>`;
- `EXPIRE`/`SETEX` → 改写为绝对时间的 `PEXPIREAT`。

**slave 是被动状态机,从不自主决策**:master 客户端写走会淘汰的 `set`;slave 回放走非淘汰的 `apply_repl_set`,**回放不触发本地淘汰**。master 是唯一真相源。这统一解决了淘汰一致(Q1)、过期一致(Q2)。

**顺序与原子性(Q3)**:每 slave 一条 TCP 长连接(字节有序);单写协程按本地应用顺序写入;"应用到 store(含淘汰)"与"effect batch 入队"之间无 `await`,故原子;每个 batch 带单调递增 offset;slave **以 batch 为单位原子回放**,offset 仅在整组成功后推进。

**复制同步性(应答时机)**:master 完成"本地应用 + effect batch 入队"后**立即返回客户端,不等 slave ACK**;无 slave 连接时写仍成功。可能丢失最后几条(主在推送前崩溃),可接受。

### Decision 6 — 重连恢复:一律全量同步(Q4)
本版不做 partial sync / backlog。slave 首连或重连一律 full sync:master 打快照(全部 k/v/绝对 deadline)并记录 `snapshot_offset` → 快照期间到达的 effect batch 缓冲住 → 发快照给 slave 替换整个 store 且置已应用 offset = `snapshot_offset` → 按 offset 顺序补放缓冲 batch → 转入正常流。
- 精髓在"边打快照边缓冲"的交接,不缓冲会丢快照生成期间的写。`snapshot_offset` 是快照与增量流的拼接点。

### Decision 7 — 一致性哈希:不做数据迁移(Q5)
一致性哈希(+ 虚拟节点)负责**最小化受影响 key 范围(~1/N)**;增删节点后,这部分 key 在新节点变成 transient cache miss,回源重建;旧节点上的孤儿副本靠 LRU/TTL 自然老化。因为缓存非真相源,miss 只是回一次源,数据不丢。明确不实现数据搬迁(需扫描/双读过渡,生产级)。

### Decision 8 — 故障切换:手动
master 挂了不自动选主;运维手动把某 slave 提升为 master 并切换客户端配置。把复制本身做扎实即可。

### Decision 9 — 读写分离:本版不做
读请求一律走 master。读写分离会引入 slave 落后的陈旧读与最终一致性复杂度,本学习版先聚焦把复制本身做正确,该特性明确移出范围。

## 模块划分(Modules)

### Module 1: `lru.py` — LRUCache
- 职责: O(1) LRU 存储 + TTL;主/单机用 authoritative,从用 logical。
- 接口: `get(key, now)`、`set(key, value, expire_at)`(返回被淘汰 key)、`apply_set(...)`(非淘汰回放)、`delete(key)`、`set_expire(key, expire_at)`、`sample_expired(now)`、`snapshot()`、`load_snapshot(items)`。

### Module 2: `protocol.py` — 协议编解码
- 职责: RESP 响应编码、RESP array 请求编码、双模式增量解析与分帧。
- 接口: `encode_simple/encode_error/encode_bulk/encode_command`、`Parser.feed/parse_one`、`decode_resp_array`。

### Module 3: `hashring.py` — ConsistentHashRing
- 职责: 带虚拟节点的一致性哈希环。
- 接口: `add_node(node)`、`remove_node(node)`、`get_node(key)`、`nodes`。

### Module 4: `client.py` — DistributedCacheClient
- 职责: 客户端侧一致性哈希路由 + 阻塞 socket 收发。
- 接口: `set/get/delete(key[, value])`、`route(key)`、`add_node/remove_node(host, port)`、`close()`。

### Module 5: `replication.py` — 复制
- 职责: master 推送 effect batch;slave 全量同步 + 原子回放。
- 接口: `MasterReplicator.append_batch(effects)`(同步、无 await)、`MasterReplicator.handle_replica(reader, writer)`;`ReplicaClient.start/stop/run`、`applied_offset`、`synced_event`;`effect_to_payload/payload_to_effect`。

### Module 6: `server.py` — Node
- 职责: asyncio TCP 节点,串起存储/协议/复制;master 接受写,slave 只读;手动切换。
- 接口: `start()`、`stop()`、`follow(host, port)`、`promote()`、内部 `_dispatch(args)`。

## 数据模型(Data Model)

- `_Node`(LRU 链表节点): `key`、`value`、`expire_at`(绝对秒,或 None)、`prev`、`next`。
- `LRUCache`: `_map: dict[key -> _Node]` + 双向链表(哨兵 `head`/`tail`,`head.next` 为 MRU,`tail.prev` 为 LRU)。
- `effect`(复制效果,元组): `("SET", key, value, pexpireat_ms)` / `("DEL", key)` / `("PEXPIREAT", key, ms)`;`ms=0` 表示无过期。
- `Batch`: `offset`(单调递增) + `effects: list`。
- 复制线格式: 控制行 `SNAPSHOT <snapshot_offset> <count>` / `BATCH <offset> <count>`,effect 帧 `E <len>\r\n<RESP array payload>\r\n`(长度前缀,可承载二进制)。

## Risks / Trade-offs

- [异步复制丢数据] master 推送前崩溃会丢最后几条写 → 文档化为已知取舍;需强一致时可后续加同步复制选项。
- [slave 容量 < master] 会存不下 master 下发的 effect(slave 又不自主淘汰)→ 约束 slave `maxsize` ≥ master,写入规格。
- [batch 半应用] 若 slave 只应用 batch 的一部分会破坏一致性 → 要求 batch 原子回放,offset 仅整组成功后推进。
- [重连频繁触发 full sync] 不做 backlog,网络抖动会重复全量同步 → 学习版可接受;快照体量小,后续可加 partial sync。
- [transient miss 风暴] 增删节点瞬间 ~1/N 回源 → 学习项目可接受;文档化为预期行为。
- [asyncio 临界区误用] 在"应用(含淘汰)+ batch 入队"间误加 `await` 会破坏顺序/原子 → 代码审查重点 + 注释标注临界区。
