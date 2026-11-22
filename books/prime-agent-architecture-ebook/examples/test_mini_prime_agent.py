"""Unit tests for the zero-dependency Prime Agent teaching model."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_prime_agent import (
    AssistantTurn,
    BusyError,
    CompactionSettings,
    HeadTailBuffer,
    MiniAgentLoop,
    PersistentReplKernel,
    SessionInbox,
    SubagentRegistry,
    compaction_threshold,
    should_compact,
)


class TestHeadTailBuffer(unittest.TestCase):
    def test_short_text_passes_through_unchanged(self) -> None:
        buf = HeadTailBuffer(max_chars=100)
        buf.push("hello ")
        buf.push("world")
        self.assertEqual(buf.finish(), "hello world")

    def test_overflow_keeps_three_quarter_head_and_one_quarter_tail(self) -> None:
        buf = HeadTailBuffer(max_chars=20)  # head=15, tail=5
        buf.push("ABCDEFGHIJKLMNO")  # 15 head
        buf.push("1234567890")  # omitted 5, tail 67890
        out = buf.finish()
        self.assertIn("ABCDEFGHIJKLMNO", out)
        self.assertIn("[... 5 chars omitted ...]", out)
        self.assertTrue(out.endswith("67890"))

    def test_multiple_small_tail_chunks_roll_and_keep_final_verdict(self) -> None:
        buf = HeadTailBuffer(max_chars=16)  # head=12, tail=4
        buf.push("0123456789ab")
        for chunk in ("cc", "dd", "ee", "ff", "FAIL"):
            buf.push(chunk)
        out = buf.finish()
        self.assertTrue(out.startswith("0123456789ab"))
        self.assertTrue(out.endswith("FAIL"))
        self.assertIn("8 chars omitted", out)


class TestPersistentReplKernel(unittest.TestCase):
    def test_variables_and_trailing_expression_persist_across_cells(self) -> None:
        kernel = PersistentReplKernel()
        r1 = kernel.execute("x = 21\nx * 2")
        self.assertEqual(r1.status, "ok")
        self.assertEqual(r1.result, "42")
        r2 = kernel.execute("_ + 8")
        self.assertEqual(r2.result, "50")

    def test_snapshot_prunes_oversized_variable_only_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snap_path = Path(tmp) / "state.json"
            kernel = PersistentReplKernel()
            kernel.execute("small = {'k': 1}\nbig = 'x' * 500\n_private = 99")
            report = kernel.snapshot(snap_path, max_variable_bytes=100, prune_oversized=True)
            self.assertEqual(report.saved, ["small"])
            self.assertEqual(report.skipped, ["big"])
            self.assertEqual(report.pruned, ["big"])
            self.assertNotIn("big", kernel.namespace)
            self.assertIn("small", kernel.namespace)
            self.assertIn("_private", kernel.namespace)

    def test_snapshot_aggregate_cap_skips_without_pruning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snap_path = Path(tmp) / "state.json"
            kernel = PersistentReplKernel()
            kernel.execute("a = '1' * 40\nb = '2' * 40")
            report = kernel.snapshot(snap_path, max_bytes=60, max_variable_bytes=100, prune_oversized=True)
            self.assertEqual(report.saved, ["a"])
            self.assertEqual(report.skipped, ["b"])
            self.assertEqual(report.pruned, [])
            self.assertIn("b", kernel.namespace)

    def test_restore_rehydrates_saved_variables_and_handles_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snap_path = Path(tmp) / "state.json"
            k1 = PersistentReplKernel()
            k1.execute("metrics = [1, 2, 3]")
            k1.snapshot(snap_path)

            k2 = PersistentReplKernel()
            restored, failed = k2.restore(snap_path)
            self.assertEqual(restored, ["metrics"])
            self.assertEqual(failed, [])
            self.assertEqual(k2.execute("sum(metrics)").result, "6")

            empty_restored, _ = k2.restore(Path(tmp) / "missing.json")
            self.assertEqual(empty_restored, [])


class TestSessionInbox(unittest.TestCase):
    def test_journal_replays_complete_records_across_torn_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "inbox.jsonl"
            inbox1 = SessionInbox(journal)
            m1 = inbox1.push("bash-done", "pytest ok", arrived_at_ms=1000, sender="pytest")
            m2 = inbox1.push("agent-message", "done", arrived_at_ms=2000, sender="worker-a")
            inbox1.read({"sender": "pytest"})
            # Simulate crash leaving a torn JSON line at EOF
            with journal.open("a", encoding="utf-8") as fh:
                fh.write('{"op":"push","id":"inbox_99","class":"bash-')

            inbox2 = SessionInbox(journal)
            self.assertEqual(inbox2.count()["count"], 1)
            msgs = inbox2.read()
            self.assertEqual([m.id for m in msgs], [m2.id])
            m3 = inbox2.push("bash-done", "build ok", arrived_at_ms=3000, sender="cargo")
            self.assertNotEqual(m3.id, m1.id)
            self.assertNotEqual(m3.id, m2.id)

    def test_strict_filter_rejects_unknown_field_and_preserves_messages(self) -> None:
        inbox = SessionInbox()
        inbox.push("bash-done", "ok", arrived_at_ms=100, sender="make")
        with self.assertRaises(ValueError):
            inbox.read({"sendr": "make"})  # typo must fail closed
        self.assertEqual(inbox.count()["count"], 1)

    def test_targeted_filter_drains_only_matching_messages(self) -> None:
        inbox = SessionInbox()
        inbox.push("bash-done", "t1", arrived_at_ms=100, sender="cmd1")
        inbox.push("bash-done", "t2", arrived_at_ms=200, sender="cmd2")
        inbox.push("child-exited", "c1", arrived_at_ms=250, sender="sub1")
        out = inbox.read({"type": "rlm.inbox.read", "class": "bash-done", "sinceMs": 150, "beforeMs": 250})
        self.assertEqual([m.text for m in out], ["t2"])
        self.assertEqual(inbox.count()["count"], 2)

    def test_heartbeat_keeps_only_latest_fire_per_job(self) -> None:
        inbox = SessionInbox()
        inbox.push("heartbeat", "fire 1", arrived_at_ms=100, sender="hb-1")
        inbox.push("heartbeat", "fire 2", arrived_at_ms=200, sender="hb-1")
        inbox.push("heartbeat", "other", arrived_at_ms=250, sender="hb-2")
        self.assertEqual(inbox.count()["count"], 2)
        msgs = inbox.read({"sender": "hb-1"})
        self.assertEqual([m.text for m in msgs], ["fire 2"])

    def test_coalesced_wake_notice_names_single_or_mixed_classes(self) -> None:
        inbox = SessionInbox()
        self.assertIsNone(inbox.wake_notice_text())
        inbox.push("bash-done", "a", arrived_at_ms=10, sender="s1")
        inbox.push("bash-done", "b", arrived_at_ms=20, sender="s2")
        self.assertIn("2 new bash-done messages", inbox.wake_notice_text() or "")
        inbox.push("child-failed", "c", arrived_at_ms=30, sender="w1")
        self.assertIn("3 new messages", inbox.wake_notice_text() or "")


class TestSubagentRegistry(unittest.TestCase):
    def test_enforces_max_depth_and_sibling_uniqueness(self) -> None:
        reg = SubagentRegistry(max_depth=2)
        c1 = reg.spawn(parent_id="root", parent_depth=0, name="researcher")
        self.assertEqual(c1.depth, 1)
        with self.assertRaises(ValueError):
            reg.spawn(parent_id="root", parent_depth=0, name="researcher")
        c2 = reg.spawn(parent_id=c1.child_id, parent_depth=1, name="leaf")
        self.assertEqual(c2.depth, 2)
        with self.assertRaises(ValueError):
            reg.spawn(parent_id=c2.child_id, parent_depth=2, name="too-deep")

    def test_progress_note_checks_utf16_limit_and_ten_second_throttle(self) -> None:
        reg = SubagentRegistry()
        child = reg.spawn(parent_id="root", parent_depth=0, name="worker")
        # Astral emoji counts as 2 UTF-16 code units: 257 * 2 = 514 > 512
        with self.assertRaises(ValueError):
            reg.progress_note(child.child_id, "\U0001f680" * 257, now_ms=10_000)
        ok = reg.progress_note(child.child_id, "step 1 done", now_ms=10_000)
        self.assertTrue(ok["accepted"])
        throttled = reg.progress_note(child.child_id, "step 2 done", now_ms=14_000)
        self.assertFalse(throttled["accepted"])
        self.assertEqual(throttled["retry_after_ms"], 6_000)
        ok2 = reg.progress_note(child.child_id, "step 2 done", now_ms=20_000)
        self.assertTrue(ok2["accepted"])

    def test_collect_returns_snapshots_for_selected_or_all_children(self) -> None:
        reg = SubagentRegistry()
        a = reg.spawn(parent_id="root", parent_depth=0, name="alpha")
        b = reg.spawn(parent_id="root", parent_depth=0, name="beta")
        reg.finish(a.child_id, status="done", answer="found 3 bugs")
        snap = reg.collect("root")
        self.assertEqual(len(snap), 2)
        only_a = reg.collect("root", targets=["alpha"])
        self.assertTrue(only_a[0]["settled"])
        self.assertEqual(only_a[0]["answer_text"], "found 3 bugs")
        only_b = reg.collect("root", targets=[b.child_id])
        self.assertFalse(only_b[0]["settled"])


class TestCompactionPolicy(unittest.TestCase):
    def test_threshold_respects_combined_ceiling_and_retention_floor(self) -> None:
        # 200k window, 32k max output, default 95% -> requested 190k, combined = 200k - 32k - 16384 = 151616
        t = compaction_threshold(200_000, 32_000, CompactionSettings())
        self.assertEqual(t, 151_616)
        self.assertFalse(should_compact(151_616, 200_000, 32_000))
        self.assertTrue(should_compact(151_617, 200_000, 32_000))

        # Very low requested percentage (10% of 200k = 20k) is lifted to retention_floor (20k + 2*16384 + 1 = 52769)
        low_pct = compaction_threshold(200_000, 32_000, CompactionSettings(threshold_percent=10.0))
        self.assertEqual(low_pct, 52_769)


class TestMiniAgentLoop(unittest.TestCase):
    def test_claim_first_prevents_overlapping_runs(self) -> None:
        loop = MiniAgentLoop(PersistentReplKernel())
        loop.claim_run()
        with self.assertRaises(BusyError):
            loop.claim_run()
        loop.release_run()

    def test_end_turn_runs_remaining_batch_calls_then_stops_without_extra_reply(self) -> None:
        kernel = PersistentReplKernel()
        loop = MiniAgentLoop(kernel)
        calls = {"n": 0}

        def model_fn(_transcript: list[dict]) -> AssistantTurn:
            calls["n"] += 1
            if calls["n"] == 1:
                return AssistantTurn(
                    text="launching work",
                    tool_calls=["end_turn()", "ran_second = True\nran_second"],
                )
            return AssistantTurn(text="should not run")

        transcript = loop.run("start", model_fn)
        self.assertEqual(calls["n"], 1)
        self.assertTrue(kernel.namespace.get("ran_second"))
        tool_results = [row for row in transcript if row["role"] == "toolResult"]
        self.assertEqual(len(tool_results), 2)
        self.assertTrue(tool_results[0]["ended_turn"])

    def test_queue_priority_runs_steering_before_follow_up_before_continuation(self) -> None:
        kernel = PersistentReplKernel()
        loop = MiniAgentLoop(kernel)
        seen_users: list[str] = []
        cont_fired = {"done": False}

        def model_fn(transcript: list[dict]) -> AssistantTurn:
            last_user = [m["text"] for m in transcript if m["role"] == "user"][-1]
            seen_users.append(last_user)
            if last_user == "init":
                loop.enqueue_follow_up("follow-up-1")
                loop.enqueue_steering("steer-1")
            return AssistantTurn(text=f"ack {last_user}")

        def continuation_fn(_transcript: list[dict]) -> list[str]:
            if not cont_fired["done"]:
                cont_fired["done"] = True
                return ["gate-retry-1"]
            return []

        loop.run("init", model_fn, continuation_fn=continuation_fn)
        self.assertEqual(seen_users, ["init", "steer-1", "follow-up-1", "gate-retry-1"])


if __name__ == "__main__":
    unittest.main()
