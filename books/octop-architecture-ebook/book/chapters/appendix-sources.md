# 附录：v1.0.3b7 源码索引、术语与核验命令

## 快照元数据

| 项目 | 值 |
| --- | --- |
| 官方仓库 | https://github.com/TencentCloud/Octop |
| 标签 / 版本 | `v1.0.3b7`（`pyproject.toml` 标记 `1.0.3b7`） |
| 完整提交 | `ecd2cdbce3a54dee0f424f021d0a144d9669685e` |
| 上游发布日期 | 2026-11-18 |
| 源码采集日期 | 2026-11-22 |
| 证据清单 | `sources/inventory.json`（92 个源码路径的固定 URL、SHA-256 与行数） |
| 验证口径 | 静态源码核对 + 本书构建与 18 个教学模型单测全通过；未对真实生产环境执行 LLM 压测或安全审计 |

## 核心术语速查

| 术语 | 在 Octop 源码中的具体含义 |
| --- | --- |
| 宿主（Host） | 指 `src/octop/` 这一层，负责把四套独立运行库接成多用户产品 |
| 控制面（Control Plane） | 保存用户、角色、专家、模型、通道、定时任务、历史投影与密钥的 SQLite 或 PostgreSQL 数据库 |
| composition root | 集中创建并连接全部依赖的装配入口，即 `src/octop/launch.py` 与 `OctopServer.start()` |
| turn（回合） | 一次完整的用户输入到模型/工具循环结束的处理过程，不等于单条 token 帧 |
| session_key | 入口到当前线程的可变绑定键，如 `<aid>:dashboard:<uid>:dm`、`latest` 或 `thread:<tid>` |
| thread_id | 持续对话与 LangGraph 图状态的稳定身份标识 |
| contributed 线程 | `v1.0.3b5` 起在共享专家上显式分享出来的对话，由 `thread_visible_to()` 放行给同专家可见成员 |
| checkpoint | LangGraph 运行图保存与断点恢复的状态快照，不等同于前端聊天历史投影 |
| 托管专家（Managed Expert） | `kind="managed"` 的一等专家，底层把对话交给 OpenCode、Claude Code、Codex 等外部 ACP Runner，并通过 6 个回环 MCP 消费宿主能力 |
| HITL | 危险工具执行前的人工审批中断与恢复机制，按 `pending_id` 和参数快照绑定 |
| 同行租约（Lease） | `infra/db/lease.py` 实现的 90s TTL / 30s 续约 / 25s 巡检接管机制，保证多副本下 Cron 与 IM 通道单活 |
| Bridge | 两台独立 Octop 实例间的机机 WebSocket 隧道与远端专家代理（`bridge:{conn_id}:{remote_agent_id}`） |
| ACP | Agent Client Protocol，覆盖入站 IDE 集成、出站 `acp_runner` 工具与托管专家三条路径 |
| MCP OAuth RFC 8414 | 带路径的 OAuth Issuer 元数据发现规范，把 `/.well-known/oauth-authorization-server` 插在域名与路径之间 |

## 推荐阅读源码的五组切入口

1. **全局地图**：先读 `docs/architecture.md`、`AGENTS.md` 与 `src/octop/infra/server.py`，把五层依赖红线和进程级单例对上号。
2. **存储演进**：连读 `docs/adr/001`、`002`、`003` 以及 `infra/history/retire_v2.py`、`infra/agents/memory/thread_cleanup.py`，弄清从单机 SQLite 到共享 PostgreSQL 的全部取舍。
3. **主对话链路**：顺着 `api/routers/chat/ws.py`、`turn.py`、`GlobalProcessor`、`ConsecutiveToolCallMiddleware` 和前端 `wsResumeGate.ts` 走一遍完整流。
4. **托管专家与扩展**：读 `infra/agents/managed.py`、`acp/session_modes.py`、`acp/turn.py`、`acp/permissions.py` 和 `connectors/oauth/mcp.py`。
5. **多副本运维**：读 `docs/cluster_CN.md`、`infra/db/lease.py`、`infra/backend/docker_spec.py` 和 `deploy/helm/octop/Chart.yaml`。

## 本地核验命令

在电子书仓库根目录可运行以下命令复现全部离线产物与检查：

```bash
python3 scripts/make_diagrams.py          # 生成 6 张内联 SVG 架构图
node wiki/build.mjs                       # 构建分章阅读站、全文搜索、book.html 与合并书稿
python3 scripts/make_epub.py              # 生成 EPUB 3
python3 scripts/check_links.py            # 校验全站本地链接、导出文件与零重复 ID
python3 examples/mini_host.py             # 运行最小宿主离线演示
python3 -m unittest discover -s examples -v  # 运行 18 个核心边界单元测试
```

## 按章节固定源码索引

以下全部链接固定到提交 `ecd2cdbce3a54dee0f424f021d0a144d9669685e`（`v1.0.3b7`），正文不写死易漂移的源码行号。

### 00-preface

- [`pyproject.toml`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/pyproject.toml)（229 行）
- [`README_CN.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/README_CN.md)（607 行）
- [`docs/architecture.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/architecture.md)（169 行）
- [`AGENTS.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/AGENTS.md)（437 行）

### 01-requirements

- [`README_CN.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/README_CN.md)（607 行）
- [`docs/architecture.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/architecture.md)（169 行）
- [`src/octop/api/common/agent.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/common/agent.py)（114 行）
- [`src/octop/api/routers/chat/ws.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/routers/chat/ws.py)（852 行）

### 02-architecture

- [`docs/architecture.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/architecture.md)（169 行）
- [`AGENTS.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/AGENTS.md)（437 行）
- [`docs/adr/001-single-process-model.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/adr/001-single-process-model.md)（36 行）
- [`docs/adr/002-database-backends.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/adr/002-database-backends.md)（70 行）
- [`docs/adr/003-skill-package-kb-pg-storage.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/adr/003-skill-package-kb-pg-storage.md)（85 行）
- [`src/octop/infra/server.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/server.py)（858 行）

### 03-lifecycle

- [`src/octop/launch.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/launch.py)（185 行）
- [`src/octop/infra/server.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/server.py)（858 行）
- [`src/octop/infra/db/factory.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/db/factory.py)（44 行）
- [`src/octop/infra/db/rebind.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/db/rebind.py)（158 行）
- [`src/octop/infra/history/retire_v2.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/history/retire_v2.py)（481 行）

### 04-agent-runtime

- [`src/octop/infra/agents/manager.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/manager.py)（4947 行）
- [`src/octop/infra/agents/managed.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/managed.py)（287 行）
- [`src/octop/infra/agents/settings/runtime_limits.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/settings/runtime_limits.py)（340 行）
- [`src/octop/infra/agents/middleware/consecutive_tool_call.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/middleware/consecutive_tool_call.py)（149 行）
- [`src/octop/infra/agents/middleware/model_failover.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/middleware/model_failover.py)（203 行）
- [`src/octop/infra/agents/middleware/octop_ui_offload.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/middleware/octop_ui_offload.py)（88 行）
- [`src/octop/infra/agents/acp/session_modes.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/acp/session_modes.py)（211 行）

### 05-identity

- [`src/octop/api/common/agent.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/common/agent.py)（114 行）
- [`src/octop/api/routers/chat/turn.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/routers/chat/turn.py)（412 行）
- [`src/octop/infra/gateway/threads.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/gateway/threads.py)（556 行）
- [`src/octop/infra/agents/memory/thread_cleanup.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/memory/thread_cleanup.py)（995 行）
- [`tests/integration/test_boundary_authz.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/tests/integration/test_boundary_authz.py)（129 行）

### 06-storage

- [`docs/adr/002-database-backends.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/adr/002-database-backends.md)（70 行）
- [`docs/adr/003-skill-package-kb-pg-storage.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/adr/003-skill-package-kb-pg-storage.md)（85 行）
- [`src/octop/infra/db/pool.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/db/pool.py)（205 行）
- [`src/octop/infra/history/projection.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/history/projection.py)（286 行）
- [`src/octop/infra/history/retire_v2.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/history/retire_v2.py)（481 行）
- [`src/octop/infra/agents/memory/thread_cleanup.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/memory/thread_cleanup.py)（995 行）
- [`src/octop/infra/agents/memory/postgres_storage.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/memory/postgres_storage.py)（220 行）
- [`src/octop/infra/db/migrations/001_initial.sql`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/db/migrations/001_initial.sql)（270 行）

### 07-workspace

- [`docs/agent-backend-file-io.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/agent-backend-file-io.md)（326 行）
- [`src/octop/infra/backend/adapter.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/backend/adapter.py)（191 行）
- [`src/octop/infra/backend/resolver.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/backend/resolver.py)（227 行）
- [`src/octop/infra/backend/docker_spec.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/backend/docker_spec.py)（152 行）
- [`src/octop/infra/backend/tos_backend.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/backend/tos_backend.py)（212 行）
- [`src/octop/infra/utils/host_dirs.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/utils/host_dirs.py)（422 行）
- [`src/octop/api/routers/chat/turn.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/routers/chat/turn.py)（412 行）

### 08-turn-path

- [`src/octop/api/routers/chat/ws.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/routers/chat/ws.py)（852 行）
- [`src/octop/api/routers/chat/turn.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/routers/chat/turn.py)（412 行）
- [`src/octop/infra/gateway/process/processor.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/gateway/process/processor.py)（2243 行）
- [`src/octop/infra/gateway/process/stream_project.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/gateway/process/stream_project.py)（325 行）
- [`src/octop/infra/agents/middleware/consecutive_tool_call.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/middleware/consecutive_tool_call.py)（149 行）
- [`src/octop/infra/agents/middleware/model_failover.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/middleware/model_failover.py)（203 行）

### 09-memory-rag

- [`src/octop/infra/agents/memory/backend.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/memory/backend.py)（87 行）
- [`src/octop/infra/agents/memory/postgres_storage.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/memory/postgres_storage.py)（220 行）
- [`src/octop/infra/knowledge/index.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/knowledge/index.py)（162 行）
- [`src/octop/infra/knowledge/pg_index.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/knowledge/pg_index.py)（207 行）
- [`src/octop/infra/knowledge/retrieve.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/knowledge/retrieve.py)（248 行）
- [`src/octop/infra/knowledge/rerank.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/knowledge/rerank.py)（165 行）
- [`src/octop/infra/knowledge/graph.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/knowledge/graph.py)（391 行）
- [`src/octop/infra/knowledge/jobs.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/knowledge/jobs.py)（102 行）

### 10-extensions

- [`src/octop/infra/agents/manager.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/manager.py)（4947 行）
- [`src/octop/infra/agents/plugins/manager.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/plugins/manager.py)（1137 行）
- [`src/octop/infra/connectors/builder.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/connectors/builder.py)（817 行）
- [`src/octop/infra/connectors/catalog.json`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/connectors/catalog.json)（830 行）
- [`src/octop/infra/connectors/custom_mcp.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/connectors/custom_mcp.py)（528 行）
- [`src/octop/infra/connectors/oauth/discovery.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/connectors/oauth/discovery.py)（224 行）
- [`src/octop/infra/connectors/oauth/mcp.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/connectors/oauth/mcp.py)（295 行）
- [`src/octop/infra/agents/acp/turn.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/acp/turn.py)（464 行）
- [`src/octop/infra/agents/acp/mcp_tools.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/acp/mcp_tools.py)（119 行）
- [`docs/octop-ui-payload-offload.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/octop-ui-payload-offload.md)（151 行）
- [`plugins/README_CN.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/plugins/README_CN.md)（133 行）

### 11-teams

- [`docs/expert-teams.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/expert-teams.md)（162 行）
- [`docs/agent-interop-mailbox.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/agent-interop-mailbox.md)（323 行）
- [`src/octop/infra/agents/teams/jobs.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/teams/jobs.py)（77 行）
- [`src/octop/infra/agents/teams/team_manager.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/teams/team_manager.py)（2881 行）
- [`src/octop/infra/agents/teams/service.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/teams/service.py)（367 行）

### 12-security

- [`src/octop/api/middleware/jwt_auth.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/middleware/jwt_auth.py)（74 行）
- [`src/octop/api/middleware/setup_lockdown.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/middleware/setup_lockdown.py)（41 行）
- [`src/octop/api/routers/auth_mfa.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/routers/auth_mfa.py)（309 行）
- [`src/octop/api/routers/cloud.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/routers/cloud.py)（381 行）
- [`src/octop/api/common/agent.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/common/agent.py)（114 行）
- [`src/octop/infra/gateway/hitl/coordinator.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/gateway/hitl/coordinator.py)（855 行）
- [`src/octop/infra/agents/security/hitl_session.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/security/hitl_session.py)（314 行）
- [`src/octop/infra/agents/acp/permissions.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/acp/permissions.py)（46 行）
- [`src/octop/infra/backend/docker_spec.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/backend/docker_spec.py)（152 行）
- [`src/octop/infra/utils/ssrf_guard.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/utils/ssrf_guard.py)（221 行）
- [`src/octop/infra/bridge/tunnel_policy.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/bridge/tunnel_policy.py)（121 行）

### 13-dashboard

- [`dashboard/package.json`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/dashboard/package.json)（94 行）
- [`dashboard/src/pages/Chat/hooks/chatStore.ts`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/dashboard/src/pages/Chat/hooks/chatStore.ts)（4451 行）
- [`dashboard/src/pages/Chat/hooks/wsResumeGate.ts`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/dashboard/src/pages/Chat/hooks/wsResumeGate.ts)（114 行）
- [`dashboard/src/pages/Chat/hooks/frameThread.ts`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/dashboard/src/pages/Chat/hooks/frameThread.ts)（15 行）
- [`dashboard/src/utils/parseHarnessChunk.ts`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/dashboard/src/utils/parseHarnessChunk.ts)（365 行）
- [`dashboard/src/pages/Chat/hooks/chatStore.history.test.ts`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/dashboard/src/pages/Chat/hooks/chatStore.history.test.ts)（95 行）

### 14-interop

- [`docs/acp.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/acp.md)（207 行）
- [`docs/bridge.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/bridge.md)（220 行）
- [`src/octop/api/routers/managed.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/routers/managed.py)（214 行）
- [`src/octop/infra/agents/managed.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/managed.py)（287 行）
- [`src/octop/infra/agents/acp/session_modes.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/acp/session_modes.py)（211 行）
- [`src/octop/infra/agents/acp/turn.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/acp/turn.py)（464 行）
- [`src/octop/infra/bridge/manager.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/bridge/manager.py)（2744 行）
- [`src/octop/infra/bridge/transport.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/bridge/transport.py)（455 行）
- [`src/octop/infra/bridge/http_tunnel.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/bridge/http_tunnel.py)（109 行）
- [`src/octop/infra/bridge/tunnel_policy.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/bridge/tunnel_policy.py)（121 行）

### 15-operations

- [`desktop/README.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/desktop/README.md)（269 行）
- [`docs/cluster_CN.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/cluster_CN.md)（350 行）
- [`deploy/docker/Dockerfile`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/deploy/docker/Dockerfile)（200 行）
- [`deploy/docker/docker-compose.yml`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/deploy/docker/docker-compose.yml)（63 行）
- [`deploy/docker/docker-compose.cluster.yml`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/deploy/docker/docker-compose.cluster.yml)（99 行）
- [`deploy/helm/octop/Chart.yaml`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/deploy/helm/octop/Chart.yaml)（7 行）
- [`Makefile`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/Makefile)（338 行）
- [`.github/workflows/ci.yml`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/.github/workflows/ci.yml)（156 行）
- [`src/octop/infra/db/lease.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/db/lease.py)（35 行）
- [`src/octop/infra/cron/manager.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/cron/manager.py)（708 行）
- [`src/octop/infra/cron/job.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/cron/job.py)（176 行）
- [`src/octop/infra/cron/session_target.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/cron/session_target.py)（19 行）

### 16-tradeoffs

- [`docs/adr/001-single-process-model.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/adr/001-single-process-model.md)（36 行）
- [`docs/adr/002-database-backends.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/adr/002-database-backends.md)（70 行）
- [`docs/adr/003-skill-package-kb-pg-storage.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/adr/003-skill-package-kb-pg-storage.md)（85 行）
- [`src/octop/infra/agents/manager.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/manager.py)（4947 行）
- [`src/octop/infra/gateway/process/processor.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/gateway/process/processor.py)（2243 行）
- [`dashboard/src/pages/Chat/hooks/chatStore.ts`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/dashboard/src/pages/Chat/hooks/chatStore.ts)（4451 行）

### 17-mini-host

- [`src/octop/api/routers/chat/turn.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/routers/chat/turn.py)（412 行）
- [`src/octop/infra/gateway/threads.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/gateway/threads.py)（556 行）
- [`src/octop/infra/agents/acp/session_modes.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/acp/session_modes.py)（211 行）
- [`src/octop/infra/agents/middleware/consecutive_tool_call.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/middleware/consecutive_tool_call.py)（149 行）
- [`src/octop/infra/agents/memory/thread_cleanup.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/memory/thread_cleanup.py)（995 行）
- [`src/octop/infra/agents/teams/jobs.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/teams/jobs.py)（77 行）

## 许可与说明

Octop 上游仓库采用 MIT License。本书为独立完成的源码架构分析与教学模型，非腾讯云官方文档。书中引用的少量短代码片段均附固定源码链接，若再分发上游源码请遵守其 MIT 许可。
