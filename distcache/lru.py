"""O(1) LRU 缓存:哈希表 + 双向链表,叠加 TTL(绝对过期时间)。

设计要点(见 design.md Decision 2/3):
- `dict[key] -> _Node`,_Node 处于双向链表中;命中移到表头,淘汰自表尾。
- 不使用 `OrderedDict`,链表与哈希表是本模块的核心学习价值。
- TTL 存绝对 deadline(秒)。过期读取分两种模式:
    * authoritative(主/单机):读到过期 → miss + 删除(惰性);并支持采样主动清理。
    * logical(从):读到过期 → miss 但不删除(等 master 的 DEL)。
"""

import random
import time
from typing import Callable, List, Optional, Tuple


class _Node:
    __slots__ = ("key", "value", "expire_at", "prev", "next")

    def __init__(self, key=None, value=None, expire_at=None):
        self.key = key
        self.value = value
        self.expire_at = expire_at  # 绝对秒时间戳,或 None 表示永不过期
        self.prev = None
        self.next = None


class LRUCache:
    AUTHORITATIVE = "authoritative"
    LOGICAL = "logical"

    def __init__(
        self,
        maxsize: int = 1024,
        mode: str = AUTHORITATIVE,
        time_func: Callable[[], float] = time.time,
    ):
        if maxsize <= 0:
            raise ValueError("maxsize must be > 0")
        self.maxsize = maxsize
        self.mode = mode
        self._time = time_func
        self._map = {}
        # 哨兵:head <-> ... <-> tail;head.next 为 MRU,tail.prev 为 LRU
        self._head = _Node()
        self._tail = _Node()
        self._head.next = self._tail
        self._tail.prev = self._head

    # ---- 双向链表操作(均 O(1)) ----
    def _remove(self, node: _Node) -> None:
        node.prev.next = node.next
        node.next.prev = node.prev
        node.prev = node.next = None

    def _add_front(self, node: _Node) -> None:
        node.next = self._head.next
        node.prev = self._head
        self._head.next.prev = node
        self._head.next = node

    def _move_to_front(self, node: _Node) -> None:
        self._remove(node)
        self._add_front(node)

    def _is_expired(self, node: _Node, now: float) -> bool:
        return node.expire_at is not None and node.expire_at <= now

    # ---- 公共 API ----
    def get(self, key, now: Optional[float] = None):
        node = self._map.get(key)
        if node is None:
            return None
        if now is None:
            now = self._time()
        if self._is_expired(node, now):
            # 角色化的过期读取
            if self.mode == self.AUTHORITATIVE:
                self._remove(node)
                del self._map[key]
            return None
        self._move_to_front(node)
        return node.value

    def set(self, key, value, expire_at: Optional[float] = None):
        """主/单机写入路径。返回被淘汰的 key(若发生淘汰)否则 None。"""
        return self._insert(key, value, expire_at, evict=True)

    def apply_set(self, key, value, expire_at: Optional[float] = None):
        """复制回放写入路径:绝不触发本地淘汰(见 design.md Decision 5)。"""
        self._insert(key, value, expire_at, evict=False)
        return None

    def _insert(self, key, value, expire_at, evict: bool):
        node = self._map.get(key)
        if node is not None:
            # 覆盖:更新值与过期,移到表头,容量计数不变
            node.value = value
            node.expire_at = expire_at
            self._move_to_front(node)
            return None
        node = _Node(key, value, expire_at)
        self._map[key] = node
        self._add_front(node)
        if evict and len(self._map) > self.maxsize:
            lru = self._tail.prev
            if lru is not self._head:
                self._remove(lru)
                del self._map[lru.key]
                return lru.key
        return None

    def set_expire(self, key, expire_at: float) -> bool:
        """对已存在 key 应用绝对过期时间(对应 PEXPIREAT)。"""
        node = self._map.get(key)
        if node is None:
            return False
        node.expire_at = expire_at
        return True

    def delete(self, key) -> bool:
        node = self._map.get(key)
        if node is None:
            return False
        self._remove(node)
        del self._map[key]
        return True

    def sample_expired(self, now: Optional[float] = None, sample_size: int = 20) -> List:
        """authoritative 模式的主动采样清理。返回被删除的 key 列表。"""
        if self.mode != self.AUTHORITATIVE:
            return []
        if now is None:
            now = self._time()
        keys = list(self._map.keys())
        if not keys:
            return []
        if len(keys) > sample_size:
            keys = random.sample(keys, sample_size)
        removed = []
        for k in keys:
            node = self._map.get(k)
            if node is not None and self._is_expired(node, now):
                self._remove(node)
                del self._map[k]
                removed.append(k)
        return removed

    def snapshot(self) -> List[Tuple]:
        """返回 (key, value, expire_at) 列表,用于全量同步。"""
        return [(n.key, n.value, n.expire_at) for n in self._map.values()]

    def load_snapshot(self, items) -> None:
        """用快照替换整个存储。"""
        self._map.clear()
        self._head.next = self._tail
        self._tail.prev = self._head
        for (k, v, exp) in items:
            node = _Node(k, v, exp)
            self._map[k] = node
            self._add_front(node)

    def keys(self):
        return list(self._map.keys())

    def __len__(self):
        return len(self._map)

    def __contains__(self, key):
        return key in self._map
