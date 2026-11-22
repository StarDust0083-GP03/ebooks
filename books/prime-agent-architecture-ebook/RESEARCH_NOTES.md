# Research Notes: Prime Agent (`783acb5d72a9b23f97fa4a793a67d421b3ec402d`)

All facts below were directly verified from `/home/stardust/Projects/ebook-sprint/upstreams/prime-agent`.

## 1. Core Architectural Shift: Single Model Tool + Programmatic Tool Calling (PTC)

- `crates/pa-core/src/prompts/layers/core.md` and `crates/pa-core/src/tools/mod.rs`: The only JSON tool registered for the LLM is `python_repl` (internally `ipython` in `crates/pa-core/src/tools/ipython.rs`, `execution_mode: Some(ExecutionMode::Sequential)`).
- All other capabilities are programmatic tools inside the persistent CPython REPL:
  - Runtime built-ins (`prime-agent-runtime/src/rlm/`): `bash(...)`, `rlm.*` (`spawn`, `collect`, `list_subagents`, `delete_subagent`, `create_session`, `find_models`, `progress_note`, `rename`, `end_turn`, `rlm.inbox.read/count`, `rlm.session.messages/count`, `rlm.factory.*`, `rlm.harness.*`), `mcp.*`.
  - Pre-imported executable Python skills (`skills/`): `edit`, `attach_image`, `attach_video`, `agent_message`, `agent_observe`, `compact`, `goal`, `refine`, `rlm_heartbeat`, `websearch`.
  - Non-executable Markdown/reference skills: `factory`, `mcp`, `prime-intellect`, `skill-creator`.

## 2. Rust Workspace & Process Topology

- 10 crates with strict one-way dependency DAG enforced by review/AGENTS rules and `unsafe_code = "forbid"` in workspace lints.
- `serde_json` enables `preserve_order` (IndexMap) so wire/durable JSON preserves TS-era key insertion order.
- `pa-tui` depends ONLY on `pa-types` (never links `pa-core` or `pa-daemon`).
- Reliability model (`crates/pa-daemon/`): Supervisor process spawns one worker process per active session, restarts with backoff, sessions persist as append-only JSONL under `~/.prime/agent/sessions`. Direct-attach uses supervisor-issued single-use tickets (10 s TTL); worker-to-worker messaging uses `worker`-purpose grants with supervisor fallback.
- Update-prepare transaction (`crates/pa-daemon/src/update_prepare.rs`): FSM `Draining -> Fenced -> Snapshotted -> Prepared -> Stopping`, 90 s prepare deadline, 45 s durable marker self-expiry, 30 s + 30 s worker stop budgets, bottom-up restore on new supervisor boot.

## 3. Agent Turn Loop (`crates/pa-agent/`)

- `agent.rs`: `claim_run` acquires `admission` mutex, snapshots `model_override`, atomically installs `ActiveRun` (`AbortController` + `watch` idle channel), returns `RunClaim` or typed `AgentContinueErrorCode::Busy`. Claim-first eliminates races where queued steering/follow-up messages dropped when another run started (`.changes/agent-queue-claim-first.md`).
- `agent_loop/run.rs`: Inner loop drains `steering_queue` and executes tool batches; `execute_tool_calls` returns `terminate: true` when any cell invokes `rlm.end_turn()`. After inner loop settles, outer loop polls `follow_up_queue`, then `get_continuation_messages` (goal continuation / `AutonomousDriver`).

## 4. CPython REPL Runtime (`prime-agent-runtime/src/rlm/repl.py`, `repl.md`, `pa-core/src/kernel/`)

- Protocol v3 newline-delimited JSON over stdio (`{"event":"ready","protocol":3,"python":...}`).
- Channel isolation:
  - Private `os.dup(1)` for locked atomic protocol frames (`_send`).
  - `_TaggedWriter` wraps `sys.stdout`/`sys.stderr`, tags Python writes with `contextvars.ContextVar` `_current_cell`, chunks at `_STREAM_FRAME_TEXT_CAP = 64 * 1024`.
  - Raw fd 1 and fd 2 redirected into pipes drained by `_Pump` daemon threads, emitting `id: null` frames; cell completion fences raw pipes with `b"\xff<drain:" + uuid + b">\xff"` before `done`.
  - fd 0 rebound to `/dev/null` after reader thread takes stdin.
- Top-level `await` compiled with `PyCF_ALLOW_TOP_LEVEL_AWAIT`; tracebacks registered in `linecache` as `<cell-N>`.
- Out-of-band requests (bypassing serial execution queue): `interrupt`, `host_reply`, `bash_activity`, and dedicated-lane `mcp_status`.
- Interrupt delivery: SIGINT to main thread on Unix (direct `KeyboardInterrupt` if active cell mid-step; task cancel if suspended at `await` or background task mid-step); task cancel on Windows.
- Output caps & timeouts (`crates/pa-core/src/kernel/shared.rs`, `manager/requests.rs`, `tools/ipython.rs`):
  - `DEFAULT_MAX_OUTPUT_CHARS = 65_536` via `HeadTailBuffer`: 3/4 head (49,152) + 1/4 rolling tail (16,384), inserting `\n[... N chars omitted ...]\n`.
  - `MAX_BACKGROUND_OUTPUT_CHARS = 64 * 1024`, `_RESULT_TEXT_CAP = 1_048_576`, `_PAYLOAD_CAP = 16 * 1024 * 1024` (strict `allow_nan=False`).
  - `DEFAULT_PYTHON_CELL_TIMEOUT_MS = 60_000`, model `timeout` clamped to `1..=180` s (`PYTHON_REPL_TIMEOUT_MAX_S`), `KERNEL_SHUTDOWN_TIMEOUT_MS = 5_000`, `KERNEL_BUSY_REUSE_WAIT_MS = 5_000`.
- Snapshot/restore: `dill` (recurse mode), magic header `PRIME-AGENT-KERNEL-SNAPSHOT-V2\n`, `DEFAULT_SNAPSHOT_MAX_BYTES = 256 MiB`, `DEFAULT_SNAPSHOT_MAX_VARIABLE_BYTES = 16 MiB`, `prune_oversized` deletes >16 MiB vars during compaction, atomic `os.replace` + JSON manifest, file handles revive as closed stubs (never reopened).

## 5. Subagents, RLM Ledger & Factory

- `DEFAULT_RLM_MAX_DEPTH = 2` (`crates/pa-core/src/session_engine/rlm_host.rs`).
- `rlm.spawn` (wire type `rlm.run` for compatibility) creates a real supervised worker child; `rlm.create_session` creates a peer depth-0 session (daemon-backed depth 0 only).
- Nuclear family scope: singular parent, siblings (roots are siblings), direct children.
- `rlm.collect(targets, timeout_ms=0)` returns typed snapshots without steering parent; `rlm.progress_note(msg)` max 512 UTF-16 code units, throttled to `RLM_PROGRESS_NOTE_MIN_INTERVAL_MS = 10_000`.
- `rlm_ledger.rs`: append-only JSONL recording spawn/rename/delete admissions; parent-death child cleanup (`supervisor_parent_death.rs`).
- `rlm.factory`: opt-in (`factory.enabled` in `settings.json`, default false; fail-closed error `the factory is disabled; run /factory on to enable it`), supports machine & DAG specs + 3 bundled templates (`builder`, `pr-manager`, `review-sweep`).

## 6. Agent Inbox (`pa-core/src/session_engine/inbox.rs`, `pa-daemon/src/agent_inbox.rs`)

- 6 classes: `bash-done`, `factory-progress`, `agent-message`, `child-exited`, `child-failed`, `heartbeat`.
- Persisted to `session-artifacts/<id>/inbox.jsonl`; torn writes skipped on replay; monotonic IDs; journal write failures degrade visibly with `(inbox persistence failed; messages are memory-only)`.
- Coalesced wake row (`inbox:arrival`) + periodic reminder (`inbox:reminder`, controlled by `agentInbox.reminderIntervalMs`; `0` disables reminder; `agentInbox.enabled: false` kill switch).
- Reserved `inbox_notice` custom kind has no client intake route; spoofed `queueKey` (`inbox:arrival`/`inbox:reminder`) rejected.
- `InboxReadFilter` uses `#[serde(rename_all = "camelCase", default, deny_unknown_fields)]` so typos fail closed instead of draining everything.
- `rlm.inbox.read()` emits `application/vnd.prime-agent.inbox-message+json` display events so the `python_repl` TUI card renders each returned message.
- Heartbeat inbox deduplication: keeps at most one waiting fire per heartbeat job (newer fire replaces older; paused/deleted jobs withdraw waiting fires).

## 7. Prompt Builder, Continual Harness & Compaction

- `system_prompt.rs`: Cache-stable static prefix (`core` -> `usage` -> `opinionated` -> `model-prompts` from `model_prompts.toml`, e.g. `glm-5.3*` -> `bash-over-subprocess.md`), followed by dynamic tail (`packages` -> `project-context` (`AGENTS.md`) -> `skills-inventory` -> `mcp-servers` -> `environment` -> `session-role` -> `appended-prompt`). `cached_prefix_len` pins exact byte boundary.
- Continual Harness (`refinement/mod.rs`, `harness_digest.rs`, `harness.py`): 5 kinds (`prompt`, `memory`, `skill`, `subagent`, `factory`), local (`<session-artifact-dir>/harness/harness_state.json`) + global (`~/.prime/agent/harness/harness_state.json`), symlink-resolved file lock, CJK bigram + ASCII >=3 char ranking, delivered via `[harness-digest]` user message with `stateFingerprint` to preserve system prompt prefix cache. Never rewrites immutable base system prompt.
- Compaction (`compaction.rs`): `DEFAULT_RESERVE_TOKENS = 16_384`, `DEFAULT_KEEP_RECENT_TOKENS = 20_000`, `COMPACT_THRESHOLD_RATIO = 0.95`, `COMBINED_LIMIT_HEADROOM_FLOOR = 4_096`, `retention_floor = keep_recent_tokens + 2 * reserve_tokens + 1`, `combined = context_window - max_output_tokens - max(reserve_tokens, 4096)`. Keeps REPL kernel alive across compaction while pruning individual variables >16 MiB.

## 8. MCP, Autonomous Gates, Search, ACP, TUI & Security

- MCP: Host (`crates/pa-core/src/mcp/`) owns auth gating, OAuth RFC 9728 discovery + PKCE + local callback server, `mcp.config`/`mcp.refresh`/`mcp.begin_login` handlers; Kernel (`prime-agent-runtime/src/rlm/mcp.py`) owns stdio/HTTP client, `_MAX_TOOL_PAGES = 25`, `_SHUTDOWN_TIMEOUT = 2.5` s (< 5 s host kill), `_SAFE_ENV`, `_SECRET_KEY_PATTERN` redaction.
- Autonomous quality gates (`autonomous/gates.rs`): `MAX_GATE_OUTPUT_CHARS = 6_000`, `MAX_CHILD_PROCESS_OUTPUT_BYTES = 1 MiB`, git worktree snapshot disables hooks/fsmonitor/promisor/ext-diff/textconv/submodules, documenting `.gitattributes` clean/process filters as residual risk.
- Session content search (`session_content_search.rs`): streams top-level `.jsonl` line-by-line, case-insensitive ASCII prefilter, matches `user`/`assistant` text only (skips `toolResult` and subagent files).
- ACP (`pa-daemon/src/acp/`): JSON-RPC over stdio; advertises `_meta.steering.supported`; `_session/steering` requires active turn (`requireBusy`), deduplicates by `messageId`; error taxonomy marks only `model_transport` as `retryable: true`.
- TUI (`pa-tui`): paint-latency critical path enforced by red-first hanging-sink test (`the_startup_flush_never_blocks_the_first_frame`); `tool_card/ipython.rs` fixed summary line; `vendor/crossterm` 250 ms kitty poll cap.
- Security boundary: `unsafe_code = "forbid"`; `SourceScope::Project` Python skills blocked from kernel auto-import (`crates/pa-core/src/skills/mod.rs:145`); README warns worker/kernel processes are NOT a security sandbox and execute with user permissions.
