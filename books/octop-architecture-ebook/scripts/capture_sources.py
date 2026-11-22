#!/usr/bin/env python3
"""Capture primary-source paths and SHA-256 hashes from an Octop checkout.

Not needed during normal ebook builds; used when refreshing the pinned upstream
snapshot into ``sources/inventory.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = Path("/home/stardust/Projects/ebook-sprint/upstreams/Octop")

MAPPING: dict[str, list[str]] = {
    "00-preface": [
        "pyproject.toml",
        "README_CN.md",
        "docs/architecture.md",
        "AGENTS.md",
    ],
    "01-requirements": [
        "README_CN.md",
        "docs/architecture.md",
        "src/octop/api/common/agent.py",
        "src/octop/api/routers/chat/ws.py",
    ],
    "02-architecture": [
        "docs/architecture.md",
        "AGENTS.md",
        "docs/adr/001-single-process-model.md",
        "docs/adr/002-database-backends.md",
        "docs/adr/003-skill-package-kb-pg-storage.md",
        "src/octop/infra/server.py",
    ],
    "03-lifecycle": [
        "src/octop/launch.py",
        "src/octop/infra/server.py",
        "src/octop/infra/db/factory.py",
        "src/octop/infra/db/rebind.py",
        "src/octop/infra/history/retire_v2.py",
    ],
    "04-agent-runtime": [
        "src/octop/infra/agents/manager.py",
        "src/octop/infra/agents/managed.py",
        "src/octop/infra/agents/settings/runtime_limits.py",
        "src/octop/infra/agents/middleware/consecutive_tool_call.py",
        "src/octop/infra/agents/middleware/model_failover.py",
        "src/octop/infra/agents/middleware/octop_ui_offload.py",
        "src/octop/infra/agents/acp/session_modes.py",
    ],
    "05-identity": [
        "src/octop/api/common/agent.py",
        "src/octop/api/routers/chat/turn.py",
        "src/octop/infra/gateway/threads.py",
        "src/octop/infra/agents/memory/thread_cleanup.py",
        "tests/integration/test_boundary_authz.py",
    ],
    "06-storage": [
        "docs/adr/002-database-backends.md",
        "docs/adr/003-skill-package-kb-pg-storage.md",
        "src/octop/infra/db/pool.py",
        "src/octop/infra/history/projection.py",
        "src/octop/infra/history/retire_v2.py",
        "src/octop/infra/agents/memory/thread_cleanup.py",
        "src/octop/infra/agents/memory/postgres_storage.py",
        "src/octop/infra/db/migrations/001_initial.sql",
    ],
    "07-workspace": [
        "docs/agent-backend-file-io.md",
        "src/octop/infra/backend/adapter.py",
        "src/octop/infra/backend/resolver.py",
        "src/octop/infra/backend/docker_spec.py",
        "src/octop/infra/backend/tos_backend.py",
        "src/octop/infra/utils/host_dirs.py",
        "src/octop/api/routers/chat/turn.py",
    ],
    "08-turn-path": [
        "src/octop/api/routers/chat/ws.py",
        "src/octop/api/routers/chat/turn.py",
        "src/octop/infra/gateway/process/processor.py",
        "src/octop/infra/gateway/process/stream_project.py",
        "src/octop/infra/agents/middleware/consecutive_tool_call.py",
        "src/octop/infra/agents/middleware/model_failover.py",
    ],
    "09-memory-rag": [
        "src/octop/infra/agents/memory/backend.py",
        "src/octop/infra/agents/memory/postgres_storage.py",
        "src/octop/infra/knowledge/index.py",
        "src/octop/infra/knowledge/pg_index.py",
        "src/octop/infra/knowledge/retrieve.py",
        "src/octop/infra/knowledge/rerank.py",
        "src/octop/infra/knowledge/graph.py",
        "src/octop/infra/knowledge/jobs.py",
    ],
    "10-extensions": [
        "src/octop/infra/agents/manager.py",
        "src/octop/infra/agents/plugins/manager.py",
        "src/octop/infra/connectors/builder.py",
        "src/octop/infra/connectors/catalog.json",
        "src/octop/infra/connectors/custom_mcp.py",
        "src/octop/infra/connectors/oauth/discovery.py",
        "src/octop/infra/connectors/oauth/mcp.py",
        "src/octop/infra/agents/acp/turn.py",
        "src/octop/infra/agents/acp/mcp_tools.py",
        "docs/octop-ui-payload-offload.md",
        "plugins/README_CN.md",
    ],
    "11-teams": [
        "docs/expert-teams.md",
        "docs/agent-interop-mailbox.md",
        "src/octop/infra/agents/teams/jobs.py",
        "src/octop/infra/agents/teams/team_manager.py",
        "src/octop/infra/agents/teams/service.py",
    ],
    "12-security": [
        "src/octop/api/middleware/jwt_auth.py",
        "src/octop/api/middleware/setup_lockdown.py",
        "src/octop/api/routers/auth_mfa.py",
        "src/octop/api/routers/cloud.py",
        "src/octop/api/common/agent.py",
        "src/octop/infra/gateway/hitl/coordinator.py",
        "src/octop/infra/agents/security/hitl_session.py",
        "src/octop/infra/agents/acp/permissions.py",
        "src/octop/infra/backend/docker_spec.py",
        "src/octop/infra/utils/ssrf_guard.py",
        "src/octop/infra/bridge/tunnel_policy.py",
    ],
    "13-dashboard": [
        "dashboard/package.json",
        "dashboard/src/pages/Chat/hooks/chatStore.ts",
        "dashboard/src/pages/Chat/hooks/wsResumeGate.ts",
        "dashboard/src/pages/Chat/hooks/frameThread.ts",
        "dashboard/src/utils/parseHarnessChunk.ts",
        "dashboard/src/pages/Chat/hooks/chatStore.history.test.ts",
    ],
    "14-interop": [
        "docs/acp.md",
        "docs/bridge.md",
        "src/octop/api/routers/managed.py",
        "src/octop/infra/agents/managed.py",
        "src/octop/infra/agents/acp/session_modes.py",
        "src/octop/infra/agents/acp/turn.py",
        "src/octop/infra/bridge/manager.py",
        "src/octop/infra/bridge/transport.py",
        "src/octop/infra/bridge/http_tunnel.py",
        "src/octop/infra/bridge/tunnel_policy.py",
    ],
    "15-operations": [
        "desktop/README.md",
        "docs/cluster_CN.md",
        "deploy/docker/Dockerfile",
        "deploy/docker/docker-compose.yml",
        "deploy/docker/docker-compose.cluster.yml",
        "deploy/helm/octop/Chart.yaml",
        "Makefile",
        ".github/workflows/ci.yml",
        "src/octop/infra/db/lease.py",
        "src/octop/infra/cron/manager.py",
        "src/octop/infra/cron/job.py",
        "src/octop/infra/cron/session_target.py",
    ],
    "16-tradeoffs": [
        "docs/adr/001-single-process-model.md",
        "docs/adr/002-database-backends.md",
        "docs/adr/003-skill-package-kb-pg-storage.md",
        "src/octop/infra/agents/manager.py",
        "src/octop/infra/gateway/process/processor.py",
        "dashboard/src/pages/Chat/hooks/chatStore.ts",
    ],
    "17-mini-host": [
        "src/octop/api/routers/chat/turn.py",
        "src/octop/infra/gateway/threads.py",
        "src/octop/infra/agents/acp/session_modes.py",
        "src/octop/infra/agents/middleware/consecutive_tool_call.py",
        "src/octop/infra/agents/memory/thread_cleanup.py",
        "src/octop/infra/agents/teams/jobs.py",
    ],
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--version", default="1.0.3b7")
    parser.add_argument("--captured", default=date.today().isoformat())
    args = parser.parse_args()

    repo = args.repo.resolve()
    commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()

    files: dict[str, dict[str, object]] = {}
    for paths in MAPPING.values():
        for relative in paths:
            if relative in files:
                continue
            path = repo / relative
            data = path.read_bytes()
            files[relative] = {
                "url": f"https://github.com/TencentCloud/Octop/blob/{commit}/{relative}",
                "sha256": hashlib.sha256(data).hexdigest(),
                "lines": len(data.splitlines()),
            }

    inventory = {
        "repository": "https://github.com/TencentCloud/Octop",
        "tag": f"v{args.version}",
        "version": args.version,
        "commit": commit,
        "upstream_date": "2026-11-18",
        "captured": args.captured,
        "file_count": len(files),
        "method": "Static source and documentation inspection; Octop runtime not executed.",
        "files": files,
        "chapters": MAPPING,
    }
    (ROOT / "sources").mkdir(exist_ok=True)
    (ROOT / "sources/inventory.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Captured {len(files)} source paths at {commit} (v{args.version})")


if __name__ == "__main__":
    main()
