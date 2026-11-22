# 持久信箱 `rlm.inbox` 与跨进程消息投递

把后台命令、子 Agent 和定时心跳全接进同一个会话后，很快会出现一种经典的“通知洪水”：主 Agent 刚在后台一口气派出去 6 个测试脚本和 4 个子 Agent，然后安心结束回合去等结果。三分钟后它们扎堆跑完，如果宿主把 10 条完整的 `[bash-done]` 输出和子 Agent 长篇回复全部当成 Steering 用户消息直接灌进主会话上下文，不仅一口气吃掉几万 Token，还会把原本有序的对话历史打成一锅粥，甚至把用户真正想插进来的纠偏指令淹没在机器噪音里。

Prime Agent 在 v0.10.5 这一轮里做了一项影响全局的重构（对应 `.changes/agent-inbox.md`、`.changes/inbox-tui-cards.md`、`.changes/inbox-agentmsg-semantics.md` 及本次固定提交 `#4123`）：引入守护进程托管的**持久会话信箱（Agent Inbox）**，把机器通知的“存储”和“唤醒”彻底劈开。

![持久信箱 rlm.inbox 与核家族消息投递链路](../../wiki/assets/diagrams/inbox-subagent-flow.svg)

这张图概括了信箱的完整生命周期：六类异步事件不再把正文直接塞进上下文，而是先落盘进会话专属的 `inbox.jsonl`，再往队列里放一条合并后的极短计数唤醒行；等模型主动调用 `rlm.inbox.read()` 按需消费后，唤醒行自动撤回，同时每条被读出的消息借由专有 MIME 帧直接渲染在 `python_repl` 卡片上。

## 六类通知收口与断写可恢复的 `inbox.jsonl`

信箱的核心存储和过滤逻辑位于 `crates/pa-core/src/session_engine/inbox.rs`（2,127 行），守护进程侧的槽位管理、身份校验和周期提醒位于 `crates/pa-daemon/src/agent_inbox.rs`。

所有走信箱的机器通知被严格归纳为 6 个固定类别（`inbox_class`），每类都有确定的 `sender` 语义：

| `inbox_class` | 来源 | `sender` 字段含义 | 特殊规则 |
| --- | --- | --- | --- |
| `bash-done` | 后台 `bash()` 命令结束 | 命令文本 | 附加 `exitClass`（`success`/`failure`）；若内核已通过句柄回收结果（`bash.consumed`），自动撤回对应未读信件 |
| `factory-progress` | 状态机工厂里程碑 | `runId` | 记录运行态关键进展与异常升级 |
| `agent-message` | 核家族 `agent_message.send` | 发送方 Agent 名称 | 走信箱后不再受旧版未读数量硬上限（too many pending messages）拦截 |
| `child-exited` | 子 Agent 正常完成 | 子会话名称 | 由守护进程内部保留类型投影，防客户端伪造 |
| `child-failed` | 子 Agent 异常失败 | 子会话名称 | 携带子会话 ID 与失败详情 |
| `heartbeat` | 定时心跳触发 | `job_id` | 同一个心跳任务在信箱里最多只留 1 封最新信件，新触发自动替换旧未读，暂停或删除任务时立刻撤回残留 |

每当新消息进入，都会追加写入 `<session-artifacts>/<id>/inbox.jsonl`。日志重放器专门针对崩溃场景做了加固：如果进程恰好在写盘中途断电留下半截残破 JSON 行，重启打开信箱时会跳过残缺记录、完整恢复前面的所有推入与消费记录，并保持消息 ID 单调递增，绝不复用旧 ID。

更难得的是它对**写盘失败**的态度。如果磁盘满或权限异常导致 `append_journal` 失败，`SessionInbox` 不会默默吞错，而是把错误信息记进 `persistence_error` 槽位，并在后续发给模型的唤醒行里显式附上一句警告：`(inbox persistence failed; messages are memory-only)`（常量 `INBOX_PERSISTENCE_ERROR_CLAUSE`），直到一次完整的日志重写成功把待读集合安全落盘，这个降级标记才会清除。

在 Worker 会话替换或恢复期间，还有一个微妙的并发竞态：旧会话实例正在退休，新会话实例准备打开同一个 `inbox.jsonl` 重放，如果此时有异步投递误写进旧实例，两边就会互相踩踏。`crates/pa-daemon/src/agent_inbox.rs` 第 118 行的 `push_to_live_inbox()` 用槽位互斥锁 + `Arc::ptr_eq` 指针身份双重检查化解了这个问题：要么追加抢在退休清理之前落盘，新会话重放自然能看到它；要么退休先完成，本次追加返回 `None`，调用方重试投递给新实例。

## 合并唤醒行、防伪造边界与严格过滤读取

消息进了信箱，怎么让空闲或忙碌中的模型知道自己有新邮件？

守护进程只往队列放两类轻量级通知行：

**到达合并行（`inbox:arrival`）**，同一波并发涌入的 3 条后台完成通知，不会被拆成 3 条消息，而是合并成一行极短的计数提示，例如 `3 new bash-done messages` 或 `3 new messages`，附带操作指引 `Read them with msgs = await rlm.inbox.read().`。

**周期提醒行（`inbox:reminder`）**，如果模型读完唤醒后忘了处理或者只消费了一部分，后台提醒任务会按 `agentInbox.reminderIntervalMs` 定期重发一条纯计数提醒（设为 `0` 时关闭周期提醒，但仍每 60 秒轮询一次设置以便热开启）。如果全局配置了 `agentInbox.enabled: false`，整套信箱退回旧模式，新通知直接走直插行，但信箱里已有的未读消息依然可读，不会平白丢失。另外，无守护进程的 `-p` 单次打印模式不启用信箱。

这里有一条很细的安全校验（见 `crates/pa-daemon/README.md`）：`inbox_notice` 自定义行类型没有任何外部客户端接入路由，只能由 Worker 内部直插；如果外部客户端在恢复队列或发消息时，试图伪造一个自定义行冒充 `inbox_notice`、`rlm_child_terminal_notice` 或占用 `inbox:arrival`/`inbox:reminder` 合并键，校验器会直接报错拒收。否则一条伪造的用户消息可能在下一次清空信箱时被误删，或者反过来伪装成系统通知误导折叠渲染。

在读取侧，`rlm.inbox.read()` 支持四个可组合（逻辑 AND）的过滤参数：`sender`、`inbox_class`（线协议键 `class`）、`since_ms`（包含下界）和 `before_ms`（排除上界）。注意 Rust 侧 `InboxReadFilter`（`crates/pa-core/src/session_engine/inbox.rs` 第 93 行）结构体上加了 `#[serde(rename_all = "camelCase", default, deny_unknown_fields)]`。为什么要加 `deny_unknown_fields`？因为 `rlm.inbox.read()` 是**消费即清空**语义（只移除命中的消息，未命中的留在信箱）。如果模型把字段名拼错成 `sendr`，宽容反序列化会把它当成一个全空过滤器，直接把整个信箱误清空！严格拒绝未知字段（同时在处理器里剥离外层路由键 `type`）确保拼错参数立刻报错，一封消息都不会丢。

读完之后还有两件自动联动的收尾事：一是执行 Worker 注册的 `InboxReadHook`，立刻撤回已经清空的唤醒行或按剩余数量重绘提醒行；二是 Python 侧 `inbox_read()` 对返回的每一条消息调用 `emit({"application/vnd.prime-agent.inbox-message+json": entry})`（即固定提交 `#4123` 刚合入的新特性），让 TUI 的 `python_repl` 工具卡片直接把读出来的每封信箱消息按原始行样式渲染出来，操作者不用翻代码输出也能一眼看清 Agent 刚收到了什么。

## `agent_message`：核家族直连投递与状态语义

最后看看 Agent 之间是怎么互相发信的。模型在内核里调用：

```python
receipt = await agent_message.send(
    "Please inspect the latest result.",
    receiver_role="child",
    receiver_name=child.session_name,
)
```

也可以用 `agent_message.send("all", text)` 向整个核家族广播。消息发送方身份完全由守护进程根据当前会话自动派生，模型传任何 `from` 字段都无法伪造身份。

在底层传输上（`crates/pa-daemon/src/agent_messaging.rs` 和 `peer_client.rs`），`pa-daemon` 采用了两级路由：

1. **优先 Worker 点对点直连**：源 Worker 向 Supervisor 请求一张 `worker` 用途的一次性访问授权（`get_worker_peer_transport`），拿到目标 Worker 的本地 Socket 后直接发起 `worker_deliver_message` 投递，绕过 Supervisor 转发。
2. **失败回退 Supervisor 路由**：直连不可达时回退走 Supervisor 的 `send_message` 路由（且绝不盲目重试导致重复送达）。
3. **休眠目标按需唤醒**：如果收件方是一个已经休眠落盘的旧会话或已完成的子 Agent，Supervisor 会通过 `session_catalog.rs` 按 ID 前缀或精确名称解析（先查同工作目录，必要时从 `sessions-archive/` 懒恢复；若目录未命中，再查 RLM Spawn 账本找到父会话制品树下的子会话文件），拉起一个 Worker 接管该会话文件并投递消息，把目标重新唤醒进入普通回合。但如果目标会话正在关闭中，投递会被明确拒绝，绝不悄悄把消息丢进黑洞（`.changes/inbox-agentmsg-semantics.md`）。

收据里的 `deliveryStatus` 区分两种状态：`"delivered"` 表示消息已经进入空闲目标的上下文或已安全写入目标的持久信箱；`"queued"` 表示目标正在忙，消息已作为 Steering 排队等待适当时机投递，且 `send()` 立刻返回不阻塞发送方。另外，如果操作者在界面里暂停了某个会话的 Agent 消息接收，暂停动作绝不会删除信箱里已有消息，清空操作也只移除尚未通知过模型的未公告消息。

持久信箱和跨进程消息让多会话协作有条不紊。接下来，我们把镜头拉远到整个守护进程的全局治理：会话 JSONL 树怎么存、怎么在不爆内存的前提下全文搜索历史会话、升级时怎么做无损热更新，以及外部 IDE 如何通过 ACP 协议接入 Prime Agent。
