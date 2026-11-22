# Research Notes: arcships/dim (`88a3df3669405730cbee6bfd113a7f75cf520677`)

## Verified Workspace Inventory (28 Cargo members)

### Core Runtime & Agent Crates (crates/, 23 crates)
1. `dim-agent-types`: `AgentEvent`, `EventKind`, `RESERVED_DOMAINS` (13 domains: `input`, `stop`, `approval`, `permission`, `exec`, `turn`, `prompt`, `context`, `model`, `step`, `branch`, `collaboration`, `session`), `InputMode` (`queue`/`steer`/`append`), `InputBlock::Raw { text, key, retire }`, `ApprovalRequestData` / `ApprovalRespondData` / `ApprovalCancelledData`, `PermissionPolicy`, `v1` frozen wire compatibility.
2. `dim-agent-runtime`: `Timeline`, `Dispatcher`, `BeforeCommit`, `TimelineTrace`, `AgentBuilder`, `PartRegistry`, `MemoryTimelineStore`, `TimelineStore`, `Names`, `StateCell`, `SessionScope`, `BranchScope`.
3. `dim-agent-capabilities`: Core capabilities (`Loop`, `InputQueue`, `Prompt`, `ContextPreparation`, `ContextUsage`, `ToolRegistry`, `ToolBinding`, `ToolExecutor`, `Permission`, `Approval`, `TimelineTree`, `TreeTimeline`, `BranchTool`, `BranchLine`, `WorkspaceTools`, `BackgroundExec`, `WaitBranch`, `FileReads`, `FileAccessFacts`, `SessionBroadcast`, `Skills`, `Hooks`, `PromptSuggestion`, `Recall`). `DEFAULT_MAX_OPEN_LINES = 4`.
4. `dim-agent-aimux`: `AimuxPlugin` (`ModelHost`), `ConfiguredModelsPlugin`, `ConfiguredModelPlugin`, Anthropic cache breakpoints (max 2 inserted by dim: system end + last user/tool-result message, within API's 4 limit), 64 MiB HTTP body guard, same-origin 307/308 redirect only (max 10 hops), RFC-0023 request recording (`recordings.jsonl`), image placement across protocols (`anthropic`/`bedrock` inline vs OpenAI/xAI/Gemini trailing user message).
5. `dim-agent-attachments`: Session attachment handling.
6. `dim-agent-questions`: `ask_user` tool and `session.question.*` methods (Session part, not Capability).
7. `dim-agent-scheduler`: `scheduleJobs`, `wakeups`, `loops` (`/loop` recurring prompt), process-level jobs + Session tool.
8. `dim-event-hub`: Cross-session event bus (`eventHub`, `eventSubscriptions`, `eventhub/ready`).
9. `dim-app-server`: Daemon hosting Agent v4, SQLite native store (WAL `synchronous=FULL` + macOS `fullfsync`, format v29/v53 evolution notes, 4-reader pool, chunked fork/delete, content-addressed message nodes, merged `step/text`/`step/thinking` output rows), RPC v3 over private Unix socket (0700 dir / 0600 socket) or Windows named pipe, Web workspace loopback entry, UI leaves (`ui.open`/`ui.attach`), MCP manager & OAuth, Settings CAS.
10. `dim-client`: Shared RPC framing (16 MiB limit), socket/pipe transport, loopback callback capture (shared by CLI and ACP gateway).
11. `dim-acp`: `AcpGateway` plugin (ACP protocol v1 over stdio, SDK 3.5.0, Lody extensions, `TemporaryMcp` session-scoped MCP servers).
12. `dim-cli`: Thin native CLI library (Ratatui inline/fullscreen TUI derived from grok-build, `exec`, `tui`, `acp`, `mcp`, `modality`, `ocr`, `session`, `daemon`, `migration`).
13. `dimcode`: Single binary bundling CLI + `dimcode app-server` subcommand (linked as `dimcode-server` in `<home>/rust`).
14. `dim-oauth`: Optional DimAccount OAuth plugin (build-time client IDs kept out of public repo).
15. `dim-session-title`: Observes `input/applied` on main, writes title via `sessionMetadata`.
16. `dim-goal`: Pursuit/Goal plugin (`GoalBranch` Capability + Session `goal_update` tool + evaluator plugin line).
17. `dim-report`: Local `/report` feedback & redaction.
18. `dim-codemode`: `rquickjs` 0.14 embedded JS sandbox (`CodeModeConfig`: 256 KiB code, 600s runtime, 4096 calls, 16 parallel, 1 GiB memory, 2 programs, 16 MiB output, 4 MiB single result; exposes only `tools.list/describe/call`; excludes `codemode` recursion; child calls still go through branch `ToolExecutor` and `Approval`; child results never enter model context).
19. `dim-modality`: Media/modality task manager (`image`, `video`, `speech`, `music`, `audio`, `motion`, `asset`, `ocr`).
20. `dim-image-understanding`: Session image-understanding consumer routing via Modality.
21. `dim-ocr`: Local OCR integration (`ocr.recognize`).
22. `dim-modality-aimux`: Aimux adapter for Modality.
23. `dim-migration`: Legacy v2 store offline migration plugin.

### Tooling & Test Crates (5 members)
24. `xtask`: Full runtime assembly graph generator (`graph`, `check`, `measure`, `context-replay`, `restore-replay`, `context-breakdown`, `scenario-eval`, `prompt-eval`, `summary-eval`).
25. `xtask/check` (`xtask-check`): Pure static repository checks without compiling business crates (`boundaries`, `modules`, `plugin_docs`, `types`, `npm`, `comment_links`, `literal_main` banning bare `"main"` in production Rust/TS/Vue).
26. `tests/extension-a`, 27. `tests/extension-b`, 28. `tests/integration`.

## Key Upstream Findings & Real Engineering Tradeoffs
- **Latest commit `88a3df3` (PR #1917)**: `branch/default` routing + `ROOT_BRANCH_ID` (`main`) vs movable `default_branch` + `branch:line` / `branch:roster` / `branch:parent` key split + `literal_main` AST check in `xtask/check` forbidding bare `"main"` string literals in production code!
- **rutis 0.23.0 (ADR 025) & ADR 015**: Sessions and branches are plain rutis plugins (`mark_scope`), not loader instances. Every public service is moving to a cross-language Data Face (`HostDispatch` at `host_key(name)` + shared catalog entry): currently 75/111 are `data`, 36/111 are `internal(reason)`.
- **Observed upstream doc drift**: `runtime/package.json` and `runtime/plugins/workflow/package.json` pin `@arcships/rutis` at `0.23.0` per ADR 025, whereas `runtime/plugins/plugin-authoring/skills/plugin-authoring/SKILL.md` line 40 still shows `"@arcships/rutis": "0.17.1"`. Worth noting honestly as a real alpha-stage doc lag.
- **Permission evolution (ADR 003 -> 009 -> 010 -> 012 -> 014 -> 017 -> 018)**:
  - ADR 003: Approval covers agent decisions only; configured commands (MCP, Hooks, Workflow, Plugins) run when enabled; project entries start disabled.
  - ADR 009 & 010: Permission decides, HostPlatform enforces; sandbox is opt-in (off by default); `exec` has no `sandbox` parameter.
  - ADR 012: Session grants (`session_grants` table) persist per-session file/dir grants, consulted at `Ask` only, never overriding `Deny`, never written by models.
  - ADR 017 -> ADR 018: ADR 017 tried splitting `/permissions` into 3 controls; ADR 018 reverted to the 3 presets (`read-only`, `workspace-write`, `full-access`), moved isolation under Settings, and intentionally dropped `deny_unknown_fields` on `PermissionPolicy` so pre-018 records deserialize without migration (accepting loss of typo detection in policy JSON).
