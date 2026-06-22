from distcache.hashring import ConsistentHashRing


def test_routing_deterministic():
    ring = ConsistentHashRing(["n1", "n2", "n3"], vnodes=100)
    keys = ["key%d" % i for i in range(200)]
    first = {k: ring.get_node(k) for k in keys}
    # 再算一次,结果必须完全一致
    for k in keys:
        assert ring.get_node(k) == first[k]


def test_empty_ring_returns_none():
    ring = ConsistentHashRing(vnodes=10)
    assert ring.get_node("anything") is None


def test_distribution_roughly_even():
    nodes = ["n1", "n2", "n3", "n4"]
    ring = ConsistentHashRing(nodes, vnodes=200)
    counts = {n: 0 for n in nodes}
    total = 20000
    for i in range(total):
        counts[ring.get_node("key-%d" % i)] += 1
    mean = total / len(nodes)
    # 每个节点都应在均值的 0.5x ~ 1.5x 之间(留足余量)
    for n in nodes:
        assert 0.5 * mean < counts[n] < 1.5 * mean, (n, counts[n])


def test_add_node_affects_about_one_over_n():
    nodes = ["n1", "n2", "n3"]
    ring = ConsistentHashRing(nodes, vnodes=200)
    keys = ["key-%d" % i for i in range(10000)]
    before = {k: ring.get_node(k) for k in keys}
    ring.add_node("n4")
    moved = sum(1 for k in keys if ring.get_node(k) != before[k])
    frac = moved / len(keys)
    # 理论约 1/4=0.25;给宽松区间防止偶然波动
    assert 0.10 < frac < 0.45, frac
    # 未移动的 key 仍落在原节点
    for k in keys:
        if ring.get_node(k) == before[k]:
            assert before[k] in nodes


def test_remove_node_reassigns_only_its_keys():
    nodes = ["n1", "n2", "n3"]
    ring = ConsistentHashRing(nodes, vnodes=200)
    keys = ["k-%d" % i for i in range(5000)]
    before = {k: ring.get_node(k) for k in keys}
    ring.remove_node("n3")
    for k in keys:
        if before[k] != "n3":
            # 不属于被删节点的 key,归属不应改变
            assert ring.get_node(k) == before[k]
        else:
            assert ring.get_node(k) in ("n1", "n2")
