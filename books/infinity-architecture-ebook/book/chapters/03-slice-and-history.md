# 切片执行循环与三阶段 HistoryManager

试想一个线上常见的死锁事故：用户随手往对话框里粘贴了一段 80 万字的日志，或者某个 `grep` 工具一不小心扫出几十兆构建产物。传统 Agent 框架收到消息后，顺手把这条输入追加进了持久化对话表，再打包发给大模型。模型立刻返回上下文超窗报错；用户重试或者刷新页面重新加载会话，运行时又把刚才那条已经落盘的巨型消息原封不动地读出来发给模型，模型继续报错。一条超长输入，就能把整条会话线程永久卡死在毒消息（poison message）上。

再想另一个并发故障：模型决定调用一个修改数据库的远程工具，运行时先发了 HTTP 请求，再去把这次工具调用写进数据库。如果恰好在发出 HTTP 之后、落盘完成之前进程崩了，或者对方工具回包极快（比如 5 毫秒内就把回调推回了队列），下一轮切片在历史表里根本找不到对应的 `ToolCall`，只能把真实的执行结果当成幽灵消息丢弃。

`crates/infinity-agent-core/src/event_processor.rs` 用一套严谨的三阶段 `HistoryManager` 和落盘屏障，把这两类问题一起挡在了核心层。

## 一次切片的三段流水线

任何平台宿主（无论是本地常驻驱动还是 AWS Lambda 的单次调用）驱动一次切片，底层组合的都是 `event_processor` 暴露的三个函数：`prepare_input`、`run_completion` 与 `execute_action`。在 `crates/infinity-agent-core/src/system/thread.rs` 的 `Thread::step_no_defer` 里，它们的执行顺序是写死的：

1. **准备阶段（`prepare_input`）**：逐条吸收本批次的 `InputMessage`。过滤已关闭线程，拦截重复投递，处理压缩触发与压缩完成消息，分流订阅事件，把 OAuth 与 `UserChoice` 直接返回给上层前端，只把真正需要模型响应的新内容塞进 `HistoryManager`。如果整批消息没有任何一条返回 `PrepareResult::Ready`，本轮直接跳过模型调用，零成本结束切片。
2. **补全阶段（`run_completion`）**：把内存历史和工具定义发给 `ModelProvider`，以流的形式接收文本、推理块和工具调用。遇到同步工具（如 `spawn_thread`）或未知工具名，在当前循环内直接执行并把结果注回历史，继续发起下一次补全；遇到异步工具调用或普通结束信号，产出终端动作 `CompletionAction` 后跳出流。
3. **落盘屏障与派发阶段（`sync()` -> `execute_action`）**：先调用 `self.history.sync().await?` 把本轮所有已知安全的消息持久化，**确认落盘成功之后**，才调用 `execute_action_with_error_result` 向外部 RAP 服务发出 HTTP POST。

这里有一条不能违背的铁律：`sync()` 必须在 `execute_action` 之前完成。哪怕进程在派发瞬间崩溃，持久化存储里也已经记下了那条待应答的 `ToolCall`；而如果派发本身因为网络错误失败，`execute_action_with_error_result` 会立即把一条错误 `ToolResult` 塞回输入队列，让模型在下一轮感知失败并自行恢复，而不会永远干等一个永远不会来的回调。

## 三阶段未落盘状态与轮次缓冲

为了解决开头提到的毒消息卡死和流式中断脏写问题，`HistoryManager` 在已持久化的 `ConversationStore` 之上，把内存里尚未落盘的内容拆成了三层（对应 `event_processor.rs` 第 150 至 174 行的注释与字段）：

![HistoryManager 三阶段未落盘状态与溢出恢复](../../wiki/assets/diagrams/history-three-phase.svg)

第一层是 **`unvalidated_items`**。所有刚通过 `handle_content` 进入内存的新输入（用户文本、工具结果、内联合成事件）都先躺在这里。它们已经加入内存的 `history` 向量，所以紧接着发起的模型请求能看到它们；但 `sync()` 会刻意跳过这批项，一字节都不往存储里写。原因很直接：模型还没见过它们，谁也不知道里面有没有一条会撑爆上下文窗口的毒消息。

第二层是 **`pending_items`**（已知安全队列）。只要模型针对这次请求流式吐出了任意有效输出，就证明整个输入上下文没有超窗，运行时立刻调用 `mark_inputs_model_validated()`，把 `unvalidated_items` 整体晋升到 `pending_items`。模型自己产出的内容则在轮次提交时通过 `append_known_safe` 直接进入 `pending_items`。`append_known_safe` 内部有一条硬断言：如果此时 `unvalidated_items` 非空直接 panic，从代码逻辑上杜绝先持久化模型回答、漏掉对应输入的乱序可能。

第三层是 **`turn_buffer`**（流式轮次缓冲）。模型流式吐出的每个 `StreamChunk` 都先攒在这里，不污染 `history`。`handle_completion` 还会通过 `try_merge_buffer_text` 把连续的 `StreamChunk::Text` 片段合并成单条助手消息，避免把一句话按 token 切成几百行写进数据库把磁盘撑爆。流式过程若中途断连需要重试，调用 `discard_turn()` 一键清空 `turn_buffer`，下一轮重试直接从干净的已提交历史重建请求；流式正常结束时，调用 `flush_turn()` 或 `flush_turn_trimming_reasoning()`（剔除末尾孤立的推理块与空白文本，确保已提交历史不以半截 thinking 结尾）把内容刷进 `history` 和 `pending_items`。

这套分层顺带给前端重放提供了很大便利：`current_turn_view()` 把 `history` 和 `turn_buffer` 拼起来返回，于是用户在模型输出到一半时新开一个浏览器标签页附着上来，也能通过 `ReplaySnapshot` 立刻看到正在生成的那半段回复。

## 上下文溢出恢复：精确止损，绝不静默丢掉用户的原话

当模型供应器明确回报 `ErrorClass::ContextOverflow` 时，三阶段设计的价值就全体现出来了。因为引发溢出的新输入还留在 `unvalidated_items` 里，根本没进数据库，`recover_from_overflow`（`event_processor.rs` 第 1444 行）可以按两种情况精准处理：

- **单一可替换元凶**：如果 `unvalidated_items` 里恰好只有 1 条记录，且它是工具结果或订阅事件，那超窗的罪魁祸首毫无歧义。运行时调用 `replace_unvalidated_tool_results` 把它的正文替换成固定占位符 `TOOL_RESULT_TOO_LARGE_PLACEHOLDER`（`"[tool result omitted: it was too large for the model's remaining context window]"`），清除附带的 `display_segments`，然后立刻重试补全。替换函数会跳过正文已经等于占位符的条目，因此万一换完占位符仍然超窗，第二次不会陷入死循环，而是自动落入下面的丢弃分支。
- **含用户输入或多条混合**：只要 `unvalidated_items` 里混进了用户文本，或者同时有多条输入无法判断谁超了限，或者重试后再次溢出，运行时把其中的可替换工具结果统一结算为 `TOOL_CALL_INTERRUPTED_TEXT`（`"Tool call interrupted by user"`），调用 `drop_unvalidated_user_inputs` 把未验证的用户输入从内存历史末尾截掉并**从 `processed_message_ids` 中移除其去重 ID**，然后**直接终止本轮并向上报错，绝不静默重试**。

源码注释把这里两个很细的判断写透了：第一，如果删掉了用户的原话还悄悄拿旧历史再跑一轮推理，模型会按过时指令乱动作，所以丢弃用户输入后必须停下来等用户重新发话；第二，此时被一起结算的挂起工具结果不能填成结果过大的提示，否则模型下一轮可能会误以为应该重新跑一遍工具，填成和用户打断一致的 `"Tool call interrupted by user"` 才准确。同时，因为被丢弃的用户输入在 `StateStore` 里从未落盘，且内存里的去重 ID 也被遗忘，这条线程在下一条正常消息到来时立刻恢复健康，彻底告别毒消息锁死。

## 双重去重策略：谁该写去重表，谁靠尾扫就行

在至少一次投递的队列上，消息重投是常态。如果把每一条工具结果、每一条助手回复的 ID 全塞进 `StateStore` 的去重集合，长会话的状态记录会越滚越大。

`HistoryManager::sync()`（第 649 至 669 行）做了一次非常克制的拆分：**只有天然不幂等的输入才会把 `message_id` 持久化到 `StateStore`**，也就是 `InfinityMessage::User` 和 `InfinityMessage::SubscriptionEvent`。订阅事件如果不记 ID，重投一次就会再生成一条新的合成调用注入历史，造成重复响应。

而普通 `ToolResult` 完全不写持久化去重表。`handle_content` 收到工具结果时，调用 `match_tool_result` 从 `history` 末尾逆向扫描：

```rust
// crates/infinity-agent-core/src/event_processor.rs
fn match_tool_result(&self, result_id: &str) -> ToolResultMatch {
    for msg in self.history.borrow().iter().rev() {
        match msg {
            InfinityMessage::ToolCall { call, .. } => {
                if call.id == result_id {
                    return ToolResultMatch::Unanswered;
                }
            }
            InfinityMessage::ToolResult { result, .. } => {
                if result.id == result_id {
                    return ToolResultMatch::AlreadyAnswered;
                }
            }
            _ => return ToolResultMatch::NoPendingCall,
        }
    }
    ToolResultMatch::NoPendingCall
}
```

这个逆向扫描只跨越连续的 `ToolCall` 和 `ToolResult`（顺手兼容了未来并行多个工具调用形成 `tc tc tr tr` 尾部的场景）；一旦遇到任何用户文本、助手文本或订阅事件，说明碰到了上一轮边界，直接判定 `NoPendingCall` 丢弃过时回包。如果逆向先撞到同 ID 的 `ToolResult`，说明已经应答过，判定 `AlreadyAnswered` 跳过；先撞到同 ID 的 `ToolCall`，才判定 `Unanswered` 放行。

当非工具结果消息（如用户突然打断）到达，且历史最后一条正好是未应答的 `ToolCall` 时，`interrupt_pending_tool_call` 会往 `interrupted_tool_calls` 里登记该调用 ID，并自动补一条内容为 `"Tool call interrupted by user"` 的合成 `ToolResult` 保持对话结构合法，随后由上层 `Thread::step_no_defer` 通过 `RapNotifier` 尽力向工具服务器发取消通知。即便远端工具没来得及停下、过几分钟仍把真实结果推了回来，尾扫也会因为历史里已经跨过了新的轮次边界而干净利落地把它挡在门外。
