# Infinity 源码研究笔记

## 核心架构发现（均来自静态源码核验）

1. **无阻塞切片不变量**：`infinity-agent-core` 的核心假设是单次执行切片（slice）绝不阻塞在外部工具上。线程在轮次之间只是存储在 `ConversationStore` 和 `StateStore` 中的数据，没有驻留任务、栈或长连接。`examples/agent_scale.rs` 与 `docs/src/components/MemoryChart.tsx` 记录了在 AMD EPYC 9R14 上单线程 `LocalSet` 跑满 80,000 个空闲 Agent（每个 20 轮、共 320 万次补全）后的实测 RSS 为 8,406,446,080 字节（约 8.4 GB，折合每个 Agent 约 102 KiB）。
2. **三函数切片与落盘屏障**：`prepare_input` -> `run_completion` -> `execute_action`。`Thread::step_no_defer` 在调用 `execute_action_with_error_result` 派发异步工具之前，必须先执行 `self.history.sync().await?`。这样即便派发后进程立刻崩溃，工具调用记录已在持久化历史中，后续回调到达时能精确挂接。
3. **三阶段未落盘状态（`HistoryManager`）**：
   - `unvalidated_items`：刚入内存历史但模型尚未产出任何输出的输入（用户文本、工具结果、合成事件）。`sync()` 跳过它们，防止超长毒消息落盘后永久卡死线程。
   - `pending_items`：模型流式产出第一个有效块时调用 `mark_inputs_model_validated()` 晋升至此；模型输出直接落入此队列，由 `sync()` 批量写入 `ConversationStore`。顺序性断言保证存在未验证输入时绝不能提交模型输出。
   - `turn_buffer`：当前流式轮次缓冲，合并连续 `StreamChunk::Text` 片段；结束时通过 `flush_turn` 或 `flush_turn_trimming_reasoning` 提交，中途出错或重试时 `discard_turn` 清空。
4. **溢出恢复（`recover_from_overflow`）**：当供应器返回 `ErrorClass::ContextOverflow`：
   - 若 `unvalidated_items` 恰好只有 1 条且为可替换项（工具结果或订阅事件），替换为 `TOOL_RESULT_TOO_LARGE_PLACEHOLDER` 并重试一次（已替换过的跳过，避免死循环）。
   - 否则（含用户输入、多条混合或再次溢出），将可替换项填为 `TOOL_CALL_INTERRUPTED_TEXT`，通过 `drop_unvalidated_user_inputs` 丢弃未验证用户输入并遗忘其去重 ID，直接停止而不静默重试。
   - 源码明确留有 `TODO(deferral)`：若已提交历史本身逼近上限，当前尚不支持阻塞等待低阈值压缩子线程完成。
5. **双重去重**：用户输入与订阅事件不是天然幂等的，其 ID 写入 `StateStore::add_processed_message_ids`；普通工具结果不写入去重表，而是通过 `HistoryManager::match_tool_result` 逆向扫描历史尾部连续的 `ToolCall`/`ToolResult` 序列判定 `Unanswered` / `AlreadyAnswered` / `NoPendingCall`。
6. **响应式工具协议 RAP（`rap-protocol`, `rap-client`, `infinity-rap-bridge`）**：
   - `GET /.well-known/rap-toolset` 发现 `ToolsetManifest`（含 `needsMigration` 与 Rhai `displayScript`）。
   - `POST /invoke` 携带 `RapInvocation`（`operation`, `arguments`, `id: ToolCallId`, `call_id: Option<ProviderCallId>`, `callback_url`, `group_id: ThreadId`, `user_id`, `thread_ancestors`），服务器立即回 2xx 后异步执行，完成后向 `callback_url` 发送 `RapCallback`（5 种变体：`tool_result`, `subscription_event`, `oauth`, `user_choice`, `view_update`）。
   - 生命周期通知端点：`/close_thread`、`/cancel_tool_call`、`/migrate`（及沙箱导入 `/migrate/import` 与用户选择 `/user_choice_response`）。
   - `infinity_rap_bridge::prepare_callback` 是守护进程和 AWS `rap-receiver` Lambda 共用的唯一回调转换入口。
7. **类型化 ID（`strkind`）**：
   - `ThreadId`、`ToolCallId`、`ProviderCallId`、`ChoiceId<T = String> = ToolCallId<T>`、`RemoteName`、`ThreadRef { remote, id }`。
   - 会话即根线程（`session_id` 已在 0.2.0 重命名为 `root_thread_id`）。`ThreadRef` 在线缆上仍编码为 `"{id}"` 或 `"{remote}/{id}"`，排序遵循本地优先于远程。
8. **线程树、同步工具与位置索引压缩**：
   - `spawn_thread` 实现 `supports_sync() == true` 与 `execute_synchronous`，在同一次切片内联完成并回环补全，防止派发与事件竞态。
   - `child_of` 必须严格等于 `context.thread_stack`，防止继承了父线程计划的子线程误触发重复派生。
   - `SpawnContext::InheritUpTo(safe_spawn_point)` 与 `SpawnContext::Fresh`。子线程始终在自身存储中写入一份 `spawn_thread` 调用副本，配合 `HistoryManager::find_tool_call` 穿透压缩摘要读取原始行，避免父线程压缩后丢失子线程回报挂接点。
   - 本地驱动在 `total_tokens > context_window * 3 / 4` 时自动派发 `TaggedSyntheticKind::Compaction`，派生压缩子线程，完成后写入 `(summary, up_to_order)`，由 `apply_compaction` 按 `compacted_up_to` 和 `ancestor_prefix_len` 精准切分内存历史。
9. **本地单线程 Actor 调度与延迟队列**：
   - `route_loop` 在单线程 `LocalSet` 上运行，用 `FuturesUnordered` 直接持有各线程 `drive_thread` future，线程空闲退出即刻释放全部内存。
   - 准入控制：非用户文本输入先查 `thread_exists` 和 `is_thread_stopped`，拒绝为不存在或已停止线程唤醒幽灵驱动（存储故障时 fail-open）。
   - `DeferQueue`（本地 `InMemoryDeferQueue` / Lambda `NoDeferral`）：当尾部存在未应答的非被动工具（`!is_passive()`）时，暂存可延迟合成事件（订阅事件、子线程报告、父线程消息），避免误触发中断；`sleep` 家族标记 `is_passive() == true` 允许自由打断。
10. **供应器协议与跨进程隔离**：
    - `infinity-provider-protocol` 通过 `ErrorClass`（`Transient`/`Throttled`/`ContextOverflow`/`Fatal`）解耦重试决策，核心运行时不设任何超时且不解析错误文本。
    - `infinity-provider-bedrock` 设置 60s `REQUEST_INITIATION_TIMEOUT`，流建立后不设中途空闲超时，并启用 `fine-grained-tool-streaming-2025-05-14` 与 `context-1m-2025-08-07`。
    - `remote.rs` 通过 Unix Domain Socket 换行分隔 JSON（每次请求新建连接以支持并发），借助 `WireError` 跨进程保留 `ErrorClass`。
11. **堆叠沙箱与 OS 级写隔离**：
    - `SandboxBackend` + `ModeProvider`：`LocalJjProvider`（`jj workspace add`、`update-stale`、`bookmark set -B`、`jj git export`、外部书签偏移检测）和 `LocalGitProvider`（`git worktree add`、`git_amend_all`、`git worktree remove`），所有工作区创建在 `{repo}/.infinity/.sandboxes/`，支持 `base_thread_id` 堆叠和 `squash_sandbox` 合并回父沙箱。
    - 写边界：macOS 使用 `sandbox-exec`（默认拒绝 `file-write*`，放行沙箱目录、临时目录、`sccache` 缓存、`/dev/null`）；Linux 使用 Bubblewrap `bwrap`（`--ro-bind / /`，仅对沙箱、临时目录及额外白名单 `--bind` 可写），并设置 `GIT_CEILING_DIRECTORIES`。`sandbox-local/build.rs` 在 Linux 缺少 `bwrap` 时会执行 `scripts/install-bwrap.sh` 从源码构建（需 `git`、C 编译器与 `libcap` 开发头文件）。
    - `Direct` 模式与 `write-orig` / `write:/path` 权限：沿 `thread_ancestors` 链检查授权，未授权时通过 RAP `UserChoice` 交互审批，可按线程层级持久化或单次放行。
    - `sandbox-remote` 使用 AWS EFS 存储裸 jj 仓库镜像，Lambda `/tmp` 作为瞬时沙箱。
12. **守护进程、SSH 远程与热迁移**：
    - `infinity-daemon` 监听 `~/.infinity/daemon.sock`（256 MiB `LengthDelimitedCodec`），支持绑定 Web UI 和 SSH 隧道代理远程守护进程（5 秒重连）。
    - 热迁移（`migrate.rs`）：本地守护进程担任编排器，关闭源会话，拉起目标端迁移 RAP 服务并建立 SSH 正向/反向/级联端口转发，通过 `jj git export` + `git bundle create` 打包所有线程书签经 multipart HTTP POST 至目标 `/migrate/import`（最多重试 5 次），再序列化迁移对话历史树并归档源会话。明确不支持 `Direct`、`Custom` 及跨多仓库会话迁移。
13. **Serverless AWS 与多端接入**：
    - Lambda 通过 SQS FIFO `MessageGroupId` 无锁串行化单线程并水平扩展多线程；Aurora DSQL 存三张表，DynamoDB 存状态与缓存；`WakeupScheduler` 对 `<= 900s` 走 SQS `DelaySeconds` 中继队列，对 `> 900s` 建一次性 EventBridge Schedule。
    - CLI 提供 TUI、无头提交 `-H` 与 `infinity acp`（Agent Client Protocol stdio 桥接 Zed 等编辑器）；Slack 机器人用 `infinity-slack-dataflow`（`hydro_lang`）声明式编排双向事件流。
