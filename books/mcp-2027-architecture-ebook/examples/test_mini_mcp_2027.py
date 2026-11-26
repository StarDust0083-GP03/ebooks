"""Unit tests for the zero-dependency MCP 2027 teaching runtime."""

from __future__ import annotations

import base64
import json
import secrets
import unittest

from mini_mcp_2027 import (
    ERR_HEADER_MISMATCH,
    ERR_INVALID_PARAMS,
    ERR_METHOD_NOT_FOUND,
    ERR_MISSING_CAPABILITY,
    ERR_UNSUPPORTED_VERSION,
    PROTOCOL_VERSION,
    EventBroker,
    EventHarnessConsumer,
    McpListCache,
    McpProtocolError,
    MrtrReplica,
    OAuthResourceGuard,
    SkillHostLoader,
    SkillsServer,
    StatelessMcpServer,
    TasksManager,
    ToolSpec,
    decode_mcp_header_value,
    encode_mcp_header_value,
    sign_standard_webhook,
    simulate_code_mode,
    simulate_plain_tool_calling,
)


def _base_meta(version: str = PROTOCOL_VERSION, caps: dict | None = None) -> dict:
    return {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientInfo": {"name": "TestHarness", "version": "1.0.0"},
        "io.modelcontextprotocol/clientCapabilities": caps or {},
    }


def _http_headers(method: str, name: str | None = None, version: str = PROTOCOL_VERSION,
                  token: str | None = None, extra: dict[str, str] | None = None) -> dict[str, str]:
    h = {
        "MCP-Protocol-Version": version,
        "Mcp-Method": method,
    }
    if name is not None:
        h["Mcp-Name"] = encode_mcp_header_value(name)
    if token is not None:
        h["Authorization"] = f"Bearer {token}"
    if extra:
        h.update(extra)
    return h


class TestStatelessHttpAndHeaders(unittest.TestCase):
    def setUp(self) -> None:
        self.server = StatelessMcpServer("spanner-mcp")
        self.server.register_tool(
            ToolSpec(
                name="execute_sql",
                description="Run SQL in a target region",
                input_schema={
                    "type": "object",
                    "properties": {
                        "region": {"type": "string", "x-mcp-header": "Region"},
                        "query": {"type": "string"},
                    },
                    "required": ["region", "query"],
                },
                output_schema={
                    "type": "object",
                    "properties": {"rows": {"type": "integer"}},
                    "required": ["rows"],
                },
                x_mcp_headers={"region": "Region"},
                handler=lambda params, tok: {
                    "resultType": "complete",
                    "isError": False,
                    "content": [{"type": "text", "text": "2 rows"}],
                    "structuredContent": {"rows": 2, "region": params["arguments"]["region"]},
                },
            )
        )

    def test_01_header_base64_sentinel_roundtrip(self) -> None:
        cases = ["us-west1", "Hello, 世界", " padded ", "line1\nline2", "=?base64?literal?="]
        for original in cases:
            encoded = encode_mcp_header_value(original)
            if original == "us-west1":
                self.assertEqual(encoded, "us-west1")
            else:
                self.assertTrue(encoded.startswith("=?base64?"))
                self.assertTrue(encoded.endswith("?="))
            self.assertEqual(decode_mcp_header_value(encoded), original)

    def test_02_discover_and_unsupported_version_negotiation(self) -> None:
        body = {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": _base_meta()}}
        status, resp = self.server.handle_http_post(_http_headers("server/discover"), body)
        self.assertEqual(status, 200)
        self.assertEqual(resp["result"]["supportedVersions"], [PROTOCOL_VERSION])
        self.assertEqual(resp["result"]["ttlMs"], 300_000)

        bad_body = {"jsonrpc": "2.0", "id": 2, "method": "server/discover", "params": {"_meta": _base_meta("2025-11-25")}}
        status, resp = self.server.handle_http_post(_http_headers("server/discover", version="2025-11-25"), bad_body)
        self.assertEqual(status, 400)
        self.assertEqual(resp["error"]["code"], ERR_UNSUPPORTED_VERSION)
        self.assertEqual(resp["error"]["data"]["supported"], [PROTOCOL_VERSION])

    def test_03_header_body_mismatch_rejected_with_32020(self) -> None:
        body = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "execute_sql", "arguments": {"region": "us-west1", "query": "SELECT 1"}, "_meta": _base_meta()},
        }
        headers = _http_headers("tools/call", name="other_tool", extra={"Mcp-Param-Region": "us-west1"})
        status, resp = self.server.handle_http_post(headers, body)
        self.assertEqual(status, 400)
        self.assertEqual(resp["error"]["code"], ERR_HEADER_MISMATCH)

    def test_04_x_mcp_header_validation_passes_and_catches_spoofing(self) -> None:
        body = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "execute_sql", "arguments": {"region": "us-west1", "query": "SELECT 1"}, "_meta": _base_meta()},
        }
        ok_headers = _http_headers("tools/call", name="execute_sql", extra={"Mcp-Param-Region": "us-west1"})
        status, resp = self.server.handle_http_post(ok_headers, body)
        self.assertEqual(status, 200)
        self.assertEqual(resp["result"]["structuredContent"]["rows"], 2)

        spoof_headers = _http_headers("tools/call", name="execute_sql", extra={"Mcp-Param-Region": "eu-central1"})
        status, resp = self.server.handle_http_post(spoof_headers, body)
        self.assertEqual(status, 400)
        self.assertEqual(resp["error"]["code"], ERR_HEADER_MISMATCH)

    def test_05_unknown_method_returns_404_and_32601(self) -> None:
        body = {"jsonrpc": "2.0", "id": 5, "method": "custom/unknown", "params": {"_meta": _base_meta()}}
        status, resp = self.server.handle_http_post(_http_headers("custom/unknown"), body)
        self.assertEqual(status, 404)
        self.assertEqual(resp["error"]["code"], ERR_METHOD_NOT_FOUND)

    def test_06_sep1303_input_validation_error_is_tool_execution_error(self) -> None:
        body = {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "execute_sql", "arguments": {"region": "us-west1"}, "_meta": _base_meta()},
        }
        headers = _http_headers("tools/call", name="execute_sql", extra={"Mcp-Param-Region": "us-west1"})
        status, resp = self.server.handle_http_post(headers, body)
        self.assertEqual(status, 200)
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("query", resp["result"]["content"][0]["text"])


class TestListCacheAndOAuth(unittest.TestCase):
    def test_07_list_cache_ttl_public_vs_private_and_invalidation(self) -> None:
        cache = McpListCache()
        pub_result = {"resultType": "complete", "tools": [{"name": "a"}], "ttlMs": 5000, "cacheScope": "public"}
        cache.put("tools/list", "", pub_result, now_ms=1000, auth_context="user:alice")
        # Public cache can be shared across principals before expiry
        self.assertIsNotNone(cache.get("tools/list", "", now_ms=4000, auth_context="user:bob"))
        # Expired at t=6000
        self.assertIsNone(cache.get("tools/list", "", now_ms=6000, auth_context="user:alice"))

        priv_result = {"resultType": "complete", "contents": ["secret"], "ttlMs": 5000, "cacheScope": "private"}
        cache.put("resources/read", "file:///secret", priv_result, now_ms=1000, auth_context="user:alice")
        self.assertIsNotNone(cache.get("resources/read", "file:///secret", now_ms=2000, auth_context="user:alice"))
        # Private cache MUST NOT leak to another auth context
        self.assertIsNone(cache.get("resources/read", "file:///secret", now_ms=2000, auth_context="user:bob"))
        # Immediate invalidation via list_changed / updated notification
        cache.invalidate_method("resources/read")
        self.assertIsNone(cache.get("resources/read", "file:///secret", now_ms=2000, auth_context="user:alice"))

    def test_08_oauth21_audience_binding_and_step_up_scope_challenge(self) -> None:
        guard = OAuthResourceGuard(
            canonical_resource_uri="https://mcp.example.com/mcp",
            metadata_url="https://mcp.example.com/.well-known/oauth-protected-resource",
        )
        server = StatelessMcpServer("files-mcp", auth_guard=guard)
        server.register_tool(
            ToolSpec(
                name="write_file",
                description="Write a file",
                input_schema={"type": "object", "required": ["path"]},
                output_schema=None,
                required_scopes={"files:write"},
                handler=lambda p, tok: {
                    "resultType": "complete",
                    "isError": False,
                    "content": [{"type": "text", "text": f"written by {tok.subject}"}],
                },
            )
        )
        body = {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "/a.txt"}, "_meta": _base_meta()},
        }
        # Wrong audience token rejected with 401 (RFC 8707)
        wrong_aud = guard.issue_token("alice", "https://other.example.com/mcp", {"files:write"}, expires_at=2000.0)
        status, _ = server.handle_http_post(_http_headers("tools/call", "write_file", token=wrong_aud), body, now_s=1000.0)
        self.assertEqual(status, 401)

        # Insufficient scope triggers 403 step-up challenge
        read_only = guard.issue_token("alice", "https://mcp.example.com/mcp", {"files:read"}, expires_at=2000.0)
        status, resp = server.handle_http_post(_http_headers("tools/call", "write_file", token=read_only), body, now_s=1000.0)
        self.assertEqual(status, 403)
        self.assertIn('error="insufficient_scope"', resp["www_authenticate"])
        self.assertIn('scope="files:write"', resp["www_authenticate"])

        # Upgraded token succeeds
        upgraded = guard.issue_token("alice", "https://mcp.example.com/mcp", {"files:read", "files:write"}, expires_at=2000.0)
        status, resp = server.handle_http_post(_http_headers("tools/call", "write_file", token=upgraded), body, now_s=1000.0)
        self.assertEqual(status, 200)
        self.assertIn("alice", resp["result"]["content"][0]["text"])


class TestCodeModeComparison(unittest.TestCase):
    def test_09_code_mode_slashes_turns_and_context_bytes(self) -> None:
        issues = [
            {
                "identifier": f"CORE-{i}",
                "title": f"Issue {i}",
                "priority": 1 if i <= 3 else 3,
                "body": "x" * 600,
            }
            for i in range(1, 21)
        ]
        sentry = {
            f"CORE-{i}": {"count24h": i * 17, "p95Ms": 420 + i, "stacktrace": "trace\n" * 40}
            for i in range(1, 21)
        }
        plain = simulate_plain_tool_calling(issues, sentry)
        code = simulate_code_mode(issues, sentry)

        self.assertEqual(plain.joined_rows, code.joined_rows)
        self.assertEqual(plain.model_turns, 4)  # 1 list + 3 sentry lookups
        self.assertEqual(code.model_turns, 1)
        self.assertLess(code.context_bytes_ingested * 20, plain.context_bytes_ingested)


class TestMrtrStatelessReplicas(unittest.TestCase):
    def setUp(self) -> None:
        shared_key = b"cluster-hmac-secret-2027"
        self.replica_a = MrtrReplica("replica-a", shared_key)
        self.replica_b = MrtrReplica("replica-b", shared_key)

    def test_10_mrtr_roundtrip_resumes_on_different_replica(self) -> None:
        args = {"version": "v2.4.0"}
        step1 = self.replica_a.call_deploy_tool(req_id=1, principal="alice", arguments=args, now_s=100.0)
        self.assertEqual(step1["resultType"], "input_required")
        self.assertIn("confirm_prod", step1["inputRequests"])

        # Load balancer routes retry to replica_b with zero shared session memory
        step2 = self.replica_b.call_deploy_tool(
            req_id=2,
            principal="alice",
            arguments=args,
            now_s=120.0,
            request_state=step1["requestState"],
            input_responses={"confirm_prod": {"action": "accept", "content": {"approved": True}}},
        )
        self.assertEqual(step2["resultType"], "complete")
        self.assertTrue(step2["structuredContent"]["deployed"])
        self.assertEqual(step2["structuredContent"]["handledBy"], "replica-b")

    def test_11_mrtr_rejects_tampered_state_wrong_principal_expired_and_same_id(self) -> None:
        args = {"version": "v2.4.0"}
        step1 = self.replica_a.call_deploy_tool(req_id=10, principal="alice", arguments=args, now_s=100.0)
        state = step1["requestState"]
        responses = {"confirm_prod": {"action": "accept", "content": {"approved": True}}}

        # Tampered state
        with self.assertRaises(ValueError):
            self.replica_b.call_deploy_tool(11, "alice", args, 120.0, state[:-2] + "AA", responses)
        # Cross-principal replay
        with self.assertRaises(ValueError):
            self.replica_b.call_deploy_tool(11, "mallory", args, 120.0, state, responses)
        # Expired state (> 300s)
        with self.assertRaises(ValueError):
            self.replica_b.call_deploy_tool(11, "alice", args, 500.0, state, responses)
        # Parameter swap attack
        with self.assertRaises(ValueError):
            self.replica_b.call_deploy_tool(11, "alice", {"version": "v9.9.9-evil"}, 120.0, state, responses)
        # Reused JSON-RPC id
        with self.assertRaises(ValueError):
            self.replica_b.call_deploy_tool(10, "alice", args, 120.0, state, responses)

    def test_12_mrtr_rejects_client_without_elicitation_capability(self) -> None:
        with self.assertRaises(McpProtocolError) as ctx:
            self.replica_a.call_deploy_tool(
                req_id=1, principal="alice", arguments={"version": "v1"}, now_s=100.0, client_supports_elicitation=False
            )
        self.assertEqual(ctx.exception.code, ERR_MISSING_CAPABILITY)


class TestSkillsOverMcp(unittest.TestCase):
    def setUp(self) -> None:
        self.server = SkillsServer("jira-mcp")
        self.server.register_skill(
            skill_path="jira/triage",
            name="triage",
            description="Route engineering bug tickets",
            body="# Jira Triage\nRead `references/rules.md`.",
            extra_files={"references/rules.md": b"P0 goes to on-call.\n"},
            listed=True,
        )
        self.server.register_skill(
            skill_path="internal/postmortem",
            name="postmortem",
            description="Unlisted postmortem playbook",
            body="# Postmortem",
            listed=False,
        )
        self.host = SkillHostLoader()

    def test_13_skills_list_and_unlisted_skills_get(self) -> None:
        listed = self.server.skills_list()
        self.assertEqual(len(listed["skills"]), 1)
        self.assertEqual(listed["skills"][0]["uri"], "skill://jira/triage/SKILL.md")

        # Unlisted skill still retrievable via skills/get
        unlisted = self.server.skills_get("skill://internal/postmortem/SKILL.md")
        self.assertEqual(unlisted["skill"]["frontmatter"]["name"], "postmortem")

        # Unknown skill returns -32602
        with self.assertRaises(McpProtocolError) as ctx:
            self.server.skills_get("skill://missing/SKILL.md")
        self.assertEqual(ctx.exception.code, ERR_INVALID_PARAMS)

    def test_14_skills_digest_verification_and_approval_revocation_on_update(self) -> None:
        entry = self.server.skills_list()["skills"][0]
        self.host.approve_skill("jira-mcp", entry)
        rules_uri = "skill://jira/triage/references/rules.md"
        data = self.host.load_file(self.server, entry, rules_uri)
        self.assertEqual(data, b"P0 goes to on-call.\n")

        # Server updates supporting file -> stale entry digest mismatch caught
        self.server.update_file("skill://jira/triage/SKILL.md", rules_uri, b"P0 goes to sec-team.\n")
        fresh_entry = self.server.skills_get("skill://jira/triage/SKILL.md")["skill"]
        # Old approval no longer matches fresh manifest
        self.assertFalse(self.host.is_approved("jira-mcp", fresh_entry))
        with self.assertRaises(PermissionError):
            self.host.load_file(self.server, fresh_entry, rules_uri)

        # Re-approve fresh manifest -> updated content loads cleanly
        self.host.approve_skill("jira-mcp", fresh_entry)
        updated = self.host.load_file(self.server, fresh_entry, rules_uri)
        self.assertEqual(updated, b"P0 goes to sec-team.\n")

    def test_15_skills_blocks_read_outside_retained_manifest(self) -> None:
        entry = self.server.skills_list()["skills"][0]
        self.host.approve_skill("jira-mcp", entry)
        with self.assertRaises(PermissionError):
            self.host.load_file(self.server, entry, "skill://internal/postmortem/SKILL.md")


class TestMcpTasksExtension(unittest.TestCase):
    def setUp(self) -> None:
        self.tasks = TasksManager()
        self.caps = {"extensions": {"io.modelcontextprotocol/tasks": {}}}

    def test_16_task_requires_capability_declaration(self) -> None:
        with self.assertRaises(McpProtocolError) as ctx:
            self.tasks.create_task("alice", {}, now_ms=1000)
        self.assertEqual(ctx.exception.code, ERR_MISSING_CAPABILITY)

    def test_17_task_lifecycle_midflight_input_and_terminal_immutability(self) -> None:
        created = self.tasks.create_task("alice", self.caps, now_ms=1000, ttl_ms=30_000)
        self.assertEqual(created["resultType"], "task")
        tid = created["taskId"]
        self.assertEqual(len(tid), 32)  # 128-bit hex

        self.tasks.request_midflight_input(tid, "approve_step", {"method": "elicitation/create"}, now_ms=2000)
        state = self.tasks.tasks_get(tid, "alice", now_ms=2500)
        self.assertEqual(state["status"], "input_required")
        self.assertIn("approve_step", state["inputRequests"])

        self.tasks.tasks_update(tid, "alice", {"approve_step": {"action": "accept"}}, now_ms=3000)
        self.assertEqual(self.tasks.tasks_get(tid, "alice", now_ms=3100)["status"], "working")

        self.tasks.complete_task(tid, {"content": [{"type": "text", "text": "done"}]}, now_ms=4000)
        done = self.tasks.tasks_get(tid, "alice", now_ms=4100)
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["result"]["content"][0]["text"], "done")

        # Terminal state cannot be overwritten
        with self.assertRaises(ValueError):
            self.tasks.complete_task(tid, {"content": []}, now_ms=4200)

    def test_18_task_owner_isolation_and_ttl_expiry(self) -> None:
        created = self.tasks.create_task("alice", self.caps, now_ms=1000, ttl_ms=5000)
        tid = created["taskId"]
        with self.assertRaises(PermissionError):
            self.tasks.tasks_get(tid, "bob", now_ms=2000)

        expired = self.tasks.tasks_get(tid, "alice", now_ms=9000)
        self.assertEqual(expired["status"], "failed")
        self.assertIn("expired", expired["error"]["message"].lower())


class TestMcpEventsExtension(unittest.TestCase):
    def setUp(self) -> None:
        self.broker = EventBroker(retention_limit=3)
        self.secret = "whsec_" + base64.b64encode(secrets.token_bytes(32)).decode("ascii")

    def test_19_webhook_ssrf_and_secret_validation(self) -> None:
        # Reject private/metadata IP (SSRF)
        with self.assertRaises(McpProtocolError):
            self.broker.events_subscribe(
                "alice", "security.alert.opened", {"severity": "p0"},
                "https://169.254.169.254/hook", "169.254.169.254", self.secret, now_s=1000.0
            )
        # Reject plain HTTP
        with self.assertRaises(McpProtocolError):
            self.broker.events_subscribe(
                "alice", "security.alert.opened", {"severity": "p0"},
                "http://hooks.example.com/cb", "93.184.216.34", self.secret, now_s=1000.0
            )
        # Reject malformed secret without whsec_ prefix
        with self.assertRaises(McpProtocolError):
            self.broker.events_subscribe(
                "alice", "security.alert.opened", {"severity": "p0"},
                "https://hooks.example.com/cb", "93.184.216.34", "bad-secret", now_s=1000.0
            )

    def test_20_webhook_hmac_verification_eventid_dedup_and_continuation_modes(self) -> None:
        sub = self.broker.events_subscribe(
            "alice", "security.alert.opened", {"severity": "p0"},
            "https://hooks.example.com/cb", "93.184.216.34", self.secret, now_s=1000.0
        )
        occ = self.broker.publish_upstream_event(
            "evt_9821", "security.alert.opened", {"alertId": "SEC-412"}, "2026-11-24T02:14:00Z"
        )
        raw_body = json.dumps(occ).encode("utf-8")
        sig = sign_standard_webhook(self.secret, "msg_1", 1010, raw_body)

        consumer = EventHarnessConsumer()
        # Valid delivery accepted
        traj1 = consumer.ingest_webhook(sub["id"], self.secret, "msg_1", 1010, sig, raw_body, now_s=1020, continuation_mode="same_thread")
        self.assertEqual(traj1, f"thread:{sub['id']}")
        self.assertEqual(consumer.cursors[sub["id"]], "cur_00001")

        # Duplicate eventId on same subscription is ignored
        dup = consumer.ingest_webhook(sub["id"], self.secret, "msg_2", 1025, sign_standard_webhook(self.secret, "msg_2", 1025, raw_body), raw_body, now_s=1030)
        self.assertIsNone(dup)

        # Same eventId on a DIFFERENT subscription is NOT treated as duplicate (SEP-3415)
        other_traj = consumer.ingest_webhook("sub_other", self.secret, "msg_3", 1025, sign_standard_webhook(self.secret, "msg_3", 1025, raw_body), raw_body, now_s=1030, continuation_mode="isolated")
        self.assertEqual(other_traj, "isolated:sub_other:evt_9821")

        # Tampered body rejected
        with self.assertRaises(PermissionError):
            consumer.ingest_webhook(sub["id"], self.secret, "msg_1", 1010, sig, raw_body + b" ", now_s=1020)

    def test_21_poll_cursor_and_truncated_gap_detection(self) -> None:
        # Retention limit is 3; publish 5 events so cur_00001 falls out of window
        for i in range(1, 6):
            self.broker.publish_upstream_event(f"evt_{i}", "deploy.succeeded", {"run": i}, f"2026-11-24T02:0{i}:00Z")
        res = self.broker.events_poll("deploy.succeeded", cursor="cur_00001", max_events=2)
        self.assertTrue(res["truncated"])
        self.assertTrue(res["hasMore"])
        self.assertEqual(len(res["events"]), 2)
        self.assertEqual(res["cursor"], "cur_00004")

        res2 = self.broker.events_poll("deploy.succeeded", cursor=res["cursor"], max_events=2)
        self.assertFalse(res2["truncated"])
        self.assertFalse(res2["hasMore"])
        self.assertEqual(len(res2["events"]), 1)
        self.assertEqual(res2["events"][0]["eventId"], "evt_5")


if __name__ == "__main__":
    unittest.main()
