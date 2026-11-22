#!/usr/bin/env python3
"""Zero-dependency Python 3.10+ teaching model of arcships/dim core invariants.

Models five mechanisms directly grounded in Dim Agent v4:
1. Sequenced Time Bus (Timeline) with RESERVED_DOMAINS guard, synchronous in-process
   `before` transform chain (rolling back to original draft on hook error),
   atomic batch commit, and committed vs published watermark separation.
2. Dispatcher with ordered live delivery and `consume(keys, rebuild, handler)` that
   holds live facts during rebuild and delivers strictly above the rebuild cutoff
   without re-executing side effects.
3. Branch tree (TimelineTree / BranchTool) with root (`main`), movable default branch
   (`branch/default` on root), opener eligibility (root or current default branch),
   Session-wide open tool-line cap excluding the protected path (default + ancestors),
   atomic reservation charging, parent-child communication, and end protection.
4. Branch-scoped Approval state machine (`approval/request`, `approval/respond`,
   `approval/cancelled`) with first-decision-wins, interrupt cancellation, and
   deterministic replay without side-effect re-execution.
5. Context compaction fact (`context/compaction`) paired with a discardable
   SHA-256 verified RecoverySnapshot that falls back to full fact replay on corruption.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable

ROOT_BRANCH_ID = "main"
DEFAULT_MAX_OPEN_LINES = 4

RESERVED_DOMAINS: frozenset[str] = frozenset(
    {
        "input",
        "stop",
        "approval",
        "permission",
        "exec",
        "turn",
        "prompt",
        "context",
        "model",
        "step",
        "branch",
        "collaboration",
        "session",
    }
)


class DimError(Exception):
    """Structured error carrying a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass
class EventDraft:
    domain: str
    name: str
    data: dict[str, Any]
    tags: list[str] = field(default_factory=list)
    parent_call_id: str | None = None

    @property
    def key(self) -> str:
        return f"{self.domain}/{self.name}"


@dataclass(frozen=True)
class TraceStep:
    handler: str
    result_data: dict[str, Any] | None


@dataclass(frozen=True)
class AgentEvent:
    id: str
    branch_id: str
    sequence: int
    domain: str
    name: str
    data: dict[str, Any]
    batch_end: bool = True
    parent_call_id: str | None = None
    trace: tuple[TraceStep, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.domain}/{self.name}"


def key_matches(patterns: tuple[str, ...], event_key: str) -> bool:
    for pat in patterns:
        if pat == "*":
            return True
        if pat.endswith("/*"):
            prefix = pat[:-1]
            if event_key.startswith(prefix):
                return True
        elif pat == event_key:
            return True
    return False


BeforeHook = Callable[[EventDraft], EventDraft]


class Timeline:
    """Single-branch sequenced Time Bus separating committed from published watermarks."""

    def __init__(
        self,
        branch_id: str,
        ancestors: list[tuple[str, int]] | None = None,
        store: dict[str, list[AgentEvent]] | None = None,
        auto_publish: bool = True,
    ) -> None:
        self.branch_id = branch_id
        self.ancestors: list[tuple[str, int]] = list(ancestors or [])
        self._store: dict[str, list[AgentEvent]] = store if store is not None else {}
        self._store.setdefault(branch_id, [])
        self._before_hooks: list[tuple[str, tuple[str, ...], BeforeHook]] = []
        self._auto_publish = auto_publish
        self._committed_seq: int = len(self._store[branch_id])
        self._published_seq: int = self._committed_seq
        self._publish_listeners: list[Callable[[AgentEvent], None]] = []
        self._snapshots: list[RecoverySnapshot] = []
        self._next_id = self._committed_seq + 1

    def committed(self) -> int:
        return self._committed_seq

    def published(self) -> int:
        return self._published_seq

    def register_before(
        self, handler_name: str, keys: tuple[str, ...], hook: BeforeHook
    ) -> None:
        self._before_hooks.append((handler_name, keys, hook))

    def on_publish(self, listener: Callable[[AgentEvent], None]) -> None:
        self._publish_listeners.append(listener)

    def _run_before(self, draft: EventDraft) -> tuple[EventDraft, tuple[TraceStep, ...]]:
        original = copy.deepcopy(draft)
        current = copy.deepcopy(draft)
        steps: list[TraceStep] = []
        for handler_name, keys, hook in self._before_hooks:
            if not key_matches(keys, current.key):
                continue
            try:
                transformed = hook(copy.deepcopy(current))
                if not isinstance(transformed, EventDraft):
                    raise TypeError("before hook must return EventDraft")
                current = transformed
                steps.append(TraceStep(handler_name, copy.deepcopy(current.data)))
            except Exception:
                # Principles §4 rule 3: if a before hook fails, fall back to original draft.
                return original, (TraceStep(handler_name, None),)
        return current, tuple(steps)

    def append_extension(self, draft: EventDraft) -> AgentEvent:
        """Extension writers cannot append into core RESERVED_DOMAINS."""
        if not draft.domain or "/" in draft.domain or "*" in draft.domain:
            raise DimError("invalid_domain", f"Invalid event domain: {draft.domain!r}")
        if not draft.name or "/" in draft.name or "*" in draft.name:
            raise DimError("invalid_name", f"Invalid event name: {draft.name!r}")
        if draft.domain in RESERVED_DOMAINS:
            raise DimError(
                "reserved_domain",
                f"Extension cannot append into reserved core domain '{draft.domain}'",
            )
        return self.append(draft)

    def append(self, draft: EventDraft) -> AgentEvent:
        return self.append_batch([draft])[0]

    def append_batch(
        self,
        drafts: list[EventDraft],
        register: Callable[[list[AgentEvent]], None] | None = None,
    ) -> list[AgentEvent]:
        if not drafts:
            raise DimError("empty_batch", "Batch must contain at least one draft")
        prepared: list[AgentEvent] = []
        seq = self._committed_seq
        for idx, draft in enumerate(drafts):
            final_draft, trace = self._run_before(draft)
            seq += 1
            ev = AgentEvent(
                id=f"{self.branch_id}-ev-{self._next_id}",
                branch_id=self.branch_id,
                sequence=seq,
                domain=final_draft.domain,
                name=final_draft.name,
                data=copy.deepcopy(final_draft.data),
                batch_end=(idx == len(drafts) - 1),
                parent_call_id=final_draft.parent_call_id,
                trace=trace,
            )
            self._next_id += 1
            prepared.append(ev)

        if register is not None:
            # Registration runs inside the commit lock; failure aborts the entire batch.
            register(prepared)

        self._store[self.branch_id].extend(prepared)
        self._committed_seq = seq
        if self._auto_publish:
            self.flush_publish()
        return prepared

    def flush_publish(self) -> list[AgentEvent]:
        """Advance publish watermark to committed watermark (models SQLite fsync commit)."""
        if self._published_seq >= self._committed_seq:
            return []
        newly_published = self._store[self.branch_id][self._published_seq : self._committed_seq]
        self._published_seq = self._committed_seq
        for ev in newly_published:
            for listener in list(self._publish_listeners):
                listener(ev)
        return list(newly_published)

    def query(
        self,
        through: int | None = None,
        include_publishing: bool = False,
        include_ancestors: bool = False,
        keys: tuple[str, ...] = ("*",),
    ) -> list[AgentEvent]:
        ceiling = self._committed_seq if include_publishing else self._published_seq
        if through is None:
            through = ceiling
        if through > ceiling:
            raise DimError("sequence_out_of_bounds", f"Requested {through} > visible {ceiling}")

        rows: list[AgentEvent] = []
        if include_ancestors:
            for anc_id, anc_cut in self.ancestors:
                anc_events = self._store.get(anc_id, [])
                for ev in anc_events:
                    if ev.sequence <= anc_cut and key_matches(keys, ev.key):
                        rows.append(ev)

        for ev in self._store[self.branch_id]:
            if ev.sequence <= through and key_matches(keys, ev.key):
                rows.append(ev)
        return rows

    def save_snapshot(self, snapshot: "RecoverySnapshot") -> None:
        self._snapshots.append(snapshot)

    def latest_snapshot(self, through: int, interpreter: str) -> "RecoverySnapshot | None":
        candidates = [
            s
            for s in self._snapshots
            if s.interpreter == interpreter and s.anchor_sequence <= through
        ]
        return candidates[-1] if candidates else None


class Dispatcher:
    """Branch-local ordered fact consumer with rebuild/live handoff (`consume`)."""

    def __init__(self, timeline: Timeline) -> None:
        self.timeline = timeline
        self.dispatched_sequence: int = 0
        self._subscribers: list[tuple[str, tuple[str, ...], Callable[[AgentEvent], None]]] = []
        self.timeline.on_publish(self._on_published_event)

    def _on_published_event(self, ev: AgentEvent) -> None:
        self.dispatched_sequence = ev.sequence
        for _name, keys, handler in list(self._subscribers):
            if key_matches(keys, ev.key):
                handler(ev)

    def subscribe(
        self, name: str, keys: tuple[str, ...], handler: Callable[[AgentEvent], None]
    ) -> None:
        self._subscribers.append((name, keys, handler))

    def consume(
        self,
        name: str,
        keys: tuple[str, ...],
        rebuild: Callable[[Timeline], int],
        handler: Callable[[AgentEvent], None],
    ) -> int:
        """Subscribe first (holding live facts), run rebuild, then drain > cutoff."""
        held: list[AgentEvent] = []
        holding = True

        def gate(ev: AgentEvent) -> None:
            if holding:
                held.append(ev)
            else:
                handler(ev)

        self.subscribe(name, keys, gate)
        cutoff = rebuild(self.timeline)
        holding = False
        for ev in held:
            if ev.sequence > cutoff:
                handler(ev)
        return cutoff


@dataclass
class BranchNode:
    branch_id: str
    parent: str | None
    source: str  # "root" | "tool" | "user" | "plugin"
    cut: int
    timeline: Timeline
    dispatcher: Dispatcher
    ended: bool = False
    reserved: bool = False


class TimelineTree:
    """Session-scoped branch directory, default-branch router, and cap enforcer."""

    def __init__(self, max_open_lines: int = DEFAULT_MAX_OPEN_LINES) -> None:
        self.max_open_lines = max_open_lines
        self._store: dict[str, list[AgentEvent]] = {}
        root_tl = Timeline(ROOT_BRANCH_ID, ancestors=[], store=self._store)
        root_dp = Dispatcher(root_tl)
        self.nodes: dict[str, BranchNode] = {
            ROOT_BRANCH_ID: BranchNode(
                branch_id=ROOT_BRANCH_ID,
                parent=None,
                source="root",
                cut=0,
                timeline=root_tl,
                dispatcher=root_dp,
            )
        }
        self._default_branch: str = ROOT_BRANCH_ID
        self._counter = 0
        self._read_cursors: dict[tuple[str, str], int] = {}

    @property
    def default_branch(self) -> str:
        return self._default_branch

    def resolve_target(self, branch_id: str | None = None) -> str:
        """Requests omitting branchId route to the Session's default branch."""
        return self._default_branch if branch_id is None else branch_id

    def protected_path(self) -> set[str]:
        """Default branch plus every ancestor up to root."""
        protected: set[str] = set()
        cur: str | None = self._default_branch
        while cur is not None:
            protected.add(cur)
            node = self.nodes.get(cur)
            cur = node.parent if node else None
        return protected

    def counted_tool_lines(self) -> int:
        """Open (or reserved) tool lines NOT on the protected path."""
        protected = self.protected_path()
        return sum(
            1
            for bid, node in self.nodes.items()
            if node.source == "tool" and not node.ended and bid not in protected
        )

    def can_open_tool_line(self, caller_branch: str) -> bool:
        """Only root (always) and current default branch may open tool lines."""
        return caller_branch == ROOT_BRANCH_ID or caller_branch == self._default_branch

    def open_branch(
        self,
        parent_id: str,
        source: str,
        prompt: str,
        at: int | None = None,
        fail_build: bool = False,
    ) -> str:
        if parent_id not in self.nodes:
            raise DimError("branch_not_found", f"Parent {parent_id} not found")
        parent = self.nodes[parent_id]
        if parent.ended:
            raise DimError("branch_ended", f"Parent {parent_id} already ended")

        if source == "tool":
            if not self.can_open_tool_line(parent_id):
                raise DimError(
                    "branch_not_opener",
                    f"Line {parent_id} is neither root nor current default branch",
                )
            if self.counted_tool_lines() >= self.max_open_lines:
                raise DimError(
                    "branch_limit_reached",
                    f"Session already holds {self.max_open_lines} counted tool lines",
                )

        cut = parent.timeline.published() if at is None else at
        if cut < 0 or cut > parent.timeline.published():
            raise DimError("invalid_cut", f"Cut {cut} out of range for {parent_id}")

        self._counter += 1
        child_id = f"b-{self._counter}"
        ancestors = list(parent.timeline.ancestors) + [(parent_id, cut)]

        # Reserve slot atomically before committing branch/create.
        child_tl = Timeline(child_id, ancestors=ancestors, store=self._store)
        child_dp = Dispatcher(child_tl)
        child_node = BranchNode(
            branch_id=child_id,
            parent=parent_id,
            source=source,
            cut=cut,
            timeline=child_tl,
            dispatcher=child_dp,
            reserved=True,
        )
        self.nodes[child_id] = child_node

        create_ev = parent.timeline.append(
            EventDraft(
                domain="branch",
                name="create",
                data={"branch": child_id, "source": source, "at": cut, "prompt": prompt},
            )
        )

        if fail_build:
            del self.nodes[child_id]
            parent.timeline.append(
                EventDraft(
                    domain="branch",
                    name="create-failed",
                    data={"branch": child_id, "create_event": create_ev.sequence},
                )
            )
            raise DimError("branch_build_failed", f"Build failed for {child_id}")

        child_tl.append(
            EventDraft(
                domain="branch",
                name="start",
                data={"parent": parent_id, "create_event": create_ev.sequence},
            )
        )
        child_tl.append(
            EventDraft(
                domain="input",
                name="message",
                data={
                    "mode": "queue",
                    "blocks": [
                        {
                            "type": "raw",
                            "key": "branch:line",
                            "text": f'<dim-branch-line branch="{child_id}" parent="{parent_id}" source="{source}">{prompt}</dim-branch-line>',
                        }
                    ],
                },
            )
        )
        child_node.reserved = False
        return child_id

    def set_default_branch(self, target_id: str) -> AgentEvent:
        if target_id == self._default_branch:
            raise DimError("branch_already_default", f"{target_id} is already default")
        node = self.nodes.get(target_id)
        if node is None or node.ended or node.source == "plugin":
            raise DimError("branch_not_selectable", f"{target_id} cannot be selected as default")
        previous = self._default_branch
        self._default_branch = target_id
        # Authoritative non-model fact is always written on the root line (`main`).
        return self.nodes[ROOT_BRANCH_ID].timeline.append(
            EventDraft(
                domain="branch",
                name="default",
                data={"branch": target_id, "from": previous},
            )
        )

    def end_branch(self, target_id: str, caller_source: str = "tool") -> None:
        node = self.nodes.get(target_id)
        if node is None:
            raise DimError("branch_not_found", f"Unknown branch {target_id}")
        if node.ended:
            raise DimError("branch_ended", f"{target_id} already ended")
        if target_id in self.protected_path():
            raise DimError(
                "branch_cannot_end",
                f"Cannot end {target_id}: it is root, default branch, or an ancestor of default",
            )
        if caller_source == "tool" and node.source != "tool":
            raise DimError(
                "branch_cannot_end",
                f"Line {target_id} ({node.source}) cannot end itself via tool",
            )

        # Cascade to open descendants first.
        children = [
            cid
            for cid, cnode in self.nodes.items()
            if cnode.parent == target_id and not cnode.ended
        ]
        for cid in children:
            self.end_branch(cid, caller_source="cascade")

        node.timeline.append(EventDraft("branch", "end", {"branch": target_id}))
        node.ended = True
        if node.parent is not None:
            self.nodes[node.parent].timeline.append(
                EventDraft("branch", "ended", {"branch": target_id})
            )

    def message_branch(self, caller_id: str, target_id: str, content: str, mode: str = "append") -> AgentEvent:
        if mode not in ("append", "steer"):
            raise DimError("invalid_mode", f"Unsupported message mode: {mode}")
        caller = self.nodes[caller_id]
        target = self.nodes[target_id]
        if target.ended:
            raise DimError("branch_ended", f"{target_id} has ended")
        if caller.source == "plugin" or target.source == "plugin":
            raise DimError("branch_not_tool_line", "Plugin lines do not use tool messaging")
        is_parent_to_tool_child = target.parent == caller_id and target.source == "tool"
        is_child_to_parent = caller.parent == target_id
        if not (is_parent_to_tool_child or is_child_to_parent):
            raise DimError("branch_not_kin", f"{caller_id} cannot message non-kin {target_id}")
        return target.timeline.append(
            EventDraft(
                domain="input",
                name="message",
                data={
                    "mode": mode,
                    "blocks": [
                        {
                            "type": "raw",
                            "text": f'<dim-branch-message from="{caller_id}" to="{target_id}">{content}</dim-branch-message>',
                        }
                    ],
                },
            )
        )

    def read_branch(self, caller_id: str, target_id: str, after: int | None = None) -> tuple[list[AgentEvent], int]:
        caller = self.nodes[caller_id]
        target = self.nodes[target_id]
        if target.source == "plugin":
            raise DimError("branch_not_tool_line", "Cannot read plugin line via tool")
        is_parent_reading_tool_child = target.parent == caller_id and target.source == "tool"
        is_child_reading_parent = caller.parent == target_id
        if not (is_parent_reading_tool_child or is_child_reading_parent):
            raise DimError("branch_not_kin", f"{caller_id} cannot read non-kin {target_id}")

        key = (caller_id, target_id)
        min_start = caller.cut if is_child_reading_parent else 0
        if after is None:
            start = self._read_cursors.get(key, min_start)
        else:
            start = max(after, min_start) if is_child_reading_parent else after

        head = target.timeline.published()
        if start > head:
            raise DimError("invalid_cursor", f"after={start} exceeds head={head}")

        all_target = target.timeline.query(through=head)
        page = [ev for ev in all_target if ev.sequence > start]
        next_cursor = page[-1].sequence if page else start
        self._read_cursors[key] = next_cursor
        return page, next_cursor

    def restore_default_branch_from_root(self) -> str:
        """Restore default branch from root's newest branch/default or session/forked fact."""
        root_events = self.nodes[ROOT_BRANCH_ID].timeline.query(
            keys=("branch/default", "session/forked")
        )
        if not root_events:
            return ROOT_BRANCH_ID
        newest = root_events[-1]
        if newest.key == "session/forked":
            return ROOT_BRANCH_ID
        candidate = newest.data.get("branch", ROOT_BRANCH_ID)
        node = self.nodes.get(candidate)
        if node is None or node.ended or node.source == "plugin":
            return ROOT_BRANCH_ID
        return candidate


class ApprovalCapability:
    """Branch-level Approval state machine driven strictly by Dispatcher facts."""

    def __init__(self, timeline: Timeline, dispatcher: Dispatcher) -> None:
        self.timeline = timeline
        self.dispatcher = dispatcher
        self.pending: dict[str, dict[str, Any]] = {}
        self.resolved: dict[str, str] = {}
        self.executed_side_effects: list[str] = []
        self._on_allow: dict[str, Callable[[], None]] = {}
        self._live = False

        self.dispatcher.consume(
            name="Approval",
            keys=("approval/*", "stop/interrupt"),
            rebuild=self._rebuild,
            handler=self._handle_live,
        )
        self._live = True

    def _fold(self, ev: AgentEvent, execute_side_effects: bool) -> None:
        if ev.key == "approval/request":
            aid = ev.data["approval_id"]
            self.pending[aid] = copy.deepcopy(ev.data)
        elif ev.key == "approval/respond":
            aid = ev.data["approval_id"]
            if aid not in self.pending:
                # First valid decision wins; duplicate/late decisions are ignored.
                return
            self.pending.pop(aid)
            decision = ev.data["decision"]
            self.resolved[aid] = decision
            if execute_side_effects and decision == "allow":
                cb = self._on_allow.pop(aid, None)
                if cb is not None:
                    cb()
                self.executed_side_effects.append(aid)
            else:
                self._on_allow.pop(aid, None)
        elif ev.key == "approval/cancelled":
            aid = ev.data["approval_id"]
            self.pending.pop(aid, None)
            self._on_allow.pop(aid, None)
            self.resolved.setdefault(aid, "cancelled")
        elif ev.key == "stop/interrupt":
            scope = ev.data.get("scope", "turn")
            if execute_side_effects:
                for aid in list(self.pending.keys()):
                    self.timeline.append(
                        EventDraft(
                            "approval",
                            "cancelled",
                            {"approval_id": aid, "reason": f"interrupted:{scope}"},
                        )
                    )

    def _rebuild(self, timeline: Timeline) -> int:
        cutoff = timeline.published()
        for ev in timeline.query(
            through=cutoff, keys=("approval/*", "stop/interrupt")
        ):
            # Side effects NEVER replay during rebuild.
            self._fold(ev, execute_side_effects=False)
        return cutoff

    def _handle_live(self, ev: AgentEvent) -> None:
        self._fold(ev, execute_side_effects=True)

    def request(
        self,
        approval_id: str,
        tool_name: str,
        reason: str,
        on_allow: Callable[[], None] | None = None,
    ) -> AgentEvent:
        if on_allow is not None:
            self._on_allow[approval_id] = on_allow
        return self.timeline.append(
            EventDraft(
                domain="approval",
                name="request",
                data={"approval_id": approval_id, "tool": tool_name, "reason": reason},
            )
        )

    def respond(self, approval_id: str, decision: str) -> AgentEvent:
        if decision not in ("allow", "deny"):
            raise DimError("invalid_decision", f"Invalid decision: {decision}")
        return self.timeline.append(
            EventDraft(
                domain="approval",
                name="respond",
                data={"approval_id": approval_id, "decision": decision},
            )
        )


@dataclass(frozen=True)
class RecoverySnapshot:
    anchor_sequence: int
    anchor_event_id: str
    interpreter: str
    resume_sequence: int
    summary: str
    messages_before_resume: tuple[str, ...]
    digest: str

    @staticmethod
    def build(
        anchor_event: AgentEvent,
        interpreter: str,
        resume_sequence: int,
        summary: str,
        messages_before_resume: list[str],
    ) -> "RecoverySnapshot":
        payload = json.dumps(
            {
                "anchor_sequence": anchor_event.sequence,
                "anchor_event_id": anchor_event.id,
                "resume_sequence": resume_sequence,
                "summary": summary,
                "messages": messages_before_resume,
            },
            sort_keys=True,
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return RecoverySnapshot(
            anchor_sequence=anchor_event.sequence,
            anchor_event_id=anchor_event.id,
            interpreter=interpreter,
            resume_sequence=resume_sequence,
            summary=summary,
            messages_before_resume=tuple(messages_before_resume),
            digest=digest,
        )

    def verify(self, anchor_event: AgentEvent) -> bool:
        if anchor_event.sequence != self.anchor_sequence or anchor_event.id != self.anchor_event_id:
            return False
        payload = json.dumps(
            {
                "anchor_sequence": self.anchor_sequence,
                "anchor_event_id": self.anchor_event_id,
                "resume_sequence": self.resume_sequence,
                "summary": self.summary,
                "messages": list(self.messages_before_resume),
            },
            sort_keys=True,
        )
        expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return expected == self.digest


INTERPRETER_ID = "dim/context-projection/v4"


def project_context(
    timeline: Timeline, through: int | None = None
) -> tuple[list[str], bool]:
    """Project model messages using verified RecoverySnapshot or full fact replay.

    Returns (messages, used_snapshot).
    """
    head = timeline.published() if through is None else through
    snap = timeline.latest_snapshot(head, INTERPRETER_ID)
    if snap is not None:
        all_events = timeline.query(through=head)
        anchor = next((e for e in all_events if e.sequence == snap.anchor_sequence), None)
        if anchor is not None and anchor.key == "context/compaction" and snap.verify(anchor):
            msgs = [f"[summary] {snap.summary}", *snap.messages_before_resume]
            for ev in all_events:
                if ev.sequence > snap.resume_sequence and ev.key == "input/consumed":
                    msgs.append(ev.data["text"])
            return msgs, True

    # Fallback: deterministic full fact replay preserving the latest completed compaction summary.
    events = timeline.query(through=head)
    compactions = [
        ev
        for ev in events
        if ev.key == "context/compaction" and ev.data.get("status") == "completed"
    ]
    if compactions:
        latest = compactions[-1]
        cursor = latest.data["cursor"]
        summary = latest.data["summary"]
        msgs = [f"[summary] {summary}"]
        for ev in events:
            if ev.sequence > cursor and ev.key == "input/consumed":
                msgs.append(ev.data["text"])
        return msgs, False

    msgs = [ev.data["text"] for ev in events if ev.key == "input/consumed"]
    return msgs, False
