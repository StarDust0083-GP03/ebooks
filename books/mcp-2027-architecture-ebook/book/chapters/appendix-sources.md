# 附录：规范索引、关键 SEP 速查表与核验命令

本附录汇总全书引用的一手源文档路径、关键 SEP 编号速查表、核心术语对照，以及本书构建与核验命令，方便读者随时对照上游仓库复查任意细节。

## 一手来源与固定快照

- **主引文章**：Philipp Schmid，《Why I'm Bullish on MCP for 2027》，发布日期 `2026-11-26`，URL `https://www.philschmid.de/mcp-2027`。
- **官方规范仓库**：`https://github.com/modelcontextprotocol/modelcontextprotocol`
- **固定提交 SHA**：`2bbace557bbf2cb5d03d6f9b48905415cd7607b0`
- **提交日期**：`2026-11-24 21:51:25 +0000`（`docs: fix small errors in the 2026-07-28 and draft specification (#3586)`）

## 官方仓库核心文档路径索引

| 主题领域 | 仓库内相对路径 |
| --- | --- |
| 核心变更日志（`2026-07-28` 与草案） | `docs/specification/2026-07-28/changelog.mdx`、`docs/specification/draft/changelog.mdx` |
| 历史版本变更日志 | `docs/specification/2025-03-26/changelog.mdx`、`2025-06-18/changelog.mdx`、`2025-11-25/changelog.mdx` |
| 架构与设计原则 | `docs/specification/2026-07-28/architecture/index.mdx` |
| Streamable HTTP 与 stdio 传输 | `docs/specification/2026-07-28/basic/transports/streamable-http.mdx`、`stdio.mdx` |
| 发现与列表缓存 | `docs/specification/2026-07-28/server/discover.mdx`、`server/utilities/caching.mdx` |
| 三大核心原语 | `docs/specification/2026-07-28/server/tools.mdx`、`resources.mdx`、`prompts.mdx` |
| 多往返请求（MRTR）与追问 | `docs/specification/2026-07-28/basic/patterns/mrtr.mdx`、`client/elicitation.mdx` |
| 授权、发现、注册与安全考量 | `docs/specification/2026-07-28/basic/authorization/index.mdx`、`authorization-server-discovery.mdx`、`client-registration.mdx`、`security-considerations.mdx` |
| 弃用特性注册表 | `docs/specification/2026-07-28/deprecated.mdx` |
| 扩展总览与客户端支持矩阵 | `docs/extensions/overview.mdx`、`docs/extensions/client-matrix.mdx` |
| 五大官方扩展概览 | `docs/extensions/skills/overview.mdx`、`tasks/overview.mdx`、`events/overview.mdx`、`apps/overview.mdx`、`auth/enterprise-managed-authorization.mdx`、`auth/oauth-client-credentials.mdx` |
| 安全最佳实践 | `docs/docs/2026-07-28/tutorials/security/security_best_practices.mdx` |
| MCP Registry 与路线图 | `docs/registry/about.mdx`、`docs/development/roadmap.mdx` |

## 关键 SEP 速查表

| SEP 编号 | 标题概要 | 轨道与状态 | 在全书对应的主要章节 |
| --- | --- | --- | --- |
| SEP-414 | `_meta` 透传 OpenTelemetry Trace 上下文 | Standards Track / Final | 第 1 章、第 12 章 |
| SEP-985 | 对齐 RFC 9728 受保护资源元数据发现 | Standards Track / Final | 第 6 章 |
| SEP-986 | 工具名称字符与长度规范 | Standards Track / Final | 第 3 章 |
| SEP-990 | 企业 IdP 策略管控（Enterprise-Managed Auth / ID-JAG） | Extensions Track / Final（Stable） | 第 6 章 |
| SEP-991 | 基于 HTTPS URL 的客户端注册（CIMD） | Standards Track / Final | 第 6 章 |
| SEP-1024 | 本地 MCP Server 一键安装的客户端安全同意要求 | Standards Track / Final | 第 12 章 |
| SEP-1036 | URL Mode Elicitation 安全带外交互 | Standards Track / Final | 第 5 章 |
| SEP-1046 | OAuth 2.0 Client Credentials 机器对机器授权 | Extensions Track / Final（Stable） | 第 6 章 |
| SEP-1303 | 输入参数校验错误归入工具执行错误（`isError: true`） | Standards Track / Final | 第 3 章、第 14 章 |
| SEP-1613 / SEP-2106 | 默认 JSON Schema 2020-12 及全关键字与任意 JSON `structuredContent` | Standards Track / Final | 第 3 章 |
| SEP-1865 | MCP Apps 交互式界面（`io.modelcontextprotocol/ui`） | Extensions Track / Final（Stable） | 第 11 章 |
| SEP-2127 | MCP Server Cards 预连接 HTTP 发现 | Extensions Track / Final（Beta） | 第 12 章 |
| SEP-2133 / SEP-3392 | 官方扩展机制与四阶段成熟度生命周期 | Standards/Process / Final | 第 7 章 |
| SEP-2164 | 资源未找到错误码统一调整为 `-32602` | Standards Track / Final | 第 2 章 |
| SEP-2207 | OIDC 风格 Refresh Token 与 `offline_access` 指南 | Standards Track / Final | 第 6 章 |
| SEP-2243 | Streamable HTTP 标准请求头镜像与 `x-mcp-header` | Standards Track / Final | 第 2 章、第 14 章 |
| SEP-2322 | 多往返请求（MRTR，`InputRequiredResult` + `requestState`） | Standards Track / Final | 第 5 章、第 14 章 |
| SEP-2468 | 授权响应 RFC 9207 `iss` 参数校验防 Mix-Up | Standards Track / Final | 第 6 章 |
| SEP-2549 | 列表与读取结果的 `ttlMs` 与 `cacheScope` 缓存规范 | Standards Track / Final | 第 2 章、第 14 章 |
| SEP-2567 | 无会话 MCP 与显式状态句柄模式 | Standards Track / Final | 第 1 章、第 2 章 |
| SEP-2575 | 无状态 MCP（移除 `initialize`、新增 `server/discover` 与 `subscriptions/listen`） | Standards Track / Final | 第 1 章、第 2 章 |
| SEP-2577 / SEP-2596 | 弃用 Roots/Sampling/Logging 及 12 个月特性弃用生命周期 | Standards Track / Final | 第 1 章 |
| SEP-2640 | Skills over MCP（`io.modelcontextprotocol/skills`） | Extensions Track / Final（Beta） | 第 8 章、第 14 章 |
| SEP-2663 | MCP Tasks 异步任务扩展（`io.modelcontextprotocol/tasks`） | Extensions Track / Final（Beta → Core） | 第 9 章、第 14 章 |
| SEP-3415 | MCP Events 事件订阅扩展（`io.modelcontextprotocol/events`） | Extensions Track / Final（Beta） | 第 10 章、第 14 章 |

## 核心术语对照

- **Streamable HTTP**：MCP 的标准远程 HTTP 传输绑定，单一路径接收 POST，按单次请求选择返回普通 JSON 或绑定到该请求的 SSE 响应流。
- **MRTR（Multi Round-Trip Requests）**：多往返请求模式。服务端通过返回 `resultType: "input_required"` 和不透明 `requestState` 暂停请求并索要输入，客户端收集完后换新 JSON-RPC `id` 重试，无需粘性会话。
- **Code Mode**：由 Agent 宿主提供无原始凭证隔离沙箱，依据 MCP `inputSchema` 与 `outputSchema` 生成类型化函数桩，让模型一次编写脚本在沙箱内存里并发调用、过滤与 Join 多台服务器数据。
- **CIMD（Client ID Metadata Documents）**：以客户端自托管的 HTTPS 元数据 URL 直接作为 `client_id` 的 OAuth 注册方式，取代已弃用的 RFC 7591 动态客户端注册。
- **ID-JAG（Identity Assertion JWT Authorization Grant）**：企业托管授权扩展中，客户端拿企业 SSO 身份断言向企业 IdP 通过 RFC 8693 换取的授权断言，再通过 JWT Bearer Grant 换取目标 MCP Server 的访问令牌。
- **Explicit State Handles（显式状态句柄）**：无会话架构下由创建类工具返回、由模型在后续工具参数中显式传递的不透明资源标识（如 `basket_id`）。

## 本书构建与核验命令

```bash
# 1. 运行零依赖教学运行时 21 项单元测试
python3 -m unittest discover -s examples -v

# 2. 重新生成 6 张内联 SVG 架构图（含文字溢出与符号检查）
python3 scripts/make_diagrams.py

# 3. 构建多页静态阅读站、全文索引、离线单文件 HTML 与合并 Markdown
node wiki/build.mjs

# 4. 生成 EPUB 3 与印刷级 PDF
python3 scripts/make_epub.py
chromium --headless=new --disable-gpu --no-sandbox --no-pdf-header-footer \
  --print-to-pdf=exports/mcp-2027-architecture.pdf wiki/book.html

# 5. 校验站点全部内部链接、图片与导出包
python3 scripts/check_links.py
```
