# cache-server Specification

## Purpose
TBD - created by archiving change add-distributed-cache. Update Purpose after archive.
## Requirements
### Requirement: 基于 asyncio 的并发 TCP 服务器

系统 SHALL 提供基于 `asyncio` 的 TCP 服务器,监听可配置端口,为每个客户端连接分配独立协程处理,支持多个客户端并发连接。

#### Scenario: 接受并发连接

- **GIVEN** 服务器已启动并监听端口
- **WHEN** 多个客户端同时连接
- **THEN** 每个连接由独立协程处理,互不阻塞,均可正常收发命令

#### Scenario: 连接关闭释放资源(异常路径)

- **GIVEN** 一个已建立的客户端连接
- **WHEN** 客户端断开连接
- **THEN** 对应协程结束并释放该连接的缓冲区等资源,不影响其他连接

### Requirement: 命令分发到本地存储

服务器 SHALL 将解析出的命令(至少包含 `SET`/`GET`/`DEL`,以及设置 TTL 的命令)分发到本地 LRU 存储执行,并按协议返回响应。对未知命令 MUST 返回错误响应。

#### Scenario: 执行 SET 后可 GET

- **GIVEN** 服务器已启动
- **WHEN** 客户端发送 `SET foo bar` 后发送 `GET foo`
- **THEN** 服务器先返回 `+OK`,再返回 `$3\r\nbar\r\n`

#### Scenario: GET 不存在的 key(边界)

- **GIVEN** 服务器中不存在 key `missing`
- **WHEN** 客户端对 `missing` 发送 `GET`
- **THEN** 服务器返回空值 `$-1\r\n`

#### Scenario: 未知命令(异常)

- **GIVEN** 服务器已启动
- **WHEN** 客户端发送服务器不支持的命令
- **THEN** 服务器返回 `-ERR` 错误响应,且连接保持可用

#### Scenario: 从节点拒绝写(异常)

- **GIVEN** 一个 role=slave 的节点
- **WHEN** 客户端向其发送写命令(如 `SET`)
- **THEN** 服务器返回 `-ERR READONLY` 而非执行写入

### Requirement: 写路径与复制队列原子入队

当服务器(作为 master)执行写类命令时,"应用到本地存储(含可能触发的淘汰)"与"将对应 effect batch 入复制队列"之间 MUST NOT 出现 `await` 让出点,以保证本地应用顺序与复制顺序一致。

#### Scenario: 并发写保持顺序

- **GIVEN** 多个协程并发提交对同一 key 的写
- **WHEN** 服务器依次处理这些写
- **THEN** 它们应用到本地存储的顺序与进入复制队列的顺序完全一致

### Requirement: 写响应时机

master 对写类命令返回客户端响应前,MUST 已完成(a)本地存储应用与(b)对应 effect batch 入复制队列;但 MUST NOT 等待任何 slave 的 ACK(异步复制)。

#### Scenario: 写应答不等待 slave

- **GIVEN** master 已连接若干 slave
- **WHEN** master 完成本地应用 + effect 入队
- **THEN** 立即返回 `+OK`,不阻塞等待 slave 确认

#### Scenario: 无 slave 连接时写仍成功(边界)

- **GIVEN** 当前没有任何 slave 连接到 master
- **WHEN** 客户端发送写命令
- **THEN** master 完成本地应用 + effect 入队后正常返回成功

