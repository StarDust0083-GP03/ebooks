# 时间总线与只认序号的事实流

如果把一条分支上发生的所有事情摊平来看，并发 bug 的根源几乎都来自同一个错觉：代码以为先发起的操作会先完成，或者以为写进了内存就已经生效。在 `crates/dim-agent-runtime/src/core/timeline.rs` 和 `docs/architecture/rust-agent/timeline-storage.md` 中，Dim 把这条链路切成了两个清晰的水位和一个单调递增的序号，彻底剥离了执行耗时与业务先后。

![时间总线写入、落盘与按序派发链路](../../wiki/assets/diagrams/time-bus-dispatch.svg)

## 信封结构与 13 个核心保留域

写上时间总线的每一条事实都装在 `crates/dim-agent-types/src/events.rs` 定义的 `AgentEvent` 信封里。信封只包含六个顶层字段：`id`（事件唯一标识）、`sequence`（分支内单调无符号整数序号）、`create_at`（Unix 毫秒时间戳）、可选的 `tags`、可选的 `parent_call_id`（用于标记 Code Mode 等嵌套工具调用产生的子事件），以及展平后的 `kind: EventKind`。

这里有一条很巧妙的上下文防火墙：`AgentEvent::context_flags()` 会先检查 `parent_call_id.is_some()`，只要事件带有父调用标识，直接返回 `(false, false)`。像 Code Mode 脚本内部触发的所有子工具调用事件，虽然照常记录在时间线上供审计和调试，但永远不会被当作主模型的上下文输入。

为了防止外部插件冒充核心状态机的事实，`dim-agent-types` 明确划定了 13 个核心保留域 `RESERVED_DOMAINS`：

`input`、`stop`、`approval`、`permission`、`exec`、`turn`、`prompt`、`context`、`model`、`step`、`branch`、`collaboration`、`session`。

核心 `EventKind` 枚举覆盖了这 13 个域下的全部强类型事件（从 `input/message`、`input/consumed` 到 `branch/default`、`session/forked`）。外部扩展只能通过 `EventKind::Extension(ExtensionEvent)` 写入自己命名的域，且域和事件名都不能为空、不能包含 `/` 或 `*`。一旦扩展试图往 13 个保留域里塞不透明载荷，Timeline 在入口直接拒绝。

## 已提交与已发布：为什么要分双水位

沿着图里的写入链路往下走，能力调用 `Timeline::append`、`append_registered` 或 `append_batch_registered` 时，函数是同步返回的，并不等待磁盘 fsync。在提交锁内部，Timeline 依次做三件事：跑完进程内同步的 `before` 变形链、分配连续的 `sequence`、执行产生方的登记回调（登记失败则整批中止，什么也不落盘）。通过之后，事件进入内存热窗口并塞进 `TimelineStore` 的后台写入队列，此时分支的 `committed()` 水位推进。

但 `committed()` 推进不代表事件已经对外生效。后台写入任务（SQLite 实现位于 `SessionStorePlugin`）按轮次把所有 Session 的待写批次打包进同一个 SQLite 事务，开启 WAL 模式与 `synchronous=FULL`（macOS 上额外开启 `fullfsync`），保证事务返回即真正落盘。只有事务提交成功后，写入任务才在释放写锁之后推进各分支的发布水位 `published()`，并唤醒该分支的 `Dispatcher`。

这种双水位设计把进程崩溃和断电的后果缩减到了三种清晰状态：

| 中断时刻 | 磁盘上的事实 | 恢复后的行为 |
| --- | --- | --- |
| 事务提交前崩溃 | 该轮事件不存在 | 这些事件从未发布，也没有被任何订阅者消费，系统回退到上一轮水位 |
| 事务提交后、发布前崩溃 | 该轮事件已完整落盘 | 此前尚未分发，重启后各能力按统一恢复协议读取历史并接手 |
| 发布后崩溃 | 事件存在且已进入分发 | 状态机从历史恢复等价状态，不重复执行外部副作用 |

两类读者的可见边界也跟着双水位分开：分支内部的能力在重建或复核时，可以通过 `Timeline` 读到 `committed()`（包含热窗口里已受理但尚未落盘的尾部）；而 `Dispatcher`、分支外部的 `Branch` 与 `Session` 门面、以及所有 RPC 客户端，只能看到 `published()` 之前的事件。对外部世界来说，一条事件只有落盘发布了才算数。

## 存储层的物理优化：去重、合行与 20ms 节流

把模型流式输出的每一个 token 增量和每一轮完整的消息数组都原样写进 SQLite，很快就会把磁盘和 I/O 打爆。Dim 在不破坏逻辑事件序号的前提下，在存储边界做了三项物理压缩，而且全部对上层透明：

**内容寻址的消息链表。** 每轮 `context/prepare` 和 `model/call` 携带的 `request.messages` 往往与上一轮共享 95% 以上的前缀。SQLite 存储把每条消息存成 Session 内容表里按内容哈希寻址的链表节点，指向前一个节点的地址；`tools` 和 `system` 也按内容哈希去重。事件正文里只存最后一个节点的地址、长度以及 `tools` 与 `system` 的引用编号。

**流式输出合行与事务内热缓存。** 同一分支、同一请求下连续产生的 `step/text` 或 `step/thinking` 增量，在物理表里合并为同一条输出行（受 1 秒或 4 KB 阈值切分），行内按序保存各逻辑事件的 `id`、`sequence`、`create_at`、`tags` 和 `delta`。写入器在同一个事务内按分支缓存最后一条输出行的已解码结构，后续增量直接内存追加，免去反复查表和解析 JSON，非输出事件到达或事务结束时立刻失效。

**分流式写入轮节流。** 后台写入器在上一轮提交完成后，只要队列里还有普通批次（比如工具调用、审批、输入采用），立刻开始下一轮提交，不人为加延迟。只有当队列里全部是流式输出行增量时，才等到距上一轮开始满 20 毫秒再合并提交，把纯流式输出压在每秒至多 50 次 WAL fsync。

`docs/architecture/rust-agent/timeline-storage.md` 坦诚记录了这套策略的实测权衡：在 Apple M4 上用 `timeline_acceptance rounds 64 200 64` 实测（64 条流式分支每秒各 200 增量，叠加 64 条依赖写入链），普通批次不等待让依赖链每步发布延迟中位数从 22 ms 降到了 9 ms、吞吐翻了 1.8 倍，但代价是每秒写入轮次升到 97 轮、WAL 写入量从每秒 40 MB 升到 80 MB（因为普通批次顺带把所有在等的流式增量一起带走了）。上游团队明确记下了这个代价，并决定暂不打破“一轮取走全部待写批次”的简单模型。

读写并发同样做了收敛：所有只读查询走按需创建的只读连接池，上限 4 条连接，空闲时收缩回 1 条，读操作在语句开始时的最新提交快照上执行，不抢写连接互斥锁；大 Session 的分叉与删除则通过复制连接按每 256 个逻辑序号、每 1024 个内容哈希分块提交，每块之间把写锁让给正常的写入轮。

当事件稳稳落盘并推进发布水位之后，真正决定业务怎么流转的，是站在总线另一端的 `Dispatcher` 和一整套事件驱动契约。
