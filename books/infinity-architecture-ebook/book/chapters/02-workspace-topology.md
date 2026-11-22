# 21 个 Crate 的分层版图与类型化 ID 体系

打开一个近六万行 Rust 代码的多 crate 仓库，最容易迷失在交叉依赖里：本地守护进程、AWS Lambda、CLI 终端、Slack 机器人、MCP 桥接和 Jujutsu 沙箱全挤在同一个 `Cargo.toml` 下面，它们之间到底谁依赖谁，共享了哪些契约，又在哪里画出了硬隔离边界？

把 `crates/` 目录下的 21 个成员按依赖方向自顶向下摊开，整座系统其实只有五层，而且上下层之间的耦合压得很低。

![Infinity 21 个 Crate 的五层依赖版图](../../wiki/assets/diagrams/crate-topology.svg)

## 五层 Crate 拓扑与各自职责

最底层是**线缆协议与模型供应层**，包含 5 个 crate，定义了跨进程和跨网络的稳定数据结构，不依赖核心状态机。`rap-protocol`（392 行）只放 RAP 协议消息、基础类型化 ID 和 `CallbackClient` trait；`infinity-protocol`（613 行）定义客户端和守护进程之间的 `ClientMessage`/`DaemonMessage` 以及 `ThreadRef`；`infinity-provider-protocol`（1,280 行）抽离出 `ModelProvider` trait、`StreamChunk`、`ErrorClass` 和 Unix Socket 跨进程协议，让 `infinity-provider-bedrock`（1,445 行）与 `infinity-provider-rig`（504 行）既可以直接链接进进程，也可以作为独立子进程被守护进程托管。

往上一层是**堆叠沙箱子系统**，由 3 个 crate 组成。`sandbox-core`（3,902 行）实现了整套 Axum HTTP RAP 沙箱服务、`SandboxBackend` 与 `ModeProvider` 两个核心 trait、流式命令执行以及 `git bundle` 迁移导入导出；`sandbox-local`（4,911 行，含大量集成测试）在其上实现本地 `jj`/`git` 工作区管理和 macOS `sandbox-exec` / Linux `bwrap` 写隔离；`sandbox-remote`（534 行）则实现面向 AWS Lambda 的 EFS 裸仓镜像后端。

第三层是**工具桥接与标准 RAP 服务层**，包含 6 个 crate。`rap-client`（515 行）封装 HTTP 客户端、工具清单加载缓存、本地回调监听和 `RapNotifier` 生命周期通知；`infinity-rap-bridge`（704 行）提供守护进程与 AWS `rap-receiver` 共用的 `prepare_callback` 转换器，避免本地和云端在回调解析上漂移；`infinity-mcp-bridge`（797 行）负责把 Stdio 和 Streamable HTTP 两类 MCP 服务适配成 Infinity 工具；`rap-steering-server`（348 行）与 `rap-github-event-poller`（715 行）是两个开箱即用的真实 RAP 服务；`rap-test-servers`（246 行）供集成测试使用。

第四层是整座仓库的心脏，**核心切片运行时**。`infinity-agent-core` 独占 15,372 行代码（占全仓四分之一以上），内部包含 `event_processor.rs`（切片三阶段逻辑与 `HistoryManager`，单文件 5,209 行含测试）、`system/`（`AgentSystemBuilder`、`Thread`、本地单线程路由器与驱动、`DeferQueue`）、`tools/`（`spawn_thread`、`report_to_parent`、`close_thread`、`send_message_to_child`、`cancel_subscription`、`sleep` 家族与 `RapTool`）和 `stores.rs`（内存版默认存储）。旁边的 `infinity-mdtests`（100 行）负责把 `docs/docs/infinity-runtime/**/*.md` 里的 44 段代码示例自动编进 Rust 测试，确保文档示例不会和主干 API 脱节。

最顶层是**交互前端与部署外壳**，共 5 个 Rust crate 外加 3 个 TypeScript 目录。`infinity-daemon`（9,215 行）是本地常驻守护进程，把 `infinity-agent-core`、本地 JSON 持久化、SSH 远程代理、热迁移编排和 WebSocket 前端打包在一起；`infinity-agent-cli`（10,294 行）实现内联终端 TUI、无头命令和 `infinity acp` 编辑器协议服务；`infinity-agent-lambda`（1,894 行）提供云端 SQS 消费 Lambda 与 `rap-receiver` Function URL 两个二进制入口；`infinity-slack-bot`（2,371 行）与 `infinity-slack-dataflow`（1,817 行）把守护进程接到 Slack，其中后者直接用 Hydro 团队自研的 `hydro_lang` 编写分阶段数据流。仓库根目录下的 `agent/`（AWS CDK）、`infinity-ui/`（共享 React 组件）和 `infinity-web/`（Vite 桌面端）则补齐了云端基础设施和图形界面。

## 工作区纪律：把 Bug 拦在编译期

翻看根目录 `Cargo.toml` 里的 `[workspace.lints]` 和 `AGENTS.md`，能看出团队对运行时健壮性的几条硬规矩。workspace clippy 显式开启了 `unwrap_used = "warn"`、`unused_async = "warn"`、`allow_attributes_without_reason = "warn"`，以及 `missing_unsafe_on_extern = "deny"`。`AGENTS.md` 要求代码中不得悄悄吞掉 `Result::Err`，需要断言不变量时必须写 `.expect("bug: ...")`，把逻辑缺陷和外部运行时错误清楚分开。这些规矩和下面要讲的强类型 ID 体系一脉相承。

## 为什么花四个阶段消灭裸 String ID

在多租户、多线程、多主机代理的 Agent 运行时里，字符串 ID 串台是一类极其隐蔽的故障：把父线程 ID 传进需要 `tool_call_id` 的存储接口，或者把带远程前缀的 `"devbox/uuid"` 直接当成本地 `ThreadId` 去查文件，编译器不会报任何错，直到线上热迁移或回调匹配时才静默丢消息。

从变更记录 `0.2.0`（PR #115 与 #116）和源码可以看到，Hydro 团队分四阶段完成了从裸 `String` 到 `strkind` 强类型 ID 的全仓迁移，同时把 JSON 线缆格式保持为透明字符串（零序列化开销）。

```rust
// crates/rap-protocol/src/lib.rs
strkind::strkind! {
    pub ThreadId;
    pub ToolCallId;
    pub ProviderCallId;
}

pub type ChoiceId<T = String> = ToolCallId<T>;
```

`rap-protocol` 定义了最基础的四个标识：

| 类型 | 线缆字段名 | 来源与含义 |
| --- | --- | --- |
| `ThreadId` | `group_id` | 会话线程标识。本地守护进程生成 UUID，Lambda 侧由调用方指定并复用为 SQS FIFO `MessageGroupId` |
| `ToolCallId` | `id` | 单次工具调用标识，由模型供应器生成（如 Bedrock 的 `tooluse_...`）或运行时为合成事件生成 UUID |
| `ProviderCallId` | `call_id` | 部分模型供应器附带的第二调用标识，工具回包时必须原样回显 |
| `ChoiceId` | `id` / `choice_id` | 挂起人工选择项的标识，目前是 `ToolCallId` 的类型别名，因为选择弹窗绑定到发起它的工具调用 |

注意 `ToolCallId` 和 `ProviderCallId` 被刻意拆成两个不兼容类型。不同大模型 API 对工具调用 ID 的约定不一致（有的只用一个 ID，有的需要把 `call_id` 原样带回），过去用两个 `String` 参数传给 `Tool::execute` 极易传反，现在编译期就能拦住。转换只发生在最外层边界，比如与 `rig-core` 结构体对接或写入 DSQL/DynamoDB 字符串列时。

## 会话即根线程与 ThreadRef

在守护进程和客户端协议层（`crates/infinity-protocol/src/lib.rs`），团队做了另一项很干净的概念收敛：**取消独立的 `session_id`，承认一个会话就是它的根线程（`root_thread_id`）**。

过去协议里同时存在 `session_id` 和 `thread_id`，连 `SwitchModel` 里原本针对单线程的字段都被误标成了 `session_id`，而远程会话又靠手工拼接和 `split_once('/')` 在字符串 `"devbox/uuid"` 上做手术。引入 `RemoteName` 和 `ThreadRef` 后，这套字符串手术全部被替换成类型方法：

```rust
// crates/infinity-protocol/src/lib.rs
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct ThreadRef {
    pub remote: Option<RemoteName>,
    pub id: ThreadId,
}
```

`ThreadRef` 在几个细节上考虑得很周全：

- **线缆兼容**：`Serialize` 与 `Deserialize` 仍然走 `"{remote}/{id}"`（远程）或 `"{id}"`（本地）的复合字符串形式，前端 TypeScript 照旧按普通字符串收发；解析器 `FromStr` 会显式拒绝空远程名、空 ID 以及包含两个及以上 `/` 的非法串。
- **边界分离**：面向客户端的 `ClientMessage`（如 `Connect`、`UserInput`、`SwitchModel`、`RequestMigrate`）一律使用 `ThreadRef`，明确表达目标所在主机；而守护进程之间点对点通信的迁移指令（`Emigrate`、`EmigrateDone`、`ImportSession`）使用纯 `ThreadId`，因为已经落在目标主机本地。
- **代理改写与排序**：`ThreadRef::prefixed(&remote)` 和 `ThreadRef::strip(&remote)` 替代了原本分散在 `client_handler.rs` 和 `migrate.rs` 里的字符串拆拼；派生 `Ord` 遵循 `(remote, id)`，让 `None`（本地线程）天然排在所有 `Some(remote)` 之前，不会像字典序字符串那样把本地 UUID 和远程主机名混插在一起。

把 21 个 crate 的分层边界和这套强类型 ID 记在心里，接下来就能直接钻进 `infinity-agent-core`，拆解一次执行切片内部最核心的三阶段状态机。
