# 零依赖 Python 教学模型与 18 个单元测试

光读 Rust 工作区的 28 个 crate 和几十万字设计文档，脑子里很容易记住一堆名词，却对边界条件缺乏手感：到底为什么 `Dispatcher::consume` 要先暂存实时事件再跑重建；为什么默认分支切回 `main` 后计数会瞬间超额却不该关闭已有分支；为什么破坏了快照的 SHA-256 摘要以后，上下文投影照样能完整保留压缩摘要。

本书在 `examples/mini_dim.py` 里用纯 Python 3.10+ 标准库写了一个不到四百行的教学模型，配套 `examples/test_mini_dim.py` 共 18 个单元测试，把 Dim 最核心的五组机制剥离网络与 SQLite 细节，缩成一眼能看穿的可运行代码。

## 模型结构与上游源码的精确映射

| `examples/mini_dim.py` 模块 | 对应上游 Rust 位置 | 验证的核心不变量 |
| --- | --- | --- |
| `Timeline`、`EventDraft`、`RESERVED_DOMAINS` | `crates/dim-agent-types/src/events.rs`、`crates/dim-agent-runtime/src/core/timeline.rs` | 13 个核心保留域拦截外部写入；`before` 链同步变换并留 `TraceStep`，任意钩子抛错回退原始草稿；`append_batch` 登记失败整批中止；`committed()` 与 `published()` 双水位隔离 |
| `Dispatcher` | `crates/dim-agent-runtime/src/core/dispatcher.rs` | 按发布序号严格派发；`consume(name, keys, rebuild, handler)` 先开启暂存闸门再执行重建，随后只派发序号严格大于 `cutoff` 的实时事件 |
| `TimelineTree`、`BranchNode` | `crates/dim-agent-capabilities/src/timeline_tree.rs` | 仅 `ROOT_BRANCH_ID`（`main`）与当前默认分支有工具开线权；`counted_tool_lines` 排除默认分支及其祖先链；预留即占槽、失败释放槽并写 `branch/create-failed`；禁止结束保护链并级联关闭子孙；仅父子亲缘可发消息与按游标读历史；重启读 `main` 最新 `branch/default` 或 `session/forked` 恢复 |
| `ApprovalCapability` | `crates/dim-agent-capabilities/src/permission.rs`（`Approval`） | 双端并发应答时首条有效 `approval/respond` 胜出；`stop/interrupt` 写 `approval/cancelled` 并阻断迟到同意；重启重建只恢复已决表，绝不重放已执行过的工具副作用 |
| `RecoverySnapshot`、`project_context` | `crates/dim-agent-capabilities/src/context_preparation.rs`、`history.rs` | 校验解释器版本、锚点与 SHA-256 摘要后增量续读；摘要篡改或缺失时自动回退全量重放，同时完整保留 `context/compaction` 摘要 |

## 核心代码片段拆解

先看 `Dispatcher.consume` 怎么堵死“重建与实时事件重叠”的竞态窗口：

```python
def consume(
    self,
    name: str,
    keys: tuple[str, ...],
    rebuild: Callable[[Timeline], int],
    handler: Callable[[AgentEvent], None],
) -> int:
    held: list[AgentEvent] = []
    holding = True

    def gate(ev: AgentEvent) -> None:
        if holding:
            held.append(ev)
        else:
            handler(ev)

    self.subscribe(name, keys, gate)
    cutoff = rebuild(self.timeline)
    holding = False
    for ev in held:
        if ev.sequence > cutoff:
            handler(ev)
    return cutoff
```

这段 20 行代码就是 `crates/dim-agent-runtime/src/core/dispatcher.rs` 里 `Held` 结构的忠实微缩：哪怕在 `rebuild` 正在读历史的过程中，第 3 号新事件被提交并发布，它也会稳稳落进 `held`；等 `rebuild` 返回 `cutoff = 2` 并撤掉闸门，只有序号大于 2 的第 3 号事件被放行给实时 `handler`，1 号和 2 号绝不会二次投递。

再看 `TimelineTree` 里最容易写错的“受保护路径配额”算法，它直接对应 `crates/dim-agent-capabilities/src/timeline_tree.rs` 的 `counted_tool_lines` 和 `protects`：

```python
def protected_path(self) -> set[str]:
    protected: set[str] = set()
    cur: str | None = self._default_branch
    while cur is not None:
        protected.add(cur)
        node = self.nodes.get(cur)
        cur = node.parent if node else None
    return protected

def counted_tool_lines(self) -> int:
    protected = self.protected_path()
    return sum(
        1
        for bid, node in self.nodes.items()
        if node.source == "tool" and not node.ended and bid not in protected
    )
```

每次切换默认分支，不需要在节点上维护容易漂移的加减计数器，而是直接对全树活跃工具线减掉当前保护路径做集合差。这样无论子树有多深，从 `main → A → B` 切回 `main` 时，A、B 以及 B 新开的子线 C 都会瞬间重新计额，既不会漏算，也不会强关任何已有分支。

最后看 `ApprovalCapability._rebuild` 怎么体现“副作用不重放”：

```python
def _rebuild(self, timeline: Timeline) -> int:
    cutoff = timeline.published()
    for ev in timeline.query(
        through=cutoff, keys=("approval/*", "stop/interrupt")
    ):
        self._fold(ev, execute_side_effects=False)
    return cutoff
```

同一套折叠函数 `_fold` 通过一个布尔开关区分重建和实时两条路径：重建时把历史上的 `approval/request`、`approval/respond`、`approval/cancelled` 折叠成完全等价的内存状态，却绝不触发 `on_allow` 回调，也不向 `executed_side_effects` 追加记录。

## 18 个单元测试的覆盖矩阵

在仓库根目录执行 `python3 -m unittest discover -s examples -v`，全部 18 个用例在数毫秒内跑完：

| 用例编号 | 测试名称 | 验证的关键边界 |
| --- | --- | --- |
| 01 | `test_01_sequence_is_monotonic_and_batch_end_marks_boundary` | 批次序号单调递增，仅末事件 `batch_end=True` |
| 02 | `test_02_reserved_domains_reject_extension_appends` | 扩展写入 13 保留域被拒，自定义域放行 |
| 03 | `test_03_before_hook_transforms_draft_and_records_trace` | `before` 同步变形草稿并记录 `TraceStep` |
| 04 | `test_04_before_hook_failure_commits_original_draft` | `before` 抛错整链回退并提交原草稿 |
| 05 | `test_05_register_callback_failure_aborts_entire_batch` | 提交锁内登记回调失败时整批中止、水位不动 |
| 06 | `test_06_committed_and_published_watermarks_stay_distinct` | 未发布事件仅内部可见，发布后才触发 Dispatcher |
| 07 | `test_07_dispatcher_consume_holds_live_facts_and_delivers_above_cutoff` | 重建期间到达的实时事件暂存并在截点后不重不漏交付 |
| 08 | `test_08_only_root_and_default_branch_can_open_tool_lines` | 非默认工具线开线被拒，晋升默认后与 `main` 双双可开 |
| 09 | `test_09_protected_path_excluded_from_cap_and_switch_back_counts_all` | 保护链免计额；切回 `main` 超额不关旧线，仅拒新开 |
| 10 | `test_10_failed_build_releases_cap_slot_and_records_create_failed` | 构建失败释放预留配额并写 `branch/create-failed` |
| 11 | `test_11_user_and_plugin_lines_never_spend_tool_cap_and_plugin_cannot_be_default` | 用户线与插件线不占工具配额；插件线不可选为默认分支 |
| 12 | `test_12_ending_root_default_or_ancestor_is_refused_and_cascade_ends_children` | 保护链拒绝结束；切离后结束父线先级联结束子线 |
| 13 | `test_13_kinship_messaging_and_read_cursor_enforcement` | 兄弟互发互读返回 `branch_not_kin`；子读父自动抬高至切点并续读游标 |
| 14 | `test_14_default_branch_restores_from_root_and_resets_after_session_forked` | 从 `main` 恢复默认分支，遇到 `session/forked` 重置回 `main` |
| 15 | `test_15_approval_first_decision_wins_under_concurrent_clients` | 双客户端并发拒绝与同意，先落盘序号者胜出 |
| 16 | `test_16_stop_interrupt_cancels_pending_approval_and_prevents_late_allow` | `stop/interrupt` 写 `approval/cancelled` 并拦截迟到同意 |
| 17 | `test_17_restart_rebuild_reconstructs_state_without_replaying_side_effects` | 同存储重启构建第二代 `ApprovalCapability`，状态等价且零重放副作用 |
| 18 | `test_18_compaction_snapshot_accelerates_projection_and_falls_back_on_corruption` | SHA-256 正常快照走增量续读，篡改摘要自动回退全量重放并保留摘要 |

需要强调一次定位边界：`examples/mini_dim.py` 是单进程同步教学模型，省去了 SQLite 异步写入轮、真实 `rutis` 跨进程通道、文件系统权限探针和真实模型网络流。它的价值在于用极低阅读成本，让读者亲手改一行条件就能观察到对应不变量立刻报警，再回头翻 Rust 源码时就能一眼认出每把锁和每个状态字段为什么长在那个位置。
