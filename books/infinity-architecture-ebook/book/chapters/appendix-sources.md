# 附录：源码索引、实测统计与核心术语对照

本附录汇总全书所依据的 `hydro-project/infinity` 上游快照信息、21 个 Rust crate 实测规模、关键源码文件路径、本地配置清单以及中英文核心术语对照。

## 上游快照信息

- **仓库地址**：`https://github.com/hydro-project/infinity`
- **固定提交 SHA**：`e1a3e4b36e5f6143e03a59a7ad86e1abacfd22e8`
- **提交日期与摘要**：`2026-11-05`，`docs: explain Linux sandbox prerequisites (#188)`
- **上游许可证**：Apache License 2.0（`LICENSE.txt`；`NOTICE` 标注版权归 Hydro Contributors 及 Amazon.com, Inc. or its affiliates）
- **采集核验日期**：`2026-11-22`
- **研究口径**：全部架构拆解与代码引用基于静态源码与官方文档核验，教学模型与 19 项单元测试在离线环境实跑通过；关于 80,000 个空闲 Agent 占约 8.4 GB RSS（约 102 KiB/Agent）的基准数字直接引自上游 `crates/infinity-agent-core/examples/agent_scale.rs` 与 `docs/src/components/MemoryChart.tsx`，未在本书编写过程中重新搭建 AWS Lambda 或 Bedrock 生产压测环境。

## 21 个 Workspace Crate 规模实测

统计范围覆盖快照 `e1a3e4b36e5f` 下 `crates/` 目录内全部 169 个 `.rs` 文件（总计 57,969 行 Rust 代码）：

| Crate | `.rs` 文件 | 总行数 | `src/` 文件 | `src/` 行数 | 核心职责 |
| --- | ---: | ---: | ---: | ---: | --- |
| `infinity-agent-core` | 29 | 15,372 | 28 | 15,050 | 切片执行循环、`HistoryManager`、本地路由器与驱动、`DeferQueue`、内置工具 |
| `infinity-agent-cli` | 26 | 10,294 | 17 | 7,195 | 内联终端 TUI、无头命令、插件安装器、`infinity acp` stdio 服务 |
| `infinity-daemon` | 25 | 9,215 | 20 | 7,490 | 常驻守护进程、Unix Socket/WS、懒加载 JSON 存储、SSH 远程代理与热迁移 |
| `sandbox-local` | 22 | 4,911 | 6 | 1,066 | `LocalBackend`、`LocalJjProvider`/`LocalGitProvider`、`sandbox-exec` 与 `bwrap` |
| `sandbox-core` | 9 | 3,902 | 9 | 3,902 | `SandboxBackend`/`ModeProvider`、Axum RAP 沙箱路由、流式命令与迁移导入导出 |
| `infinity-slack-bot` | 6 | 2,371 | 5 | 2,328 | Slack Socket Mode / Web API 与守护进程 I/O 外壳 |
| `infinity-agent-lambda` | 10 | 1,894 | 10 | 1,894 | SQS Lambda 入口、`rap-receiver`、DSQL/DynamoDB 存储、SQS/EventBridge 定时器 |
| `infinity-slack-dataflow` | 9 | 1,817 | 7 | 1,342 | 基于 `hydro_lang` 的 Slack 机器人分阶段数据流编排 |
| `infinity-provider-bedrock` | 5 | 1,445 | 4 | 1,350 | AWS Bedrock ConverseStream 供应器、60s 建连超时、错误分类与细粒度工具流 |
| `infinity-provider-protocol` | 5 | 1,280 | 5 | 1,280 | `ModelProvider`、`ErrorClass`、`StreamChunk` 及 Unix Socket 跨进程协议 |
| `infinity-mcp-bridge` | 1 | 797 | 1 | 797 | Stdio 与 Streamable HTTP MCP 懒连接传输层与工具桥接 |
| `rap-github-event-poller` | 2 | 715 | 2 | 715 | GitHub 事件订阅轮询 RAP 服务（ETag + `X-Poll-Interval`） |
| `infinity-rap-bridge` | 2 | 704 | 2 | 704 | 守护进程与 Lambda 共用的 `prepare_callback` 回调转换器 |
| `infinity-protocol` | 1 | 613 | 1 | 613 | `ClientMessage`/`DaemonMessage`、`ThreadRef`、`RemoteName`、256 MiB 帧编解码 |
| `sandbox-remote` | 5 | 534 | 5 | 534 | 面向 AWS Lambda 的 `EfsBackend` 裸仓沙箱与 SigV4 回调客户端 |
| `rap-client` | 5 | 515 | 5 | 515 | RAP HTTP 客户端、工具清单加载缓存、本地回调监听与 `RapNotifier` |
| `infinity-provider-rig` | 2 | 504 | 2 | 504 | 基于 `rig-core` 的 OpenAI 兼容模型供应器与错误分类 |
| `rap-protocol` | 1 | 392 | 1 | 392 | `ThreadId`/`ToolCallId`/`ProviderCallId`/`ChoiceId`、RAP 线缆类型与 `CallbackClient` |
| `rap-steering-server` | 2 | 348 | 2 | 348 | 项目与家目录规则文件（`INFINITY.md`/`CLAUDE.md`/`AGENTS.md` 等）读取服务 |
| `rap-test-servers` | 1 | 246 | 1 | 246 | 集成测试用模拟 RAP 工具服务 |
| `infinity-mdtests` | 1 | 100 | 1 | 100 | 将 `docs/docs/infinity-runtime/**/*.md` 示例自动作为 Rust 测试执行 |

配套 TypeScript 子项目规模：`agent/`（18 文件、1,200 行 AWS CDK）、`infinity-ui/`（15 文件、2,193 行共享 React 组件）、`infinity-web/`（6 文件、1,373 行 Vite 桌面端）、`docs/`（51 篇 `.md`/`.mdx` 文档与 10 个交互组件共 3,036 行）。

## 关键源码定位速查

| 主题 | 上游文件路径 | 关键符号 |
| --- | --- | --- |
| 类型化 ID 与 RAP 协议 | `crates/rap-protocol/src/lib.rs` | `ThreadId`, `ToolCallId`, `ProviderCallId`, `ChoiceId`, `RapInvocation`, `RapCallback`, `ToolsetManifest` |
| 三阶段历史与切片循环 | `crates/infinity-agent-core/src/event_processor.rs` | `HistoryManager`, `prepare_input`, `run_completion`, `recover_from_overflow`, `match_tool_result`, `safe_spawn_point`, `find_tool_call` |
| 同步线程派生与关闭 | `crates/infinity-agent-core/src/tools/thread.rs` | `SpawnThreadTool`, `ReportToParentTool`, `CloseThreadTool`, `SendMessageToChildTool` |
| 本地路由、驱动与延迟 | `crates/infinity-agent-core/src/system/local/{router,driver}.rs`, `system/defer.rs` | `route_loop`, `drive_thread`, `check_before_idle`, `InFlightStep`, `DeferQueue`, `InMemoryDeferQueue`, `NoDeferral` |
| 空闲内存扩展基准 | `crates/infinity-agent-core/examples/agent_scale.rs` | `ScriptedModel`, `RunCommand`, `rss_bytes`, `trim_allocator` |
| 供应器错误与跨进程协议 | `crates/infinity-provider-protocol/src/{completion,remote}.rs` | `ErrorClass`, `CompletionError`, `WireError`, `serve_provider`, `RemoteModelProvider` |
| 统一 RAP 回调转换 | `crates/infinity-rap-bridge/src/callback.rs` | `prepare_callback`, `convert_callback` |
| MCP 桥接 | `crates/infinity-mcp-bridge/src/lib.rs` | `McpTransport`, `StdioMcpTransport`, `HttpMcpTransport`, `McpToolSet` |
| 堆叠沙箱与写隔离 | `crates/sandbox-core/src/{sandbox,server,jj,git}.rs`, `crates/sandbox-local/src/{backend,providers}.rs`, `build.rs` | `SandboxBackend`, `ModeProvider`, `LocalBackend`, `LocalJjProvider`, `LocalGitProvider`, `migrate_inner`, `handle_execute_command_streaming_inner` |
| 本地守护进程与热迁移 | `crates/infinity-protocol/src/lib.rs`, `crates/infinity-daemon/src/{memory_store,remote,migrate}.rs` | `ThreadRef`, `RemoteName`, `length_delimited_codec`, `PersistentConversationStore`, `orchestrate_migration` |
| AWS 无服务器部署 | `crates/infinity-agent-lambda/src/{event_handler,conversation_history,bin/rap-receiver,tools/sleep}.rs` | `function_handler`, `DsqlConversationStore`, `WakeupScheduler`, `SleepTool`, `SleepUntilTool` |
| Slack Hydro 数据流 | `crates/infinity-slack-dataflow/src/flow.rs` | `slack_dataflow` |

## 核心术语对照

| 英文术语 | 本书统一译法 | 简要说明 |
| --- | --- | --- |
| Slice | 执行切片 | 一次绝不阻塞在外部工具上的有界状态推进单元 |
| RAP (Reactive Agent Protocol) | 响应式工具协议（RAP） | 基于无状态 HTTP 触发与异步回调的 Agent 工具通信协议 |
| Turn buffer | 轮次缓冲 | 流式推理期间暂存增量输出的缓冲层，提交前可整轮丢弃 |
| Unvalidated items | 未验证输入 | 已入内存但模型尚未产出输出的新输入，`sync()` 跳过防毒消息落盘 |
| Stacked sandboxes | 堆叠沙箱 | 子线程基于父线程书签拉出的隔离 `jj`/`git` 工作区，完成后压缩合并回父书签 |
| Passive tool | 被动工具 | `Tool::is_passive() == true` 的等待型工具（如 `sleep` 家族），允许外部事件自由打断 |
| Associative subscription event | 内联订阅事件 | `associative: true` 的订阅回调，跳过子线程直接注入当前线程历史 |
| ACP (Agent Client Protocol) | 代理客户端协议（ACP） | 编辑器侧栏与后端 Agent 通信的 stdio 协议，由 `infinity acp` 提供 |

## 本地核验命令

```bash
# 运行本书离线 Python 教学模型与 19 项单元测试
python3 -m unittest discover -s examples -v

# 重新生成 6 张主题自适应 SVG 架构图
python3 scripts/make_diagrams.py

# 构建完整静态阅读站、离线合订本、合并 Markdown、EPUB 3 与 PDF
bash scripts/build.sh
```
