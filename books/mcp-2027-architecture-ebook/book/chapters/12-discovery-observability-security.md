# 未解难题：发现、可观测性与纵深安全

把一个远程 MCP Server 做到符合 `2026-07-28` 规范，上线后的第一个月往往还是会遇到三类扎手的运营难题。第一类来自上线推广：厂商为了被不同客户端找到，得把同一份服务器配置挨个提交给五个互不相通的目录和市场，版本一升级到处失步。第二类来自线上排障和产品迭代：后台监控日志每天刷过几十万次 `tools/call`，工具成功率 99.5%，可产品经理根本不知道用户为什么调它、模型是不是绕了三圈弯路才勉强凑齐参数、最后到底帮没帮用户把事办成。第三类来自安全评审：每给协议加上一项新扩展（Code Mode、Webhook Events、iframe Apps、一键本地安装），攻击面就实打实往外扩一圈。

Philipp Schmid 在原文的 What still needs work 一节里直接点出了这三块缺口：发现、可观测性与意图、纵深安全。对照 `mcp-spec` 仓库现有的 Registry 文档、`SEP-2127`、安全最佳实践与 `SEP-1024`，我们可以准确分辨哪些已经有标准雏形，哪些在 2027 年依然悬而未决。

## 发现层：从中心化 Registry 到 `/.well-known` Server Cards

目前 MCP 社区的发现体系正在两条轨道上并行演进，各有明确职责边界。

第一条轨道是官方 **MCP Registry**（`docs/registry/about.mdx`，目前处于 preview 阶段，由 Anthropic、GitHub、PulseMCP 和 Microsoft 等维护）。它是一个中立、不做主观评分的中心化元数据仓库，通过反向 DNS 命名空间验证（GitHub、DNS 或 HTTP 挑战，例如 `io.github.user/server-name` 或 `com.example/server`）确保发布者真实身份，以统一的 `server.json` 格式指向 npm/PyPI/Docker 包或公网远程 URL。注意它的三个明确设计边界：

- **不托管二进制代码，也不做私有服务托管**：代码依然留在 npm、PyPI 和 Docker Hub，企业内网私有 MCP Server（如 `mcp.acme-corp.internal`）需要自行实现符合 Registry OpenAPI 规范的私有注册表。
- **不直接面向终端宿主，而是供下游聚合器拉取**：官方预期各类应用市场和聚合器按小时拉取元数据，再叠加评分、策展和额外安全扫描。
- **本身不做代码级安全扫描**：官方明确把漏洞扫描与恶意代码检测委托给了底层包仓库和下游聚合器，主站只负责命名空间认证、字段校验和人工下架。

第二条轨道就是 Philipp Schmid 点名看好的去中心化域名发现：**MCP Server Cards**（`SEP-2127` 已达 Final，扩展处于 Beta 阶段，`ext-server-card`）。它要解决的是连接前发现：客户端拿到一个域名（如 `restaurant-a.com`），无需人工去五个市场翻找，也无需提前打开 MCP 连接，直接访问域名下的 **`/.well-known/ai-catalog.json`** 就能找到该域名关联的若干 MCP Server Card（推荐托管在 `<streamable-http-url>/server-card`），一次静态 HTTP GET 就能读出服务器身份、远程传输 URL、支持协议版本与连接指引，且天然兼容 CDN 缓存和自动化索引。

`SEP-2127` 做了一个极其关键的安全取舍：**Server Card 故意不包含工具、资源和提示词列表，也不声明运行时扩展能力**。因为这些列表可能因登录账号、权限 Scope、灰度开关和部署状态而异，如果允许静态卡片直接广告工具清单，客户端就会基于过期或被篡改的静态文档做权限和安全判断。静态卡片只回答服务器在哪、怎么连；连上之后的一切能力与工具清单，一律以带鉴权的实时 `server/discover` 和 `tools/list` 为准，两者冲突时始终以实时结果为准。

尽管如此，发现层离真正省心还差最后一公里：当域名发现普及后，自动化供应链扫描、恶意工具描述检测（Tool Poisoning）和可信签名背书怎么无缝接入客户端安装流，目前社区里还没有统一的强制标准。

## 可观测性与意图黑洞：隔离原则带来的天然张力

回顾第 1 章提到的 MCP 四条核心设计原则之一：**服务端不得读取完整对话，也不得窥探其他服务器**（`docs/specification/2026-07-28/architecture/index.mdx`）。这条隐私隔离红线保护了用户，却给服务端可观测性留了一个大坑。

从服务端视角看，每天收到的是一堆孤立的 JSON-RPC 请求：有人查了三次列表、调了一次详情、接着没下文了。服务端根本不知道这是模型顺利完成了任务，还是返回字段写得太晦涩导致模型看懵放弃了；也不知道用户原本想做数据分析、排障还是写代码。

目前协议层已经落地的可观测性标准只有 **`SEP-414`**，它约定了在 `_meta` 里透传 OpenTelemetry 的 `traceparent`、`tracestate` 和 `baggage` 字段，解决了分布式链路追踪的串连问题。但链路 ID 只能告诉你哪几个 RPC 属于同一次调用，回答不了为什么调和效果好不好。

Philipp Schmid 在原文里提到了两条正在试水的补充手段：一是客户端在请求元数据里传递脱敏后的高层意图标签（如 `telemetry.intent`）；二是服务端主动暴露轻量反馈工具（类似 Cloudflare 的 `submit_feedback`），允许模型在遇到工具描述模糊、参数难凑或结果缺失时提交结构化改进反馈。工程上这里有一个微妙的平衡点：意图元数据绝不能变成变相泄露用户完整聊天记录的后门，必须限制在粗粒度任务分类或用户显式同意的遥测范围内；而反馈工具也必须防模型滥用刷屏或把敏感业务上下文误塞进反馈正文里。如何用标准扩展既保护对话隐私、又让服务端看懂调用意图，是 2027 年非常值得关注的演进方向。

## 纵深安全：2027 新扩展带来的五重攻击面

随着 MCP 从纯工具调用进化成完整运行时，安全边界远不止校验 OAuth Token 那么简单。把官方 `security_best_practices.mdx`、`SEP-1024` 和 Phil Schmid 的风险清单合并起来，工程团队在上线前必须逐项核查五类攻击面：

| 攻击面 | 典型攻击手法 | 规范与工程防线 |
| --- | --- | --- |
| **1. 身份与代理链路** | 令牌透传（Token Passthrough）、OAuth Mix-Up 混肴、代理型服务器 Confused Deputy、本地 `localhost` 回调劫持 | 严格验证 RFC 8707 `aud` 受众；校验 RFC 9207 `iss`；代理服务器对每个注册客户端单独实施带 `__Host-` Cookie 与防点击劫持的二次同意页；弃用 DCR 转向 CIMD 域名信任策略 |
| **2. 提示注入与工具投毒** | 恶意或被攻破的外部工具在 `description`、返回文本或资源内容中藏匿隐藏指令（Tool Poisoning / Indirect Prompt Injection），诱导模型调用另一台高权限服务器 | 永远把工具注解、返回内容、技能正文、事件载荷视为不可信输入；跨服务器写操作和敏感读取强制人机确认；宿主打通来源标签隔离 |
| **3. 不可信 Webhook 与 SSRF** | 攻击者利用 `events/subscribe` 或 CIMD 元数据拉取功能，让服务端向内网 `169.254.169.254`、`127.0.0.1` 或内网管理端口发起请求；伪造或重放事件 POST | 强制 HTTPS 与目标 IP 校验（拦截私有、回环、链路本地网段，防范 DNS 重绑定）；严格校验 Standard Webhooks HMAC 签名与时间戳窗口；事件载荷最小化，事件触发隔离新轨迹 |
| **4. Code Mode 沙箱与 MCP Apps iframe** | 沙箱脚本试图读取宿主机磁盘、环境变量或直连外网外泄数据；恶意 MCP App 突破 iframe 偷取宿主 Cookie 或静默伪造模型上下文 | Code Mode 运行在无磁盘凭证、无直连外网的隔离沙箱，OAuth 留在沙箱外；MCP Apps 锁死受限 iframe + 严格 CSP，所有 `postMessage` JSON-RPC 经宿主白名单审计 |
| **5. 本地服务器安装与 DNS 重绑定（`SEP-1024`）** | 钓鱼链接诱导用户一键安装恶意本地 MCP Server 静默执行任意命令；远程网页通过 DNS 重绑定攻击监听 `0.0.0.0` 的本地 HTTP MCP Server | `SEP-1024` 强制规定：安装前弹窗展示**未截断的完整命令和全部参数**并要求用户显式点击确认；本地 Streamable HTTP 仅绑定 `127.0.0.1` 且强制校验 `Origin` 头，非法 `Origin` 一律回 `HTTP 403` |

此外，在企业网关侧还有一个实战细节：千万不要以为把 MCP Server 藏在公司 VPC 里就高枕无忧了。凡是支持 CIMD、远程资源读取、URL Mode Elicitation 或 Events Webhook 的服务端，都会主动向外发 HTTP 请求，必须统一经过带出口白名单与私网黑名单的专用 Egress Proxy（出口代理），不能直接给服务端容器开放任意出站网络权限。

把安全边界拉直之后，最后还剩一个最具前瞻性的架构命题：如果一台客户端已经集齐了 Code Mode、Typed Tools、Skills、Tasks、Events 和 Apps，多代理协作本身还需要另发明一套全新的协议吗？下一章我们拆解 Philipp Schmid 原文最后抛出的核心判断：把 Agent Harness 本身做成 MCP Server，以及为什么把整个 SaaS 包成单个 `ask_agent` 工具是必须避开的架构反模式。
