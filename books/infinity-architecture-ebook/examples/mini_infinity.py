"""Zero-dependency teaching model of hydro-project/infinity core invariants.

Models the verified mechanisms from commit e1a3e4b36e5f (2026-11-05):
1. ThreadRef composite wire encoding & local-before-remote ordering
2. ConversationStore + StateStore with Fresh boundaries and position-indexed compaction
3. HistoryManager three-phase pending pipeline, turn buffer, tail-scan dedup, and overflow recovery
4. Slice loop with commit-before-dispatch barrier, sync tool loop-back (spawn_thread),
   RAP async callbacks, subscription child-thread routing, DeferQueue + is_passive(),
   and ErrorClass-driven retries
5. Stacked per-thread sandboxes with squash-to-parent and ancestor-chain write-orig grants
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Callable, Literal
import uuid


TOOL_RESULT_TOO_LARGE_PLACEHOLDER = (
    "[tool result omitted: it was too large for the model's remaining context window]"
)
TOOL_CALL_INTERRUPTED_TEXT = "Tool call interrupted by user"


# ── 1. Typed ThreadRef (infinity-protocol) ──


@dataclass(frozen=True)
class ThreadRef:
    remote: str | None
    id: str

    def __post_init__(self) -> None:
        if self.remote is not None and ("/" in self.remote or not self.remote):
            raise ValueError(f"invalid remote name: {self.remote!r}")
        if not self.id or "/" in self.id:
            raise ValueError(f"invalid thread id: {self.id!r}")

    @classmethod
    def local(cls, thread_id: str) -> ThreadRef:
        return cls(remote=None, id=thread_id)

    @classmethod
    def parse(cls, raw: str) -> ThreadRef:
        if "/" in raw:
            parts = raw.split("/")
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise ValueError(f"invalid thread ref: {raw!r}")
            return cls(remote=parts[0], id=parts[1])
        return cls(remote=None, id=raw)

    def prefixed(self, remote: str) -> ThreadRef:
        return ThreadRef(remote=remote, id=self.id)

    def strip(self, remote: str) -> ThreadRef:
        if self.remote == remote:
            return ThreadRef(remote=None, id=self.id)
        return self

    def sort_key(self) -> tuple[int, str, str]:
        return (0 if self.remote is None else 1, self.remote or "", self.id)

    def __lt__(self, other: ThreadRef) -> bool:
        return self.sort_key() < other.sort_key()

    def __str__(self) -> str:
        return f"{self.remote}/{self.id}" if self.remote else self.id


# ── 2. Message & Provider Protocol Types ──


class ErrorClass(Enum):
    TRANSIENT = "Transient"
    THROTTLED = "Throttled"
    CONTEXT_OVERFLOW = "ContextOverflow"
    FATAL = "Fatal"


class ProviderError(Exception):
    def __init__(self, error_class: ErrorClass, message: str) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.message = message


@dataclass
class InfinityMessage:
    kind: Literal["user", "assistant", "reasoning", "tool_call", "tool_result", "subscription_event"]
    text: str = ""
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] = field(default_factory=dict)
    child_thread_id: str | None = None
    original_tool_call_id: str | None = None


@dataclass
class InputMessage:
    group_id: str
    kind: Literal["user_text", "tool_result", "subscription_event", "thread_report", "compaction_complete", "user_choice"]
    text: str = ""
    tool_call_id: str | None = None
    associative: bool = False
    final: bool = False
    starts_subscription: bool = False
    child_thread_id: str | None = None
    choices: list[str] = field(default_factory=list)


@dataclass
class StreamChunk:
    kind: Literal["text", "reasoning", "tool_call", "final"]
    text: str = ""
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] = field(default_factory=dict)
    total_tokens: int = 0


# ── 3. Stores (ConversationStore & StateStore) ──


@dataclass
class ThreadRecord:
    thread_id: str
    parent_thread_id: str | None = None
    spawn_tool_call_id: str | None = None
    spawn_order: int = 0
    fresh_context: bool = False
    is_subscription_event: bool = False
    is_compaction: bool = False
    closed: bool = False
    messages: list[tuple[InfinityMessage, str]] = field(default_factory=list)
    compactions: list[tuple[int, str]] = field(default_factory=list)  # (up_to_order, summary)


class ConversationStore:
    def __init__(self) -> None:
        self.threads: dict[str, ThreadRecord] = {}

    def ensure_root_thread(self, thread_id: str) -> None:
        if thread_id not in self.threads:
            self.threads[thread_id] = ThreadRecord(thread_id=thread_id)

    def thread_exists(self, thread_id: str) -> bool:
        return thread_id in self.threads

    def is_thread_closed(self, thread_id: str) -> bool:
        rec = self.threads.get(thread_id)
        return bool(rec and rec.closed)

    def close_thread(self, thread_id: str) -> None:
        self.threads[thread_id].closed = True

    def append_messages(self, thread_id: str, items: list[tuple[InfinityMessage, str]]) -> None:
        self.ensure_root_thread(thread_id)
        self.threads[thread_id].messages.extend(items)

    def load_history_up_to(
        self, thread_id: str, start_from: int | None = None, up_to: int | None = None
    ) -> list[InfinityMessage]:
        rec = self.threads.get(thread_id)
        if not rec:
            return []
        lo = start_from or 0
        hi = len(rec.messages) if up_to is None else up_to
        return [m for m, _ in rec.messages[lo:hi]]

    def spawn_thread(
        self,
        parent_thread_id: str,
        spawn_tool_call_id: str,
        *,
        is_for_subscription_event: bool = False,
        fresh_context: bool = False,
        inherit_up_to: int = 0,
        child_id: str | None = None,
    ) -> str:
        tid = child_id or f"thread-{uuid.uuid4().hex[:8]}"
        self.threads[tid] = ThreadRecord(
            thread_id=tid,
            parent_thread_id=parent_thread_id,
            spawn_tool_call_id=spawn_tool_call_id,
            spawn_order=inherit_up_to,
            fresh_context=fresh_context,
            is_subscription_event=is_for_subscription_event,
        )
        return tid

    def get_ancestor_chain(self, thread_id: str) -> list[tuple[str, int]]:
        chain: list[tuple[str, int]] = []
        cur = self.threads.get(thread_id)
        while cur and cur.parent_thread_id is not None:
            chain.append((cur.parent_thread_id, cur.spawn_order))
            cur = self.threads.get(cur.parent_thread_id)
        chain.reverse()
        return chain

    def save_compaction_summary(self, thread_id: str, summary: str, up_to_order: int) -> None:
        rec = self.threads[thread_id]
        rec.compactions.append((up_to_order, summary))
        rec.compactions.sort(key=lambda x: x[0])

    def load_latest_compaction_up_to(
        self, thread_id: str, up_to_order: int | None
    ) -> tuple[str, int] | None:
        rec = self.threads.get(thread_id)
        if not rec or not rec.compactions:
            return None
        candidates = [
            (order, text)
            for order, text in rec.compactions
            if up_to_order is None or order <= up_to_order
        ]
        if not candidates:
            return None
        order, text = candidates[-1]
        return text, order

    def load_history_with_ancestors(
        self, thread_id: str
    ) -> tuple[list[InfinityMessage], int | None, int]:
        own_comp = self.load_latest_compaction_up_to(thread_id, None)
        if own_comp is not None:
            summary, compacted_up_to = own_comp
            combined = [InfinityMessage(kind="assistant", text=f"[Compacted conversation summary]\n{summary}")]
            combined.extend(self.load_history_up_to(thread_id, start_from=compacted_up_to))
            return combined, compacted_up_to, 0

        ancestors = self.get_ancestor_chain(thread_id)
        rec = self.threads.get(thread_id)
        if rec and rec.fresh_context:
            ancestors = []
        else:
            for i in range(len(ancestors) - 1, -1, -1):
                anc_rec = self.threads.get(ancestors[i][0])
                if anc_rec and anc_rec.fresh_context:
                    ancestors = ancestors[i:]
                    break

        comp_idx: int | None = None
        comp_info: tuple[str, int] | None = None
        for i in range(len(ancestors) - 1, -1, -1):
            anc_id, cutoff = ancestors[i]
            found = self.load_latest_compaction_up_to(anc_id, cutoff)
            if found is not None:
                comp_idx = i
                comp_info = found
                break

        combined: list[InfinityMessage] = []
        if comp_idx is not None and comp_info is not None:
            summary, compacted_up_to = comp_info
            combined.append(
                InfinityMessage(kind="assistant", text=f"[Compacted conversation summary]\n{summary}")
            )
            anc_id, cutoff = ancestors[comp_idx]
            combined.extend(self.load_history_up_to(anc_id, start_from=compacted_up_to, up_to=cutoff))
            for anc_id, cutoff in ancestors[comp_idx + 1 :]:
                combined.extend(self.load_history_up_to(anc_id, up_to=cutoff))
        else:
            for anc_id, cutoff in ancestors:
                combined.extend(self.load_history_up_to(anc_id, up_to=cutoff))

        ancestor_prefix_len = len(combined)
        combined.extend(self.load_history_up_to(thread_id))
        return combined, None, ancestor_prefix_len


class StateStore:
    def __init__(self) -> None:
        self.processed_ids: dict[str, set[str]] = {}
        self.active_subscriptions: dict[str, set[str]] = {}
        self.stopped_threads: set[str] = set()

    def get_processed_ids(self, thread_id: str) -> set[str]:
        return set(self.processed_ids.get(thread_id, set()))

    def add_processed_ids(self, thread_id: str, ids: list[str]) -> None:
        self.processed_ids.setdefault(thread_id, set()).update(ids)

    def add_subscription(self, thread_id: str, tool_call_id: str) -> None:
        self.active_subscriptions.setdefault(thread_id, set()).add(tool_call_id)

    def remove_subscription(self, thread_id: str, tool_call_id: str) -> None:
        self.active_subscriptions.setdefault(thread_id, set()).discard(tool_call_id)

    def is_thread_stopped(self, thread_id: str) -> bool:
        return thread_id in self.stopped_threads


# ── 4. Three-Phase HistoryManager (event_processor.rs) ──


class ToolResultMatch(Enum):
    UNANSWERED = "unanswered"
    ALREADY_ANSWERED = "already_answered"
    NO_PENDING_CALL = "no_pending_call"


class HistoryManager:
    def __init__(
        self, conv: ConversationStore, state: StateStore, thread_id: str
    ) -> None:
        conv.ensure_root_thread(thread_id)
        self.conv = conv
        self.state = state
        self.thread_id = thread_id
        self.ancestor_chain = [tid for tid, _ in conv.get_ancestor_chain(thread_id)]
        self.root_thread_id = self.ancestor_chain[0] if self.ancestor_chain else thread_id
        history, compacted_up_to, ancestor_prefix_len = conv.load_history_with_ancestors(thread_id)
        self.history: list[InfinityMessage] = history
        self.compacted_up_to: int | None = compacted_up_to
        self.ancestor_prefix_len: int = ancestor_prefix_len
        self.processed_ids: set[str] = state.get_processed_ids(thread_id)

        # Three-phase unpersisted layers
        self.pending_items: list[tuple[InfinityMessage, str]] = []
        self.unvalidated_items: list[tuple[InfinityMessage, str]] = []
        self.turn_buffer: list[tuple[InfinityMessage, str]] = []
        self.interrupted_tool_calls: list[str] = []

    def thread_stack(self) -> list[str]:
        return [*self.ancestor_chain, self.thread_id]

    def match_tool_result(self, result_id: str) -> ToolResultMatch:
        for msg in reversed(self.history):
            if msg.kind == "tool_call":
                if msg.tool_call_id == result_id:
                    return ToolResultMatch.UNANSWERED
            elif msg.kind == "tool_result":
                if msg.tool_call_id == result_id:
                    return ToolResultMatch.ALREADY_ANSWERED
            else:
                return ToolResultMatch.NO_PENDING_CALL
        return ToolResultMatch.NO_PENDING_CALL

    def _interrupt_pending_tool_call(self) -> None:
        if self.history and self.history[-1].kind == "tool_call":
            tc = self.history[-1]
            assert tc.tool_call_id is not None
            self.interrupted_tool_calls.append(tc.tool_call_id)
            synth = InfinityMessage(
                kind="tool_result",
                tool_call_id=tc.tool_call_id,
                text=TOOL_CALL_INTERRUPTED_TEXT,
            )
            self._append_unvalidated(synth, f"{tc.tool_call_id}-interrupted")

    def handle_content(self, message: InfinityMessage, message_id: str) -> bool:
        if message_id in self.processed_ids:
            return False
        if message.kind == "tool_result":
            assert message.tool_call_id is not None
            m = self.match_tool_result(message.tool_call_id)
            if m == ToolResultMatch.ALREADY_ANSWERED:
                self.processed_ids.add(message_id)
                return False
            if m == ToolResultMatch.NO_PENDING_CALL:
                return False
        else:
            self._interrupt_pending_tool_call()

        self._append_unvalidated(message, message_id)
        self.processed_ids.add(message_id)
        return True

    def _append_unvalidated(self, message: InfinityMessage, message_id: str) -> None:
        assert not self.turn_buffer, "bug: append_unvalidated with non-empty turn_buffer"
        self.history.append(message)
        self.unvalidated_items.append((message, message_id))

    def append_known_safe(self, message: InfinityMessage, message_id: str) -> None:
        assert not self.turn_buffer, "bug: append_known_safe with non-empty turn_buffer"
        assert not self.unvalidated_items, (
            "bug: committing model output while unvalidated inputs exist"
        )
        self.history.append(message)
        self.pending_items.append((message, message_id))

    def mark_inputs_model_validated(self) -> None:
        self.pending_items.extend(self.unvalidated_items)
        self.unvalidated_items.clear()

    def handle_completion_chunk(self, chunk: StreamChunk, completion_id: str) -> None:
        if chunk.kind == "text":
            if self.turn_buffer and self.turn_buffer[-1][0].kind == "assistant":
                self.turn_buffer[-1][0].text += chunk.text
                return
            self.turn_buffer.append((InfinityMessage(kind="assistant", text=chunk.text), completion_id))
        elif chunk.kind == "reasoning":
            self.turn_buffer.append((InfinityMessage(kind="reasoning", text=chunk.text), completion_id))
        elif chunk.kind == "tool_call":
            self.turn_buffer.append(
                (
                    InfinityMessage(
                        kind="tool_call",
                        tool_call_id=chunk.tool_call_id,
                        tool_name=chunk.tool_name,
                        tool_args=dict(chunk.tool_args),
                    ),
                    completion_id,
                )
            )

    def flush_turn(self, *, trim_trailing_reasoning: bool = False) -> None:
        if trim_trailing_reasoning:
            while self.turn_buffer:
                msg = self.turn_buffer[-1][0]
                if msg.kind == "reasoning" or (msg.kind == "assistant" and not msg.text.strip()):
                    self.turn_buffer.pop()
                else:
                    break
        drained = list(self.turn_buffer)
        self.turn_buffer.clear()
        for msg, mid in drained:
            self.append_known_safe(msg, mid)

    def discard_turn(self) -> None:
        self.turn_buffer.clear()

    def current_turn_view(self) -> list[InfinityMessage]:
        return [*self.history, *(m for m, _ in self.turn_buffer)]

    def sync(self) -> None:
        assert not self.turn_buffer, "bug: sync() called with un-flushed turn_buffer"
        if not self.pending_items:
            return
        to_persist = list(self.pending_items)
        self.pending_items.clear()
        self.conv.append_messages(self.thread_id, to_persist)
        non_idempotent_ids = [
            mid
            for msg, mid in to_persist
            if msg.kind in ("user", "subscription_event")
        ]
        if non_idempotent_ids:
            self.state.add_processed_ids(self.thread_id, non_idempotent_ids)

    def replace_unvalidated_tool_results(self, placeholder: str) -> bool:
        if not self.unvalidated_items:
            return False
        tail_start = len(self.history) - len(self.unvalidated_items)
        replaced = False
        for i, (msg, _) in enumerate(self.unvalidated_items):
            if msg.kind not in ("tool_result", "subscription_event"):
                continue
            if msg.text == placeholder:
                continue
            msg.text = placeholder
            self.history[tail_start + i] = msg
            replaced = True
        return replaced

    def drop_unvalidated_user_inputs(self) -> int:
        if not self.unvalidated_items:
            return 0
        tail_start = len(self.history) - len(self.unvalidated_items)
        kept: list[tuple[InfinityMessage, str]] = []
        dropped = 0
        for msg, mid in self.unvalidated_items:
            if msg.kind in ("tool_result", "subscription_event"):
                kept.append((msg, mid))
            else:
                self.processed_ids.discard(mid)
                dropped += 1
        del self.history[tail_start:]
        self.history.extend(m for m, _ in kept)
        self.unvalidated_items = kept
        return dropped

    def recover_from_overflow(self) -> tuple[Literal["retry", "dropped"], int]:
        if len(self.unvalidated_items) == 1 and self.replace_unvalidated_tool_results(
            TOOL_RESULT_TOO_LARGE_PLACEHOLDER
        ):
            return ("retry", 0)
        self.replace_unvalidated_tool_results(TOOL_CALL_INTERRUPTED_TEXT)
        dropped = self.drop_unvalidated_user_inputs()
        return ("dropped", dropped)

    def safe_spawn_point(self) -> int:
        answered: set[str] = set()
        safe = len(self.history)
        for i in range(len(self.history) - 1, -1, -1):
            msg = self.history[i]
            if msg.kind == "tool_call":
                if msg.tool_call_id not in answered:
                    safe = i
            elif msg.kind == "tool_result":
                if msg.tool_call_id:
                    answered.add(msg.tool_call_id)
            else:
                break
        validated_len = len(self.history) - len(self.unvalidated_items)
        safe = min(safe, validated_len)
        offset = 0 if self.compacted_up_to is None else (self.compacted_up_to - 1)
        return max(0, safe - self.ancestor_prefix_len) + offset

    def find_tool_call(self, tool_call_id: str) -> InfinityMessage | None:
        for msg in reversed(self.history):
            if msg.kind == "tool_call" and msg.tool_call_id == tool_call_id:
                return msg
        for msg in reversed(self.conv.load_history_up_to(self.thread_id)):
            if msg.kind == "tool_call" and msg.tool_call_id == tool_call_id:
                return msg
        return None


# ── 5. DeferQueue & Slice Runtime (system/defer.rs, thread.rs, router.rs) ──


class DeferQueue:
    def push(self, item: tuple[InputMessage, str]) -> bool:
        raise NotImplementedError

    def drain(self) -> list[tuple[InputMessage, str]]:
        raise NotImplementedError


class InMemoryDeferQueue(DeferQueue):
    def __init__(self) -> None:
        self._items: list[tuple[InputMessage, str]] = []

    def push(self, item: tuple[InputMessage, str]) -> bool:
        self._items.append(item)
        return True

    def drain(self) -> list[tuple[InputMessage, str]]:
        out = list(self._items)
        self._items.clear()
        return out


class NoDeferral(DeferQueue):
    def push(self, item: tuple[InputMessage, str]) -> bool:
        return False

    def drain(self) -> list[tuple[InputMessage, str]]:
        return []


@dataclass
class ToolSpec:
    name: str
    is_passive: bool = False
    supports_sync: bool = False
    handler: Callable[[dict[str, Any], str, str], str | None] | None = None


class MiniInfinityRuntime:
    """Stateless-slice runtime + router modeling local and serverless execution."""

    def __init__(
        self,
        model_fn: Callable[[str, list[InfinityMessage]], list[StreamChunk]],
        *,
        use_deferral: bool = True,
        max_retries: int = 3,
    ) -> None:
        self.conv = ConversationStore()
        self.state = StateStore()
        self.model_fn = model_fn
        self.use_deferral = use_deferral
        self.max_retries = max_retries
        self.tools: dict[str, ToolSpec] = {
            "spawn_thread": ToolSpec(name="spawn_thread", supports_sync=True),
            "sleep": ToolSpec(name="sleep", is_passive=True),
        }
        self.defer_queues: dict[str, DeferQueue] = {}
        self.dispatched_async_calls: list[tuple[str, str, str, dict[str, Any], int]] = []
        self.queue: list[tuple[InputMessage, str]] = []
        self.woken_threads: list[str] = []

    def register_tool(self, spec: ToolSpec) -> None:
        self.tools[spec.name] = spec

    def enqueue(self, msg: InputMessage, dedup_id: str | None = None) -> None:
        self.queue.append((msg, dedup_id or uuid.uuid4().hex))

    def _defer_queue(self, thread_id: str) -> DeferQueue:
        if thread_id not in self.defer_queues:
            self.defer_queues[thread_id] = InMemoryDeferQueue() if self.use_deferral else NoDeferral()
        return self.defer_queues[thread_id]

    def _pending_active_tool_call(self, hm: HistoryManager) -> str | None:
        if not hm.history or hm.history[-1].kind != "tool_call":
            return None
        tc = hm.history[-1]
        spec = self.tools.get(tc.tool_name or "")
        if spec and spec.is_passive:
            return None
        return tc.tool_call_id

    def _is_deferrable(self, msg: InputMessage) -> bool:
        return msg.kind in ("subscription_event", "thread_report")

    def drain_all(self) -> int:
        steps = 0
        while self.queue:
            msg, mid = self.queue.pop(0)
            if self.process_one(msg, mid):
                steps += 1
        return steps

    def process_one(self, msg: InputMessage, message_id: str) -> bool:
        tid = msg.group_id
        # Admission policy (router.rs): non-user-text cannot wake unknown or stopped threads
        if msg.kind != "user_text":
            if not self.conv.thread_exists(tid) or self.state.is_thread_stopped(tid):
                return False
        else:
            self.state.stopped_threads.discard(tid)

        if self.conv.is_thread_closed(tid):
            return False

        self.woken_threads.append(tid)
        hm = HistoryManager(self.conv, self.state, tid)
        dq = self._defer_queue(tid)

        # Deferral filter (thread.rs)
        batch: list[tuple[InputMessage, str]] = [(msg, message_id)]
        pending_active = self._pending_active_tool_call(hm)
        if pending_active is None:
            batch.extend(dq.drain())
        else:
            settles = msg.kind == "user_text" or (
                msg.kind == "tool_result" and msg.tool_call_id == pending_active
            )
            if settles:
                batch.extend(dq.drain())
            elif self._is_deferrable(msg) and dq.push((msg, message_id)):
                return False

        any_ready = False
        for item_msg, item_id in batch:
            if self._prepare_one(hm, item_msg, item_id):
                any_ready = True

        if not any_ready:
            hm.sync()
            return False

        # Completion loop (supports sync tool loop-back & ErrorClass retries)
        pending_async_call: InfinityMessage | None = None
        retries = 0
        while True:
            try:
                chunks = self.model_fn(tid, hm.history)
            except ProviderError as err:
                hm.discard_turn()
                if err.error_class in (ErrorClass.TRANSIENT, ErrorClass.THROTTLED):
                    if retries < self.max_retries:
                        retries += 1
                        continue
                    raise
                if err.error_class == ErrorClass.CONTEXT_OVERFLOW:
                    mode, _ = hm.recover_from_overflow()
                    if mode == "retry":
                        retries += 1
                        continue
                    hm.sync()
                    return False
                raise

            validated = False
            sync_call: InfinityMessage | None = None
            for idx, chunk in enumerate(chunks):
                if chunk.kind != "final" and not validated:
                    hm.mark_inputs_model_validated()
                    validated = True
                hm.handle_completion_chunk(chunk, f"{message_id}-c{idx}")
                if chunk.kind == "tool_call":
                    spec = self.tools.get(chunk.tool_name or "")
                    if spec and spec.supports_sync:
                        hm.flush_turn()
                        sync_call = hm.history[-1]
                        break
                    else:
                        hm.flush_turn()
                        pending_async_call = hm.history[-1]
                        break
                elif chunk.kind == "final":
                    if not validated:
                        hm.mark_inputs_model_validated()
                    hm.flush_turn(trim_trailing_reasoning=True)

            if sync_call is not None:
                res_text = self._execute_sync_tool(hm, sync_call)
                hm.handle_content(
                    InfinityMessage(
                        kind="tool_result",
                        tool_call_id=sync_call.tool_call_id,
                        text=res_text,
                    ),
                    f"{sync_call.tool_call_id}-sync-res",
                )
                continue
            break

        # Commit barrier BEFORE dispatching async tool (durability invariant)
        hm.sync()
        persisted_len_before_dispatch = len(self.conv.threads[tid].messages)

        if pending_async_call is not None:
            assert pending_async_call.tool_call_id is not None
            assert pending_async_call.tool_name is not None
            self.dispatched_async_calls.append(
                (
                    tid,
                    pending_async_call.tool_call_id,
                    pending_async_call.tool_name,
                    pending_async_call.tool_args,
                    persisted_len_before_dispatch,
                )
            )
            spec = self.tools.get(pending_async_call.tool_name)
            if spec and spec.handler:
                spec.handler(pending_async_call.tool_args, pending_async_call.tool_call_id, tid)

        return True

    def _prepare_one(self, hm: HistoryManager, msg: InputMessage, mid: str) -> bool:
        if msg.kind == "user_text":
            return hm.handle_content(InfinityMessage(kind="user", text=msg.text), mid)
        if msg.kind == "tool_result":
            accepted = hm.handle_content(
                InfinityMessage(kind="tool_result", tool_call_id=msg.tool_call_id, text=msg.text),
                mid,
            )
            if accepted and msg.starts_subscription and msg.tool_call_id:
                self.state.add_subscription(hm.thread_id, msg.tool_call_id)
            return accepted
        if msg.kind == "subscription_event":
            assert msg.tool_call_id is not None
            orig = hm.find_tool_call(msg.tool_call_id)
            if orig is None:
                return False
            if msg.final:
                self.state.remove_subscription(hm.thread_id, msg.tool_call_id)
            if msg.associative:
                return hm.handle_content(
                    InfinityMessage(
                        kind="subscription_event",
                        tool_call_id=msg.tool_call_id,
                        original_tool_call_id=msg.tool_call_id,
                        text=msg.text,
                    ),
                    mid,
                )
            # Non-associative subscription event -> auto-spawn isolated child thread
            safe_point = hm.safe_spawn_point()
            child_id = self.conv.spawn_thread(
                hm.thread_id,
                msg.tool_call_id,
                is_for_subscription_event=True,
                inherit_up_to=safe_point,
            )
            ev_call_id = f"ev-{uuid.uuid4().hex[:8]}"
            self.conv.append_messages(
                child_id,
                [
                    (
                        InfinityMessage(
                            kind="tool_call",
                            tool_call_id=ev_call_id,
                            tool_name="receive_event__injected",
                            tool_args={"original_tool_call_id": msg.tool_call_id},
                        ),
                        f"{ev_call_id}-call",
                    )
                ],
            )
            self.enqueue(
                InputMessage(
                    group_id=child_id,
                    kind="tool_result",
                    tool_call_id=ev_call_id,
                    text=msg.text,
                ),
                ev_call_id,
            )
            return False
        if msg.kind == "thread_report":
            assert msg.tool_call_id is not None
            if hm.find_tool_call(msg.tool_call_id) is None:
                return False
            return hm.handle_content(
                InfinityMessage(
                    kind="subscription_event",
                    tool_call_id=msg.tool_call_id,
                    child_thread_id=msg.child_thread_id,
                    text=msg.text,
                ),
                mid,
            )
        return False

    def _execute_sync_tool(self, hm: HistoryManager, call: InfinityMessage) -> str:
        if call.tool_name == "spawn_thread":
            child_of = call.tool_args.get("child_of", [])
            if child_of != hm.thread_stack():
                return (
                    f"Error: child_of {child_of} does not match actual thread stack "
                    f"{hm.thread_stack()}"
                )
            fresh = bool(call.tool_args.get("fresh_context", False))
            safe_point = hm.safe_spawn_point()
            assert call.tool_call_id is not None
            child_id = self.conv.spawn_thread(
                hm.thread_id,
                call.tool_call_id,
                fresh_context=fresh,
                inherit_up_to=safe_point,
                child_id=call.tool_args.get("child_id"),
            )
            # Seed child's own copy of spawn_thread so it survives parent compaction
            self.conv.append_messages(
                child_id,
                [
                    (
                        InfinityMessage(
                            kind="tool_call",
                            tool_call_id=call.tool_call_id,
                            tool_name="spawn_thread",
                            tool_args=dict(call.tool_args),
                        ),
                        f"{call.tool_call_id}-spawn-call",
                    )
                ],
            )
            self.enqueue(
                InputMessage(
                    group_id=child_id,
                    kind="tool_result",
                    tool_call_id=call.tool_call_id,
                    text=call.tool_args.get("instructions", ""),
                ),
                f"{call.tool_call_id}-child-seed",
            )
            return f"Child thread spawned: {child_id}"
        return f"Error: unknown sync tool {call.tool_name}"


# ── 6. Stacked Sandbox Workspace & Write Confinement (sandbox-core / sandbox-local) ──


@dataclass
class SandboxWorkspace:
    thread_id: str
    bookmark: str
    base_snapshot: dict[str, str]
    files: dict[str, str]
    sandbox_root: str
    write_orig_granted: bool = False
    granted_paths: set[str] = field(default_factory=set)


class StackedSandboxManager:
    """Models jj/git stacked workspaces, squash_sandbox, and OS write-path confinement."""

    def __init__(self, repo_root: str, host_files: dict[str, str]) -> None:
        self.repo_root = str(PurePosixPath(repo_root))
        self.host_files: dict[str, str] = dict(host_files)
        self.workspaces: dict[str, SandboxWorkspace] = {}

    def create_sandbox(self, thread_id: str, *, base_thread_id: str | None = None) -> SandboxWorkspace:
        if base_thread_id is not None:
            base = dict(self.workspaces[base_thread_id].files)
        else:
            base = dict(self.host_files)
        ws = SandboxWorkspace(
            thread_id=thread_id,
            bookmark=f"sandbox-{thread_id}",
            base_snapshot=dict(base),
            files=dict(base),
            sandbox_root=f"{self.repo_root}/.infinity/.sandboxes/{thread_id}",
        )
        self.workspaces[thread_id] = ws
        return ws

    def edit_file(self, thread_id: str, rel_path: str, content: str) -> None:
        ws = self.workspaces[thread_id]
        ws.files[rel_path] = content

    def diff_files(self, thread_id: str) -> dict[str, tuple[str | None, str | None]]:
        ws = self.workspaces[thread_id]
        keys = set(ws.base_snapshot) | set(ws.files)
        return {
            k: (ws.base_snapshot.get(k), ws.files.get(k))
            for k in sorted(keys)
            if ws.base_snapshot.get(k) != ws.files.get(k)
        }

    def squash_sandbox(self, target_thread_id: str, from_thread_id: str) -> list[str]:
        target = self.workspaces[target_thread_id]
        child_diff = self.diff_files(from_thread_id)
        conflicts: list[str] = []
        for path, (child_base, child_new) in child_diff.items():
            if target.files.get(path) != child_base:
                conflicts.append(path)
            else:
                if child_new is None:
                    target.files.pop(path, None)
                else:
                    target.files[path] = child_new
        return conflicts

    def grant_write_orig(self, thread_id: str) -> None:
        self.workspaces[thread_id].write_orig_granted = True

    def check_write_allowed(self, thread_chain: list[str], target_path: str) -> tuple[bool, str]:
        """Verify bwrap / sandbox-exec write confinement + ancestor-chain grant."""
        norm = str(PurePosixPath(target_path))
        leaf = self.workspaces[thread_chain[-1]]
        if norm == leaf.sandbox_root or norm.startswith(leaf.sandbox_root + "/"):
            return True, "inside_sandbox"
        # Check write-orig across full ancestor chain (sandbox-core/src/server.rs)
        if norm == self.repo_root or norm.startswith(self.repo_root + "/"):
            if any(
                tid in self.workspaces and self.workspaces[tid].write_orig_granted
                for tid in thread_chain
            ):
                return True, "write_orig_granted"
            return False, "needs_user_choice:write-orig"
        if any(
            tid in self.workspaces
            and any(norm == p or norm.startswith(p + "/") for p in self.workspaces[tid].granted_paths)
            for tid in thread_chain
        ):
            return True, "explicit_path_granted"
        return False, f"needs_user_choice:write:{norm}"
