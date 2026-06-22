import pytest

from distcache.lru import LRUCache


def test_invalid_maxsize_raises():
    with pytest.raises(ValueError):
        LRUCache(maxsize=0)
    with pytest.raises(ValueError):
        LRUCache(maxsize=-1)


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_set_get_basic():
    c = LRUCache(maxsize=4)
    assert c.set(b"a", b"1") is None
    assert c.get(b"a") == b"1"
    assert c.get(b"missing") is None


def test_overwrite_no_count_increase():
    c = LRUCache(maxsize=4)
    c.set(b"a", b"1")
    c.set(b"a", b"2")
    assert len(c) == 1
    assert c.get(b"a") == b"2"


def test_eviction_when_full():
    c = LRUCache(maxsize=3)
    c.set(b"a", b"1")
    c.set(b"b", b"2")
    c.set(b"c", b"3")
    evicted = c.set(b"d", b"4")  # 触发淘汰最久未用的 a
    assert evicted == b"a"
    assert len(c) == 3
    assert c.get(b"a") is None
    assert c.get(b"d") == b"4"


def test_access_refreshes_recency():
    c = LRUCache(maxsize=3)
    c.set(b"a", b"1")
    c.set(b"b", b"2")
    c.set(b"c", b"3")
    c.get(b"a")  # a 变为最近使用
    evicted = c.set(b"d", b"4")  # 此时最久未用是 b
    assert evicted == b"b"
    assert c.get(b"a") == b"1"


def test_apply_set_does_not_evict():
    c = LRUCache(maxsize=2)
    c.apply_set(b"a", b"1")
    c.apply_set(b"b", b"2")
    evicted = c.apply_set(b"c", b"3")  # 即使超出 maxsize 也不淘汰
    assert evicted is None
    assert len(c) == 3
    assert c.get(b"a") == b"1"


def test_ttl_lazy_delete_authoritative():
    clk = FakeClock(1000.0)
    c = LRUCache(maxsize=4, mode=LRUCache.AUTHORITATIVE, time_func=clk)
    c.set(b"a", b"1", expire_at=1005.0)
    assert c.get(b"a") == b"1"
    clk.t = 1006.0  # 过期
    assert c.get(b"a") is None
    assert b"a" not in c  # authoritative 惰性删除


def test_ttl_logical_miss_keeps_entry():
    clk = FakeClock(1000.0)
    c = LRUCache(maxsize=4, mode=LRUCache.LOGICAL, time_func=clk)
    c.apply_set(b"a", b"1", expire_at=1005.0)
    clk.t = 1006.0
    assert c.get(b"a") is None      # 逻辑 miss
    assert b"a" in c                # 但不删除,等外部 DEL


def test_sample_expired_authoritative():
    clk = FakeClock(1000.0)
    c = LRUCache(maxsize=100, mode=LRUCache.AUTHORITATIVE, time_func=clk)
    for i in range(10):
        c.set(("k%d" % i).encode(), b"v", expire_at=1005.0)
    clk.t = 1006.0
    removed = c.sample_expired(sample_size=100)
    assert len(removed) == 10
    assert len(c) == 0


def test_sample_expired_noop_for_logical():
    c = LRUCache(maxsize=10, mode=LRUCache.LOGICAL)
    c.apply_set(b"a", b"1", expire_at=1.0)
    assert c.sample_expired(now=2.0) == []
    assert b"a" in c


def test_snapshot_roundtrip():
    c = LRUCache(maxsize=10)
    c.set(b"a", b"1", expire_at=None)
    c.set(b"b", b"2", expire_at=5000.0)
    snap = c.snapshot()
    c2 = LRUCache(maxsize=10)
    c2.load_snapshot(snap)
    assert c2.get(b"a", now=0) == b"1"
    assert len(c2) == 2
