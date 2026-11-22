# Code Mode 与 Aimux 模型宿主

让大模型批量查 20 个文件、过滤出 3 处匹配再交叉比对，如果每次读取都走一轮“模型生成 `tool_use` → 宿主执行 → 把几千行原文塞回上下文 → 模型再调下一轮”，不仅慢、费 Token，还会把大量中间噪音永久留在对话历史里。另一方面，真正把请求发给 OpenAI、Anthropic、Gemini 或兼容网关时，各家在多模态工具结果、提示词缓存断点、HTTP 重定向安全、请求体上限和流式录制上的细微差异，稍不留神就会变成线上故障。

Dim 用两个职责极其聚焦的 crate 分别解决这两件事：`crates/dim-codemode` 和 `crates/dim-agent-aimux`。

## Code Mode：用 rquickjs 把工具编排压回单次调用

`dim-codemode` 基于 `rquickjs 0.14`（启用了 `futures` 和 `parallel` 特性），按三层作用域拆成三个部件：进程级保存运行表 `CodeModeRuns`，Session 级 `CodeModeSessionPlugin` 向 `toolRegistry` 注册名为 `codemode`（`risk: "session"`）的工具，分支级把调用接到本分支的 `ToolExecutor`。

模型调用 `codemode` 时传入的是一段纯 JavaScript 异步函数体（支持顶层 `await` 和 `return`）。`crates/dim-codemode/src/interpreter.rs` 每次执行都会新建一个全新的 `AsyncRuntime` 和 `AsyncContext`，**里面没有 Node API、没有文件系统、没有网络、没有定时器，甚至没有 `console`**。宿主往这个全新上下文里注入的唯一对象是 `tools`，只暴露三个方法：

- `tools.list()`：返回本次模型请求可见的工具摘要列表 `[{ name, description }]`（主动剔除了 `codemode` 自己，防止递归套娃）。
- `tools.describe(name)`：按需返回某个工具的完整 JSON Schema 声明。
- `tools.call(name, args)`：异步调用该工具，失败时抛出携带 `code`、`message` 和可选 `data` 的 `ToolCallError`。

这里有三条设计原则，直接体现了 Dim 不搞旁路的克制：

**第一，子调用绝不绕过权限与审批。** `tools.call` 通过 `NestedToolHost` 经过本分支的 `ToolExecutor`、`ToolBinding` 和 `Approval`，不会直接调底层文件或 Shell 函数。脚本里哪怕写了一百行循环，每一次外部读写或命令执行照样受当前会话的权限预设、文件事实和人工审批门禁约束，取消令牌也通过 `cancel.child_token()` 联动中断。

**第二，子调用结果绝不污染主模型上下文。** 脚本里调用 50 次 `read` 读出来的几兆源码，全都在 QuickJS 内存里过滤、聚合、归并；最终只有 `return` 出来的 JSON 序列化值会成为外层 `codemode` 工具的 `ToolResult.content` 返回给模型。前面第 3 章提过的 `AgentEvent::parent_call_id` 在这里正好派上用场：所有子调用在时间线上留痕时都会通过 `tokio::task_local!` 自动盖上外层 `codemode` 的 `parent_call_id`，而 `AgentEvent::context_flags()` 见到非空 `parent_call_id` 直接返回 `(false, false)`，保证子工具的庞大结果永远不会漏进后续模型请求的上下文。

**第三，硬资源边界留在宿主，不交给模型调参。** `crates/dim-codemode/src/config.rs` 给 `CodeModeConfig` 定义了八项纯宿主侧硬上限，全部不暴露成工具参数，也不写进提示词：

| 配置项 | 默认上限 | 保护目标 |
| --- | --- | --- |
| `max_code_bytes` | `256 KiB`（262,144 字节） | 限制单次提交的脚本代码长度 |
| `max_runtime_ms` | `600,000 ms`（10 分钟） | 配合 QuickJS 中断处理器强停死循环 |
| `max_calls` | `4096` 次 | 限制单次脚本最多发起的子工具调用总数 |
| `max_parallel_calls` | `16` 并发 | 用 Tokio `Semaphore` 控制脚本内 `Promise.all` 并发度 |
| `max_memory_bytes` | `1 GiB` | 通过 `runtime.set_memory_limit` 限制 QuickJS 堆内存 |
| `max_programs` | `2` | 限制同一作用域并发运行的程序数 |
| `max_output_bytes` | `16 MiB` | 限制外层 `return` 序列化结果体积 |
| `max_single_result_bytes` | `4 MiB` | 限制单个子工具结果传入 JS 内存的体积 |

当脚本超时或中途被取消时，外层错误文案还会附带一句明确提示：`Some child calls may have completed; check their effects before retrying.` 提醒模型不要因为脚本报错就盲目重跑已经产生副作用的步骤。

## Aimux 模型宿主：把协议怪癖封死在发送边界

`crates/dim-agent-aimux` 把上游 crates.io 固定版本 `=0.6.1` 的 `aimux-core`、`aimux-provider-utils`、`aimux-providers` 和 `aimux-stream` 封装成进程级 `AimuxPlugin`（提供 `ModelHost`），负责对接 OpenAI Chat Completions、OpenAI Responses、Anthropic Messages、Gemini GenerateContent、xAI、Bedrock 以及各类兼容端点。

`crates/dim-agent-aimux/README.md` 和源码里的四个工程细节，把模型接入层最容易踩的暗坑全挡在了外面：

### 1. 工具结果里的图像按协议分路，不污染历史

当工具（比如 `read` 读取截图或 OCR 布局图）返回了图像内容块时，并非所有大模型协议都允许在 `tool_result` 消息里直接夹带图片。Aimux 宿主在发送前按 `ModelProtocol` 自动分路，同时保持 Timeline 历史和存储原样不变：

- `anthropic` 与 `bedrock` 协议原生支持在 `tool_result.content` 数组里放图像块，直接内嵌发送。
- `openai-chat`、`openai-compatible`、`openai-responses`、`xai`、`xai-responses` 和 `gemini` 的工具返回字段只接受纯字符串，转换器会在工具文本原位置留一个文本标记，等该步骤所有 tool 消息结束后，紧跟补发一条 `user` 消息，按调用顺序附带全部图像及来源说明。

### 2. 发送前 64 MiB 体积闸门与严格的同源重定向

多模态输入或超大工具输出很容易拼出异常庞大的 HTTP JSON 请求体。Aimux 宿主在真正发出网络请求前，会对协议编码后的实际字节长度做一道本地硬检查，取 `64 MiB` 与当前连接声明上限的较小者；超限直接作为本地请求拒绝结束，既不发往远端，也不触发重试。

更细心的是 HTTP 重定向策略：模型请求是带 JSON body 和 API Key 的 `POST`。如果用 `reqwest` 默认的重定向策略，遇到 `301`/`302`/`303` 会被自动改成不带 body 的 `GET`，遇到跨源跳转时 `reqwest` 也只会剥离 `Authorization` 和 `Cookie`，却不会剥离 `x-api-key` 或 `x-goog-api-key`，直接造成凭据外泄。因此 Aimux 的 HTTP client 只在**完全同源（scheme、host、port 一致）**的前提下跟随 `307` 和 `308`，最多 10 跳；其余跳转一律拒绝并在 `src/driver.rs` 里把脱敏后的状态码、目标 URL 和拒绝原因补写回错误信息。

### 3. Anthropic 提示词缓存断点的精准放置

针对 Anthropic 协议，Aimux 转换器最多主动放两个 5 分钟生命周期的缓存断点（API 上限是 4 个）：第一个放在 System 最后一个块上，使工具声明和静态 System 文本合成一份跨轮复用的稳定前缀（前提是 System 没有自带显式断点）；第二个放在最后一条 `user` 或 `tool_result` 消息上（前提是总断点不超过 4 个）。如果调用方自己塞了超过 4 个显式断点，Aimux 绝不替它默默裁剪，请求发出后明确报错 `Unsupported model parameter: cacheControl breakpoint limit`。文档里甚至顺带坦承了统一打标的已知代价：压缩摘要这类一次性请求也会多付一次缓存写入费用（短于最小可缓存长度的不计费），换取的是代码路径完全统一，不需要给压缩请求开特例。

### 4. 无重试归属与 RFC-0023 录制

Aimux 适配器本身不做自动重试，HTTP 失败只保留类型化的状态码和条件码交回给上层 `ModelProvider` 决定是否退避重来；卸载时先关准入、释放未读流、排空已受理调用，再释放独占 HTTP 连接池，旧句柄一律返回 `OwnerClosed`。需要排查线上模型报文时，只需打开 `model-log` 设置源，Aimux 的 `RecordingOutcomeStream` 就会在流结束、出错或丢弃时把完整线上报文（含缓存断点）按 RFC-0023 格式写入 `<目录>/recordings.jsonl`，关闭时零调用开销。

到这里，Agent 内核里的存储、事件、分支、插件、压缩、权限、脚本沙箱和模型宿主已经全部就位。下一章把视角拉到最外层，看看守护进程 App Server、终端 CLI、Web 工作区、MCP 与 ACP 网关是如何把这套内核送到用户手里的。
