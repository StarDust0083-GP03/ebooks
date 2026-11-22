# 阻塞式 Agent 的死结与无阻塞切片不变量

让一个编码 Agent 在真实仓库里跑 `cargo test --workspace`，或者让运维 Agent 等一个 GitHub PR 通过人工审批，传统同步循环很快会露馅。模型在第 1 秒吐出一条工具调用，工具需要跑二十分钟，MCP 的 stdio 管道或 HTTP SSE 会话就必须一直开着，调用栈上的异步任务、内存里的整段对话历史和宿主进程谁都不能退。把这套模式搬到多 Agent 并行场景，问题会成倍放大：一百个等待 Webhook 的 Agent 占着一百份常驻内存和一百条长连接；放到 AWS Lambda 这类按毫秒计费、单次调用最多十五分钟的无服务器平台上，等待本身就直接变成超时故障和纯浪费账单。

## 为什么传统 Agent 架构卡在等待上

常规 Agent 框架把一轮多步任务写成一条长循环：`while !done { call_llm(); await call_tool(); }`。这种写法把两件性质截然不同的事情绑死在同一个操作系统进程生命周期里。

大模型推理和本地状态更新属于短时、有界的计算，通常几秒到几十秒结束。而外部世界里的工具耗时完全无界：跑完一轮集成测试可能要二十分钟，等一位同事在 Slack 里点下确认按钮可能要两小时，订阅一个仓库的 PR 动态更是按周甚至按月计算。一旦运行时在 `await call_tool()` 上挂住，进程亲和性就被锁死了。结果回包必须回到原来那台机器、原来的那个进程、原来的那条管道；机器重启或容器回收，这轮任务就断了线。

![阻塞式 Agent 与 Infinity 无阻塞切片对比](../../wiki/assets/diagrams/blocking-vs-slice.svg)

上面这张图对比了两种执行模型的分叉点。上半部分是传统阻塞循环，进程在二十分钟等待期里全程在线；下半部分是 Infinity 的解法，把长循环切碎成一个个彼此独立、绝不等待外部 I/O 的执行切片（slice）。

## 一条核心不变量：切片绝不阻塞

阅读 `docs/docs/infinity-runtime/architecture.md` 和 `crates/infinity-agent-core/src/system/thread.rs`，整个 Infinity 运行时的设计都围绕同一条约束展开：**单个执行切片绝不阻塞在任何外部操作上**。

这条约束逼出了四个连环设计决策：

1. **万物皆消息（Everything is a message）**，`crates/infinity-agent-core/src/message.rs` 里只定义了一种入口信封 `InputMessage`。用户输入、异步工具结果、外部订阅事件、子线程回报、OAuth 跳转请求、人工选择弹窗、上下文压缩指令以及定时器唤醒，全都包装成 `InputMessage` 投进输入队列，通过 `group_id: ThreadId` 路由到对应线程。进程里不存在一个记录着正在等工具 X 或睡到早上九点的内存状态机。
2. **先落盘、再派发、立刻让出**，当模型决定调用异步工具时，切片先执行 `HistoryManager::sync()` 把本轮对话与工具调用写入持久化存储，然后向工具服务器发出一次无状态的 HTTP POST（携带 `callback_url`），只要对方回一个 `2xx` 确认收到，当前切片立刻结束。本地常驻驱动退出当前任务，Lambda 函数直接结束进程。
3. **跨线程并发靠队列分组，不靠分布式锁**，同一个线程的两次切片不能并发读写同一份历史，否则消息顺序会乱；不同线程之间没有共享可变状态，应该彻底并行。Infinity 没有在运行时里手写分布式锁，而是把串行约束直接交给队列层：AWS 侧用 SQS FIFO 的 `MessageGroupId` 绑定 `group_id`，本地侧用 `route_loop` 为每个活跃 `ThreadId` 维护单条 `mpsc` 通道。
4. **重投递安全靠持久化去重与尾扫**，队列只能保证至少一次投递（at-least-once），因此每个切片必须幂等。用户输入与订阅事件在 `StateStore` 中记录已处理 ID；工具结果则直接逆向扫描历史尾部匹配待应答调用，重复送达自动丢弃。

| 维度 | 传统阻塞式 Agent（如直连 MCP） | Infinity 无阻塞切片运行时 |
| --- | --- | --- |
| 工具等待期资源占用 | 进程、任务栈、连接与内存历史全程常驻 | 仅持久化存储中的历史行，无任务、无栈、无连接 |
| 进程亲和性 | 强绑定原进程与 stdio/SSE 通道 | 无亲和性，任意进程按 `ThreadId` 重载历史继续 |
| 适用等待时长 | 秒级到分钟级，长等待依赖轮询或保活 | 百毫秒到数天等价，成本只与实际推理切片成正比 |
| 单线程并发控制 | 进程内锁或粘性路由 | 队列层按 `group_id` 保证每线程 FIFO，无分布式锁 |
| Serverless 适配 | 只能硬扛空闲计费与超时上限 | 原生契合 Lambda 缩容到零，平台缩容即休眠 |

## 七万五千个 Agent 放进树莓派是怎么测出来的

README 开篇提到 Infinity 轻到能在一台树莓派内存里装下 75,000 个 Agent。这句宣传语背后对应的真实代码是 `crates/infinity-agent-core/examples/agent_scale.rs`，以及文档里嵌的实测数据 `docs/src/components/MemoryChart.tsx`。

拆开 `agent_scale.rs` 看测量口径很清楚：基准程序在一个单线程 `tokio::task::LocalSet` 上启动 `LocalAgentSystem`，使用内存版 `InMemoryConversationStore` 和 `InMemoryStateStore`，配合一个进程内脚本化模型 `ScriptedModel` 和异步回环工具 `RunCommand`。每个 Agent 真实走完 20 轮完整切片链路，每轮包含两次模型流式补全（每次 12 块、共约 1.2 KB 文本）加一次异步工具经队列回传。每跑完一波（`WAVE=4000`），程序等待所有线程驱动退出进入空闲态，排空生命周期通道，调用 Linux 的 `libc::malloc_trim(0)` 把分配器缓存还给内核，再读取 `/proc/self/status` 里的 `VmRSS`。

上游在 AMD EPYC 9R14 上跑 `AGENTS=80000 TURNS=20 WAVE=4000` 的记录如下：

- 0 个 Agent 基准 RSS：5,304,320 字节（约 5.3 MB）。
- 40,000 个空闲 Agent：4,348,772,352 字节（约 4.35 GB）。
- 80,000 个空闲 Agent：8,406,446,080 字节（约 8.41 GB）。
- 线性斜率折算：约 **102 KiB / Agent**。

按这个斜率外推，8 GB 内存的树莓派确实能装下约 75,000 个各带 20 轮对话历史的空闲 Agent。不过必须把口径看清：这 102 KiB 几乎全是 20 轮历史字符串本身在 `InMemoryConversationStore` 里的存储体积，之所以没有额外膨胀，是因为本地路由器 `route_loop` 直接把每个线程驱动放进 `FuturesUnordered`，线程一旦空闲就立刻退出并释放整个 `drive_thread` future 与 `HistoryManager` 缓存。如果换成真正驻留的大内存工具进程，或者成千上万个线程同时处于模型流式推理中，峰值内存会取决于同时活跃的切片数，而不是总注册 Agent 数。

## 同步工具的例外：为什么要允许切片内回环

并非所有工具都适合发出去然后结束切片。以内置的 `spawn_thread` 为例，它在 `ConversationStore` 里写几条元数据、往队列丢一条子线程启动消息，几微秒就能完成。如果让它也走异步让出，不仅白白多出一次存储重载和切片调度，还会引出一个隐蔽竞态：父线程刚派发 `spawn_thread` 就退出了切片，紧接着一条无关的并发事件抢在 `spawn_thread` 的结果之前抵达父线程，父线程就会以为 `spawn_thread` 被中断了，可实际上子线程早就已经启动并在后台跑起来了。

因此 `Tool` trait 在 `crates/infinity-agent-core/src/tools/mod.rs` 里特意留了 `supports_sync()` 和 `execute_synchronous()` 两个钩子。当 `run_completion` 在流里碰到同步工具调用，或者碰到模型幻觉出来的未知工具名时，运行时不会结束切片，而是当场内联执行（或注入错误结果），把结果塞回当前内存历史，在同一个切片里直接发起下一轮模型推理。只有真正跨进程或跨网络的异步工具才会触发落盘屏障与切片让出。

这种切分把状态变更极快、怕竞态的元操作留在切片内部当场完成，把一切耗时不可控的外部调用隔离在队列边界之外。
