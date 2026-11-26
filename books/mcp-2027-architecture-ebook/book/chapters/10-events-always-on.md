# 常驻与多人协作代理：MCP Events

凌晨两点十四分，`auth-api` 触发了一条 P0 安全告警。在 2026 年秋以前，就算你的团队接好了二十台 MCP Server，Agent 这会儿也在呼呼大睡。传统 MCP 是纯拉取驱动的：用户或模型不发问，服务端就永远沉默。核心规范里虽然有 `subscriptions/listen` 和 `notifications/resources/updated`，但它们根本扛不起异步事件驱动的三项硬要求：第一，核心通知只报某个 URI 变了，不带订阅过滤参数，也没有载荷 Schema，表达不了只订阅 `auth-api` 的 P0 告警；第二，核心通知要求客户端死死挂着一条长连接，一断网就什么也收不到；第三，没有游标（cursor），断线期间发生的所有事直接丢进黑洞，客户端甚至不知道自己漏了消息。

**MCP Events**（`io.modelcontextprotocol/events`，`SEP-3415` 已达 Final，扩展处于 Beta 阶段，要求核心协议 `2026-07-28` 及以上）补齐了 Agent 运行时最关键的最后一块动态能力：把 MCP 从被动应答的远程工具箱，推进到外部事件触发、离线可唤醒、多人可协作的常驻代理平面。Philipp Schmid 在原文里提到，Shop MCP（订单变更）和 Vercel（`deployment.succeeded`）等早期服务已经上线了事件支持，OpenCode 2.4.0 等宿主则把事件订阅封装成了自动化工具，ChatGPT 等远程持久化宿主开始开放回调端点，在用户离线时也能接住告警并自动拉日志完成初筛。下面这张图把 MCP Events 的三种投递模式、去重消费层和 MCP Tasks 的协同链路画了出来。

![MCP Events 三种投递模式与 MCP Tasks 异步编排回路](../../wiki/assets/diagrams/events-tasks-loop.svg)

图里左侧的三条分支对应不同部署形态的投递选择，中间的 Client SDK 消费层统一吸收游标持久化、缺口感知、`eventId` 去重与不可信输入隔离，右侧则由唤醒后的 Agent 按需触发工具或把耗时的修复排查工作转成 MCP Tasks 持续跟踪。

## 为什么必须同时定义三种投递模式

`SEP-3415` 最务实的设计决定，是没有强推某一种统一传输，而是按部署现实同时定义了 **Webhook**、**Push** 和 **Poll** 三种模式。原因很直白：Serverless 服务端挂不住成千上万条常驻推送连接；跑在开发者笔记本或内网 NAT 后面的 CLI 客户端没有公网 HTTPS 回调地址；对电池和费用敏感的移动端或托管宿主又不该无休止空转轮询。任何单一模式都会把至少一类真实场景拒之门外。

因此在 `events/list` 返回的每个事件类型描述符里，除了事件名 `name`、订阅过滤参数 `inputSchema`（同样支持 `x-mcp-header` 镜像到 HTTP 头）和事件载荷 `payloadSchema` 之外，服务端通过 `delivery` 数组声明该事件支持哪几种模式（三选其一至全选皆可，没有强制项），客户端 SDK 根据自身运行环境自动择优（典型优先级：有公网回调就选 Webhook，能保活连接就选 Push，其余走 Poll）：

| 投递模式 | 协议方法 | 服务端状态与保活机制 | 适用部署形态 | 顺序保证 |
| --- | --- | --- | --- | --- |
| **Webhook** | `events/subscribe` / `events/unsubscribe` | 服务端保存带可续期 TTL（`refreshBefore`）的回调订阅，支持 `ttlMs: null` 申请永不过期（服务端可拒绝并给有限值） | 远程无状态/云端常驻宿主、支持前向代理（Forward Proxy）中转 | 尽力有序，以 `timestamp` 为准 |
| **Push** | `events/stream` | 单条订阅一条长生命周期请求（HTTP POST 返回 SSE，stdio 走 stdout 按 `subscriptionId` 解复用），服务端按 `heartbeatMs`（默认建议 30 秒）发 JSON-RPC 心跳推进游标 | 本地 `stdio` 服务、常在线桌面端或实时构建/部署日志流 | 订阅内严格有序 |
| **Poll** | `events/poll` | 服务端无需保存每客户端订阅状态；首次传 `cursor: null` 拿到起点游标，后续按 `nextPollMs` 轮询（客户端应设 1000ms 下限防死循环），`hasMore: true` 时立即连拉清积压 | Serverless 无状态服务端、NAT 后简单 CLI | 订阅内严格有序 |

有一条架构原则贯穿三种模式：**客户端永远是订阅清单的唯一权威来源**。服务端不提供 `events/subscriptions/list` 枚举接口；如果服务端重启丢了内存里的短 TTL 订阅，客户端下一次续期或重连时带着本地持久化的订阅参数和 `cursor` 就能无损重建。还要注意 Push 模式的心跳细节：Streamable HTTP 下，`events/stream` 的心跳故意不用 SSE 注释行（`: keepalive`），而是发正式的 `notifications/events/heartbeat` JSON-RPC 通知，因为心跳帧里必须携带最新的 `cursor`，防止长时间静默导致客户端手里攥着过老的游标落出上游保留窗口。客户端连续三个 `heartbeatMs` 没收到任何帧，就判定流已死并带随机抖动（jitter）重连。

## Webhook 契约、Standard Webhooks 签名与续期机制

回到凌晨两点的 P0 安全告警场景。远程宿主（比如 ChatGPT 或企业后台调度器）暴露一个 HTTPS 回调地址，调 `events/subscribe` 注册过滤条件与密钥（注意 `SEP-3415` 中 Webhook 投递对象使用 `delivery.mode: "webhook"` 和 `delivery.url` 字段，早期草案及部分示例写法使用 `type`/`callbackUrl`，对接具体服务端时需按其 `revision` 与 Schema 对齐）：

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "events/subscribe",
  "params": {
    "name": "security.alert.opened",
    "arguments": { "severity": "p0" },
    "delivery": {
      "mode": "webhook",
      "url": "https://host.example/mcp/webhook/sub_01H",
      "secret": "whsec_bXlzZWNyZXRrZXkyNGR5dGVzbWluaW11bQ=="
    },
    "cursor": null,
    "maxAgeMs": 300000,
    "ttlMs": 3600000
  }
}
```

这里有四条规范级细节非常值得工程团队记牢：

1. **密钥必须由客户端生成且符合 Standard Webhooks 规范**：`delivery.secret` 必填，格式必须是字面前缀 `whsec_` 后接 24 到 64 字节密码学随机数的 Base64 编码，服务端遇到不合规密钥必须以 `InvalidParams` 拒收。投递时服务端按 Standard Webhooks 规范对消息 ID、时间戳和原始 Body 计算 HMAC-SHA256 签名；接收端必须验签并校验时间戳窗口以防重放。
2. **服务端派生确定性 `id`**：响应返回的 `id`（如 `sub_a3f1c8e2b0d49f7e`）由服务端根据 `(principal, delivery.url, name, arguments)` 确定性计算，每次投递放在 `X-MCP-Subscription-Id` HTTP 头里供接收端路由。重复调用同一组键的 `events/subscribe` 是幂等的，既用来刷新 `refreshBefore` 租约，也用来在服务端重启后原地重建订阅。
3. **回调端点防 SSRF**：回调 URL 必须走 `https`，服务端在真正投递前必须先验证端点并校验解析出的目标 IP，拦截私有网段、回环地址和云元数据地址（如 `169.254.169.254`），防止攻击者借事件订阅把 MCP Server 当内网探测跳板。
4. **前向代理（Forward Proxy）解耦模式**：规范特意指出，Webhook 回调地址不必就是最终运行模型的客户端本身。很多桌面或移动宿主会部署一个轻量的公网前向代理统一接住远程 MCP Server 的签名 Webhook，再通过 Push 或 Poll 转交回内网客户端，把公网暴露面收敛到单一网关。

## 游标、缺口感知、`eventId` 去重与轨迹续跑

外部事件投递在分布式系统里天然是至少一次（at-least-once）语义。`SEP-3415` 用一套克制的字段把可靠消费的边界讲清了：

每一条事件发生对象 `EventOccurrence` 都包含 `{eventId, name, timestamp, data, cursor}`。服务端应当直接复用上游系统自带的稳定事件 ID（如 Stripe 的 `evt_*`、GitHub 投递 GUID、Kafka offset 或邮件 Message-ID），这样哪怕同一事件同时从 Webhook 和断线补拉（Poll backfill）两条路径送达，客户端也能精准识别重复。去重的作用域严格限定在**单条订阅 `(name, arguments)` 内**；如果用户同时订了某个频道的所有消息和某位老板发的所有消息，同一条消息触发了两条不同订阅，两边拿到相同 `eventId` 并不算重复，各自都要投递。

游标 `cursor` 对客户端完全不透明，支持上游可重放时用于断线续传；如果上游本身没有历史日志，服务端返回 `cursor: null`，客户端接受断线期间事件不可恢复的事实。客户端还可以在重连或轮询时传入 `maxAgeMs`（有界回放），避免一台离线一周的笔记本一开机就把过去七天积压的几万条事件瞬间灌进 Agent。如果客户端传来的旧游标已经落出了上游保留窗口，服务端不会报错中断，而是返回 `truncated: true` 和最新起点游标，明确提示中间可能存在漏掉的事件，让 Agent 视业务重要性决定要不要主动调工具重新拉取一次权威状态。

事件通过验签和去重交给 Harness 之后，真正决定产品体验的是**轨迹续跑策略（Continuation）**，Philipp Schmid 在原文里点明了两种截然不同的路由模式：

- **同轨迹续跑（Same Trajectory）**：把新事件直接注入发起订阅的那条对话线程里，适合围绕同一项持续工作（比如盯着某个 PR 的 CI 与评审进展、跟进一个线上事故工单）保持完整上下文记忆。
- **隔离新轨迹（Isolated Trajectory）**：每来一条新事件就派生一条全新的独立执行轨迹，适合每收到一封高优客户邮件或每出现一条新 P0 安全告警这类彼此独立的工单分诊，防止多起不相关事件把同一条长对话搅成一锅粥，也能把某一条恶意外来事件里的提示注入严格隔离在单次沙箱轨迹内。

## Events 与 Tasks 的分工和组合

架构评审里最常被问到的问题是：MCP Tasks 和 MCP Events 都在解决异步，到底什么时候用哪个？官方规范划出的分界线非常清楚：**等待自己刚发起的长耗时作业出结果，用 MCP Tasks；监听外部系统在未知时刻发生的业务活动，用 MCP Events。**

两者组合起来就是 2027 年无人值守代理的完整执行回路：

1. 团队提前通过 `events/subscribe` 订阅 `security.alert.opened`（过滤 `severity: "p0"`）并写好常驻处置指令。
2. 凌晨两点告警触发，服务端签名 POST 到宿主回调，宿主验签去重后在隔离轨迹中唤醒 Agent。
3. Agent 拿到最小载荷（只有 `alertId` 和 `service`，遵循安全规范绝不把整段外部日志塞进事件体），随后用 OAuth 2.1 令牌调用日志和代码仓 MCP Server 查明根因。注意：**收到事件本身绝不授予 Agent 任何执行权限**，后续所有工具调用仍须走完整的 OAuth 校验，且服务端在订阅期满或账号权限被撤销时必须立即终止事件投递。
4. 如果排查或回滚需要跑一趟耗时 10 分钟的自动化验证流水线，Agent 调起部署工具拿到一个 **MCP Task** 句柄，在后台持续轮询或监听状态，跑完后把根因摘要和修复链接发到值班频道，等值班工程师早上打开电脑就能直接拍板。

文本回复、类型化脚本、异步任务和事件唤醒都已经就位，但工程师和业务人员终究不只是盯着纯文本日志工作。当你面对一张 P95 延迟趋势图、一个需要筛选几十个维度的发布看板、或者一份需要缩放批注的 PDF 和设计稿时，纯文本和原始 JSON 远远不够。下一章我们看已经进入 **Stable** 阶段的 **MCP Apps**。
