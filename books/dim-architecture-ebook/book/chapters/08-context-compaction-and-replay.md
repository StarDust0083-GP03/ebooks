# 上下文投影、压缩与历史渲染边界

一个持续跑了 2000 步、经历过七八次上下文压缩的长会话，每次发新请求前到底要从数据库里读多少字节？如果每次准备模型请求都把整条分支历史从第 1 条事件全量反序列化进内存，长会话很快就会把守护进程的内存拖爆；反过来，如果把压缩后的消息数组当成唯一真相写死在缓存里，一旦缓存版本升级、校验损坏或者用户把分支切回到更早的历史点，整个会话的上下文就会永久失真。

Dim 在 `ContextPreparation`、`ContextUsage`、`HistoryRenderer` 和 `TimelineStore` 之间划出了一道非常干净的边界：**事件事实管真相，恢复快照只管加速，上下文入口只留两条窄门。**

## 两条窄门：什么能进入模型上下文

在 `AGENTS.md` 规则 10 和 `principles.md` §4 规则 6 里，Dim 明确禁止各个业务插件绕开统一路径往模型上下文里塞文字。一条事件能不能变成模型看到的 `messages`，只有两条合法通道：

1. **已采用的输入**（经过 `InputQueue` 交接并由 `Loop` 消费的 `input/consumed`，以及由 `Prompt` 发布的 `context/configured`）。未经过交接的草稿、被撤回的 `input/deleted`、被 Hook 拦下的提交、控制类事件（如 `approval/*`、`stop/interrupt`、`branch/default`）天然不会进入模型消息，渲染器不需要到处写黑名单去补漏。
2. **工具执行结果**（`step/tool-result` 里的 `ToolResult.content`）。这里还有一条容易被忽略的约定：`ToolResult.content` 是模型唯一能读到的文本，而 `structuredContent` 只留给持久化、客户端 UI 和内部状态机使用。凡是模型后续还需要用到的句柄、任务 id 或分页游标，工具作者必须显式写进 `content`。

另外，所有运行时注入（例如技能文本、Hook 附加说明、分支身份块 `<dim-branch-line>`、分支名册 `<dim-branch-roster>`、父线进展提示 `<dim-branch-parent>`）一律包装成带标记的原始块 `InputBlock::Raw` 通过输入通道写入历史，绝不靠“针对某业务的特殊历史渲染器”在读历史时凭空拼装。这样模型实际看到的输入、客户端回放的记录、以及分支继承的前缀始终来自同一份事实。

## 一次模型请求是怎么准备出来的

当 `Loop` 准备发起一轮模型调用时，它会先向分支时间线写入 `context/prepare`，把本次请求的固定截点 `through` 与一个单次请求局部的投影交接给 `ContextPreparation`（`crates/dim-agent-capabilities/src/context_preparation.rs` 与 `context_policy.rs`）。

整个准备过程保持单飞（single-flight），同一时间一条分支最多只有一个进行中的准备任务，随时可以被 `stop/interrupt` 或新的 `steer` 取消。准备阶段先结合 `ContextUsage` 记录的累计用量和已知投影边界做数值预算估算：

- 如果估算没有超过上下文输入预算，且配置和投影边界没有变化，直接复用数值估算并发布 `model/call`，不需要对历史做第二次重读或全量投影，也不加载历次旧的模型请求正文。
- 如果估算超出了输入预算，或者用户手动触发压缩，`ContextPreparation` 就在准备流程内部调用绑定的压缩策略宿主 `CompactionStrategyHost`（在提交 `context/compaction-start` 与 `context/compaction` 之间执行），成功后直接采用已提交的压缩事实构建新的 `model/call`，不会向外发布虚假的“模型调用失败”事件。如果压缩后依然装不下本次请求，则直接按终态失败结束，绝不陷入死循环重试。

需要注意一个在 `event-driven-flows.md` §5 里写明的细节：如果在自动压缩正在跑的时候来了新的 `steer` 输入，`ContextPreparation` 会等这次压缩把结果落完盘，再替换请求，避免刚刚算好的摘要白白作废。此外，如果宿主没有绑定 `CompactionStrategyHost`，`ContextPreparation` 连 `dim-digest` 的提示词指引条目都不会注册，避免在没有压缩能力的配置里误导模型。

## 不可变摘要事实 vs 可丢弃恢复快照

![上下文压缩、SHA-256 恢复快照与回退重放](../../wiki/assets/diagrams/compaction-replay.svg)

上面这张图展示了 Dim 解决“长会话低内存”与“历史绝对可重放”矛盾的核心设计，对应 `timeline-storage.md` 的压缩检查点规范：

| 持久化对象 | 性质与表位置 | 存储内容 | 损坏或失败时的后果 |
| --- | --- | --- | --- |
| `context/compaction`（`Completed`） | 不可变历史事实，随原子批次写入 `timeline` | 来源 `through`、截断游标 `cursor`、摘要文本 `summary`、`context_baseline` 与诊断信息，**绝对不内嵌快照** | 若已提交的 `cursor` 无法投影到固定历史，准备明确报错失败，不跳过该事实，也不静默重做摘要 |
| `RecoverySnapshot` | 可丢弃的派生缓存，独立存于 `timeline_snapshots` | `{ sequence, event_id, interpreter, state }`，锚定已发布的 `Completed` 压缩事件 | 构建、编码或保存失败只打告警；读取时若缺失、损坏、校验和不符或越界，静默忽略并回退到完整事实重放，同时完整保留 `summary` |

默认的投影解释器标识为 `dim/context-projection/v4`（位于 `HistoryRenderer`），它把 `ProjectionCheckpoint` 编码成带 **SHA-256 摘要**的 `state`：

- `resume` 记录祖先链上的续读定位三元组 `(branch_id, sequence, event_id)`，指向压缩游标之后产生第一条保留历史条目的那条事件（且不晚于该次准备的 `through`）。
- `state` 记录在 `resume` 之前已经折叠好的全部投影状态：保留起点锚点 `retain_from`、当前生效的提示词条目与修订号、已投递的上下文基线、已提交但尚未采用的排队输入、最新 `context/configured`、以及尚未闭合的输出和工具调用。

读取路径的自我校验非常严格。`Timeline::checkpoint` 先按解释器版本挑出 `sequence <= through` 的最新快照，然后去查单个锚点事件（在热窗口里就直接取，不扫描区间），依次验证锚点 `event_id`、序号、状态是否为 `Completed`、游标是否匹配、SHA-256 摘要是否一致，再按 `resume.event_id` 确认续读位置落在当前固定祖先链与 `through` 范围内。v2 编解码器会自动绕过旧版没有摘要保护的 v1 缓存。只要任何一项检查不通过，系统立刻丢弃这个快照，从祖先链起点按 `HistoryRenderer::replay_context` 重新分页折叠所有带上下文标志的事实，依然应用已提交的 `context/compaction` 摘要，产出与快照完全等价的消息列表。

把键控状态块（第 5 章的 `branch:line`、`branch:roster`、`branch:parent` 以及 `Goal` 的 `goal:state`）放进这套机制里看，就会明白它们的配合有多严丝合缝：压缩抹掉了旧对话轮次，但每个活跃键（未设置 `retire: true`）的最新一块都会被投影状态保留并在压缩后重放，所以分支压缩完既不会忘记自己是谁，也不会忘记自己开过哪几条活跃子线。

## 内存驻留与冷读收敛

为了防止 2000 步的长会话把常驻内存撑大，Dim 把内存生命周期绑死在操作边界上：

- 热窗口只保存尚未发布和尚未分发完毕的事件，一旦 `Dispatcher` 分发完成就移出热窗口，进程内不留已分发事件的全量副本。
- `HistoryRenderer::rendered_stream` 采用分页流式投影，消费完一页就释放该页事件对象；一次读取（`read_segments` / `read_page`）内部共享一个解码内容缓存，上限 64 MiB，超限先清空，单次读取结束立刻释放。
- `ContextPreparation` 完成后只保留一个弱引用和数值证据，不跨调用缓存完整的 `ModelRequest`。
- 仓库甚至在 `xtask` 里专门提供了 `context-replay`、`restore-replay` 和 `context-breakdown` 三个测量子命令，用来直接量化 100 轮模拟会话或真实 SQLite 数据库在恢复、读取、投影和组装请求四个阶段各自申请了多少字节。

当上下文投影和压缩把对话主链路的资源账算清楚之后，另一块同样棘手的地盘就浮出了水面：脱离当前回合继续跑的后台工作、工具审批竞态，以及从 ADR 003 演进到 ADR 018 的整套权限边界。
