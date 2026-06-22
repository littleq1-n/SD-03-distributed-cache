# Distributed cache system project
_Exported on 2026/6/23 at GMT+8 3:06:39 from Cursor (3.2.21)_

---

**User**

/opsx:explore 我想要实现#### SD-03: 分布式缓存系统
**难度**: ⭐⭐⭐  
**技术栈**: Python/Go

**核心功能**:
- LRU淘汰策略
- TCP服务器实现
- 自定义协议设计
- 一致性哈希分片
- 主从复制（简化版）

**学习要点**:
- 缓存淘汰算法
- 分布式哈希
- 网络协议设计
- 数据一致性

---

---

**Cursor**

I'm in **explore mode** — let's think through this distributed cache system together. No code yet, just exploring the shape of the problem. First let me understand your current workspace context.

空项目,一张白纸 — 正好从头探索。我们一起把这个分布式缓存系统的形状摸清楚。先不写代码,先把脑子里的架构和那些"岔路口"理清楚。

## 这东西本质上是什么

你列的五个功能拼起来,其实是一个 **mini-Redis / mini-Memcached**:单机是带淘汰的 KV 存储,多机是分片 + 复制。我把它拆成两层来看会更清楚:

```
┌──────────────────────────────────────────────────────────────┐
│                       客户端 Client                            │
│   知道所有节点 + 一致性哈希环 → 自己算 key 落在哪个节点         │
└───────────────┬──────────────────────────┬───────────────────┘
                │ 自定义协议 (TCP)          │
                ▼                          ▼
        ┌───────────────┐          ┌───────────────┐
        │   Node A       │          │   Node B       │   ← 分片层(横向)
        │  (master)      │          │  (master)      │
        │ ┌───────────┐  │          │ ┌───────────┐  │
        │ │ LRU Cache │  │          │ │ LRU Cache │  │   ← 单机存储层
        │ └───────────┘  │          │ └───────────┘  │
        └──────┬────────┘          └──────┬────────┘
               │ 复制                      │ 复制
               ▼                          ▼
        ┌───────────────┐          ┌───────────────┐
        │  Node A'       │          │  Node B'       │   ← 副本层(纵向)
        │  (slave)       │          │  (slave)       │
        └───────────────┘          └───────────────┘
```

横轴是**一致性哈希分片**(扩容量),纵轴是**主从复制**(扩可用性)。这两个维度是正交的,可以分开实现、分开测试。

## 五个功能里,真正有"设计含量"的点

| 功能 | 简单做法 | 但有意思的地方在于… |
|------|---------|---------------------|
| LRU 淘汰 | `OrderedDict` | 怎么做到 O(1)?哈希表 + 双向链表的经典组合。要不要支持 TTL 过期(那就变成 LRU+惰性删除了)? |
| TCP 服务器 | 阻塞 socket | 单线程够吗?要不要 `asyncio`/goroutine 处理并发连接?粘包怎么办? |
| 自定义协议 | 文本行协议 | 文本(像 Redis RESP)还是二进制(带长度前缀)?这直接决定了粘包/拆包的复杂度 |
| 一致性哈希 | 取模 `hash(key) % N` | 取模在扩缩容时几乎全部 key 都要搬家;一致性哈希环 + 虚拟节点才是重点 |
| 主从复制 | 写完同步转发 | 同步还是异步?主挂了谁来切?这里"简化版"的边界要划清楚 |

最容易被低估的两个:**协议设计**(粘包问题会贯穿始终)和**一致性哈希的虚拟节点**(没有虚拟节点的话数据分布会很不均)。

## 几个关键的"岔路口",想先和你对一下

**1. Python 还是 Go?** 这个选择会改变整个项目的味道:

```
Python                          Go
─────────                       ─────────
+ 写得快,LRU/哈希逻辑清爽       + goroutine 天生适合每连接一协程
+ 适合"讲清楚算法"             + 真正的并发,性能像样
- GIL,并发靠 asyncio,稍绕     + 二进制协议 + bytes 处理顺手
- 性能不是强项                 - 代码量略大,样板多
适合:重点在"理解算法和架构"    适合:重点在"做出能跑的并发系统"
```

**2. 这是"学习项目"还是"要尽量像真东西"?** 你的学习要点写的是算法/分布式哲学,所以我猜偏前者 —— 那我们可以大胆简化(比如复制用同步、不做故障自动切换),把精力放在把每个概念讲透。你怎么想?

**3. 分片的"路由"放在哪?** 客户端算(像图里那样,简单)、还是任意节点都能转发请求(像 Redis Cluster 的 MOVED 重定向,复杂但更真实)?

**4. 主从复制的一致性边界** —— "简化版"我理解是:写主 → 主把命令转发给从 → 从应用。但要不要回答这几个问题:
- 写请求要等从确认吗(同步)还是主写完就返回(异步)?
- 主挂了之后从能不能自动顶上,还是手动?
- 读请求能不能打到从上(读写分离)?

---

在我们往下挖之前,帮我定几个方向就行:

1. **语言**:Python(偏讲清算法)还是 Go(偏做出并发系统)?
2. **定位**:学习导向(可大胆简化)还是尽量逼真?
3. **想优先吃透哪块**?是 LRU 算法、还是一致性哈希、还是复制/分布式那套?

定下来之后,我们可以接着把选中的部分画细,或者等你觉得想法成型了,我帮你落成一份 OpenSpec proposal(我现在还不会写代码,只帮你想清楚)。

---

**User**

1.语言使用python进行开发。2.可以简化但不要过于的简化。

---

**Cursor**

Python + "适度简化" —— 好,这就有了清晰的基调。`asyncio` 会是 Python 里最舒服的选择:每个连接一个协程,绕开了 GIL 在 IO 密集场景下的尴尬,概念上也接近 Go 的 goroutine。下面我把"适度简化"这条线在每个组件上具体画出来,你看看边界划得对不对。

## "适度简化" 的边界线

```
            过度简化 ←─────────────●─────────────→ 生产级(不需要)
                                  适度
LRU       OrderedDict 一把梭   哈希表+双向链表(O(1))   分段锁/近似LRU
TTL       不做                 惰性删除+定期扫描        时间轮
协议      split(' ')           RESP风格+正确分帧        二进制+pipeline
并发      单连接串行           asyncio 每连接协程       多进程+SO_REUSEPORT
一致性哈希 hash % N             哈希环+虚拟节点          带权重+故障感知
复制      不做                 异步复制+手动failover    Raft/共识
```

我建议每个组件都停在中间那个"适度"点。重点解释下几个:

**LRU 自己手写**,不用 `OrderedDict`。因为这个项目的学习价值有一半就在这里 —— 哈希表给你 O(1) 定位,双向链表给你 O(1) 移动到头部。用 `OrderedDict` 等于把最精彩的部分跳过了。再叠一层 **TTL**(惰性删除:读到过期 key 当 miss 处理;再加个定期采样扫描),这样它才像个真缓存。

**协议用 RESP 风格(类 Redis 的文本协议)**,而不是裸 `split`。理由是它可调试 —— 你能直接用 `nc`/`telnet` 敲命令测试,但又强制你正确处理**分帧/粘包**(用 `\r\n` 和长度声明来界定边界)。这是"不过度简化"最划算的一个点。大概长这样:

```
请求:  SET foo bar\r\n          响应:  +OK\r\n
       GET foo\r\n                     $3\r\nbar\r\n   (长度前缀+内容)
       GET missing\r\n                 $-1\r\n        (空)
```

## 整体模块结构(还是只画架构,不写代码)

```
distributed_cache/
│
├── 单机层 ──────────────────────────────
│   ├── lru.py          手写 LRU(哈希表 + 双向链表 + TTL)
│   └── server.py       asyncio TCP 服务器,每连接一协程
│
├── 协议层 ──────────────────────────────
│   └── protocol.py     RESP 编解码 + 分帧(解决粘包)
│
├── 分片层 ──────────────────────────────
│   ├── hashring.py     一致性哈希环 + 虚拟节点
│   └── client.py       客户端:算 key→节点,发命令
│
└── 复制层 ──────────────────────────────
    └── replication.py  master 转发写命令 → slave 应用
```

数据流一眼看全:

```
client.set("foo","bar")
      │ 1. hashring 算出 foo 落在 Node A
      ▼
   Node A (master) ──2. 写本地 LRU──▶ 返回 +OK
      │
      │ 3. 异步把 "SET foo bar" 转发给 slave
      ▼
   Node A' (slave) ──应用同样的写──▶ (不回客户端)
```

## 我建议的搭建顺序(从内到外,每步都能独立测)

```
① LRU(纯内存,写单元测试)        ← 最先,独立,有成就感
② 协议编解码(喂字节,验证分帧)    ← 独立,可单测
③ asyncio 服务器(①+②,nc 能连)  ← 第一个"活的"东西
④ 一致性哈希环(纯算法,看分布)    ← 独立,可单测
⑤ 客户端 + 多节点分片(③+④)     ← 第一个"分布式"时刻
⑥ 主从复制(在③基础上加转发)     ← 最后,最有挑战
```

①②④ 都是纯逻辑、可独立单测,适合先做;③⑤⑥ 才涉及网络和多进程联调。

---

剩下几个还没定的小岔路,定完我就能帮你落成一份 OpenSpec proposal:

1. **复制的同步性**:写主后**立即返回**(异步复制,主挂了可能丢最后几条)还是**等 slave 确认再返回**(同步,慢但不丢)?适度简化我倾向**异步**,你认同吗?
2. **故障切换**:slave 能不能自动顶上?适度简化我建议**手动**(挂了就挂了,不做自动选主),只把复制本身做扎实。行不行?
3. **读写分离**:读请求允许打到 slave 吗?这个加上不难,而且能体现"最终一致性"的味道 —— 要不要纳入?
4. **多节点怎么跑**:同一台机器开多个端口(`localhost:7001/7002/...`)来模拟多节点,够吗?还是你想真的跨机器?

我的默认建议:**异步复制 + 手动切换 + 单机多端口模拟**,读写分离作为可选加分项。你拍个板,或者就说"按你建议的来",我就把这些整理成正式的变更提案。

---

**User**

按你的默认建议来：Python + asyncio + 适度简化版分布式缓存系统。

我想把第一版定位成学习导向的 mini Redis / mini Memcached Cluster，不追求生产级，但每个核心概念都要真正实现，而不是只做玩具版。

---

**Cursor**

方向定了,我把所有决策"锁死"成一张表,这就是第一版的契约:

## 决策锁定 — mini Redis/Memcached Cluster (学习导向 · 真实现非玩具)

| 维度 | 第一版定案 |
|------|-----------|
| 语言/并发 | Python + `asyncio`,每连接一协程 |
| LRU | 手写 哈希表 + 双向链表,O(1);**不用 `OrderedDict`** |
| TTL | 惰性删除 + 定期采样扫描 |
| 协议 | RESP 风格文本协议,正确分帧(`nc` 可调试) |
| 分片 | 一致性哈希环 + 虚拟节点,**客户端路由** |
| 复制 | 异步复制(主写完即返回,转发命令给 slave) |
| 故障切换 | 手动(不做自动选主) |
| 读写分离 | 可选加分项(读可打 slave,体现最终一致性) |
| 部署 | 单机多端口模拟多节点 |

核心原则:**每个概念都真实现**(LRU 链表、哈希环虚拟节点、分帧、复制转发都不偷工),但**不碰生产级的那一圈**(共识、自动 failover、时间轮、多进程)。

明确划在范围外(避免范围蔓延):Raft/Paxos、自动选主、持久化(AOF/RDB)、集群内部 gossip、客户端重定向(MOVED)、多进程/SO_REUSEPORT。

---

想法已经成型了。下一步我建议把它落成一份正式的 OpenSpec **变更提案**(proposal + specs + tasks),这样后面 `/opsx:apply` 实现时有据可依。

建议的变更名:**`add-distributed-cache`**

要我现在就创建这份提案吗?如果要,我会生成:
- `proposal.md` — 为什么做、范围、上面这张决策表
- `specs/` — 按能力拆分(`lru-cache` / `cache-protocol` / `consistent-hashing` / `replication`)的需求规格
- `tasks.md` — 按 ①→⑥ 的搭建顺序拆成可勾选的任务

你说"创建"我就开始(这属于捕获思考、不是写代码)。或者如果还想再调整哪个决策,我们接着聊。

---

**User**

请进一步思考1. master 触发 LRU 淘汰后，evicted key 如何同步给 slave？
2. TTL 过期删除时，master 和 slave 如何保持一致？
3. 异步复制如何保证命令顺序？
4. slave 断线/重启后是否需要 full sync？
5. 一致性哈希增删节点后，是否明确不做数据迁移？

---

**Cursor**

这五个问题全部命中了同一个要害:**slave 到底是"自己也思考"还是"无脑复读 master"**。我的总答案是后者 —— **slave 是被动的状态机,master 是唯一真相源**。这一个原则能干净地解掉前四个问题。逐个拆:

## 1. LRU 淘汰的 key 怎么同步给 slave?

**原则**:淘汰也是一次"写",它必须进复制流。

master 因容量超限淘汰掉 key X 时,在内部把它**翻译成一条合成的 `DEL X`** 塞进复制流,slave 当普通命令应用。**slave 绝不自己跑淘汰逻辑。**

```
Master 容量满 → 淘汰 X
   ├─ 本地 LRU 删 X
   └─ 复制流追加 "DEL X"  ──────▶  Slave 应用 DEL X
```

**为什么 slave 不能自己淘汰?** 因为如果开了读写分离,slave 上的读会扰动它自己的 LRU 顺序,两边淘汰决策就会分叉。让 slave 被动接受 master 的 DEL,两边才永远一致。代价:slave 的 `maxsize` 必须 ≥ master(否则它存不下 master 让它存的东西)—— 这点写进规格即可。这跟真实 Redis 的行为完全一致。

## 2. TTL 过期,两边怎么一致?

这里有两个**真实现才会踩到的坑**,正好是学习价值所在:

**坑一:复制相对 TTL 会分叉。** 如果把 `EXPIRE foo 100` 原样转发,slave 的 100 秒从它收到命令的那一刻才开始算 → 两边过期时刻不同。**解法:存绝对过期时间戳,复制时也下发绝对时间**(就是 Redis 把 `EXPIRE`/`SETEX` 在复制流里改写成 `PEXPIREAT` 的原因)。

**坑二:谁来执行过期删除。** 还是那条原则 —— **slave 不主动删过期 key,等 master 的 `DEL`**。但 slave 在响应读请求时**要做逻辑判断**:发现 key 已过绝对时间 → 当 miss 返回(不删,只是不给)。这样在 master 的 DEL 还没到的窗口里,slave 也不会吐出 master 眼里已经死掉的数据。

```
Master 惰性/定期发现 foo 过期
   ├─ 本地删 foo
   └─ 复制 "DEL foo" ───▶ Slave 删 foo
                          (DEL 到达前,slave 读 foo:发现过绝对时间 → 返回 miss,但不删)
```

## 3. 异步复制怎么保证命令顺序?

**原则**:单条有序流 + 单调偏移量。

- 每个 slave 一条**长连接**,TCP 本身保证字节有序。
- master 用**一个写协程**把命令按"它本地应用的顺序"写进这条连接。关键技巧:**应用到本地 store 和追加到复制流之间不能有 `await`** —— asyncio 协程只在 `await` 处让出,所以"应用+入队"中间无 await 就是原子的,本地顺序 == 复制顺序。
- 给每条复制命令一个**单调递增的 offset**,slave 记录"已应用到哪个 offset"。

```
client1: SET k v1 ─┐
client2: SET k v2 ─┤→ 单写协程按到达顺序: [#41 SET k v1][#42 SET k v2] → slave 顺序应用
                          (offset 单调递增,顺序天然保持)
```

这个 offset 不只是为了顺序 —— 它正好是第 4 题的钥匙。

## 4. slave 断线/重启要 full sync 吗?

**要,full sync 作为必备底线;partial sync 作为可选进阶**(复用第 3 题的 offset)。

```
Slave 重连,上报 last_offset
        │
master 判断:
   ├─ last_offset 还在我的 backlog 环形缓冲里? → 部分重同步(只补缺的命令)  [可选进阶]
   └─ 不在 / slave 是全新的?                  → 全量同步                     [必备]
                                                  ① master 打快照(全部 k/v/绝对deadline)
                                                  ② 快照期间到达的写命令缓冲住
                                                  ③ 发快照 → slave 替换整个 store
                                                  ④ 接着把缓冲的命令补上,转入正常流
```

**最该吃透的就是第 ②④ 步那个"边打快照边缓冲"的交接** —— 不缓冲就会丢掉快照生成期间的写。这是 full sync 的精髓,建议必做。partial sync(backlog 环形缓冲)概念漂亮但可后置。

## 5. 一致性哈希增删节点,明确不做数据迁移?

**明确不做 —— 而且这对缓存是正确选择,不是偷懒。** 但要把因果讲清楚,别混淆两件事:

```
一致性哈希的作用 = 减少"受影响的 key 范围"(只 ~1/N,而非取模的几乎全部)
不做迁移的后果   = 那 ~1/N 的 key 在新节点上变成 cache miss,自己回源重建;
                   旧节点上的副本成为孤儿,靠 LRU/TTL 自然老化淘汰
```

因为**缓存不是真相源**(背后还有数据库),miss 只是回一次源,数据不会丢。所以"不迁移"完全成立。要是做迁移,就得引入扫描、搬运、双读过渡的一整套协议 —— 那是生产级,明确划在范围外。

规格里写清这句即可:*增删节点会造成约 1/N 的 key 短暂变冷(transient miss),回源后自愈;不实现数据搬迁。*

---

## 这五条收敛出的"复制流契约"

把上面提炼一下,其实诞生了一条贯穿全局的设计主线,值得单独立条:

> **复制流里流动的不是"用户命令",而是 master 应用后的"效果命令"(effect log)**:
> 写 → 转发;淘汰 → `DEL`;过期 → `DEL`;`EXPIRE` → 改写成绝对时间的 `PEXPIREAT`。slave 只无脑回放 effect log,从不自主决策。

这一条是整个复制设计的灵魂,也是它和"玩具版"(直接转发用户命令)的本质区别。

---

这五个答案都是实打实的**设计决策**,正好该进 `design.md`。我建议把它们和之前那张决策表一起,落进 `add-distributed-cache` 提案里 —— 尤其上面这条"effect log"主线,会直接影响 `replication` 和 `lru-cache` 两块的规格写法。

要我现在创建提案(proposal + design + specs + tasks)并把这些决策写进去吗?还是你想再就某条往下挖(比如 partial sync 的 backlog 设计,或读写分离下 slave 的 LRU 行为)?

---

**User**

/opsx:propose

---

**Cursor**

The user wants to create the proposal now. Let me read the propose command to follow its workflow correctly.

The exploration crystallized into a clear proposal. Let me create the change scaffold using the change name we settled on.

Build order is clear: `proposal` → `design` + `specs` → `tasks`. Let me set up tracking and start with the proposal instructions.

Now I'll write the proposal following the template.

Proposal created. Now design and specs are unblocked. Let me get instructions for both.

Now I'll write the design doc, capturing all the consistency decisions we worked through.

Now I'll write the five capability specs. Starting with `lru-cache`.

All five specs written. Let me verify status and get tasks instructions.

Specs and design confirmed done. Now the tasks list, following the ①→⑥ build order we designed.

提案创建完成,4/4 artifacts 全部就绪。

## `add-distributed-cache` 已生成

位置:`openspec/changes/add-distributed-cache/`

| Artifact | 内容 |
|----------|------|
| `proposal.md` | 为什么做、范围、5 个 capability、明确的范围外清单 |
| `design.md` | 9 条关键设计决策,含我们深挖的 effect log 复制主线 + 五个一致性问题的定案 |
| `specs/` | 五份按能力拆分的可测规格 |
| `tasks.md` | 按 ①→⑥ 搭建顺序的可勾选任务清单 + 可选加分项 |

五份规格:

```
specs/
├── lru-cache/          O(1) LRU + 容量淘汰 + TTL 过期
├── cache-protocol/     RESP 编码 + 分帧/粘包
├── cache-server/       asyncio 并发服务器 + 写路径原子入队
├── consistent-hashing/ 哈希环+虚拟节点 + 客户端路由 + transient-miss
└── replication/        effect log + 异步顺序 + 全量同步 + 手动切换 + (可选)读写分离
```

我们探索阶段的几条核心结论都已固化进规格,后面实现时有据可依:

- **effect log 主线** → `replication` 里写成 normative requirement(淘汰/过期合成 `DEL`、相对 TTL 改写绝对时间、slave 被动回放)
- **写路径无 `await` 临界区** → `cache-server` 里作为顺序保证的硬约束
- **不做数据迁移** → `consistent-hashing` 里明确为 transient-miss 语义而非缺陷

下一步:运行 `/opsx:apply` 开始按 `tasks.md` 实现(那会进入写代码阶段,退出当前的 explore/propose)。如果想在动工前再调整某条决策或规格,现在改成本最低,跟我说就行。

---

**User**

请明晰下列问题:1. 明确协议到底是 RESP array，还是 RESP + inline command 双模式。
2. 区分 master/slave 的 TTL 行为：master 删除，slave 只逻辑 miss。
3. 区分 master set 和 slave apply_repl_set，slave 回放不得触发本地淘汰。
4. effect log 改成 effect batch：一个 offset 对应一组原子效果。
5. 明确返回客户端前必须完成本地应用 + effect 入队，但不等 slave ACK。
6. full sync 明确 snapshot_offset；如果不做 backlog，就写重连一律 full sync。
7. 可以不实现读写分离和 partial sync 

---

**Cursor**

这七条把模糊地带都收紧了,全部并入提案。先把定案讲清,再落到 artifacts 里。

**关键定案**
- **#1 协议**:双模式请求 —— **inline**(`SET foo bar\r\n`,给人/`nc` 用,值不能含空格/二进制)+ **RESP array**(`*3\r\n$3\r\nSET\r\n...`,客户端库用,支持任意字节);**响应统一 RESP**。
- **#4 effect batch**:一个 offset = 一组原子效果(例:一次 `SET` 触发淘汰 → `[SET k v, DEL evicted]` 同一 offset,slave 原子回放),避免崩溃时半应用。
- **#6**:既然不做 backlog/partial sync,**重连一律 full sync**,快照携带 `snapshot_offset`,之后从该 offset 续流。
- **#7**:读写分离 + partial sync 移出本版范围。

我来逐个改 artifact。

I'll rewrite the protocol spec to make the dual-mode request format explicit.

Now updating `lru-cache` to distinguish authoritative (delete) vs logical-miss (no delete) read modes.

Adding a non-evicting apply mode to the LRU primitive (needed for slave replay, #3).

Now `cache-server`: make explicit that responding to client requires local apply + effect enqueue, but not slave ACK (#5).

Now the big one — rewriting `replication` spec: effect batch + offset, master/slave apply distinction, full sync always with `snapshot_offset`, removing read-write split & partial sync.

Now updating `proposal.md` to drop read-write split & partial sync from scope.

Now updating `design.md` decisions to reflect all clarifications.

Finally, updating `tasks.md` — adjusting replication tasks and removing the dropped optional items.

Let me validate the change is still well-formed.

Validation passes. 七条澄清已全部并入提案。

## 已落定的修改

| # | 澄清点 | 落地位置 |
|---|--------|---------|
| 1 | 协议双模式:inline + RESP array,响应统一 RESP | `cache-protocol` 新增"双模式请求编码"+"RESP 响应编码"两条 requirement |
| 2 | master 删除 / slave 仅逻辑 miss | `lru-cache` 新增"过期读取的两种模式"(authoritative vs logical-miss);`replication` effect log 语义 |
| 3 | master `set` vs slave `apply_repl_set`,回放不淘汰 | `lru-cache` 加"复制回放写入不触发淘汰"场景;`replication` 新增"master 与 slave 的写应用区分" |
| 4 | effect log → effect batch,一个 offset = 一组原子效果 | `replication` 新增"effect batch 与 offset"requirement(含原子回放) |
| 5 | 返回前完成本地应用+effect 入队,但不等 ACK | `cache-server` 新增"写响应时机";`replication`"异步复制(应答不等待 ACK)" |
| 6 | 重连一律 full sync + 明确 `snapshot_offset` | `replication` 改为"重连一律全量同步",删除 partial sync/backlog |
| 7 | 不实现读写分离 / partial sync | 从 `proposal`/`design`/`tasks` 移出,列入 Non-Goals |

**几个值得点名的设计收紧:**

- **effect batch 的原子性**(#4)现在是硬约束 —— 一次 `SET` 触发淘汰产生的 `[SET k v, DEL evicted]` 同属一个 offset,slave 要么全应用要么全不应用,offset 仅在整组成功后推进。这堵住了崩溃时半应用导致主从分叉的口子。
- **角色化的过期读取**(#2)把"删不删"从 LRU 原语里分离出来:同一套存储,主用 authoritative 模式、从用 logical-miss 模式,逻辑很干净。
- **`snapshot_offset`**(#6)成了快照与增量流的拼接点 —— slave 加载快照后把已应用 offset 置为它,再从该点续流,语义无歧义。

`design.md` 的 Decisions 5/6 和 Risks 也同步更新了(新增"batch 半应用""重连频繁 full sync"两条风险)。`openspec validate` 通过,4/4 artifacts 仍完整。

要不要我把 `02_cache` 目录初始化成 git 仓库并提交这份提案(方便 `/opsx:apply` 前留一个干净基线)?或者直接进 `/opsx:apply` 开始实现。



**User**

/opsx:apply 

---

**Cursor**

Now in Agent mode. Let me read the apply command to follow its workflow.

Using change: `add-distributed-cache` (override with `/opsx:apply <other>`). Let me check status and get apply instructions.

State: `ready`, 32 tasks, 0 complete. Let me read all context files to ground the implementation precisely.

I have full context. This is a substantial implementation across 32 tasks. Let me track the 7 task-groups and build them in dependency order (LRU → protocol → server → hashring → replication → demo), with tests at each layer.

Starting with the package skeleton and supporting files (Group 1).

Now Group 2: the LRU cache with TTL.

Now Group 3: protocol codec and framing.

Let me verify the foundation (LRU + protocol) passes before building on top.

LRU and protocol solid (21 passed). Now the consistent hashing ring (Group 5) and replication (Group 6) modules, then the server that ties them together.

Now Group 6: the replication module (effect batch, master replicator, slave client).

Now Group 4: the asyncio TCP server / node that ties everything together.

Now the tests for hashring, server, replication, and sharding.

Adding a root `conftest.py` so imports resolve when running pytest, then running the full suite.

All 39 tests pass. Now Group 7: the end-to-end demo script, plus the README (task 1.2).

The demo runs cleanly — sharding, replication, eviction-as-DEL propagation, READONLY enforcement, and manual failover all work. Now the README (task 1.2).

All groups implemented and the demo works. Let me mark every task complete in `tasks.md`.

Let me check for lint issues on the new modules and confirm the full suite + openspec status.

All 32 tasks implemented, all checkboxes marked, no lint errors.

## Implementation Complete

**Change:** `add-distributed-cache` (schema: spec-driven)
**Progress:** 32/32 tasks complete ✓ · 39/39 tests passing · demo runs end-to-end

### 交付的代码结构

```
02_cache/
├── distcache/
│   ├── lru.py          # O(1) LRU(哈希表+双向链表)+ TTL,authoritative/logical 双模式
│   ├── protocol.py     # RESP 双模式请求 + RESP 响应 + 增量分帧解析器
│   ├── server.py       # asyncio 节点:master/slave、写路径临界区、SYNC 劫持、promote
│   ├── hashring.py     # 一致性哈希环 + 虚拟节点
│   ├── client.py       # 客户端侧一致性哈希路由
│   └── replication.py  # effect batch 复制流、master 推送、slave 全量同步与原子回放
├── tests/              # 39 个测试,覆盖 6 个能力
├── demo.py             # 端到端演示(分片 + 主从 + 淘汰传播 + 手动切换)
├── README.md  requirements.txt  .gitignore  conftest.py
```

### 关键设计点都落到了代码里

- **effect batch 原子性**:`server._do_set` 里 `store.set()` + `replicator.append_batch()` 是无 `await` 的临界区;一次 `SET` 触发淘汰会把 `[SET, DEL]` 打进同一个 offset 的 batch,slave 读齐整组再原子应用。
- **角色化过期**:master 用 authoritative(惰性删除+采样),slave 用 logical(只读不删,等 `DEL`)。
- **绝对时间复制**:`EXPIRE` 被改写成 `PEXPIREAT`(ms 绝对时间)再下发,避免主从计时分叉。
- **重连全量同步**:master 在无 `await` 临界区里捕获 `snapshot_offset` + 快照 + 注册队列,期间到达的 batch 自动缓冲、同步后按序补发。

演示实测输出确认:maxsize=3 的 master 在 `SET d` 时淘汰 `a`,slave 通过 `DEL` 同步删除,`master.offset == slave.applied_offset == 4`;提升 slave 为 master 后可正常接受写入。

### 下一步(提交到 GitHub)

代码完成了,但 `02_cache` 还不是 git 仓库。按你之前的要求,接下来需要(这些命令需要你的 git 身份,我没有改你 git config):

```bash
cd /root/xinruipeixun/02_cache
git config --global user.name "你的名字"   # 邮箱已配,补一下用户名
git init && git branch -M main
git add .gitignore openspec/ distcache/ tests/ demo.py README.md requirements.txt conftest.py
git commit -m "feat: implement distributed cache (LRU/protocol/server/hashring/replication)"
git remote add origin https://github.com/littleq1-n/SD-03-distributed-cache.git
git push -u origin main
```

如果想要评审看重的"按 Task 提交"的细粒度历史,我可以帮你把这批改动拆成 7 个符合 Conventional Commits 规范的提交(对应 7 个任务组:`docs:` 骨架 → `feat:` LRU → `feat:` 协议 → `feat:` 服务器 → `feat:` 哈希环 → `feat:` 复制 → `docs:` demo+README)。需要的话告诉我,我来执行(git config 仍需你先设好用户名)。另外 `openspec/changes/add-distributed-cache` 现已全部完成,可随时 `/opsx:archive` 归档。

---

**User**

我已经完成了相关的初始提交，请告诉我下一步需要如何按照要求给出下一步的相关提交，给给出相应的终端命令。并且说明为了符合相关要求，我现在还需要做什么
(base) root@9gpu-com:~/xinruipeixun/02_cache# git config --global user.name
Jiangxiaoqian
(base) root@9gpu-com:~/xinruipeixun/02_cache# git config --global user.email
1481865494@qq.com
(base) root@9gpu-com:~/xinruipeixun/02_cache# git init
hint: Using 'master' as the name for the initial branch. This default branch name
hint: is subject to change. To configure the initial branch name to use in all
hint: of your new repositories, which will suppress this warning, call:
hint: 
hint:   git config --global init.defaultBranch <name>
hint: 
hint: Names commonly chosen instead of 'master' are 'main', 'trunk' and
hint: 'development'. The just-created branch can be renamed via this command:
hint: 
hint:   git branch -m <name>
Initialized empty Git repository in /root/xinruipeixun/02_cache/.git/
(base) root@9gpu-com:~/xinruipeixun/02_cache# git branch -M main
(base) root@9gpu-com:~/xinruipeixun/02_cache# git push
fatal: The current branch main has no upstream branch.
To push the current branch and set the remote as upstream, use

    git push --set-upstream origin main

(base) root@9gpu-com:~/xinruipeixun/02_cache# git push -u origin main
Missing or invalid credentials.
Error: Bad status code: 401
    at ClientRequest.<anonymous> (/root/.cursor-server/bin/linux-x64/806df57ed3b6f1ee0175140d38039a38574ec720/extensions/git/dist/askpass-main.js:1:3335)
    at Object.onceWrapper (node:events:633:26)
    at ClientRequest.emit (node:events:518:28)
    at HTTPParser.parserOnIncomingClient (node:_http_client:702:27)
    at HTTPParser.parserOnHeadersComplete (node:_http_common:118:17)
    at Socket.socketOnData (node:_http_client:544:22)
    at Socket.emit (node:events:518:28)
    at addChunk (node:internal/streams/readable:561:12)
    at readableAddChunkPushByteMode (node:internal/streams/readable:512:3)
    at Readable.push (node:internal/streams/readable:392:5)
Missing or invalid credentials.
Error: Bad status code: 401
    at ClientRequest.<anonymous> (/root/.cursor-server/bin/linux-x64/806df57ed3b6f1ee0175140d38039a38574ec720/extensions/git/dist/askpass-main.js:1:3335)
    at Object.onceWrapper (node:events:633:26)
    at ClientRequest.emit (node:events:518:28)
    at HTTPParser.parserOnIncomingClient (node:_http_client:702:27)
    at HTTPParser.parserOnHeadersComplete (node:_http_common:118:17)
    at Socket.socketOnData (node:_http_client:544:22)
    at Socket.emit (node:events:518:28)
    at addChunk (node:internal/streams/readable:561:12)
    at readableAddChunkPushByteMode (node:internal/streams/readable:512:3)
    at Readable.push (node:internal/streams/readable:392:5)
remote: No anonymous write access.
fatal: Authentication failed for 'https://github.com/littleq1-n/SD-03-distributed-cache.git/'
(base) root@9gpu-com:~/xinruipeixun/02_cache# git remote set-url origin git@github.com:littleq1-n/SD-03-distributed-cache.git
(base) root@9gpu-com:~/xinruipeixun/02_cache# git push -u origin main
git@github.com: Permission denied (publickey).
fatal: Could not read from remote repository.

Please make sure you have the correct access rights
and the repository exists.
(base) root@9gpu-com:~/xinruipeixun/02_cache# ls -al ~/.ssh/id_*
ls: cannot access '/root/.ssh/id_*': No such file or directory
(base) root@9gpu-com:~/xinruipeixun/02_cache# ssh-keygen -t ed25519 -C "1481865494@qq.com"
Generating public/private ed25519 key pair.
Enter file in which to save the key (/root/.ssh/id_ed25519): cat ~/.ssh/id_ed25519.pub 
Enter passphrase (empty for no passphrase): 

(base) root@9gpu-com:~/xinruipeixun/02_cache# ssh-keygen -t ed25519 -C "1481865494@qq.com"
Generating public/private ed25519 key pair.
Enter file in which to save the key (/root/.ssh/id_ed25519): 
Enter passphrase (empty for no passphrase): 
Enter same passphrase again: 
Your identification has been saved in /root/.ssh/id_ed25519
Your public key has been saved in /root/.ssh/id_ed25519.pub
The key fingerprint is:
SHA256:un+LXKZP+zxRSwc0JOpJI6aOQ4D+TN/C8mijGazT7/g 1481865494@qq.com
The key's randomart image is:
+--[ED25519 256]--+
|            .o+  |
| .         . ... |
|. .     o +    . |
|.  .   o + o  o .|
| . .. . S o  o o |
|. +.oo..    . .  |
| + +o+o.  +  .   |
|o +++..o *.o.    |
|.++=E...=o+oo.   |
+----[SHA256]-----+
(base) root@9gpu-com:~/xinruipeixun/02_cache# cat /root/.ssh/id_ed25519.pub
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFi9nsU4PBkfI/BR1/N1wQNVpAke6trVIDQwJaO/Jks8 1481865494@qq.com
(base) root@9gpu-com:~/xinruipeixun/02_cache# ssh -T git@github.com
Hi littleq1-n! You've successfully authenticated, but GitHub does not provide shell access.
(base) root@9gpu-com:~/xinruipeixun/02_cache# git push -u origin main
Enumerating objects: 22, done.
Counting objects: 100% (22/22), done.
Delta compression using up to 2 threads
Compressing objects: 100% (15/15), done.
Writing objects: 100% (22/22), 14.83 KiB | 7.41 MiB/s, done.
Total 22 (delta 0), reused 0 (delta 0), pack-reused 0
To github.com:littleq1-n/SD-03-distributed-cache.git
 * [new branch]      main -> main
Branch 'main' set up to track remote branch 'main' from 'origin'.
(base) root@9gpu-com:~/xinruipeixun/02_cache# 

---

**Cursor**

你已经成功用 SSH 推送上去了。让我先看一下当前仓库的真实状态,再给你精确的下一步命令。

你的状态很健康:第一个提交 `docs: add OpenSpec SDD artifacts...` 已经把 SDD 四件套作为干净基线推上去了。现在工作区里是**未提交的实现代码**(`distcache/`、`tests/`、`demo.py`、`README.md` 等)。

下一步的关键是:**不要一坨全提交**,要按任务组拆成多个符合 Conventional Commits 规范的提交,这样才拿得到评审里"按 Task 提交"那 10 分。

## 当前状态
- 已提交并推送:`docs:` SDD 文档基线 ✓
- 未提交:全部源码 + 测试 + demo + README
- 另有 `tasks.md`(勾选)和 `.gitignore` 处于 modified

## 下一步:按任务组分提交(直接复制)

我把提交顺序按**依赖关系**排好了(底层模块先行),这样每个提交点 checkout 出来 `pytest` 都是绿的:

```bash
cd /root/xinruipeixun/02_cache

# (可选)忽略 Cursor 工具目录,保持仓库整洁
echo ".cursor/" >> .gitignore

# 提交① 项目骨架 + 勾选 tasks
git add .gitignore conftest.py requirements.txt distcache/__init__.py tests/__init__.py openspec/changes/add-distributed-cache/tasks.md
git commit -m "feat: scaffold distcache package and test tooling"

# 提交② LRU + TTL(单机存储层)
git add distcache/lru.py tests/test_lru.py
git commit -m "feat: implement O(1) LRU cache with TTL eviction"

# 提交③ RESP 协议编解码 + 分帧
git add distcache/protocol.py tests/test_protocol.py
git commit -m "feat: add RESP dual-mode protocol codec and framing"

# 提交④ 一致性哈希环
git add distcache/hashring.py tests/test_hashring.py
git commit -m "feat: add consistent hash ring with virtual nodes"

# 提交⑤ 复制流(effect batch)
git add distcache/replication.py
git commit -m "feat: implement master-slave replication with effect batches"

# 提交⑥ asyncio 服务器 + 集成测试
git add distcache/server.py tests/helpers.py tests/test_server.py tests/test_replication.py
git commit -m "feat: add asyncio TCP cache server with replication"

# 提交⑦ 分片客户端
git add distcache/client.py tests/test_sharding.py
git commit -m "feat: add consistent-hashing sharding client"

# 提交⑧ 演示脚本 + README
git add demo.py README.md
git commit -m "docs: add end-to-end demo and project README"

# 一次性推送所有新提交
git push
```

推送后到 GitHub 仓库的 commits 页面确认能看到这 9 个提交(含最初的 docs)。

## 为符合要求,你还需要做这几件事

评审 100 分里,代码和 SDD 部分你基本满足了(测试 **39/39 = 100% 通过**,远超 80% 线)。还差这几项**过程性 / 演示性**的产物:

1. **README 测试结果截图(必须)** — `07.md` 第 250 行明确要求。README 里我留了占位符。请实际跑一遍并截图:
   ```bash
   python3 -m pytest -q          # 截图:39 passed
   python3 demo.py               # 截图:分片+主从+切换的输出
   ```
   把图片存到 `docs/` 目录,在 README 里用 `![](docs/xxx.png)` 引用,再 `git add docs/ README.md && git commit -m "docs: add test and demo screenshots" && git push`。

2. **保留 AI 协作证据(20 分)** — `07.md` FAQ Q5 + 评审第 274-279 行。把本次对话导出/截图,作为过程产物(可放 `docs/ai-collaboration/` 或在 README 链接说明)。

3. **准备 10 分钟 Demo(10 分)** — 3 分钟项目+技术栈介绍,5 分钟跑 `demo.py` 演示,2 分钟 SDD 心得回顾。

4. **(可选)归档 OpenSpec change** — 全部任务已完成,可运行 `/opsx:archive` 把 change 的 specs 固化进主 `openspec/specs/`。提交时保留 change 文档通常也可以,看你导师要求。

需要的话,我可以直接帮你**执行上面这串提交命令**(SSH 已通,我来跑 git add/commit/push),或者帮你**把 README 里的截图占位改成真实的 docs/ 引用结构**。你说一声就行。

---

**User**

请先思考是否符合
### 8.3 各阶段详细要求

#### Day 1: Proposal + Specs初稿

**proposal.md 模板**:
```markdown
# Proposal: [课题名称]

## 背景
[为什么要做这个工具/服务]

## 目标
[具体要实现什么]

## 范围
### 包含
- [功能1]
- [功能2]

### 不包含
[超出范围的功能]

## 验收标准
- [ ] 标准1: 具体可验证的条件
- [ ] 标准2: 具体可验证的条件

## 技术栈
- 语言: [Python/Node.js/C/...]
- 主要依赖: [列出关键库]
- 运行环境: [纯软件/仿真器]
```

**Specs编写规范**:
```markdown
### Requirement: [功能名称]
The system SHALL [功能描述]

#### Scenario: [场景名称]
- GIVEN [前置条件]
- WHEN [操作]
- THEN [预期结果]

#### Scenario: [异常场景]
- GIVEN [前置条件]
- WHEN [错误操作]
- THEN [错误处理结果]
```

#### Day 2: 完善Specs

- 每个核心功能至少2个场景（正常+异常）
- 边界条件覆盖
- 使用RFC 2119关键词（SHALL/MUST/SHOULD/MAY）

#### Day 3: Design + Tasks

**design.md 模板**:
```markdown
# Design: [课题名称]

## 架构概览
[文字描述或简单架构图]

## 模块划分
### Module 1: [名称]
- 职责: [描述]
- 接口: [关键函数/方法]

### Module 2: [名称]
...

## 数据模型
[如果有数据存储，描述数据结构]

## 技术选型说明
- 选择X而不是Y的原因
- 仿真/模拟方案说明（如适用）
```

**tasks.md 模板**:
```markdown
# Tasks: [课题名称]

## Phase 1: 基础架构
- [ ] Task 1.1: [具体任务]
- [ ] Task 1.2: [具体任务]

## Phase 2: 核心功能
- [ ] Task 2.1: [具体任务]
- [ ] Task 2.2: [具体任务]

## Phase 3: 测试与优化
- [ ] Task 3.1: 编写单元测试
- [ ] Task 3.2: 集成测试
```

#### Day 4: Implementation

**AI Coding最佳实践**:
1. **按Task逐个实现**，每完成一个Task提交一次代码
2. **Commit Message规范**:
   ```
   feat: 添加XXX功能
   fix: 修复XXX问题
   test: 添加XXX测试
   docs: 更新XXX文档
   ```
3. **与AI协作模式**:
   - 先给AI看当前Task的Specs
   - 要求AI先生成代码，再解释关键逻辑
   - Review AI生成的代码，要求改进

#### Day 5: Verification + Demo准备

**验证清单**:
- [ ] 所有Specs场景都有对应测试
- [ ] 测试通过率≥80%
- [ ] 核心功能可演示（纯软件运行）
- [ ] 代码风格统一
- [ ] 无严重bug

**Demo准备**:
- 3分钟: 项目介绍 + 技术栈说明
- 5分钟: 功能演示（纯软件运行，无需硬件）
- 2分钟: SDD过程回顾（遇到的挑战、AI使用心得）

---

## 九、成果提交

培训成果提交至 **GitHub 个人仓库**，提交作品链接即可。要求：
- 仓库包含完整的项目代码及 SDD 各阶段文档（proposal、specs、design、tasks）
- Git 提交历史清晰，按 Task 提交，commit message 规范
- README.md 包含项目说明、运行方式、测试结果截图
这些要求，我现在的项目的相关文档是否符合？如果不是的话要怎么修改？修改完后要怎么提交？

---

**Cursor**

Good question — let me audit each artifact against the **8.3 templates** specifically (not just the OpenSpec schema). Let me re-read the current docs to judge precisely.

Here's my honest verdict against the **8.3 templates** (not just OpenSpec schema):

| 文档 | 实质内容 | 模板结构符合度 | 缺什么 |
|------|---------|--------------|--------|
| `proposal.md` | 充分 | ⚠️ 部分 | 缺显式 **目标**、**范围(包含/不包含)**、**验收标准(可勾选)**、**技术栈** 小节 |
| `specs/*.md` | 充分(SHALL/MUST✓,多场景✓) | ⚠️ 部分 | 场景用 `WHEN/THEN`,**缺 `GIVEN`**;部分 requirement 缺明确**异常场景** |
| `design.md` | 充分 | ⚠️ 部分 | 缺 **模块划分(职责/接口)** 与 **数据模型** 小节(架构概览✓、技术选型✓) |
| `tasks.md` | 充分 | ✅ 基本符合 | 可勾选/分组/可追踪都有,仅 `## 1.` 命名非 `Phase 1`(可接受) |
| Git 历史 | 进行中 | — | 按 task 提交(上一步命令) |
| `README.md` | 缺截图 | ⚠️ | 测试结果截图 |

结论:**实质内容都达标,但 proposal / design / specs 的"模板小节"和 specs 的 `GIVEN` 格式没对齐**。评审"格式规范"那几分有风险。我现在就把这三类文档补齐到既满足 OpenSpec、又满足 8.3 模板。

先补 `proposal.md`(目标 / 范围 / 验收标准 / 技术栈)。

Now `design.md` — add 模块划分 (职责/接口) and 数据模型.

Now the 5 spec files — adding `GIVEN` to every scenario and ensuring each requirement has a normal + 异常/边界 scenario. Starting with `lru-cache`.

Now let me validate the OpenSpec change still passes after these edits.

Validation passes, 4/4 artifacts still complete. Let me check the current git state so my commit instructions are accurate.

全部对齐完成,`openspec validate` 通过,4/4 artifacts 仍完整。

## 已修复的内容

- **`proposal.md`**:补上了 **目标**、**范围(包含/不包含)**、**验收标准(可勾选)**、**技术栈** 四个模板小节。
- **`design.md`**:补上了 **模块划分**(每个模块的职责+接口)和 **数据模型** 两个小节。
- **5 个 `specs/*.md`**:所有场景改为 **`GIVEN/WHEN/THEN`** 格式,并保证每个 requirement 都有**正常 + 异常/边界**场景;RFC 2119 关键词(SHALL/MUST)保留。
- **`tasks.md`**:已是可勾选/分组/可追踪(`## 1.` 命名与 `Phase 1` 等价,评审"任务可执行、追踪完整"已满足,无需改)。

现在四份文档**同时满足 OpenSpec 与 8.3 模板**。

## 提交命令(直接复制)

当前只有 1 个 commit(docs 基线),文档改动 + 实现代码都还没提交。下面这串先提交"文档对齐",再按任务组提交实现:

```bash
cd /root/xinruipeixun/02_cache

# 提交A:Day1-3 文档对齐模板
git add openspec/changes/add-distributed-cache/proposal.md \
        openspec/changes/add-distributed-cache/design.md \
        openspec/changes/add-distributed-cache/specs/ \
        openspec/changes/add-distributed-cache/tasks.md
git commit -m "docs: align proposal/design/specs with training templates"

# 提交①-⑧:按任务组提交实现
echo ".cursor/" >> .gitignore   # 可选:忽略 Cursor 工具目录
git add .gitignore conftest.py requirements.txt distcache/__init__.py tests/__init__.py
git commit -m "feat: scaffold distcache package and test tooling"

git add distcache/lru.py tests/test_lru.py
git commit -m "feat: implement O(1) LRU cache with TTL eviction"

git add distcache/protocol.py tests/test_protocol.py
git commit -m "feat: add RESP dual-mode protocol codec and framing"

git add distcache/hashring.py tests/test_hashring.py
git commit -m "feat: add consistent hash ring with virtual nodes"

git add distcache/replication.py
git commit -m "feat: implement master-slave replication with effect batches"

git add distcache/server.py tests/helpers.py tests/test_server.py tests/test_replication.py
git commit -m "feat: add asyncio TCP cache server with replication"

git add distcache/client.py tests/test_sharding.py
git commit -m "feat: add consistent-hashing sharding client"

git add demo.py README.md
git commit -m "docs: add end-to-end demo and project README"

git push
```

每个提交点 checkout 出来 `pytest` 都是绿的(底层模块先提交)。

## 提交后还剩这几件(才算完全符合"九、成果提交")

1. **README 测试截图**(硬性要求) — 跑 `python3 -m pytest -q` 和 `python3 demo.py`,截图存到 `docs/`,在 README 引用后再 `git add docs/ README.md && git commit -m "docs: add test and demo screenshots" && git push`。
2. **AI 协作证据**(评审 20 分) — 导出本次对话/截图,放仓库或在 README 链接。
3. **Demo 准备**(10 分) — 3 分钟介绍 + 5 分钟 `demo.py` 演示 + 2 分钟 SDD 心得。

需要的话我可以**直接帮你执行上面整串提交命令并 push**(你的 SSH 已通),或者帮你**把 README 改成带 `docs/` 截图引用的结构**。你说一声。

---

**User**

现在修改了相关的spce的文档，请仔细分析与检查是否需要重新执行/opsx:apply 命令从而完成相应的功能？

---

**Cursor**

Good instinct to check. Let me analyze precisely whether the spec edits introduced any **new behavior** that the code doesn't already do, versus just format/clarity changes. Let me verify the relevant behaviors exist in code and check test coverage for the newly-added scenarios.

Analysis complete. Here's the verdict:

**No, you do NOT need to re-run `/opsx:apply`.** The spec edits were **format/clarity changes** (added `GIVEN`, reorganized scenarios) plus a few **exception scenarios** — and every one of those behaviors is **already implemented** in the code:

| 新增/明确的场景 | 代码已实现? | 已有测试? |
|----------------|-----------|----------|
| LRU 读不存在 key → miss | ✅ `lru.py` | ✅ |
| LRU 非法容量 `maxsize<=0` 抛错 | ✅ `lru.py:38` | ❌ 缺 |
| 协议非法 multibulk 抛错 | ✅ | ✅ |
| server 未知命令 / 从拒绝写 | ✅ | ✅ |
| 空哈希环查询返回 None | ✅ `hashring.py` | ✅ |
| 客户端无节点报错 | ✅ `client.py:102` | ❌ 缺 |
| slave 不自主过期删除 / batch 原子回放 | ✅ | ✅ |

功能层面**完全不缺**,`/opsx:apply` 是用来补"未实现的任务"的,这里没有。

唯一的小缺口是 Day-5 验证清单里那条 **"所有 Specs 场景都有对应测试"**:新加的两个异常场景(非法容量、客户端无节点)目前没有专门的测试。这只需**补 2 个小测试**,不是重跑 apply。我直接补上。

全部通过:**41 passed**(原 39 + 新增 2)。

## 结论

**不需要重新执行 `/opsx:apply`。** spec 文档的修改只是**格式对齐(GIVEN/WHEN/THEN)+ 补充异常场景描述**,而这些行为代码早已实现 —— 没有任何"未实现的功能"需要 apply 去补。

我只做了一件收尾的小事:为新写进 spec 的两个异常场景补了对应测试,使其满足 Day-5 验证清单的"所有 Specs 场景都有对应测试":

- `test_invalid_maxsize_raises` — LRU `maxsize<=0` 抛 `ValueError`
- `test_client_with_no_nodes_raises` — 客户端无节点时抛 `CacheError`

## 对你提交计划的影响

这两个测试分别落在 `test_lru.py` 和 `test_sharding.py` 里,正好就在你之前那串提交命令的 **提交②(LRU)** 和 **提交⑦(分片客户端)** 中,所以**提交命令不用改**,照原计划跑即可。测试通过率现在是 **41/41 = 100%**。

什么时候才真正需要 `/opsx:apply`?——当 spec 里新增了**代码还没实现的需求**(比如真去加"读写分离"或"partial sync")。那种情况下 `tasks.md` 会出现新的未勾选任务,apply 才有意义。现在不是这种情况。

接下来你可以直接按上一条消息的命令提交 + push,然后补 README 截图与 AI 协作证据即可。需要我帮你执行提交吗?

---

**User**

请分析我想要展示与测试这个demo需要怎么做？请给我详细的指导

---

**Cursor**

好问题 —— 演示和测试是评审里 20 分(演示 10 + 验证清单)。我先给你把演示**跑顺**所需的一个小工具补上(目前缺一个能固定端口、长期运行的启动器,这样才能用 `nc` 做交互演示),然后给你一份完整的"测试 + 演示"操作手册和 10 分钟演示脚本。

先加一个启动器 `serve.py`。

Let me smoke-test the launcher (start it, connect, send a command, verify) and confirm `nc` is available.

The launcher works — `nc` is available and the node returned `+OK` / `$3` / `bar` correctly. Let me add `flush=True` so startup logs show immediately even when redirected.

Launcher is ready. Here's your complete **测试 + 演示操作手册**,包含可直接照着敲的命令和一份掐好时间的 10 分钟演示脚本。

---

## 一、环境确认(演示前 1 分钟)

```bash
cd /root/xinruipeixun/02_cache
python3 --version          # 需 3.9+
pip install -r requirements.txt   # 只装 pytest
```

---

## 二、自动化测试(对应"测试通过率≥80%")

```bash
python3 -m pytest -q
```

预期看到:`41 passed`。想展示"每个 Spec 场景都有测试",可按模块跑并显示用例名:

```bash
python3 -m pytest -v        # 显示每个用例名(命中刷新/淘汰/半包/粘包/复制…)
```

**截图①**:`pytest -q` 的 `41 passed` 结果(README 必需)。

---

## 三、一键端到端演示(最稳,演示主力)

```bash
python3 demo.py
```

它会自动跑完整故事并打印,涵盖三大块:
1. **一致性哈希分片** —— 12 个 key 被路由到 3 个节点,各存一部分;
2. **主从复制 + 淘汰传播** —— 写 master、slave 同步;maxsize=3 触发淘汰 `a`,slave 通过 `DEL` 一致删除;`offset` 主从一致;
3. **手动故障切换** —— slave 写被拒(`READONLY`),提升为 master 后可写。

**截图②**:`python3 demo.py` 的完整输出(README 必需)。

> 这是你 5 分钟演示的核心,**不会翻车**,建议作为主线。

---

## 四、交互式演示(展示"协议/复制是真的",加分)

用刚加的 `serve.py` 开多个终端。

### 4.1 协议 + 单机(终端 A、B)

终端 A(起一个容量很小的 master,方便演示淘汰):
```bash
python3 serve.py --port 7001 --maxsize 3
```

终端 B(用 `nc` 手敲协议,展示 inline 模式可调试):
```bash
nc 127.0.0.1 7001
# 然后逐行输入(回车):
SET foo bar
GET foo
DEL foo
GET foo
# 观察返回:+OK / $3 bar / +OK / $-1
```
继续在 B 里演示 **LRU 淘汰**(maxsize=3):
```bash
SET a 1
SET b 2
SET c 3
SET d 4      # 触发淘汰最久未用的 a
GET a        # 返回 $-1(已被淘汰)
GET d        # 返回 4
```

### 4.2 主从复制(终端 A、C、D)

保持 A 的 master 运行,终端 C 起 slave:
```bash
python3 serve.py --port 7002 --role slave --master 127.0.0.1:7001
```
终端 D 演示"写主、读从"复制是实时的:
```bash
nc 127.0.0.1 7001
SET hello world      # 写到 master
# 换连到 slave 读:
# (新开 nc)
nc 127.0.0.1 7002
GET hello            # 返回 world —— 已被复制到 slave
SET x 1              # 返回 -ERR READONLY —— 从节点拒绝写
```

> **故障切换**用 `nc` 不好现场触发(提升是运维动作),直接用 `demo.py` 第 2 段的输出讲解即可。

**截图③(可选)**:一张多终端 `nc` 会话图,体现协议 + 主从。

---

## 五、10 分钟演示脚本(掐时间)

**① 项目介绍 + 技术栈(3 分钟)**
- 一句话定位:Python + asyncio 的 mini Redis/Memcached Cluster,学习导向但每个概念真实现。
- 亮出架构图(`design.md` 顶部那张):单机存储层 / 协议层 / 网络层 + 横向分片 + 纵向复制。
- 技术栈:Python 3.9+,**仅标准库**,测试用 pytest。
- SDD:展示 `openspec/changes/add-distributed-cache/` 的 proposal/specs/design/tasks 四件套。

**② 功能演示(5 分钟)**
- 先跑 `python3 -m pytest -q` → `41 passed`(30 秒)。
- 跑 `python3 demo.py`,边跑边讲三段输出(分片 → 复制+淘汰 → 切换)(3 分钟)。
- `nc` 手敲 `SET/GET/DEL` + 触发一次淘汰,证明协议真实可交互(1.5 分钟)。

**③ SDD 过程回顾(2 分钟)**
- 讲一个设计深挖点:**effect batch**(淘汰/过期合成 `DEL`、与触发写同属一个 offset、slave 原子回放),这是"真实现 vs 玩具版"的分界。
- AI 协作心得:先给 AI 看 spec → 生成代码 → review;遇到的坑(如 asyncio 临界区不能有 `await`)。

---

## 六、给 README 补截图(对应"测试结果截图")

```bash
mkdir -p docs
# 把下面两条命令的终端截图保存为:
#   docs/pytest.png   (python3 -m pytest -q 的 41 passed)
#   docs/demo.png     (python3 demo.py 的输出)
```
然后在 `README.md` 的"测试结果"小节把占位符换成:
```markdown
![pytest](docs/pytest.png)
![demo](docs/demo.png)
```

---

## 七、提交时别忘了

`serve.py` 是这次新加的文件,把它并进 demo 那个提交:

```bash
git add demo.py serve.py README.md docs/
git commit -m "docs: add demo launcher, end-to-end demo and screenshots"
```

要我现在帮你:① 把 README 的"测试结果"小节改成 `docs/` 截图引用结构,或者 ② 直接执行提交+push?你说一声。

