# 失败模式、安全边界与架构取舍

任何一套架构选型都是在用一种约束换另一种自由。只看设计文档很容易觉得无阻塞切片加堆叠沙箱无懈可击，但把源码里的防御断言、降级路径和作者亲手写下的 `TODO` 翻出来对照，才能看清它真正守住了什么、故意放弃了什么，以及哪些坑还没有填平。

## 安全边界：本地沙箱防的是误写，不是恶意代码

把 `sandbox-local/src/backend.rs` 里的 macOS `sandbox-exec` 配置和 Linux `bwrap` 参数摊开看，有一条边界必须先亮明：

- **只限写，不限读**：Linux 端是 `--ro-bind / /`，macOS 端是 `(allow default)` 再接 `(deny file-write*)`。沙箱里的子进程能正常读取整台机器上当前用户有权限读的任何文件，包括 `~/.ssh/`、`~/.aws/`、`.env` 和其他项目代码。
- **不限网络**：`bwrap` 没有加 `--unshare-net`，`sandbox-exec` 也没有屏蔽网络外连。构建工具下载依赖顺畅无阻，代价是如果模型被提示词注入跑了一段恶意脚本，依然可以把本地凭据外传出去。
- **不支持平台的静默降级**：在 macOS 和 Linux 之外的平台（`!platform_sandbox_available()`），`LocalBackend::new` 只打印一条 `tracing::warn!` 后直接裸跑命令。

`sandbox-local` 解决的工程问题是防止可信开发场景下的并发文件踩踏和误删宿主工作树，它不能替代 Firecracker 微虚拟机或无网隔离容器去运行不可信的第三方代码。如果要在多租户云端跑不受信仓库的任意脚本，必须在 `sandbox-remote` 之外叠加容器级或微虚机级的网络与读隔离。

## 溢出恢复的已知盲区：已提交历史本身逼近上限

第 3 章拆解过 `HistoryManager` 的三阶段溢出恢复：只要超窗元凶在刚进来的 `unvalidated_items` 里，就能干净地替换占位符或丢弃未验证用户输入。

但 `crates/infinity-agent-core/src/event_processor.rs` 第 1439 至 1443 行坦诚留了一段 `TODO(deferral)`：如果**已经提交落盘的历史本身**就非常接近模型上下文上限（比如本地 75% 自动压缩还没来得及跑完，或者在没有自动压缩的 Lambda 路径上持续累加），此时哪怕进来一条很短的正常消息也会触发 `ContextOverflow`。`recover_from_overflow` 只能丢掉刚进来的新输入，根本缩不动已提交历史。理想的修复是派发一个更低阈值的压缩子线程并让当前线程进入延迟等待，等压缩完成后再重放当前消息，但等待进行中的压缩子线程对应的延迟机制目前源码里尚未实现。

## 本地 vs Serverless 的能力落差

尽管本地和云端共用同一个 `infinity-agent-core`，实际行为上仍然存在四处清晰的能力落差：

| 机制 | 本地常驻（`infinity-daemon` / `LocalAgentSystem`） | 无服务器云端（`infinity-agent-lambda`） |
| --- | --- | --- |
| 活跃工具期间到达的合成事件 | `InMemoryDeferQueue` 暂存，等活跃工具结算后按序排空（但进程崩溃会丢内存延迟队列） | 默认 `NoDeferral`，直接按中断语义给活跃工具注入中断结果 |
| 上下文自动压缩 | `drive_thread` 在 `total_tokens > context_window * 3 / 4` 时自动触发 | `event_handler.rs` 未接入自动压缩触发器，需外部显式发压缩消息 |
| 模型选择 | 支持每线程动态 `/model` 切换与外部插件供应器 | `event_handler.rs` 第 35 行当前硬编码 `global.anthropic.claude-sonnet-4-6` |
| 前端实时视图 `ViewUpdate` | 写入 `{thread_id}.views.json` 并推给 TUI / Web 渲染 Diff | `rap-receiver` 直接回 `200 {"ok":true}` 丢弃 |

如果准备把 Infinity 真正放上 AWS Lambda 跑复杂的多子线程任务，`NoDeferral` 和缺少自动压缩是最先需要补齐的两块拼图：前者可以通过在 `DeferQueue::push` 里把消息带 `DelaySeconds` 投回 SQS 中继队列来实现传输层持久化延迟；后者则可以在 `event_handler.rs` 收到 `StepOutcome::Completed { usage, context_window }` 后复用与本地相同的 75% 阈值判断并往 SQS 投递一条 `TaggedSyntheticKind::Compaction`。

## 单线程 LocalSet 与工具侧的隐性限制

本地调度器选了单线程 `tokio::task::LocalSet` 换取零锁状态机、极低内存占用和无竞态的同步 `check_before_idle`。这个取舍的背面是：**所有在 `LocalSet` 上直接运行的同步代码都不能做重 CPU 计算或阻塞磁盘 I/O**。

守护进程里的 `PersistentConversationStore` 目前用 `std::sync::Mutex` 和同步文件写入来落盘每线程 JSON；当单个线程的历史增长到几兆字节，或者几十个线程同时刷盘时，序列化 JSON 和同步 `fs::write` 会短暂卡住同一个线程里的所有其他活跃驱动。同样的，第 9 章看到的跨主机热迁移也明确划定了禁区：`SandboxMode::Direct`、`SandboxMode::Custom` 以及同一个会话里同时操作了两个以上不同 Git 仓库时，`migrate_inner` 会直接返回错误拒绝迁移。

## 何时该用这套架构，何时不必折腾

回头对照全书拆过的机制，Infinity 的适用边界其实非常清楚：

- **适合的场景**：需要同时挂载大量长周期、事件驱动 Agent（CI/CD 盯盘、PR 审查、人工审批流、定时巡检）；需要按实际推理切片缩容到零以控制云账单；或者需要在一台开发机上同时开多条并行编码线程，既要互不干扰又要能随时把会话和代码整体迁往远程算力机。
- **不必引入的场景**：如果业务只是同步一问一答的短平快 RAG 机器人，所有工具调用都在几百毫秒内返回，既没有跨小时等待也没有多子线程并发编辑，直接用轻量单循环加标准 MCP 服务反而架构更薄、排障链路更短。

理解了取舍与边界之后，最好的巩固方式是亲手跑一遍核心状态机。下一章不依赖 Rust 工具链、也不依赖任何第三方 Python 包，直接用一份四百多行的标准库 Python 教学实现把 Infinity 的五项核心不变量完整跑通并验证。
