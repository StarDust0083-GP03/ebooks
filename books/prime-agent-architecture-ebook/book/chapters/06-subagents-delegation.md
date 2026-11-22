# 递归子 Agent 树、Spawn 账本与状态机工厂

让一个 Agent 把重活分派给子 Agent，在纸面上非常简单；真跑进大型代码库，立刻会冒出四个运维噩梦：子 Agent 自己又无限套娃，十分钟烧光整个团队的 API 预算；父会话强制退出或者被 `SIGKILL` 干掉，十几个子进程还在后台空转烧钱；重启一次守护进程，之前派出去的孩子全从列表里消失，账单和进度对不上号；或者父节点为了等孩子跑完，在循环里疯狂 `time.sleep()` 轮询，把自己的单元格和上下文全堵死。

Prime Agent 对多 Agent 编排的定位很克制：每一个子 Agent 都是一个由 Supervisor 托管的独立持久化会话，整棵会话树受深度上限、唯一命名、核家族通信边界和仅追加账本约束；如果任务需要更复杂的多阶段依赖与重试，还可以在开启开关后交给内建的状态机工厂 `rlm.factory` 驱动。

## 深度上限、持久子会话与 `RlmSubagentHost` 契约

递归控制边界定义在 `crates/pa-core/src/session_engine/rlm_host.rs`。核心默认常量是 `DEFAULT_RLM_MAX_DEPTH = 2`：根会话位于深度 `0`，它可以派生深度 `1` 的子 Agent，深度 `1` 的子 Agent 还可以再派生深度 `2` 的叶子节点，再往下就会触发深度上限错误，挡住失控递归。

`pa-core` 本身不直接起进程，而是定义了对象安全的 `RlmSubagentHost` trait，暴露 `spawn`、`create_session`、`list_subagents`、`delete_subagent`、`collect`、`rename` 和 `session_messages` 七个异步方法。不同运行模式各自注入实现：

- 守护进程 Worker（`pa-daemon`）提供 `SupervisorChildSessions`：每次 `rlm.spawn()` 经由与 Supervisor 的连接创建一个真正独立的受托管子 Worker 进程，并把 `rlmDepth`、`rlmMaxDepth`、`parentSessionPath`、`thinking` 一并写进创建配置，保证子 Worker 崩溃重启后依然记得自己的递归深度。
- 无头单次打印模式（`pa-cli`）提供进程内子会话宿主。
- 未接入子会话宿主的纯引擎实例则回退到 `NoRlmChildren`，所有子列表查询诚实返回空列表，派生请求显式报错，绝不默默假装成功。

从 Python 侧看（`prime-agent-runtime/src/rlm/__init__.py`），有两个容易混淆的创建接口：

1. `await rlm.spawn(prompt, *, name, model=None, thinking=None, cwd=None)`：派生深度加 1 的直接子 Agent，**立即返回** `RLMSpawnHandle`，不等待子任务结束。`name` 必须在同级兄弟中唯一且不超过 64 字符；`model` 和 `thinking` 默认继承父节点，若显式指定的思考等级与目标模型不兼容，在准入阶段直接拒绝；`cwd` 允许让子 Agent 在指定子目录启动并加载该目录的项目上下文。注意一个小历史痕迹：它的线协议请求名仍是 `rlm.run`，用于保持跨版本内核和宿主的兼容。
2. `await rlm.create_session(...)`：只能由守护进程托管的深度 `0` 根会话调用，创建一个平级的新深度 `0` 常驻会话（相当于兄弟会话，而不是子节点），等首条提示词发出后返回 `RLMCreateSessionHandle`。

## 非阻塞回收 `collect` 与节流进度条 `progress_note`

为了让父节点不必傻等或瞎轮询，Prime Agent 把状态观测拆成了两种低干扰机制。

一种是父主动拉取：`await rlm.collect(targets=None, *, timeout_ms=0)`。传入 `timeout_ms=0` 时，宿主立刻返回目标子 Agent 的类型化快照 `RLMChildResult`（包含 `status`、`settled`、`answer_preview`、`answer_text`、`error`、`duration_ms`、`tool_use_count` 等字段），绝不会去打断或转向父会话，也不会唤醒子会话。传入正数 `timeout_ms` 时，只阻塞当前这一个内核调用直到目标结算或超时，超时同样返回当前快照而不是抛异常。而且已完成子 Agent 的最终答案会一直保留到被显式删除，哪怕 Worker 刚吐完最终回答就崩溃，`.changes/spawn-replacement-answer-capture.md` 也修补了答案捕获路径，保证 `list_subagents()` 和父通知里依然能拿到完整结论。

另一种是子主动汇报：`await rlm.progress_note(message)`。子 Agent 在长程执行途中可以把简短进展推送到父节点的名册视图里，而不给父节点发聊天消息、也不要求父节点回复。为防刷屏，Rust 和 Python 两侧共同执行双约束：长度不得超过 `512` 个 UTF-16 码元（`RLM_PROGRESS_NOTE_MAX_LENGTH = 512`，Python 侧特意按 `utf-16-le` 长度计量，防止表情符号等辅助平面字符在 Python 算 1 个字、到宿主算 2 个码元引发误判），发送频率限制为每 10 秒最多一条（`RLM_PROGRESS_NOTE_MIN_INTERVAL_MS = 10_000`），被限流时返回 `RLMProgressNoteResult(accepted=False, retry_after_ms=...)` 而不是抛异常。

至于跨 Agent 通信权限，系统强制限定在**核家族（Nuclear Family）**以内：任何 Agent 只能观测和联系它的唯一父节点（深度 0 无父节点）、同父的兄弟节点（所有根会话互为兄弟）以及它的直接子节点，不允许越级直接给孙节点或陌生会话树的节点发消息。

## `rlm_ledger`：为什么家族拓扑不能靠扫会话文件反推

如果一个会话目录里攒了几百个已保存的历史会话和几千个子任务目录，每次打开 `prime-agent agents` 或重启后重建名册都去全盘扫一遍每个 JSONL 文件，IO 开销会非常难看，而且已休眠（Passivated）的子会话在进程表里根本不存在。

`crates/pa-daemon/src/rlm_ledger.rs` 专门维护了一本守护进程自有的**仅追加 Spawn 账本**，存放在 `<agent-dir>/rlm-ledger/` 下，每个会话目录对应一个 JSONL 文件。

每当 Supervisor 受理派生（`spawn`）、重命名（`rename`）或删除（`delete`）时，用单次小包 `O_APPEND` 写入一条记录；读取时先做文件 `stat` 校验再重放，同一个 `(childId, child)` 键遵循后写覆盖，单行损坏记录只记日志跳过，不拖垮整本账本。为防止异常账本撑爆内存，读取设了明确的失败闭合上限：文件最大 `32 MiB`（`RLM_LEDGER_MAX_BYTES`）、最多 `100,000` 条记录（`RLM_LEDGER_MAX_RECORDS`），超限直接报错。

账本里有两个很实用的设计细节：

1. **墓碑留账（Deleted Usage）**。删除一个子 Agent 时，墓碑记录的 `RlmLedgerDeleteReason` 区分 `user`、`parent-teardown`、`revoked` 和 `gc` 四种原因，并把该子会话截至删除前的 Token 消耗和按模型拆分的用量一并封进 `deleted_usage` 与 `deleted_usage_by_model`。这样即使子会话已经清理，整棵任务树的真实花费依然完整可核算。
2. **休眠子代可见性**。订阅实时名册或执行 `list --all` 时，`supervisor_roster.rs` 和 `rlm_roster.rs` 会以常驻与已保存会话为根，沿着账本向下遍历把所有已休眠的后代节点补回名册视图，同时绝不覆盖已有的活跃行。

## `rlm.factory`：把多阶段编排收进声明式状态机

当团队反复执行固定模式的多 Agent 流水线（例如先并行扫代码审查、再汇总打分、不通过就回退修复重审）时，每次靠主模型手写 `rlm.spawn()` 和 `if/else` 容易漏掉重试上限或并发控制。

`prime-agent-runtime/src/rlm/factory.py`（5,759 行）提供了一套可选开启的状态机工厂。它默认处于关闭状态，由全局 `settings.json` 里的 `factory.enabled` 控制（运行 `/factory on` 开启）；未开启时，除了 `rlm.factory.help()` 返回内嵌指南外，所有调用和写入都按失败闭合原则拒绝，返回宿主与内核字节级一致的提示语 `the factory is disabled; run /factory on to enable it`。

开启后，一份工厂规格既可以直接写成带守卫转换与有界重入（`max_entries`）的声明式状态机（`arguments["machine"]`），也可以写成更简洁的 DAG 语法糖（`arguments["dag"]`），由写入期校验器自动编译为规范状态机，把一个节点的所有前置依赖合成为单条汇合（Join）转换，避免只等完一个父节点就误触发下游。

为防配置失控，校验器在写入期就把硬上限卡死了：最多 `1,024` 个状态节点（`MAX_STATES`）、单次运行并发度限制在 `1` 到 `64`（默认 `8`）、`foreach` 扇出上限 `256`、重试上限 `10` 次、单次运行子 Agent 上限默认 `10,000`（硬帽 `1,000,000`），失败策略支持 `fail_fast`、`continue` 和 `escalate` 三种，并自带 `builder`、`pr-manager` 和 `review-sweep` 三个内置模板（位于 `prime-agent-runtime/src/rlm/machines/`）。

工厂执行器自己运行在内核的 `asyncio` 后台任务里，但底层派生、回收和取消全部复用既有的 `rlm.spawn`、`rlm.collect` 和 `rlm.delete_subagent`。需要看清它的持久性边界：工厂运行态本身只存在内核内存里，内核重启后运行器不自动续跑，但已经派生出去的子 Agent 归 Supervisor 所有，重启后照常存活并可通过 `rlm.list_subagents()` 看到。模型和 `/factory` 视图还可以随时调用 `graph()` 和带超时的 `watch(run_id, timeout)` 拿到结构与实时状态融合的快照，直接渲染成 ASCII 或 Mermaid 图。

当后台同时跑着好几个 `bash()`、几台子 Agent 甚至一条状态机工厂时，新的麻烦马上来了：这么多异步完成事件和跨 Agent 消息涌回来，如果不加节流直接往主会话上下文里灌，主会话很快就会被通知冲垮。下一章我们就来看 v0.10.5 新落地的核心基础设施：持久信箱 `rlm.inbox`。
