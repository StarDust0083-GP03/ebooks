# 协议演进史：从 `2024-11-05` 到 `2026-07-28`

2025 年秋天，做远程 Playwright MCP Server 的开发者踩进过一个典型的架构坑：用户在对话里先调用工具打开网页，紧接着下一轮让模型点一下登录按钮，结果浏览器状态凭空消失了。排查下来，服务器代码并没写错，问题出在协议本身的会话语义上。ChatGPT 和早期 Claude.ai 会为每一次单独的工具调用新建并立刻关闭一个 MCP 会话，桌面 IDE 客户端却在应用启动时建一个会话一直挂到进程退出，网页端则是刷新一次页面换一个会话。同一个 `Mcp-Session-Id`，在不同宿主手里对应着完全不同的生命周期。

类似的结构性摩擦在 2025 年集中爆发：远程无服务器函数（Serverless）挂不住常驻 HTTP GET SSE 连接；放在标准 L7 负载均衡后面的集群被迫开启脆弱的粘性路由；TypeScript SDK 把会话状态存进内存私有字段，节点一重启全部断线；更麻烦的是，只要协议允许服务端按会话动态篡改 `tools/list`，任何编排器每派生一个子代理，就必须把所有已连接服务器的工具列表重新拉一遍。

理解 `2026-07-28` 为什么大刀阔斧砍掉握手和会话，最直接的方式是沿着官方仓库的五个正式变更日志，把这两年的演进脉络摊开来看。

## 五个正式版本的演进主线

截至 2026 年 11 月 24 日快照，MCP 共发布过五个正式版本，加上持续累积的 `draft` 草案分支。下表对照了每个版本的关键架构转向与核验路径：

| 规范版本 | 核心架构变化 | 解决的工程痛点 | 官方变更日志路径 |
| --- | --- | --- | --- |
| `2024-11-05` | 首发 stdio 与 HTTP+SSE 双端点传输，定义 Tools / Resources / Prompts、Roots、Sampling、Logging | 打通本地进程与远程服务接入，建立最小上下文交换协议 | `docs/specification/2024-11-05/` |
| `2025-03-26` | 引入 OAuth 2.1 授权、Streamable HTTP（替代 HTTP+SSE）、JSON-RPC 批处理、Tool Annotations、音频内容块 | 合并远程双端点，补齐远程鉴权，标记只读与破坏性工具 | `docs/specification/2025-03-26/changelog.mdx` |
| `2025-06-18` | 删除 JSON-RPC 批处理；新增 `structuredContent` 与 `outputSchema`、OAuth 受保护资源元数据、RFC 8707、Form Elicitation、`MCP-Protocol-Version` 头 | 让工具返回值机器可解析，防止令牌透传与跨服务滥用 | `docs/specification/2025-06-18/changelog.mdx` |
| `2025-11-25` | 接入 OIDC Discovery 1.0、URL Mode Elicitation（SEP-1036）、CIMD 客户端注册（SEP-991）、实验性 Tasks（SEP-1686）、参数校验归入工具错误（SEP-1303）、JSON Schema 2020-12 默认方言（SEP-1613） | 补齐带外敏感授权、无预注册 OAuth、模型参数自纠错与早期异步任务 | `docs/specification/2025-11-25/changelog.mdx` |
| `2026-07-28` | 删除会话（SEP-2567）与 `initialize` 握手（SEP-2575）；引入 `server/discover`、MRTR（SEP-2322）、HTTP 头标准化（SEP-2243）、列表缓存 `ttlMs`/`cacheScope`（SEP-2549）、官方扩展框架（SEP-2133）；移出 Tasks 至扩展（SEP-2663）；弃用 Roots/Sampling/Logging（SEP-2577）与 DCR | 彻底云原生化与无状态化，控制核心规范体积，将高迭代能力移入扩展轨道 | `docs/specification/2026-07-28/changelog.mdx` |

紧跟 `2026-07-28` 之后的 `docs/specification/draft/changelog.mdx` 只有两项小幅修订：把 OAuth Client ID Metadata Document 引用从 `draft-00` 升级到 `draft-03`，以及补回在 `2026-07-28` 重构时误删的 Elicitation/Sampling/Roots 参数与结果中的可选 `_meta` 字段（Issue `#3468`）。`2026-07-28` 已经构成了 2027 年各方实现依赖的稳定地基。

## 为什么必须砍掉有状态会话与常驻双向流

很多习惯了 WebSocket 或本地 stdio 开发的工程师最初会怀疑：把会话改成可选不行吗，为什么要做不向后兼容的彻底删除？`SEP-2567` 和 `SEP-2575` 给出了非常硬的工程证据。

最直接的瓶颈出在**子代理场景下的列表缓存失效**。只要协议规范允许某个服务器把 `tools/list` 绑定到会话状态（比如先调 `connect_database`，后续列表里才凭空多出 `query` 工具），客户端在连接任意陌生服务器时就无法预先判定它会不会变脸。结果就是编排器每启动一个短生命周期子代理，面对 $M$ 个服务器，就必须重跑一遍 `initialize` 和 `tools/list`，整体发现流量变成 $O(\text{subagents} \times \text{servers})$。即使 99% 的服务器工具列表在编译期就固定了，哪怕把会话改成可选项，客户端照样不敢跨会话缓存。彻底删掉会话并规定列表不得随连接状态变化，再配合 `SEP-2549` 的 `ttlMs` 缓存提示，父代理拉一次列表，所有子代理就能零成本复用，开销直接降到 $O(\text{servers})$。

另一个结构性硬伤是**单会话固定基数（Cardinality）与多代理共享冲突**。一个会话在协议层只能隐式挂载一份状态。假设主代理派生三个子代理一起帮用户选商品，它们需要往同一个购物车里加东西，同时各自打开独立的浏览器上下文去查不同网站。在隐式会话模型里，这是一个无解的死结：

| 会话分配策略 | 购物车（期望：共享） | 浏览器上下文（期望：隔离） |
| --- | --- | --- |
| 子代理共享父会话 | 正常共享 | 互相踩踏、状态错乱 |
| 子代理各自建新会话 | 各自孤立、无法汇总 | 正常隔离 |

换成显式状态句柄（Explicit State Handles）后，编排器调一次 `create_basket()` 拿到 `basket_id` 发给所有子代理，每个子代理再各自调 `create_browser()` 拿到自己的 `browser_id`。协议本身甚至不需要定义新原语，句柄只是工具返回值和入参里的普通字符串，却天然支持跨对话恢复、跨代理移交和细粒度权限校验。

更关键的现实数据是**真实仓库里几乎没人真在用会话存业务状态**。`SEP-2567` 公布了对 1000 个随机开源 MCP Server 仓库的自动化扫描结果：

- **90.0%** 的仓库在应用层根本没有引用过 MCP Session ID；
- **3.5%** 只是复制了 TypeScript SDK 样板里的 `Map<sessionId, Transport>` 路由表；
- **2.8%** 只传了 `sessionIdGenerator` 构造参数，业务代码从未读取；
- 真正用会话做应用层状态存储的只占 **2.5%**，做网关粘性路由的占 **0.7%**，把 OAuth 状态绑在会话上的占 **0.5%**。

为了这不到 4% 且语义本就混乱的存量用法，让所有客户端和服务端承担粘性负载均衡、重启丢状态、无法缓存列表的全局税，显然不划算。

与此同时，`SEP-2575` 顺手清理了依赖持久连接的一整批旧机制：删除 HTTP GET 独立 SSE 端点、删除 `Last-Event-ID` 断流续传（需要持久化和断线恢复的作业统一交给 MCP Tasks）、删除 `ping`、`logging/setLevel` 以及 `resources/subscribe`/`unsubscribe`。客户端只在真正需要接收列表变更或资源更新时，才发一条 POST 请求调用 `subscriptions/listen`，按需换回一条带 `io.modelcontextprotocol/subscriptionId` 标签的 SSE 响应流。这种按需付费（pay as you go）原则让绝大多数普通请求退化成了标准的一问一答 HTTP POST。

## `SEP-2577` 与弃用治理：核心协议的瘦身决心

协议成熟的一个重要标志，是敢于把早期设想美好但现实中无人维护的特性清出核心。`2026-07-28` 同步落地了 `SEP-2596` 特性生命周期政策，规定任何特性进入 Deprecated 状态后，享有至少 12 个月的宽限窗口，期间线协议行为保持不变，之后在最早可移除日期后的版本中正式删除。

下表整理自 `docs/specification/2026-07-28/deprecated.mdx`，列出当前已进入弃用清单的全部核心特性：

| 已弃用特性 | 弃用提案 | 弃用版本 | 官方推荐迁移路径 | 最早可移除时间 |
| --- | --- | --- | --- | --- |
| Roots（`roots/list` 等） | SEP-2577 | `2026-07-28` | 通过工具参数、资源 URI 或服务端配置显式传递目录与文件 | 2027-07-28 及之后的首个版本 |
| Sampling（`sampling/createMessage`） | SEP-2577 | `2026-07-28` | 服务端直接调用 LLM 提供商 API，避免反向让客户端代跑推理 | 2027-07-28 及之后的首个版本 |
| Logging（`logging/setLevel` 等） | SEP-2577 | `2026-07-28` | stdio 直接写 `stderr`，远程服务使用 OpenTelemetry（配合 SEP-414 `_meta` 透传 trace） | 2027-07-28 及之后的首个版本 |
| Dynamic Client Registration（RFC 7591） | PR #2858 | `2026-07-28` | 迁移至 Client ID Metadata Documents（CIMD，SEP-991） | 2027-07-28 及之后的首个版本 |
| Sampling `includeContext` 非 `none` 值 | SEP-2596 | `2025-11-25` | 省略该字段或设为 `"none"` | 随 Sampling 一并移除 |
| HTTP+SSE 传输 | SEP-2596 | `2025-03-26` | 迁移至 Streamable HTTP | SEP-2596 终稿后满 3 个月 |

`SEP-2577` 弃用 Roots、Sampling 和 Logging 背后的判断很清醒。Roots 在规范里只是信息性提示，服务端并不受强制约束，反而暴露了客户端本地目录结构；Sampling 允许服务端反向要求客户端执行模型补全，甚至在 `SEP-1577` 加入带工具循环的采样后，客户端要同时处理人工审批、模型路由、防提示注入和防数据外泄，复杂度极高而采用率极低；Logging 则纯粹是在应用协议层重复造轮子，远程服务本就有 OpenTelemetry，本地子进程直接写 `stderr` 更简单可靠。

把这些高维护成本、低回报或边界模糊的机制清理出核心，再把 Tasks、Skills、Events、Apps 和企业授权放进独立版本化的官方扩展轨道，MCP 核心层在 `2026-07-28` 终于收敛成了一层精干、无状态、标准 HTTP 友好的传输与能力基座。接下来的一章，我们就钻进这层无状态 HTTP 内核，看看每一个请求头、错误码和缓存字段是怎么咬合在一起的。
