# 响应式工具协议 RAP：断连回调、订阅与 MCP 桥接

用过 MCP（Model Context Protocol）写长耗时工具的工程师都遇到过同样的别扭事：MCP 天生是面向持久会话的请求响应协议，无论是本地 stdio 子进程管道，还是远程 Streamable HTTP 会话，Agent 运行时都得一直挂着连接等回包。查个天气、读个文件自然够用；一旦要触发二十分钟的编译测试、订阅某个 GitHub 仓库持续一周的 PR 事件，或者中途弹出人工审批等待两小时，连接一旦断开或容器被回收，上下文就全散了。于是每个团队只能各自在业务代码里拼凑轮询循环、Webhook 胶水和状态表。

Infinity 给出的解法是把工具协议本身翻转过来，定义了 **RAP（Reactive Agent Protocol）**。核心代码分散在 `rap-protocol`、`rap-client`、`infinity-rap-bridge` 和 `infinity-mcp-bridge` 四个 crate 中。

![RAP 响应式工具协议全链路与五类回调分流](../../wiki/assets/diagrams/rap-lifecycle.svg)

## 清单发现与即发即忘调用

一个标准 RAP 服务本质上就是一个普通的无状态 HTTP 服务，它完全不知道调用方背后跑的是什么大模型，也不保存对话历史。

接入从工具清单发现开始。运行时向工具服务的 `GET /.well-known/rap-toolset` 发请求，拿到 `ToolsetManifest`（`crates/rap-protocol/src/lib.rs` 第 212 行）：

```rust
pub struct ToolsetManifest {
    pub name: String,
    pub description: Option<String>,
    pub endpoint: String,
    pub tools: Vec<ToolDef>,
    #[serde(default, rename = "needsMigration")]
    pub needs_migration: bool,
}
```

清单里有两个很有工程味的字段。一个是 `needsMigration`，布尔值，向守护进程声明这个工具服务在本地持有不可丢弃的外部状态（比如 `sandbox-local` 里的 Jujutsu 书签和工作区），跨主机热迁移会话时必须先通知它搬家。另一个在每条 `ToolDef` 上，叫 `displayScript`，允许工具作者附带一段简短的 Rhai 脚本。核心运行时里的 `eval_display_script`（`crates/infinity-agent-core/src/tools/mod.rs` 第 117 行）会把模型传入的参数灌进 `args` 变量里求值，生成人类易读的工具调用摘要（例如 `sleep` 工具自带脚本把秒数和原因拼成 `"Sleeping 30s: wait for CI"`），而不用在前端硬编码每种工具的渲染逻辑。

模型决定调用工具时，核心层经由 `invoke_rap_tool`（`crates/infinity-agent-core/src/tools/rap_tool.rs` 第 72 行）向清单里的 `endpoint`（通常是 `/invoke`）发一次 HTTP POST，正文是 `RapInvocation`：

- `operation` 与 `arguments`，工具名与 JSON 参数。
- `id: ToolCallId` 与 `call_id: Option<ProviderCallId>`，工具回包时原样带回。
- `group_id: ThreadId` 与 `thread_ancestors: Option<Vec<ThreadId>>`，标明当前线程以及从根到父线程的完整祖先链（沙箱服务正是靠 `thread_ancestors` 继承父线程的写权限授权）。
- `callback_url`，异步结果该往哪里投递。
- `user_id`，可选用户身份。

错误处理策略在本地和云端完全一致：如果 HTTP POST 遇到网络层传输失败，`invoke_rap_tool` 返回 `Err`，由运行时兜底回注一条通用失败结果；如果服务端可达但返回了非 `2xx` 状态码，说明服务端拒绝了调用、绝不会有后续回调，函数直接合成一条包含操作名、URL 和状态码的错误 `ToolResult` 投回输入队列并返回 `Ok(())`，让模型马上知情并调整策略，绝不会把线程挂死在永久等待上。只要收到 `2xx`，调用立刻返回，切片结束，运行时可以安心休眠。

## 五类回调与统一转换入口

几百毫秒或几天之后，工具干完活，把一个带 `type` 标签的 JSON 对象 `RapCallback` POST 到 `callback_url`。`rap-protocol` 定义了五种回调变体：

| 变体 (`type`) | 携带核心字段 | 运行时处理去向 |
| --- | --- | --- |
| `tool_result` | `id`, `call_id`, `text` 或多模态 `content`, `display_as`, `subscription` | 转为 `InputMessage` 入队；若 `subscription: true` 则在 `StateStore` 登记活跃订阅 |
| `subscription_event` | `tool_call_id`, `text`, `associative`, `final` | 默认派生隔离事件子线程；`associative: true` 时内联注入；`final: true` 清理订阅追踪 |
| `oauth` | `id`, `call_id`, `auth_url` | 经 `PrepareResult::OAuthRequired` 透传给前端引导授权，不进模型历史 |
| `user_choice` | `id: ChoiceId`, `prompt`, `choices`, `default`, `response_url` | 写入 `StateStore` 挂起列表并透传前端弹窗，用户选完后由客户端 POST 回 `response_url` |
| `view_update` | `group_id`, `view_type`, `content` | 旁路展示通道（如沙箱实时 Diff 视图），守护进程透传给 UI，Lambda 接收后直接返回 200 丢弃 |

注意 `tool_result` 里模型视角与人类视角的分离设计：模型看到的是 `content`（支持文本与 Base64 图像块 `RapToolResultContent::Image`；如果当前模型不支持图像输入，`HistoryManager::get_history` 会在发出请求前自动替换成 `IMAGE_OMITTED_PLACEHOLDER`）或纯 `text`；而人类在 TUI/Web 上看到的是 `display_as` 里的结构化 `DisplaySegment`（支持 `Text`、`Diff`、`Image`），`build_display_segments` 会把工具提供的美化片段排在前面、原始文本兜底挂在末尾。

为了防止本地守护进程和云端 AWS Lambda 在回调转换上出现行为漂移，所有平台都强制共用 `crates/infinity-rap-bridge/src/callback.rs` 里的 `prepare_callback`（底层调用 `convert_callback`）。守护进程的本地回调监听器和 `infinity-agent-lambda` 的 `rap-receiver` 二进制都只做网络收发，消息语义转换全收口在这一处。

## 生命周期通知：关闭、取消与迁移

异步协议一旦放手让工具独立运行，就必须处理三件善后事：线程关了怎么回收外部资源，用户按 Ctrl+S 打断时怎么停掉正在跑的构建，会话搬家到另一台机器时怎么把外部状态一起搬走。

`crates/rap-client/src/notifier.rs` 的 `RapNotifier` 封装了这三条尽力而为（best-effort）的 HTTP POST 端点：

- `POST /close_thread`，携带 `{ "thread_id": ... }`，在 `CloseThreadTool` 关闭子线程后广播，比如通知 `sandbox-local` 执行 `jj workspace forget` 并删掉缓存目录。
- `POST /cancel_tool_call`，携带 `{ "thread_id": ..., "tool_call_id": ... }`，当用户文本打断挂起工具调用后触发，比如通知 `sandbox-core` 给正在运行的子进程组发 `SIGTERM`。
- `POST /migrate`，携带 `{ "session_id": ..., "destination_url": ... }`，由热迁移编排器定向发给声明了 `needsMigration: true` 的服务，触发状态打包传输。

前两条通知故意设计成 best-effort，即便某个外部工具服务已经离线或没实现该路由，也只记警告日志，不阻断线程关闭或用户打断。

## 仓库内置的两个真实 RAP 服务

光看协议定义容易抽象，仓库里自带了两个很紧凑的参考实现：

1. **`rap-steering-server`**（348 行），暴露两个操作：扫描项目根目录和家目录里的规则约定文件（`INFINITY.md`、`CLAUDE.md`、`AGENTS.md`、`.cursorrules`、`.claude/skills` 等目录）并按规范路径去重符号链接，以及带路径穿越校验的安全读取。
2. **`rap-github-event-poller`**（715 行），展示了标准 RAP 订阅服务的写法。收到订阅调用后，它先登记 `(owner/repo, tool_call_id, filters, callback_url)` 并立即回传带 `subscription: true` 的 `tool_result`；后台轮询任务按仓库共享一个 ETag 和 GitHub 响应头里的 `X-Poll-Interval`（默认 60 秒），首次轮询只记录 `last_seen_id` 跳过历史旧事件，此后每出现匹配过滤条件的新事件就通过 `send_subscription_event` 推送回 Agent 的 `callback_url`。没有活跃订阅时后台轮询任务靠 `tokio::sync::Notify` 挂起零开销，线程关闭或取消订阅时移除条目。

## 兼容现有 MCP 工具：infinity-mcp-bridge 如何桥接

协议再先进，如果不能直接复用现有的 MCP 服务器，落地成本也会太高。`crates/infinity-mcp-bridge/src/lib.rs`（797 行）在客户端侧做了一层干净的适配。

它定义了 `McpTransport` trait，并内置了两种传输实现：`StdioMcpTransport` 启动本地子进程（带 `kill_on_drop(true)` 防止孤儿进程），按换行分隔的 JSON-RPC 2.0 读写；`HttpMcpTransport` 对接 Streamable HTTP MCP 服务，自动处理 `initialize` + `notifications/initialized` 握手、维护 `mcp-session-id` 请求头，并能同时解析普通 JSON 响应与 `text/event-stream` SSE 响应。

顶层的 `McpToolSet` 通过 `McpTransportFactory` 延迟建立连接，把 MCP 服务器暴露出的每个工具包装成核心运行时认识的标准 `Tool` 实现。调用发生时，桥接层在后台任务里等待 JSON-RPC 返回，再把结果转换成 `InputMessage` 投回运行时输入队列。对于本地常驻模式，这意味着任何现有 MCP 工具零改动就能并入 Infinity 的并发线程与统一展示链路；而在无服务器 Lambda 模式下，仓库的 `agent/` 目录则允许把 MCP 服务包在独立的远程代理层之后，对外呈现为标准无状态 RAP 端点。

协议层的收发机制清楚了，但还有一个关键问题悬着：当订阅事件不断涌入、或者父 Agent 一口气派生五个子 Agent 并行干活时，上下文不会乱套吗？下一章进入线程树与位置索引压缩。
