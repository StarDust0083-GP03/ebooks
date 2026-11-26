# 长程异步执行：MCP Tasks

在 `2026-07-28` 之前，想让一个 MCP 工具跑一趟 8 分钟的端到端测试或者云端视频转码，开发者手里只有两个别扭的选择。要么硬扛着 HTTP 连接不放，祈祷沿途的 Nginx、Cloudflare、公司出口代理和客户端 SDK 全都别在 60 秒或 120 秒时把空闲连接掐断；要么在业务层自己手搓一套 `start_job` / `check_job_status` / `get_job_result` 的自定义工具三件套，每家服务器字段名和状态枚举各写各的，客户端宿主完全看不出这是一条挂起中的异步任务，断线重启后也无法统一接管恢复。

`2025-11-25` 曾经通过 `SEP-1686` 把实验性 Tasks 塞进过核心规范，但经过大半年真实落地，暴露出三个结构性毛病。第一，握手极其脆弱：客户端必须先调 `tools/list` 查每个工具的 `execution.taskSupport`，才能决定调用时带不带 `task` 参数，不能用统一方式透明调用所有工具。第二，`tasks/result` 是个阻塞陷阱：旧设计要求任务进入 `input_required` 时，客户端提前调用阻塞式的 `tasks/result` 撑开一条 SSE 通道，好让服务端倒着往回塞 `elicitation/create` 请求，这直接撞上了 `2026-07-28` 禁止服务端主动发请求的铁律（`SEP-2260`）。第三，`tasks/list` 在无会话架构下根本没法安全界定归属范围：删掉 `Mcp-Session-Id` 后，对于未鉴权或弱绑定场景，服务端只认一个个独立的高熵 `taskId`，一旦开放全局 `tasks/list` 就会导致跨用户任务泄露。

于是 `2026-07-28` 通过 **`SEP-2663`** 做了一次彻底重设计：把旧版实验 Tasks 移出核心，发布为官方扩展 **`io.modelcontextprotocol/tasks`**（目前处于 Beta 阶段，路线图明确标定目标为成熟后晋升 Core），用一套干脆利落的纯异步状态机彻底替换掉了阻塞式旧模型。

## 服务端主导的多态返回：告别 `tools/list` 预热

新版 MCP Tasks 最舒服的改动，是把要不要创建异步任务的决定权完全交给了服务端，客户端再也不用提前探测每个工具支不支持异步（`docs/seps/2663-tasks-extension.mdx`）。

协商只需要一步：服务端在 `server/discover` 里声明 `"io.modelcontextprotocol/tasks": {}`，客户端在每条请求的 `_meta["io.modelcontextprotocol/clientCapabilities"].extensions` 里带上同一键名。声明之后，客户端照常发一条普通的 `tools/call` 请求，既不需要多传特殊标志，也不用先拉 `tools/list`。服务端根据当前任务的实际预估耗时，**按单次请求动态决策**：

- 几百毫秒能算完的，直接返回标准 `CallToolResult`（`resultType: "complete"`）。
- 需要几分钟跑完的，先把任务持久化落盘，再返回 `CreateTaskResult`（`resultType: "task"`）。

注意规范的持久化顺序要求：服务端在返回 `CreateTaskResult` 之前，任务**必须已经持久化落成**，确保客户端紧接着发来的第一次 `tasks/get` 一定能查到，在最终一致性存储上也要等写入可见后再回包。如果一个工具必须以异步任务运行，但来访客户端的请求 `_meta` 里没有声明 `io.modelcontextprotocol/tasks`，服务端绝不能擅自返回 `CreateTaskResult`，而是必须返回 `-32021`（`MissingRequiredClientCapability`）并在 `data.requiredCapabilities` 里指明缺少该扩展。

对于不想改动上层业务签名的客户端 SDK，规范给出了很实用的实现提示：SDK 内部完全可以在 `callTool()` 里透明拦截 `resultType: "task"`，自动走完后台轮询并把最终 `CallToolResult` 返回给调用方；而具备异步编排能力的高级 Agent 宿主，则可以把 `taskId` 直接暴露为后台作业句柄，让主代理一边并行干别的事，一边等任务终态。

## 五态生命周期与三个精简 RPC

删掉阻塞式的 `tasks/result` 和高风险的 `tasks/list` 后，整个 Tasks 扩展只剩三个清晰的方法：`tasks/get`、`tasks/update` 和 `tasks/cancel`，围绕五个状态流转（`docs/extensions/tasks/overview.mdx`）：

| 任务状态 | 性质 | 携带附加字段 | 含义与流转规则 |
| --- | --- | --- | --- |
| `working` | 进行中 | 可选 `statusMessage` | 任务正在后台执行 |
| `input_required` | 进行中（挂起） | `inputRequests` | 执行中途需要客户端提供人工确认或输入，等待 `tasks/update` |
| `completed` | 终态（不可变） | `result` | 原始请求执行完毕，内联原始结果（包含 `isError: true` 的工具业务报错） |
| `failed` | 终态（不可变） | `error` | 执行期间发生 JSON-RPC 协议级错误（不得混用普通业务错误） |
| `cancelled` | 终态（不可变） | 可选 `statusMessage` | 任务在完成前被取消 |

一旦进入 `completed`、`failed` 或 `cancelled` 三个终态之一，任务状态永久冻结，不再变化。还要留意一个容易写错的细节：`completed` 和 `failed` 的划分严格对齐 `SEP-1303`，如果一个工具任务正常跑完了但业务上返回了 `isError: true`（比如目标仓库里测试没过），任务状态应当是 **`completed`** 且在 `result` 里原样携带带 `isError: true` 的 `CallToolResult`，让模型看到测试失败详情；只有发生 JSON-RPC 级异常（如服务端内部崩溃或任务超过 TTL 失效）才用 `failed`。

在 `tasks/get` 轮询中，服务端通过 `pollIntervalMs` 给出建议轮询间隔（且允许在任务生命周期内动态调整，比如前期慢、收尾快），客户端应当遵守该间隔，以免被服务端限流，同时**应当持久化 `taskId`**，这样哪怕客户端进程崩溃或网络断开，重启后照样能凭 ID 接上进度。如果不想纯轮询，服务端也可以支持通过 `subscriptions/listen` 传入 `notifications: { taskIds: [...] }`，由服务端在状态变化时直接推送携带完整 `DetailedTask` 的 `notifications/tasks` 通知，省掉额外一次 `tasks/get` 往返。注意，Tasks 扩展明确禁止在监听流上混发 `notifications/progress` 或 `notifications/message`。

## 任务中途追问（`tasks/update`）与协作取消（`tasks/cancel`）

长程任务跑着跑着常常需要人工拍板，比如批量迁移脚本扫出 3 张高危表，需要管理员确认后再继续删旧索引。`SEP-2663` 复用了 MRTR 的 `inputRequests`/`inputResponses` 数据结构，但明确区分了两个时机：

- **建任务之前**需要补充信息（比如连参数都没齐、还决定不了要不要建任务）：直接在原始 `tools/call` 上同步走第 5 章的 MRTR（`InputRequiredResult`）解决。
- **建任务之后**执行到半途需要输入：任务状态切到 `input_required`，在下一次 `tasks/get`（或 `notifications/tasks`）里附带当前所有未满足的 `inputRequests` 快照。

客户端看到 `input_required` 后，把请求呈现给用户，拿到答复后调用 `tasks/update` 提交 `inputResponses`，服务端返回空确认即可（允许最终一致性，不必等后台状态立刻翻回 `working` 才回包）。为了防止连续两次轮询把同一个确认弹窗反复弹给用户，规范定了两条互保约束：服务端保证同一个 `taskId` 整个生命周期内，**`inputRequests` 的每个键名绝不复用**；客户端按已处理键名做去重，服务端则静默忽略重复或已失效的键。

取消任务同样走专门的 `tasks/cancel` 方法，规范明文禁止用核心的 `notifications/cancelled` 去取消 Task。取消是**协作式（Cooperative）且最终一致的**：服务端收到 `tasks/cancel` 只需立刻返回空确认并尽量通知后台作业停手，但并不保证一定能拦住已经走到最后一毫秒的写入，因此任务最终仍有可能落到 `completed` 而非 `cancelled`；客户端发完取消后即可自行清理本地记录，无需死等状态翻转。

## 路由头、高熵能力令牌与 TTL 安全边界

把 Tasks 跑在分布式 HTTP 集群里，还有三道架构与安全约束不能漏：

1. **`Mcp-Name` 承载 `taskId` 实现网关路由**：在 Streamable HTTP 下，发送 `tasks/get`、`tasks/update` 或 `tasks/cancel` 时，客户端**必须**把 HTTP 头 `Mcp-Name` 设为 `params.taskId`（`Mcp-Method` 照常设为对应方法名）。如果某些服务端的后台作业状态只存在特定分片或节点上，中间网关无需拆解 JSON Body 就能按 `Mcp-Name` 精准路由。
2. **高熵 `taskId` 与权限重验**：如果服务端有 OAuth 2.1 鉴权，每次 `tasks/get`、`tasks/update` 和 `tasks/cancel` 都必须重新校验调用方身份是否匹配任务创建者（`owner_principal`），不能只凭猜到或捡到一个 `taskId` 就偷看别人任务的结果；对于无鉴权场景，`taskId` 必须具备至少 128 位密码学随机熵（例如 32 位十六进制随机串或 UUIDv4），防止枚举撞库。
3. **`ttlMs` 兜底与资源回收**：每个任务都有自创建起算的 `ttlMs`（`null` 表示无过期上限）。超过 `createdAt + ttlMs` 后，服务端有权把未完成任务标为 `failed` 并回收存储，客户端也应把超时未刷新的任务视作失效，避免僵尸轮询永远挂在调度器里。

Tasks 解决的是 Agent 自己发起长耗时作业后如何持续追踪直到完成。但现实工作流还有另一半完全相反的方向：凌晨两点生产环境爆了一条 P0 安全告警、客户发来一封紧急索赔邮件、或者 GitHub 仓库里合并了一个新 PR，此时用户压根没打开对话框，Agent 根本不知道发生了什么。下一章我们看让 Agent 真正跨入常驻与多人协作时代的 **MCP Events**。
