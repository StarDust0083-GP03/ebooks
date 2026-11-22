# 线程树、上下文继承与位置索引压缩

让一个主 Agent 拆出三个子 Agent 并行审查三份代码文件，看起来只是多开几个异步循环。真跑到生产环境，立刻会冒出四类头疼的上下文事故：第一，子线程继承了父线程准备派生三个子线程的思考过程，刚启动就误以为自己是父线程，接着把同样的三个子线程又派生了一遍，引发指数级套娃；第二，父线程派完子线程后继续聊了几十轮、触发了上下文压缩，把当初那条 `spawn_thread` 调用折进了摘要里，等子线程干完活回来交差，父线程翻遍当前上下文却找不到当初派活的调用 ID，结果直接把子线程的成果丢弃；第三，某个 GitHub 订阅每分钟推十条无关 PR 事件进来，如果全塞进主线程上下文，半小时就能把主线程窗口填满；第四，压缩历史时如果直接删旧行，压缩前就已经派出去、还没读完父历史的子线程，继承到的索引瞬间全部错位。

`infinity-agent-core` 用一套紧凑的线程树模型和位置索引压缩机制，把这四个坑逐一填平了。

## 同步派生与 child_of 线程栈校验

内置的线程控制由四个工具组成，全部定义在 `crates/infinity-agent-core/src/tools/thread.rs`：`spawn_thread`、`report_to_parent`、`close_thread` 和 `send_message_to_child`。

其中 `spawn_thread` 专门实现了 `supports_sync() == true` 和 `execute_synchronous`。前文提到过，把派生做成切片内同步执行有两个好处：一是省掉一次存储重载，二是彻底避免刚派发出去就被并发事件误判中断的竞态。

为了挡住上面说的子线程身份错乱重复派生事故，`spawn_thread` 强制要求模型传入 `child_of` 参数，内容必须是从根线程到当前线程的完整路径栈（例如根线程传 `["R"]`，一级子线程传 `["R", "A"]`）。`execute_synchronous` 第一步就把模型传进来的 `child_of` 和运行时掌握的真实 `context.thread_stack` 做精确比对：

```rust
// crates/infinity-agent-core/src/tools/thread.rs
if child_of != context.thread_stack {
    return Some(ToolResult {
        id: id.as_str().to_owned(),
        call_id: call_id.map(|c| c.as_str().to_owned()),
        content: vec![ToolResultContent::Text(Text {
            text: format!(
                "Error: child_of {:?} does not match the actual thread stack {:?}. \
                 You may be confused and think you are in the parent thread, \
                 but you are not. You are in thread {}. Do NOT spawn threads — \
                 focus on your assigned task.",
                child_of, context.thread_stack, context.group_id
            ),
        })],
    });
}
```

这条校验非常管用。如果一个子线程读了继承下来的父线程计划，顺手照抄了父线程的 `child_of: ["R"]`，运行时会当场把这次调用打回，并把真实的线程栈提醒给它，把它拉回自己的子任务轨道上。

## 继承边界、Fresh 子代理与自身副本播种

派生新线程时，调用方可以在两种继承语义里二选一（`crates/infinity-agent-core/src/traits.rs` 的 `SpawnContext`）：

- **`SpawnContext::InheritUpTo(safe_point)`**（默认），子线程继承父线程（及所有更早祖先）直到 `safe_point` 为止的对话历史，但看不到父线程在派生之后新增的任何消息。`safe_point` 由 `HistoryManager::safe_spawn_point()` 计算，它会刻意截断在两类内容之前：一是尾部所有尚未收到结果的 `ToolCall`（防止子线程继承到悬空调用），二是所有还在内存里、尚未持久化到 `ConversationStore` 的 `unvalidated_items`（因为子线程是从持久化存储加载祖先历史的）。
- **`SpawnContext::Fresh`**（通过参数 `fresh_context: true` 开启），子线程以完全空白的历史启动，只看到本次派发给它的自包含指令。源码里把新鲜边界做成可传递的线程属性：`ConversationStore::load_history_with_ancestors` 从叶节点向根回溯祖先链时，一旦遇到某个标记为 `is_fresh_context_thread` 的节点，就会把该节点之上所有更早祖先的切片全部裁掉，确保新鲜子代理再派生孙线程时也不会意外泄漏更早的背景噪声。

这里藏着一个极其讲究的实现细节：哪怕在默认继承模式下，`spawn_thread` 也会把继承截断点落在**本次 `spawn_thread` 调用之前**，然后把本次 `spawn_thread` 调用（保留原始 `id`、`call_id` 和参数）**单独复制一份写进子线程自己的存储行里**，再把子线程任务说明作为对应的 `ToolResult` 投进子线程队列。

为什么要多此一举自己种一份副本？原因就在父线程压缩。因为父线程随后可能触发上下文压缩，把父线程里的那条 `spawn_thread` 折进摘要；如果子线程只能通过继承切片看到它，那么当父线程发来 `send_message_to_child` 消息时，子线程将无法在自身历史里解析原始调用。如今子线程在自己的存储里拥有一份独立副本，配合下面提到的穿透查找，父线程怎么压缩都不影响子线程后续通信。

## 合成工具调用与双向通信

大模型 API 通常只认识用户消息、助手消息以及成对出现的工具调用与工具结果，并不理解后台子线程突然发来异步报告这种概念。如果硬把报告伪装成一条普通用户消息塞进去，模型很容易把子代理的汇报错当成人类用户的最新指令。

Infinity 的统一包装手法是**合成工具调用（synthetic tool call）**。无论是子线程调 `report_to_parent` 发送中间进度、调 `close_thread` 汇报最终结论并永久关闭自己，还是父线程调 `send_message_to_child` 向运行中的子线程追加指示，底层的 `prepare_input` 都会根据 `SyntheticKind`（`ThreadReport`、`ParentMessage` 或 `SubscriptionEvent`）先反查出最初的那条调用记录，再往目标线程历史里注入一对结构合法的 `receive_event__injected` 工具调用与结果：

- 合成调用参数包含 `original_tool_name`、`original_tool_call_id` 和 `original_args`，模型一眼就能对齐这是哪次 `spawn_thread` 或哪次订阅带来的异步消息。
- 在持久化结构 `InfinityMessage::SubscriptionEvent` 中，这对合成调用与结果作为一个变体保存（payload 用 `Box` 包裹，避免拉大整个 `InfinityMessage` 枚举体积）。发给模型时 `into_messages()` 把它展开成两条标准消息；前端重放历史时则直接渲染成干净的订阅或子线程汇报卡片，不会把内部的 `receive_event__injected` 脏细节暴露给用户。

那么如果当初父线程的那条 `spawn_thread` 已经被压缩进了摘要，怎么反查？`HistoryManager::find_tool_call`（`event_processor.rs` 第 779 行）先在内存中的当前视图逆向查找；一旦未命中，立刻回退调用 `conversation_store.load_history_up_to(&self.thread_id, None, None)`，穿透压缩视图直接翻查本线程在持久化存储里的全部原始行。因为压缩只折叠加载出来的视图、从不删除底层原始记录，所以即使父线程休眠并压缩了好几轮，子线程迟到半天的关闭报告依然能精准挂接回原始 `spawn_thread` 调用。

## 订阅事件分流：隔离事件子线程 vs associative 内联

同样的线程树原语，顺手解决了外部事件流的上下文污染问题。

当一条来自 RAP 服务的 `subscription_event` 到达父线程时，`prepare_input` 会查看事件上的 `associative` 标志：

- **默认模式（`associative == false`）**：父线程自己不消费原始事件，也不把它写入父历史。`prepare_input` 直接计算父线程当前的 `safe_spawn_point()`，自动派生一个临时的事件处理子线程（标记 `is_subscription_event = true`），往子线程存储里种好合成的 `spawn_thread` 和 `receive_event__injected` 引导上下文，再把原始事件正文投进子线程的输入队列。子线程独立分析事件（例如拉取 PR diff 检查改动），觉得重要就调 `report_to_parent` 或带报告调用 `close_thread`；如果事件毫无价值，子线程直接空报告关闭，父线程上下文里连一丝痕迹都不会留下。短时间内涌入十条事件，就会派生十个子线程并行消化。
- **内联模式（`associative == true`）**：适用于每一条输出都必须按序进入当前线程自身历史的流式场景（例如 `sandbox-core` 跑长命令时实时推送的构建日志行）。这类事件跳过派生子线程，直接以 `receive_event__injected` 注入订阅线程自身历史。

无论走哪条路，只要事件携带了 `final: true`，`prepare_input` 就会同步调用 `current_history.remove_subscription`，把该订阅从 `StateStore` 的活跃集合里摘除。

## 位置索引压缩：不删旧行，按游标折叠

长对话总有一天会撞上模型上下文窗口。本地驱动 `drive_thread` 在 `handle_step_result`（`crates/infinity-agent-core/src/system/local/driver.rs` 第 384 行）里监控每次补全返回的 `usage.total_tokens`（刻意用 `total_tokens` 而不是 `input_tokens`，避免启用 Prompt Caching 时少算缓存命中部分）。一旦满足：

```rust
total_tokens as usize > context_window * 3 / 4
```

本地驱动就自动向自己塞入一条 `TaggedSyntheticKind::Compaction` 合成消息（同时置位 `compaction_triggered = true` 防止重复触发，直到收到 `CompactionComplete` 后复位）。

有趣的是，**上下文压缩本身也是用子线程机制实现的**。`prepare_input` 收到压缩触发消息后，取当前 `safe_spawn_point()` 派生一个标记为 `is_compaction = true` 的子线程，种入名为 `__harness_begin_compaction__` 的引导调用，要求该子线程把继承到的全部历史总结成摘要，再以摘要作为 `report_to_parent` 调用 `close_thread`。

压缩子线程调用 `close_thread` 时，`CloseThreadTool` 识别出 `is_compaction == true`，不走普通父子汇报流程，而是读取子线程派生时记录的 `spawn_order`（记作 `up_to_order`），调用 `conversation_store.save_compaction_summary(&parent_id, report_text, up_to_order)` 单独存入摘要表，随后向父线程投递一条 `TaggedSyntheticKind::CompactionComplete` 消息。

父线程收到通知后调用 `HistoryManager::apply_compaction()`，核心只是一段精准的相对坐标换算：

```rust
// crates/infinity-agent-core/src/event_processor.rs
let offset = self
    .compacted_up_to
    .borrow()
    .map_or(0, |prev| prev as usize - 1);
let up_to =
    (up_to_order as usize).saturating_sub(offset) + self.ancestor_prefix_len.get();
```

这段算式把两个边界条件交代得清清楚楚：如果当前线程之前已经压缩过一次，那么旧摘要本身占住了内存历史的槽位 `0`，所以偏移量要减 1；如果当前线程是个子线程且之前还没压缩过，内存历史最前面还拼着 `ancestor_prefix_len` 条不属于自己存储表的祖先消息，切分位置必须加上这部分前缀，并在压缩完成后把 `ancestor_prefix_len` 归零（因为祖先内容已经被吞进新摘要里了）。

把摘要按 `up_to_order` 单独建表而不删改原始消息行，带来的直接收益就是历史视图的时序一致性。一个在索引 10 处被派生出来的子线程，哪怕父线程后来在索引 40 处做了压缩，`load_history_with_ancestors` 为该子线程查父摘要时传入的是 `up_to_order <= 10`，自然会跳过索引 40 的摘要，照旧精确还原出 0 到 10 的原始父历史；而在索引 50 处派生的新子线程，则会直接拿到索引 40 的摘要加上 40 到 50 的增量行，省下全部重复 token。

线程树和压缩机制把状态结构理顺了，但在本地单机跑几十个并发线程时，消息什么时候该批处理、什么时候该延迟、什么时候该立刻抢占打断？下一章拆解本地单线程路由器与延迟队列。
