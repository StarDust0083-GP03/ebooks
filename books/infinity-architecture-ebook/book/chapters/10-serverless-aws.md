# 零闲置云端部署：SQS FIFO、Lambda、Aurora DSQL 与持久定时器

把一个本地能跑的 Agent 搬上无服务器平台（Serverless），通常要大动干戈重写一遍状态机：本地内存通道没了，本地文件没了，`tokio::time::sleep` 超过十五分钟直接被 Lambda 强杀，多个并发回调同时撞进来还会把数据库写花。

Infinity 最具辨识度的架构验证，就落在 `crates/infinity-agent-lambda/`（1,894 行）和根目录的 `agent/`（AWS CDK 栈，1,200 行 TypeScript）里。它没有为云端另写一套执行引擎，而是把同一个 `infinity-agent-core` 的 `AgentSystemBuilder` 换上四块云端存储与传输适配器，直接跑在按次计费、空闲缩零的 AWS Lambda 上。

## 无锁并发：用 SQS FIFO 替代分布式锁

云端架构的入口只有两条：所有外部事件（用户输入、RAP 工具回调、定时器唤醒、子线程报告）全部汇入同一条 **SQS FIFO 输入队列**；外部工具服务的异步回包则 POST 到一个极薄的 **RAP Receiver Lambda Function URL**（`crates/infinity-agent-lambda/src/bin/rap-receiver.rs`，仅 136 行）。

`rap-receiver` 的代码干净得像一段胶水：解开可选的 Base64 请求体，解析 `RapCallback`，调用与本地守护进程完全共享的 `infinity_rap_bridge::prepare_callback`。如果是 `ViewUpdate`（旁路展示通道，无服务器环境没有常驻前端），直接返回 `200 {"ok":true}` 丢弃；其余四类回调则直接调用 `sqs_client.send_message()` 投进输入队列：

```rust
// crates/infinity-agent-lambda/src/bin/rap-receiver.rs
sqs_client
    .send_message()
    .queue_url(input_queue_url)
    .message_body(enqueue.message_body)
    .message_group_id(enqueue.group_id.into_inner())
    .message_deduplication_id(enqueue.dedup_id)
    .send()
    .await
```

这两行队列参数承载了整个云端并发控制的核心保证：

- **`message_group_id` 设为 `ThreadId`**，SQS FIFO 保证同一个 `MessageGroupId` 在同一时刻最多只会被一个 Lambda 实例拉取消费，彻底串行化单线程的读写切片；而一千个不同的 `ThreadId` 属于一千个不同的消息组，Lambda 会自动并行拉起多个实例水平扩展。核心运行时不需要引入 Redis 锁或数据库悲观锁。
- **`message_deduplication_id` 设为 `dedup_id`**，先在队列入口挡掉 5 分钟窗口内的重复投递；超过窗口的重投则由第 3 章的 `StateStore` 去重集合和历史尾扫兜底。

当 SQS 触发主 Agent Lambda（`crates/infinity-agent-lambda/src/event_handler.rs`）时，处理函数把收到的 `SqsEvent` 记录解析成 `(InputMessage, message_id)` 批次，交给 `AgentSystem::step`。即便把 SQS `batchSize` 调大到一次拉多条横跨不同消息组的记录，`step` 内部也会按 `group_id` 自动分组并并发推进各线程切片，完事把最终输出发往 `OUTPUT_QUEUE_URL`，随后进程退出，等待期间零算力账单。

## 双存储分工：Aurora DSQL 存历史树，DynamoDB 存状态与缓存

无服务器切片每次唤醒都要从零重建 `HistoryManager`，因此云端实现了 `ConversationStore` 和 `StateStore` 的持久化版本，刻意拆给两套特征互补的 AWS 存储服务：

| 抽象 Trait | AWS 实现 | 数据结构与表设计 | 选用考量 |
| --- | --- | --- | --- |
| `ConversationStore` | `DsqlConversationStore`（`conversation_history.rs`） | Aurora DSQL（兼容 Postgres）：`conversation_history(session_id, message_order, message_id, message_data)`、`thread_hierarchy(thread_id, parent_thread_id, root_thread_id, spawn_message_order, spawn_tool_call_id, closed, is_subscription_event, is_compaction, fresh_context)`、`compaction_summaries(thread_id, up_to_order, summary)` | 需要按 `message_order` 区间切片读取（`start_from`/`up_to`）、递归追溯祖先链以及按 `up_to_order <= cutoff` 查询压缩摘要，关系型范围查询最契合 |
| `StateStore` 与 `ToolsetCache` | `DynamoDbStateStore` / `DynamoDbToolsetCache`（`state_store.rs`、`toolset_cache.rs`） | 单表 `InfinityAgentsState`（主键 `session`）：存放去重 ID 集合、会话元数据、活跃订阅集合、挂起人工选择项、工具集配置和 RAP 清单缓存 | 键值读写频率高、结构松散、冷启动建连零握手开销 |

读 `DsqlConversationStore::new`（第 36 至 81 行）能看到几个真实的云端适配细节：它不存静态数据库密码，而是每次冷启动通过 `aws_sdk_dsql::auth_token::AuthTokenGenerator` 签发临时 IAM 管理员令牌，强制 `PgSslMode::Require` 走 TLS 建立最大 5 连接的 `sqlx` Postgres 连接池，并在建表时用了 DSQL 特有的 `CREATE INDEX ASYNC IF NOT EXISTS`。

另外，`traits.rs` 里关于 `load_history_up_to` 的一段注释解释了一个容易踩坑的边界对齐问题：本地 `InMemoryConversationStore` 内部用 0-based 数组下标（左闭右开 `[a, b)`），而 `DsqlConversationStore` 的 `message_order` 是 1-based 行号（SQL 条件 `message_order > $start AND message_order <= $up_to`）。为了不让 `spawn_message_order` 和压缩 `up_to_order` 在两种存储间差一位，trait 契约统一规定**所有边界参数一律按前导消息条数（leading-row count）解释**，于是无论 0-based 还是 1-based，`(Some(a), Some(b))` 取出的都是逻辑上的第 `a` 到 `b-1` 条消息。

## 持久定时器：900 秒为界的双轨休眠

本地驱动里的 `TokioSleepTool` 和 `TokioSleepUntilTool` 只要 `tokio::spawn` 一个内存定时器就行，但在 Lambda 上进程两秒后就退出了，内存定时器根本活不到唤醒时刻。

`crates/infinity-agent-lambda/src/tools/sleep.rs` 用 `WakeupScheduler` 实现了完全无状态的**持久定时器（durable timers）**，以 SQS 的最大原生延迟 **900 秒（15 分钟）** 为分水岭切出两条路径：

- **延迟 `<= 0` 秒（或目标时间已过）**：直接调用 `send_to_input_queue` 立即投回主输入队列唤醒。
- **延迟 `1 ..= 900` 秒**：把唤醒消息包进信封发往一条独立的延迟中继队列 `delay_queue_url`，设置 SQS 原生的 `delay_seconds`。到期后中继队列触发极轻量的 `delay-relay` Lambda，把里面的 `InputMessage` 按原 `group_id` 和 `dedup_id` 转发回主 FIFO 输入队列。
- **延迟 `> 900` 秒**：调用 AWS EventBridge Scheduler 的 `create_schedule`，用 `at(YYYY-MM-DDTHH:MM:SS)` 创建一个精确触发（`FlexibleTimeWindowMode::Off`）的一次性调度计划，目标直接指向 `input_queue_arn` 并附带 `SqsParameters { message_group_id }`。哪怕 Agent 决定睡到下周一早上九点纽约时间，这期间整个 AWS 账号里没有任何运行中的计算实例。

而且这两条路径和本地一样完全可打断：`SleepTool` 和 `SleepUntilTool` 都标记了 `is_passive() == true`。如果在休眠期间用户提前发了新消息，切片立刻醒来、给休眠调用注入中断结果继续干活；等几小时后 EventBridge 真的把迟到的唤醒消息投进来时，`HistoryManager::match_tool_result` 逆向尾扫发现早就跨过了轮次边界，直接当作过期消息静默丢弃，不会惊扰正在进行的新对话。

## 云端适配里的显式取舍

对比 `infinity-agent-lambda` 和 `infinity-daemon`，也能清楚看到无服务器部署为了换取缩容到零而主动接受的几条限制：

- `event_handler.rs` 传入的是 `NoDeferral`，这意味着如果某个活跃远程 RAP 工具正在执行时恰好有另一条订阅事件或子线程报告到达主输入队列，Lambda 切片无法在内存里暂存它，会直接按中断处理（除非自行实现传输层延迟重投的 `DeferQueue`）。
- 发起外部 RAP 请求使用 `RapHttpClient` 自动附带 SigV4 签名，免去在工具函数 URL 上维护静态共享密钥。
- `event_handler.rs` 第 35 行目前把云端模型 ID 硬编码为 `global.anthropic.claude-sonnet-4-6`（源码注释明确写着 `"hardcoded for now"`），且不携带本地 `drive_thread` 里的 75% 自动压缩逻辑。这些现状我们在第 12 章还会集中复盘。

后台的本地守护进程和云端 Lambda 两条运行底座都看全了，接下来转向最外层：人类开发者和外部团队究竟通过哪些交互壳子接触到 Infinity？下一章拆解终端 TUI、Zed/ACP 编辑器协议，以及用 Hydro 数据流语言写成的 Slack 机器人。
