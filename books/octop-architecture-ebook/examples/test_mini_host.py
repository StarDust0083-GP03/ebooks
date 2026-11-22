"""Unit tests for the offline Octop teaching model."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_host import (
    ConsecutiveToolCallGuard,
    MiniHost,
    approval_for_session,
    resolve_managed_session_mode,
)


class HostIdentityAndHitlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.host = MiniHost()
        self.host.add_agent("research", 7, shared=True)
        self.thread = self.host.open_thread(7, "research")

    def tearDown(self) -> None:
        self.host.close()

    def propose(self) -> str:
        return self.host.propose(
            7, "research", self.thread, recipient="ops@example.invalid", text="待审批通知"
        )

    def test_shared_agent_isolates_private_threads(self) -> None:
        other = self.host.open_thread(9, "research")
        self.assertNotEqual(other, self.thread)
        with self.assertRaises(PermissionError):
            self.host.assert_thread(9, "research", self.thread)

    def test_shared_agent_user_cannot_pass_owner_check(self) -> None:
        self.host.assert_agent_owner(7, "research")
        with self.assertRaises(PermissionError):
            self.host.assert_agent_owner(9, "research")

    def test_contributed_thread_becomes_visible_on_shared_agent(self) -> None:
        self.host.contribute_thread(7, "research", self.thread)
        self.host.assert_thread(9, "research", self.thread)

    def test_private_agent_rejects_contributing_thread(self) -> None:
        self.host.add_agent("private-ops", 7, shared=False)
        priv_thread = self.host.open_thread(7, "private-ops")
        with self.assertRaises(PermissionError):
            self.host.contribute_thread(7, "private-ops", priv_thread)

    def test_new_session_binding_keeps_old_thread_accessible(self) -> None:
        fresh = self.host.open_thread(7, "research", fresh=True)
        self.assertNotEqual(fresh, self.thread)
        self.assertEqual(fresh, self.host.open_thread(7, "research"))
        self.host.assert_thread(7, "research", self.thread)

    def test_duplicate_approval_is_noop_and_payload_stays_fixed(self) -> None:
        action = self.propose()
        self.assertTrue(self.host.approve(7, "research", self.thread, action))
        self.assertFalse(self.host.approve(7, "research", self.thread, action))
        rows = self.host.db.execute("SELECT * FROM outbox").fetchall()
        self.assertEqual(1, len(rows))
        self.assertEqual("ops@example.invalid", json.loads(rows[0]["payload"])["recipient"])

    def test_cross_user_approval_rejected(self) -> None:
        action = self.propose()
        other = self.host.open_thread(9, "research")
        with self.assertRaises(PermissionError):
            self.host.approve(9, "research", other, action)
        self.assertEqual(0, self.host.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0])

    def test_cancelled_action_cannot_be_approved(self) -> None:
        action = self.propose()
        self.assertTrue(self.host.cancel(7, "research", self.thread, action))
        self.assertFalse(self.host.approve(7, "research", self.thread, action))

    def test_pending_action_survives_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "teaching.sqlite"
            before = MiniHost(path)
            before.add_agent("research", 7)
            thread = before.open_thread(7, "research")
            action = before.propose(7, "research", thread, recipient="ops@example.invalid", text="发布")
            before.close()
            after = MiniHost(path)
            try:
                self.assertTrue(after.approve(7, "research", thread, action))
            finally:
                after.close()


class ManagedSessionModeTests(unittest.TestCase):
    def test_ignores_native_ask_and_craft_keeps_plan(self) -> None:
        self.assertEqual(
            "acceptEdits",
            resolve_managed_session_mode(
                runner="claude_code", explicit="ask", stored="craft", pinned="acceptEdits"
            ),
        )
        self.assertEqual(
            "plan",
            resolve_managed_session_mode(runner="opencode", stored="plan", pinned="build"),
        )

    def test_yolo_permission_falls_back_to_runner_unattended_mode(self) -> None:
        self.assertEqual(
            "full-access",
            resolve_managed_session_mode(runner="codex", permission_mode="yolo"),
        )
        self.assertEqual(
            "",
            resolve_managed_session_mode(runner="opencode", permission_mode="yolo"),
        )

    def test_approval_gate_matches_runner_unattended_semantics(self) -> None:
        self.assertEqual("yolo", approval_for_session("bypassPermissions", "ask", runner="claude_code"))
        self.assertEqual("ask", approval_for_session("acceptEdits", "yolo", runner="claude_code"))
        # OpenCode publishes no unattended mode id, so the chat's yolo setting stands.
        self.assertEqual("yolo", approval_for_session("build", "yolo", runner="opencode"))


class ConsecutiveToolCallGuardTests(unittest.TestCase):
    def test_identical_calls_past_limit_jump_to_end(self) -> None:
        guard = ConsecutiveToolCallGuard(limit=2)
        d1 = guard.observe_tool_calls([("grep", {"q": "TODO", "dir": "src"})])
        d2 = guard.observe_tool_calls([("grep", {"dir": "src", "q": "TODO"})])
        d3 = guard.observe_tool_calls([("grep", {"q": "TODO", "dir": "src"})])
        self.assertFalse(d1.blocked)
        self.assertFalse(d2.blocked)
        self.assertTrue(d3.blocked)
        self.assertEqual("end", d3.jump_to)

    def test_different_args_or_tool_resets_streak(self) -> None:
        guard = ConsecutiveToolCallGuard(limit=2)
        guard.observe_tool_calls([("read_file", {"path": "a.py"})])
        guard.observe_tool_calls([("read_file", {"path": "a.py"})])
        # Changing arguments resets count to 1.
        d = guard.observe_tool_calls([("read_file", {"path": "b.py"})])
        self.assertFalse(d.blocked)
        self.assertEqual(1, d.count)

    def test_plain_text_reply_resets_streak(self) -> None:
        guard = ConsecutiveToolCallGuard(limit=1)
        self.assertFalse(guard.observe_tool_calls([("status", {})]).blocked)
        guard.observe_text_reply()
        self.assertFalse(guard.observe_tool_calls([("status", {})]).blocked)


class OrphanCheckpointGcTests(unittest.TestCase):
    def setUp(self) -> None:
        self.host = MiniHost()

    def tearDown(self) -> None:
        self.host.close()

    def test_sqlite_orphans_reported_dry_run_and_deleted_on_apply(self) -> None:
        self.host.add_agent("local-a", 1, memory_backend="sqlite")
        live = self.host.open_thread(1, "local-a")
        self.host.write_checkpoint("local-a", live, 1024)
        self.host.write_checkpoint("local-a", "orphan-1", 4096)

        dry = self.host.scan_orphan_checkpoints(apply=False)
        self.assertEqual((("local-a", "orphan-1", 4096),), dry.found_sqlite)
        self.assertEqual((), dry.deleted_sqlite)

        applied = self.host.scan_orphan_checkpoints(apply=True)
        self.assertEqual((("local-a", "orphan-1", 4096),), applied.deleted_sqlite)
        remaining = self.host.db.execute("SELECT thread FROM checkpoints").fetchall()
        self.assertEqual([live], [r["thread"] for r in remaining])

    def test_postgres_orphans_are_report_only_and_never_deleted(self) -> None:
        self.host.add_agent("pg-a", 1, memory_backend="postgres")
        self.host.add_agent("pg-b", 2, memory_backend="postgres")
        other_agent_thread = self.host.open_thread(2, "pg-b")
        # Shared PG table holds another agent's live thread plus a real orphan.
        self.host.write_checkpoint("pg-a", other_agent_thread, 2048)
        self.host.write_checkpoint("pg-a", "pg-orphan", 8192)

        report = self.host.scan_orphan_checkpoints(agent_id="pg-a", apply=True)
        self.assertEqual((), report.deleted_sqlite)
        # Only pg-orphan is counted; pg-b's live thread is recognized as live across agents.
        self.assertEqual((("pg-a", "postgres_report_only", 1, 8192),), report.skipped_postgres)
        # Nothing deleted from PG checkpoints table even with apply=True.
        rows = self.host.db.execute("SELECT COUNT(*) FROM checkpoints WHERE backend='postgres'").fetchone()[0]
        self.assertEqual(2, rows)

    def test_external_postgres_database_is_skipped_without_scan(self) -> None:
        self.host.add_agent("pg-ext", 1, memory_backend="external_postgres")
        self.host.write_checkpoint("pg-ext", "ext-orphan", 9999)
        report = self.host.scan_orphan_checkpoints(apply=True)
        self.assertEqual((("pg-ext", "external_database", None, 0),), report.skipped_postgres)
        rows = self.host.db.execute("SELECT COUNT(*) FROM checkpoints WHERE backend='external_postgres'").fetchone()[0]
        self.assertEqual(1, rows)


if __name__ == "__main__":
    unittest.main()
