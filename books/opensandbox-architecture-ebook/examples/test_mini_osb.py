"""Offline unit tests verifying structural invariants modeled in mini_osb."""

import unittest

from mini_osb import (
    CreateRequest,
    CredentialVaultExchange,
    Egress,
    Event,
    Execd,
    PoolRuntime,
    Rule,
    SandboxError,
    Server,
    SingleHostRuntime,
    SnapshotMode,
    State,
    TemplateRuntime,
    create_service,
)


class LifecycleTest(unittest.TestCase):
    def test_create_is_pending_then_running(self):
        service = SingleHostRuntime()
        sandbox = service.create(CreateRequest(image="alpine"))
        self.assertEqual(sandbox.state, State.RUNNING)
        self.assertEqual(sandbox.env.get("EXECD_INIT"), "1")
        self.assertEqual(service.get(sandbox.id).id, sandbox.id)

    def test_missing_sandbox_is_404(self):
        with self.assertRaises(SandboxError) as caught:
            SingleHostRuntime().get("sbx-9999")
        self.assertEqual(caught.exception.status, 404)

    def test_pause_then_resume_keeps_id(self):
        service = SingleHostRuntime()
        sandbox = service.create(CreateRequest(image="alpine"))
        service.pause(sandbox.id)
        paused = service.get(sandbox.id)
        self.assertEqual(paused.state, State.PAUSED)
        self.assertEqual(paused.snapshot, f"snap-{sandbox.id}")
        resumed = service.resume(sandbox.id)
        self.assertEqual(resumed.id, sandbox.id)
        self.assertEqual(resumed.state, State.RUNNING)

    def test_resume_is_not_generic_restart(self):
        service = SingleHostRuntime()
        sandbox = service.create(CreateRequest(image="alpine"))
        service.delete(sandbox.id)
        with self.assertRaises(SandboxError) as caught:
            service.resume(sandbox.id)
        self.assertEqual(caught.exception.status, 404)

    def test_pause_twice_conflicts(self):
        service = SingleHostRuntime()
        sandbox = service.create(CreateRequest(image="alpine"))
        service.pause(sandbox.id)
        with self.assertRaises(SandboxError) as caught:
            service.pause(sandbox.id)
        self.assertEqual(caught.exception.status, 409)

    def test_batchsandbox_clean_exit_is_sticky_completed(self):
        service = PoolRuntime()
        sandbox = service.create(CreateRequest(image="alpine"))
        done = service.complete(sandbox.id, exit_code=0)
        self.assertEqual(done.phase, "Completed")
        self.assertEqual(done.state, State.TERMINATED)
        self.assertEqual(done.reason, "SANDBOX_COMPLETED")
        with self.assertRaises(SandboxError) as pause_err:
            service.pause(sandbox.id)
        self.assertEqual(pause_err.exception.code, "SANDBOX_COMPLETED")
        with self.assertRaises(SandboxError) as resume_err:
            service.resume(sandbox.id)
        self.assertEqual(resume_err.exception.code, "SANDBOX_COMPLETED")


class EndpointTest(unittest.TestCase):
    def test_single_host_multiplexes_ports_through_one_mapping(self):
        service = SingleHostRuntime()
        sandbox = service.create(CreateRequest(image="alpine"))
        first = service.endpoint(sandbox.id, 8080)["url"]
        second = service.endpoint(sandbox.id, 5900)["url"]
        self.assertIn("/proxy/8080", first)
        self.assertIn("/proxy/5900", second)
        self.assertEqual(first.split("/proxy/")[0], second.split("/proxy/")[0])

    def test_kubernetes_endpoint_carries_routing_header(self):
        service = PoolRuntime()
        sandbox = service.create(CreateRequest(image="alpine"))
        endpoint = service.endpoint(sandbox.id, 8080)
        self.assertEqual(endpoint["headers"]["OpenSandbox-Ingress-To"], f"{sandbox.id}-8080")

    def test_paused_sandbox_has_no_endpoint(self):
        service = SingleHostRuntime()
        sandbox = service.create(CreateRequest(image="alpine"))
        service.pause(sandbox.id)
        with self.assertRaises(SandboxError) as caught:
            service.endpoint(sandbox.id, 8080)
        self.assertEqual(caught.exception.status, 503)


class RoutingAndPoolTest(unittest.TestCase):
    def test_factory_selects_by_configuration_only(self):
        self.assertIsInstance(create_service("docker"), SingleHostRuntime)
        self.assertEqual(create_service("kubernetes").pod_backend.__class__, PoolRuntime)
        with self.assertRaises(ValueError):
            create_service("firecracker")

    def test_pool_is_bounded_capacity(self):
        service = PoolRuntime()
        service.create(CreateRequest(image="alpine"))
        service.create(CreateRequest(image="alpine"))
        with self.assertRaises(SandboxError) as caught:
            service.create(CreateRequest(image="alpine"))
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(caught.exception.code, "POOL_CAPACITY_EXHAUSTED")

    def test_released_pod_returns_to_pool(self):
        service = PoolRuntime()
        first = service.create(CreateRequest(image="alpine"))
        service.delete(first.id)
        again = service.create(CreateRequest(image="alpine"))
        self.assertEqual(again.state, State.RUNNING)

    def test_template_requests_reject_per_request_workload(self):
        service = TemplateRuntime()
        request = CreateRequest(image="ignored", metadata={"templateId": "t-1"}, entrypoint=["sh"])
        with self.assertRaises(SandboxError) as caught:
            service.create(request)
        self.assertEqual(caught.exception.status, 400)

    def test_composite_routes_by_create_source_and_id_prefix(self):
        service = create_service("kubernetes")
        by_template = service.create(CreateRequest(image="ignored", metadata={"templateId": "t-1"}))
        by_image = service.create(CreateRequest(image="alpine"))
        self.assertTrue(by_template.id.startswith("fsb-"))
        self.assertFalse(by_image.id.startswith("fsb-"))
        self.assertEqual(service.get(by_template.id).id, by_template.id)


class SnapshotModeTest(unittest.TestCase):
    def test_docker_and_k8s_reject_rootfs_only_with_409(self):
        docker = SingleHostRuntime()
        d_sbx = docker.create(CreateRequest(image="alpine"))
        with self.assertRaises(SandboxError) as d_err:
            docker.create_snapshot(d_sbx.id, mode=SnapshotMode.ROOTFS_ONLY)
        self.assertEqual(d_err.exception.status, 409)

        k8s = PoolRuntime()
        k_sbx = k8s.create(CreateRequest(image="alpine"))
        with self.assertRaises(SandboxError) as k_err:
            k8s.create_snapshot(k_sbx.id, mode=SnapshotMode.ROOTFS_ONLY)
        self.assertEqual(k_err.exception.status, 409)

    def test_docker_allows_full_snapshot_from_paused_sandbox(self):
        docker = SingleHostRuntime()
        sbx = docker.create(CreateRequest(image="alpine"))
        docker.pause(sbx.id)
        snap = docker.create_snapshot(sbx.id, mode=SnapshotMode.FULL)
        self.assertEqual(snap.mode, SnapshotMode.FULL)
        self.assertEqual(snap.artifact_path, "local-oci")

    def test_fsb_rootfs_only_covers_both_s3_and_oci_and_cold_boots(self):
        fsb = TemplateRuntime(node_rootfs_qualified=True)
        s3_sbx = fsb.create(CreateRequest(image="ignored", metadata={"templateId": "s3-base"}))
        s3_sbx.rootfs_files["/workspace/a.py"] = b"print(1)"
        s3_snap = fsb.create_snapshot(s3_sbx.id, mode=SnapshotMode.ROOTFS_ONLY)
        self.assertEqual(s3_snap.artifact_path, "s3-native")
        self.assertIsNone(s3_snap.memory_state)

        oci_sbx = fsb.create(CreateRequest(image="ignored", metadata={"templateId": "oci://reg/base@sha256:abc"}))
        oci_snap = fsb.create_snapshot(oci_sbx.id, mode=SnapshotMode.ROOTFS_ONLY)
        self.assertEqual(oci_snap.artifact_path, "overlaybd-oci")

        restored = fsb.create_from_snapshot(s3_snap.id)
        self.assertEqual(restored.rootfs_files["/workspace/a.py"], b"print(1)")
        self.assertEqual(restored.memory_state, {})
        self.assertEqual(restored.metadata["restoreType"], "cold-boot")

    def test_fsb_unqualified_node_fails_closed_instead_of_falling_back_to_full(self):
        fsb = TemplateRuntime(node_rootfs_qualified=False)
        sbx = fsb.create(CreateRequest(image="ignored", metadata={"templateId": "s3-base"}))
        with self.assertRaises(SandboxError) as err:
            fsb.create_snapshot(sbx.id, mode=SnapshotMode.ROOTFS_ONLY)
        self.assertEqual(err.exception.status, 409)
        self.assertEqual(err.exception.code, "NODE_UNQUALIFIED_FOR_ROOTFS_ONLY")
        self.assertEqual(len(fsb.snapshots), 0)


class ExpirationAndLockTest(unittest.TestCase):
    def test_expiration_is_absolute(self):
        clock = [1000.0]
        server = Server("docker", api_key=None, now=lambda: clock[0])
        sandbox = server.create(None, CreateRequest(image="alpine", timeout=30))
        server.service.pause(sandbox.id)
        clock[0] += 31
        self.assertEqual(server.expire(), [sandbox.id])

    def test_expired_sandbox_reports_terminated(self):
        clock = [1000.0]
        server = Server("docker", api_key=None, now=lambda: clock[0])
        sandbox = server.create(None, CreateRequest(image="alpine", timeout=10))
        clock[0] += 11
        self.assertEqual(server.get(None, sandbox.id).state, State.TERMINATED)

    def test_docker_renewal_serialized_against_expiration(self):
        docker = SingleHostRuntime()
        sbx = docker.create(CreateRequest(image="alpine", timeout=10, now=1000.0))
        # Renewal succeeds before expiry and pushes deadline forward.
        docker.renew_expiration(sbx.id, new_expires_at=1050.0, now=1005.0)
        self.assertFalse(docker.expire_if_due(sbx.id, now=1015.0))
        # Once expired and removed under lock, a late renewal fails cleanly with 404.
        self.assertTrue(docker.expire_if_due(sbx.id, now=1051.0))
        with self.assertRaises(SandboxError) as err:
            docker.renew_expiration(sbx.id, new_expires_at=1200.0, now=1052.0)
        self.assertEqual(err.exception.status, 404)

    def test_api_key_is_enforced(self):
        server = Server("kubernetes", api_key="k")
        with self.assertRaises(SandboxError) as caught:
            server.create("wrong", CreateRequest(image="alpine"))
        self.assertEqual(caught.exception.status, 401)


class DataPlaneAndEgressTest(unittest.TestCase):
    def test_command_stream_ends_with_exit_code(self):
        events = Execd().run(["echo", "hi"])
        self.assertEqual(events[-1].type, "execution_complete")
        self.assertEqual(events[-1].exit_code, 0)

    def test_nonzero_exit_is_reported(self):
        events = Execd().run(["false"])
        self.assertEqual(events[-1].exit_code, 1)

    def test_foreground_timeout_reports_command_timeout(self):
        events = Execd().run(["sleep", "30"], timeout_ms=100, simulated_ms=250)
        self.assertEqual(events[0].type, "error")
        self.assertEqual(events[0].error_name, "CommandTimeout")
        self.assertEqual(events[0].exit_code, -1)

    def test_execd_init_mode_reaps_orphans_and_shields_sigkill(self):
        execd = Execd(init_mode=True)
        execd.spawn_orphan(101)
        execd.spawn_orphan(102)
        self.assertEqual(execd.reap_orphans(), [101, 102])
        self.assertEqual(execd.deliver_signal("SIGKILL"), "shielded")
        self.assertEqual(execd.deliver_signal("SIGTERM"), "forwarded")
        self.assertEqual(execd.entrypoint_exit_code, 143)

    def test_upload_is_atomic_and_rejects_empty_path(self):
        execd = Execd()
        execd.write_file("/tmp/a", b"old")
        execd.write_file("/tmp/a", b"new")
        self.assertEqual(execd.read_file("/tmp/a"), b"new")
        self.assertNotIn("/tmp/a.stage", execd.files)
        with self.assertRaises(SandboxError) as err:
            execd.write_file("", b"bad")
        self.assertEqual(err.exception.status, 400)

    def test_platform_deny_beats_user_allow(self):
        egress = Egress(deny_always=[Rule("deny", "metadata.internal")])
        egress.add(Rule("allow", "metadata.internal"))
        self.assertEqual(egress.decide("metadata.internal"), "deny")

    def test_default_action_and_trailing_dot_normalization(self):
        egress = Egress()
        egress.add(Rule("allow", "pypi.org."), Rule("allow", "*.python.org."))
        self.assertEqual(egress.decide("pypi.org"), "allow")
        self.assertEqual(egress.decide("pypi.org."), "allow")
        self.assertEqual(egress.decide("files.python.org"), "allow")
        self.assertEqual(egress.decide("python.org"), "deny")
        self.assertEqual(egress.decide("example.com"), "deny")

    def test_vault_exchange_forwards_bounded_headers_without_persisting(self):
        seen: dict[str, str] = {}

        def resolver(sbx_id: str, headers: dict[str, str]) -> dict[str, str]:
            seen.update(headers)
            return {"Authorization": f"SigV4-for-{sbx_id}"}

        vault = CredentialVaultExchange(resolver)
        injected = vault.resolve_headers("sbx-1", {"X-Amz-Date": "20261119T000000Z", "Host": "s3.example.com"})
        self.assertEqual(injected["Authorization"], "SigV4-for-sbx-1")
        self.assertIn("X-Amz-Date", seen)
        self.assertNotIn("Host", seen)
        self.assertEqual(vault.persisted_secrets, {})

        too_many = {f"X-H-{i}": "v" for i in range(65)}
        with self.assertRaises(SandboxError) as err:
            vault.resolve_headers("sbx-1", too_many)
        self.assertEqual(err.exception.status, 407)

    def test_sse_frame_is_single_line_json(self):
        frame = Event("stdout", "a\nb").to_sse()
        self.assertTrue(frame.startswith("data: {"))
        self.assertEqual(frame.count("\n\n"), 1)


if __name__ == "__main__":
    unittest.main()
