# Research Notes: MCP 2027 Architecture & Engineering

Snapshot date: 2026-11-27
Primary sources verified:
1. Philipp Schmid, "Why I'm Bullish on MCP for 2027" (`https://www.philschmid.de/mcp-2027`, published 2026-11-26)
2. Official MCP spec repository (`modelcontextprotocol/modelcontextprotocol`, commit `2bbace557bbf2cb5d03d6f9b48905415cd7607b0`, 2026-11-24)

## Key Verified Protocol Invariants (2026-07-28 + Draft + Official Extensions)

### 1. Evolution (`2024-11-05` -> `2026-07-28`)
- `2024-11-05`: Initial spec with stdio and HTTP+SSE transport.
- `2025-03-26`: Added OAuth 2.1 auth framework, Streamable HTTP (replacing HTTP+SSE), JSON-RPC batching, tool annotations, audio content, completions.
- `2025-06-18`: Removed JSON-RPC batching; added structured tool outputs (`outputSchema` / `structuredContent`), OAuth Protected Resource Metadata + RFC 8707 Resource Indicators, Elicitation (form), resource links, `MCP-Protocol-Version` HTTP header, `title` field.
- `2025-11-25`: OIDC Discovery 1.0, icons (`SEP-973`), incremental scope consent (`SEP-835`), tool naming rules (`SEP-986`), URL mode elicitation (`SEP-1036`), CIMD (`SEP-991`), experimental tasks (`SEP-1686`), input validation as tool execution error (`SEP-1303`), JSON Schema 2020-12 default (`SEP-1613`), RFC 9728 alignment (`SEP-985`).
- `2026-07-28`: Complete stateless rewrite:
  - Removed protocol sessions & `Mcp-Session-Id` (`SEP-2567`), replaced by explicit state handles in tool args/outputs.
  - Removed `initialize` / `notifications/initialized`, HTTP GET endpoint, `ping`, `logging/setLevel`, `resources/subscribe`, `Last-Event-ID` SSE resumability (`SEP-2575`).
  - Added `server/discover` RPC, per-request `_meta` (`io.modelcontextprotocol/protocolVersion`, `clientInfo`, `clientCapabilities`, optional `logLevel`), `subscriptions/listen` POST SSE stream.
  - Added Multi Round-Trip Requests (`SEP-2322`) with `resultType: "complete" | "input_required"`, `inputRequests`, `inputResponses`, `requestState`.
  - Standardized HTTP headers (`SEP-2243`): `MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`, `x-mcp-header` -> `Mcp-Param-{Name}`, Base64 sentinel `=?base64?...?=`, `HeaderMismatch` `-32020`.
  - Allocated MCP spec error codes `-32020` to `-32099`: `-32020` (`HeaderMismatch`), `-32021` (`MissingRequiredClientCapability`), `-32022` (`UnsupportedProtocolVersion`). Resource not found changed to `-32602` (`SEP-2164`).
  - Required `ttlMs` and `cacheScope` (`"public" | "private"`) on `CacheableResult` (`SEP-2549`).
  - Moved Tasks out of core to `io.modelcontextprotocol/tasks` (`SEP-2663`).
  - Deprecated Roots, Sampling, Logging (`SEP-2577`), DCR (`PR #2858`), HTTP+SSE & Sampling `includeContext` (`SEP-2596`), with 12-month deprecation policy.
  - Added `iss` validation per RFC 9207 (`SEP-2468`) and OIDC refresh token `offline_access` guidance (`SEP-2207`).
- `draft/` (post-2026-07-28): CIMD bumped from `draft-00` to `draft-03`, restored `_meta` on Elicitation/Sampling/Roots params & results (`#3468`).

### 2. Official Extensions Status (from `docs/extensions/overview.mdx` & `client-matrix.mdx`)
- `io.modelcontextprotocol/oauth-client-credentials` (`ext-auth`, Stable, SEP-1046)
- `io.modelcontextprotocol/enterprise-managed-authorization` (`ext-auth`, Stable, SEP-990, ID-JAG via RFC 8693 + jwt-bearer)
- `io.modelcontextprotocol/ui` (`ext-apps`, Stable, SEP-1865, `ui://`, `text/html;profile=mcp-app`, postMessage JSON-RPC, CSP & sandboxed iframe)
- `io.modelcontextprotocol/tasks` (`ext-tasks`, Beta to Core, SEP-2663, `resultType: "task"`, `tasks/get`, `tasks/update`, `tasks/cancel`, `notifications/tasks`, `Mcp-Name: <taskId>`)
- `io.modelcontextprotocol/skills` (`ext-skills`, Beta, SEP-2640, `skill://`, `skills/list`, `skills/get`, `resources/read`, optional `resources/directory/read`, SHA-256 digests, max 512 files / 16 MiB recommendation)
- `io.modelcontextprotocol/events` (`ext-events`, Beta, SEP-3415, `events/list`, `events/poll`, `events/stream`, `events/subscribe`, `events/unsubscribe`, Standard Webhooks `whsec_` HMAC, cursor & `eventId` dedup, `truncated: true`, `maxAgeMs`)
- Server Cards (`ext-server-card`, Beta, SEP-2127, `<streamable-http-url>/server-card`, `/.well-known/ai-catalog.json`, deliberately excludes primitives)

### 3. Roadmap Priorities (`docs/development/roadmap.mdx`, updated 2026-08-22)
1. Agentic Messaging Primitives (Tasks + Events composition)
2. HTTP-Native Transport Unification (HTTP/2 over stdio, ETag caching)
3. Agent Identity & Enterprise Security (DPoP, Workload Identity Federation SEP-1933, ID-JAG, WIMSE)
4. Improved Primitives (Streamlining `content` vs `structuredContent`, progressive discovery, primitive annotations)
5. Improved SDK DX (Spec-driven generated artifacts, conformance suites)

### 4. Survey Numbers from SEP-2567 (1000-repo random OSS MCP server sample)
- 90.0%: No application-level reference to MCP session ID
- 3.5%: TS SDK boilerplate `Map<sessionId, Transport>`
- 2.8%: Transport setup only (`sessionIdGenerator`, never read)
- 2.5%: Session-keyed application state
- 0.7%: Proxy/gateway sticky routing
- 0.5%: Auth binding
