# 上游源码快照记录

- **上游仓库**：`https://github.com/hydro-project/infinity`
- **提交 SHA**：`e1a3e4b36e5f6143e03a59a7ad86e1abacfd22e8`
- **提交说明**：`docs: explain Linux sandbox prerequisites (#188)`
- **提交日期**：`2026-11-05`
- **分析快照采集日期**：`2026-11-22`
- **上游许可证**：Apache License 2.0（`LICENSE.txt`；`NOTICE` 注明版权归 Hydro Contributors 及 Amazon.com, Inc. or its affiliates）
- **最近版本标签**（见变更记录）：`0.2.0` 系列（`infinity-agent-core` / `rap-protocol` / `rap-client` / `infinity-rap-bridge` / `infinity-provider-protocol` / `infinity-provider-rig` / `sandbox-core` / `sandbox-local` / `sandbox-remote` / `rap-steering-server` / `rap-github-event-poller` 于 `2026-10-22` 发布）

## 代码库规模实测

| 类别 | 数量 | 行数 | 说明 |
| --- | ---: | ---: | --- |
| Workspace Rust crates | 21 | 57,969 | 共 169 个 `.rs` 文件（含测试与示例） |
| `agent/`（AWS CDK TypeScript） | 18 文件 | 1,200 | Lambda / SQS / DSQL / DynamoDB / EventBridge 部署栈 |
| `infinity-ui/`（React 组件库） | 15 文件 | 2,193 | 会话侧栏、DiffView、ChoicePicker、MigratePicker 等 |
| `infinity-web/`（桌面 Web 前端） | 6 文件 | 1,373 | Vite + WebSocket 桌面端 |
| `docs/`（Docusaurus 站点） | 51 篇 md/mdx | — | 另含 10 个 TS/TSX 交互图表与组件（3,036 行） |

## 21 个 Rust Crate 清单

| Crate | `.rs` 文件数 | 总行数 | `src/` 文件数 | `src/` 行数 | 职责 |
| --- | ---: | ---: | ---: | ---: | --- |
| `infinity-agent-core` | 29 | 15,372 | 28 | 15,050 | 核心切片引擎、`HistoryManager`、本地路由器/驱动、内置线程与休眠工具 |
| `infinity-agent-cli` | 26 | 10,294 | 17 | 7,195 | 终端内联 TUI、守护进程客户端、插件安装更新、ACP stdio 服务器 |
| `infinity-daemon` | 25 | 9,215 | 20 | 7,490 | 常驻守护进程、Unix Socket/WS、本地 JSON 持久化、SSH 远程代理与迁移编排 |
| `sandbox-local` | 22 | 4,911 | 6 | 1,066 | 本地沙箱后端、`LocalJjProvider`/`LocalGitProvider`、macOS `sandbox-exec` 与 Linux `bwrap` |
| `sandbox-core` | 9 | 3,902 | 9 | 3,902 | `SandboxBackend`/`ModeProvider` trait、Axum RAP 沙箱服务、jj/git 封装、流式命令与迁移导入导出 |
| `infinity-slack-bot` | 6 | 2,371 | 5 | 2,328 | Slack Socket Mode / API I/O 与守护进程连接壳 |
| `infinity-agent-lambda` | 10 | 1,894 | 10 | 1,894 | AWS Lambda 入口、`rap-receiver`、Aurora DSQL / DynamoDB 存储、SQS/EventBridge 定时器 |
| `infinity-slack-dataflow` | 9 | 1,817 | 7 | 1,342 | 基于 `hydro_lang` 的 Slack 机器人分阶段数据流图 |
| `infinity-provider-bedrock` | 5 | 1,445 | 4 | 1,350 | AWS Bedrock ConverseStream 供应器、60s 启动超时、错误分类与 Prompt Caching |
| `infinity-provider-protocol` | 5 | 1,280 | 5 | 1,280 | `ModelProvider` trait、`ErrorClass`、`StreamChunk` 及 Unix Socket 跨进程协议 |
| `infinity-mcp-bridge` | 1 | 797 | 1 | 797 | Stdio 与 Streamable HTTP MCP 客户端及懒连接工具集桥接 |
| `rap-github-event-poller` | 2 | 715 | 2 | 715 | GitHub 事件轮询 RAP 订阅服务（ETag + `X-Poll-Interval`） |
| `infinity-rap-bridge` | 2 | 704 | 2 | 704 | RAP 回调统一转换器 `prepare_callback`（守护进程与 Lambda 共用）与连接桥 |
| `infinity-protocol` | 1 | 613 | 1 | 613 | 客户端与守护进程通信协议、`ThreadRef`、`RemoteName`、256 MiB 帧编解码 |
| `sandbox-remote` | 5 | 534 | 5 | 534 | 面向 Lambda 的 EFS 裸仓镜像沙箱后端与 SigV4 回调客户端 |
| `rap-client` | 5 | 515 | 5 | 515 | RAP HTTP 客户端抽象、清单加载缓存、本地回调服务器与 `RapNotifier` |
| `infinity-provider-rig` | 2 | 504 | 2 | 504 | 基于 `rig-core` 的 OpenAI 兼容模型供应器与错误分类 |
| `rap-protocol` | 1 | 392 | 1 | 392 | `ThreadId`/`ToolCallId`/`ProviderCallId`/`ChoiceId`、RAP 线缆类型与 `CallbackClient` |
| `rap-steering-server` | 2 | 348 | 2 | 348 | 仓库与家目录规则文件（`INFINITY.md`/`CLAUDE.md`/`AGENTS.md` 等）扫描加载服务 |
| `rap-test-servers` | 1 | 246 | 1 | 246 | 集成测试用模拟 RAP 工具服务 |
| `infinity-mdtests` | 1 | 100 | 1 | 100 | 将 `docs/docs/infinity-runtime/**/*.md` 代码块转为可执行 Rust 文档测试 |
