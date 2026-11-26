# 无状态内核与 HTTP 标准化

2025 年把 MCP Server 放到企业 API 网关（如 Envoy、Nginx 或 Cloudflare Workers）后面时，运维团队最头疼的问题不是业务逻辑，而是流量全成了黑盒。所有请求都发向同一个 `POST /mcp` 路径，网关如果不把整段 JSON-RPC 请求体读进内存反序列化，就根本不知道这条请求是在调 `tools/list` 还是 `tools/call`，更不知道它要调哪个工具、访问哪个区域集群，限流、路由、WAF 审计和分区域灰度全都无从下手。再加上强制的 `initialize` 三步握手和 `Mcp-Session-Id` 粘性路由，一台普通无状态函数或标准 L7 负载均衡器根本接不住。

`2026-07-28` 规范通过四个紧密咬合的提案，彻底把 MCP 改造成了标准云原生 HTTP 工作负载：`SEP-2575` 去掉握手、把元数据放进每条请求；`SEP-2567` 去掉会话、用显式句柄替代；`SEP-2243` 把关键字段镜像到标准 HTTP 请求头；`SEP-2549` 给所有列表和读取结果加上 `ttlMs` 与 `cacheScope`。下面这张图对照了旧版有状态架构和 `2026-07-28` 无状态内核在请求链路上的差异。

![2025 有状态粘性会话与 2026-07-28 无状态头路由对比](../../wiki/assets/diagrams/stateless-routing.svg)

图里上半部分展示的旧架构里，每一个新开的客户端或子代理都必须重走 `initialize` 并拉取不可跨会话缓存的列表，中间的网关被迫维护粘性会话，后端副本一旦重启就会直接丢失内存状态。下半部分的新内核里，客户端把协议版本与能力放进每条请求的 `_meta`，同时镜像到 HTTP 头，中间网关只需读取请求头就能直接路由和限流，后端任意无状态副本或 Serverless 实例都能独立完成验权和执行。

## 每请求自包含：去掉 `initialize` 之后怎么协商版本

去掉 `initialize` 和 `notifications/initialized` 后，客户端可以不经握手直接发业务请求，或者在需要提前展示服务端能力时调用一次强制实现的 `server/discover`。每一条请求的参数 `_meta` 里都必须自包含以下元数据（源自 `SEP-2575` 与 `docs/specification/2026-07-28/basic/transports/streamable-http.mdx`）：

- `io.modelcontextprotocol/protocolVersion`（必填）：本条请求遵循的协议版本，例如 `"2026-07-28"`。
- `io.modelcontextprotocol/clientCapabilities`（必填）：本条请求周期内客户端声明支持的能力与扩展（例如 `elicitation`、`extensions`）。空对象 `{}` 表示不支持可选能力，服务端绝不能凭先前请求去猜测客户端能力。
- `io.modelcontextprotocol/clientInfo`（建议每请求携带）：客户端软件名称和版本，用于日志与调试。
- `io.modelcontextprotocol/logLevel`（可选）：替代已删除的 `logging/setLevel`，只有请求里显式带了这个字段，服务端才允许在响应流里发 `notifications/message`。

服务端在响应的 `_meta` 里同样可以回传 `io.modelcontextprotocol/serverInfo`，规范特别提醒（`server/discover.mdx`）：`serverInfo` 是服务端自报的，未经协议校验，只用于界面显示、日志和排障，客户端不得拿它来做安全决策或改变协议行为。

版本协商也变成了内联模式。客户端直接带自己偏好的版本发请求；如果服务端不支持，就返回 HTTP `400 Bad Request` 和 JSON-RPC 错误码 `-32022`（`UnsupportedProtocolVersionError`），并在 `error.data.supported` 数组里列出自己支持的版本，客户端挑一个双方共有的版本重试即可。如果客户端调用了服务端未实现的方法，服务端返回 HTTP `404 Not Found` 加上 JSON-RPC `-32601`（`Method not found`）。

`2026-07-28` 还专门划定了 JSON-RPC 服务端错误码的分配边界（`changelog.mdx`）：`-32000` 到 `-32019` 留给各 SDK 和具体实现自定义，`-32020` 到 `-32099` 保留给 MCP 规范本身，目前已经正式分配了三个高频错误码：

| 错误码 | 名称 | HTTP 状态码 | 触发场景 |
| --- | --- | --- | --- |
| `-32020` | `HeaderMismatch` | `400 Bad Request` | 必填 HTTP 头缺失、格式非法，或解码后的头值与 JSON-RPC Body 不一致 |
| `-32021` | `MissingRequiredClientCapability` | `400 Bad Request` | 请求需要某项客户端能力（如 `elicitation` 或 `io.modelcontextprotocol/tasks`），但请求 `_meta` 未声明 |
| `-32022` | `UnsupportedProtocolVersion` | `400 Bad Request` | 客户端请求的版本不被服务端支持，响应附带 `supported` 列表 |

## HTTP 请求头标准化与防拆包欺骗（`SEP-2243`）

为了让反向代理、WAF 和可观测性系统无需解析请求体就能看见路由信息，Streamable HTTP 要求客户端在每条 POST 请求上镜像一组标准 HTTP 头（名称大小写不敏感，值大小写敏感）：

| HTTP 请求头 | 镜像来源字段 | 必填范围 |
| --- | --- | --- |
| `MCP-Protocol-Version` | `params._meta["io.modelcontextprotocol/protocolVersion"]` | 所有 POST 请求 |
| `Mcp-Method` | `method` | 所有 POST 请求 |
| `Mcp-Name` | `params.name`、`params.uri`（或 Tasks 扩展中的 `params.taskId`） | `tools/call`、`resources/read`、`prompts/get`、`tasks/*` |
| `Mcp-Param-{Name}` | `inputSchema` 中标注了 `"x-mcp-header": "{Name}"` 的工具参数 | 仅当对应参数存在且非 `null` 时 |

这里有一个很容易被忽略的安全细节：如果中间网关按 `Mcp-Param-Region: us-west1` 把请求放行到了美西集群，但攻击者在 JSON 请求体里偷偷把 `arguments.region` 改成了 `eu-central1`，就会出现经典的双重解析源不一致（Request Smuggling / Policy Bypass）。因此规范在 `Server Validation` 中定了一条死规矩：任何真正处理消息体的 MCP Server，都**必须**校对所有标准头与自定义 `Mcp-Param-*` 头的值是否与 Body 完全一致（整数比较按数值等价，例如 `42.0` 与 `42` 视为相等），只要对不上，立刻拒掉并返回 HTTP `400` 与 `-32020`（`HeaderMismatch`）。同时，中间网关如果基于镜像头做租户路由或限流，应当先检查 `MCP-Protocol-Version` 是否达到要求服务端强制校验的版本，否则绝不能盲信未校验的请求头。

为了让非 ASCII 字符、换行符或首尾空格也能安全放进 HTTP 头（遵循 RFC 9110 可见 ASCII 范围 `0x21-0x7E`），`SEP-2243` 定义了一套统一的 Base64 哨兵编码格式：

```text
Mcp-Param-Greeting: =?base64?SGVsbG8sIOS4lueVjA==?=
```

当 `Mcp-Name` 或 `Mcp-Param-*` 的原始值包含非 ASCII 字符、控制字符、首尾空格，或者本身字面量就以 `=?base64?` 开头且以 `?=` 结尾时，客户端必须对 UTF-8 字节做 Base64 编码并套上小写哨兵前缀后缀；服务端校验前必须先解码再和 Body 比对。对于 `x-mcp-header` 注解，规范还划定了严格的静态可达边界：只能挂在 `string`、`integer`（JS 安全整数范围内）或 `boolean` 这三种原始类型上，禁止用于 `number` 浮点类型，且路径只能穿过纯 `properties` 链，不能穿过数组 `items`、`oneOf`/`anyOf`/`allOf`、`if`/`then`/`else` 或 `$ref`。如果服务端下发的某个工具定义违反了这些规则，Streamable HTTP 客户端必须把那个畸形工具从 `tools/list` 里剔除并记日志，而不影响同一服务器上的其他合法工具。而且服务端作者绝不能把密码、Token、PII 这类敏感参数标成 `x-mcp-header`，因为 HTTP 头对沿途所有代理和日志系统都是明文可见的。

## 列表缓存：`ttlMs`、`cacheScope` 与变更失效（`SEP-2549`）

在无状态内核里，`server/discover`、`tools/list`、`prompts/list`、`resources/list`、`resources/templates/list` 和 `resources/read` 这六个操作返回 `resultType: "complete"` 时，都必须带上 `ttlMs` 和 `cacheScope` 两个缓存提示字段（而 MRTR 中间态 `input_required` 或携带 `inputResponses`/`requestState` 的重试结果绝对禁止缓存）。

`ttlMs` 是非负整数毫秒值，类似 HTTP 的 `Cache-Control: max-age`：客户端记录收到响应的本地时间 `t_received`，在 `now < t_received + ttlMs` 期间直接复用缓存；`ttlMs: 0` 表示立刻过期。规范特意提醒，TTL 只是懒惰新鲜度提示，客户端不应该把它当成后台定时器到处空转轮询，而是在真正需要数据时再检查是否过期。

`cacheScope` 只有 `"public"` 和 `"private"` 两个值，直接决定多租户场景下会不会串数据：

- **`"public"`**，响应不含用户特定数据，客户端、共享网关或代理可以跨不同调用方复用（例如所有用户看到的工具定义完全一致）。
- **`"private"`**，响应依赖当前鉴权身份（比如按 OAuth Scope 过滤后的工具列表，或者用户私有资源内容），只能在同一个授权上下文内复用，换一个 Access Token 就必须隔离缓存。分页列表的所有页必须声明一致的 `cacheScope`。

此外，服务端返回 `tools/list` 时**应当**保持确定性排序（Deterministic Ordering）。这不仅是为了客户端缓存稳定，更重要的是当工具清单注入 LLM 提示词时，稳定的顺序能显著提高大模型的 Prompt Cache 命中率。如果服务端同时支持推送通知，客户端可以通过 `POST subscriptions/listen` 订阅 `toolsListChanged` 等事件；一旦收到 `notifications/tools/list_changed` 或 `notifications/resources/updated`，客户端立刻把本地还没到 TTL 的缓存标为失效，实现低频兜底加实时刷新的组合。

## 显式状态句柄与双传输一致性

删掉会话后，像购物车、数据库连接或浏览器实例这类跨调用状态怎么办？`SEP-2567` 和 `tools.mdx` 给出的答案很朴素：用普通工具返回值颁发显式句柄（比如 `create_basket` 返回 `basket_id: "bsk_a1b2c3"`），后续工具把 `basket_id` 当普通入参传回来。Linear 的 `create_issue`、Notion 的 `notion-create-pages`、GitHub 的 `create_pull_request` 和 Stripe 的 `create_customer` 早就这么干了。

这里有一条安全底线必须分清：对**已鉴权**服务器，句柄只是资源名而不是通行证，服务端每次调用都必须校验 `(handle, auth_context)`，就像拿到一个 Google Doc 链接不代表你有权限打开它；只有对**无鉴权**服务器，句柄才不得不充当承载凭证（Bearer Token），此时必须用密码学随机源生成至少 128 位熵（如 UUIDv4）并设置明确过期时间，且把过期策略写进创建工具的 `description` 里让模型能看见。

最后看看 `stdio` 与 Streamable HTTP 的关系。`2026-07-28` 坚持两套传输保持完全一致的无状态语义：`stdio` 同样去掉了握手，每行一条换行分隔的 JSON-RPC 消息，元数据全在 `_meta` 里，日志走 `stderr`。两者的核心区别只剩传输力学：

- **请求取消**：Streamable HTTP 关闭该请求的 SSE 响应流即代表取消（不再发 `notifications/cancelled`）；`stdio` 共享同一对标准输入输出管道，所以仍需发 `notifications/cancelled` 指定请求 ID。
- **向下兼容探测**：连接未知版本的旧服务时，Streamable HTTP 客户端可以直接发一条现代 POST 请求，如果收到 `400`/`404`/`405` 且响应体不是现代规范认可的 JSON-RPC 错误，再回退到 `initialize` 或老旧的 GET SSE 端点；`stdio` 没有 HTTP 状态码，规范建议客户端先发一条 `server/discover` 探针，收到 `DiscoverResult` 或现代版本错误就走新协议，收到其他错误或超时再回退 `initialize`。

根据 2026 年 8 月更新的官方路线图（`docs/development/roadmap.mdx`），Transports 工作组下一步甚至计划探索 **HTTP/2 over stdio**，试图用一套统一的多路复用 HTTP 传输同时服务远程和本地子进程，再配合 ETag 扩展工具结果缓存。到那时，本地与远程传输层的双轨维护成本还会进一步压缩。

传输层已经轻装上阵，但要让 Agent 在不撑爆上下文的前提下精准调用这些无状态接口，核心还要看工具、资源与提示词这三类原语的契约设计。下一章我们拆解它们的 Schema 约束与错误处理机制。
