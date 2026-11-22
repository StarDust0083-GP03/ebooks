# 单线程本地调度器、延迟队列与抢占打断

在云端 AWS Lambda 上，每个活跃线程的切片由 SQS FIFO 自动串行调度，跑完进程就销毁，不存在长期驻留的调度器。但把同一套 `infinity-agent-core` 搬回开发者本机守护进程或者嵌入式应用时，立刻会碰到四个本地运行特有的竞态难题：

1. 几万个历史线程如果每个都在后台养一个常驻 `tokio::spawn` 任务，光任务结构体和通道缓存就会吃掉可观内存。
2. 父线程刚刚派发了一个耗时十秒的 `execute_command`，跑了一半时另一个子线程发回一条 `report_to_parent`。如果父线程立刻处理这条汇报，就会因为历史末尾还有未应答的工具调用，被迫给 `execute_command` 注入一条中断结果，把本来好端端在跑的构建平白无故杀掉。
3. 用户正在看模型流式吐字，突然发现方向错了，直接敲下一句新指令回车。运行时必须立刻停掉当前流式生成、保存已经吐出来的半截内容，还得确保不能把模型还没响应过的未验证输入提前写坏。
4. 某个线程刚准备空闲退出，就在释放通道前的一微秒，一条新消息或一个新前端订阅请求撞了进来，怎么保证不丢消息、也不把同一个线程同时拉起两个驱动？

`crates/infinity-agent-core/src/system/local/` 里的 `router.rs`、`driver.rs` 加上 `system/defer.rs` 与 `system/thread.rs`，用单线程协作式调度和极克制的状态机把这四件事全部收敛清楚了。

![本地单线程路由器与线程驱动状态机](../../wiki/assets/diagrams/driver-deferral-state.svg)

## 为什么选择单线程 LocalSet 加 FuturesUnordered

翻开 `crates/infinity-agent-core/src/system/local/router.rs`，整个本地 Actor 系统刻意运行在单线程的 `tokio::task::LocalSet` 上，而不是多线程工作窃取池。驱动内部大量使用了轻量的 `Rc`、`RefCell` 和 `Cell`（`HistoryManager` 和 `Thread` 都不需要 `Arc<Mutex<...>>` 锁竞争）。更重要的是，单线程 `LocalSet` 带来了确定性的协作切换点：只要一段函数里没有 `.await`，路由器 `route_loop` 和各线程驱动 `drive_thread` 之间就不会互相抢占，这正是后面无竞态原子退出检查的前提。

另一个省内存的关键手笔在 `route_loop`（第 256 至 261 行）：路由器**没有**用 `tokio::spawn` 给每个线程建独立任务，而是用一个 `FuturesUnordered` 直接持有所有活跃线程的 `drive_thread` future，再配一张 `workers: HashMap<ThreadId, WorkerChannels>` 路由表。

当某个线程空闲返回时，它的 future 从 `FuturesUnordered` 中产出 `ThreadId`，整个驱动状态机连同 `HistoryManager` 的内存立刻彻底释放，路由器紧接着把 `workers` 里对应通道已关闭的条目删掉（代码特意检查 `w.input_tx.is_closed()`，防止误删同一瞬间刚重新拉起的新驱动条目）。为了让单个线程的意外 bug 不至于拖垮整台守护进程，每个驱动 future 外层都包了 `rap_protocol::log_panic("thread_driver", driver)`，用 `AssertUnwindSafe(...).catch_unwind()` 捕获 panic 并记日志，其余线程照常运转。

## 准入策略：绝不为幽灵或已停线程唤醒驱动

外部 RAP 服务随时可能迟到几小时推回来一条过期回调，或者针对一个已经被用户手工停止的会话发来订阅事件。如果路由器照单全收、见消息就拉起驱动，就会平白创建出幽灵线程记录，或者把已经停止的代理偷偷叫醒。

`route_loop`（第 310 至 356 行）在拉起新驱动之前加了一道准入门禁：

- **普通用户文本直接放行**（`is_user_text_input` 为真），因为发新文本正是用户创建新线程和唤醒已停止线程的合法入口。
- **订阅客户端请求直接放行**，允许 UI 在线程第一条消息到来之前提前挂载监听。
- **其余一切事件型输入**（工具结果、订阅事件、子线程回报、OAuth、用户选择）必须过两关：先查 `conversation_store.thread_exists(&thread_id)`，不存在则直接丢弃并记日志；再查 `state_store.is_thread_stopped(&thread_id)`，已停止则直接丢弃。两关遇到存储短暂报错时都采用 **fail-open** 策略（记录警告并放行），宁可让驱动醒来走一遍 `prepare_input` 吸收脏消息，也不因存储抖动误丢真实的工具回调。

## DeferQueue 与 is_passive：什么时候该等，什么时候能打断

再看前面提到的第二个难题：子线程回报撞上正在执行的工具调用。

`crates/infinity-agent-core/src/system/defer.rs` 定义了 `DeferQueue` trait，只有 `push` 和 `drain` 两个方法，提供两种内置实现：本地常驻模式用 `InMemoryDeferQueue` 暂存在内存向量里；无服务器 Lambda 模式用 `NoDeferral`（`push` 永远返回 `Ok(false)`），因为 Lambda 没有驻留进程可存内存，除非调用方自己实现基于 SQS 延迟重投的传输层队列，否则直接接受打断语义。

真正决定一条事件该不该被延迟的逻辑，在 `Thread::pending_active_tool_call` 和 `Thread::filter_deferrable`（`crates/infinity-agent-core/src/system/thread.rs` 第 216 至 291 行）里，依赖 `Tool::is_passive()` 这个精细的布尔标记：

| 尾部挂起的工具状态 | 到来的新消息类型 | `filter_deferrable` 处理动作 |
| --- | --- | --- |
| 无未应答工具，或挂起的是被动工具（如 `sleep` / `sleep_until` / `sleep_until_event_or_input`） | 任何消息 | 立即放行，并排空此前积压的延迟队列一起拼进本批；若原工具在休眠，则按正常中断逻辑唤醒 |
| 挂起的是活跃工具（`is_passive() == false`，如 `execute_command`） | 恰好是该工具的 `ToolResult`，或普通用户文本 | 立即放行，并将延迟队列排空追加在后面（保持本批先结算挂起调用、再处理延迟事件的顺序） |
| 挂起的是活跃工具（`is_passive() == false`） | 可延迟合成事件（`SubscriptionEvent`、`ThreadReport`、`ParentMessage`） | 压入 `DeferQueue` 暂存，不打断正在运行的工具调用 |

这条规则把语义分得极细：`sleep` 家族工具虽然也在等结果，但它们代表原地待命，标记为 `is_passive() == true`，允许外部订阅事件或子线程回报随时叫醒；真正在跑构建、改文件的工具属于活跃任务，无关合成事件乖乖在 `InMemoryDeferQueue` 里排队，等构建结果一到（或者用户主动发话打断）再按顺序接上。

## 流式抢占打断与优雅关闭

本地驱动 `drive_thread`（`crates/infinity-agent-core/src/system/local/driver.rs`）用 `InFlightStep` 把正在执行的 `step_no_defer` future 和它的 `oneshot::Sender<()>` 取消句柄绑在同一个结构体里。这样做的好处是所有权天然同步：取消步骤会消费掉整个 `InFlightStep`，哪怕驱动因为外层关闭被直接 `drop`，`cancel_tx` 也会随之释放，内层的 `run_completion` 立刻收到取消信号。

当一个步骤正在流式推理中，`drive_thread` 在 `tokio::select! { biased; ... }` 里同时监听步骤完成、新输入通道 `rx` 和新订阅通道 `subscribe_rx`：

- 如果 `rx` 收到的批次里**包含用户文本**，驱动立即调用 `in_flight.take().cancel().await` 中断当前补全流。如果此时模型已经吐出部分文本，取消路径会把已生成的半截内容正常 `flush_turn` 并 `sync()` 落盘，不会丢掉用户刚才在屏幕上看到的话；如果模型还没吐出第一个字节就取消了，本轮输入留在内存的 `unvalidated_items` 里不落盘，下一轮连同新的打断消息一起发给模型。接着驱动把批次拆成用户文本和非用户消息两部分，给第一条用户文本加上 `<interrupt>` 前缀（前端回显 `input_event` 时会剥掉这个前缀，模型则能明确感知自己被用户中途打断），非用户消息放进 `pending_batch` 留待后续处理。
- 如果 `rx` 收到的批次里**全是异步事件**（没有用户文本），驱动不打断当前推理，只是顺手塞进 `pending_batch`，等当前步骤自然跑完再接手。
- 如果 `rx` 关闭（代表整个 `RunningSystem::shutdown()` 被触发），驱动同样显式 `cancel().await` 等待当前步骤把已验证历史刷盘完毕再退出。路由器侧也会 `drop(workers)` 后把 `drivers` 排空，绝不在持久化中途把进程掀翻。

## 同步原子退出与订阅安装 ACK

最后看本地驱动最容易出并发 Bug 的两个边角：空闲退出与中途订阅。

当 `pending_batch`、`rx` 和 `defer` 队列都为空时，`drive_thread` 调用一个**完全同步、不含任何 `.await`** 的函数 `check_before_idle`（`driver.rs` 第 331 行）：先排空 `subscribe_rx` 处理所有刚到的订阅请求，再 `try_recv()` 确认 `rx` 确实空了；接着检查 `thread.awaiting_tool_result()`（即历史最后一条是不是除 `close_thread` 之外的未应答工具调用）。如果是，返回 `IdleDecision::AwaitInput`，驱动继续挂在 `select!` 上等工具回包；如果不是（即便线程还挂着活跃订阅，也不必常驻驱动，事件来了路由器会再拉起它），立刻返回 `IdleDecision::Exit` 退出驱动。

因为 `check_before_idle` 里没有让出点，且与 `route_loop` 共享同一个单线程 `LocalSet`，所以新消息要么在检查前就已经躺进了 `rx` 被它当场捞走，要么一定发生在驱动退出、接收端关闭之后，被路由器检测到 `is_closed()` 并拉起新驱动，绝不存在消息掉进两头裂缝的可能。驱动退出时，`DriverGuard::drop` 在同一临界区里移除 `active_threads` 集合成员并向 `lifecycle_tx` 发出 `ThreadLifecycleState::Idle`，让外部宿主（例如守护进程）据此精准回收该会话关联的附加资源。

前端订阅也用同样的严谨手法消除了竞态：`SubscribeHandle::subscribe` 带一个 `oneshot::Sender<()>`，直到目标线程驱动把 `thread.replay_snapshot()` 交给了 `observer.on_subscribe` 并真正把监听者注册完成，才会发回 ACK。调用方只要 `await` 完 `subscribe()` 再发 `send_user_text()`，就能百分之百保证不会漏收第一批流式事件。

把本地调度器拆透后，再往下看一个经常被低估的边界：核心运行时是怎么在不认识任何一家大模型 SDK、也不解析一句报错文本的前提下，处理五花八门的限流、超窗、断流和跨进程模型供应的？下一章拆解模型供应协议。
