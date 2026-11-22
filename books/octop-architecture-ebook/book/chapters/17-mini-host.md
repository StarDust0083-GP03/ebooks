# 代码实战：用两百行标准库模型验证六条边界

前面十六章讲的边界分散在宿主、中间件、数据库和 ACP 适配器几十个文件里。光读源码容易只记住函数名，动手把最关键的几条不变量压成一个零依赖可运行程序，再用单元测试把每条边界的正反用例跑一遍，手感会立刻扎实起来。

配套代码放在本仓库的 `examples/mini_host.py`，测试放在 `examples/test_mini_host.py`。它只依赖 Python 3.10+ 标准库与内置 SQLite，不调真实 LLM，不启 WebSocket，也不发外部邮件，专门用来验证 Octop `v1.0.3b7` 的六条核心架构边界：

1. 共享专家不共享私人线程，显式 `contributed` 标记才允许同专家成员查看单条对话。
2. `session_key` 改绑新线程时，旧线程完好保留且仍按身份受控访问。
3. HITL 审批绑定预先落库的 `payload`，条件更新保证重复批准幂等、取消后不可再批准。
4. 托管专家的 ACP 会话模式解析自动忽略原生 `ask`/`craft`、保留 `plan`，并按无人值守标记决定 `ask`/`yolo` 审批门。
5. 连续相同工具调用熔断器只在 `(tool_name, sorted_args)` 连续超过上限时 `jump_to="end"`，换工具、换参数或纯文本回复立刻重置。
6. 孤儿 checkpoint 清理对 SQLite 仅在 `apply=True` 时删除，对共享 PostgreSQL 永远只统计不删除（且跨全部专家汇总活跃线程），外部独立数据库直接跳过扫描。

## 六张表先钉住数据边界

`MiniHost` 用六张极简 SQLite 表把状态关系立起来：`agents` 存专家归属、分享标志和记忆后端类型；`threads` 存对话归属与 `contributed` 共享标记；`sessions` 存入口键到当前线程的可变指针；`actions` 存待审批动作的固定载荷和三态流转（`pending` / `approved` / `cancelled`）；`outbox` 以 `action` 为主键存待投递记录；`checkpoints` 模拟 SQLite、共享 PostgreSQL 和外部 PostgreSQL 三类存储中的图状态字节数。

这里刻意把所有表放进同一个 SQLite 库，方便在一个本地事务里把状态机演清楚。真实 Octop 里这些表横跨控制面、checkpoint 和外部连接器，跨库并没有天然全局原子事务。

## 边界一与二：专家分享、私有线程与显式贡献

对照第 5 章的 `resolve_thread_id()` 与 `thread_visible_to()`，`MiniHost.assert_thread()` 严格执行先查专家访问、再查线程归属两步检查：

```python
def assert_thread(self, user: int, agent: str, thread: str) -> None:
    self._agent_access(user, agent)
    row = self.db.execute("SELECT * FROM threads WHERE id=?", (thread,)).fetchone()
    if row is None or row["agent"] != agent:
        raise PermissionError("thread inaccessible")
    if not (row["user"] == user or bool(row["contributed"])):
        raise PermissionError("thread inaccessible")
```

当 `research` 设为 `shared=True` 时，用户 9 可以调用 `open_thread(9, "research")` 开启自己的独立会话，但拿用户 7 的 `thread_id` 调用 `assert_thread()` 会直接抛 `PermissionError`，调用 `assert_agent_owner(9, "research")` 同样被拒。只有当用户 7 显式调用 `contribute_thread(7, "research", thread)`（且前提是该专家本身已共享，私有专家直接拒绝共享对话）之后，用户 9 才获得读取权限。

而调用 `open_thread(7, "research", fresh=True)` 只更新 `sessions` 表里 `research:dashboard:7:dm` 指向的新线程，原来的旧线程行纹丝不动，随时可以切回。

## 边界三：审批绑定载荷与本地幂等

`propose()` 先校验参数非空、把收件人和正文序列化为 `payload` 落库，返回 `action_id`。随后的 `approve()` 不允许调用方重新传入收件人或正文，并用一条带状态前提的条件更新完成状态流转：

```sql
UPDATE actions SET state='approved' WHERE id=? AND state='pending'
```

只有 `rowcount == 1` 的首次批准才在同一事务里把载荷写入 `outbox`。用户连点两次批准，第二次影响 0 行、返回 `False`，`outbox` 依然只有一条记录；一旦先执行了 `cancel()` 把状态置为 `cancelled`，后续任何 `approve()` 同样影响 0 行。持久化测试把 SQLite 落到临时目录关闭再重开，验证挂起的审批动作跨越重启后依然有效。

当然，本地 `outbox` 只有一行，解决的是审批层面的重复触发，不等于外部发邮件具备跨网络精确一次语义。如果后台投递器把邮件发出去之后、写回本地已发送标记之前崩溃，重启重投依然会发第二封，那一层的去重必须靠对端供应商的幂等键或状态反查来兜底。

## 边界四与五：托管会话模式与连续调用熔断

这两个纯逻辑组件直接复刻了 `v1.0.3b7` 的核心算法：

- `resolve_managed_session_mode()` 与 `approval_for_session()` 对照 `src/octop/infra/agents/acp/session_modes.py`：传入 Octop 原生的 `ask` 或 `craft` 会被忽略并继续往下取固定模式；传入 `plan` 会保留；当模式命中 `bypassPermissions`、`full-access`、`yolo` 等无人值守标记时返回 `yolo`，而当 Runner（如 `claude_code`）发布过无人值守模式但当前处于 `acceptEdits` 时，即使聊天曾设过 `yolo` 也会收紧回 `ask`；像 `opencode` 这样未发布无人值守模式的 Runner，则尊重聊天本身的 `yolo` 设置。
- `ConsecutiveToolCallGuard` 对照 `src/octop/infra/agents/middleware/consecutive_tool_call.py`：先把参数 dict 按 `sort_keys=True` 序列化，确保 `{"q": "TODO", "dir": "src"}` 和 `{"dir": "src", "q": "TODO"}` 算出同一个签名。连调次数超过 `limit` 立刻返回 `blocked=True, jump_to="end"`；只要参数变了、换了工具，或者调用 `observe_text_reply()` 模拟纯文本回复，连击计数马上重置。

## 边界六：SQLite 可删、PostgreSQL 只统计的孤儿扫描

`scan_orphan_checkpoints(agent_id=..., apply=...)` 精确建模了 `v1.0.3b7` PR #2960 修复后的 `gc_orphan_checkpoints()`：

- 面对 `sqlite` 后端的专家，拿该专家的活跃线程集合做差集；`apply=False` 仅汇报 `found_sqlite`，`apply=True` 才执行删除。
- 面对 `postgres` 后端的专家，先汇总**全库所有专家**的 `all_live_threads`（避免单查 `pg-a` 时把同表里 `pg-b` 的活跃线程错算成孤儿），而且就算传入 `apply=True`，也只往 `skipped_postgres` 里追加 `("pg-a", "postgres_report_only", count, total_bytes)`，一行都不删。
- 面对 `external_postgres` 后端，直接登记 `("pg-ext", "external_database", None, 0)` 跳过扫描。

## 运行与实验

在电子书仓库根目录执行下面两条命令，即可运行演示和全部 18 个单元测试：

```bash
python3 examples/mini_host.py
python3 -m unittest discover -s examples -v
```

如果拿这份模型做练手，可以试着在临时副本里故意破坏三条规则观察测试如何报警：把 `approve()` 里的 `AND state='pending'` 删掉，看重复批准如何把 `outbox` 撞出主键冲突；把 `scan_orphan_checkpoints()` 里的 `all_live_threads` 改成只查当前专家的线程，看共享 PostgreSQL 场景如何把另一位专家的正常对话错判成孤儿；把 `tool_call_signature()` 里的 `sort_keys=True` 去掉，看键序不同的重复参数如何绕过熔断器。

## 源码入口

- [`src/octop/api/routers/chat/turn.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/routers/chat/turn.py)
- [`src/octop/infra/gateway/threads.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/gateway/threads.py)
- [`src/octop/infra/agents/acp/session_modes.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/acp/session_modes.py)
- [`src/octop/infra/agents/middleware/consecutive_tool_call.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/middleware/consecutive_tool_call.py)
- [`src/octop/infra/agents/memory/thread_cleanup.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/memory/thread_cleanup.py)
- [`src/octop/infra/agents/teams/jobs.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/teams/jobs.py)
