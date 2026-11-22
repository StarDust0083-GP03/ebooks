# 多端接入：终端 TUI、ACP 编辑器协议与 Hydro 数据流 Slack 机器人

很多 Agent 项目把核心状态机和终端 UI 揉成一团：流式输出直接 `println!`，确认弹窗直接读 `stdin`，结果后来想加一个桌面 Web 端、接进 Zed 编辑器或者挂到公司 Slack 频道里时，不得不把半套逻辑复制粘贴三遍。

Infinity 因为从底层把 `infinity-daemon` 做成了统一的长连接枢纽（Unix Socket + WebSocket），外围接入层反而可以按各自平台的交互习惯独立演进。仓库里目前并存着四套真实前端：`infinity-agent-cli` 里的终端 TUI、同一二进制里的 `infinity acp` 编辑器协议服务、`infinity-web`/`infinity-ui` 桌面端，以及由 `infinity-slack-bot` 和 `infinity-slack-dataflow` 组成的 Slack 机器人。

## infinity-agent-cli：内联视口、无头派活与插件管理

`crates/infinity-agent-cli/`（10,294 行）是本地开发者的主入口，同时承担了三类工作：

1. **内联终端 TUI（`inline_viewport.rs`、`terminal.rs`、`modifier_diff.rs`、`choice_picker.rs`、`model_picker.rs`、`session_picker.rs`、`quit_picker.rs`）**，采用内联视口渲染，不接管全屏备用缓冲区，并针对宽字符、窗口缩放竞态和文本折行写了 8 组 `insta` + `vt100`/`alacritty_terminal` 快照测试。按下 `Ctrl+D` 退出时，CLI 发送 `ClientMessage::SoftDetach`：如果代理刚好空闲，守护进程直接收尾关闭；如果代理正在后台忙，守护进程回传 `DisconnectNotIdle`，CLI 弹出 `quit_picker` 让用户二选一是脱离挂后台（`Disconnect`）还是彻底停机（`ShutdownSession`）。
2. **脚本化与无头模式**，除了交互式启动，CLI 支持 `-i "prompt"` 带初始指令进入、`-s <session-id>` 直连已有会话、`--local` 跳过守护进程在进程内直跑，以及适合自动化流水线的 `-H "prompt"` 无头模式：把任务交给后台守护进程立刻返回退出码，稍后再用 `/load` 或桌面 Web UI 验收沙箱 Diff。
3. **自举插件管理（`install.rs`）**，`infinity provider install`、`infinity rap install` 和 `infinity update` 默认从 crates.io（也支持 `--git` 与 `--path`）拉取编译外部供应器和 RAP 服务，并自动维护 `~/.infinity/providers.json` 与 RAP 服务配置表。

## infinity acp：把守护进程会话接进 Zed 等编辑器

现代编辑器正在向 **ACP（Agent Client Protocol）** 收敛，让编辑器侧栏的 Agent 面板可以通过标准 stdio JSON-RPC 接入任意后端代理。执行 `infinity acp` 启动的就是 `crates/infinity-agent-cli/src/acp_server.rs`。

`acp_server.rs` 的架构定位很纯粹：它自己不跑大模型，也不管沙箱，只做 ACP stdio 协议与 `infinity-daemon` Unix Socket 协议之间的无状态翻译桥。编辑器发来列会话、加载历史、新建会话、发提示词、切模型等指令，桥接层转成对应的 `ClientMessage` 发给本地守护进程，再把守护进程推回来的流式文本、思考过程、工具调用和 Diff 视图实时映射成 ACP 更新帧送回编辑器面板。

这里有一条和第 2 章呼应的边界纪律：在 `0.2.0` 的类型化 ID 重构中，团队特意把外部 ACP 协议侧的会话 ID 保留为原生 `String`（且保持 `acp-sessions.json` 持久化格式不变），只在 `acp_server.rs` 边界处与内部的 `ThreadRef` 互相转换，不让外部协议的弱类型侵入守护进程内部。

## 用 Hydro 数据流写 Slack 机器人：infinity-slack-dataflow

仓库里最能体现 UC Berkeley Hydro 团队技术底色的两个 crate，是 `infinity-slack-bot`（2,371 行）和 `infinity-slack-dataflow`（1,817 行）。

Slack 机器人的状态同步一向容易写得支离破碎：一边要消费 Slack Socket Mode 的消息、斜杠命令、按钮点击和 App Home 打开事件，另一边要同时监听守护进程发来的流式分片、人工选择弹窗和会话断开通知；用户点下一个确认按钮时，既要把按钮消息更新成已选状态、停掉旧的流式输出，又要向守护进程发送 `AnswerChoice`，还得防止随后守护进程回传 `UserChoiceComplete` 时重复改写同一条 Slack 消息。

团队没有用一堆互相嵌套的 `tokio::select!` 和共享通道回调去硬凑，而是直接用自家研发的 Rust 分布式数据流框架 **`hydro_lang`**，把整个 Slack 机器人的核心逻辑写成了一个纯粹的、可离线模拟测试的数据流图（`crates/infinity-slack-dataflow/src/flow.rs`）：

```rust
// crates/infinity-slack-dataflow/src/flow.rs
pub fn slack_dataflow<'a, P: 'a>(
    slack_events: Stream<SlackEvent, Process<'a, P>, Unbounded>,
    daemon_events: Stream<DaemonEvent, Process<'a, P>, Unbounded>,
) -> (
    Stream<SlackAction, Process<'a, P>, Unbounded>,
    Stream<DaemonCommand, Process<'a, P>, Unbounded>,
)
```

整套业务逻辑就是从两个输入流 `(slack_events, daemon_events)` 到两个输出流 `(slack_actions, daemon_commands)` 的声明式变换管线：

- **入口过滤与分叉**，先用 `filter` 过滤机器人自身回声与未授权用户，再通过 `partition` 逐层把事件无拷贝地拆成四条互斥支路：交互按钮点击、斜杠命令（如 `/model` 弹 Slack Modal 选择器或纯文本列表）、App Home 首次打开（每用户只钉一次推荐提示词），以及常规聊天消息。
- **按钮点击的三路扇出**，一条 `button_clicks` 流同时派生出三条动作流：`button_stop_actions` 立刻结束当前活跃流式输出（让模型接下来的新回复落在新消息里），`button_update_actions` 把原按钮块改写成带勾选标记的确认文本，`button_commands` 解析 `action_id`（格式 `choice_{choice_id}_{selected_index}`），先从共享状态中摘除 `choice_messages` 防止重复更新，再发出 `DaemonCommand::AnswerChoice`。
- **离线模拟测试**，核心编排完全与真实 Slack HTTP/WebSocket 和守护进程 Socket I/O 解耦（真实网络壳子留在 `infinity-slack-bot` 里的 `slack_io.rs` 和 `daemon_io.rs`），`crates/infinity-slack-dataflow/tests/dataflow_sim.rs` 可以直接在进程内喂入模拟事件流、断言输出动作流，把整套复杂的多步 Slack 交互逻辑用确定性单元测试覆盖干净。

## 为什么多套前端能共用同一条总线

把 CLI TUI、桌面 Web、ACP 编辑器和 Slack 机器人横向摆在一起，会发现它们共享了同一组极简契约：

- 所有前端都不直接操作 `ConversationStore`，只通过 `ClientMessage`/`DaemonMessage`（或 WebSocket 同构 JSON）与守护进程对话。
- 所有人机交互中断点（`OAuthRequired`、`UserChoiceRequired`、`UserChoiceDismissed`）都是运行时和 RAP 协议的一等消息，因此 TUI 渲染成方向键选择器、Web 渲染成 `ChoicePicker.tsx` 卡片、Slack 渲染成 Block Kit 按钮，底层沙箱的 `write-orig` 审批逻辑一行代码都不用改。
- `DisplaySegment`（`Text`、`Diff`、`Image`）和 `ViewUpdate` 把模型消费的原始文本与前端展示的富媒体视图分开传输，客户端按自身渲染能力降级即可（例如终端渲染不出图片就自动跳过 `Image` 片段回退到后续 `Text` 片段）。

一路看下来，Infinity 的设计非常连贯，但它绝不是没有边界和短板的银弹。下一章我们专门翻出源码里的 `TODO`、显式拒绝分支、安全边界与架构代价，做一次冷静的工程复盘。
