"""Zero-dependency teaching model of Prime Agent's core architectural invariants.

Models (Python 3.10+ stdlib only):
1. HeadTailBuffer (3/4 head + 1/4 rolling tail stream truncation)
2. PersistentReplKernel (persistent namespace, trailing expr `_`, snapshot/prune, end_turn)
3. SessionInbox (durable JSONL journal with torn-write replay, strict filter, heartbeat dedup)
4. SubagentRegistry (depth guard, sibling uniqueness, nuclear-family scope, progress_note throttle)
5. CompactionPolicy (exact threshold formula with retention floor & combined ceiling)
6. MiniAgentLoop (claim-first run exclusion, steering/follow-up/continuation priority, end_turn)
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

DEFAULT_MAX_OUTPUT_CHARS = 65_536
DEFAULT_RLM_MAX_DEPTH = 2
RLM_PROGRESS_NOTE_MAX_UTF16 = 512
RLM_PROGRESS_NOTE_MIN_INTERVAL_MS = 10_000

DEFAULT_RESERVE_TOKENS = 16_384
DEFAULT_KEEP_RECENT_TOKENS = 20_000
DEFAULT_THRESHOLD_PERCENT = 95.0
COMBINED_LIMIT_HEADROOM_FLOOR = 4_096

INBOX_CLASSES = frozenset(
    {
        "bash-done",
        "factory-progress",
        "agent-message",
        "child-exited",
        "child-failed",
        "heartbeat",
    }
)
ALLOWED_INBOX_FILTER_KEYS = frozenset({"sender", "class", "sinceMs", "beforeMs"})
ALWAYS_SKIP_SNAPSHOT_NAMES = frozenset(
    {"rlm", "mcp", "bash", "asyncio", "In", "Out", "get_ipython", "exit", "quit", "open"}
)


class BusyError(RuntimeError):
    """Raised when a second run tries to claim an already active agent run slot."""


class HeadTailBuffer:
    """Bounded character buffer retaining 3/4 head and 1/4 rolling tail."""

    def __init__(self, max_chars: int = DEFAULT_MAX_OUTPUT_CHARS) -> None:
        if max_chars < 0:
            raise ValueError("max_chars must be non-negative")
        self.max_chars = max_chars
        self.tail_cap = max_chars // 4
        self.head_cap = max_chars - self.tail_cap
        self._head: list[str] = []
        self._head_len = 0
        self._tail = ""
        self._omitted = 0

    def push(self, text: str) -> None:
        if not text:
            return
        if self._head_len < self.head_cap:
            remaining = self.head_cap - self._head_len
            if len(text) <= remaining:
                self._head.append(text)
                self._head_len += len(text)
                return
            self._head.append(text[:remaining])
            self._head_len = self.head_cap
            self._push_tail(text[remaining:])
        else:
            self._push_tail(text)

    def _push_tail(self, text: str) -> None:
        if not text:
            return
        if self.tail_cap == 0:
            self._omitted += len(text)
            return
        if len(text) >= self.tail_cap:
            self._omitted += len(self._tail) + len(text) - self.tail_cap
            self._tail = text[-self.tail_cap :]
            return
        self._tail += text
        if len(self._tail) > 2 * self.tail_cap:
            drop = len(self._tail) - self.tail_cap
            self._tail = self._tail[drop:]
            self._omitted += drop

    def finish(self) -> str:
        if len(self._tail) > self.tail_cap:
            drop = len(self._tail) - self.tail_cap
            self._tail = self._tail[drop:]
            self._omitted += drop
        head = "".join(self._head)
        if self._omitted == 0:
            return head + self._tail
        return f"{head}\n[... {self._omitted} chars omitted ...]\n{self._tail}"


@dataclass(frozen=True)
class CellResult:
    status: Literal["ok", "error"]
    stdout: str
    stderr: str
    result: str | None
    error: str | None
    ended_turn: bool


@dataclass(frozen=True)
class SnapshotReport:
    saved: list[str]
    skipped: list[str]
    pruned: list[str]
    total_bytes: int


class PersistentReplKernel:
    """Single-namespace Python REPL modeling persistent state, truncation, and snapshot pruning."""

    def __init__(self, *, max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS) -> None:
        self.max_output_chars = max_output_chars
        self._ended_turn_in_cell = False
        self.namespace: dict[str, Any] = {}
        self.install_binding("end_turn", self._mark_end_turn)

    def install_binding(self, name: str, value: Any) -> None:
        self.namespace[name] = value

    def _mark_end_turn(self) -> dict[str, Any]:
        self._ended_turn_in_cell = True
        return {"ended_turn": True}

    def execute(self, code: str) -> CellResult:
        self._ended_turn_in_cell = False
        stdout_raw = io.StringIO()
        stderr_raw = io.StringIO()
        expr_repr: str | None = None
        error_text: str | None = None
        status: Literal["ok", "error"] = "ok"

        try:
            tree = ast.parse(code, mode="exec")
            trailing_expr: ast.Expression | None = None
            if tree.body and isinstance(tree.body[-1], ast.Expr):
                last = tree.body.pop()
                assert isinstance(last, ast.Expr)
                trailing_expr = ast.Expression(body=last.value)
                ast.fix_missing_locations(trailing_expr)
            with contextlib.redirect_stdout(stdout_raw), contextlib.redirect_stderr(stderr_raw):
                if tree.body:
                    exec(compile(tree, "<cell>", "exec"), self.namespace)
                if trailing_expr is not None:
                    val = eval(compile(trailing_expr, "<cell>", "eval"), self.namespace)
                    if val is not None:
                        self.namespace["_"] = val
                        expr_repr = repr(val)
        except Exception as exc:  # noqa: BLE001
            status = "error"
            error_text = f"{type(exc).__name__}: {exc}"

        out_buf = HeadTailBuffer(self.max_output_chars)
        out_buf.push(stdout_raw.getvalue())
        err_buf = HeadTailBuffer(self.max_output_chars)
        err_buf.push(stderr_raw.getvalue())
        return CellResult(
            status=status,
            stdout=out_buf.finish(),
            stderr=err_buf.finish(),
            result=expr_repr,
            error=error_text,
            ended_turn=self._ended_turn_in_cell,
        )

    def snapshot(
        self,
        path: Path,
        *,
        max_bytes: int = 256 * 1024 * 1024,
        max_variable_bytes: int = 16 * 1024 * 1024,
        prune_oversized: bool = False,
    ) -> SnapshotReport:
        saved_payload: dict[str, Any] = {}
        saved: list[str] = []
        skipped: list[str] = []
        pruned: list[str] = []
        total_bytes = 0

        for name in sorted(self.namespace):
            if name.startswith("_") or name in ALWAYS_SKIP_SNAPSHOT_NAMES:
                continue
            val = self.namespace[name]
            if callable(val):
                continue
            try:
                encoded = json.dumps(val, allow_nan=False).encode("utf-8")
            except (TypeError, ValueError):
                skipped.append(name)
                continue
            size = len(encoded)
            if size > max_variable_bytes:
                skipped.append(name)
                if prune_oversized:
                    pruned.append(name)
                continue
            if total_bytes + size > max_bytes:
                skipped.append(name)
                continue
            saved_payload[name] = val
            saved.append(name)
            total_bytes += size

        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".snap-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"magic": "MINI-SNAPSHOT-V2", "vars": saved_payload}, fh)
            os.replace(tmp_name, path)
        except Exception:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise

        # Prune only after atomic write succeeds, matching repl.py semantics.
        for name in pruned:
            self.namespace.pop(name, None)

        return SnapshotReport(saved=saved, skipped=skipped, pruned=pruned, total_bytes=total_bytes)

    def restore(self, path: Path) -> tuple[list[str], list[str]]:
        if not path.exists():
            return [], []
        doc = json.loads(path.read_text(encoding="utf-8"))
        restored: list[str] = []
        failed: list[str] = []
        for name, val in doc.get("vars", {}).items():
            if name in {"In", "Out", "get_ipython"}:
                continue
            self.namespace[name] = val
            restored.append(name)
        return restored, failed


@dataclass(frozen=True)
class InboxMessage:
    id: str
    inbox_class: str
    arrived_at_ms: int
    sender: str | None
    text: str
    meta: dict[str, Any] = field(default_factory=dict)

    def to_wire(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "class": self.inbox_class,
            "arrivedAtMs": self.arrived_at_ms,
            "sender": self.sender,
            "text": self.text,
            "meta": self.meta,
        }


class SessionInbox:
    """Durable per-session inbox with torn-write replay, strict filter, and wake coalescing."""

    def __init__(self, journal_path: Path | None = None) -> None:
        self._journal_path = journal_path
        self._messages: list[InboxMessage] = []
        self._next_id = 1
        self.persistence_error: str | None = None
        if journal_path is not None and journal_path.exists():
            self._replay(journal_path)

    def _replay(self, path: Path) -> None:
        raw = path.read_text(encoding="utf-8")
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                # Torn trailing write is ignored; complete prior records survive.
                continue
            op = rec.get("op")
            if op == "push":
                msg = InboxMessage(
                    id=rec["id"],
                    inbox_class=rec["class"],
                    arrived_at_ms=rec["arrivedAtMs"],
                    sender=rec.get("sender"),
                    text=rec["text"],
                    meta=rec.get("meta", {}),
                )
                self._messages.append(msg)
                num = int(msg.id.split("_")[-1])
                self._next_id = max(self._next_id, num + 1)
            elif op == "drain":
                drained = set(rec.get("ids", []))
                self._messages = [m for m in self._messages if m.id not in drained]

    def _append_journal(self, record: dict[str, Any]) -> None:
        if self._journal_path is None:
            return
        try:
            self._journal_path.parent.mkdir(parents=True, exist_ok=True)
            with self._journal_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, separators=(",", ":")) + "\n")
        except OSError as exc:
            self.persistence_error = str(exc)

    def push(
        self,
        inbox_class: str,
        text: str,
        *,
        arrived_at_ms: int,
        sender: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> InboxMessage:
        if inbox_class not in INBOX_CLASSES:
            raise ValueError(f"Unsupported inbox class: {inbox_class}")
        # Heartbeat deduplication: keep at most one waiting fire per heartbeat job id.
        if inbox_class == "heartbeat" and sender is not None:
            self.withdraw_by_sender("heartbeat", sender)
        msg = InboxMessage(
            id=f"inbox_{self._next_id}",
            inbox_class=inbox_class,
            arrived_at_ms=arrived_at_ms,
            sender=sender,
            text=text,
            meta=dict(meta or {}),
        )
        self._next_id += 1
        self._messages.append(msg)
        self._append_journal({"op": "push", **msg.to_wire()})
        return msg

    def withdraw_by_sender(self, inbox_class: str, sender: str) -> list[InboxMessage]:
        removed = [
            m for m in self._messages if m.inbox_class == inbox_class and m.sender == sender
        ]
        if removed:
            removed_ids = {m.id for m in removed}
            self._messages = [m for m in self._messages if m.id not in removed_ids]
            self._append_journal({"op": "drain", "ids": sorted(removed_ids)})
        return removed

    def count(self) -> dict[str, Any]:
        classes: dict[str, int] = {}
        oldest: int | None = None
        for m in self._messages:
            classes[m.inbox_class] = classes.get(m.inbox_class, 0) + 1
            if oldest is None or m.arrived_at_ms < oldest:
                oldest = m.arrived_at_ms
        return {"count": len(self._messages), "oldestArrivedAtMs": oldest, "classes": classes}

    def wake_notice_text(self) -> str | None:
        if not self._messages:
            return None
        classes = self.count()["classes"]
        if len(classes) == 1:
            cls, n = next(iter(classes.items()))
            noun = "message" if n == 1 else "messages"
            base = f"[inbox] {n} new {cls} {noun}. Read them with msgs = await rlm.inbox.read()."
        else:
            n = len(self._messages)
            base = f"[inbox] {n} new messages. Read them with msgs = await rlm.inbox.read()."
        if self.persistence_error:
            base += " (inbox persistence failed; messages are memory-only)"
        return base

    def read(self, filter_dict: dict[str, Any] | None = None) -> list[InboxMessage]:
        flt = dict(filter_dict or {})
        flt.pop("type", None)
        unknown = set(flt) - ALLOWED_INBOX_FILTER_KEYS
        if unknown:
            raise ValueError(f"Unknown inbox filter field(s): {sorted(unknown)}")
        sender = flt.get("sender")
        inbox_class = flt.get("class")
        since_ms = flt.get("sinceMs")
        before_ms = flt.get("beforeMs")

        matched: list[InboxMessage] = []
        remaining: list[InboxMessage] = []
        for m in self._messages:
            ok = (
                (sender is None or m.sender == sender)
                and (inbox_class is None or m.inbox_class == inbox_class)
                and (since_ms is None or m.arrived_at_ms >= since_ms)
                and (before_ms is None or m.arrived_at_ms < before_ms)
            )
            if ok:
                matched.append(m)
            else:
                remaining.append(m)
        self._messages = remaining
        if matched:
            self._append_journal({"op": "drain", "ids": [m.id for m in matched]})
        return matched


@dataclass
class SubagentRecord:
    child_id: str
    name: str
    depth: int
    parent_id: str
    status: Literal["running", "done", "error", "cancelled"] = "running"
    answer_text: str | None = None
    progress_note: str | None = None
    last_note_at_ms: int | None = None


class SubagentRegistry:
    """Tracks RLM recursion depth, sibling name uniqueness, collect(), and progress notes."""

    def __init__(self, *, max_depth: int = DEFAULT_RLM_MAX_DEPTH) -> None:
        self.max_depth = max_depth
        self._seq = 0
        self._children: dict[str, SubagentRecord] = {}
        self._ledger: list[dict[str, Any]] = []

    def spawn(self, *, parent_id: str, parent_depth: int, name: str) -> SubagentRecord:
        next_depth = parent_depth + 1
        if next_depth > self.max_depth:
            raise ValueError(
                f"RLM max depth exceeded: depth {next_depth} > max_depth {self.max_depth}"
            )
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Subagent name must not be empty")
        for existing in self._children.values():
            if existing.parent_id == parent_id and existing.name == clean_name:
                raise ValueError(f"Duplicate sibling subagent name: {clean_name!r}")
        self._seq += 1
        rec = SubagentRecord(
            child_id=f"child_{self._seq}",
            name=clean_name,
            depth=next_depth,
            parent_id=parent_id,
        )
        self._children[rec.child_id] = rec
        self._ledger.append(
            {"op": "spawn", "childId": rec.child_id, "parentId": parent_id, "name": clean_name, "depth": next_depth}
        )
        return rec

    def finish(self, child_id: str, *, status: Literal["done", "error", "cancelled"], answer: str | None = None) -> None:
        rec = self._children[child_id]
        rec.status = status
        rec.answer_text = answer

    def progress_note(self, child_id: str, message: str, *, now_ms: int) -> dict[str, Any]:
        stripped = message.strip()
        if not stripped:
            raise ValueError("Progress note must not be empty")
        utf16_units = len(stripped.encode("utf-16-le", "surrogatepass")) // 2
        if utf16_units > RLM_PROGRESS_NOTE_MAX_UTF16:
            raise ValueError(f"Progress note exceeds {RLM_PROGRESS_NOTE_MAX_UTF16} UTF-16 code units")
        rec = self._children[child_id]
        if rec.last_note_at_ms is not None:
            elapsed = now_ms - rec.last_note_at_ms
            if elapsed < RLM_PROGRESS_NOTE_MIN_INTERVAL_MS:
                return {"accepted": False, "retry_after_ms": RLM_PROGRESS_NOTE_MIN_INTERVAL_MS - elapsed}
        rec.progress_note = stripped
        rec.last_note_at_ms = now_ms
        return {"accepted": True, "retry_after_ms": None}

    def collect(self, parent_id: str, targets: list[str] | None = None) -> list[dict[str, Any]]:
        direct = [c for c in self._children.values() if c.parent_id == parent_id]
        if targets:
            wanted = set(targets)
            direct = [c for c in direct if c.child_id in wanted or c.name in wanted]
        return [
            {
                "rlm_child_id": c.child_id,
                "name": c.name,
                "status": c.status,
                "settled": c.status != "running",
                "answer_text": c.answer_text,
                "progress_note": c.progress_note,
            }
            for c in direct
        ]


@dataclass(frozen=True)
class CompactionSettings:
    enabled: bool = True
    threshold_percent: float = DEFAULT_THRESHOLD_PERCENT
    reserve_tokens: int = DEFAULT_RESERVE_TOKENS
    keep_recent_tokens: int = DEFAULT_KEEP_RECENT_TOKENS


def compaction_threshold(
    context_window: int,
    max_output_tokens: int,
    settings: CompactionSettings = CompactionSettings(),
) -> int:
    """Exact port of pa-core::session_engine::compaction::compaction_threshold."""
    percent = (
        settings.threshold_percent
        if 0.0 < settings.threshold_percent <= 100.0
        else DEFAULT_THRESHOLD_PERCENT
    )
    requested = int(context_window * (percent / 100.0))
    retention_floor = settings.keep_recent_tokens + (settings.reserve_tokens * 2) + 1
    percentage = max(requested, retention_floor)
    headroom = max(settings.reserve_tokens, COMBINED_LIMIT_HEADROOM_FLOOR)
    combined = max(0, context_window - max_output_tokens - headroom)
    return min(percentage, combined)


def should_compact(
    context_tokens: int,
    context_window: int,
    max_output_tokens: int,
    settings: CompactionSettings = CompactionSettings(),
) -> bool:
    if not settings.enabled or context_window <= 0:
        return False
    threshold = compaction_threshold(context_window, max_output_tokens, settings)
    return threshold > 0 and context_tokens > threshold


@dataclass
class AssistantTurn:
    text: str = ""
    tool_calls: list[str] = field(default_factory=list)


class MiniAgentLoop:
    """Models claim-first exclusion, steering/follow-up/continuation queues, and end_turn."""

    def __init__(self, kernel: PersistentReplKernel) -> None:
        self.kernel = kernel
        self._active = False
        self.steering_queue: list[str] = []
        self.follow_up_queue: list[str] = []
        self.transcript: list[dict[str, Any]] = []

    def enqueue_steering(self, text: str) -> None:
        self.steering_queue.append(text)

    def enqueue_follow_up(self, text: str) -> None:
        self.follow_up_queue.append(text)

    def claim_run(self) -> None:
        if self._active:
            raise BusyError("Agent is already processing.")
        self._active = True

    def release_run(self) -> None:
        self._active = False

    def run(
        self,
        initial_prompt: str,
        model_fn: Callable[[list[dict[str, Any]]], AssistantTurn],
        continuation_fn: Callable[[list[dict[str, Any]]], list[str]] | None = None,
    ) -> list[dict[str, Any]]:
        self.claim_run()
        try:
            pending: list[str] = [initial_prompt]
            if self.steering_queue:
                pending.extend(self.steering_queue)
                self.steering_queue.clear()

            while True:
                has_more_tools = True
                while has_more_tools or pending:
                    for msg in pending:
                        self.transcript.append({"role": "user", "text": msg})
                    pending = []

                    turn = model_fn(self.transcript)
                    self.transcript.append(
                        {"role": "assistant", "text": turn.text, "tool_calls": list(turn.tool_calls)}
                    )

                    has_more_tools = False
                    if turn.tool_calls:
                        batch_ended_turn = False
                        for code in turn.tool_calls:
                            res = self.kernel.execute(code)
                            if res.ended_turn:
                                batch_ended_turn = True
                            self.transcript.append(
                                {
                                    "role": "toolResult",
                                    "status": res.status,
                                    "stdout": res.stdout,
                                    "result": res.result,
                                    "ended_turn": res.ended_turn,
                                }
                            )
                        has_more_tools = not batch_ended_turn

                    if self.steering_queue:
                        pending = list(self.steering_queue)
                        self.steering_queue.clear()

                if self.follow_up_queue:
                    pending = list(self.follow_up_queue)
                    self.follow_up_queue.clear()
                    continue

                if continuation_fn is not None:
                    cont = continuation_fn(self.transcript)
                    if cont:
                        pending = list(cont)
                        continue
                break
            return list(self.transcript)
        finally:
            self.release_run()
