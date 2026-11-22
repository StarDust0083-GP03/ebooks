# 把渲染留在关键路径上：TUI 卡片、延迟护栏与可观测性

终端类编程 Agent 容易给人笨重卡顿的印象，根源常常藏在启动与渲染路径里的同步阻塞：刚敲完 `prime-agent` 回车，启动路径先同步发了一次 157 毫秒的埋点 HTTPS 请求；或者底层终端库发了一条 kitty 键盘协议探测，碰到不回话的终端死等整整 2 秒；再或者一个跑了两百轮的会话，每来一个流式 Token 就把整屏 Markdown 和所有历史工具输出从头重新排版一遍。

Prime Agent 的终端层 `pa-tui`（262 个 `.rs` 文件、131,748 行）在 `AGENTS.md` 里把首帧渲染延迟列为不可退化的产品红线，背后落了四项实打实的架构和代码护栏。

## 零引擎耦合、首帧零网络与挂起 Sink 回归测试

回顾第 2 章的依赖表：`pa-tui` 只依赖 `pa-types`，完全不链接 `pa-core`、`pa-ai` 或 `pa-daemon`。启动 TUI 时，它只管初始化终端状态、连上守护进程 Socket 并接管帧渲染，所有沉重的模型目录拉取、技能扫描、Python 环境引导和 OAuth 刷新都在后台 Worker 或独立异步任务里跑。

`AGENTS.md` 里专门记录了一起编号 `#3288` 的真实性能回归事故，读起来非常有警示意义：曾经有一次改版不小心在交互启动路径上 `await` 了埋点启动刷盘，结果一次真实的 HTTPS POST 往返让冷启动首帧凭空多花了约 157 毫秒。之所以当时所有 CI 绿灯全过，是因为测试环境默认开了 `DO_NOT_TRACK=1` 且基准测试用了离线 Mock，完全遮住了真实网络延迟。

这次踩坑直接催生了全仓四条铁律：

1. **渲染关键路径绝不等待网络或磁盘同步**：遥测、目录更新、版本检查一律交由后台 Worker 托管，渲染路径只做 fire-and-forget 投递并立刻返回。
2. **用挂起 Sink 做红灯先行的回归测试**：`pa-telemetry` 与启动路径必须通过 `the_startup_flush_never_blocks_the_first_frame` 回归测试，测试里注入一个**永远不回包**的挂起网络 Sink；只要关键路径上任何一处不小心 `await` 到了投递过程，测试就会直接超时失败。
3. **修补第三方终端库的 2 秒黑洞**：根目录 `Cargo.toml` 里有一个 `[patch.crates-io]` 指向 `vendor/crossterm`。因为上游 `crossterm 0.28.1` 在检测 kitty 键盘协议支持时，遇到不回应的终端会把整个进程的事件读取线程死锁 2 秒。Prime Agent 在保持原有解析器和排队输入不变的前提下，把这个查询轮询上限硬切到了 250 毫秒，保住了绝大多数冷门终端和复用器下的秒开手感。
4. **流式渲染 50 毫秒单槽合并**：后端 `crates/pa-daemon/src/streaming.rs` 对高频到达的 `message_update` 令牌增量包做 50 毫秒单槽合并广播，而遇到 `message_start`、`message_end` 或工具事件帧时先冲刷暂存增量再直发，既保证帧序严格一致，又避免每秒上百次重绘把终端缓冲区刷爆。

## 固定首行的 `python_repl` 工具卡片与信箱直显

在传统单工具 REPL 界面里，把代码、报错、Diff、后台命令和收到的消息混成一大坨纯文本输出，人眼根本扫不动。`crates/pa-tui/src/tool_card/ipython.rs`（695 行）围绕 `python_repl` 做了精细的卡片分层：

**固定高度的折叠首行**，每张卡片顶部都是一行结构固定的摘要：状态图标（`Queued`、`Running`、`Done`、`Error`、`Aborted`）、语言标签、代码或命令预览、行数统计、耗时与异常类名。不管用户点击展开还是收起详情，顶部这一行永不跳动移位。而且如果一个单元格已经结束但里面拉起的后台 Shell 还在运行（`background_shell_running`），状态行会继续保持运行态动画且跳过静态行缓存；如果单元格本身已被用户中止，即使残留部分结果里带着已成功退出的后台 Shell，`CardStatus::of` 依然以 `card.aborted` 为准，绝不误报为成功。

**面向结构化 MIME 载荷的专有子行**，得益于内核通过 `rlm.emit()` 上报的专有 MIME 类型，TUI 卡片可以把一次 Python 执行里干的不同事情拆开清晰呈现：

- `application/vnd.prime-agent.diff+json`：渲染每文件编辑摘要和可展开的行级代码 Diff；
- `application/vnd.prime-agent.bash-command+json`：提炼单元格启动的 Shell 命令概览与非空行数；
- `application/vnd.prime-agent.agent-message+json`：按发送方向和投递状态渲染跨 Agent 消息收据；
- `application/vnd.prime-agent.transcript-read+json`：总结调用 `rlm.session.messages()` 翻阅了哪段历史区间；
- `application/vnd.prime-agent.inbox-message+json`（本次固定提交 `#4123` 新增）：在 `python_repl` 卡片内部逐条渲染 `rlm.inbox.read()` 刚刚读回来的信箱消息，样式与信箱归属行一致，彻底免去操作者自己去肉眼拆 Python `list[RLMInboxMessage]` 字典 repr 的痛苦。

在界面细节上，最近的变更（`.changes/inbox-notice-row.md`、`.changes/at-path-token-unify.md`、`.changes/md-bash-highlight.md`）也在持续消除误导：信箱到达通知以独立系统归属行渲染（标题如 `Agent inbox · 3 new messages`），绝不伪装成用户敲入的消息；排队中的信箱唤醒行折叠进队列条的内部提示计数里，不混入可浏览的用户 Steering 预览；输入框里的 `@` 路径高亮与补全规则完全统一，支持单引号、双引号、转义空格以及 `--file=@src/main.rs` 紧跟等号的写法。

## 三条可观测性管线：埋点、轨迹上传与语义边账本

除了给实时操作者看的 TUI 和 `/export` 离线 HTML 导出（`crates/pa-core/src/export_html/`，内嵌 vendored `marked` 和 `highlight.js` 并把 ANSI 终端色转换成内联 HTML 样式），Prime Agent 在后台维护了三条职责分明的可观测性数据链：

**采用度埋点（`pa-telemetry`）**，`AGENTS.md` 规定每个用户可见特性都在同一 PR 接入版本化事件，同时立下两条死规矩：属性里**绝对不能携带提示词、会话内容或文件内容**，只允许基础类型；投递全程在后台异步完成，绝不阻塞交互。

**用户授权驱动的连续轨迹上传（`crates/pa-core/src/agent_traces/`）**，`ContinuousTraceUpload` 采用全进程共享的后台异步运行时管理所有转录读取、重试和网络上传，并把并发度硬卡在最多 256 个注册控制器和 4 路并发上传，满载时直接拒绝准入记日志而不无限积压。每次会话落盘时同步路径只检查缓存的授权状态并写一个轻量待发标记文件（不 `fsync`）；后台工作线程每秒监测一次用户授权变更，一旦用户撤销授权立刻取消在途请求，重传遵守 `Retry-After` 且最多重试 3 个周期。

**语义边账本（`crates/pa-core/src/session_engine/semantic_edges/`）**，这是一份按会话落盘的仅追加模型请求账本，记录每一次向大模型发起的调用及其关联头（`X-ACP-Model-Request-ID` 与 `Idempotency-Key`），与早期 TypeScript 格式保持字节级兼容，用来把每一次模型请求和上游 ACP 调用、子 Agent 派生以及计费行精确串联起来。

至此，Prime Agent 从底层的 Rust 工作区到顶层的终端卡片与可观测性已经全部拆解完毕。最后一章，我们退后一步审视整套系统最关键的取舍：安全边界到底防住了什么、没防住什么，成本账本怎么算，最后用一份零依赖的 Python 教学模型把全书六大核心不变量亲手跑通。
