# Protocol v3 内核：`python_repl` 如何把状态、输出和中断管稳

自己动手写过基于子进程的 Python REPL 封装的人，多半都被同一堆脏活折磨过：在子进程里用标准输入输出走 JSON 协议，模型代码一个 `print()` 或 C 扩展往 `fd 1` 写一行调试信息，JSON 解析直接崩盘；单元格里起了个后台 `asyncio` 任务，结果它的日志张冠李戴算到下一个单元格头上；模型写了 `while True: pass` 或者调了阻塞 C 函数，Ctrl+C 发过去根本停不下来；更别提程序重启后怎么把内存里的变量尽量救回来，还不把刚打开的写模式文件重新截断成零字节。

`prime-agent-runtime/src/rlm/repl.py`（1,789 行）与对应的协议规范 `repl.md`，加上 Rust 侧的 `crates/pa-core/src/kernel/`，把这些坑一个不漏地拆开了。整个内核不依赖 Jupyter 或 ZeroMQ，而是直接启动 `python -m rlm.repl`，在单一 `asyncio` 事件循环和持久化 `__main__` 命名空间里运行，使用换行分隔的 UTF-8 JSON 协议（当前协议版本 `3`，启动后首帧发出 `{"event":"ready","protocol":3,"python":"..."}`）。

![Protocol v3 Python REPL 双通道与状态循环](../../wiki/assets/diagrams/rlm-repl-cycle.svg)

这张图展示的是宿主 `KernelManager` 与 CPython 进程之间的四股数据流。让这套协议在长程任务里跑稳的关键在于五个细节处理：文件描述符双通道隔离、顶层异步编译、带外控制帧、3:1 头尾截断缓冲，以及 `dill` v2 原子快照。

## 双通道隔离与排空围栏

内核进程启动后做的第一件事就是重构标准文件描述符：

**私有协议通道**，先用 `os.dup(1)` 复制出原始标准输出句柄存进 `_protocol_fd`，同时保存原始 `_host_stderr_fd` 用来在协议写出失败时上报异常。此后所有协议事件都经由 `_send()` 持 `_write_lock` 一次性写完，确保多线程并发发送事件时绝不发生半帧交错。

**屏蔽用户输入**，请求读取线程接管 `fd 0` 后，立刻把进程级 `fd 0` 重定向到 `/dev/null`。这样就算模型代码不小心调了 `input()`，也只会立刻拿到 EOF，绝不会去偷吃宿主发来的 JSON 请求帧。

**Python 层输出按单元格精确归属**，运行时用 `_TaggedWriter` 替换 `sys.stdout` 和 `sys.stderr`。每个正在执行的单元格都会设置 `contextvars.ContextVar` 变量 `_current_cell`。Python 层的 `print()` 写入会被拦截、打上当前单元格 ID，并按每帧最多 `64 Ki` 字符（`_STREAM_FRAME_TEXT_CAP`）直接推送到协议流。因为 `asyncio` 任务创建时会自动复制上下文，一个单元格派生的后台协程，即使在单元格结束后才打印，依然带着原单元格的上下文；而用户新开的普通线程因为没有继承该上下文，归属 ID 为 `null`。

**原始文件描述符独立泵出**，如果代码绕过 `sys.stdout`，直接调 `os.write(1, ...)`、写入 `sys.stdout.buffer`、或者由 C 扩展和子进程往 `fd 1`/`fd 2` 输出怎么办？运行时在启动时把真正的 `fd 1` 和 `fd 2` 重定向进操作系统管道，由两个后台守护线程 `_Pump` 每次最多读 64 KiB 字节，增量 UTF-8 解码后统一发成 `id: null` 的流事件。源码注释写得很清楚：原始 fd 字节没有可证明的所有者，宁可标成未归属的后台输出，也绝不胡乱算到某个单元格头上。

这就带来一个同步问题：单元格同步代码刚通过 `os.write(1, ...)` 写完几行字，怎么保证在发出 `done` 事件之前，`_Pump` 线程已经把管道里的字节全搬完了？`repl.py` 第 227 行的 `_Pump.drain()` 写得非常干净：通过一个私有复制的管道写端 `_token_fd`（防止用户代码关闭或复用 `fd 1/2` 劫持标记），往管道里注入一段带随机 UUID 的二进制围栏令牌 `b"\xff<drain:<uuid>>\xff"`，然后阻塞等待泵线程在字节流中读到该令牌并触发事件。等两根管道都过了围栏，再发送 `done` 事件，既不漏字节，又不用靠 `sleep` 赌运气。

## 顶层 `await`、带外通道与严格载荷上限

在 `python_repl` 里，模型可以直接写 `msgs = await rlm.inbox.read()` 而不用自己套 `asyncio.run()`。运行时把每个单元格源码用 `PyCF_ALLOW_TOP_LEVEL_AWAIT` 标志编译成协程任务，放进持久事件循环执行，并把源码按 `<cell-N>` 注册进 Python 标准库 `linecache`。这样抛出异常时，堆栈回溯会剥离运行时自身帧，只保留单元格代码与三方库帧，并且精准印出出错的那一行源码。如果单元格最后一条语句是表达式且求值不为 `None`，运行时把结果绑定到命名空间的 `_` 变量，同时发回 `result` 事件。

正常请求（`execute`、`snapshot`、`restore`、`list_names`、`shutdown`）严格排成单队列，一次只跑一个；但有四类请求必须绕过执行队列走带外通道，否则整个系统会自己把自己锁死：

1. `host_reply`：当运行中的单元格调用 `await rlm.repl.host_request(data)` 请求宿主（例如派生子 Agent、读信箱、发消息）时，单元格自己正占着执行队列。因此读线程收到 `host_reply` 后必须直接通过 `loop.call_soon_threadsafe` 唤醒对应的 `Future`，不能去排执行队列。
2. `interrupt`：读线程收到中断请求后直接投递中断。如果目标单元格还没排到，中断会被暂存，等到该单元格一激活立刻生效，避免连续发送 `execute` 加 `interrupt` 时把中断丢掉。
3. `bash_activity`：允许前端或宿主在单元格仍在运行时，带外查询后台 `bash()` 列表、拉取最多 200 行尾部日志（上限 16 KiB）或按不透明 ID 强杀进程，绝不接受裸 PID 以免误杀。
4. `mcp_status`：交给独立的后台任务集合 `_mcp_status_tasks` 并发查询，防止某个卡死的 MCP 服务把用户的代码执行队列一起拖住。

为防止失控单元格把宿主内存撑爆，协议对三个出口都上了硬帽：

- 单个 `result`、`evalue` 或 `traceback` 条目在 Python 侧先卡死在 `1,048,576` 字符（`_RESULT_TEXT_CAP`），超出就截断并附带标记。
- `emit(data)` 展示事件与 `host_request(data)` 宿主请求在发送前都经过 `_check_payload()` 校验：先用 `json.dumps(data, allow_nan=False)` 严格检查（防止 `NaN`/`Infinity` 生成非标准 JSON 破坏 Rust 解析器），再确认编码长度不超过 `16 MiB`（`_PAYLOAD_CAP`），超限直接向单元格抛 `ValueError`。

## `HeadTailBuffer`：为什么 3:1 头尾截断比简单切尾更实用

进入 Rust 宿主（`crates/pa-core/src/kernel/manager/requests.rs`）后，标准输出和标准错误还要再过一层上下文保护：`HeadTailBuffer`。默认上限是 `DEFAULT_MAX_OUTPUT_CHARS = 65_536` 字符，但没有一刀切地只留前 64 Ki 字符，而是按固定比例切分：

```rust
fn split_cap(max_chars: usize) -> (usize, usize) {
    let tail_cap = max_chars / 4;
    (max_chars - tail_cap, tail_cap)
}
```

头部保留 49,152 字符，尾部维护一个 16,384 字符的滚动窗口。推送流式文本时，先填满头部；头部满后，后续字符滚入尾部，当尾部缓存超过两倍 `tail_cap` 时一次性从前端裁剪掉多余部分，并把丢弃字符数累加进 `omitted`，平摊时间复杂度为线性且内存严格有界。结束时如果有省略，输出拼接为头部、`\n[... N chars omitted ...]\n` 标记和滚动尾部。

这个细节在真实编程场景里很救命。跑 `cargo test` 或 `pytest` 输出十万行日志时，前面往往是编译配置和前几个用例，真正的失败摘要和错误堆栈永远在最后几十行。简单截断头部会把报错全扔掉，3:1 头尾保留则既让模型看到开头上下文，又保住最后的失败结论。

此外，单元格默认执行超时为 `60` 秒（`DEFAULT_PYTHON_CELL_TIMEOUT_MS = 60_000`），模型可在调用工具时通过 `timeout` 参数显式申请，宿主会把它钳制在 `1` 到 `180` 秒之间（`PYTHON_REPL_TIMEOUT_MAX_S`）。如果单元格超时，宿主先发 `interrupt`；Unix 上读线程会向主线程发 `SIGINT`（若当前正是该单元格在跑同步字节码或阻塞系统调用，直接抛出 `KeyboardInterrupt`；若单元格正挂在 `await` 上，或者是一个后台协程卡死了事件循环，则取消活动单元格任务，并对阻塞主线程的后台协程同样注入 `KeyboardInterrupt` 以解开事件循环）。如果单元格连中断也不理会，宿主在 `crates/pa-core/src/tools/ipython.rs` 第 274 行果断调用 `provisioner.kill()` 强杀并重启内核，同时给模型返回 `<ipython_kernel_reset>` 提示，明确告知之前内存里的变量和任务已经清空，请重新创建。

## `dill` v2 快照：怎么跨重启保留状态而不踩雷

当会话休眠、Worker 升级重启、或准备执行上下文压缩时，宿主会向内核发送 `snapshot` 请求。内核懒加载 `dill`（recurse 模式），逐个变量序列化写入带魔数头 `PRIME-AGENT-KERNEL-SNAPSHOT-V2\n` 的二进制文件。

快照机制里有四条刻意设计的护栏：

1. **固定跳过集合**。所有下划线开头的私有变量，以及 `{rlm, mcp, bash, asyncio, In, Out, get_ipython, exit, quit, open}` 这些由启动引导重建的对象，永不写入快照。
2. **双层容量上限与定向裁剪**。单个变量序列化后超过 `16 MiB`（`DEFAULT_SNAPSHOT_MAX_VARIABLE_BYTES`），或累计大小超过 `256 MiB`（`DEFAULT_SNAPSHOT_MAX_BYTES`）都会跳过并记入清单。当宿主传入 `prune_oversized: true`（例如上下文压缩阶段），只有**超过单变量 16 MiB 上限**的变量会被从内存命名空间里删除并记入 `pruned`；因总容量 256 MiB 跳过的小变量依然留在内存里。而且删除动作严格发生在临时文件 `os.replace` 原子落盘和 JSON 清单写完之后，如果写清单失败，绝不提前删内存变量。
3. **不重开文件句柄**。`repl.md` 第 68 行记录了一个极其老道的工程决策：如果命名空间里残留着被 `dill` 序列化的已打开文件句柄，恢复时绝不会按原模式重新 `open()`。因为用 `"w"` 或 `"w+"` 重开会直接把用户磁盘上的文件清空，用 `"x"` 重开会在文件已存在时报错。所以按路径保存的句柄恢复成一个已关闭的存根对象，保留 `name` 和 `mode` 属性，一旦读写就按正常已关闭文件抛出 `ValueError`；按裸 fd 保存的句柄则直接记入该变量恢复失败。
4. **宿主失联兜底**。只有在本次进程已经至少成功提交过一次快照后，运行时才会记住 `_last_snapshot_target`；如果此时宿主异常崩溃导致 `stdin` 出现 EOF，内核退出前还能抢着把最新命名空间刷回快照文件。而在首次快照前遭遇 EOF 时绝不落盘，避免用未确认状态覆盖磁盘上的旧快照。

把 `python_repl` 的隔离、截断、中断和快照全部理顺之后，我们再往上走一层，看看跑在这个内核里的第一批主力工具：`bash`、`edit` 和 14 个内置技能包是怎么协作的。
