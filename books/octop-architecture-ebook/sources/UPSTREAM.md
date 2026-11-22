# Upstream Snapshot

- Repository: `https://github.com/TencentCloud/Octop`
- Tag / Version: `v1.0.3b7` (`pyproject.toml` version `1.0.3b7`)
- Commit SHA: `ecd2cdbce3a54dee0f424f021d0a144d9669685e`
- Upstream Release Date: `2026-11-18` (`2026-11-18T02:53:37Z`)
- Snapshot Captured Date: `2026-11-22`
- Inventory File: `sources/inventory.json`
- Runtime Libraries Pinned in `pyproject.toml`:
  - `octop-harness[all]>=1.0.11`
  - `octop-memory>=1.0.5`
  - `octop-gateway>=1.0.4`
  - `octop-browser>=1.0.0`

## Major Architectural Delta (`v1.0.3b1` → `v1.0.3b7`)

- **v1.0.3b2 (2026-11-02)**: TOTP / Passkey MFA and passwordless sign-in (`auth_mfa.py`, migration `025_user_mfa`); RAGFlow knowledge connector; bundled virtual desktop and system browser opt-in by default; system prompt recorded in turn trajectory; password change revokes other sessions.
- **v1.0.3b3 (2026-11-04)**: ADR 003 storing skill package sources and knowledge bases (documents + `pgvector` chunks) on PostgreSQL; multi-replica same-row leases for cron jobs and IM channels (`infra/db/lease.py`); remote marketplace loading and in-repo catalog removal; Docker Lite vs Full split; Volcengine TOS storage backend (`tos_backend.py`); Windows SQLite `PRAGMA synchronous = NORMAL` fix (`pool.py`); LDAP user pre-provisioning.
- **v1.0.3b4 (2026-11-06)**: Tencent Cloud CVM hosts console (`api/routers/cloud.py`), shared credentials in PostgreSQL on multi-replica installs; default Docker `:latest` switched to Lite image, Full moved to `:full` / `:<version>-full`; startup preset model sync + HY4.
- **v1.0.3b5 (2026-11-11)**: Desktop shell replaced Wails with Electron 45 (`desktop/src`); versioned `history_v2` retired with startup one-time migration back to `thread_messages` (`infra/history/retire_v2.py`); custom MCP OAuth app credentials for servers without dynamic registration; `octop migrate openclaw` / `octop migrate hermes`; contributed shared conversations on shared experts (`thread_visible_to`).
- **v1.0.3b6 (2026-11-15)**: Helm chart for multi-replica clusters (`deploy/helm/octop`, `docs/cluster_CN.md`); managed experts (`kind="managed"`) wrapping ACP runners like OpenCode / Claude Code / Codex / CodeBuddy (`infra/agents/managed.py`); knowledge base entity graph (`graph.py`) and second-stage reranking (`rerank.py`); local provider Magpie; cron pinned historical thread vs `latest` (`session_target.py`); admin restrictions on allowed expert storage backends; Cursor overlapping ACP permission serialization (`acp/permissions.py`); Docker host-mount restriction and per-user Tencent Cloud credential isolation.
- **v1.0.3b7 (2026-11-18)**: Managed experts gain pinned session modes (`acp/session_modes.py`) and 6 loopback MCP servers exposing Octop connectors, knowledge bases, expert delegation, files, cron, memory, and enabled plugins (`acp/turn.py`, `acp/mcp_tools.py`); tool-call loop guard scoped strictly to consecutive identical calls (`ConsecutiveToolCallMiddleware`, default 40); Docker-deployed Octop preserves operator-configured host mounts into Docker experts while host installs continue stripping `volumes` (`omit_docker_volumes` in `docker_spec.py`); PostgreSQL orphan checkpoint GC switched to report-only so shared checkpoint tables never delete other agents' conversations (`thread_cleanup.py`); RFC 8414 path-scoped MCP OAuth metadata discovery (`oauth/mcp.py`).
