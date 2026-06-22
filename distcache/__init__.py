"""distcache — 学习导向的 mini Redis / mini Memcached Cluster。

模块:
- lru:         O(1) LRU 缓存(哈希表 + 双向链表)+ TTL
- protocol:    RESP 风格双模式协议编解码与分帧
- server:      基于 asyncio 的 TCP 缓存节点
- hashring:    带虚拟节点的一致性哈希环
- client:      客户端侧一致性哈希路由
- replication: master→slave 异步复制(effect batch)
"""

__all__ = [
    "lru",
    "protocol",
    "server",
    "hashring",
    "client",
    "replication",
]
