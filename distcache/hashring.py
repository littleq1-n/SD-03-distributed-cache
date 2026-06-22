"""带虚拟节点的一致性哈希环(见 design.md Decision 7)。

- 每个物理节点在环上放置 `vnodes` 个虚拟节点,使分布更均匀。
- key 顺时针映射到第一个虚拟节点所属的物理节点。
- 增删节点只影响约 1/N 的 key 归属;本环不负责数据迁移。
"""

import bisect
import hashlib
from typing import Iterable, Optional, Set


class ConsistentHashRing:
    def __init__(self, nodes: Optional[Iterable[str]] = None, vnodes: int = 150):
        if vnodes <= 0:
            raise ValueError("vnodes must be > 0")
        self.vnodes = vnodes
        self._ring = {}          # vnode_hash -> 物理节点
        self._sorted = []        # 排序后的 vnode_hash 列表(用于二分)
        self._nodes: Set[str] = set()
        if nodes:
            for n in nodes:
                self.add_node(n)

    def _hash(self, key) -> int:
        if isinstance(key, str):
            key = key.encode()
        elif not isinstance(key, (bytes, bytearray)):
            key = str(key).encode()
        return int.from_bytes(hashlib.md5(key).digest()[:8], "big")

    def add_node(self, node: str) -> None:
        if node in self._nodes:
            return
        self._nodes.add(node)
        for i in range(self.vnodes):
            h = self._hash("%s#%d" % (node, i))
            self._ring[h] = node
            bisect.insort(self._sorted, h)

    def remove_node(self, node: str) -> None:
        if node not in self._nodes:
            return
        self._nodes.discard(node)
        for i in range(self.vnodes):
            h = self._hash("%s#%d" % (node, i))
            if h in self._ring:
                del self._ring[h]
                idx = bisect.bisect_left(self._sorted, h)
                if idx < len(self._sorted) and self._sorted[idx] == h:
                    self._sorted.pop(idx)

    def get_node(self, key) -> Optional[str]:
        if not self._sorted:
            return None
        h = self._hash(key)
        idx = bisect.bisect(self._sorted, h)
        if idx == len(self._sorted):
            idx = 0  # 环形回绕
        return self._ring[self._sorted[idx]]

    @property
    def nodes(self) -> Set[str]:
        return set(self._nodes)
