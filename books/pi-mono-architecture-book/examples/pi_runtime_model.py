"""Zero-dependency Python 3.10+ teaching model for Pi v1.6.0 core invariants.

Models four architectural mechanisms directly grounded in upstream Pi source:
1. TreeSession: JSONL id/parentId tree, compaction + context_edit projection,
   truncated tool-call pairing, and branch-relative codemode-store replay.
2. CodeModeRuntime: pi-schema-style coercion/validation, extension hook gating,
   FIFO worker admission with shared deadline budget, and store vs side-effect separation.
3. StreamingJsonToolParser & TuiContainer: v1.5.0 incremental tool-call JSON parsing
   with copy-credit throttling, frame-coalesced argsPending, and v1.6.0 read-only
   Container render cache identity reuse.
4. ToolRegistry: MCP exposure rules (direct/codemode/deferred/hidden), CJK-aware
   tool search ranking, tool_search deferred loading, and reachesTools lazy connect.

Scope: offline teaching model for unit verification, not a QuickJS security sandbox
or upstream conformance suite.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import copy
import json
import re
from typing import Any, Callable


# ---------------------------------------------------------------------------
# 1. Tree Session, Context Projection & Branch-Relative Store
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SessionEntry:
    id: str
    parent_id: str | None
    kind: str  # "message" | "compaction" | "context_edit" | "codemode-store"
    payload: dict[str, Any]


class TreeSession:
    """In-memory model of Pi's id/parentId JSONL session tree."""

    def __init__(self) -> None:
        self._entries: dict[str, SessionEntry] = {}
        self._seq = 0
        self.leaf_id: str | None = None

    def _append(self, kind: str, payload: dict[str, Any]) -> SessionEntry:
        self._seq += 1
        entry_id = f"e{self._seq}"
        entry = SessionEntry(
            id=entry_id,
            parent_id=self.leaf_id,
            kind=kind,
            payload=copy.deepcopy(payload),
        )
        self._entries[entry_id] = entry
        self.leaf_id = entry_id
        return entry

    def append_message(
        self,
        role: str,
        content: str,
        *,
        tool_calls: list[dict[str, Any]] | None = None,
        tool_call_id: str | None = None,
        truncated: bool = False,
    ) -> SessionEntry:
        return self._append(
            "message",
            {
                "role": role,
                "content": content,
                "tool_calls": tool_calls or [],
                "tool_call_id": tool_call_id,
                "truncated": truncated,
            },
        )

    def append_compaction(self, summary: str, keep_from_id: str) -> SessionEntry:
        return self._append(
            "compaction",
            {"summary": summary, "keep_from_id": keep_from_id},
        )

    def append_context_edit(self, target_id: str, replacement: str | None) -> SessionEntry:
        """Omit (replacement=None) or replace a historic entry in future projections."""
        return self._append(
            "context_edit",
            {"target_id": target_id, "replacement": replacement},
        )

    def append_store_commit(self, delta: dict[str, Any]) -> SessionEntry:
        return self._append("codemode-store", {"delta": delta})

    def branch_from(self, entry_id: str) -> None:
        if entry_id not in self._entries:
            raise KeyError(f"Unknown session entry: {entry_id}")
        self.leaf_id = entry_id

    def active_branch(self) -> list[SessionEntry]:
        path: list[SessionEntry] = []
        cursor = self.leaf_id
        while cursor is not None:
            entry = self._entries[cursor]
            path.append(entry)
            cursor = entry.parent_id
        path.reverse()
        return path

    def project_context(self) -> list[dict[str, Any]]:
        """Build the model-visible message list from the active branch.

        Preserves original history on disk while applying compaction boundaries,
        context edits, and v1.4.0+ truncated tool-call / orphan result cleanup.
        """
        branch = self.active_branch()
        edits: dict[str, str | None] = {}
        latest_compaction_idx = -1
        for idx, entry in enumerate(branch):
            if entry.kind == "compaction":
                latest_compaction_idx = idx
            elif entry.kind == "context_edit":
                edits[entry.payload["target_id"]] = entry.payload["replacement"]

        projected: list[dict[str, Any]] = []
        if latest_compaction_idx != -1:
            comp = branch[latest_compaction_idx]
            keep_from_id = comp.payload["keep_from_id"]
            projected.append(
                {
                    "id": comp.id,
                    "role": "summary",
                    "content": comp.payload["summary"],
                }
            )
            start_idx = next(
                (i for i, e in enumerate(branch) if e.id == keep_from_id),
                latest_compaction_idx + 1,
            )
            tail = branch[start_idx:]
        else:
            tail = branch

        valid_call_ids: set[str] = set()
        dropped_call_ids: set[str] = set()

        for entry in tail:
            if entry.kind != "message":
                continue
            if entry.id in edits:
                replacement = edits[entry.id]
                if replacement is None:
                    continue
                content = replacement
            else:
                content = entry.payload["content"]

            role = entry.payload["role"]
            tool_calls = list(entry.payload["tool_calls"])
            if role == "assistant" and entry.payload["truncated"]:
                # Drop incomplete tool calls from truncated assistant replies.
                for call in tool_calls:
                    dropped_call_ids.add(call["id"])
                tool_calls = []
            elif role == "assistant":
                for call in tool_calls:
                    valid_call_ids.add(call["id"])

            if role == "tool_result":
                tc_id = entry.payload["tool_call_id"]
                if tc_id in dropped_call_ids or (tc_id is not None and tc_id not in valid_call_ids):
                    continue

            msg: dict[str, Any] = {"id": entry.id, "role": role, "content": content}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            if entry.payload["tool_call_id"] is not None:
                msg["tool_call_id"] = entry.payload["tool_call_id"]
            projected.append(msg)

        return projected

    def replay_store(self) -> dict[str, Any]:
        """Reconstruct branch-relative Code Mode store along root -> active leaf."""
        state: dict[str, Any] = {}
        for entry in self.active_branch():
            if entry.kind == "codemode-store":
                for key, value in entry.payload["delta"].items():
                    if value is None:
                        state.pop(key, None)
                    else:
                        state[key] = copy.deepcopy(value)
        return state


# ---------------------------------------------------------------------------
# 2. pi-schema-style Coercion, Worker Admission & Code Mode Boundary
# ---------------------------------------------------------------------------

class SchemaValidationError(ValueError):
    """Dotted-path validation failure mirroring @earendil-works/pi-schema."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


def coerce_and_validate(schema: dict[str, Any], raw_args: dict[str, Any]) -> dict[str, Any]:
    """Model pi-schema's JSON Schema coercion and dotted-path validation errors."""
    props: dict[str, dict[str, Any]] = schema.get("properties", {})
    required = set(schema.get("required", []))
    coerced: dict[str, Any] = {}
    errors: list[str] = []

    for key, val in raw_args.items():
        if key not in props:
            if schema.get("additionalProperties") is False:
                errors.append(f"- {key}: unexpected property")
            else:
                coerced[key] = val
            continue
        prop = props[key]
        if val is None and key not in required:
            # pi-schema drops null on optional properties before validation.
            continue
        expected = prop.get("type")
        if expected == "number":
            if isinstance(val, bool):
                errors.append(f"- {key}: expected number, got boolean")
            elif isinstance(val, (int, float)):
                coerced[key] = val
            elif isinstance(val, str):
                try:
                    num = float(val) if "." in val else int(val)
                    coerced[key] = num
                except ValueError:
                    errors.append(f"- {key}: expected number, got string")
            else:
                errors.append(f"- {key}: expected number, got {type(val).__name__}")
        elif expected == "boolean":
            if isinstance(val, bool):
                coerced[key] = val
            elif val == "true":
                coerced[key] = True
            elif val == "false":
                coerced[key] = False
            else:
                errors.append(f"- {key}: expected boolean, got {type(val).__name__}")
        elif expected == "array":
            coerced[key] = val if isinstance(val, list) else [val]
        elif expected == "string":
            if isinstance(val, str):
                coerced[key] = val
            else:
                errors.append(f"- {key}: expected string, got {type(val).__name__}")
        else:
            coerced[key] = val

    for req in sorted(required):
        if req not in coerced:
            errors.append(f"- {req}: missing")

    if errors:
        raise SchemaValidationError(errors)
    return coerced


@dataclass
class WorkerLease:
    worker_id: int
    wait_ms: int
    remaining_deadline_ms: int
    released: bool = False


class WorkerAdmissionPool:
    """Deterministic FIFO model of v1.4.0 reserveCodemodeWorker()."""

    MAX_WAIT_MS = 60_000

    def __init__(self, max_workers: int = 2) -> None:
        self.max_workers = max_workers if max_workers >= 1 else 2
        self._active = 0
        self._next_id = 0
        self._waiters: deque[dict[str, Any]] = deque()
        self.now_ms = 0

    @property
    def active_count(self) -> int:
        return self._active

    def acquire(self, deadline_ms: int, *, wait_until_ms: int | None = None) -> WorkerLease:
        """Reserve a slot immediately or simulate waiting in FIFO queue."""
        if self._active < self.max_workers:
            self._active += 1
            self._next_id += 1
            return WorkerLease(
                worker_id=self._next_id,
                wait_ms=0,
                remaining_deadline_ms=deadline_ms,
            )

        wait_budget = min(deadline_ms, self.MAX_WAIT_MS)
        if wait_until_ms is None or wait_until_ms - self.now_ms > wait_budget:
            raise TimeoutError(
                f"Timed out waiting for a free codemode worker after {wait_budget}ms"
            )

        waited = max(0, wait_until_ms - self.now_ms)
        self.now_ms = wait_until_ms
        remaining = deadline_ms - waited
        if remaining <= 0:
            raise TimeoutError("Total script deadline exhausted while waiting for worker")
        self._active += 1
        self._next_id += 1
        return WorkerLease(
            worker_id=self._next_id,
            wait_ms=waited,
            remaining_deadline_ms=remaining,
        )

    def release(self, lease: WorkerLease) -> None:
        """Release capacity only once the worker has actually exited."""
        if not lease.released:
            lease.released = True
            self._active = max(0, self._active - 1)


@dataclass
class ToolSpec:
    name: str
    schema: dict[str, Any]
    execute: Callable[[dict[str, Any]], Any]


class CodeModeRuntime:
    """Models Code Mode execution through ctx.executeTool(), hooks, and atomic store commit."""

    def __init__(
        self,
        session: TreeSession,
        tools: dict[str, ToolSpec],
        *,
        before_tool_call: Callable[[str, dict[str, Any]], str | None] | None = None,
    ) -> None:
        self.session = session
        self.tools = tools
        self.before_tool_call = before_tool_call
        self.call_log: list[tuple[str, dict[str, Any]]] = []

    def execute_tool(self, name: str, raw_args: dict[str, Any]) -> Any:
        if name not in self.tools:
            raise KeyError(f"Unknown tool: {name}")
        spec = self.tools[name]
        args = coerce_and_validate(spec.schema, raw_args)
        if self.before_tool_call is not None:
            blocked_reason = self.before_tool_call(name, args)
            if blocked_reason is not None:
                raise PermissionError(blocked_reason)
        self.call_log.append((name, args))
        return spec.execute(args)

    def run_script(self, script_fn: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
        outputs: list[Any] = []
        pending_store = self.session.replay_store()
        store_delta: dict[str, Any] = {}

        def emit_text(value: Any) -> None:
            outputs.append(value)

        def load(key: str) -> Any:
            return copy.deepcopy(pending_store.get(key))

        def store(key: str, value: Any) -> None:
            if value is None:
                pending_store.pop(key, None)
            else:
                pending_store[key] = copy.deepcopy(value)
            store_delta[key] = copy.deepcopy(value)

        env = {
            "execute_tool": self.execute_tool,
            "text": emit_text,
            "load": load,
            "store": store,
        }
        try:
            script_fn(env)
        except Exception as exc:
            # Failed script discards store_delta, while external side-effects remain.
            return {"ok": False, "output": outputs, "error": str(exc)}

        if store_delta:
            self.session.append_store_commit(store_delta)
        return {"ok": True, "output": outputs}


# ---------------------------------------------------------------------------
# 3. v1.5.0 StreamingJsonToolParser & v1.6.0 TUI Container Render Cache
# ---------------------------------------------------------------------------

_SIMPLE_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}
_NUMBER_RE = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$")


@dataclass
class _Frame:
    container: dict[str, Any] | list[Any]
    key: str | None = None
    phase: str = "key"  # for dict: key|colon|value|comma; for list: value|comma


class StreamingJsonToolParser:
    """Faithful teaching model of packages/ai/src/utils/streaming-json.ts.

    - append(delta) processes each incoming character once in O(len(delta)).
    - snapshot() copies only the open path and enforces a copy-credit budget
      (CREDIT_PER_APPEND + CREDIT_PER_CHARACTER * len(delta)) vs open path cost.
    - finish() performs a single strict json.loads() on the accumulated raw text.
    """

    CREDIT_PER_APPEND = 256
    CREDIT_PER_CHARACTER = 8

    def __init__(self) -> None:
        self._raw = ""
        self._root: Any = None
        self._stack: list[_Frame] = []
        self._in_string = False
        self._string_is_key = False
        self._string_buf: list[str] = []
        self._escape_buf = ""
        self._scalar_buf = ""
        self._credit = 0
        self._dirty = False
        self._last_snapshot: Any = None
        self.snapshot_copies = 0

    @property
    def raw_text(self) -> str:
        return self._raw

    def _open_path_cost(self) -> int:
        return sum(len(f.container) + 1 for f in self._stack)

    def _attach_value(self, val: Any) -> None:
        if not self._stack:
            self._root = val
        else:
            top = self._stack[-1]
            if isinstance(top.container, dict):
                if top.key is not None:
                    top.container[top.key] = val
                    top.key = None
                    top.phase = "comma"
            else:
                top.container.append(val)
                top.phase = "comma"
        self._dirty = True

    def _flush_scalar(self) -> None:
        token = self._scalar_buf
        self._scalar_buf = ""
        if not token:
            return
        if token == "true":
            self._attach_value(True)
        elif token == "false":
            self._attach_value(False)
        elif token == "null":
            self._attach_value(None)
        elif _NUMBER_RE.match(token):
            self._attach_value(float(token) if ("." in token or "e" in token.lower()) else int(token))

    def append(self, delta: str) -> None:
        self._raw += delta
        self._credit += self.CREDIT_PER_APPEND + self.CREDIT_PER_CHARACTER * len(delta)
        for ch in delta:
            if self._in_string:
                if self._escape_buf:
                    if self._escape_buf == "\\":
                        if ch in _SIMPLE_ESCAPES:
                            self._string_buf.append(_SIMPLE_ESCAPES[ch])
                            self._escape_buf = ""
                            self._dirty = True
                        elif ch == "u":
                            self._escape_buf = "\\u"
                        else:
                            self._string_buf.append("\\" + ch)
                            self._escape_buf = ""
                            self._dirty = True
                    else:
                        self._escape_buf += ch
                        if len(self._escape_buf) == 6:
                            try:
                                self._string_buf.append(chr(int(self._escape_buf[2:], 16)))
                            except ValueError:
                                self._string_buf.append(self._escape_buf)
                            self._escape_buf = ""
                            self._dirty = True
                    continue
                if ch == "\\":
                    self._escape_buf = "\\"
                    continue
                if ch == '"':
                    text = "".join(self._string_buf)
                    self._string_buf = []
                    self._in_string = False
                    if self._string_is_key and self._stack and isinstance(self._stack[-1].container, dict):
                        self._stack[-1].key = text
                        self._stack[-1].phase = "colon"
                    else:
                        self._attach_value(text)
                    continue
                self._string_buf.append(ch)
                self._dirty = True
                continue

            if self._scalar_buf and ch in ' \t\n\r,]}:':
                self._flush_scalar()

            if ch in " \t\n\r":
                continue
            if ch == "{":
                obj: dict[str, Any] = {}
                self._attach_value(obj)
                self._stack.append(_Frame(obj, phase="key"))
            elif ch == "[":
                arr: list[Any] = []
                self._attach_value(arr)
                self._stack.append(_Frame(arr, phase="value"))
            elif ch in "}]":
                if self._stack:
                    self._stack.pop()
                self._dirty = True
            elif ch == ":":
                if self._stack and isinstance(self._stack[-1].container, dict):
                    self._stack[-1].phase = "value"
            elif ch == ",":
                if self._stack:
                    top = self._stack[-1]
                    top.phase = "key" if isinstance(top.container, dict) else "value"
            elif ch == '"':
                self._in_string = True
                self._string_buf = []
                self._escape_buf = ""
                self._string_is_key = bool(
                    self._stack
                    and isinstance(self._stack[-1].container, dict)
                    and self._stack[-1].phase == "key"
                )
            else:
                self._scalar_buf += ch
                self._dirty = True

    def _build_snapshot_value(self) -> Any:
        # Deep-copy only active open containers so snapshots stay immutable.
        if self._root is None:
            return None
        snap = copy.deepcopy(self._root)
        # Overlay unterminated string value or pending literal if present.
        if self._in_string and not self._string_is_key and self._stack:
            partial_str = "".join(self._string_buf)
            target = snap
            for frame in self._stack[:-1]:
                if isinstance(target, dict) and frame.key is not None:
                    target = target[frame.key]
                elif isinstance(target, list) and target:
                    target = target[-1]
            top = self._stack[-1]
            if isinstance(target, dict) and top.key is not None:
                target[top.key] = partial_str
            elif isinstance(target, list):
                target.append(partial_str)
        return snap

    def snapshot(self) -> Any:
        if not self._dirty:
            return self._last_snapshot
        cost = self._open_path_cost() + (1 if self._in_string or self._scalar_buf else 0)
        if cost > self._credit:
            return self._last_snapshot
        self._credit -= cost
        self._dirty = False
        self._last_snapshot = self._build_snapshot_value()
        self.snapshot_copies += 1
        return self._last_snapshot

    def finish(self) -> dict[str, Any]:
        try:
            parsed = json.loads(self._raw)
            if isinstance(parsed, dict):
                return {"arguments": parsed, "arguments_error": None}
            return {"arguments": {}, "arguments_error": "Root JSON value must be an object"}
        except json.JSONDecodeError as exc:
            return {"arguments": {}, "arguments_error": str(exc)}


class TuiComponent:
    """Base TUI component returning read-only tuple[str, ...] lines."""

    def render(self, width: int) -> tuple[str, ...]:
        raise NotImplementedError

    def invalidate(self) -> None:
        pass


class StaticTextView(TuiComponent):
    def __init__(self, text: str) -> None:
        self._text = text
        self._cached_width: int | None = None
        self._cached_lines: tuple[str, ...] | None = None
        self.render_computes = 0

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self.invalidate()

    def invalidate(self) -> None:
        self._cached_width = None
        self._cached_lines = None

    def render(self, width: int) -> tuple[str, ...]:
        if self._cached_lines is not None and self._cached_width == width:
            return self._cached_lines
        self.render_computes += 1
        self._cached_width = width
        lines = tuple(line[:width] for line in self._text.splitlines() or [""])
        self._cached_lines = lines
        return lines


class ToolExecutionView(TuiComponent):
    """Models v1.5.0 ToolExecutionComponent argsPending frame coalescing."""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        self._args: dict[str, Any] = {}
        self._args_pending = True
        self._cached_width: int | None = None
        self._cached_lines: tuple[str, ...] | None = None
        self.format_runs = 0
        self.render_requests = 0

    def update_args(self, partial_args: dict[str, Any]) -> None:
        self._args = partial_args
        self._args_pending = True
        self.render_requests += 1

    def invalidate(self) -> None:
        self._args_pending = True
        self._cached_lines = None

    def render(self, width: int) -> tuple[str, ...]:
        if not self._args_pending and self._cached_lines is not None and self._cached_width == width:
            return self._cached_lines
        self._args_pending = False
        self._cached_width = width
        self.format_runs += 1
        summary = f"[{self.tool_name}] {json.dumps(self._args, ensure_ascii=False, sort_keys=True)}"
        self._cached_lines = (summary[:width],)
        return self._cached_lines


@dataclass(frozen=True)
class _ChildLinesCache:
    width: int
    children: tuple[TuiComponent, ...]
    parts: tuple[tuple[str, ...], ...]
    lines: tuple[str, ...]


class TuiContainer(TuiComponent):
    """Models v1.6.0 Container.render() + renderChildLines() identity reuse."""

    def __init__(self) -> None:
        self.children: list[TuiComponent] = []
        self._cache: _ChildLinesCache | None = None
        self.rebuilds = 0

    def add_child(self, child: TuiComponent) -> None:
        self.children.append(child)

    def invalidate(self) -> None:
        for child in self.children:
            child.invalidate()
        self._cache = None

    def render(self, width: int) -> tuple[str, ...]:
        parts = tuple(child.render(width) for child in self.children)
        prev = self._cache
        if (
            prev is not None
            and prev.width == width
            and len(prev.children) == len(self.children)
            and all(
                c is prev.children[i] and (parts[i] is prev.parts[i] or parts[i] == prev.parts[i])
                for i, c in enumerate(self.children)
            )
        ):
            return prev.lines

        self.rebuilds += 1
        merged = tuple(line for part in parts for line in part)
        self._cache = _ChildLinesCache(
            width=width,
            children=tuple(self.children),
            parts=parts,
            lines=merged,
        )
        return merged


class ZonedAssistantView(TuiContainer):
    """Models v1.6.0 AssistantMessageComponent copying cached super.render() output before decorating."""

    def __init__(self) -> None:
        super().__init__()
        self._zone_source: tuple[str, ...] | None = None
        self._zone_lines: tuple[str, ...] = ()
        self.zone_copies = 0

    def render(self, width: int) -> tuple[str, ...]:
        lines = super().render(width)
        if not lines:
            return lines
        if self._zone_source is not lines:
            self.zone_copies += 1
            copied = list(lines)
            copied[0] = "<OSC133:A>" + copied[0]
            copied[-1] = copied[-1] + "<OSC133:BC>"
            self._zone_source = lines
            self._zone_lines = tuple(copied)
        return self._zone_lines


# ---------------------------------------------------------------------------
# 4. MCP On-Demand Exposure, CJK Search & reachesTools Lazy Connection
# ---------------------------------------------------------------------------

@dataclass
class RegisteredTool:
    name: str
    description: str
    exposure: str  # "direct" | "codemode" | "deferred" | "hidden" | "model-only"
    source: str = "builtin"  # "builtin" | "extension" | "mcp"
    server: str | None = None


def _tokenize_multilingual(text: str) -> list[str]:
    """Tokenize ASCII words and CJK bigrams/unigrams without external libraries."""
    tokens: list[str] = []
    for word in re.findall(r"[A-Za-z0-9_]+", text.lower()):
        tokens.extend(part for part in word.split("_") if part)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) == 1:
            tokens.append(run)
        else:
            for i in range(len(run)):
                tokens.append(run[i])
            for i in range(len(run) - 1):
                tokens.append(run[i : i + 2])
    return tokens


class ToolRegistry:
    """Models Pi's exposure matrix, tool_search, CJK search ranking, and lazy MCP connect."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._deferred_loaded: set[str] = set()
        self.connected_servers: set[str] = set()

    def register(self, tool: RegisteredTool) -> None:
        self._tools[tool.name] = tool

    def declared_for_model(
        self,
        *,
        codemode_mode: str = "on",
        cli_tools: set[str] | None = None,
        no_mcp: bool = False,
    ) -> list[str]:
        result: list[str] = []
        for tool in self._tools.values():
            if tool.source == "mcp" and no_mcp:
                continue
            if tool.exposure == "hidden":
                continue
            if tool.exposure == "direct":
                if tool.source == "builtin" and cli_tools is not None and tool.name not in cli_tools:
                    continue
                if codemode_mode == "only" and tool.name != "codemode":
                    continue
                result.append(tool.name)
            elif tool.exposure == "model-only":
                if cli_tools is None or tool.name in cli_tools:
                    result.append(tool.name)
            elif tool.exposure == "deferred" and tool.name in self._deferred_loaded:
                result.append(tool.name)
        return sorted(result)

    def callable_from_script(self, *, no_mcp: bool = False) -> list[str]:
        result: list[str] = []
        for tool in self._tools.values():
            if tool.source == "mcp" and no_mcp:
                continue
            if tool.exposure in ("direct", "codemode", "deferred"):
                result.append(tool.name)
        return sorted(result)

    def search_tools(self, query: str, *, limit: int = 5, no_mcp: bool = False) -> list[str]:
        q_tokens = set(_tokenize_multilingual(query))
        q_lower = query.lower()
        scored: list[tuple[float, str]] = []
        for tool in self._tools.values():
            if tool.source == "mcp" and no_mcp:
                continue
            if tool.exposure in ("hidden", "model-only"):
                continue
            name_tokens = set(_tokenize_multilingual(tool.name))
            desc_tokens = set(_tokenize_multilingual(tool.description))
            name_hits = len(q_tokens & name_tokens)
            desc_hits = len(q_tokens & desc_tokens)
            if name_hits == 0 and desc_hits == 0:
                continue
            # Mirror v1.5.0 #11331: matching words in name outweighs mentions in description.
            score = name_hits * 10.0 + desc_hits * 1.0
            if tool.name.lower() in q_lower:
                score += 25.0
            scored.append((score, tool.name))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [name for _, name in scored[:limit]]

    def tool_search(self, query: str, *, limit: int = 3) -> list[str]:
        """Load matching deferred tools into model declarations (never loads hidden)."""
        hits = self.search_tools(query, limit=limit)
        loaded: list[str] = []
        for name in hits:
            if self._tools[name].exposure == "deferred":
                self._deferred_loaded.add(name)
                loaded.append(name)
        return loaded

    def reaches_tools(self, script_source: str) -> str | set[str]:
        """Inspect codemode script to determine which MCP servers must finish connecting."""
        if any(marker in script_source for marker in ("searchTools(", "ALL_TOOLS", "describeNamespace(")):
            return "all"
        servers = set(re.findall(r"tools\.mcp__([A-Za-z0-9_-]+)__", script_source))
        return servers

    def ensure_servers_connected(self, reach: str | set[str]) -> set[str]:
        all_mcp_servers = {t.server for t in self._tools.values() if t.source == "mcp" and t.server}
        targets = all_mcp_servers if reach == "all" else (reach & all_mcp_servers)
        self.connected_servers.update(targets)
        return targets
