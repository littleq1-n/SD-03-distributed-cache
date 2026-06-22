# SD-03 分布式缓存系统(mini Redis / mini Memcached Cluster)

学习导向的分布式缓存,使用 **Python + asyncio**,仅依赖标准库。每个核心概念都"真实现"而非玩具版:手写 O(1) LRU、RESP 风格协议与分帧、带虚拟节点的一致性哈希、master→slave 异步复制(effect batch)。

> 本项目遵循 SDD(规范驱动开发)流程,完整的 proposal / specs / design / tasks 见 [`openspec/changes/add-distributed-cache/`](openspec/changes/add-distributed-cache/)。

## 核心功能

| 功能 | 实现要点 |
|------|---------|
| LRU 淘汰 | 哈希表 + 双向链表,`get/set/delete` 均摊 O(1)(不使用 `OrderedDict`) |
| TTL 过期 | 绝对 deadline;authoritative 模式惰性删除 + 采样清理,logical 模式只读不删 |
| TCP 服务器 | `asyncio`,每连接一协程,支持并发 |
| 自定义协议 | RESP 风格;请求双模式(inline 便于 `nc` 调试 + RESP array 承载二进制);正确分帧解决粘包 |
| 一致性哈希分片 | 哈希环 + 虚拟节点,客户端侧路由,增删节点只影响 ~1/N 的 key |
| 主从复制(简化版) | 异步复制、effect batch、重连全量同步、手动故障切换 |

## 架构

```
                客户端 (一致性哈希,客户端侧路由)
                     │ RESP / TCP
        ┌────────────┼────────────┐
        ▼            ▼            ▼
     Node A       Node B       Node C        ← 分片层(横向扩容量)
   (master)     (master)     (master)
     │ effect batch 异步复制
     ▼
     Node A'  (slave)                         ← 副本层(纵向扩可用性)
```

模块(`distcache/`):

- `lru.py` — O(1) LRU + TTL 存储
- `protocol.py` — RESP 双模式编解码 + 增量分帧解析器
- `server.py` — asyncio 缓存节点(master/slave、写路径临界区、手动切换)
- `hashring.py` — 一致性哈希环 + 虚拟节点
- `client.py` — 客户端侧一致性哈希路由(阻塞 socket)
- `replication.py` — effect batch 复制流、master 推送、slave 全量同步与回放

## 运行方式

环境:Python 3.9+(无第三方运行时依赖)。

### 1. 启动单机节点并用 `nc` 调试(inline 协议)

```bash
python3 -c "import asyncio; from distcache.server import Node; \
n=Node(host='127.0.0.1', port=7001); \
asyncio.run((lambda: (asyncio.get_event_loop().run_until_complete(n.start()), \
asyncio.get_event_loop().run_forever()))[1]())" &
# 另开终端:
printf 'SET foo bar\r\nGET foo\r\n' | nc 127.0.0.1 7001
# +OK
# $3
# bar
```

### 2. 端到端演示(分片 + 主从 + 故障切换)

```bash
python3 demo.py
```

### 3. 运行测试

```bash
pip install -r requirements.txt
python3 -m pytest -q
```

## 协议示例

请求(双模式,服务器按首字节区分):

```
# inline(人工调试)
SET foo bar\r\n
GET foo\r\n

# RESP array(客户端库,可承载二进制/含空格值)
*3\r\n$3\r\nSET\r\n$3\r\nfoo\r\n$3\r\nbar\r\n
```

响应(统一 RESP):

```
+OK\r\n            # 简单状态
-ERR <msg>\r\n     # 错误
$3\r\nbar\r\n      # bulk 值
$-1\r\n            # 空值(miss)
```

支持命令:`SET key value [EX secs]`、`SETEX key secs value`、`GET key`、`DEL key`、`EXPIRE key secs`、`PING`、`ROLE`。slave 上的写命令返回 `-ERR READONLY`。

## 复制语义(要点)

- 复制流流动的是 master **应用后的效果**(effect),以 **batch** 为单位,一个单调递增 `offset` 对应一组原子效果。
- 淘汰 / 过期删除会合成 `DEL` 进入复制流;`EXPIRE` 改写为绝对时间的 `PEXPIREAT`。
- slave 是被动状态机:走非淘汰的回放路径,**不自主淘汰/过期删除**,以 batch 为单位原子回放。
- 异步复制:master 完成"本地应用 + effect 入队"即返回客户端,不等 slave ACK。
- 重连一律全量同步(快照 + `snapshot_offset` + 期间写缓冲),本版不做 partial sync。

## 测试结果

```
$ python3 -m pytest -q
.......................................                                  [100%]
39 passed
```

覆盖范围:LRU(命中刷新/容量淘汰/惰性删除/采样/快照)、协议(inline/RESP/半包/粘包/跨读 bulk/二进制)、哈希环(确定性/分布/增删节点 ~1/N)、服务器(并发/未知命令/READONLY)、复制(基本同步/淘汰传播/过期绝对时间/全量同步/增量流)、分片(路由落点)。

> 演示与测试均为纯软件运行。提交前请在此处补充 `pytest` 与 `python3 demo.py` 的运行截图。
```
（在此粘贴运行截图）
```
