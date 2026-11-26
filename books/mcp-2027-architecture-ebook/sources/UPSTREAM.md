# 上游基线与事实口径

本书基于两类已核验的一手上游材料编写，所有协议字段、RPC 方法、错误码、扩展标识符与 SEP 编号均可在下方固定快照中逐行核对。

## 1. 主引文章

- 标题：Why I'm Bullish on MCP for 2027
- 作者：Philipp Schmid
- 来源 URL：`https://www.philschmid.de/mcp-2027`
- 发布日期：`2026-11-26`
- 本地采集文本：`upstreams/philschmid-mcp-2027.md`（采集时间 `2026-11-27`）
- 覆盖内容：2026 年底真实采用数据（Linear、Notion、Atlassian、Neon、Apple）、Code Mode 与类型化输出、Skills over MCP、MCP Events 与常驻代理、MCP Apps、未解工程缺口，以及将 Agent Harness 本身作为 MCP Server 的架构判断。

## 2. 官方规范仓库快照

- 仓库：`https://github.com/modelcontextprotocol/modelcontextprotocol`
- 固定提交：`2bbace557bbf2cb5d03d6f9b48905415cd7607b0`
- 提交日期：`2026-11-24 21:51:25 +0000`
- 提交说明：`docs: fix small errors in the 2026-07-28 and draft specification (#3586)`
- 核心正式规范版本：`2026-07-28`（位于 `docs/specification/2026-07-28/`），并对照历史版本 `2024-11-05`、`2025-03-26`、`2025-06-18`、`2025-11-25` 及最新草案 `docs/specification/draft/`。

## 3. 核心规范与扩展状态分类

| 类别 | 对象 / 标识符 | 规范 / SEP 出处 | 当前状态（截至 2026-11-24 快照） |
| --- | --- | --- | --- |
| 核心无状态与传输 | `2026-07-28` 无会话、无握手、HTTP 头镜像、列表缓存 | SEP-2567 / SEP-2575 / SEP-2243 / SEP-2549 | Final，已并入 `2026-07-28` 正式规范 |
| 多往返与追问 | MRTR (`InputRequiredResult`)、URL Mode Elicitation | SEP-2322 / SEP-1036 / SEP-1330 | Final，已并入 `2026-07-28` 正式规范 |
| 弃用治理 | Roots / Sampling / Logging / DCR / HTTP+SSE 弃用窗口 | SEP-2577 / SEP-2596 / PR #2858 | Final，已列入 `deprecated.mdx` |
| 授权基线 | OAuth 2.1、RFC 9728、CIMD、Refresh Token、`iss` 校验 | SEP-985 / SEP-991 / SEP-2207 / SEP-2468 | Final，`draft/` 已将 CIMD 升级至 draft-03 |
| 授权扩展 | `io.modelcontextprotocol/oauth-client-credentials` | SEP-1046 / `ext-auth` | Stable |
| 企业授权扩展 | `io.modelcontextprotocol/enterprise-managed-authorization` | SEP-990 / `ext-auth` | Stable |
| 交互式界面扩展 | `io.modelcontextprotocol/ui` | SEP-1865 / `ext-apps` | Stable |
| 长程任务扩展 | `io.modelcontextprotocol/tasks` | SEP-2663 / `ext-tasks` | Beta（目标并入 Core） |
| Skills 扩展 | `io.modelcontextprotocol/skills` | SEP-2640 / `ext-skills` | Beta（未声明最终去向） |
| 事件订阅扩展 | `io.modelcontextprotocol/events` | SEP-3415 / `ext-events` | Beta（未声明最终去向） |
| 预连接发现扩展 | Server Cards (`<streamable-http-url>/server-card`) | SEP-2127 / `ext-server-card` | Beta（未声明最终去向） |
| 扩展治理与生命周期 | 扩展注册、命名空间与 Experimental / Beta / Stable / Core | SEP-2133 / SEP-3392 | Final |
| 客户端本地安全 | 一键安装本地 Server 的显式确认对话框 | SEP-1024 | Final |
| 工具错误归类 | 参数校验失败归入 `isError: true` 工具执行错误 | SEP-1303 | Final |

## 4. 事实边界说明

- **来自 Phil Schmid 原文的行业数据与产品观察**：Linear（约 240 万/周，Agent 创建近半 issue）、Notion（MCP 流量 60 倍增长）、Atlassian（每个工作日超 500 万次工具调用，44% 用户来自非研发团队）、Neon（周环比增长 14%）、Apple（Pages/Numbers/Keynote 本地 MCP Server），以及各客户端（Claude、Claude Code、OpenCode、Codex、Slack、Google Antigravity 等）的实际动态，在正文中均标注为 Philipp Schmid 2026-11-26 文章披露或官方客户端支持矩阵记录。
- **来自官方仓库快照的协议规则**：所有 `MUST` / `SHOULD` / `MAY` 约束、JSON-RPC 错误码（如 `-32020` / `-32021` / `-32022` / `-32601` / `-32602`）、消息载荷字段与安全边界均核对自 `mcp-spec` 仓库原文。
- **教学实验代码**：`examples/mini_mcp_2027.py` 与 `examples/test_mini_mcp_2027.py` 是零依赖 Python 标准库教学模型，用来离线验证协议不变量与状态机行为，不代表生产级压测或外部 SaaS 真实调用。
