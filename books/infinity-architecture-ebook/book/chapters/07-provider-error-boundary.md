# 模型供应协议、跨进程隔离与错误分类契约

做过多模型接入的团队大多写过这种越改越脏的代码：核心 Agent 循环里嵌着一大段 `if err.contains("Throttling") || err.contains("429") || err.contains("too many requests")`，旁边又叠着一个 120 秒的流式空闲超时定时器。结果一换模型供应商，报错措辞变了，限流被当成致命错误直接放弃；一碰上推理模型思考两分半钟不吐字，核心层的空闲超时把好好的流强行掐断，触发毫无意义的重试，既烧双倍 token 又让用户干等。

Infinity 在 `0.2.0`（PR #121）里把这套脆弱的耦合彻底拆干净了。整个模型供应子系统由三个 crate 组成：`infinity-provider-protocol`（1,280 行）定义协议与跨进程传输，`infinity-provider-bedrock`（1,445 行）对接 AWS Bedrock ConverseStream，`infinity-provider-rig`（504 行）基于 `rig-core` 对接 OpenAI 兼容服务。

## ErrorClass 契约：供应器分类，核心层只看枚举

翻开 `crates/infinity-provider-protocol/src/completion.rs`，核心运行时对模型失败的所有认知，全部收敛在四个枚举变体里：

```rust
pub enum ErrorClass {
    Transient,       // 瞬时故障：断流、后端抖动、建连超时，可短退避重试
    Throttled,       // 限流配额不足：需要更长退避后重试
    ContextOverflow, // 上下文超窗：原样重试必败，必须收缩输入或压缩
    Fatal,           // 永久错误：非法请求、未知模型、无权限，立刻放弃
}
```

`CompletionError::class()` 把四类包装错误也归口到这四个值：`RequestError` 和 `JsonError` 属于本地构造或解析出错，归为 `Fatal`；`ResponseError` 属于读取流时的传输帧故障，归为 `Transient`；`ProviderError { message, class }` 直接携带供应器自己填好的 `ErrorClass`。

这样一来，`infinity-agent-core` 里的 `run_completion` 彻底不再做任何字符串匹配，也删掉了原本硬编码的 60 秒建连超时和 120 秒流中途停顿定时器。它的重试状态机变得极度干净（`event_processor.rs` 第 1404 至 1599 行）：

- 收到 `ErrorClass::Throttled` 且未超过 `MAX_COMPLETION_RETRIES`（10 次）：休眠 `THROTTLED_RETRY_DELAY`（30 秒，休眠期间仍响应用户 `cancel_rx` 中断）后重试。
- 收到 `ErrorClass::Transient` 且未超过 10 次：休眠 `TRANSIENT_RETRY_DELAY`（5 秒，可取消）后重试；若中途已经吐过部分块，先调用 `history.discard_turn()` 扔掉脏缓冲，再从干净历史重发。
- 收到 `ErrorClass::ContextOverflow`：不走普通退避，直接转交第 3 章拆解过的 `recover_from_overflow`。
- 收到 `ErrorClass::Fatal`：直接报错退出，一次都不重试。

超时策略也同步下放给供应器自己决定。核心层自己**零超时**，理由很务实：不同后端的延迟特征天差地别，核心层根本猜不准一次深度推理要思考多久。

## Bedrock 供应器的真实工程细节

`crates/infinity-provider-bedrock/src/lib.rs` 浓缩了不少对接真实生产云服务踩出来的坑，读它的注释比读官方文档还解渴：

1. **只卡建连，绝不掐活流**，Bedrock 偶尔会出现请求黑洞（发出去既不返回也不报错），因此 `BedrockProvider` 在 `ConverseStream` 请求发起阶段加了 60 秒的 `REQUEST_INITIATION_TIMEOUT`，超时后显式包装成 `ErrorClass::Transient` 交给核心层重试。一旦流式响应建立，代码里**刻意不设任何中途停顿或读取超时**，宁可相信底层连接与模型思考，也绝不人为掐断正常长推理。
2. **细粒度工具流 Beta 绕开服务端看门狗**，Bedrock 上调用 Anthropic 模型吐出一个很大的 `edit_file` 或 `create_file` 工具参数时，默认情况下后端会在服务端把整段工具输入缓冲校验完才开始下发。这中间长达数十秒的静默空档经常触发 Bedrock 自己的流看门狗，直接报 `ModelTimeoutException`。因此供应器主动附带了 `fine-grained-tool-streaming-2025-05-14` Beta 请求头，让工具参数碎片边生成边下发、流永不静默。源码同时坦诚写明了代价：服务端不再替客户端预校验工具 JSON，碎片拼完后可能解析失败，这部分交由 `stream.rs` 里的 `finish_tool_call` 单独分类处理。此外还启用了 `context-1m-2025-08-07` 百万上下文 Beta 标志，并自动在每次请求的最后一条消息末尾追加 Prompt Caching 缓存点。
3. **集中式错误映射**，`classify_bedrock_error` 根据 AWS SDK 错误码和消息启发式完成映射：`ThrottlingException` / `ServiceQuotaExceededException` 归 `Throttled`；包含 `"too long"`、`"too large"`、`"input length"` 或同时含 `"exceed"` 与 `"context"` 的 `ValidationException` 归 `ContextOverflow`；`InternalServerException`、`ServiceUnavailableException`、`ModelTimeoutException`、`ModelNotReadyException`、`ModelStreamErrorException`、`ModelErrorException` 以及 `"unexpected end of stream"` 归 `Transient`；其余默认 `Fatal`。`infinity-provider-rig` 则在自己 crate 里维护针对 OpenAI 兼容接口的同类映射。
4. **反套娃的真实集成测试**，除了常规单测，`infinity-provider-bedrock` 还准备了非默认特性 `live-tests`（`tests/live.rs`），直接用本地 AWS 凭证打真实 Bedrock 端点验证超窗分类、未知模型致命错误和正常流式路径。变更记录里特意提到，正是跑真实测试才抓出了开发期一个算错的 token 估算问题，而光靠写死的单元测试字符串对照表永远测不出真实服务改文案。

## 插件化架构：通过 Unix Socket 跨进程运行供应器

在 Lambda 部署里，模型供应器直接作为 Rust 库静态链接进二进制即可。但在本地产品 Infinity Code 里，用户希望像装插件一样执行 `infinity provider install bedrock --crate infinity-provider-bedrock`，随时安装、升级、混用多个不同供应器（比如同时装 Bedrock 和 OpenAI 兼容供应器，在不同线程间实时 `/model` 切换），而不必把所有云厂商的庞大 SDK 全编进主二进制 `infinity-daemon`。

`crates/infinity-provider-protocol/src/remote.rs` 用不到五百行代码搭出了一套极其清爽的跨进程供应器协议：

- **启动与发现**：每个独立供应器二进制启动后调用 `serve_provider(Arc<dyn ModelProvider>)`，在系统临时目录生成一个短路径 Unix Domain Socket（格式 `inf-provider-<12位hex>.sock`，特意压短路径长度以避开 macOS 上约 104 字节的 Unix Socket 路径上限），把 socket 路径打印到 stdout 交给守护进程读取，然后进入监听循环。
- **并发连接模型**：协议使用 `LinesCodec` 承载换行分隔的 JSON（JSON Lines），**每次请求新建一条独立的 Unix Socket 连接**。这个朴素的选择省掉了在单连接上写多路复用帧 ID、反压和队头阻塞的整套复杂性：十个线程同时推理，就是十条并行的本地 socket 连接，各自独立读写互不干扰。
- **两种请求与流式帧**：`ProviderRequest::ListModels` 返回单行 `Models(Vec<ModelEntry>)` 或 `Error(WireError)`；`ProviderRequest::InvokeModel` 返回 `InvokeStarted`，随后逐行流式推送 `Chunk(Result<StreamChunk, WireError>)`，最后以 `StreamEnd` 收尾。注意流中途的错误也能作为 `Chunk(Err(WireError))` 沿流透传，与进程内流行为保持完全同构。
- **跨进程保留错误分类**：错误在 socket 上包装成 `WireError { message, class }`，反序列化时如果对方是老版本供应器漏掉了 `class` 字段，默认兜底为安全的 `ErrorClass::Fatal`（不盲目重试）。于是 `ErrorClass` 跨越了进程边界，客户端侧的 `RemoteModelProvider` 对 `infinity-agent-core` 呈现出与进程内供应器一模一样的 `ModelProvider` trait。

到这里，Agent 怎么运行切片、怎么管历史、怎么调异步工具、怎么分线程、怎么调度本地并发以及怎么连大模型，全都拼装完整了。但对于一个 Coding Agent 来说，最要命的现场还没解决：当三个并发线程同时改写同一个代码仓库时，怎么不把代码改烂，又不让模型一条 `rm -rf` 误伤开发者的宿主目录？下一章拆解堆叠沙箱子系统。
