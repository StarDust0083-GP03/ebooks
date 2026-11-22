# 会话生命周期、流式全文搜索与 ACP 外部协议

把编程 Agent 当作日常基础设施而不是一次性玩具时，有四个很现实的运维问题躲不开：聊到一半想回退到三轮前另开分支怎么办；上个月在某个会话里改过一个重试参数，攒了几百个几百 MB 的会话文件后怎么把那段对话搜出来；发布新版本执行 `prime-agent update` 时，后台正跑着五个活跃会话和定时心跳，怎么平滑交接而不把现场全杀光；以及除了官方 TUI，外部编辑器和自动化宿主怎么用标准协议接入并中途插话。

这四件事都落在 `pa-daemon` 和 `pa-core/src/session/` 的交界处。

## 仅追加 JSONL 会话树与单存储恢复

Prime Agent 的所有会话持久化都采用统一的仅追加（Append-Only）JSONL 格式，默认存放在 `~/.prime/agent/sessions/` 下（子 Agent 存放在父会话制品目录树中，并由 RLM Spawn 账本索引）。

打开 `crates/pa-core/src/session/manager/` 和 `crates/pa-daemon/src/session_store/`，每条记录都带唯一 `id` 和 `parentId`，本质上是一棵写进顺序日志的树。这种结构的直接好处是支持非破坏性分支导航（`branch_navigation.rs`）：用户可以退回历史上任意一条消息创建新分支，旧分支依然完整留在文件里，还可以生成 `branchSummary` 供新分支参考。

除了普通消息，所有影响运行态的关键变迁也以结构化记录追加进同一份会话文件：

- 会话创建前缀固定写入 `model_change`、`thinking_level_change` 和 `service_tier_change`；
- 每次目标状态更新都会同步落盘一条 `thread_goal_state` 自定义行（`goal_state_persist.rs`）；
- 上下文压缩写入 `compactionSummary` 条目，读取活跃窗口时由会话存储自动折叠；
- 排队中的待发指令和未结算文本队列则不写进主会话历史，而是写进独立的 Worker 恢复日志（`journal.rs`），防止把尚未执行的队列草稿污染进正式对话树。

当一个 Worker 进程因异常崩溃被 Supervisor 带 `--resume <sessionFile>` 重新拉起时，新 Worker 从磁盘重建完整分支历史与最新 `thread_goal_state`（同时把已发布基线静默对齐，避免重启后重复刷屏播报目标状态），再重放恢复日志里的文本队列（注意：排队提示里的 base64 图片附件不随恢复日志跨崩溃保留），让后续压缩与推理完全接续崩前的真实历史。

## 零内存暴涨的流式全文搜索

随着使用时间拉长，单个长程会话的 JSONL 文件可能膨胀到几百 MB 甚至上 GB。在 `prime-agent agents` 视图里按 `Ctrl+F` 切换按消息正文搜索历史会话时，如果把所有文件全量 `serde_json::from_str` 读进 Supervisor 内存，守护进程瞬间就会 OOM。

`crates/pa-daemon/src/session_content_search.rs`（仅 123 行）用非常干净的三级过滤流式扫描解决了这个问题：

1. **逐行流式读取**：用 `BufReader::lines()` 每次只在内存里保留一行文本，先解析首行会话头拿到持久 `id`。
2. **零解析原始行预筛**：先判断行里是否包含 `"type":"message"`；如果查询词是纯 ASCII 且不含引号、反斜杠、空格和控制字符（因为 JSON 转义或跨块拼接可能改变字节序列），直接对原始行做大小写不敏感子串检查，跳过绝大多数无关行，连 JSON 反序列化都省了。
3. **精准角色与正文匹配**：只有通过预筛的行才解析为 `serde_json::Value`，且严格只匹配 `user` 和 `assistant` 角色的正文文本，刻意跳过 `toolResult` 的海量工具日志，减少噪声误命中。

也要注意它当前的明确范围边界（见 `.changes/session-content-search.md`）：扫描只覆盖顶层会话目录里的 `.jsonl` 文件，子 Agent 存放在父制品树里的对话记录目前尚未纳入全文检索。

## `update_prepare`：五态热更新事务与跨版本接管

给一个正在后台托管多会话的守护进程做原地升级，最怕两种事故：一是旧守护进程还在受理新创建请求、新版本已经启动抢占 Socket；二是旧 Worker 被强杀，还没来得及把 Python 内核快照和队列日志刷盘。

`crates/pa-daemon/src/update_prepare.rs`、`update_roster.rs`、`update_stop.rs` 和 `update_restore.rs` 为此实现了一套完整的五态热更新事务状态机：

```text
Serving -> Draining -> Fenced -> Snapshotted -> Prepared -> Stopping
                \____________________________________________/
                     任一阶段失败或超时 -> Aborted -> Serving
```

整套交接被拆成准备、提交、恢复三个严格有界阶段：

**准备阶段（`prepare_update_restart`）**，Supervisor 进入 `Draining`，立刻立起变更闸门：所有写操作命令被拒绝并明确提示 `Daemon is preparing an update restart`，但只读查询、客户端挂载以及中止类排空命令继续放行。排空在途变更后进入 `Fenced`，向所有常驻 Worker 发起 `update_snapshot` 让它们完成状态落盘，随后汇总所有会话、Worker、子 Agent 账本和只读的 `scheduled-jobs.json` 心跳清单，原子写入 `prepared/<id>/{roster,marker}.json` 并返回 `Prepared`。整个准备阶段受 90 秒硬截止时间保护，磁盘上的准备标记自带 45 秒自过期看门狗，且支持同一 `updateId` 幂等重入；超时或异常时自动回退到 `Serving` 并清理准备目录。

**提交阶段（`commit_update_restart`）**，Supervisor 向每个 Worker 发送带确认的 `shutdown` 充当刷盘屏障，设有 30 秒加 30 秒的分段预算。只要有任意 Worker 拒绝退出，Supervisor 绝不强杀，而是立刻中止本次提交、把已经停掉的 Worker 重新拉起并恢复 `Serving`；只有全部 Worker 平滑停稳，旧 Supervisor 才退出，把描述符留给新版本接手。

**恢复阶段（`update_restore.rs`）**，新 Supervisor 启动时先读取环境变量 `PRIME_AGENT_UPDATE_ROSTER`，再无条件清扫本次更新的临时目录。随后按自底向上的顺序逐条恢复或认领会话（单条恢复失败只记入报告，绝不拖死整个守护进程启动），同时扫描 `scheduled-jobs.json` 重新挂起定时与心跳任务。在恢复扫描彻底完成前，客户端对尚未恢复会话的挂载请求会短暂排队等待，而不会误报会话不存在。更周全的是，它还保留了对历史 TypeScript 版更新清单的兼容逻辑：遇到旧格式清单时，先重建父会话再重建子会话，重映射活跃父 ID 并显式还原上下文与动作队列。

## ACP 外部协议与 `_session/steering` 扩展

除了官方终端界面，Prime Agent 还通过 `prime-agent --mode acp` 对外提供标准 **Agent Client Protocol（ACP）** 的 stdio JSON-RPC 服务（`crates/pa-daemon/src/acp/`），且底层同样挂载到守护进程会话上，而不是单独另起一套孤立引擎。

除了标准的 `initialize`、`session/new`、`session/prompt`、`session/close`、`session/cancel` 与带命名空间 `_meta` 关联字段的 `session/update` 通知，v0.10.5 在 ACP 层做了两项针对真实宿主集成的打磨：

**`_session/steering` 转向扩展（`.changes/sebastian-acp-session-steering.md`）**，服务端在 `initialize` 响应里通过 `_meta.steering.supported` 声明支持该扩展。外部客户端可以在一个 `session/prompt` 正在流式运行的途中调用 `_session/steering`（支持文本块和按会话去重的可选 `messageId`）。底层会向 Worker 发送带 `requireBusy` 标志的 `steer` 命令：在入队锁内原子检查当前是否真有活跃回合。如果在运行中，立刻排入 Steering 队列并返回 `injected`，由当前回合在空闲屏障前消化完毕；如果当前根本没有运行中的回合，直接回答 `promptRequired`（或 `noRunningTurn`），**绝不会偷偷启动一个新回合，也绝不会解除队列暂停状态**。

**克制的错误可重试契约（`crates/pa-daemon/src/acp/mod.rs` 第 39 行）**，当一个 `session/prompt` 最终失败时，服务端返回带 `kind`、`category` 和 `retryable` 的结构化错误数据。源码注释点透了一个常被忽视的陷阱：此时脚手架内部的自动重试已经耗尽，而外部宿主再次发起 `session/prompt` 是一次全新的提示词请求，**不是幂等重放**，因为用户消息已经落盘、有副作用的工具可能已经跑过。因此只有网络断流或超时的 `model_transport` 类别会标为 `retryable: true`，而 `429` 限流、`402` 余额不足、`401` 鉴权失败和上下文溢出（`context_limit`）一律标为 `retryable: false`，防止外部编排器盲目重放造成重复扣费或重复改文件。

到此为止，会话如何运行、如何协作、如何落盘与对外服务已经全部清楚。下一章进入 Prime Agent 最具特色的“自我改进”核心：分层系统提示词与 Continual Harness 怎么做到既越用越聪明，又不把大模型的 Prompt Caching 打废。
