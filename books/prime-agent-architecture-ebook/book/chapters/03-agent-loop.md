# Turn 循环、Claim-First 抢占与三层消息队列

当一个编程 Agent 既允许用户在生成中途插话纠偏，又允许后台命令完成、定时心跳触发、子 Agent 回信、目标驱动自动续跑同时往同一个会话里投递消息时，最容易出两类隐蔽故障。

第一类是**竞态丢消息**：后台队列里刚躺进一条 `[bash-done]` 通知，准备唤醒空闲会话；恰在几毫秒前，用户敲了回车启动新一轮对话。如果队列先弹出了消息、再尝试抢占执行权，抢占失败时那条通知就已经从队列里消失了。

第二类是**死锁占位**：某次回合中途因为网络断开或任务被取消而异常退出，如果执行状态的锁或标志位没有通过 RAII 守卫释放，这个会话就会永远卡在忙碌态，之后用户的每一句话都会收到冰冷的报错：`Agent is already processing.`。

`crates/pa-agent` 虽然只有 14 个源文件、5,646 行代码，却是整个系统的节奏控制器。它用极小的接口面把上面两类并发陷阱都堵死了。

## Claim-First：先拿到运行凭证，再动队列

翻开 `crates/pa-agent/src/agent.rs` 第 648 行的 `claim_run()`，以及 `.changes/agent-queue-claim-first.md` 和 `.changes/fix-remove-parked-seat.md`，能看清这套并发契约的核心。

任何想推动 Agent 运转的路径，不管是用户发新提示词，还是队列泵消费积压消息，都必须先调用 `claim_run()` 换取一个具名的所有权凭证 `RunClaim`。具体步骤严格串行：

1. 获取异步互斥锁 `admission`，防止两个启动流程交错读取配置。
2. 一次性拍下当前的 `model_override` 快照，保证这次运行里使用的模型身份和请求参数完全一致，不会被并发的 `/model` 切换撕裂。
3. 锁住 `run` 槽位。如果槽位已被占用，立刻返回带错误码 `AgentContinueErrorCode::Busy` 的类型化错误，上层按错误码分支，不再靠匹配字符串判断。
4. 槽位为空时，创建新的 `AbortController` 和 `watch::channel(false)` 空闲通知通道，把 `ActiveRun` 装进槽位，释放 `admission` 锁并返回 `RunClaim`。

真正进入执行体的是 `run_with_lifecycle()`（第 691 行）。它一进门就在栈上构造了一个 `RunEndGuard`，无论后面的异步流是正常结束、返回错误，还是在 Tokio 任务里被外部直接 drop，守卫都会确保清理 `is_streaming` 标记、清空 `run` 槽位并向 `idle_tx` 广播 `true`。这就是 `.changes/fix-remove-parked-seat.md` 修复“会话崩溃后永久卡死”问题的根基。

有了 Claim-First，队列消费顺序也就顺了：队列里的消息先留在队列里，等拿到 `RunClaim` 之后，或者等正在运行的 `run_loop` 走到合法的轮询检查点时，才执行 `drain()`。抢不到运行权时，队列纹丝不动，消息绝不会凭空蒸发。

## 三层消息队列与 Turn 循环优先级

拿稳了运行权，控制流进入 `crates/pa-agent/src/agent_loop/run.rs` 的 `run_loop()`。这个函数是一个双层循环，把所有输入按紧急程度切成了三层，优先级从高到低依次是：**Steering（转向插话）**、**Follow-Up（后续排队）** 和 **Continuation（宿主续跑）**。

整个状态机的流转逻辑可以直接对照源码读下来：

```text
进入 run_loop()
  ├─ 首次轮询 Steering 队列（可按 skip_initial_steering_poll 跳过）
  └─ 外层循环（处理后续队列与续跑钩子）
       ├─ 内层循环：只要 has_more_tool_calls 或 pending_messages 非空
       │    1. 把 pending_messages 逐条写入上下文并发射 MessageStart/MessageEnd
       │    2. 调用 stream_assistant_response() 流式拉取模型回复
       │    3. 若 stop_reason 为 Error 或 Aborted，发 TurnEnd + AgentEnd 立即退出
       │    4. 若模型发出 tool_calls，调用 execute_tool_calls() 顺序执行整批工具
       │       -> 若批内有调用标记 terminate（例如 rlm.end_turn()），则 has_more_tool_calls = false
       │    5. 发射 TurnEnd，若 AbortSignal 已触发则立刻退出
       │    6. 评估 should_stop_after_turn 与 should_stop_before_turn 钩子
       │    7. 轮询 Steering 队列；若有新插话，装入 pending_messages 继续内层循环
       ├─ 内层循环结束，轮询 Follow-Up 队列 -> 若非空，装入 pending_messages 回到外层循环
       ├─ Follow-Up 为空，调用 get_continuation_messages 钩子 -> 若非空，回到外层循环
       └─ 全部为空 -> 发射 AgentEnd，正常退出
```

把输入拆成三层，对应三种截然不同的交互时机：

**Steering 队列**用于立刻生效的中途插话。用户在模型连续调工具的中途发来纠偏指令、ACP 客户端调用 `_session/steering`、或者紧急心跳按 `delivery_mode="steer"` 触发时，消息走 Steering 队列。它在每一次工具批结算完后就被检查，不用等模型彻底停下来。

**Follow-Up 队列**用于当前任务彻底收尾后再接手的待办。例如用户提前排好下一项任务、或者某些非抢占式通知，会等到内层工具循环和 Steering 全部消化完毕，才会从 Follow-Up 队列里拉出来开启新的一轮。

**Continuation 钩子**由上层引擎通过异步回调 `get_continuation_messages` 注入，没有固定内存队列。只有当模型自然停下、且用户和外部都没有排任何 Steering 或 Follow-Up 消息时，它才会被调用。Prime Agent 的 `/goal` 持久目标续跑和 `/autonomous` 质量门禁驱动，全部挂载在这个最底优先级的钩子上。只要用户发来任何指令，用户意图永远压过自动续跑。

## `rlm.end_turn()` 如何让模型体面地结束回合

在一个能后台跑 `bash()`、能 `rlm.spawn()` 一堆子 Agent 的系统里，经常出现一种尴尬局面：模型刚刚在 `python_repl` 里拉起了三个后台构建任务，此时不需要再做任何事，只需要把控制权还给用户，等任务完成后通过信箱通知回来即可。

如果单靠工具循环的默认规则，工具调用返回结果后，模型被迫还得再多调一次 LLM，只为了输出一句“我已经启动后台任务，正在等待”。既白烧一次带完整上下文的输入 Token，又多等几秒延迟。

Prime Agent 在 `python_repl` 里提供了一个受宿主认可的终结原语：`await rlm.end_turn()`。它的流转横跨三层源码：

1. Python 侧（`prime-agent-runtime/src/rlm/__init__.py` 第 229 行）向宿主发送类型为 `rlm.end_turn` 的 `host_request`。如果跑在不支持该请求的外部宿主机上，它会直接抛异常，绝不静默吞掉。
2. Rust 内核管理器收到后，给当前执行中的单元格打上 `ended_turn = true` 标记。`crates/pa-core/src/tools/ipython.rs` 第 385 行和第 473 行在拼装工具返回时，往结果文本末尾追加简短的 `turn ended` 记录以便会话回放，同时在 `ToolExecutionResult` 中返回 `terminate: Some(true)`。
3. 回到 `pa-agent` 的 `run_loop()` 第 104 行：`has_more_tool_calls = !executed_tool_batch.terminate`。注意它的批处理语义：如果同一条助手消息里包含多个工具调用，批内其余调用依然会按顺序跑完，只要批内任意一次 `rlm.end_turn()` 成功，整批结束后 `has_more_tool_calls` 就置为 `false`，不再触发下一次模型推理，直接把回合交还给用户，同时后台进程与子 Agent 继续在 Worker 和内核里运行。

## 队列暂停与恢复闸门

还有一个容易踩坑的边界是用户按下中止键（Abort）或手动触发 `/compact` 之后，排队里的消息该怎么办。

按照 `crates/pa-daemon/README.md` 记录的队列暂停规则（对应 `#227`/`#233` 决策），当 Worker 收到 `abort`、`abort_and_clear_queue` 或手动 `compact` 时，不只是掐断当前网络请求，还会把队列输入泵设为无限期暂停状态。在暂停期间，普通的 `prompt` 请求会被直接拒绝，队列里的待办项安全停放，不会被误自动触发。

直到出现明确的恢复动作，暂停闸门才会重新打开：用户发出显式带 `streamingBehavior` 的提示、显式发 `steer`/`follow_up`、调用 `resume_queue`、修改排队消息 `mutate_queued_message`、定时心跳触发，或者在存在活跃目标的情况下完成了一次成功的压缩。这样一来，用户在打断失控 Agent 后，就有充足时间检查和修改队列，不用担心刚按完 Ctrl+C，排在后面的消息又把系统带飞。

此外，在 `crates/pa-daemon/src/worker/` 的队列投影里，系统还会用 Rust 原生的类型化字段 `rlmChildStatus` 和 `inboxNotice` 标记出哪些排队行是守护进程内部生成的机器通知。渲染层据此把机器唤醒行折叠进计数条里，绝不把系统通知伪装成用户输入，也不会让用户输入的相似文本误触折叠逻辑。

Turn 循环解决了“什么时候调用模型、什么时候执行工具、怎么安全打断”的问题。接下来，我们沿着 `execute_tool_calls` 往下走，拆开 Prime Agent 最核心的唯一工具：`python_repl` 内核和它的 Protocol v3 线协议。
