# Upstream Snapshot Record

- **Repository**: `https://github.com/PrimeIntellect-ai/prime-agent`
- **Pinned Commit**: `783acb5d72a9b23f97fa4a793a67d421b3ec402d`
- **Commit Subject**: `feat(inbox): show what rlm.inbox.read returned in the python_repl card (#4123)`
- **Workspace Version (`Cargo.toml`)**: `0.10.5` (Rust edition 2021, MIT License)
- **Latest Release Tag**: `v0.10.5` (`2026-11-18T19:15:02Z`)
- **Snapshot Date**: `2026-11-21`

## Verified Inventory

### Rust Workspace Crates (`crates/`)

| Crate | Source `.rs` Files (`src/`) | Lines of Code | Role | Direct Workspace Dependencies |
| --- | ---: | ---: | --- | --- |
| `pa-types` | 44 | 13,253 | Shared wire & domain vocabulary | none |
| `pa-telemetry` | 17 | 4,733 | Events, sinks, atomic rename primitive | none |
| `pa-agent` | 14 | 5,646 | Provider-independent agent turn loop | none |
| `pa-ai` | 83 | 41,483 | LLM providers, model registry, streaming | `pa-types` |
| `pa-models` | 11 | 3,753 | Live model catalog & transport | `pa-ai`, `pa-types` |
| `pa-core` | 275 | 135,470 | Session engine, tools, skills, kernel manager, compaction, refinement, MCP host, autonomous/goals/cron | `pa-types`, `pa-ai`, `pa-models`, `pa-agent`, `pa-telemetry` |
| `pa-daemon` | 256 | 148,695 | Supervisor, per-session workers, wire protocol, inbox, RLM ledger, ACP stdio | `pa-types`, `pa-core`, `pa-agent`, `pa-ai`, `pa-telemetry`, `pa-models` |
| `pa-tui` | 262 | 131,748 | Terminal UI, daemon wire client, tool cards | `pa-types` |
| `pa-cli` | 81 | 35,768 | `prime-agent` binary composition root | `pa-types`, `pa-core`, `pa-ai`, `pa-agent`, `pa-daemon`, `pa-tui`, `pa-telemetry` |
| `pa-perf` | 10 | 4,242 | Performance corpora & PTY bench harness (unshipped) | `pa-types`, `pa-core`, `pa-daemon` |
| **Total** | **1,053** | **524,791** | | |

### Python RLM Runtime (`prime-agent-runtime/`)

- Total Python files (src + test): 22 files, 33,135 lines.
- Runtime modules (`prime-agent-runtime/src/rlm/`):
  - `repl.py` (1,789 lines) + `repl.md` (74 lines): Protocol v3 newline-delimited JSON CPython REPL runtime
  - `__init__.py` (1,076 lines): `rlm` namespace (`spawn`, `collect`, `list_subagents`, `delete_subagent`, `create_session`, `find_models`, `progress_note`, `rename`, `end_turn`, `rlm.inbox`, `rlm.session`, `rlm.factory`, `rlm.harness`)
  - `bash.py` (1,843 lines): Async/non-blocking process runner, `BashHandle`, `persistent=True` shell rehydration
  - `harness.py` (1,381 lines): Local/global `HarnessState`, file locking, CJK/ASCII query tokenization, refinement records
  - `factory.py` (5,759 lines): Opt-in state-machine / DAG factory runner + 3 bundled machines (`builder`, `pr-manager`, `review-sweep`)
  - `mcp.py` (1,075 lines) + `mcp_base.py` (337 lines): Kernel-side MCP stdio/HTTP client, bounded pagination, credential hygiene
  - `_winjob.py` (378 lines): Windows Job Object process-group cleanup
  - `skill.py` (37 lines): Callable skill module wrapper

### Bundled Skills (`skills/`, 14 directories)

`agent-message`, `agent-observe`, `attach-image`, `attach-video`, `compact`, `edit`, `factory`, `goal`, `mcp`, `prime-intellect`, `refine`, `rlm-heartbeat`, `skill-creator`, `websearch`.

### Vendored Patch

- `vendor/crossterm`: Patches `crossterm 0.28.1` kitty keyboard protocol query poll timeout from 2 s down to 250 ms (`Cargo.toml` `[patch.crates-io]`).
