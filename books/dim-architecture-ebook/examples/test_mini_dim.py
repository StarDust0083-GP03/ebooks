#!/usr/bin/env python3
"""Unit tests verifying arcships/dim core architectural invariants via mini_dim."""

from __future__ import annotations

import unittest

from mini_dim import (
    INTERPRETER_ID,
    ROOT_BRANCH_ID,
    ApprovalCapability,
    DimError,
    Dispatcher,
    EventDraft,
    RecoverySnapshot,
    Timeline,
    TimelineTree,
    project_context,
)


class TestTimeBusAndDispatcher(unittest.TestCase):
    def test_01_sequence_is_monotonic_and_batch_end_marks_boundary(self) -> None:
        tl = Timeline(ROOT_BRANCH_ID)
        batch = tl.append_batch(
            [
                EventDraft("prompt", "changed", {"rev": 1}),
                EventDraft("context", "configured", {"model": "demo"}),
            ]
        )
        self.assertEqual([e.sequence for e in batch], [1, 2])
        self.assertFalse(batch[0].batch_end)
        self.assertTrue(batch[1].batch_end)

    def test_02_reserved_domains_reject_extension_appends(self) -> None:
        tl = Timeline(ROOT_BRANCH_ID)
        with self.assertRaises(DimError) as ctx:
            tl.append_extension(EventDraft("approval", "request", {"approval_id": "x"}))
        self.assertEqual(ctx.exception.code, "reserved_domain")

        ev = tl.append_extension(EventDraft("acme_plugin", "recorded", {"ok": True}))
        self.assertEqual(ev.key, "acme_plugin/recorded")
        self.assertEqual(ev.sequence, 1)

    def test_03_before_hook_transforms_draft_and_records_trace(self) -> None:
        tl = Timeline(ROOT_BRANCH_ID)

        def rewrite(draft: EventDraft) -> EventDraft:
            draft.data["text"] = draft.data["text"].upper()
            return draft

        tl.register_before("uppercase", ("input/message",), rewrite)
        ev = tl.append(EventDraft("input", "message", {"text": "hello"}))
        self.assertEqual(ev.data["text"], "HELLO")
        self.assertEqual(len(ev.trace), 1)
        self.assertEqual(ev.trace[0].handler, "uppercase")

    def test_04_before_hook_failure_commits_original_draft(self) -> None:
        tl = Timeline(ROOT_BRANCH_ID)

        def boom(_draft: EventDraft) -> EventDraft:
            raise RuntimeError("hook crashed")

        tl.register_before("broken", ("input/*",), boom)
        ev = tl.append(EventDraft("input", "message", {"text": "keep-me"}))
        self.assertEqual(ev.data["text"], "keep-me")
        self.assertEqual(ev.trace[0].handler, "broken")
        self.assertIsNone(ev.trace[0].result_data)

    def test_05_register_callback_failure_aborts_entire_batch(self) -> None:
        tl = Timeline(ROOT_BRANCH_ID)

        def reject(_events) -> None:
            raise DimError("duplicate_id", "already reserved")

        with self.assertRaises(DimError) as ctx:
            tl.append_batch(
                [EventDraft("branch", "create", {"branch": "b-1"})],
                register=reject,
            )
        self.assertEqual(ctx.exception.code, "duplicate_id")
        self.assertEqual(tl.committed(), 0)
        self.assertEqual(tl.published(), 0)

    def test_06_committed_and_published_watermarks_stay_distinct(self) -> None:
        tl = Timeline(ROOT_BRANCH_ID, auto_publish=False)
        dp = Dispatcher(tl)
        seen: list[int] = []
        dp.subscribe("watcher", ("*",), lambda ev: seen.append(ev.sequence))

        tl.append(EventDraft("input", "message", {"text": "queued"}))
        self.assertEqual(tl.committed(), 1)
        self.assertEqual(tl.published(), 0)
        self.assertEqual(seen, [])
        self.assertEqual(len(tl.query(include_publishing=False)), 0)
        self.assertEqual(len(tl.query(include_publishing=True)), 1)

        tl.flush_publish()
        self.assertEqual(tl.published(), 1)
        self.assertEqual(seen, [1])

    def test_07_dispatcher_consume_holds_live_facts_and_delivers_above_cutoff(self) -> None:
        tl = Timeline(ROOT_BRANCH_ID)
        dp = Dispatcher(tl)
        tl.append(EventDraft("input", "message", {"text": "before-1"}))
        tl.append(EventDraft("input", "message", {"text": "before-2"}))

        rebuilt_seqs: list[int] = []
        live_seqs: list[int] = []

        def rebuild(t: Timeline) -> int:
            cutoff = t.published()
            for ev in t.query(through=cutoff):
                rebuilt_seqs.append(ev.sequence)
            # Simulate a live event arriving during rebuild.
            t.append(EventDraft("input", "message", {"text": "during-rebuild"}))
            return cutoff

        dp.consume("consumer", ("input/*",), rebuild, lambda ev: live_seqs.append(ev.sequence))
        tl.append(EventDraft("input", "message", {"text": "after"}))

        self.assertEqual(rebuilt_seqs, [1, 2])
        self.assertEqual(live_seqs, [3, 4])


class TestBranchTreeAndCap(unittest.TestCase):
    def test_08_only_root_and_default_branch_can_open_tool_lines(self) -> None:
        tree = TimelineTree(max_open_lines=4)
        b1 = tree.open_branch(ROOT_BRANCH_ID, "tool", "investigate A")
        # Non-default tool line cannot open a child.
        with self.assertRaises(DimError) as ctx:
            tree.open_branch(b1, "tool", "nested")
        self.assertEqual(ctx.exception.code, "branch_not_opener")

        # Promote b1 to default branch; now both main and b1 may open.
        tree.set_default_branch(b1)
        b2 = tree.open_branch(b1, "tool", "from default")
        b3 = tree.open_branch(ROOT_BRANCH_ID, "tool", "still allowed from root")
        self.assertIn(b2, tree.nodes)
        self.assertIn(b3, tree.nodes)

    def test_09_protected_path_excluded_from_cap_and_switch_back_counts_all(self) -> None:
        tree = TimelineTree(max_open_lines=2)
        a = tree.open_branch(ROOT_BRANCH_ID, "tool", "A")
        self.assertEqual(tree.counted_tool_lines(), 1)
        tree.set_default_branch(a)
        # Moving default to A protects A, so counted drops from 1 to 0.
        self.assertEqual(tree.counted_tool_lines(), 0)

        b = tree.open_branch(a, "tool", "B")
        self.assertEqual(tree.counted_tool_lines(), 1)
        tree.set_default_branch(b)
        # Both A and B are now on the protected path main -> A -> B.
        self.assertEqual(tree.counted_tool_lines(), 0)

        # Open one more child C while cap allows it.
        c = tree.open_branch(b, "tool", "C")
        self.assertEqual(tree.counted_tool_lines(), 1)

        # Switch back to root: A, B, C all become counted (3 > cap 2).
        # Switch closes nothing, but next create is refused.
        tree.set_default_branch(ROOT_BRANCH_ID)
        self.assertEqual(tree.counted_tool_lines(), 3)
        self.assertFalse(tree.nodes[a].ended)
        self.assertFalse(tree.nodes[b].ended)
        self.assertFalse(tree.nodes[c].ended)
        with self.assertRaises(DimError) as ctx:
            tree.open_branch(ROOT_BRANCH_ID, "tool", "D")
        self.assertEqual(ctx.exception.code, "branch_limit_reached")

    def test_10_failed_build_releases_cap_slot_and_records_create_failed(self) -> None:
        tree = TimelineTree(max_open_lines=1)
        with self.assertRaises(DimError) as ctx:
            tree.open_branch(ROOT_BRANCH_ID, "tool", "broken", fail_build=True)
        self.assertEqual(ctx.exception.code, "branch_build_failed")
        self.assertEqual(tree.counted_tool_lines(), 0)

        root_events = tree.nodes[ROOT_BRANCH_ID].timeline.query()
        self.assertEqual([e.key for e in root_events], ["branch/create", "branch/create-failed"])

        ok_id = tree.open_branch(ROOT_BRANCH_ID, "tool", "succeeds")
        self.assertEqual(tree.counted_tool_lines(), 1)
        self.assertIn(ok_id, tree.nodes)

    def test_11_user_and_plugin_lines_never_spend_tool_cap_and_plugin_cannot_be_default(self) -> None:
        tree = TimelineTree(max_open_lines=1)
        u1 = tree.open_branch(ROOT_BRANCH_ID, "user", "user side track")
        p1 = tree.open_branch(ROOT_BRANCH_ID, "plugin", "goal evaluator")
        self.assertEqual(tree.counted_tool_lines(), 0)
        with self.assertRaises(DimError) as ctx:
            tree.set_default_branch(p1)
        self.assertEqual(ctx.exception.code, "branch_not_selectable")
        tree.set_default_branch(u1)
        self.assertEqual(tree.resolve_target(None), u1)

    def test_12_ending_root_default_or_ancestor_is_refused_and_cascade_ends_children(self) -> None:
        tree = TimelineTree(max_open_lines=4)
        a = tree.open_branch(ROOT_BRANCH_ID, "tool", "A")
        tree.set_default_branch(a)
        b = tree.open_branch(a, "tool", "B")
        tree.set_default_branch(b)

        for protected in (ROOT_BRANCH_ID, a, b):
            with self.assertRaises(DimError) as ctx:
                tree.end_branch(protected, caller_source="user")
            self.assertEqual(ctx.exception.code, "branch_cannot_end")

        # Move default back to root, then ending A cascades to B first.
        tree.set_default_branch(ROOT_BRANCH_ID)
        tree.end_branch(a, caller_source="tool")
        self.assertTrue(tree.nodes[b].ended)
        self.assertTrue(tree.nodes[a].ended)

    def test_13_kinship_messaging_and_read_cursor_enforcement(self) -> None:
        tree = TimelineTree(max_open_lines=4)
        tree.nodes[ROOT_BRANCH_ID].timeline.append(EventDraft("input", "consumed", {"text": "pre-cut"}))
        c1 = tree.open_branch(ROOT_BRANCH_ID, "tool", "C1")
        c2 = tree.open_branch(ROOT_BRANCH_ID, "tool", "C2")
        tree.nodes[ROOT_BRANCH_ID].timeline.append(EventDraft("input", "consumed", {"text": "post-cut"}))

        # Siblings cannot message or read each other.
        with self.assertRaises(DimError) as ctx:
            tree.message_branch(c1, c2, "hi sibling")
        self.assertEqual(ctx.exception.code, "branch_not_kin")
        with self.assertRaises(DimError) as ctx:
            tree.read_branch(c1, c2)
        self.assertEqual(ctx.exception.code, "branch_not_kin")

        # Child reading parent starts after its cut (even if after=0 is requested).
        page, cursor = tree.read_branch(c1, ROOT_BRANCH_ID, after=0)
        self.assertTrue(all(ev.sequence > tree.nodes[c1].cut for ev in page))
        self.assertGreater(cursor, tree.nodes[c1].cut)

        # Next read without `after` continues from cursor and returns empty until new events.
        page2, cursor2 = tree.read_branch(c1, ROOT_BRANCH_ID)
        self.assertEqual(page2, [])
        self.assertEqual(cursor2, cursor)

    def test_14_default_branch_restores_from_root_and_resets_after_session_forked(self) -> None:
        tree = TimelineTree(max_open_lines=4)
        a = tree.open_branch(ROOT_BRANCH_ID, "tool", "A")
        tree.set_default_branch(a)
        self.assertEqual(tree.restore_default_branch_from_root(), a)

        # A session/forked fact after branch/default resets default to root.
        tree.nodes[ROOT_BRANCH_ID].timeline.append(EventDraft("session", "forked", {"from": "s-0"}))
        self.assertEqual(tree.restore_default_branch_from_root(), ROOT_BRANCH_ID)


class TestApprovalAndCompactionReplay(unittest.TestCase):
    def test_15_approval_first_decision_wins_under_concurrent_clients(self) -> None:
        tl = Timeline(ROOT_BRANCH_ID)
        dp = Dispatcher(tl)
        approval = ApprovalCapability(tl, dp)
        runs: list[str] = []

        approval.request("req-1", "exec", "shell command", on_allow=lambda: runs.append("ran"))
        approval.respond("req-1", "deny")
        approval.respond("req-1", "allow")  # Late racing client must lose.

        self.assertEqual(approval.resolved["req-1"], "deny")
        self.assertEqual(runs, [])
        self.assertEqual(approval.executed_side_effects, [])

    def test_16_stop_interrupt_cancels_pending_approval_and_prevents_late_allow(self) -> None:
        tl = Timeline(ROOT_BRANCH_ID)
        dp = Dispatcher(tl)
        approval = ApprovalCapability(tl, dp)
        runs: list[str] = []

        approval.request("req-2", "write", "outside workspace", on_allow=lambda: runs.append("ran"))
        tl.append(EventDraft("stop", "interrupt", {"scope": "turn", "reason": "user-esc"}))
        approval.respond("req-2", "allow")

        self.assertEqual(approval.resolved["req-2"], "cancelled")
        self.assertEqual(runs, [])
        cancelled = tl.query(keys=("approval/cancelled",))
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(cancelled[0].data["approval_id"], "req-2")

    def test_17_restart_rebuild_reconstructs_state_without_replaying_side_effects(self) -> None:
        store: dict[str, list] = {}
        tl1 = Timeline(ROOT_BRANCH_ID, store=store)
        dp1 = Dispatcher(tl1)
        gen1 = ApprovalCapability(tl1, dp1)
        gen1.request("req-3", "exec", "ls")
        gen1.respond("req-3", "allow")
        self.assertEqual(gen1.executed_side_effects, ["req-3"])

        # Simulate daemon restart / capability reload with the same durable store.
        tl2 = Timeline(ROOT_BRANCH_ID, store=store)
        dp2 = Dispatcher(tl2)
        gen2 = ApprovalCapability(tl2, dp2)
        self.assertEqual(gen2.resolved["req-3"], "allow")
        self.assertEqual(gen2.pending, {})
        self.assertEqual(gen2.executed_side_effects, [])

    def test_18_compaction_snapshot_accelerates_projection_and_falls_back_on_corruption(self) -> None:
        tl = Timeline(ROOT_BRANCH_ID)
        tl.append(EventDraft("input", "consumed", {"text": "turn-1"}))
        tl.append(EventDraft("input", "consumed", {"text": "turn-2"}))
        comp = tl.append(
            EventDraft(
                "context",
                "compaction",
                {"status": "completed", "cursor": 2, "summary": "condensed 1..2"},
            )
        )
        tl.append(EventDraft("input", "consumed", {"text": "turn-3"}))

        good_snap = RecoverySnapshot.build(
            anchor_event=comp,
            interpreter=INTERPRETER_ID,
            resume_sequence=3,
            summary="condensed 1..2",
            messages_before_resume=[],
        )
        tl.save_snapshot(good_snap)

        msgs, used_snap = project_context(tl)
        self.assertTrue(used_snap)
        self.assertEqual(msgs, ["[summary] condensed 1..2", "turn-3"])

        # Corrupt the latest snapshot digest: projection must fall back to full replay
        # while keeping the durable compaction summary intact.
        bad_snap = RecoverySnapshot(
            anchor_sequence=comp.sequence,
            anchor_event_id=comp.id,
            interpreter=INTERPRETER_ID,
            resume_sequence=3,
            summary="tampered",
            messages_before_resume=(),
            digest="0" * 64,
        )
        tl.save_snapshot(bad_snap)

        msgs_fallback, used_snap_fallback = project_context(tl)
        self.assertFalse(used_snap_fallback)
        self.assertEqual(msgs_fallback, ["[summary] condensed 1..2", "turn-3"])


if __name__ == "__main__":
    unittest.main()
