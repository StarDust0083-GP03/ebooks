# 事件驱动契约：Dispatcher、before 与重启重建

把事件写进 SQLite 只是前半场。如果订阅者各自开后台线程轮询数据库、或者在处理回调里同步等一个耗时 30 秒的模型请求，整个分支的顺序语义就会瞬间瓦解。Dim 用四个机制把消费端锁死在可预测的轨道上：单分支 `Dispatcher`、提交锁内的 `before` 同步变形链、`consume` 重建交接协议，以及不经时间线的轻量广播通道 `SessionBroadcast`。

## Dispatcher：一条分支唯一的顺序读者

在 `crates/dim-agent-runtime/src/core/dispatcher.rs` 里，`Dispatcher` 挂载在每条分支的私有总线通道 `dim/timeline/{instance}/committed` 上。每当 `Timeline` 推进发布水位，热窗口里的已发布事件按 `sequence` 从小到大送进 `Dispatcher`，再逐条匹配订阅者注册的键模式：精确键（如 `approval/request`）、域通配（如 `approval/*`）或全局通配（`*`）。

事件处理器（handler）有两条不能碰的红线：

第一，**handler 绝对不能等慢操作**。网络请求、工具执行、磁盘冷读、子分支构建都不允许卡在 handler 里。`docs/architecture/rust-agent/event-driven-flows.md` 规定得很清楚：能力在 handler 返回之前，把异步工作登记到自己持有的任务表里就立刻返回。等异步任务将来跑完，再通过 `Timeline::append` 把结果事实写到时间线尾部，交给后面的分发轮次消费。

第二，**错误与 panic 必须归属到具体订阅者**。`SubscriptionListener::on_event` 用 `catch_unwind` 包住同步启动和异步 future 轮询。一旦某个订阅者返回 `Err` 或发生 panic，Dispatcher 会把故障归因到该订阅者名称，通过 `Dispatcher::fail` 终止该分支的后续派发并上报，而不是悄无声息地跳过一条事实继续往下跑。

## 九条时间线扩展规则的核心约束

`docs/architecture/rust-agent/principles.md` 第 4 节写下了通过时间线扩展行为的九条规则，其中四条直接决定了业务代码的写法：

**改别人的状态，只能写指令事件。** 调用方永远不直接调同步方法去修改另一个模块的内存状态，也不替状态所有者写结果事件。审批是最典型的例子：外部客户端发来决定后，经入口写入 `approval/respond`；`Approval` 状态机按序号读到指令，只有第一条对未决请求有效的指令会被采纳，后面抢跑的指令直接忽略，不需要为“什么都没发生”再多写一条无用事件。输入撤回同理：入口写 `input/withdraw`，`InputQueue` 只有在还握着这条未采用输入时才写 `input/deleted`。

**交接一旦完成，原模块就不再插手。** 当 `InputQueue` 写出 `input/applied`，这条输入的所有权就正式移交给了 `Loop`。交接之前可以用 `input/withdraw` 撤回；交接之后想停下来，只能走新所有者的指令 `stop/interrupt`。

**原子批次一起落盘，但消费端按条可见。** 需要同时生效的事实（例如 `Prompt` 变更、`input/applied` 与 `context/configured`）由产生方打包进一次 `append_batch_registered` 提交，起决定作用的事件放在批次最后。订阅者看不到批次边界，只按序号一条条收，需要校验批次终点的地方（如历史分页、分叉切点）统一通过 `Timeline::batch_end` 查询存储记录的批次元数据。

**写入前复核由状态机自负其责，且只看已消费状态。** 状态机先做决定、稍后才提交写入时，不能去偷看时间线尾部尚未被自己消费的事件，只能拿自身已消费的状态复核。即便生产方在消费中断前写出了迟到请求，消费方按序处理时也会依据自己已经消费到的 `stop/interrupt` 把属于该 turn 的请求取消掉。

## `before` 钩子：受限的可信变形器

有时宿主需要在事件真正落盘前改写草稿，比如注入元数据、重写测试用的候选载荷，或者统一规范化字段。Dim 没有让外部去篡改已提交历史，而是在 `Timeline` 的提交锁内部提供了一条同步瀑布链 `BeforeCommit`（`crates/dim-agent-runtime/src/core/timeline.rs`）。

`before` 的边界被卡得非常窄：

- 它只在写入前同步变换当前 `EventDraft`，不能改写已经提交的历史，也不能阻止写入或等待任何异步 I/O。
- 因为运行在分支提交锁内部，任何跨进程 RPC 都可能把整条分支锁死，所以只有进程内的 Rust 插件可以注册 `before`，TypeScript 和 Python 插件无法注册（ADR 001）。
- 每一个钩子的输入和变形结果都会被记录成 `TraceStep`，挂在 `TimelineTrace` 里随事件一起持久化，通过 `dimcode export` 可以完整导出审计轨迹。
- 一旦链上任意一个 `before` 钩子报错或 panic，整条链直接回退，提交**最初的原始草稿**，且不会让分支提交锁中毒。
- 产生方登记状态和后续执行，一律以 `before` 变形后最终提交的事件为准，绝不能继续拿着提交前的候选变量干活，也不做二次校验去反对可信变形。

## 副作用不重放与 `consume` 重建协议

在 Dim 的世界观里，插件热重载和进程崩溃重启对新一代状态机来说是同一件事，走的也是同一套交接协议：`Dispatcher::consume`（`docs/design/plugin-contract.md` 第 5.2 节）。

普通写法很容易掉进漏事件或重复事件的坑：如果先读历史再订阅，读历史期间新到达的事件会漏掉；如果先订阅再读历史，重叠区间的事件会被处理两遍。`Dispatcher::consume` 用一个极简的暂存闸门堵死了这个窗口：

1. 先向 Dispatcher 注册监听，进入 `holding = true` 状态，期间到达的实时匹配事件全部暂存进 `Held.events` 列表。
2. 执行该能力提供的 `rebuild` 回调。回调在分支 `Timeline` 上固定一个查询截点 `cutoff`（内部重建可包含已提交未发布的尾部），只查询自己折叠状态所需的事件字段，按历史恢复内存视图，并把上一代留在半路的在途操作结算成终态（绝不重新发起外部工具或模型请求），最后把 `cutoff` 返回给 Dispatcher。
3. Dispatcher 把 `holding` 切回 `false`，只把 `Held.events` 里序号**严格大于 `cutoff`** 的事件按序喂给实时 handler，之后无缝衔接后续实时事件。

每条事件在重建和实时两个阶段合起来恰好处理一次，既不丢、也不重。

这套要求同时划清了内存状态的合法性边界（`time-bus.md` 第 3 条）：任何跨工作、跨代使用的业务状态，都必须能由“时间线历史 + 重读外部配置状态”恢复到等价。唯独已结算的在途 I/O 局部控制状态是例外，比如单次模型调用失败后的重试计数、退避等待预算、凭据刷新标记，它们只属于当前这一次调用，重载时原调用直接按失败或取消结算，不会把重试计数带进下一项工作。

## 时间线事实 vs 总线通知：什么该落盘

不是所有消息都值得占用时间线序号。`principles.md` 第 4 节第 9 条定了一个很硬的筛选条件：只有影响后续业务决定的事实才进 Timeline。

给界面展示的实时进度、进程级状态变化（比如会话标题更新、Provider 目录变更、设置修订）不进 Timeline，而是走 `rutis` 根上的扁平事件总线。每条会话有一条由 `SessionBroadcast` 插件维护的命名通道 `session/{sessionId}`，负责把本会话各分支已经分发的事件零拷贝（`Arc<AgentEvent>`）广播给订阅方，同时转发进程级所有者发出的 `SessionNotice`、宿主观察屏障 `Fence { recipient, token }` 以及卸载时的 `Closed`。

| 传输通道 | 持久化与重放 | 顺序保证 | 典型用途 |
| --- | --- | --- | --- |
| 分支 Timeline + Dispatcher | SQLite WAL 持久化，按 `consume` 重建 | 分支内严格单调 `sequence` | 输入、模型调用、工具结果、审批、分支创建、压缩 |
| `session/{sessionId}` 广播 | 不持久化、不补发、无订阅即丢弃 | 同分支与分发序一致，跨分支按发出序 | TUI / Web / ACP 客户端实时流、UI 通知、交付屏障 |
| `settings/{source}` 等总线事件 | 不持久化、不重放 | 单事件名内按发出序启动监听 | 配置变更提醒、`skills/catalog`、`agent/session-deleted` |

因为总线事件不重放，任何消费总线事件的部件（比如 `SessionTitle` 监听 `input/applied`）都必须具备从 Timeline 或所有者状态重新读取的能力，这样漏掉一次总线事件顶多延迟动作，不会破坏状态一致性。

把单条分支的时间线和分发契约夯实之后，下一步就能顺理成章地看一条会话如何长出一整棵分支树。
