## ADDED Requirements

### Requirement: effect log 复制语义

master SHALL 通过复制流向 slave 传播"应用后的效果命令"(effect),而非原始用户命令。具体地:写命令按效果转发;LRU 淘汰 MUST 合成 `DEL <key>`;TTL 过期删除 MUST 合成 `DEL <key>`;相对过期命令(如 `EXPIRE`/`SETEX`)MUST 改写为携带绝对时间戳的形式后再传播。slave MUST 仅回放 effect,MUST NOT 自主执行淘汰或主动过期删除。

#### Scenario: 淘汰传播为 DEL

- **GIVEN** master 已连接 slave 且容量已满
- **WHEN** master 因容量超限淘汰 key X
- **THEN** master 本地删除 X,并向复制流追加 `DEL X`,slave 收到后删除 X

#### Scenario: 过期传播为 DEL

- **GIVEN** master 已连接 slave 且 key Y 已过期
- **WHEN** master 惰性或定期发现并删除 Y
- **THEN** master 向复制流追加 `DEL Y`,slave 收到后删除 Y

#### Scenario: 相对过期改写为绝对时间

- **GIVEN** master 已连接 slave
- **WHEN** 客户端在 master 上对 key 设置相对 TTL
- **THEN** 传播给 slave 的 effect 携带绝对过期时间戳,使 master 与 slave 的过期时刻一致

#### Scenario: slave 不自主过期删除(异常路径)

- **GIVEN** slave 上某 key 的绝对 deadline 已过,但 master 的 `DEL` 尚未到达
- **WHEN** slave 的后台逻辑运行
- **THEN** slave MUST NOT 主动删除该 key,只能等待 master 下发的 `DEL`

### Requirement: master 与 slave 的写应用区分

master 的客户端写入路径(如 `set`)MUST 使用会触发本地容量淘汰的存储写入;slave 的复制回放路径(如 `apply_repl_set`)MUST 使用非淘汰的 apply 模式,回放 MUST NOT 触发 slave 本地的 LRU 淘汰。slave 上的删除仅由 master 下发的 `DEL` 驱动。

#### Scenario: master 写触发淘汰并传播

- **GIVEN** master 容量已满
- **WHEN** master 客户端写入导致超过 `maxsize`
- **THEN** master 触发本地淘汰,并将该淘汰作为 `DEL` 进入同一 effect batch 传播给 slave

#### Scenario: slave 回放不触发本地淘汰(异常路径)

- **GIVEN** slave 条目数已达其 `maxsize`
- **WHEN** slave 回放一条会使其条目数超过 `maxsize` 的写
- **THEN** slave 仅写入该 key,不运行本地淘汰逻辑;条目的减少只发生在收到 master 的 `DEL` 时

### Requirement: effect batch 与 offset

复制流 MUST 以 effect batch 为单位推进:一个单调递增的复制 offset 对应一组原子效果(一条用户写可能产生多条效果,如 `SET` 同时触发淘汰会产生 `[SET k v, DEL evicted]`)。slave MUST 以 batch 为单位原子回放(要么整组应用,要么都不应用),不得只应用 batch 的一部分。每 slave 连接 MUST 为一条 TCP 长连接,master MUST 按本地应用顺序写入各 batch。

#### Scenario: 一次写产生多效果同属一个 offset

- **GIVEN** master 的一次 `SET` 同时导致一次淘汰
- **WHEN** master 生成对应的复制效果
- **THEN** `SET` 与对应 `DEL` 被打包进同一个 offset 的 effect batch 传播

#### Scenario: batch 原子回放

- **GIVEN** slave 接收到一个含多条效果的 batch
- **WHEN** slave 回放该 batch
- **THEN** 该 batch 的所有效果要么全部应用、要么都不应用,slave 记录的已应用 offset 仅在整组成功后推进

#### Scenario: offset 单调递增且顺序一致

- **GIVEN** master 连续应用多条写
- **WHEN** 对应 effect batch 进入复制流
- **THEN** 以与本地应用相同的顺序、携带递增 offset 推送,slave 按该顺序回放

### Requirement: 异步复制(应答不等待 ACK)

master SHALL 采用异步复制:本地写应用且 effect batch 入复制队列后立即响应客户端,MUST NOT 等待 slave ACK。

#### Scenario: 写不被复制阻塞

- **GIVEN** master 已连接若干 slave
- **WHEN** 客户端向 master 发送写命令
- **THEN** master 完成本地应用 + effect 入队后立即返回成功,不等待 slave

### Requirement: 重连一律全量同步

本版 MUST NOT 实现部分重同步(partial sync)与复制 backlog。slave 首次连接或断线重连时,master MUST 一律执行全量同步:

1. master 生成当前完整 keyspace 快照(含每个 key 的值与绝对 deadline),并记录该快照对应的 `snapshot_offset`;
2. 快照生成/发送期间到达的写命令对应的 effect batch MUST 被缓冲;
3. 快照发送完成后,slave MUST 用快照替换其整个存储,并将已应用 offset 置为 `snapshot_offset`;
4. master MUST 补发缓冲的 effect batch,随后转入正常复制流。

#### Scenario: 全新或重连 slave 一律全量同步

- **GIVEN** 一个 slave(无论全新还是断线重连)
- **WHEN** 它连接 master 并发起同步
- **THEN** master 执行全量同步,发送快照与 `snapshot_offset`,slave 加载后 keyspace 与 master 快照时刻一致,已应用 offset = `snapshot_offset`

#### Scenario: 快照期间的写不丢失(异常路径)

- **GIVEN** master 正在生成/发送快照
- **WHEN** 期间收到新的写命令
- **THEN** 这些写对应的 effect batch 被缓冲,并在快照应用后按 offset 顺序补发给 slave,slave 不丢失这些写

### Requirement: 手动故障切换

系统 SHALL 仅支持手动故障切换。master 不可用时,系统 MUST NOT 自动选主;运维 SHALL 可手动将某个 slave 提升为 master,并由客户端配置切换写入目标。

#### Scenario: 手动提升 slave

- **GIVEN** master 不可用
- **WHEN** 运维将某 slave 提升为 master
- **THEN** 该节点开始接受写入,系统不进行任何自动选主

#### Scenario: 提升前从节点仍拒绝写(异常)

- **GIVEN** 一个尚未被提升的 slave
- **WHEN** 客户端向其发送写命令
- **THEN** 返回 `-ERR READONLY`,直到被手动提升为 master
