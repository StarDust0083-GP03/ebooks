"""Unit tests verifying Infinity's core architectural invariants."""

from __future__ import annotations

import unittest

from mini_infinity import (
    ConversationStore,
    ErrorClass,
    HistoryManager,
    InfinityMessage,
    InputMessage,
    MiniInfinityRuntime,
    ProviderError,
    StackedSandboxManager,
    StateStore,
    StreamChunk,
    TOOL_CALL_INTERRUPTED_TEXT,
    TOOL_RESULT_TOO_LARGE_PLACEHOLDER,
    ThreadRef,
    ToolResultMatch,
    ToolSpec,
)


class TestThreadRef(unittest.TestCase):
    def test_parse_prefix_strip_and_sort_order(self) -> None:
        local_ref = ThreadRef.parse("t-2")
        remote_a = ThreadRef.parse("devbox/t-1")
        remote_b = ThreadRef.parse("gpu/t-0")
        self.assertIsNone(local_ref.remote)
        self.assertEqual(str(local_ref.prefixed("devbox")), "devbox/t-2")
        self.assertEqual(str(remote_a.strip("devbox")), "t-1")
        self.assertEqual(str(remote_a.strip("other")), "devbox/t-1")
        # Local sorts before remote regardless of lexicographic string order
        self.assertEqual(sorted([remote_b, remote_a, local_ref]), [local_ref, remote_a, remote_b])

    def test_invalid_thread_ref_rejected(self) -> None:
        for bad in ("", "/abc", "devbox/", "a/b/c"):
            with self.assertRaises(ValueError):
                ThreadRef.parse(bad)


class TestHistoryManagerThreePhase(unittest.TestCase):
    def test_sync_skips_unvalidated_and_enforces_sequentiality(self) -> None:
        conv, state = ConversationStore(), StateStore()
        hm = HistoryManager(conv, state, "root")
        self.assertTrue(hm.handle_content(InfinityMessage(kind="user", text="huge input"), "m1"))
        # sync() must NOT persist unvalidated inputs
        hm.sync()
        self.assertEqual(len(conv.load_history_up_to("root")), 0)
        # Committing model output before promoting unvalidated inputs must assert
        with self.assertRaises(AssertionError):
            hm.append_known_safe(InfinityMessage(kind="assistant", text="ok"), "c1")
        # Promoting on first model output unlocks sync()
        hm.mark_inputs_model_validated()
        hm.append_known_safe(InfinityMessage(kind="assistant", text="ok"), "c1")
        hm.sync()
        self.assertEqual(len(conv.load_history_up_to("root")), 2)
        self.assertIn("m1", state.get_processed_ids("root"))

    def test_turn_buffer_coalesces_text_trims_reasoning_and_discards(self) -> None:
        conv, state = ConversationStore(), StateStore()
        hm = HistoryManager(conv, state, "root")
        hm.handle_completion_chunk(StreamChunk(kind="text", text="hello "), "c1")
        hm.handle_completion_chunk(StreamChunk(kind="text", text="world"), "c2")
        hm.handle_completion_chunk(StreamChunk(kind="reasoning", text="trailing thought"), "c3")
        self.assertEqual(len(hm.current_turn_view()), 2)
        hm.flush_turn(trim_trailing_reasoning=True)
        self.assertEqual(len(hm.history), 1)
        self.assertEqual(hm.history[0].text, "hello world")

        # Discard wipes partial streamed turn on mid-stream error
        hm.handle_completion_chunk(StreamChunk(kind="text", text="half"), "c4")
        hm.discard_turn()
        self.assertEqual(len(hm.turn_buffer), 0)

    def test_tail_scan_tool_result_dedup_without_durable_id(self) -> None:
        conv, state = ConversationStore(), StateStore()
        hm = HistoryManager(conv, state, "root")
        hm.append_known_safe(
            InfinityMessage(kind="tool_call", tool_call_id="tc-1", tool_name="build"), "c1"
        )
        self.assertEqual(hm.match_tool_result("tc-1"), ToolResultMatch.UNANSWERED)
        self.assertTrue(
            hm.handle_content(InfinityMessage(kind="tool_result", tool_call_id="tc-1", text="ok"), "r1")
        )
        self.assertEqual(hm.match_tool_result("tc-1"), ToolResultMatch.ALREADY_ANSWERED)
        # Redelivery with different message_id is still deduplicated via history tail
        self.assertFalse(
            hm.handle_content(InfinityMessage(kind="tool_result", tool_call_id="tc-1", text="ok"), "r2")
        )
        # Tool result does NOT pollute durable StateStore processed_ids
        hm.mark_inputs_model_validated()
        hm.sync()
        self.assertNotIn("r1", state.get_processed_ids("root"))

    def test_overflow_recovery_single_tool_result_retries_once_then_drops(self) -> None:
        conv, state = ConversationStore(), StateStore()
        hm = HistoryManager(conv, state, "root")
        hm.append_known_safe(
            InfinityMessage(kind="tool_call", tool_call_id="tc-big", tool_name="grep"), "c1"
        )
        hm.handle_content(
            InfinityMessage(kind="tool_result", tool_call_id="tc-big", text="x" * 10000), "r1"
        )
        mode1, dropped1 = hm.recover_from_overflow()
        self.assertEqual((mode1, dropped1), ("retry", 0))
        self.assertEqual(hm.history[-1].text, TOOL_RESULT_TOO_LARGE_PLACEHOLDER)
        # Second overflow must NOT loop forever; falls through to interrupted + stop
        mode2, _ = hm.recover_from_overflow()
        self.assertEqual(mode2, "dropped")
        self.assertEqual(hm.history[-1].text, TOOL_CALL_INTERRUPTED_TEXT)

    def test_overflow_recovery_with_user_input_drops_and_forgets_dedup_id(self) -> None:
        conv, state = ConversationStore(), StateStore()
        hm = HistoryManager(conv, state, "root")
        hm.append_known_safe(
            InfinityMessage(kind="tool_call", tool_call_id="tc-1", tool_name="run"), "c1"
        )
        hm.handle_content(InfinityMessage(kind="tool_result", tool_call_id="tc-1", text="big"), "r1")
        hm.handle_content(InfinityMessage(kind="user", text="huge user paste"), "u1")
        mode, dropped = hm.recover_from_overflow()
        self.assertEqual((mode, dropped), ("dropped", 1))
        self.assertNotIn("u1", hm.processed_ids)
        self.assertEqual(hm.history[-1].text, TOOL_CALL_INTERRUPTED_TEXT)

    def test_safe_spawn_point_excludes_unanswered_calls_and_unvalidated_inputs(self) -> None:
        conv, state = ConversationStore(), StateStore()
        hm = HistoryManager(conv, state, "root")
        hm.handle_content(InfinityMessage(kind="user", text="hi"), "u1")
        hm.mark_inputs_model_validated()
        hm.append_known_safe(InfinityMessage(kind="assistant", text="hello"), "a1")
        hm.append_known_safe(
            InfinityMessage(kind="tool_call", tool_call_id="tc-open", tool_name="ci"), "a2"
        )
        self.assertEqual(hm.safe_spawn_point(), 2)
        # Adding unvalidated user input also stays excluded
        hm2 = HistoryManager(conv, state, "root2")
        hm2.append_known_safe(InfinityMessage(kind="assistant", text="committed"), "x1")
        hm2.handle_content(InfinityMessage(kind="user", text="not persisted"), "x2")
        self.assertEqual(hm2.safe_spawn_point(), 1)


class TestInheritanceAndCompaction(unittest.TestCase):
    def test_position_indexed_compaction_preserves_earlier_child_slice(self) -> None:
        conv = ConversationStore()
        conv.ensure_root_thread("root")
        conv.append_messages(
            "root",
            [(InfinityMessage(kind="user", text=f"m{i}"), f"id{i}") for i in range(6)],
        )
        child_before = conv.spawn_thread("root", "sp1", inherit_up_to=3, child_id="c-before")
        # Parent compacts up to order 5 later
        conv.save_compaction_summary("root", "summary-0-5", up_to_order=5)
        child_after = conv.spawn_thread("root", "sp2", inherit_up_to=6, child_id="c-after")

        hist_before, _, _ = conv.load_history_with_ancestors(child_before)
        self.assertEqual([m.text for m in hist_before], ["m0", "m1", "m2"])

        hist_after, _, _ = conv.load_history_with_ancestors(child_after)
        self.assertTrue(hist_after[0].text.startswith("[Compacted conversation summary]"))
        self.assertEqual(hist_after[1].text, "m5")

    def test_fresh_context_severs_ancestor_history_for_descendants(self) -> None:
        conv = ConversationStore()
        conv.ensure_root_thread("root")
        conv.append_messages("root", [(InfinityMessage(kind="user", text="secret"), "u0")])
        fresh_child = conv.spawn_thread("root", "sp1", fresh_context=True, child_id="fresh")
        conv.append_messages(fresh_child, [(InfinityMessage(kind="user", text="task"), "u1")])
        grandchild = conv.spawn_thread(fresh_child, "sp2", inherit_up_to=1, child_id="grand")

        h_fresh, _, _ = conv.load_history_with_ancestors(fresh_child)
        h_grand, _, _ = conv.load_history_with_ancestors(grandchild)
        self.assertEqual([m.text for m in h_fresh], ["task"])
        self.assertEqual([m.text for m in h_grand], ["task"])

    def test_find_tool_call_unwinds_past_compaction(self) -> None:
        conv, state = ConversationStore(), StateStore()
        conv.ensure_root_thread("root")
        conv.append_messages(
            "root",
            [
                (InfinityMessage(kind="tool_call", tool_call_id="sp-old", tool_name="spawn_thread"), "m0"),
                (InfinityMessage(kind="tool_result", tool_call_id="sp-old", text="ok"), "m1"),
                (InfinityMessage(kind="user", text="later"), "m2"),
            ],
        )
        conv.save_compaction_summary("root", "summarized", up_to_order=2)
        hm = HistoryManager(conv, state, "root")
        # Not in compacted in-memory view, but still resolved from persisted rows
        found = hm.find_tool_call("sp-old")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.tool_name, "spawn_thread")


class TestRuntimeSliceDeferralAndSubscriptions(unittest.TestCase):
    def test_commit_barrier_persists_tool_call_before_async_dispatch(self) -> None:
        def model(_tid: str, _hist: list[InfinityMessage]) -> list[StreamChunk]:
            return [
                StreamChunk(kind="tool_call", tool_call_id="tc-ci", tool_name="run_ci", tool_args={"cmd": "test"})
            ]

        rt = MiniInfinityRuntime(model)
        rt.register_tool(ToolSpec(name="run_ci"))
        rt.enqueue(InputMessage(group_id="t1", kind="user_text", text="run tests"), "u1")
        rt.drain_all()

        self.assertEqual(len(rt.dispatched_async_calls), 1)
        tid, tc_id, name, _, persisted_len = rt.dispatched_async_calls[0]
        self.assertEqual((tid, tc_id, name), ("t1", "tc-ci", "run_ci"))
        # Both user message and tool_call were persisted BEFORE dispatch
        self.assertEqual(persisted_len, 2)

    def test_sync_spawn_thread_validates_child_of_and_loops_back(self) -> None:
        calls = {"root": 0}

        def model(tid: str, hist: list[InfinityMessage]) -> list[StreamChunk]:
            if tid == "root":
                calls["root"] += 1
                if calls["root"] == 1:
                    return [
                        StreamChunk(
                            kind="tool_call",
                            tool_call_id="sp-1",
                            tool_name="spawn_thread",
                            tool_args={
                                "child_of": ["root"],
                                "instructions": "review auth.rs",
                                "child_id": "child-1",
                            },
                        )
                    ]
                return [StreamChunk(kind="text", text="spawned ok"), StreamChunk(kind="final")]
            # Child tries to re-spawn with parent's stack -> rejected!
            if not any(m.kind == "tool_result" and "Error: child_of" in m.text for m in hist):
                return [
                    StreamChunk(
                        kind="tool_call",
                        tool_call_id="bad-sp",
                        tool_name="spawn_thread",
                        tool_args={"child_of": ["root"], "instructions": "confused"},
                    )
                ]
            return [StreamChunk(kind="text", text="focused on own task"), StreamChunk(kind="final")]

        rt = MiniInfinityRuntime(model)
        rt.enqueue(InputMessage(group_id="root", kind="user_text", text="parallel review"), "u1")
        rt.drain_all()

        self.assertEqual(calls["root"], 2)  # looped back in same slice
        child_msgs = rt.conv.load_history_up_to("child-1")
        self.assertTrue(any("Error: child_of" in m.text for m in child_msgs))
        self.assertEqual(child_msgs[-1].text, "focused on own task")

    def test_defer_queue_holds_reports_during_active_tool_but_not_passive_sleep(self) -> None:
        def model(_tid: str, hist: list[InfinityMessage]) -> list[StreamChunk]:
            if hist[-1].kind == "user" and hist[-1].text == "run active":
                return [StreamChunk(kind="tool_call", tool_call_id="tc-act", tool_name="run_ci")]
            if hist[-1].kind == "user" and hist[-1].text == "go sleep":
                return [StreamChunk(kind="tool_call", tool_call_id="tc-slp", tool_name="sleep")]
            return [StreamChunk(kind="text", text="done"), StreamChunk(kind="final")]

        rt = MiniInfinityRuntime(model, use_deferral=True)
        rt.register_tool(ToolSpec(name="run_ci", is_passive=False))
        # Seed a spawn_thread call so thread_report resolves
        rt.conv.ensure_root_thread("t-active")
        rt.conv.append_messages(
            "t-active",
            [(InfinityMessage(kind="tool_call", tool_call_id="sp-0", tool_name="spawn_thread"), "s0"),
             (InfinityMessage(kind="tool_result", tool_call_id="sp-0", text="ok"), "s1")],
        )
        rt.enqueue(InputMessage(group_id="t-active", kind="user_text", text="run active"), "u1")
        rt.drain_all()

        # Report arrives while active tool is in flight -> deferred, NOT interrupting tc-act
        rt.enqueue(
            InputMessage(group_id="t-active", kind="thread_report", tool_call_id="sp-0", text="child done"),
            "rep1",
        )
        rt.drain_all()
        msgs_mid = rt.conv.load_history_up_to("t-active")
        self.assertEqual(msgs_mid[-1].kind, "tool_call")

        # Now real tool result arrives -> drains deferred report in order
        rt.enqueue(InputMessage(group_id="t-active", kind="tool_result", tool_call_id="tc-act", text="pass"), "res1")
        rt.drain_all()
        msgs_end = rt.conv.load_history_up_to("t-active")
        self.assertTrue(any(m.text == "child done" for m in msgs_end))
        self.assertFalse(any(m.text == TOOL_CALL_INTERRUPTED_TEXT for m in msgs_end))

        # Passive sleep tool IS interrupted immediately by report
        rt.conv.ensure_root_thread("t-sleep")
        rt.conv.append_messages(
            "t-sleep",
            [(InfinityMessage(kind="tool_call", tool_call_id="sp-s", tool_name="spawn_thread"), "s0"),
             (InfinityMessage(kind="tool_result", tool_call_id="sp-s", text="ok"), "s1")],
        )
        rt.enqueue(InputMessage(group_id="t-sleep", kind="user_text", text="go sleep"), "u2")
        rt.drain_all()
        rt.enqueue(
            InputMessage(group_id="t-sleep", kind="thread_report", tool_call_id="sp-s", text="wake!"),
            "rep2",
        )
        rt.drain_all()
        sleep_msgs = rt.conv.load_history_up_to("t-sleep")
        self.assertTrue(any(m.text == TOOL_CALL_INTERRUPTED_TEXT for m in sleep_msgs))

    def test_subscription_event_spawns_isolated_child_vs_associative_inline(self) -> None:
        seen_threads: list[str] = []

        def model(tid: str, hist: list[InfinityMessage]) -> list[StreamChunk]:
            seen_threads.append(tid)
            if hist[-1].kind == "user":
                return [StreamChunk(kind="tool_call", tool_call_id="sub-1", tool_name="watch")]
            return [StreamChunk(kind="text", text="handled"), StreamChunk(kind="final")]

        rt = MiniInfinityRuntime(model)
        rt.register_tool(ToolSpec(name="watch"))
        rt.enqueue(InputMessage(group_id="root", kind="user_text", text="watch pr"), "u1")
        rt.drain_all()
        rt.enqueue(
            InputMessage(
                group_id="root",
                kind="tool_result",
                tool_call_id="sub-1",
                text="subscribed",
                starts_subscription=True,
            ),
            "r1",
        )
        rt.drain_all()
        self.assertIn("sub-1", rt.state.active_subscriptions["root"])

        # Non-associative event spawns isolated child thread (parent history stays clean)
        parent_len_before = len(rt.conv.load_history_up_to("root"))
        rt.enqueue(
            InputMessage(
                group_id="root",
                kind="subscription_event",
                tool_call_id="sub-1",
                text="PR #42 opened",
                associative=False,
            ),
            "ev1",
        )
        rt.drain_all()
        self.assertEqual(len(rt.conv.load_history_up_to("root")), parent_len_before)
        self.assertTrue(any(t != "root" for t in seen_threads))

        # Associative + final event injects inline and cleans up active subscription
        rt.enqueue(
            InputMessage(
                group_id="root",
                kind="subscription_event",
                tool_call_id="sub-1",
                text="log line EOF",
                associative=True,
                final=True,
            ),
            "ev2",
        )
        rt.drain_all()
        self.assertNotIn("sub-1", rt.state.active_subscriptions["root"])
        self.assertTrue(any(m.text == "log line EOF" for m in rt.conv.load_history_up_to("root")))

    def test_admission_policy_drops_events_for_unknown_or_stopped_threads(self) -> None:
        rt = MiniInfinityRuntime(lambda _t, _h: [StreamChunk(kind="text", text="ok"), StreamChunk(kind="final")])
        rt.enqueue(InputMessage(group_id="ghost", kind="tool_result", tool_call_id="x", text="stale"), "m1")
        rt.drain_all()
        self.assertFalse(rt.conv.thread_exists("ghost"))
        self.assertEqual(rt.woken_threads, [])

        rt.enqueue(InputMessage(group_id="t1", kind="user_text", text="start"), "u1")
        rt.drain_all()
        rt.state.stopped_threads.add("t1")
        woken_before = len(rt.woken_threads)
        rt.enqueue(InputMessage(group_id="t1", kind="tool_result", tool_call_id="x", text="late"), "m2")
        rt.drain_all()
        self.assertEqual(len(rt.woken_threads), woken_before)
        # User text resumes stopped thread
        rt.enqueue(InputMessage(group_id="t1", kind="user_text", text="resume"), "u2")
        rt.drain_all()
        self.assertEqual(len(rt.woken_threads), woken_before + 1)

    def test_provider_error_class_drives_retry_vs_fatal(self) -> None:
        attempts = {"n": 0}

        def flaky_model(_tid: str, _hist: list[InfinityMessage]) -> list[StreamChunk]:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise ProviderError(ErrorClass.TRANSIENT, "socket reset")
            if attempts["n"] == 2:
                raise ProviderError(ErrorClass.THROTTLED, "rate limited")
            return [StreamChunk(kind="text", text="recovered"), StreamChunk(kind="final")]

        rt = MiniInfinityRuntime(flaky_model)
        rt.enqueue(InputMessage(group_id="t1", kind="user_text", text="hi"), "u1")
        rt.drain_all()
        self.assertEqual(attempts["n"], 3)
        self.assertEqual(rt.conv.load_history_up_to("t1")[-1].text, "recovered")

        fatal_rt = MiniInfinityRuntime(
            lambda _t, _h: (_ for _ in ()).throw(ProviderError(ErrorClass.FATAL, "unknown model"))
        )
        fatal_rt.enqueue(InputMessage(group_id="t1", kind="user_text", text="hi"), "u1")
        with self.assertRaises(ProviderError):
            fatal_rt.drain_all()


class TestStackedSandboxes(unittest.TestCase):
    def test_parallel_child_sandboxes_squash_cleanly_without_touching_host(self) -> None:
        mgr = StackedSandboxManager("/repo", {"app.py": "v1\n", "README.md": "init\n"})
        mgr.create_sandbox("root")
        mgr.create_sandbox("c1", base_thread_id="root")
        mgr.create_sandbox("c2", base_thread_id="root")

        mgr.edit_file("c1", "test_app.py", "assert True\n")
        mgr.edit_file("c2", "README.md", "updated\n")

        self.assertEqual(mgr.squash_sandbox("root", "c1"), [])
        self.assertEqual(mgr.squash_sandbox("root", "c2"), [])
        self.assertEqual(mgr.workspaces["root"].files["test_app.py"], "assert True\n")
        self.assertEqual(mgr.workspaces["root"].files["README.md"], "updated\n")
        # Host working copy remains completely untouched
        self.assertEqual(mgr.host_files, {"app.py": "v1\n", "README.md": "init\n"})

    def test_write_confinement_and_ancestor_write_orig_grant(self) -> None:
        mgr = StackedSandboxManager("/repo", {"app.py": "v1\n"})
        mgr.create_sandbox("root")
        mgr.create_sandbox("child", base_thread_id="root")

        # Inside own sandbox is allowed
        ok, reason = mgr.check_write_allowed(["root", "child"], "/repo/.infinity/.sandboxes/child/app.py")
        self.assertTrue(ok)
        self.assertEqual(reason, "inside_sandbox")

        # Writing to original repo requires user_choice approval
        ok2, reason2 = mgr.check_write_allowed(["root", "child"], "/repo/app.py")
        self.assertFalse(ok2)
        self.assertEqual(reason2, "needs_user_choice:write-orig")

        # Granting write-orig on parent propagates along ancestor chain to child
        mgr.grant_write_orig("root")
        ok3, reason3 = mgr.check_write_allowed(["root", "child"], "/repo/app.py")
        self.assertTrue(ok3)
        self.assertEqual(reason3, "write_orig_granted")


if __name__ == "__main__":
    unittest.main()
