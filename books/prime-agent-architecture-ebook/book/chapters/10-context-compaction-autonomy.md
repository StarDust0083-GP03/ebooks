# 自动压缩、目标驱动与有界自主运行

一个跑上几个小时的自动化研究或代码重构任务，最容易在三个节点半途而废：第一，上下文逼近窗口上限，压缩触发得太晚直接撞供应商的 `input + output > contextWindow` 报错，或者触发阈值配得太低，导致每次工具调用都在反复重摘要同一段近期历史；第二，模型刚改完一半就以为差不多了，自己提前宣布结束回合；第三，启用了循环重试门禁，但模型根本没改代码，脚手架却在原地把耗时的 `cargo test` 傻傻重跑十遍。

Prime Agent 用四块咬合在一起的组件管住长程续跑：带双保护边界的**自动压缩**、单活跃槽位的**持久目标（Goal）**、跨重启的**定时心跳（Heartbeat/Cron）**，以及带工作树快照防重跑的**有界自主质量门禁（Autonomous Mode）**。

![RLM 变量卸载与上下文自动压缩机制](../../wiki/assets/diagrams/context-compaction-offload.svg)

这张图把 RLM 变量卸载和自动压缩放在一起看，就能明白为什么压缩在这里不像传统聊天框架那样伤筋动骨：压缩只折叠对话消息，Python 内核继续保留小中型变量，同时用精确的数学公式卡住触发线。

## 双保护底线的自动压缩公式

上下文压缩的纯决策逻辑位于 `crates/pa-core/src/session_engine/compaction.rs`。它面对两个相互拉扯的工程约束：

一方面，很多模型供应商（尤其是把思考预算折算进输出上限的 Anthropic 与 Bedrock 路由）实行**联合上限**校验，只要 `输入 Token + 请求的输出预算 > contextWindow` 就会直接拒单。旧版 TypeScript 代码只按 `contextWindow - reserveTokens` 判断，在输出预算较大时仍可能撞墙。Rust 重写版专门补上了这条安全上限：

```rust
let requested = (context_window as f64 * (percent / 100.0)) as u64;
let retention_floor = settings
    .keep_recent_tokens
    .saturating_add(settings.reserve_tokens.saturating_mul(2))
    .saturating_add(1);
let percentage = requested.max(retention_floor);
let headroom = settings.reserve_tokens.max(COMBINED_LIMIT_HEADROOM_FLOOR);
let combined = context_window
    .saturating_sub(max_output_tokens)
    .saturating_sub(headroom);
percentage.min(combined)
```

公式里四个默认常量都写死在源码顶部：

- `DEFAULT_THRESHOLD_PERCENT = 95.0`（`COMPACT_THRESHOLD_RATIO = 0.95`）：防止大窗口下 `chars / 4` 估算漂移；
- `DEFAULT_RESERVE_TOKENS = 16_384`：预留缓冲；
- `DEFAULT_KEEP_RECENT_TOKENS = 20_000`：压缩后保留的近期对话 Token 数；
- `COMBINED_LIMIT_HEADROOM_FLOOR = 4_096`：即便用户调小 `reserve_tokens`，联合上限下依然保留至少 4,096 Token 余量应对估算误差。

留意 `retention_floor = keep_recent_tokens + 2 * reserve_tokens + 1`（默认 `52,769`）。如果没有这条底线，一旦用户把压缩比例设成 10%，每次压缩完留下的 20,000 Token 近期上下文加上两轮摘要开销又立刻超过阈值，系统就会陷入每跑一个工具就重压缩一次的死循环。

Token 估算本身走两路：有最近一次非错误、非中止助手回复的真实供应商 `Usage` 时优先采用真实账单（`total_tokens` 或 `input + output + cache_read + cache_write` 之和，因为本轮输出就是下一轮输入的一部分）；新增未结算消息则按保守的 `chars.div_ceil(4)` 估算，图片块固定按 `4,800` 字符（约 1,200 Token）计入，视频块按 `VIDEO_ESTIMATED_TOKENS * 4` 计入。

触发压缩的方式有三种：达到阈值自动触发、用户执行 `/compact`、或者模型在 REPL 里调用 `await compact.run(instructions=...)` 预约在当前回合结束后执行。压缩过程中，旧消息被总结为 `compactionSummary` 落盘，随后系统做三件收尾动作：通知 Python 内核清理单个序列化超过 `16 MiB` 的超大变量、在压缩后的冷边界重新投递 `[harness-digest]` 并附加 `ipython_state` 持久态通知，以及在存在活跃目标时把目标续跑重新接入队列。而被压掉的历史并没有消失，模型随时可以通过 `await rlm.session.messages(start=..., limit=..., role=...)` 按切片翻回压缩前的原始记录。

## 持久目标 `/goal` 与定时心跳

长程任务里，模型常常在修完一个报错后就习惯性地停下来问“接下来还要做什么”。

`/goal`（及内核技能 `goal.create` / `goal.get` / `goal.complete`）在单会话内维护唯一一个活跃目标。每当目标状态变化，Worker 都会把最新状态（目标文本、状态、用量计数、续跑次数）作为 `thread_goal_state` 自定义行落盘进会话 JSONL，保证崩溃重启后完整续接。在每个回合自然结束时，引擎通过 `crates/pa-core/src/session_engine/goal_boundary.rs` 决定是否继续：

1. 若仍有未结算的 RLM 子 Agent，目标续跑主动让位延后，避免在子任务还没回信前空转催促主 Agent；
2. 若目标尚未调用 `goal.complete()` 且未触及 Token 预算上限，引擎自动铸造一条带目标上下文的 Follow-Up 续跑消息驱动下一轮；
3. 针对用户按下 Stop 键或中途 `/goal pause` 的竞态，v0.10.5 专门修复了两处细节（`.changes/sebastian-goal-status-error-race.md` 和 `.changes/sebastian-goal-update-terminal-check.md`）：刚发提示就按 Stop 不再把活跃目标误标为失败，回合中途暂停目标也保证在回合结束时向所有客户端广播最新状态。

而对于需要按时间周期巡检（例如每 5 分钟检查一次训练指标或 CI 状态）的任务，Prime Agent 提供了 `crates/pa-core/src/cron/` 与内核技能 `rlm_heartbeat`。所有定时任务按跨进程文件锁存在会话制品的 `scheduled-jobs.json` 里，支持 `steer`（默认，可打断忙碌会话）和 `follow_up` 两种投递模式。每次修改心跳都会通过 `heartbeats_changed` 广播给所有挂载的 UI 客户端；而在开启信箱的会话里，同一个心跳任务在信箱里永远只留最新一封未读，避免离线一夜攒出一百条重复提醒。

## 有界自主模式与防重跑质量门禁

如果说 `/goal` 靠模型自己判断是否完成，那么 `/autonomous`（`crates/pa-core/src/autonomous/`）则把验收权交给外部命令和硬预算。

CLI 和会话配置允许同时设四道硬边界：`max_continuations`、`max_turns`、`max_tokens` 和 `timeout_ms`，再加上一组用户自定义的 Shell 质量门禁命令（`--autonomous-gate`）、单门禁超时和最大重试次数。

每当 Agent 回合自然停下，Worker 的 `autonomous_continuation.rs` 钩子调用 `should_autonomously_continue()`（`crates/pa-core/src/autonomous/gates.rs` 第 86 行）：

1. **门禁优先于预算**：只要质量门禁全部退出码为 0，自主循环立刻宣告达标停止，不再浪费任何预算。
2. **工作树快照防空跑**：门禁失败后，`ShellGateRunner` 会对当前会话工作目录拍一份 Git 工作树内容哈希快照（`capture_git_worktree_snapshot`），把失败结果（输出上限 `MAX_GATE_OUTPUT_CHARS = 6,000`，子进程单流捕获上限 `1 MiB`）拼进续跑提示交给模型去修。下一轮结束时，如果模型只是嘴上解释了一通却根本没有改动工作树文件，快照比对一致，门禁拒绝重跑耗时的测试脚本，防止原地空烧。
3. **关闭 Git 配置注入面**：因为快照要在用户可能不完全信任的目标仓库里跑 `git` 探测命令，`gates.rs` 顶部明文列出了它用命令行参数强行关掉的危险扩展点：禁用 `fsmonitor` 钩子、禁用索引写钩子、禁用 promisor 懒拉取、禁用外部 diff 与 textconv 转换器、禁用子模块读取。它也极其坦诚地在源码注释里写明了唯一无法通过参数关掉的残余面：如果已修改的受跟踪文件命中了 `.gitattributes` 里的 `clean` 或 `process` 过滤器，Git 仍会执行它们。
4. **明确不夸大结论**：`README.md` 与 `gates.rs` 都把话讲得很实：门禁通过只证明你配置的那几条命令通过了；而因为触发轮次、Token 或时间上限停下来，绝不等于任务已经成功。

自动压缩、目标、心跳和门禁管住了本地长程执行；但如果模型需要连 Slack、Google Workspace 或公司内部 API，怎么既把外部工具无缝接进 Python REPL，又不把 OAuth 密钥和访问令牌散落进模型代码？下一章看 Prime Agent 的 MCP 桥接层。
