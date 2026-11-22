# 附录 C：配套教学实现

以下代码由构建器从 `examples/` 目录直接同步，完全基于 Python 3.10+ 标准库实现。它复刻了本书讨论的核心机制：生命周期服务、三种后端路由、单主机端口 `/proxy/{port}`、Pool 预热与 `Completed` 终态、Docker 续期竞态锁、`rootfsOnly` 与 `full` 快照准入、`EXECD_INIT=1` 进程树管理、egress 尾点归一化与 Credential Vault Exchange。用于离线核验结构不变量，不能替代真实 OpenSandbox 平台。

## mini_osb.py

```python
#!/usr/bin/env python3
"""mini_osb: A zero-dependency standard-library model of OpenSandbox v1.3.0-rc.2.

This teaching implementation models the core architectural invariants discussed in
the book without running real containers, Kubernetes clusters, or Firecracker VMs:

    Server (API + factory)            -> server/opensandbox_server/services/factory.py
    SandboxService interface          -> server/opensandbox_server/services/sandbox_service.py
    Prefix-based Composite routing    -> server/opensandbox_server/services/composite_service.py
    Single host port + /proxy/{port}  -> components/execd built-in reverse proxy
    Pool warm buffer & Completed      -> kubernetes/internal/controller/pool_controller.go
    Docker per-sandbox lifecycle lock -> server/opensandbox_server/services/docker/docker_service.py
    RootfsOnly vs Full snapshots      -> server/opensandbox_server/services/fast_sandbox/snapshot_runtime.py
    execd --init & CommandTimeout     -> components/execd/pkg/runtime/initmode_linux.go
    Egress rules & Vault Exchange     -> components/egress/pkg/policy/ & oseps/0025

Run:  python3 examples/mini_osb.py
Test: python3 -m unittest discover -s examples -v
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable


class State(str, Enum):
    PENDING = "Pending"
    RUNNING = "Running"
    PAUSING = "Pausing"
    PAUSED = "Paused"
    RESUMING = "Resuming"
    STOPPING = "Stopping"
    TERMINATED = "Terminated"
    FAILED = "Failed"


class SnapshotMode(str, Enum):
    FULL = "full"
    ROOTFS_ONLY = "rootfsOnly"


class SandboxError(Exception):
    """Unified service-layer exception carrying HTTP-style status and code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message


@dataclass
class Sandbox:
    id: str
    image: str
    state: State
    expires_at: float | None
    entrypoint: list[str]
    env: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)
    node: str | None = None
    data_plane: tuple[str, int] | None = None
    snapshot: str | None = None
    phase: str | None = None
    reason: str | None = None
    message: str | None = None
    rootfs_files: dict[str, bytes] = field(default_factory=dict)
    memory_state: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SnapshotRecord:
    id: str
    source_sandbox_id: str
    backend: str
    mode: SnapshotMode
    artifact_path: str  # "local-oci" | "registry-oci" | "s3-native" | "overlaybd-oci"
    rootfs_files: dict[str, bytes]
    memory_state: dict[str, str] | None


# --------------------------------------------------------------------------
# Data plane: execd model with OSEP-0018 init mode, SSE events, and timeouts.
# --------------------------------------------------------------------------

@dataclass
class Event:
    """Models execd ServerStreamEvent (type, text, exit_code, error_name)."""

    type: str
    text: str = ""
    exit_code: int | None = None
    error_name: str | None = None

    def to_sse(self) -> str:
        payload: dict[str, object] = {"type": self.type}
        if self.text:
            payload["text"] = self.text
        if self.exit_code is not None:
            payload["exit_code"] = self.exit_code
        if self.error_name is not None:
            payload["ename"] = self.error_name
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class Execd:
    """In-sandbox daemon modeling commands, staged uploads, and EXECD_INIT=1."""

    FORWARDED_SIGNALS = frozenset({"SIGTERM", "SIGHUP", "SIGUSR1", "SIGUSR2", "SIGWINCH"})
    MAX_SCAN_LINE_BYTES = 16 * 1024 * 1024

    def __init__(self, init_mode: bool = True) -> None:
        self.init_mode = init_mode
        self.files: dict[str, bytes] = {}
        self.env: dict[str, str] = {"EXECD_INIT": "1"} if init_mode else {}
        self.orphans: list[int] = []
        self.reaped_pids: list[int] = []
        self.forwarded_signals: list[str] = []
        self.entrypoint_exit_code: int | None = None

    def spawn_orphan(self, pid: int) -> None:
        self.orphans.append(pid)

    def reap_orphans(self) -> list[int]:
        """Under EXECD_INIT=1, a single reaper collects reparented orphans."""
        if not self.init_mode:
            return []
        reaped = list(self.orphans)
        self.reaped_pids.extend(reaped)
        self.orphans.clear()
        return reaped

    def deliver_signal(self, sig: str) -> str:
        """PID 1 shields in-namespace SIGKILL, while forwarding SIGTERM/HUP/USR*."""
        if self.init_mode and sig == "SIGKILL":
            return "shielded"
        if self.init_mode and sig in self.FORWARDED_SIGNALS:
            self.forwarded_signals.append(sig)
            if sig == "SIGTERM":
                self.entrypoint_exit_code = 143
            return "forwarded"
        return "ignored"

    def run(self, argv: list[str], *, timeout_ms: int | None = None, simulated_ms: int = 0) -> list[Event]:
        if not argv:
            return [Event("error", "argv must not be empty", error_name="CommandExecError"), Event("execution_complete", exit_code=2)]
        if timeout_ms is not None and simulated_ms > timeout_ms:
            return [
                Event("error", f"command timed out after {timeout_ms}ms", exit_code=-1, error_name="CommandTimeout"),
            ]
        out = " ".join(argv)
        if argv[0] == "false":
            return [Event("stderr", "command failed\n"), Event("execution_complete", exit_code=1)]
        if argv[0] == "ls":
            body = "\n".join(sorted(self.files)) or ""
            return [Event("stdout", body + ("\n" if body else "")), Event("execution_complete", exit_code=0)]
        return [Event("stdout", f"{out}\n"), Event("execution_complete", exit_code=0)]

    def write_file(self, path: str, data: bytes) -> None:
        if not path or not path.strip():
            raise SandboxError(400, "INVALID_PATH", "empty path is rejected")
        staged = f"{path}.stage"
        self.files[staged] = data
        self.files[path] = self.files.pop(staged)

    def read_file(self, path: str) -> bytes:
        if path not in self.files:
            raise SandboxError(404, "FILE_NOT_FOUND", path)
        return self.files[path]


# --------------------------------------------------------------------------
# Egress policy evaluation & Credential Vault Exchange (OSEP-0025).
# --------------------------------------------------------------------------

def _normalize_host(host: str) -> str:
    """Normalize trailing root dot (e.g. 'pypi.org.' -> 'pypi.org')."""
    return host.strip().lower().rstrip(".")


@dataclass(frozen=True)
class Rule:
    action: str  # "allow" | "deny"
    target: str  # "pypi.org" / "*.pypi.org" / "pypi.org."

    def matches(self, host: str) -> bool:
        norm_target = _normalize_host(self.target)
        norm_host = _normalize_host(host)
        if norm_target.startswith("*."):
            return norm_host.endswith(norm_target[1:])
        return norm_host == norm_target


class Egress:
    """Policy precedence: platform deny.always > platform allow.always > user rules."""

    def __init__(self, deny_always: Iterable[Rule] = (), allow_always: Iterable[Rule] = ()) -> None:
        self.deny_always = list(deny_always)
        self.allow_always = list(allow_always)
        self.rules: list[Rule] = []
        self.default_action = "deny"

    def add(self, *rules: Rule) -> None:
        self.rules.extend(rules)

    def decide(self, host: str) -> str:
        for rule in self.deny_always:
            if rule.matches(host):
                return "deny"
        for rule in self.allow_always:
            if rule.matches(host):
                return "allow"
        for rule in self.rules:
            if rule.matches(host):
                return rule.action
        return self.default_action


class CredentialVaultExchange:
    """Models OSEP-0025 per-request header-bound dynamic credential resolution."""

    MAX_HEADERS = 64
    MAX_VALUE_BYTES = 8 * 1024
    HOP_BY_HOP_DENYLIST = frozenset({"connection", "proxy-authorization", "transfer-encoding", "host"})

    def __init__(self, resolver: Callable[[str, dict[str, str]], dict[str, str]]) -> None:
        self._resolver = resolver
        self.persisted_secrets: dict[str, str] = {}  # always empty by invariant

    def resolve_headers(self, sandbox_id: str, outbound_headers: dict[str, str]) -> dict[str, str]:
        forwarded: dict[str, str] = {}
        for k, v in outbound_headers.items():
            if k.lower() in self.HOP_BY_HOP_DENYLIST:
                continue
            if len(v.encode("utf-8")) > self.MAX_VALUE_BYTES:
                raise SandboxError(407, "VAULT_HEADER_TOO_LARGE", f"header {k} exceeds 8 KiB")
            forwarded[k] = v
        if len(forwarded) > self.MAX_HEADERS:
            raise SandboxError(407, "VAULT_TOO_MANY_HEADERS", "exceeds 64 forwarded headers")
        # Resolve per request without caching or persisting secrets in the vault.
        return self._resolver(sandbox_id, forwarded)


# --------------------------------------------------------------------------
# Control plane: SandboxService interface and three runtime backends.
# --------------------------------------------------------------------------

@dataclass
class CreateRequest:
    image: str
    timeout: float | None = 600.0
    entrypoint: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)
    now: float = field(default_factory=time.time)

    def expires_at(self) -> float | None:
        return None if self.timeout is None else self.now + self.timeout


class SandboxService:
    """Unified lifecycle interface; API routes never branch on runtime internals."""

    id_prefix = ""
    backend_name = "generic"

    def __init__(self) -> None:
        self.sandboxes: dict[str, Sandbox] = {}
        self.snapshots: dict[str, SnapshotRecord] = {}
        self._snap_seq = 0

    def ready(self, sandbox: Sandbox) -> None:
        sandbox.state = State.RUNNING
        sandbox.phase = "Succeed"
        sandbox.reason = "Ready"
        sandbox.message = "sandbox is running"

    def create(self, request: CreateRequest) -> Sandbox:
        raise NotImplementedError

    def get(self, sandbox_id: str) -> Sandbox:
        sandbox = self.sandboxes.get(sandbox_id)
        if sandbox is None:
            raise SandboxError(404, "SANDBOX_NOT_FOUND", sandbox_id)
        return sandbox

    def list(self) -> list[Sandbox]:
        return sorted(self.sandboxes.values(), key=lambda s: s.id)

    def delete(self, sandbox_id: str) -> None:
        sandbox = self.get(sandbox_id)
        sandbox.state = State.STOPPING
        self.sandboxes.pop(sandbox_id)

    def pause(self, sandbox_id: str) -> None:
        sandbox = self.get(sandbox_id)
        if sandbox.phase == "Completed":
            raise SandboxError(409, "SANDBOX_COMPLETED", f"cannot pause completed sandbox {sandbox_id}")
        if sandbox.state == State.PAUSED:
            raise SandboxError(409, "SANDBOX_ALREADY_PAUSED", sandbox_id)
        if sandbox.state != State.RUNNING:
            raise SandboxError(409, "SANDBOX_NOT_RUNNING", f"{sandbox_id} is {sandbox.state.value}")
        sandbox.state = State.PAUSING
        # Pause/resume checkpoints remain full-only.
        sandbox.snapshot = f"snap-{sandbox_id}"
        sandbox.state = State.PAUSED
        sandbox.phase = "Paused"
        sandbox.node = None
        sandbox.data_plane = None

    def resume(self, sandbox_id: str) -> Sandbox:
        sandbox = self.get(sandbox_id)
        if sandbox.phase == "Completed":
            raise SandboxError(409, "SANDBOX_COMPLETED", f"cannot resume completed sandbox {sandbox_id}")
        if sandbox.state != State.PAUSED:
            raise SandboxError(409, "SANDBOX_NOT_PAUSED", f"{sandbox_id} is {sandbox.state.value}")
        if sandbox.snapshot is None:
            raise SandboxError(409, "SANDBOX_SNAPSHOT_MISSING", sandbox_id)
        sandbox.state = State.RESUMING
        self._restore(sandbox)
        self.ready(sandbox)
        return sandbox

    def create_snapshot(self, sandbox_id: str, mode: SnapshotMode = SnapshotMode.FULL) -> SnapshotRecord:
        sandbox = self.get(sandbox_id)
        if mode == SnapshotMode.ROOTFS_ONLY:
            raise SandboxError(409, "UNSUPPORTED_RUNTIME", f"{self.backend_name} rejects rootfsOnly snapshots")
        if sandbox.state != State.RUNNING:
            raise SandboxError(409, "SANDBOX_NOT_RUNNING", f"{sandbox_id} is {sandbox.state.value}")
        self._snap_seq += 1
        snap = SnapshotRecord(
            id=f"snap-{self.backend_name}-{self._snap_seq:04d}",
            source_sandbox_id=sandbox_id,
            backend=self.backend_name,
            mode=mode,
            artifact_path="local-oci" if self.backend_name == "docker" else "registry-oci",
            rootfs_files=dict(sandbox.rootfs_files),
            memory_state=None,
        )
        self.snapshots[snap.id] = snap
        return snap

    def _restore(self, sandbox: Sandbox) -> None:
        raise NotImplementedError

    def endpoint(self, sandbox_id: str, port: int) -> dict[str, str]:
        raise NotImplementedError


class SingleHostRuntime(SandboxService):
    """Docker backend: single host port + /proxy/{port}, per-sandbox lifecycle lock."""

    id_prefix = "sbx-"
    backend_name = "docker"
    PROXY_PORT = 44772

    def __init__(self, execd_run_as_init: bool = True) -> None:
        super().__init__()
        self._next_host_port = 40000
        self.execd_run_as_init = execd_run_as_init
        self._locks_guard = threading.Lock()
        self._lifecycle_locks: dict[str, threading.Lock] = {}

    def _sandbox_lifecycle_lock(self, sandbox_id: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._lifecycle_locks.get(sandbox_id)
            if lock is None:
                lock = threading.Lock()
                self._lifecycle_locks[sandbox_id] = lock
            return lock

    def create(self, request: CreateRequest) -> Sandbox:
        env = dict(request.env)
        if self.execd_run_as_init:
            env.setdefault("EXECD_INIT", "1")
        sandbox = Sandbox(
            id=f"sbx-{len(self.sandboxes) + 1:04d}",
            image=request.image,
            state=State.PENDING,
            expires_at=request.expires_at(),
            entrypoint=list(request.entrypoint),
            env=env,
            metadata=dict(request.metadata),
        )
        self.sandboxes[sandbox.id] = sandbox
        self._next_host_port += 1
        sandbox.data_plane = ("127.0.0.1", self._next_host_port)
        sandbox.node = "localhost"
        self.ready(sandbox)
        return sandbox

    def delete(self, sandbox_id: str) -> None:
        with self._sandbox_lifecycle_lock(sandbox_id):
            super().delete(sandbox_id)

    def renew_expiration(self, sandbox_id: str, new_expires_at: float, now: float) -> float:
        """Serialized with per-sandbox lock to prevent renewal-vs-expiry deletion race."""
        with self._sandbox_lifecycle_lock(sandbox_id):
            sandbox = self.get(sandbox_id)
            if new_expires_at <= now:
                raise SandboxError(400, "INVALID_EXPIRATION", "expiresAt must be in the future")
            sandbox.expires_at = new_expires_at
            return sandbox.expires_at

    def expire_if_due(self, sandbox_id: str, now: float) -> bool:
        """Expiration deletion acquires the same per-sandbox lifecycle lock."""
        with self._sandbox_lifecycle_lock(sandbox_id):
            sandbox = self.sandboxes.get(sandbox_id)
            if sandbox is None or sandbox.expires_at is None or now < sandbox.expires_at:
                return False
            sandbox.state = State.STOPPING
            self.sandboxes.pop(sandbox_id)
            return True

    def create_snapshot(self, sandbox_id: str, mode: SnapshotMode = SnapshotMode.FULL) -> SnapshotRecord:
        sandbox = self.get(sandbox_id)
        if mode == SnapshotMode.ROOTFS_ONLY:
            raise SandboxError(409, "UNSUPPORTED_RUNTIME", "docker rejects rootfsOnly snapshots")
        # Since 1.2.1, Docker permits full snapshots directly from paused sandboxes too.
        if sandbox.state not in (State.RUNNING, State.PAUSED):
            raise SandboxError(409, "SANDBOX_INVALID_STATE", f"{sandbox_id} is {sandbox.state.value}")
        self._snap_seq += 1
        snap = SnapshotRecord(
            id=f"snap-docker-{self._snap_seq:04d}",
            source_sandbox_id=sandbox_id,
            backend="docker",
            mode=SnapshotMode.FULL,
            artifact_path="local-oci",
            rootfs_files=dict(sandbox.rootfs_files),
            memory_state=None,
        )
        self.snapshots[snap.id] = snap
        return snap

    def _restore(self, sandbox: Sandbox) -> None:
        self._next_host_port += 1
        sandbox.data_plane = ("127.0.0.1", self._next_host_port)
        sandbox.node = "localhost"

    def endpoint(self, sandbox_id: str, port: int) -> dict[str, str]:
        sandbox = self.get(sandbox_id)
        if sandbox.state != State.RUNNING or sandbox.data_plane is None:
            raise SandboxError(503, "SANDBOX_NOT_READY", sandbox_id)
        host, proxy_port = sandbox.data_plane
        if port == self.PROXY_PORT:
            return {"url": f"http://{host}:{proxy_port}"}
        return {"url": f"http://{host}:{proxy_port}/proxy/{port}", "headers": {}}


class PoolRuntime(SandboxService):
    """Kubernetes BatchSandbox + Pool with sticky Completed terminal phase."""

    id_prefix = "sbx-"
    backend_name = "kubernetes"
    CAPACITY = 2

    def __init__(self, execd_run_as_init: bool = True) -> None:
        super().__init__()
        self.execd_run_as_init = execd_run_as_init
        self.warm: list[str] = [f"pod-{i}" for i in range(1, self.CAPACITY + 1)]
        self.allocated: dict[str, str] = {}

    def create(self, request: CreateRequest) -> Sandbox:
        if not self.warm:
            raise SandboxError(429, "POOL_CAPACITY_EXHAUSTED", "no warm pod available")
        pod = self.warm.pop(0)
        env = dict(request.env)
        if self.execd_run_as_init:
            env.setdefault("EXECD_INIT", "1")
        sandbox = Sandbox(
            id=f"sbx-{len(self.sandboxes) + 1:04d}",
            image=request.image,
            state=State.PENDING,
            expires_at=request.expires_at(),
            entrypoint=list(request.entrypoint),
            env=env,
            metadata=dict(request.metadata),
            node=pod,
        )
        self.sandboxes[sandbox.id] = sandbox
        self.allocated[sandbox.id] = pod
        sandbox.metadata.setdefault("opensandbox.io/task", json.dumps(sandbox.entrypoint))
        sandbox.data_plane = (f"10.0.0.{len(self.allocated)}", 44772)
        self.ready(sandbox)
        return sandbox

    def complete(self, sandbox_id: str, exit_code: int = 0) -> Sandbox:
        """Clean exit-0 enters sticky Completed phase -> public Terminated (SANDBOX_COMPLETED)."""
        sandbox = self.get(sandbox_id)
        if exit_code == 0:
            sandbox.phase = "Completed"
            sandbox.state = State.TERMINATED
            sandbox.reason = "SANDBOX_COMPLETED"
            sandbox.message = "Sandbox workload completed cleanly (exit 0)"
        else:
            sandbox.phase = "Failed"
            sandbox.state = State.FAILED
            sandbox.reason = "SANDBOX_FAILED"
            sandbox.message = f"Main container exited with {exit_code}"
        return sandbox

    def _restore(self, sandbox: Sandbox) -> None:
        sandbox.node = self.allocated.get(sandbox.id, "pod-recycled")
        sandbox.data_plane = (f"10.0.0.{len(self.allocated) + 10}", 44772)

    def endpoint(self, sandbox_id: str, port: int) -> dict[str, str]:
        sandbox = self.get(sandbox_id)
        if sandbox.state != State.RUNNING or sandbox.data_plane is None:
            raise SandboxError(503, "SANDBOX_NOT_READY", sandbox_id)
        host, _ = sandbox.data_plane
        return {"url": f"http://{host}:{port}", "headers": {"OpenSandbox-Ingress-To": f"{sandbox_id}-{port}"}}

    def delete(self, sandbox_id: str) -> None:
        super().delete(sandbox_id)
        pod = self.allocated.pop(sandbox_id, None)
        if pod is not None:
            self.warm.append(pod)


class TemplateRuntime(SandboxService):
    """Fast Sandbox backend: template-first, supports S3 & OCI rootfsOnly snapshots."""

    id_prefix = "fsb-"
    backend_name = "fsb"
    counter = 0

    def __init__(self, *, node_rootfs_qualified: bool = True) -> None:
        super().__init__()
        self.node_rootfs_qualified = node_rootfs_qualified

    def create(self, request: CreateRequest) -> Sandbox:
        if request.entrypoint or request.env:
            raise SandboxError(400, "TEMPLATE_FIELD_REJECTED", "entrypoint/env come from the template")
        TemplateRuntime.counter += 1
        template_id = request.metadata["templateId"]
        artifact_path = "overlaybd-oci" if template_id.startswith("oci://") else "s3-native"
        sandbox = Sandbox(
            id=f"fsb-{TemplateRuntime.counter:04d}",
            image=f"template:{template_id}",
            state=State.PENDING,
            expires_at=request.expires_at(),
            entrypoint=[],
            metadata={**request.metadata, "artifact_path": artifact_path},
            node="fastlet-1",
            data_plane=("10.30.0.5", 44772),
            memory_state={"pid1": "guest-init", "ram": "warm"},
        )
        self.sandboxes[sandbox.id] = sandbox
        self.ready(sandbox)
        return sandbox

    def create_snapshot(self, sandbox_id: str, mode: SnapshotMode = SnapshotMode.FULL) -> SnapshotRecord:
        sandbox = self.get(sandbox_id)
        if sandbox.state != State.RUNNING:
            raise SandboxError(409, "SANDBOX_NOT_RUNNING", f"{sandbox_id} is {sandbox.state.value}")
        if mode == SnapshotMode.ROOTFS_ONLY and not self.node_rootfs_qualified:
            # Fail closed: never silently produce a full snapshot on an unqualified node.
            raise SandboxError(409, "NODE_UNQUALIFIED_FOR_ROOTFS_ONLY", "fastlet node failed rootfs qualification")
        self._snap_seq += 1
        snap = SnapshotRecord(
            id=f"snap-fsb-{self._snap_seq:04d}",
            source_sandbox_id=sandbox_id,
            backend="fsb",
            mode=mode,
            artifact_path=sandbox.metadata.get("artifact_path", "s3-native"),
            rootfs_files=dict(sandbox.rootfs_files),
            memory_state=None if mode == SnapshotMode.ROOTFS_ONLY else dict(sandbox.memory_state),
        )
        self.snapshots[snap.id] = snap
        return snap

    def create_from_snapshot(self, snapshot_id: str, timeout: float | None = 600.0) -> Sandbox:
        snap = self.snapshots.get(snapshot_id)
        if snap is None:
            raise SandboxError(404, "SNAPSHOT_NOT_FOUND", snapshot_id)
        TemplateRuntime.counter += 1
        is_cold_boot = snap.mode == SnapshotMode.ROOTFS_ONLY
        sandbox = Sandbox(
            id=f"fsb-{TemplateRuntime.counter:04d}",
            image=f"snapshot:{snap.id}",
            state=State.PENDING,
            expires_at=None if timeout is None else time.time() + timeout,
            entrypoint=["rerun-entrypoint"] if is_cold_boot else [],
            metadata={"snapshotId": snap.id, "snapshotMode": snap.mode.value, "restoreType": "cold-boot" if is_cold_boot else "vmstate"},
            node="fastlet-2",
            data_plane=("10.30.0.9", 44772),
            rootfs_files=dict(snap.rootfs_files),
            memory_state={} if is_cold_boot else dict(snap.memory_state or {}),
        )
        self.sandboxes[sandbox.id] = sandbox
        self.ready(sandbox)
        return sandbox

    def _restore(self, sandbox: Sandbox) -> None:
        sandbox.node = "fastlet-2"
        sandbox.data_plane = ("10.30.0.9", 44772)

    def endpoint(self, sandbox_id: str, port: int) -> dict[str, str]:
        sandbox = self.get(sandbox_id)
        if sandbox.state != State.RUNNING or sandbox.data_plane is None:
            raise SandboxError(503, "SANDBOX_NOT_READY", sandbox_id)
        return {"url": f"https://gateway.example.com/f1.{sandbox_id}.{port}", "headers": {"OPEN-SANDBOX-ORIGIN": "template"}}


class CompositeService(SandboxService):
    """Single kubernetes runtime.type dispatching container Pods and fsb templates."""

    def __init__(self, pod_backend: PoolRuntime, template_backend: TemplateRuntime | None = None) -> None:
        super().__init__()
        self.pod_backend = pod_backend
        self.template_backend = template_backend

    def _route(self, request: CreateRequest) -> SandboxService:
        if request.metadata.get("templateId") and self.template_backend is not None:
            return self.template_backend
        return self.pod_backend

    def create(self, request: CreateRequest) -> Sandbox:
        return self._route(request).create(request)

    def _backend(self, sandbox_id: str) -> SandboxService:
        if sandbox_id.startswith("fsb-") and self.template_backend is not None:
            return self.template_backend
        return self.pod_backend

    def get(self, sandbox_id: str) -> Sandbox:
        return self._backend(sandbox_id).get(sandbox_id)

    def list(self) -> list[Sandbox]:
        return self.pod_backend.list() + (self.template_backend.list() if self.template_backend else [])

    def delete(self, sandbox_id: str) -> None:
        self._backend(sandbox_id).delete(sandbox_id)

    def pause(self, sandbox_id: str) -> None:
        self._backend(sandbox_id).pause(sandbox_id)

    def resume(self, sandbox_id: str) -> Sandbox:
        return self._backend(sandbox_id).resume(sandbox_id)

    def create_snapshot(self, sandbox_id: str, mode: SnapshotMode = SnapshotMode.FULL) -> SnapshotRecord:
        return self._backend(sandbox_id).create_snapshot(sandbox_id, mode=mode)

    def endpoint(self, sandbox_id: str, port: int) -> dict[str, str]:
        return self._backend(sandbox_id).endpoint(sandbox_id, port)


def create_service(runtime: str) -> SandboxService:
    """Matches services/factory.py: configuration picks implementation."""
    if runtime == "docker":
        return SingleHostRuntime()
    if runtime == "kubernetes":
        return CompositeService(PoolRuntime(), TemplateRuntime())
    raise ValueError(f"Unsupported runtime: {runtime}")


class Server:
    """Minimal control-plane wrapper: auth, startup guard, and absolute TTL expiry."""

    def __init__(self, runtime: str, api_key: str | None = None, now: float | None = None) -> None:
        if api_key is None and runtime != "docker":
            raise SystemExit("refusing to start without an API key outside local development")
        self.service = create_service(runtime)
        self.api_key = api_key
        self.clock = (lambda: now) if isinstance(now, (int, float)) else (now if now is not None else time.time)

    def _auth(self, key: str | None) -> None:
        if self.api_key is not None and key != self.api_key:
            raise SandboxError(401, "UNAUTHORIZED", "missing or invalid OPEN-SANDBOX-API-KEY")

    def create(self, key: str | None, request: CreateRequest) -> Sandbox:
        self._auth(key)
        request.now = self.clock()
        return self.service.create(request)

    def get(self, key: str | None, sandbox_id: str) -> Sandbox:
        self._auth(key)
        sandbox = self.service.get(sandbox_id)
        if sandbox.expires_at is not None and self.clock() >= sandbox.expires_at:
            sandbox.state = State.TERMINATED
            sandbox.reason = "Expired"
        return sandbox

    def endpoint(self, key: str | None, sandbox_id: str, port: int) -> dict[str, str]:
        self._auth(key)
        return self.service.endpoint(sandbox_id, port)

    def expire(self) -> list[str]:
        now = self.clock()
        expired = [
            sandbox.id
            for sandbox in self.service.list()
            if sandbox.expires_at is not None and now >= sandbox.expires_at
        ]
        for sandbox_id in expired:
            if isinstance(self.service, SingleHostRuntime):
                self.service.expire_if_due(sandbox_id, now)
            else:
                self.service.delete(sandbox_id)
        return expired


def main() -> None:
    server = Server("kubernetes", api_key="demo-key")
    sandbox = server.create("demo-key", CreateRequest(image="alpine:3.19", timeout=60))
    print("created:", sandbox.id, sandbox.state.value, "on", sandbox.node, "EXECD_INIT=", sandbox.env.get("EXECD_INIT"))

    execd = Execd(init_mode=True)
    execd.spawn_orphan(4242)
    print("reaped orphans:", execd.reap_orphans())
    execd.write_file("/tmp/hello.sh", b"echo hi\n")
    for event in execd.run(["sh", "/tmp/hello.sh"]):
        print(event.to_sse().strip())

    egress = Egress(deny_always=[Rule("deny", "metadata.internal")], allow_always=[Rule("allow", "pypi.org.")])
    egress.add(Rule("allow", "*.python.org"))
    print("egress pypi.org (trailing dot rule) ->", egress.decide("pypi.org"))
    print("egress evil.example                 ->", egress.decide("evil.example"))
    print("egress metadata.internal            ->", egress.decide("metadata.internal"))

    fsb_sbx = server.create("demo-key", CreateRequest(image="ignored", metadata={"templateId": "s3-python"}))
    fsb_sbx.rootfs_files["/workspace/out.txt"] = b"ok"
    snap = server.service.create_snapshot(fsb_sbx.id, mode=SnapshotMode.ROOTFS_ONLY)
    print("fsb snapshot:", snap.id, snap.mode.value, snap.artifact_path)


if __name__ == "__main__":
    main()
```

## test_mini_osb.py

```python
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
```
