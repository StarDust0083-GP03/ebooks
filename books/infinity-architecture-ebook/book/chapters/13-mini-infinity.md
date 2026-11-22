# 零依赖 Python 教学实现：复现切片、延迟队列与堆叠沙箱

看懂一份五万多行 Rust 运行时的最好办法，是把它的核心不变量抽离出来，用几百行没有外部依赖的代码重新搭一遍，再写一组能把每个边界条件都打疼的单元测试。

仓库的 `examples/mini_infinity.py`（零第三方依赖，仅用 Python 3.10+ 标准库）和 `examples/test_mini_infinity.py`（19 个单元测试）精确复刻了前面章节拆解过的五组核心机制，方便在没有 Rust 编译器、没有 AWS 账号也没有真实大模型 Key 的环境下单步调试。

## 教学模型与上游 Rust 源码的逐项映射

为了防止教学代码跑偏成脱离源码的伪代码，每个类和方法都严格对应上游源码里的真实边界：

| `examples/mini_infinity.py` 组件 | 对应的上游 Rust 模块 | 复现的核心不变量 |
| --- | --- | --- |
| `ThreadRef` | `crates/infinity-protocol/src/lib.rs` | `"{remote}/{id}"` 复合解析、非法斜杠拒绝、`prefixed`/`strip`、本地优先于远程的 `Ord` 排序 |
| `ConversationStore` / `StateStore` | `crates/infinity-agent-core/src/traits.rs`、`stores.rs` | 祖先链回溯、`Fresh` 上下文截断传递、按 `up_to_order` 位置索引加载压缩摘要 |
| `HistoryManager` | `crates/infinity-agent-core/src/event_processor.rs` | `unvalidated_items` -> `pending_items` -> `sync()` 三阶段顺序断言、`turn_buffer` 文本合并与尾推理裁剪、`match_tool_result` 逆向尾扫去重、`safe_spawn_point`、`recover_from_overflow` 与穿透压缩的 `find_tool_call` |
| `MiniInfinityRuntime` / `DeferQueue` | `crates/infinity-agent-core/src/system/{thread,defer,local/router}.rs` | 先 `sync()` 落盘后派发异步工具、`spawn_thread` 同步回环加 `child_of` 栈校验、订阅事件自动拆子线程 vs `associative` 内联加 `final` 清理、活跃工具延迟队列 vs 被动 `sleep` 自由打断、未知/已停线程准入拒绝、`ErrorClass` 驱动重试 |
| `StackedSandboxManager` | `crates/sandbox-core/src/server.rs`、`sandbox-local/src/backend.rs` | 按 `base_thread_id` 堆叠子工作区、`squash_sandbox` 合并回父书签而不触碰宿主文件、沿祖先链检查 `write-orig` 写权限授权 |

## 核心代码走读

挑三段最能体现 Infinity 架构精髓的逻辑来看。

### 1. 三阶段历史与溢出止损

在 `HistoryManager` 里，`append_known_safe` 直接复现了 Rust 版的顺序性断言：在模型尚未验证当前新输入之前，绝不允许提交模型输出；而 `sync()` 只刷 `pending_items`，且只把天然不幂等的 `user` 与 `subscription_event` 写入持久化去重集：

```python
def append_known_safe(self, message: InfinityMessage, message_id: str) -> None:
    assert not self.turn_buffer, "bug: append_known_safe with non-empty turn_buffer"
    assert not self.unvalidated_items, (
        "bug: committing model output while unvalidated inputs exist"
    )
    self.history.append(message)
    self.pending_items.append((message, message_id))

def recover_from_overflow(self) -> tuple[Literal["retry", "dropped"], int]:
    if len(self.unvalidated_items) == 1 and self.replace_unvalidated_tool_results(
        TOOL_RESULT_TOO_LARGE_PLACEHOLDER
    ):
        return ("retry", 0)
    self.replace_unvalidated_tool_results(TOOL_CALL_INTERRUPTED_TEXT)
    dropped = self.drop_unvalidated_user_inputs()
    return ("dropped", dropped)
```

注意 `replace_unvalidated_tool_results` 里对已经等于占位符的正文跳过不计的细节：这样如果单条超大工具结果换完占位符再试一次仍然触发溢出，第二次调用会自动落入 `dropped` 分支，不会无限重试。

### 2. 落盘屏障与同步回环

在 `MiniInfinityRuntime.process_one` 里，同步工具（`spawn_thread`）在 `while True` 补全循环内部直接执行并把结果喂回同一轮切片，而异步工具则跳出循环、**先执行 `hm.sync()`**，再登记派发记录：

```python
# Commit barrier BEFORE dispatching async tool (durability invariant)
hm.sync()
persisted_len_before_dispatch = len(self.conv.threads[tid].messages)

if pending_async_call is not None:
    self.dispatched_async_calls.append(
        (tid, pending_async_call.tool_call_id, pending_async_call.tool_name,
         pending_async_call.tool_args, persisted_len_before_dispatch)
    )
```

测试用例 `test_commit_barrier_persists_tool_call_before_async_dispatch` 会断言派发发生那一刻 `persisted_len_before_dispatch == 2`（用户输入和工具调用均已持久化），直接把落盘屏障做成了可验证断言。

### 3. 堆叠沙箱与祖先链写授权

`StackedSandboxManager` 用纯内存字典模拟文件系统快照，还原了两个关键约束：第一，父线程和两个并行子线程各自修改互不覆盖，子线程完成后通过 `squash_sandbox` 把差量合回父书签，宿主 `host_files` 全程保持原样；第二，`check_write_allowed` 检查目标路径时，落在自己沙箱内直接放行，落在宿主仓库根目录下则沿着 `thread_chain` 逐级向上检查是否已授予 `write-orig`，未授权时返回 `needs_user_choice:write-orig` 触发前端确认：

```python
def check_write_allowed(self, thread_chain: list[str], target_path: str) -> tuple[bool, str]:
    norm = str(PurePosixPath(target_path))
    leaf = self.workspaces[thread_chain[-1]]
    if norm == leaf.sandbox_root or norm.startswith(leaf.sandbox_root + "/"):
        return True, "inside_sandbox"
    if norm == self.repo_root or norm.startswith(self.repo_root + "/"):
        if any(
            tid in self.workspaces and self.workspaces[tid].write_orig_granted
            for tid in thread_chain
        ):
            return True, "write_orig_granted"
        return False, "needs_user_choice:write-orig"
    ...
```

## 运行全部 19 个单元测试

在仓库根目录执行标准库 `unittest` 发现命令即可完成全部核验：

```bash
python3 -m unittest discover -s examples -v
```

19 条用例覆盖了五大测试类：

- `TestThreadRef`（2 项）：复合解析、前缀增删、本地优先排序与非法输入拦截。
- `TestHistoryManagerThreePhase`（6 项）：未验证项跳过落盘与顺序断言、轮次缓冲合并与尾推理裁剪、无持久 ID 的工具结果尾扫去重、单工具结果溢出重试一次防死循环、含用户输入溢出丢弃并遗忘去重 ID、`safe_spawn_point` 截断规则。
- `TestInheritanceAndCompaction`（3 项）：位置索引压缩保护早先派生的子线程切片、`Fresh` 边界向孙线程传递、`find_tool_call` 穿透压缩视图。
- `TestRuntimeSliceDeferralAndSubscriptions`（6 项）：落盘屏障先于异步派发、`spawn_thread` 同步回环与 `child_of` 栈拦截、活跃工具延迟队列 vs 被动 `sleep` 立即打断、订阅自动子线程 vs `associative` 内联与 `final` 清理、未知/已停线程准入拦截、`ErrorClass` 驱动瞬时/限流重试与致命报错不重试。
- `TestStackedSandboxes`（2 项）：双并行子沙箱压缩汇流且宿主不动、沙箱内写放行与祖先链 `write-orig` 授权传递。

这份教学模型有意省略了网络传输、真实 `jj`/`git` 命令行调用以及 Tokio 异步调度器，目的是把状态机的核心不变量剥离到肉眼一眼能看穿的厚度。如果你想对照核查书里提到的任何一个上游文件、类型或指标，可以翻到后面的附录索引。
