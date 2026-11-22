# 十个 Rust Crate 与一层 Python 运行时的双层拓扑

代码库膨胀到 50 万行以上时，最怕出现网状依赖：改一行 UI 卡片结构，结果触发整个会话引擎和 HTTP 供应商层全量重编；或者一个底层工具悄悄引用了守护进程状态，导致无守护进程的 `-p` 单次打印模式根本没法独立实例化。

Prime Agent 用一条很硬的工作区规则把边界焊死了。打开根目录 `Cargo.toml` 和 `AGENTS.md`，整个 Rust 部分由 10 个以 `pa-` 为前缀的 crate 组成，只有一个共享词表 crate（`pa-types`），依赖方向严格从组合根向下单向流动，全工作区通过 `[workspace.lints.rust]` 开启 `unsafe_code = "forbid"`，并在 CI 中执行 `cargo clippy --workspace --all-targets -- -D warnings`。

下面这张图给出了 10 个 Rust crate 的分层关系，以及运行时真正的进程边界：

![Prime Agent 十个 Rust Crate 与双层进程拓扑](../../wiki/assets/diagrams/dual-layer-topology.svg)

图里的虚线框标出了编译期依赖和运行期进程的对应关系。编译期，`pa-cli` 是唯一把各层拼装起来的组合根；运行期，一个普通交互会话至少横跨三个独立操作系统进程：前端终端界面、全局常驻的 Supervisor 守护进程、该会话专属的 Worker 进程，以及 Worker 拉起的 CPython REPL 子进程。

## 十个 Crate 的职责与依赖边界

每个 crate 都在自己的 `README.md` 里明确划出了负责范围和非目标，防止出现无所不包的超大模块：

| Crate | 源码规模（`src/`） | 直接工作区依赖 | 核心职责与刻意排除项 |
| --- | --- | --- | --- |
| `pa-types` | 44 文件 / 13,253 行 | 无 | 全工作区唯一共享词表：AI 消息类型、压缩配置、会话 JSONL 条目、守护进程帧与命令平面、信箱通知、平台路径与传输 trait |
| `pa-telemetry` | 17 文件 / 4,733 行 | 无 | 版本化埋点事件、后台 fire-and-forget 上报、跨平台原子替换原语 `rename_onto` |
| `pa-agent` | 14 文件 / 5,646 行 | 无 | 与具体模型供应商无关的 Turn 循环、Claim-First 运行槽位、三层消息队列、中止信号 |
| `pa-ai` | 83 文件 / 41,483 行 | `pa-types` | 各大 LLM 供应商流式协议适配、OAuth 凭据刷新、内置兜底模型表 `models_generated.rs`、缓存计费 |
| `pa-models` | 11 文件 / 3,753 行 | `pa-ai`, `pa-types` | 在线模型目录拉取、磁盘缓存与回退链路 |
| `pa-core` | 275 文件 / 135,470 行 | `pa-types`, `pa-ai`, `pa-models`, `pa-agent`, `pa-telemetry` | 会话引擎 `SessionEngine`、工具注册、Python 内核生命周期、技能加载、提示词分层组装、压缩、持续精炼、MCP 宿主认证、目标与自主门禁、Cron 调度、HTML 导出 |
| `pa-daemon` | 256 文件 / 148,695 行 | `pa-types`, `pa-core`, `pa-agent`, `pa-ai`, `pa-telemetry`, `pa-models` | Supervisor 监督进程、每会话 Worker 托管、重启退避、RLM Spawn 账本、信箱托管、直接挂载 Ticket、Worker 点对点通信、热更新事务、ACP stdio 服务 |
| `pa-tui` | 262 文件 / 131,748 行 | `pa-types` | 终端交互界面、守护进程线协议客户端、Markdown 与工具卡片渲染；不链接 `pa-core` 或 `pa-daemon` |
| `pa-cli` | 81 文件 / 35,768 行 | `pa-types`, `pa-core`, `pa-ai`, `pa-agent`, `pa-daemon`, `pa-tui`, `pa-telemetry` | `prime-agent` 二进制入口、手写参数解析器、把用户命令路由到 TUI、守护进程、无头打印或 ACP 模式 |
| `pa-perf` | 10 文件 / 4,242 行 | `pa-types`, `pa-core`, `pa-daemon` | 性能语料库与 PTY 基准测试脚手架，不随发行版发布 |

最关键的一条隔离线在 `pa-tui`。它有 13 万多行代码，却只依赖 `pa-types` 一个工作区 crate。TUI 进程压根不链接大模型客户端、不链接会话引擎、也不直接碰磁盘上的状态机，它只消费线协议帧并渲染。这样既保住了首帧启动速度，也让多个客户端同时挂载到同一个运行中会话成为可能。

另一个容易忽略的细节是 `pa-cli` 的参数解析器（`crates/pa-cli/src/args.rs`）。它没有用常见的 `clap` 派生宏，而是纯手写实现。文件头部注释给出了原因：`--resume` 和 `--print` 这类标志允许可选地吃掉下一个参数，而且 `--goal`、`--autonomous-gate`、`--system-prompt` 后面的自由文本可能合法地以短横线或 YAML frontmatter 开头，普通派生解析器很难原样复刻既有 CLI 行为。

## Supervisor 与 Per-Session Worker：为什么要拆进程

很多 CLI Agent 把会话循环直接跑在交互进程里，或者做成一个单进程多协程的守护服务。Prime Agent 选了第三种架构：**一个轻量的 Supervisor 进程，为每个活跃会话各拉起一个独立的 Worker 进程**。

在 `crates/pa-daemon/README.md` 和 `crates/pa-daemon/src/supervisor/` 中，这套模型的运转方式非常清晰：

**故障隔离与退避重启**，单个会话如果因为极端输出、内核异常或底层崩溃挂掉，只影响对应的 Worker，不会连累正在后台跑测试的其他十几个会话或子 Agent。Supervisor 监控每个 Worker 进程，异常退出后按退避策略拉起，新 Worker 带 `--resume <sessionFile>` 从磁盘上的 append-only JSONL 恢复分支历史与队列日志。

**Supervisor 重启不丢会话身份**，哪怕守护进程自己重启或升级，存活的 Worker 也会带着自身会话身份反向退避重连，把名册（Roster）重新注册回来。终端断开只是断掉一条客户端连接，后台 Worker 和里面的 Python REPL、定时心跳、子 Agent 全都继续推进，随时可以用 `prime-agent attach <agent>` 重新接上。

**直接挂载（Direct Attach）减轻中转压力**，流式生成时每个 Token 都会产生增量更新事件。如果所有客户端都经由 Supervisor 转发每个字节，Supervisor 就会变成性能瓶颈。`pa-daemon` 的做法是让 Supervisor 充当控制面与鉴权者：客户端请求挂载时，Supervisor 签发一张有效期 10 秒的一次性票据（Single-use Ticket），Worker 验票即焚，之后高频的 `message_update` 令牌流直接走客户端与 Worker 之间的点对点连接（Worker 内部还配有 50 毫秒单槽合并刷盘器），Supervisor 自身保持无载荷转发。

**父死子灭的生命周期回收**，`crates/pa-daemon/src/supervisor_parent_death.rs` 专门处理一种棘手场景：如果某个父会话的 Worker 意外退出（例如遭遇 `SIGKILL` 没来得及走优雅清理），Supervisor 会依据创建记录里的 `parentActiveSessionId` 找到其名下所有常驻 RLM 子会话并执行关闭，防止产生无人认领的孤儿计费会话。

## Rust 宿主与 Python 运行时的职责切分

Rust 层负责高并发、持久化、进程托管、网络流和终端渲染，而 `prime-agent-runtime` 负责模型直接编写和调用的编程环境。两层之间没有 FFI，也没有把 CPython 静态链接进 Rust 二进制，而是通过标准输入输出的 JSON 行协议隔开。

这样切分后，两边的职责一目了然：

Rust 宿主（`pa-core` + `pa-daemon`）掌握所有需要强一致、跨进程或涉及用户凭据的能力。比如模型凭据与 OAuth 令牌存储、JSONL 会话与信箱日志落盘、RLM 深度校验与子进程派生、上下文 Token 估算与压缩调度、以及 TUI 卡片的增量渲染。

Python 运行时（`prime-agent-runtime/src/rlm/`，共 9 个核心模块约 1.37 万行源码）运行在 `uv` 管理的独立虚拟环境中，专注做好一件事：提供一个支持顶层 `await` 的持久 `__main__` 命名空间，让模型用原生 Python 语法读写文件、跑 `bash()`、调 `mcp.call_tool()`、操作 `rlm.harness`、驱动 `rlm.factory` 状态机，并通过 `await rlm.repl.host_request(...)` 向 Rust 宿主发起带类型的控制请求。

顺带一提，发布构建也体现出这支团队的工程克制。根目录 `Cargo.toml` 的 `[profile.release]` 使用 `lto = "thin"`、`codegen-units = 4` 和 `debug = "line-tables-only"`，但不在链接期剥离符号，而是由发布脚本 `scripts/release/split_debug.py` 对同一份链接产物分别产出裁剪后的发行二进制和独立的调试解码侧车文件。注释里专门说明了原因：他们实测过，如果在链接期直接 strip，`.text` 段布局会发生变化，导致离线堆栈还原地址对不上。

摸清了静态 crate 分层和动态进程拓扑之后，下一章我们直接钻进最核心的执行心脏：`pa-agent` 的 Turn 循环怎样协调并发抢占与三层消息队列。
