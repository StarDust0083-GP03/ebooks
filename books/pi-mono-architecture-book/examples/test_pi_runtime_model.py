"""Unit tests verifying Pi v1.6.0 core architectural invariants."""
from __future__ import annotations

import unittest

from pi_runtime_model import (
    CodeModeRuntime,
    RegisteredTool,
    SchemaValidationError,
    StaticTextView,
    StreamingJsonToolParser,
    ToolExecutionView,
    ToolRegistry,
    ToolSpec,
    TreeSession,
    TuiContainer,
    WorkerAdmissionPool,
    ZonedAssistantView,
    coerce_and_validate,
)


class TestTreeSessionProjection(unittest.TestCase):
    def test_branching_isolates_history_and_store(self) -> None:
        session = TreeSession()
        session.append_message("user", "inspect repo")
        common = session.append_store_commit({"inspected": 1})

        # Branch A commits inspected=2
        session.append_store_commit({"inspected": 2})
        self.assertEqual(session.replay_store(), {"inspected": 2})

        # Branch B forks from common node and sees inspected=1
        session.branch_from(common.id)
        session.append_message("user", "try another route")
        self.assertEqual(session.replay_store(), {"inspected": 1})

    def test_compaction_and_context_edit_preserve_original_branch(self) -> None:
        session = TreeSession()
        m1 = session.append_message("user", "old turn")
        m2 = session.append_message("assistant", "old answer")
        m3 = session.append_message("user", "keep me")
        m4 = session.append_message("assistant", "secret token leak")
        session.append_compaction("Summary of earlier turns", keep_from_id=m3.id)
        session.append_context_edit(m4.id, "[redacted]")

        projected = session.project_context()
        self.assertEqual([m["role"] for m in projected], ["summary", "user", "assistant"])
        self.assertEqual(projected[0]["content"], "Summary of earlier turns")
        self.assertEqual(projected[2]["content"], "[redacted]")
        # Original entry m1 and m4 still intact in tree history.
        self.assertEqual(session.active_branch()[0].id, m1.id)
        self.assertEqual(session.active_branch()[3].payload["content"], "secret token leak")

    def test_truncated_tool_call_drops_orphan_tool_result(self) -> None:
        session = TreeSession()
        session.append_message("user", "run task")
        session.append_message(
            "assistant",
            "partial",
            tool_calls=[{"id": "call_cut", "name": "read"}],
            truncated=True,
        )
        session.append_message("tool_result", "stale result", tool_call_id="call_cut")
        session.append_message("assistant", "recovered answer")

        projected = session.project_context()
        roles = [m["role"] for m in projected]
        self.assertNotIn("tool_result", roles)
        self.assertEqual(projected[-1]["content"], "recovered answer")


class TestCodeModeBoundaryAndWorkers(unittest.TestCase):
    def test_pi_schema_coercion_and_dotted_error_format(self) -> None:
        schema = {
            "properties": {
                "path": {"type": "string"},
                "limit": {"type": "number"},
                "recursive": {"type": "boolean"},
                "tags": {"type": "array"},
                "note": {"type": "string"},
            },
            "required": ["path"],
        }
        parsed = coerce_and_validate(
            schema,
            {"path": "README.md", "limit": "25", "recursive": "true", "tags": "docs", "note": None},
        )
        self.assertEqual(parsed, {"path": "README.md", "limit": 25, "recursive": True, "tags": ["docs"]})

        with self.assertRaises(SchemaValidationError) as ctx:
            coerce_and_validate(schema, {"limit": "not-a-number"})
        self.assertIn("- path: missing", str(ctx.exception))
        self.assertIn("- limit: expected number, got string", str(ctx.exception))

    def test_extension_hook_blocks_nested_tool_call(self) -> None:
        session = TreeSession()
        tools = {
            "bash": ToolSpec(
                name="bash",
                schema={"properties": {"command": {"type": "string"}}, "required": ["command"]},
                execute=lambda args: {"exit_code": 0, "output": args["command"]},
            )
        }
        runtime = CodeModeRuntime(
            session,
            tools,
            before_tool_call=lambda name, args: "dangerous rm blocked" if "rm -rf" in args["command"] else None,
        )
        outcome = runtime.run_script(lambda env: env["execute_tool"]("bash", {"command": "rm -rf /tmp/x"}))
        self.assertFalse(outcome["ok"])
        self.assertIn("dangerous rm blocked", outcome["error"])
        self.assertEqual(runtime.call_log, [])

    def test_failed_script_rolls_back_store_but_keeps_file_side_effect(self) -> None:
        session = TreeSession()
        session.append_store_commit({"inspected": 2})
        disk: dict[str, str] = {}
        tools = {
            "write": ToolSpec(
                name="write",
                schema={"properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
                execute=lambda args: disk.__setitem__(args["path"], args["content"]) or "ok",
            )
        }
        runtime = CodeModeRuntime(session, tools)

        def failing_script(env: dict) -> None:
            env["execute_tool"]("write", {"path": "disposable.txt", "content": "written"})
            env["store"]("inspected", 99)
            raise RuntimeError("script crashed mid-way")

        outcome = runtime.run_script(failing_script)
        self.assertFalse(outcome["ok"])
        self.assertEqual(session.replay_store(), {"inspected": 2})
        self.assertEqual(disk["disposable.txt"], "written")

    def test_worker_pool_fifo_wait_consumes_total_deadline(self) -> None:
        pool = WorkerAdmissionPool(max_workers=2)
        l1 = pool.acquire(deadline_ms=10_000)
        l2 = pool.acquire(deadline_ms=10_000)
        self.assertEqual(pool.active_count, 2)

        # Waiting 8s out of a 10s total deadline leaves 2s for execution.
        pool.release(l1)
        # Re-fill to test wait path deterministically.
        l1b = pool.acquire(deadline_ms=10_000)
        l3 = pool.acquire(deadline_ms=10_000, wait_until_ms=8_000)
        self.assertEqual(l3.wait_ms, 8_000)
        self.assertEqual(l3.remaining_deadline_ms, 2_000)

        # Release l2 and l1b; double-release is idempotent.
        pool.release(l2)
        pool.release(l2)
        pool.release(l1b)
        pool.release(l3)
        self.assertEqual(pool.active_count, 0)

    def test_worker_pool_rejects_when_wait_exceeds_budget(self) -> None:
        pool = WorkerAdmissionPool(max_workers=1)
        pool.acquire(deadline_ms=10_000)
        with self.assertRaises(TimeoutError):
            pool.acquire(deadline_ms=10_000, wait_until_ms=12_000)


class TestStreamingJsonAndTuiCache(unittest.TestCase):
    def test_streaming_json_incremental_escapes_and_finish(self) -> None:
        parser = StreamingJsonToolParser()
        parser.append('{"path":"src/a')
        self.assertEqual(parser.snapshot(), {"path": "src/a"})
        # Split escape sequence across deltas: hold back backslash until next char arrives.
        parser.append('\\')
        self.assertEqual(parser.snapshot(), {"path": "src/a"})
        parser.append('nb.ts","limit":42}')
        self.assertEqual(parser.snapshot(), {"path": "src/a\nb.ts", "limit": 42})
        final = parser.finish()
        self.assertIsNone(final["arguments_error"])
        self.assertEqual(final["arguments"], {"path": "src/a\nb.ts", "limit": 42})

    def test_streaming_json_copy_credit_throttles_huge_open_container(self) -> None:
        parser = StreamingJsonToolParser()
        # Open a large array of 1000 items to make open_path_cost > 1000.
        payload = '{"items":[' + ",".join(str(i) for i in range(1000))
        parser.append(payload)
        first = parser.snapshot()
        self.assertIsNotNone(first)
        # Drain credit then feed tiny 2-char deltas; snapshot should lag instead of copying O(N) every time.
        parser._credit = 0
        copies_before = parser.snapshot_copies
        parser.append(",1")
        snap = parser.snapshot()
        self.assertIs(snap, first)
        self.assertEqual(parser.snapshot_copies, copies_before)

    def test_streaming_json_finish_reports_malformed_input(self) -> None:
        parser = StreamingJsonToolParser()
        parser.append('{"path": "unclosed')
        final = parser.finish()
        self.assertIsNotNone(final["arguments_error"])
        self.assertEqual(final["arguments"], {})

    def test_container_render_returns_cached_identity_when_unchanged(self) -> None:
        c = TuiContainer()
        t1 = StaticTextView("hello world")
        t2 = StaticTextView("second line")
        c.add_child(t1)
        c.add_child(t2)

        frame1 = c.render(80)
        frame2 = c.render(80)
        self.assertIs(frame1, frame2)
        self.assertEqual(c.rebuilds, 1)
        self.assertEqual(t1.render_computes, 1)

    def test_container_invalidates_on_width_or_child_change(self) -> None:
        c = TuiContainer()
        t1 = StaticTextView("hello")
        c.add_child(t1)

        f80 = c.render(80)
        f40 = c.render(40)
        self.assertIsNot(f80, f40)
        self.assertEqual(c.rebuilds, 2)

        t1.set_text("updated")
        f40_b = c.render(40)
        self.assertIsNot(f40, f40_b)
        self.assertEqual(f40_b, ("updated",))
        self.assertEqual(c.rebuilds, 3)

    def test_nested_container_skips_ancestor_rebuilds(self) -> None:
        leaf = StaticTextView("stable block")
        inner = TuiContainer()
        inner.add_child(leaf)
        outer = TuiContainer()
        outer.add_child(inner)

        f1 = outer.render(80)
        f2 = outer.render(80)
        self.assertIs(f1, f2)
        self.assertEqual(inner.rebuilds, 1)
        self.assertEqual(outer.rebuilds, 1)

    def test_tool_execution_view_coalesces_deltas_per_frame(self) -> None:
        view = ToolExecutionView("write")
        for i in range(50):
            view.update_args({"path": "out.ts", "bytes": i})
        self.assertEqual(view.render_requests, 50)
        self.assertEqual(view.format_runs, 0)

        lines1 = view.render(80)
        lines2 = view.render(80)
        self.assertIs(lines1, lines2)
        self.assertEqual(view.format_runs, 1)
        self.assertIn('"bytes": 49', lines1[0])

    def test_zoned_assistant_copies_without_mutating_super_cache(self) -> None:
        view = ZonedAssistantView()
        msg = StaticTextView("answer line")
        view.add_child(msg)

        z1 = view.render(80)
        z2 = view.render(80)
        self.assertIs(z1, z2)
        self.assertEqual(view.zone_copies, 1)
        # Underlying cached container output remains unmodified.
        self.assertEqual(view._cache.lines, ("answer line",))  # type: ignore[union-attr]
        self.assertTrue(z1[0].startswith("<OSC133:A>"))


class TestMcpExposureAndDiscovery(unittest.TestCase):
    def _build_registry(self) -> ToolRegistry:
        reg = ToolRegistry()
        reg.register(RegisteredTool("read", "Read local files", "direct", "builtin"))
        reg.register(RegisteredTool("codemode", "Run JS script", "model-only", "extension"))
        reg.register(RegisteredTool("mcp__docs__search", "搜索技术文档记录", "codemode", "mcp", "docs"))
        reg.register(RegisteredTool("mcp__peekaboo__app", "Inspect macOS app window", "deferred", "mcp", "peekaboo"))
        reg.register(RegisteredTool("mcp__helper__mention", "Mentions peekaboo app in notes", "deferred", "mcp", "helper"))
        reg.register(RegisteredTool("mcp__internal__secret", "Hidden admin action", "hidden", "mcp", "internal"))
        return reg

    def test_exposure_matrix_and_codemode_only_mode(self) -> None:
        reg = self._build_registry()
        self.assertEqual(reg.declared_for_model(codemode_mode="on"), ["codemode", "read"])
        self.assertEqual(reg.declared_for_model(codemode_mode="only"), ["codemode"])
        # Hidden and model-only are excluded from script callable set.
        callable_tools = reg.callable_from_script()
        self.assertIn("mcp__docs__search", callable_tools)
        self.assertIn("mcp__peekaboo__app", callable_tools)
        self.assertNotIn("mcp__internal__secret", callable_tools)
        self.assertNotIn("codemode", callable_tools)

    def test_cli_tools_allowlist_does_not_remove_mcp_without_no_mcp(self) -> None:
        reg = self._build_registry()
        # --tools read,codemode keeps MCP callable unless --no-mcp is passed.
        self.assertIn("mcp__docs__search", reg.callable_from_script(no_mcp=False))
        self.assertNotIn("mcp__docs__search", reg.callable_from_script(no_mcp=True))

    def test_search_tools_prioritizes_name_match_and_handles_cjk(self) -> None:
        reg = self._build_registry()
        hits = reg.search_tools("peekaboo app")
        self.assertEqual(hits[0], "mcp__peekaboo__app")

        cjk_hits = reg.search_tools("搜索记录")
        self.assertEqual(cjk_hits, ["mcp__docs__search"])
        # Hidden tools never appear in search results.
        self.assertEqual(reg.search_tools("secret admin"), [])

    def test_tool_search_loads_deferred_only_and_reaches_tools_connects_lazily(self) -> None:
        reg = self._build_registry()
        loaded = reg.tool_search("peekaboo app", limit=1)
        self.assertEqual(loaded, ["mcp__peekaboo__app"])
        self.assertIn("mcp__peekaboo__app", reg.declared_for_model())

        # Direct script call only connects the referenced MCP server.
        reach_single = reg.reaches_tools('await tools.mcp__docs__search({ query: "tree" })')
        self.assertEqual(reach_single, {"docs"})
        connected = reg.ensure_servers_connected(reach_single)
        self.assertEqual(connected, {"docs"})
        self.assertNotIn("peekaboo", reg.connected_servers)

        # Discovery call triggers connecting all lazy MCP servers.
        reach_all = reg.reaches_tools('await searchTools("window")')
        self.assertEqual(reach_all, "all")
        reg.ensure_servers_connected(reach_all)
        self.assertTrue({"docs", "peekaboo", "helper", "internal"}.issubset(reg.connected_servers))


if __name__ == "__main__":
    unittest.main()
