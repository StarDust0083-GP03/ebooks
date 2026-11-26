"""Zero-dependency Python 3.10+ teaching runtime for MCP 2026-07-28 + 2027 extensions.

Models core protocol invariants verified against modelcontextprotocol/modelcontextprotocol
(commit 2bbace557bbf2cb5d03d6f9b48905415cd7607b0, 2026-11-24):
  1. Stateless HTTP header mirroring, Base64 sentinel encoding, and error codes (SEP-2567/2575/2243)
  2. CacheableResult with ttlMs, cacheScope (public/private), and list_changed invalidation (SEP-2549)
  3. OAuth 2.1 Resource Indicator (RFC 8707) audience check and runtime scope step-up (401/403)
  4. Typed tool schemas, SEP-1303 input validation errors (isError=True), and Code Mode join comparison
  5. Stateless MRTR (InputRequiredResult + HMAC-bound requestState) across independent server replicas (SEP-2322)
  6. Skills over MCP (skills/list, skills/get, resources/read, SHA-256 digest & approval invalidation, SEP-2640)
  7. MCP Tasks state machine (resultType="task", tasks/get, tasks/update, tasks/cancel, TTL & owner check, SEP-2663)
  8. MCP Events (events/subscribe, Standard Webhooks HMAC, SSRF guard, cursor & eventId dedup, SEP-3415)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable

PROTOCOL_VERSION = "2026-07-28"

# MCP specification allocated JSON-RPC error codes (2026-07-28)
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_HEADER_MISMATCH = -32020
ERR_MISSING_CAPABILITY = -32021
ERR_UNSUPPORTED_VERSION = -32022

BASE64_PREFIX = "=?base64?"
BASE64_SUFFIX = "?="


class McpProtocolError(Exception):
    """JSON-RPC protocol error paired with an HTTP status code."""

    def __init__(self, http_status: int, code: int, message: str, data: dict[str, Any] | None = None):
        super().__init__(message)
        self.http_status = http_status
        self.code = code
        self.message = message
        self.data = data or {}

    def to_jsonrpc(self, req_id: Any = None) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data:
            err["data"] = self.data
        return {"jsonrpc": "2.0", "id": req_id, "error": err}


class AuthError(Exception):
    """HTTP 401/403 transport-level OAuth 2.1 error with WWW-Authenticate challenge."""

    def __init__(self, http_status: int, www_authenticate: str, detail: str):
        super().__init__(detail)
        self.http_status = http_status
        self.www_authenticate = www_authenticate
        self.detail = detail


# ---------------------------------------------------------------------------
# 1. SEP-2243 Header Encoding / Decoding & OAuth 2.1 Token Verification
# ---------------------------------------------------------------------------

def encode_mcp_header_value(raw: str) -> str:
    """Encode a header value per SEP-2243, using =?base64?...?= when unsafe or ambiguous."""
    needs_b64 = (
        not raw
        or raw != raw.strip(" \t")
        or (raw.startswith(BASE64_PREFIX) and raw.endswith(BASE64_SUFFIX))
        or any(ord(ch) < 0x20 or ord(ch) > 0x7E for ch in raw)
    )
    if not needs_b64:
        return raw
    encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")
    return f"{BASE64_PREFIX}{encoded}{BASE64_SUFFIX}"


def decode_mcp_header_value(header_val: str) -> str:
    """Decode a plain or Base64-sentinel SEP-2243 header value."""
    if header_val.startswith(BASE64_PREFIX) and header_val.endswith(BASE64_SUFFIX):
        payload = header_val[len(BASE64_PREFIX):-len(BASE64_SUFFIX)]
        try:
            return base64.b64decode(payload, validate=True).decode("utf-8")
        except Exception as exc:
            raise McpProtocolError(400, ERR_HEADER_MISMATCH, "Malformed base64 header") from exc
    if any(ord(ch) < 0x20 or ord(ch) > 0x7E for ch in header_val):
        raise McpProtocolError(400, ERR_HEADER_MISMATCH, "Invalid characters in header value")
    return header_val


@dataclass(frozen=True)
class AccessToken:
    """Simulated OAuth 2.1 access token bound to a resource audience (RFC 8707)."""

    token_id: str
    subject: str
    audience: str
    scopes: frozenset[str]
    expires_at: float


class OAuthResourceGuard:
    """Resource-server OAuth 2.1 validator (RFC 8707 audience + RFC 6750 scope step-up)."""

    def __init__(self, canonical_resource_uri: str, metadata_url: str):
        self.canonical_resource_uri = canonical_resource_uri
        self.metadata_url = metadata_url
        self._tokens: dict[str, AccessToken] = {}

    def issue_token(self, subject: str, audience: str, scopes: set[str], expires_at: float) -> str:
        token_str = f"tok_{secrets.token_hex(12)}"
        self._tokens[token_str] = AccessToken(
            token_id=token_str,
            subject=subject,
            audience=audience,
            scopes=frozenset(scopes),
            expires_at=expires_at,
        )
        return token_str

    def authenticate(self, auth_header: str | None, now: float, required_scopes: set[str] | None = None) -> AccessToken:
        if not auth_header or not auth_header.startswith("Bearer "):
            raise AuthError(
                401,
                f'Bearer resource_metadata="{self.metadata_url}"',
                "Missing Bearer token",
            )
        raw = auth_header[len("Bearer "):].strip()
        tok = self._tokens.get(raw)
        if tok is None or now >= tok.expires_at:
            raise AuthError(
                401,
                f'Bearer resource_metadata="{self.metadata_url}", error="invalid_token"',
                "Invalid or expired access token",
            )
        if tok.audience != self.canonical_resource_uri:
            raise AuthError(
                401,
                f'Bearer resource_metadata="{self.metadata_url}", error="invalid_token"',
                f"Token audience mismatch: expected {self.canonical_resource_uri}",
            )
        if required_scopes:
            missing = required_scopes - set(tok.scopes)
            if missing:
                scope_str = " ".join(sorted(required_scopes))
                raise AuthError(
                    403,
                    (
                        f'Bearer error="insufficient_scope", scope="{scope_str}", '
                        f'resource_metadata="{self.metadata_url}"'
                    ),
                    f"Missing scopes: {sorted(missing)}",
                )
        return tok


# ---------------------------------------------------------------------------
# 2. SEP-2549 Client-Side List Cache (ttlMs + cacheScope + list_changed)
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    value: dict[str, Any]
    received_at_ms: int
    ttl_ms: int
    cache_scope: str


class McpListCache:
    """Client cache honoring ttlMs, cacheScope ('public'|'private'), and invalidation."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str, str], _CacheEntry] = {}

    def _key(self, method: str, params_fingerprint: str, cache_scope: str, auth_context: str) -> tuple[str, str, str]:
        scope_owner = "*" if cache_scope == "public" else auth_context
        return (method, params_fingerprint, scope_owner)

    def put(self, method: str, params_fingerprint: str, result: dict[str, Any], now_ms: int, auth_context: str) -> None:
        if result.get("resultType") != "complete":
            return
        ttl_ms = int(result.get("ttlMs", 0))
        if ttl_ms <= 0:
            return
        cache_scope = result.get("cacheScope", "private")
        key = self._key(method, params_fingerprint, cache_scope, auth_context)
        self._store[key] = _CacheEntry(result, now_ms, ttl_ms, cache_scope)

    def get(self, method: str, params_fingerprint: str, now_ms: int, auth_context: str) -> dict[str, Any] | None:
        for scope_owner in (auth_context, "*"):
            entry = self._store.get((method, params_fingerprint, scope_owner))
            if entry is None:
                continue
            if now_ms < entry.received_at_ms + entry.ttl_ms:
                return entry.value
        return None

    def invalidate_method(self, method: str) -> int:
        keys = [k for k in self._store if k[0] == method]
        for k in keys:
            del self._store[k]
        return len(keys)


# ---------------------------------------------------------------------------
# 3. Typed Tools, SEP-1303 Validation, and Stateless HTTP Router
# ---------------------------------------------------------------------------

@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    handler: Callable[[dict[str, Any], AccessToken | None], dict[str, Any]]
    required_scopes: set[str] = field(default_factory=set)
    x_mcp_headers: dict[str, str] = field(default_factory=dict)  # arg_name -> HeaderSuffix


class StatelessMcpServer:
    """Stateless 2026-07-28 MCP Server supporting header validation, tools, MRTR, skills, tasks."""

    def __init__(
        self,
        name: str,
        supported_versions: list[str] | None = None,
        auth_guard: OAuthResourceGuard | None = None,
        hmac_secret: bytes | None = None,
    ) -> None:
        self.name = name
        self.supported_versions = supported_versions or [PROTOCOL_VERSION]
        self.auth_guard = auth_guard
        self.hmac_secret = hmac_secret or b"shared-cluster-mrtr-key-2027"
        self.tools: dict[str, ToolSpec] = {}
        self.list_ttl_ms = 300_000
        self.list_cache_scope = "public"

    def register_tool(self, spec: ToolSpec) -> None:
        self.tools[spec.name] = spec

    def handle_http_post(
        self,
        headers: dict[str, str],
        body: dict[str, Any],
        now_s: float = 1000.0,
    ) -> tuple[int, dict[str, Any]]:
        norm_headers = {k.lower(): v for k, v in headers.items()}
        req_id = body.get("id")
        method = body.get("method", "")
        params = body.get("params") or {}
        meta = params.get("_meta") or {}

        # 1. Validate protocol version header vs _meta (SEP-2575 + SEP-2243)
        hdr_ver = norm_headers.get("mcp-protocol-version")
        meta_ver = meta.get("io.modelcontextprotocol/protocolVersion")
        if not hdr_ver or not meta_ver:
            err = McpProtocolError(400, ERR_HEADER_MISMATCH, "Missing protocol version header or _meta")
            return err.http_status, err.to_jsonrpc(req_id)
        if hdr_ver != meta_ver:
            err = McpProtocolError(400, ERR_HEADER_MISMATCH, "MCP-Protocol-Version header disagrees with _meta")
            return err.http_status, err.to_jsonrpc(req_id)
        if hdr_ver not in self.supported_versions:
            err = McpProtocolError(
                400,
                ERR_UNSUPPORTED_VERSION,
                f"Unsupported protocol version: {hdr_ver}",
                {"supported": self.supported_versions, "requested": hdr_ver},
            )
            return err.http_status, err.to_jsonrpc(req_id)

        # 2. Validate Mcp-Method header (SEP-2243)
        if norm_headers.get("mcp-method") != method:
            err = McpProtocolError(400, ERR_HEADER_MISMATCH, "Mcp-Method header does not match body method")
            return err.http_status, err.to_jsonrpc(req_id)

        # 3. Validate Mcp-Name header for named operations
        expected_name: str | None = None
        if method in ("tools/call", "prompts/get"):
            expected_name = params.get("name")
        elif method == "resources/read":
            expected_name = params.get("uri")
        elif method in ("tasks/get", "tasks/update", "tasks/cancel"):
            expected_name = params.get("taskId")

        if expected_name is not None:
            raw_name_hdr = norm_headers.get("mcp-name")
            if raw_name_hdr is None:
                err = McpProtocolError(400, ERR_HEADER_MISMATCH, "Missing required Mcp-Name header")
                return err.http_status, err.to_jsonrpc(req_id)
            try:
                decoded_name = decode_mcp_header_value(raw_name_hdr)
            except McpProtocolError as err:
                return err.http_status, err.to_jsonrpc(req_id)
            if decoded_name != expected_name:
                err = McpProtocolError(400, ERR_HEADER_MISMATCH, "Mcp-Name header does not match body")
                return err.http_status, err.to_jsonrpc(req_id)

        # 4. Dispatch methods
        if method == "server/discover":
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "resultType": "complete",
                    "supportedVersions": list(self.supported_versions),
                    "capabilities": {"tools": {"listChanged": True}},
                    "_meta": {"io.modelcontextprotocol/serverInfo": {"name": self.name, "version": "1.0.0"}},
                    "ttlMs": self.list_ttl_ms,
                    "cacheScope": self.list_cache_scope,
                },
            }

        # OAuth guard for protected calls
        token: AccessToken | None = None
        if self.auth_guard is not None:
            req_scopes: set[str] | None = None
            if method == "tools/call":
                spec = self.tools.get(params.get("name", ""))
                if spec:
                    req_scopes = spec.required_scopes
            try:
                token = self.auth_guard.authenticate(norm_headers.get("authorization"), now_s, req_scopes)
            except AuthError as err:
                return err.http_status, {"error": err.detail, "www_authenticate": err.www_authenticate}

        if method == "tools/list":
            # Deterministic ordering (2026-07-28 SHOULD)
            ordered = [self.tools[k] for k in sorted(self.tools)]
            tool_defs = []
            for spec in ordered:
                if token and spec.required_scopes and not spec.required_scopes.issubset(token.scopes):
                    continue
                entry: dict[str, Any] = {
                    "name": spec.name,
                    "description": spec.description,
                    "inputSchema": spec.input_schema,
                }
                if spec.output_schema is not None:
                    entry["outputSchema"] = spec.output_schema
                tool_defs.append(entry)
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "resultType": "complete",
                    "tools": tool_defs,
                    "ttlMs": self.list_ttl_ms,
                    "cacheScope": "private" if self.auth_guard else self.list_cache_scope,
                },
            }

        if method == "tools/call":
            tool_name = params.get("name", "")
            spec = self.tools.get(tool_name)
            if spec is None:
                err = McpProtocolError(200, ERR_INVALID_PARAMS, f"Unknown tool: {tool_name}")
                return 200, err.to_jsonrpc(req_id)

            arguments = params.get("arguments") or {}
            # Validate x-mcp-header mirrored parameters (SEP-2243)
            for arg_name, hdr_suffix in spec.x_mcp_headers.items():
                hdr_key = f"mcp-param-{hdr_suffix.lower()}"
                if arg_name in arguments and arguments[arg_name] is not None:
                    if hdr_key not in norm_headers:
                        err = McpProtocolError(400, ERR_HEADER_MISMATCH, f"Missing header Mcp-Param-{hdr_suffix}")
                        return err.http_status, err.to_jsonrpc(req_id)
                    try:
                        decoded = decode_mcp_header_value(norm_headers[hdr_key])
                    except McpProtocolError as err:
                        return err.http_status, err.to_jsonrpc(req_id)
                    expected_str = (
                        "true" if arguments[arg_name] is True
                        else "false" if arguments[arg_name] is False
                        else str(arguments[arg_name])
                    )
                    if decoded != expected_str:
                        err = McpProtocolError(400, ERR_HEADER_MISMATCH, f"Mcp-Param-{hdr_suffix} mismatch")
                        return err.http_status, err.to_jsonrpc(req_id)
                elif hdr_key in norm_headers:
                    err = McpProtocolError(400, ERR_HEADER_MISMATCH, f"Unexpected header Mcp-Param-{hdr_suffix}")
                    return err.http_status, err.to_jsonrpc(req_id)

            # Validate required input arguments per SEP-1303 (return tool execution error isError=True)
            required_fields = spec.input_schema.get("required", [])
            missing_fields = [f for f in required_fields if f not in arguments]
            if missing_fields:
                return 200, {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "resultType": "complete",
                        "isError": True,
                        "content": [
                            {
                                "type": "text",
                                "text": f"Input validation error: missing required fields {missing_fields}",
                            }
                        ],
                    },
                }

            result = spec.handler(params, token)
            return 200, {"jsonrpc": "2.0", "id": req_id, "result": result}

        err = McpProtocolError(404, ERR_METHOD_NOT_FOUND, f"Method not found: {method}")
        return err.http_status, err.to_jsonrpc(req_id)


# ---------------------------------------------------------------------------
# 4. Code Mode vs Plain Multi-Turn Tool Calling Simulator
# ---------------------------------------------------------------------------

@dataclass
class ExecutionMetrics:
    model_turns: int
    context_bytes_ingested: int
    joined_rows: list[dict[str, Any]]


def simulate_plain_tool_calling(
    linear_issues: list[dict[str, Any]],
    sentry_stats: dict[str, dict[str, Any]],
) -> ExecutionMetrics:
    """Plain tool calling dumps full payloads into model context on every turn."""
    context_bytes = len(json.dumps(linear_issues).encode("utf-8"))
    turns = 1  # Turn 1: linear.listIssues
    p0s = [i for i in linear_issues if i["priority"] == 1]
    joined = []
    for issue in p0s:
        turns += 1  # One turn per sentry.getIssueStats call
        stats = sentry_stats[issue["identifier"]]
        context_bytes += len(json.dumps(stats).encode("utf-8"))
        joined.append({
            "id": issue["identifier"],
            "title": issue["title"],
            "events24h": stats["count24h"],
        })
    return ExecutionMetrics(model_turns=turns, context_bytes_ingested=context_bytes, joined_rows=joined)


def simulate_code_mode(
    linear_issues: list[dict[str, Any]],
    sentry_stats: dict[str, dict[str, Any]],
) -> ExecutionMetrics:
    """Code Mode filters & joins inside sandbox memory; only final summary enters context."""
    p0s = [i for i in linear_issues if i["priority"] == 1]
    joined = [
        {
            "id": i["identifier"],
            "title": i["title"],
            "events24h": sentry_stats[i["identifier"]]["count24h"],
        }
        for i in p0s
    ]
    summary_bytes = len(json.dumps(joined).encode("utf-8"))
    return ExecutionMetrics(model_turns=1, context_bytes_ingested=summary_bytes, joined_rows=joined)


# ---------------------------------------------------------------------------
# 5. SEP-2322 Stateless MRTR Replica (HMAC-signed opaque requestState)
# ---------------------------------------------------------------------------

class MrtrReplica:
    """Stateless server replica for MRTR; different instances share only HMAC key."""

    def __init__(self, replica_id: str, signing_key: bytes) -> None:
        self.replica_id = replica_id
        self._key = signing_key

    def _sign_state(self, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        sig = hmac.new(self._key, raw, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(raw).decode("ascii") + "." + base64.urlsafe_b64encode(sig).decode("ascii")

    def _verify_state(self, token: str) -> dict[str, Any]:
        if "." not in token:
            raise ValueError("Malformed requestState")
        raw_b64, sig_b64 = token.split(".", 1)
        raw = base64.urlsafe_b64decode(raw_b64.encode("ascii"))
        provided_sig = base64.urlsafe_b64decode(sig_b64.encode("ascii"))
        expected_sig = hmac.new(self._key, raw, hashlib.sha256).digest()
        if not hmac.compare_digest(provided_sig, expected_sig):
            raise ValueError("Tampered requestState signature")
        return json.loads(raw.decode("utf-8"))

    @staticmethod
    def _params_digest(tool_name: str, arguments: dict[str, Any]) -> str:
        blob = json.dumps({"name": tool_name, "arguments": arguments}, sort_keys=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def call_deploy_tool(
        self,
        req_id: int,
        principal: str,
        arguments: dict[str, Any],
        now_s: float,
        request_state: str | None = None,
        input_responses: dict[str, Any] | None = None,
        client_supports_elicitation: bool = True,
    ) -> dict[str, Any]:
        params_digest = self._params_digest("deploy_release", arguments)
        if request_state is None:
            if not client_supports_elicitation:
                raise McpProtocolError(
                    400,
                    ERR_MISSING_CAPABILITY,
                    "Client must support elicitation",
                    {"requiredCapabilities": {"elicitation": {"form": {}}}},
                )
            state_payload = {
                "principal": principal,
                "method": "tools/call",
                "paramsDigest": params_digest,
                "initialReqId": req_id,
                "exp": now_s + 300.0,
            }
            return {
                "resultType": "input_required",
                "inputRequests": {
                    "confirm_prod": {
                        "method": "elicitation/create",
                        "params": {
                            "mode": "form",
                            "message": f"Confirm deploying {arguments.get('version')} to prod?",
                            "requestedSchema": {
                                "type": "object",
                                "properties": {"approved": {"type": "boolean"}},
                                "required": ["approved"],
                            },
                        },
                    }
                },
                "requestState": self._sign_state(state_payload),
            }

        state = self._verify_state(request_state)
        if state["principal"] != principal:
            raise ValueError("requestState principal mismatch")
        if now_s > state["exp"]:
            raise ValueError("requestState expired")
        if state["paramsDigest"] != params_digest:
            raise ValueError("requestState bound to different request parameters")
        if req_id == state["initialReqId"]:
            raise ValueError("Retry MUST use a different JSON-RPC id")

        resp = (input_responses or {}).get("confirm_prod") or {}
        if resp.get("action") != "accept" or not (resp.get("content") or {}).get("approved"):
            return {
                "resultType": "complete",
                "isError": False,
                "content": [{"type": "text", "text": "Deployment cancelled by user"}],
                "structuredContent": {"deployed": False, "handledBy": self.replica_id},
            }
        return {
            "resultType": "complete",
            "isError": False,
            "content": [{"type": "text", "text": f"Deployed {arguments.get('version')} via {self.replica_id}"}],
            "structuredContent": {"deployed": True, "version": arguments.get("version"), "handledBy": self.replica_id},
        }


# ---------------------------------------------------------------------------
# 6. SEP-2640 Skills over MCP (Manifest SHA-256 + Approval Revocation)
# ---------------------------------------------------------------------------

def sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass
class SkillRecord:
    uri: str
    frontmatter: dict[str, str]
    files: dict[str, bytes]  # uri -> raw bytes
    listed: bool = True

    def manifest(self) -> list[dict[str, Any]]:
        return [
            {"uri": u, "digest": sha256_digest(raw), "size": len(raw)}
            for u, raw in sorted(self.files.items())
        ]

    def to_entry(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "frontmatter": dict(self.frontmatter),
            "resources": self.manifest(),
        }


class SkillsServer:
    """Server implementing io.modelcontextprotocol/skills."""

    def __init__(self, server_id: str) -> None:
        self.server_id = server_id
        self._skills: dict[str, SkillRecord] = {}

    def register_skill(self, skill_path: str, name: str, description: str, body: str,
                       extra_files: dict[str, bytes] | None = None, listed: bool = True) -> None:
        if skill_path.split("/")[-1] != name:
            raise ValueError("Final path segment of skill URI MUST match frontmatter name")
        skill_uri = f"skill://{skill_path}/SKILL.md"
        skill_md = f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n".encode("utf-8")
        files = {skill_uri: skill_md}
        for rel, content in (extra_files or {}).items():
            files[f"skill://{skill_path}/{rel}"] = content
        self._skills[skill_uri] = SkillRecord(
            uri=skill_uri,
            frontmatter={"name": name, "description": description},
            files=files,
            listed=listed,
        )

    def update_file(self, skill_uri: str, file_uri: str, new_bytes: bytes) -> None:
        self._skills[skill_uri].files[file_uri] = new_bytes

    def skills_list(self) -> dict[str, Any]:
        entries = [s.to_entry() for s in self._skills.values() if s.listed]
        return {"resultType": "complete", "skills": entries, "ttlMs": 300_000, "cacheScope": "public"}

    def skills_get(self, uri: str) -> dict[str, Any]:
        if uri not in self._skills:
            raise McpProtocolError(200, ERR_INVALID_PARAMS, f"Unknown skill: {uri}")
        return {"resultType": "complete", "skill": self._skills[uri].to_entry(), "ttlMs": 300_000, "cacheScope": "public"}

    def resources_read(self, uri: str) -> bytes:
        for rec in self._skills.values():
            if uri in rec.files:
                return rec.files[uri]
        raise McpProtocolError(200, ERR_INVALID_PARAMS, f"Unknown resource: {uri}")


class SkillHostLoader:
    """Host-side skill verifier binding user approval to exact manifest digests."""

    def __init__(self) -> None:
        self._approved_manifests: dict[tuple[str, str], dict[str, tuple[str, int]]] = {}
        self._disk_cache: dict[tuple[str, str, str], bytes] = {}  # (server_id, uri, digest) -> bytes

    def approve_skill(self, server_id: str, entry: dict[str, Any]) -> None:
        self._approved_manifests[(server_id, entry["uri"])] = {
            item["uri"]: (item["digest"], item["size"]) for item in entry["resources"]
        }

    def is_approved(self, server_id: str, entry: dict[str, Any]) -> bool:
        current = {item["uri"]: (item["digest"], item["size"]) for item in entry["resources"]}
        return self._approved_manifests.get((server_id, entry["uri"])) == current

    def load_file(self, server: SkillsServer, entry: dict[str, Any], file_uri: str) -> bytes:
        manifest = {item["uri"]: (item["digest"], item["size"]) for item in entry["resources"]}
        if file_uri not in manifest:
            raise PermissionError(f"URI {file_uri} is outside retained skill manifest")
        if not self.is_approved(server.server_id, entry):
            raise PermissionError("Skill manifest changed or not approved; re-approval required")
        expected_digest, expected_size = manifest[file_uri]
        cache_key = (server.server_id, file_uri, expected_digest)
        if cache_key in self._disk_cache:
            return self._disk_cache[cache_key]
        raw = server.resources_read(file_uri)
        if len(raw) != expected_size or sha256_digest(raw) != expected_digest:
            raise ValueError("Digest or byte size mismatch; reject content and refresh via skills/get")
        self._disk_cache[cache_key] = raw
        return raw


# ---------------------------------------------------------------------------
# 7. SEP-2663 MCP Tasks State Machine
# ---------------------------------------------------------------------------

TERMINAL_TASK_STATES = frozenset({"completed", "failed", "cancelled"})


@dataclass
class TaskRecord:
    task_id: str
    owner_principal: str
    status: str  # working | input_required | completed | failed | cancelled
    created_at_ms: int
    last_updated_at_ms: int
    ttl_ms: int
    poll_interval_ms: int
    input_requests: dict[str, dict[str, Any]] = field(default_factory=dict)
    answered_keys: set[str] = field(default_factory=set)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class TasksManager:
    """Implements io.modelcontextprotocol/tasks (SEP-2663)."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}

    def create_task(
        self,
        owner_principal: str,
        client_capabilities: dict[str, Any],
        now_ms: int,
        ttl_ms: int = 60_000,
        poll_interval_ms: int = 2_000,
    ) -> dict[str, Any]:
        exts = client_capabilities.get("extensions") or {}
        if "io.modelcontextprotocol/tasks" not in exts:
            raise McpProtocolError(
                400,
                ERR_MISSING_CAPABILITY,
                "Client did not declare io.modelcontextprotocol/tasks",
                {"requiredCapabilities": {"extensions": {"io.modelcontextprotocol/tasks": {}}}},
            )
        task_id = secrets.token_hex(16)  # 128-bit unguessable capability ID
        rec = TaskRecord(
            task_id=task_id,
            owner_principal=owner_principal,
            status="working",
            created_at_ms=now_ms,
            last_updated_at_ms=now_ms,
            ttl_ms=ttl_ms,
            poll_interval_ms=poll_interval_ms,
        )
        self._tasks[task_id] = rec
        return {
            "resultType": "task",
            "taskId": rec.task_id,
            "status": rec.status,
            "ttlMs": rec.ttl_ms,
            "pollIntervalMs": rec.poll_interval_ms,
        }

    def _require_task(self, task_id: str, caller_principal: str, now_ms: int) -> TaskRecord:
        rec = self._tasks.get(task_id)
        if rec is None:
            raise McpProtocolError(200, ERR_INVALID_PARAMS, f"Unknown taskId: {task_id}")
        if rec.owner_principal != caller_principal:
            raise PermissionError("Task belongs to a different principal")
        if now_ms > rec.created_at_ms + rec.ttl_ms and rec.status not in TERMINAL_TASK_STATES:
            rec.status = "failed"
            rec.error = {"code": -32000, "message": "Task expired after ttlMs"}
            rec.last_updated_at_ms = now_ms
        return rec

    def request_midflight_input(self, task_id: str, key: str, req_payload: dict[str, Any], now_ms: int) -> None:
        rec = self._tasks[task_id]
        if rec.status in TERMINAL_TASK_STATES:
            raise ValueError("Cannot mutate terminal task")
        if key in rec.answered_keys or key in rec.input_requests:
            raise ValueError("inputRequests keys MUST be unique over task lifetime")
        rec.status = "input_required"
        rec.input_requests[key] = req_payload
        rec.last_updated_at_ms = now_ms

    def tasks_get(self, task_id: str, caller_principal: str, now_ms: int) -> dict[str, Any]:
        rec = self._require_task(task_id, caller_principal, now_ms)
        out: dict[str, Any] = {
            "resultType": "complete",
            "taskId": rec.task_id,
            "status": rec.status,
            "ttlMs": rec.ttl_ms,
            "pollIntervalMs": rec.poll_interval_ms,
        }
        if rec.status == "input_required":
            out["inputRequests"] = dict(rec.input_requests)
        elif rec.status == "completed":
            out["result"] = rec.result
        elif rec.status == "failed":
            out["error"] = rec.error
        return out

    def tasks_update(self, task_id: str, caller_principal: str, input_responses: dict[str, Any], now_ms: int) -> dict[str, Any]:
        rec = self._require_task(task_id, caller_principal, now_ms)
        for key in list(input_responses):
            if key in rec.input_requests:
                del rec.input_requests[key]
                rec.answered_keys.add(key)
        if rec.status == "input_required" and not rec.input_requests:
            rec.status = "working"
            rec.last_updated_at_ms = now_ms
        return {"resultType": "complete"}

    def complete_task(self, task_id: str, tool_result: dict[str, Any], now_ms: int) -> None:
        rec = self._tasks[task_id]
        if rec.status in TERMINAL_TASK_STATES:
            raise ValueError("Terminal task state is immutable")
        rec.status = "completed"
        rec.result = tool_result
        rec.last_updated_at_ms = now_ms

    def tasks_cancel(self, task_id: str, caller_principal: str, now_ms: int) -> dict[str, Any]:
        rec = self._require_task(task_id, caller_principal, now_ms)
        if rec.status not in TERMINAL_TASK_STATES:
            rec.status = "cancelled"
            rec.last_updated_at_ms = now_ms
        return {"resultType": "complete"}


# ---------------------------------------------------------------------------
# 8. SEP-3415 MCP Events (Webhook HMAC, SSRF Guard, Cursor & eventId Dedup)
# ---------------------------------------------------------------------------

def validate_webhook_secret(secret: str) -> bytes:
    """Validate Standard Webhooks secret: whsec_<base64 of 24..64 random bytes>."""
    if not secret.startswith("whsec_"):
        raise McpProtocolError(200, ERR_INVALID_PARAMS, "Webhook secret MUST start with whsec_")
    raw_b64 = secret[len("whsec_"):]
    try:
        key_bytes = base64.b64decode(raw_b64, validate=True)
    except Exception as exc:
        raise McpProtocolError(200, ERR_INVALID_PARAMS, "Webhook secret is not valid base64") from exc
    if not (24 <= len(key_bytes) <= 64):
        raise McpProtocolError(200, ERR_INVALID_PARAMS, "Webhook secret MUST decode to 24..64 bytes")
    return key_bytes


def validate_webhook_url(url: str, resolved_ip: str) -> None:
    """Enforce HTTPS and block private/loopback/link-local IPs to prevent SSRF."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise McpProtocolError(200, ERR_INVALID_PARAMS, "Webhook callback URL MUST use https")
    ip = ipaddress.ip_address(resolved_ip)
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        raise McpProtocolError(200, ERR_INVALID_PARAMS, f"SSRF guard rejected IP {resolved_ip}")


def sign_standard_webhook(secret: str, msg_id: str, timestamp_s: int, raw_body: bytes) -> str:
    key = validate_webhook_secret(secret)
    signed_content = f"{msg_id}.{timestamp_s}.".encode("utf-8") + raw_body
    sig = base64.b64encode(hmac.new(key, signed_content, hashlib.sha256).digest()).decode("ascii")
    return f"v1,{sig}"


def verify_standard_webhook(
    secret: str,
    msg_id: str,
    timestamp_s: int,
    signature_header: str,
    raw_body: bytes,
    now_s: int,
    max_skew_s: int = 300,
) -> bool:
    if abs(now_s - timestamp_s) > max_skew_s:
        return False
    expected = sign_standard_webhook(secret, msg_id, timestamp_s, raw_body)
    return hmac.compare_digest(expected, signature_header)


@dataclass
class WebhookSubscription:
    sub_id: str
    principal: str
    name: str
    arguments: dict[str, Any]
    callback_url: str
    secret: str
    expires_at_s: float | None


class EventBroker:
    """Server-side MCP Events broker supporting webhook subscribe & durable cursor replay."""

    def __init__(self, retention_limit: int = 50) -> None:
        self.retention_limit = retention_limit
        self._subs: dict[str, WebhookSubscription] = {}
        self._log: list[dict[str, Any]] = []
        self._seq = 0

    @staticmethod
    def derive_sub_id(principal: str, url: str, name: str, arguments: dict[str, Any]) -> str:
        blob = json.dumps([principal, url, name, arguments], sort_keys=True).encode("utf-8")
        return "sub_" + hashlib.sha256(blob).hexdigest()[:16]

    def publish_upstream_event(self, event_id: str, name: str, data: dict[str, Any], timestamp: str) -> dict[str, Any]:
        self._seq += 1
        occ = {
            "eventId": event_id,
            "name": name,
            "timestamp": timestamp,
            "data": data,
            "cursor": f"cur_{self._seq:05d}",
        }
        self._log.append(occ)
        if len(self._log) > self.retention_limit:
            self._log = self._log[-self.retention_limit:]
        return occ

    def events_subscribe(
        self,
        principal: str,
        name: str,
        arguments: dict[str, Any],
        callback_url: str,
        resolved_ip: str,
        secret: str,
        now_s: float,
        ttl_ms: int | None = 3_600_000,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        validate_webhook_url(callback_url, resolved_ip)
        validate_webhook_secret(secret)
        sub_id = self.derive_sub_id(principal, callback_url, name, arguments)
        expires_at = None if ttl_ms is None else now_s + (ttl_ms / 1000.0)
        self._subs[sub_id] = WebhookSubscription(
            sub_id=sub_id,
            principal=principal,
            name=name,
            arguments=dict(arguments),
            callback_url=callback_url,
            secret=secret,
            expires_at_s=expires_at,
        )
        truncated = False
        if cursor is not None and self._log:
            oldest_seq = int(self._log[0]["cursor"].split("_")[1])
            req_seq = int(cursor.split("_")[1])
            if req_seq < oldest_seq - 1:
                truncated = True
        head_cursor = self._log[-1]["cursor"] if self._log else "cur_00000"
        return {
            "resultType": "complete",
            "id": sub_id,
            "refreshBefore": None if expires_at is None else f"{int(expires_at)}",
            "cursor": head_cursor,
            "truncated": truncated,
        }

    def events_poll(self, name: str, cursor: str | None, max_events: int = 50) -> dict[str, Any]:
        if cursor is None:
            head = self._log[-1]["cursor"] if self._log else "cur_00000"
            return {
                "resultType": "complete",
                "events": [],
                "cursor": head,
                "truncated": False,
                "hasMore": False,
                "nextPollMs": 30_000,
            }
        req_seq = int(cursor.split("_")[1])
        oldest_seq = int(self._log[0]["cursor"].split("_")[1]) if self._log else req_seq + 1
        truncated = bool(self._log) and req_seq < oldest_seq - 1
        matching = [
            e for e in self._log
            if e["name"] == name and int(e["cursor"].split("_")[1]) > req_seq
        ]
        batch = matching[:max_events]
        has_more = len(matching) > max_events
        new_cursor = batch[-1]["cursor"] if batch else (self._log[-1]["cursor"] if self._log else cursor)
        return {
            "resultType": "complete",
            "events": batch,
            "cursor": new_cursor,
            "truncated": truncated,
            "hasMore": has_more,
            "nextPollMs": 30_000,
        }


class EventHarnessConsumer:
    """Client/harness side deduplicator & trajectory router (same_thread vs isolated)."""

    def __init__(self) -> None:
        self._seen: dict[str, set[str]] = {}  # sub_id -> set[eventId]
        self.cursors: dict[str, str] = {}
        self.trajectories: dict[str, list[dict[str, Any]]] = {}

    def ingest_webhook(
        self,
        sub_id: str,
        secret: str,
        msg_id: str,
        timestamp_s: int,
        signature: str,
        raw_body: bytes,
        now_s: int,
        continuation_mode: str = "same_thread",
    ) -> str | None:
        if not verify_standard_webhook(secret, msg_id, timestamp_s, signature, raw_body, now_s):
            raise PermissionError("Invalid or expired webhook HMAC signature")
        occ = json.loads(raw_body.decode("utf-8"))
        event_id = occ["eventId"]
        seen_for_sub = self._seen.setdefault(sub_id, set())
        if event_id in seen_for_sub:
            return None  # Duplicate within the same subscription
        seen_for_sub.add(event_id)
        if occ.get("cursor"):
            self.cursors[sub_id] = occ["cursor"]
        if continuation_mode == "same_thread":
            traj_id = f"thread:{sub_id}"
        else:
            traj_id = f"isolated:{sub_id}:{event_id}"
        self.trajectories.setdefault(traj_id, []).append(occ)
        return traj_id
