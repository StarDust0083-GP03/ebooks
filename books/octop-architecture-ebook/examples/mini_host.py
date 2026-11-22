"""Offline teaching model for Octop's core host invariants.

This module uses only the Python standard library (SQLite) to demonstrate six
architectural boundaries observed in Octop v1.0.3b7:
1. Shared experts vs private and explicitly contributed threads.
2. Mutable session-key bindings that preserve historical threads.
3. HITL approval bound to stored payload with idempotent pending -> approved transition.
4. Managed expert ACP session-mode resolution and unattended (yolo) approval gating.
5. Consecutive identical tool-call loop breaker (resets on tool/argument change).
6. Safe orphan checkpoint GC (deletes SQLite orphans on apply, report-only on PostgreSQL).

It is NOT the Octop API, does not call LLMs, and does not send external requests.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

_UNATTENDED_MARKERS = (
    "yolo",
    "bypass",
    "full-access",
    "fullaccess",
    "auto-accept",
    "autoaccept",
    "unrestricted",
    "danger",
)
_NATIVE_OCTOP_MODES = frozenset({"ask", "craft"})
_RUNNER_YOLO_MODES: dict[str, str] = {
    "codebuddy": "bypassPermissions",
    "claude_code": "bypassPermissions",
    "kimi_code": "yolo",
    "codex": "full-access",
    "opencode": "",
}


def is_unattended_mode(mode_id: str) -> bool:
    folded = (mode_id or "").strip().lower()
    return any(marker in folded for marker in _UNATTENDED_MARKERS)


def resolve_managed_session_mode(
    *,
    runner: str,
    explicit: str | None = None,
    stored: str | None = None,
    pinned: str | None = None,
    permission_mode: str = "ask",
) -> str:
    """Resolve an ACP session mode id, ignoring Octop-native ``ask``/``craft``."""
    for candidate in (explicit, stored, pinned):
        text = (candidate or "").strip()
        if text and text not in _NATIVE_OCTOP_MODES:
            return text
    if permission_mode == "yolo":
        return _RUNNER_YOLO_MODES.get(runner.strip(), "")
    return ""


def approval_for_session(session_mode: str, permission_mode: str, *, runner: str) -> str:
    """Decide ``ask`` or ``yolo`` for a managed turn (mirrors ``acp/session_modes.py``)."""
    if is_unattended_mode(session_mode):
        return "yolo"
    if session_mode and _RUNNER_YOLO_MODES.get(runner.strip(), ""):
        return "ask"
    return "yolo" if permission_mode == "yolo" else "ask"


def tool_call_signature(name: str, args: dict[str, Any] | Any) -> str:
    payload = args if isinstance(args, dict) else {"__args__": args}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return f"{name}\n{encoded}"


@dataclass(frozen=True)
class ToolGuardDecision:
    blocked: bool
    count: int
    signature: str
    jump_to: str | None = None


class ConsecutiveToolCallGuard:
    """Models Octop v1.0.3b7 ``ConsecutiveToolCallMiddleware``.

    Only consecutive identical ``(name, args)`` calls accumulate toward ``limit``.
    A different tool, different arguments, or a plain text turn resets the streak.
    """

    def __init__(self, *, limit: int = 40) -> None:
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        self.limit = limit
        self._signature = ""
        self._count = 0

    def observe_text_reply(self) -> None:
        self._signature = ""
        self._count = 0

    def observe_tool_calls(self, calls: list[tuple[str, Any]]) -> ToolGuardDecision:
        if not calls:
            self.observe_text_reply()
            return ToolGuardDecision(blocked=False, count=0, signature="")
        blocked = False
        for name, args in calls:
            sig = tool_call_signature(name, args)
            if sig == self._signature:
                self._count += 1
            else:
                self._signature = sig
                self._count = 1
            if self._count > self.limit:
                blocked = True
        return ToolGuardDecision(
            blocked=blocked,
            count=self._count,
            signature=self._signature,
            jump_to="end" if blocked else None,
        )


@dataclass(frozen=True)
class OrphanReport:
    deleted_sqlite: tuple[tuple[str, str, int], ...]
    found_sqlite: tuple[tuple[str, str, int], ...]
    skipped_postgres: tuple[tuple[str, str, int | None, int], ...]


class MiniHost:
    def __init__(self, path: str | Path = ":memory:"):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY,
                owner INTEGER NOT NULL,
                shared INTEGER NOT NULL,
                memory_backend TEXT NOT NULL DEFAULT 'sqlite'
                    CHECK(memory_backend IN ('sqlite', 'postgres', 'external_postgres'))
            );
            CREATE TABLE IF NOT EXISTS threads (
                id TEXT PRIMARY KEY,
                agent TEXT NOT NULL REFERENCES agents(id),
                user INTEGER NOT NULL,
                contributed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS sessions (
                key TEXT PRIMARY KEY,
                thread TEXT NOT NULL REFERENCES threads(id)
            );
            CREATE TABLE IF NOT EXISTS actions (
                id TEXT PRIMARY KEY,
                thread TEXT NOT NULL REFERENCES threads(id),
                payload TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('pending', 'approved', 'cancelled'))
            );
            CREATE TABLE IF NOT EXISTS outbox (
                action TEXT PRIMARY KEY REFERENCES actions(id),
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS checkpoints (
                backend TEXT NOT NULL CHECK(backend IN ('sqlite', 'postgres', 'external_postgres')),
                agent TEXT NOT NULL,
                thread TEXT NOT NULL,
                nbytes INTEGER NOT NULL,
                PRIMARY KEY (backend, agent, thread)
            );
            """
        )

    def close(self) -> None:
        self.db.close()

    def add_agent(
        self,
        agent: str,
        owner: int,
        *,
        shared: bool = False,
        memory_backend: str = "sqlite",
    ) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO agents (id, owner, shared, memory_backend) VALUES (?,?,?,?)",
                (agent, owner, int(shared), memory_backend),
            )

    def _agent_access(self, user: int, agent: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM agents WHERE id=?", (agent,)).fetchone()
        if row is None or not (row["owner"] == user or row["shared"]):
            raise PermissionError("agent inaccessible")
        return row

    def assert_agent_owner(self, user: int, agent: str) -> None:
        row = self.db.execute("SELECT * FROM agents WHERE id=?", (agent,)).fetchone()
        if row is None or row["owner"] != user:
            raise PermissionError("owner required")

    def assert_thread(self, user: int, agent: str, thread: str) -> None:
        self._agent_access(user, agent)
        row = self.db.execute("SELECT * FROM threads WHERE id=?", (thread,)).fetchone()
        if row is None or row["agent"] != agent:
            raise PermissionError("thread inaccessible")
        if not (row["user"] == user or bool(row["contributed"])):
            raise PermissionError("thread inaccessible")

    def contribute_thread(self, user: int, agent: str, thread: str) -> None:
        """Share one conversation on a shared expert (mirrors v1.0.3b5 contributed flag)."""
        agent_row = self._agent_access(user, agent)
        if not agent_row["shared"]:
            raise PermissionError("cannot contribute thread on private agent")
        row = self.db.execute("SELECT * FROM threads WHERE id=? AND agent=?", (thread, agent)).fetchone()
        if row is None or row["user"] != user:
            raise PermissionError("only thread owner can share conversation")
        with self.db:
            self.db.execute("UPDATE threads SET contributed=1 WHERE id=?", (thread,))

    def open_thread(self, user: int, agent: str, *, fresh: bool = False) -> str:
        self._agent_access(user, agent)
        key = f"{agent}:dashboard:{user}:dm"
        old = self.db.execute("SELECT thread FROM sessions WHERE key=?", (key,)).fetchone()
        if old is not None and not fresh:
            self.assert_thread(user, agent, old["thread"])
            return str(old["thread"])
        thread = uuid4().hex
        with self.db:
            self.db.execute("INSERT INTO threads (id, agent, user) VALUES (?,?,?)", (thread, agent, user))
            self.db.execute(
                "INSERT INTO sessions VALUES (?,?) ON CONFLICT(key) DO UPDATE SET thread=excluded.thread",
                (key, thread),
            )
        return thread

    def propose(self, user: int, agent: str, thread: str, *, recipient: str, text: str) -> str:
        self.assert_thread(user, agent, thread)
        if not recipient.strip() or not text.strip():
            raise ValueError("recipient and text required")
        action = uuid4().hex
        payload = json.dumps({"recipient": recipient, "text": text}, ensure_ascii=False)
        with self.db:
            self.db.execute("INSERT INTO actions VALUES (?,?,?,'pending')", (action, thread, payload))
        return action

    def _action(self, user: int, agent: str, thread: str, action: str) -> sqlite3.Row:
        self.assert_thread(user, agent, thread)
        row = self.db.execute("SELECT * FROM actions WHERE id=? AND thread=?", (action, thread)).fetchone()
        if row is None:
            raise PermissionError("action inaccessible")
        return row

    def approve(self, user: int, agent: str, thread: str, action: str) -> bool:
        self._action(user, agent, thread, action)
        with self.db:
            changed = self.db.execute(
                "UPDATE actions SET state='approved' WHERE id=? AND state='pending'", (action,)
            ).rowcount
            if changed:
                self.db.execute(
                    "INSERT INTO outbox SELECT id,payload FROM actions WHERE id=?", (action,)
                )
        return bool(changed)

    def cancel(self, user: int, agent: str, thread: str, action: str) -> bool:
        self._action(user, agent, thread, action)
        with self.db:
            changed = self.db.execute(
                "UPDATE actions SET state='cancelled' WHERE id=? AND state='pending'", (action,)
            ).rowcount
        return bool(changed)

    def write_checkpoint(self, agent: str, thread: str, nbytes: int) -> None:
        row = self.db.execute("SELECT memory_backend FROM agents WHERE id=?", (agent,)).fetchone()
        if row is None:
            raise ValueError(f"unknown agent: {agent}")
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO checkpoints (backend, agent, thread, nbytes) VALUES (?,?,?,?)",
                (row["memory_backend"], agent, thread, nbytes),
            )

    def scan_orphan_checkpoints(self, *, agent_id: str | None = None, apply: bool = False) -> OrphanReport:
        """Models ``gc_orphan_checkpoints`` in ``thread_cleanup.py`` (v1.0.3b7).

        - SQLite orphans are deleted only when ``apply=True``.
        - PostgreSQL shared checkpoint tables are ALWAYS report-only (never deleted),
          and live thread checks span ALL agents on the control plane.
        - External PostgreSQL databases are skipped without scanning.
        """
        agents = self.db.execute("SELECT id, memory_backend FROM agents ORDER BY id").fetchall()
        if agent_id is not None:
            agents = [a for a in agents if a["id"] == agent_id]
        all_live_threads = {
            str(r["id"]) for r in self.db.execute("SELECT id FROM threads").fetchall()
        }
        found_sqlite: list[tuple[str, str, int]] = []
        deleted_sqlite: list[tuple[str, str, int]] = []
        skipped_pg: list[tuple[str, str, int | None, int]] = []

        for ag in agents:
            aid = str(ag["id"])
            backend = str(ag["memory_backend"])
            if backend == "external_postgres":
                skipped_pg.append((aid, "external_database", None, 0))
                continue
            rows = self.db.execute(
                "SELECT thread, nbytes FROM checkpoints WHERE backend=? AND agent=? ORDER BY thread",
                (backend, aid),
            ).fetchall()
            if backend == "postgres":
                orphan_bytes = [int(r["nbytes"]) for r in rows if str(r["thread"]) not in all_live_threads]
                if orphan_bytes:
                    skipped_pg.append((aid, "postgres_report_only", len(orphan_bytes), sum(orphan_bytes)))
                continue
            agent_live = {
                str(r["id"])
                for r in self.db.execute("SELECT id FROM threads WHERE agent=?", (aid,)).fetchall()
            }
            for r in rows:
                tid = str(r["thread"])
                if tid not in agent_live:
                    item = (aid, tid, int(r["nbytes"]))
                    found_sqlite.append(item)
                    if apply:
                        with self.db:
                            self.db.execute(
                                "DELETE FROM checkpoints WHERE backend='sqlite' AND agent=? AND thread=?",
                                (aid, tid),
                            )
                        deleted_sqlite.append(item)

        return OrphanReport(
            deleted_sqlite=tuple(deleted_sqlite),
            found_sqlite=tuple(found_sqlite),
            skipped_postgres=tuple(skipped_pg),
        )


def demo() -> None:
    host = MiniHost()
    try:
        host.add_agent("research", 7, shared=True)
        owner_thread = host.open_thread(7, "research")
        guest_thread = host.open_thread(9, "research")
        assert owner_thread != guest_thread

        action = host.propose(7, "research", owner_thread, recipient="ops@example.invalid", text="发布通知")
        assert host.approve(7, "research", owner_thread, action)
        assert not host.approve(7, "research", owner_thread, action)
        assert host.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1

        guard = ConsecutiveToolCallGuard(limit=2)
        assert not guard.observe_tool_calls([("read_file", {"path": "a.md"})]).blocked
        assert not guard.observe_tool_calls([("read_file", {"path": "a.md"})]).blocked
        assert guard.observe_tool_calls([("read_file", {"path": "a.md"})]).blocked

        print("Teaching model OK: thread isolation, idempotent approval, and loop breaker verified.")
    finally:
        host.close()


if __name__ == "__main__":
    demo()
