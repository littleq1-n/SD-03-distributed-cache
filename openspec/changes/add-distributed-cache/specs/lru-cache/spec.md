## ADDED Requirements

### Requirement: O(1) LRU 读写

系统 SHALL 提供单机内存 KV 存储,`get`/`set`/`delete` 的平均时间复杂度 MUST 为 O(1),内部 MUST 使用哈希表 + 双向链表实现,MUST NOT 依赖 `OrderedDict` 等现成有序容器。

#### Scenario: 命中后更新为最近使用

- **WHEN** 对已存在的 key 执行 `get`
- **THEN** 返回其值,并将该 key 移动到链表头部(标记为最近使用)

#### Scenario: 写入新 key

- **WHEN** 对不存在的 key 执行 `set`
- **THEN** 在哈希表与链表头部插入该 key,后续 `get` 能取回该值

#### Scenario: 覆盖已有 key

- **WHEN** 对已存在的 key 执行 `set` 新值
- **THEN** 更新其值并移动到链表头部,容量计数不增加

### Requirement: 容量上限与 LRU 淘汰

系统 SHALL 支持配置最大容量 `maxsize`。当写入导致条目数超过 `maxsize` 时,系统 MUST 淘汰链表尾部(最久未使用)的条目,使条目数不超过 `maxsize`。

#### Scenario: 超出容量触发淘汰

- **WHEN** 缓存已满(达到 `maxsize`)且写入一个新 key
- **THEN** 链表尾部最久未使用的 key 被删除,新 key 被插入,总条目数仍等于 `maxsize`

#### Scenario: 访问顺序影响淘汰对象

- **WHEN** 缓存已满,且最久写入的 key 在写入新 key 前被 `get` 过
- **THEN** 该 key 因被刷新为最近使用而不被淘汰,被淘汰的是当前真正最久未使用的 key

#### Scenario: 复制回放写入不触发淘汰

- **WHEN** 通过非淘汰的 apply 模式(供复制回放使用)写入,即使条目数超过 `maxsize`
- **THEN** 仅写入/更新该 key,MUST NOT 触发本地表尾淘汰(淘汰由外部 `DEL` 驱动)

### Requirement: TTL 过期语义

系统 SHALL 支持为 key 设置过期时间,内部 MUST 以绝对时间戳(deadline)存储。已过期的 key MUST NOT 被读取返回。

#### Scenario: 未过期 key 正常读取

- **WHEN** 读取一个设置了 TTL 但尚未到达 deadline 的 key
- **THEN** 正常返回其值,并刷新为最近使用

### Requirement: 过期读取的两种模式

存储 SHALL 提供两种过期处理模式,由调用方按角色选择:

- **authoritative(主/单机)**:读到已过 deadline 的 key 时,视为 miss 返回空并**删除**该过期条目(惰性删除);并 SHALL 通过定期采样扫描回收长期不被访问的过期冷 key。
- **logical-miss(从)**:读到已过 deadline 的 key 时,视为 miss 返回空,但 MUST NOT 删除该条目(等待复制流下发的 `DEL`)。从角色 MUST NOT 主动运行过期采样删除。

#### Scenario: authoritative 模式惰性删除

- **WHEN** authoritative 模式下读取一个已过绝对 deadline 的 key
- **THEN** 视为 miss 返回空,并将该过期条目从存储中删除

#### Scenario: authoritative 模式定期采样清理

- **WHEN** authoritative 模式下后台清理周期触发
- **THEN** 系统随机采样若干 key,删除其中已过期者,使长期不被访问的过期冷 key 也能被回收

#### Scenario: logical-miss 模式只读不删

- **WHEN** logical-miss 模式下读取一个已过绝对 deadline 的 key
- **THEN** 视为 miss 返回空,但该条目仍保留在存储中,直至收到外部 `DEL`
