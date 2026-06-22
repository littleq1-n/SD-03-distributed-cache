# cache-protocol Specification

## Purpose
TBD - created by archiving change add-distributed-cache. Update Purpose after archive.
## Requirements
### Requirement: 双模式请求编码(inline + RESP array)

系统 SHALL 支持两种请求格式,解析器 MUST 通过首字节区分:首字节为 `*` 时按 RESP array 解析,否则按 inline 命令解析。

- **inline 命令**:一行以 `\r\n` 结尾的文本,按空白切分为参数(如 `SET foo bar\r\n`),供人工 `nc`/`telnet` 调试使用;该模式 MUST NOT 用于承载含空格或二进制字节的值。
- **RESP array**:`*<argc>\r\n` 后跟 `argc` 个 `$<len>\r\n<bytes>\r\n` 参数(如 `*3\r\n$3\r\nSET\r\n$3\r\nfoo\r\n$3\r\nbar\r\n`),为客户端库的规范格式,MUST 能承载含空格与任意二进制字节的值。

#### Scenario: inline 命令解析

- **GIVEN** 一个空的解析器
- **WHEN** 收到 `SET foo bar\r\n`
- **THEN** 解析为参数序列 `["SET", "foo", "bar"]`

#### Scenario: RESP array 解析

- **GIVEN** 一个空的解析器
- **WHEN** 收到 `*3\r\n$3\r\nSET\r\n$3\r\nfoo\r\n$3\r\nbar\r\n`
- **THEN** 解析为参数序列 `["SET", "foo", "bar"]`

#### Scenario: RESP array 承载二进制/含空格值

- **GIVEN** 一个空的解析器
- **WHEN** 通过 RESP array 发送值为 `a b\x00c` 的 `SET`
- **THEN** 该值被完整无损地解析,不被空白或控制字节截断

#### Scenario: 非法 multibulk 长度(异常)

- **GIVEN** 一个空的解析器
- **WHEN** 收到 `*abc\r\n` 这样的非法长度声明
- **THEN** MUST 抛出协议错误,而非返回错误的命令

### Requirement: RESP 响应编码

服务器响应 MUST 统一使用 RESP 编码:简单状态用 `+OK\r\n`,错误用 `-ERR <msg>\r\n`,bulk 值用 `$<len>\r\n<bytes>\r\n`,空值用 `$-1\r\n`。

#### Scenario: 编码成功状态

- **GIVEN** 一次写操作成功完成
- **WHEN** 服务器编码响应
- **THEN** 输出 `+OK\r\n`

#### Scenario: 编码 bulk 值

- **GIVEN** 待返回的值为 `bar`
- **WHEN** 服务器编码响应
- **THEN** 输出 `$3\r\nbar\r\n`

#### Scenario: 编码空值(异常/边界)

- **GIVEN** 查询的 key 不存在或已过期
- **WHEN** 服务器编码响应
- **THEN** 输出 `$-1\r\n`

### Requirement: 分帧与粘包处理

协议解析器 SHALL 维护输入字节缓冲区,对 inline 与 RESP array 两种格式均基于 `\r\n` 边界与 `$<len>` 长度声明界定每条消息。当缓冲区数据不足以构成一条完整消息时,解析器 MUST 等待更多字节而非报错;当缓冲区一次性含多条消息时,MUST 能逐条正确切分。

#### Scenario: 半包等待(异常路径)

- **GIVEN** 解析器缓冲区中只有一条命令的前半部分
- **WHEN** 调用 `parse_one`
- **THEN** 不返回命令,保留已收字节,等待后续数据

#### Scenario: 粘包拆分

- **GIVEN** 一次读取的字节中包含两条完整命令
- **WHEN** 连续调用 `parse_one`
- **THEN** 依次返回两条命令,缓冲区被正确消费

#### Scenario: 跨多次读取的 bulk 值

- **GIVEN** 一个 `$<len>` 声明的值分多次网络读取到达
- **WHEN** 每次喂入部分字节后调用 `parse_one`
- **THEN** 在累计字节达到声明长度前返回等待,达到后才组装出完整值

