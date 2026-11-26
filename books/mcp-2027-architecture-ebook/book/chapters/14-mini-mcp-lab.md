# 动手验证：零依赖 Python MCP 2027 教学运行时

协议规范里写满了 `MUST` 和 `SHOULD`，光靠通读文档很容易漏掉边界细节。比如：当 `Mcp-Name` 恰好包含中文或者字面量以 `=?base64?` 开头时，编解码会不会出错？当负载均衡把 MRTR 重试打到另一台没有任何内存状态的新副本，而攻击者顺手调包了参数或复用了 JSON-RPC `id` 时，服务端到底靠哪几道校验把它拦下来？当远端服务器静默修改了 `SKILL.md` 的某一个支撑文件，宿主的缓存和持久化审批是怎么同步失效的？

为了把全书最核心的协议不变量变成可执行、可单步调试的实证代码，我们在本书仓库的 `examples/` 目录下用纯 Python 3.10+ 标准库（零外部依赖）编写了一份约 570 行的教学运行时 `examples/mini_mcp_2027.py` 和 21 个单元测试 `examples/test_mini_mcp_2027.py`。

## 模块设计与协议映射

整份教学模型把前十三章拆解的八块关键机制收拢成了八个独立组件，每个组件都直接对应具体的规范章节与 SEP 提案：

| 运行时组件（`examples/mini_mcp_2027.py`） | 核验的协议规范与 SEP | 核心验证的不变量 |
| --- | --- | --- |
| `encode_mcp_header_value` / `decode_mcp_header_value` | `SEP-2243` | 非 ASCII、首尾空格、换行符及哨兵字面量走 `=?base64?...?=` 编码并无损往返 |
| `StatelessMcpServer` | `SEP-2575` / `SEP-2243` / `SEP-1303` | 头与 Body 逐项核对（不符返 `-32020`）、版本协商（`-32022`）、未知方法 `404/-32601`、`x-mcp-header` 校验、入参缺失按 `isError: true` 返回工具执行错误 |
| `McpListCache` | `SEP-2549` | `ttlMs` 懒惰过期判定、`public` 跨身份复用与 `private` 按授权上下文严格隔离、`list_changed` 立即失效 |
| `OAuthResourceGuard` | OAuth 2.1 / RFC 8707 / RFC 6750 | 令牌受众 `aud` 必须精确匹配规范资源 URI（不符返 `401`）；作用域不足返回 `403` 与 `insufficient_scope` 提权挑战头 |
| `simulate_plain_tool_calling` vs `simulate_code_mode` | 第 4 章 Code Mode 对比 | 量化对比 20 条 issue 与 Sentry 统计跨服务 Join 时的推理轮次与进入模型上下文的字节开销 |
| `MrtrReplica` | `SEP-2322` | 副本 A 签发 HMAC-SHA256 `requestState`，零共享内存的副本 B 验签续跑；拦截篡改、跨主体重放、超时、参数调包和复用 JSON-RPC `id` |
| `SkillsServer` + `SkillHostLoader` | `SEP-2640` | `skills/list` 与未列举技能 `skills/get`、未知 URI 返 `-32602`、加载前校验字节数与 SHA-256、远端文件变更自动撤销旧审批、禁止越清单读取 |
| `TasksManager` | `SEP-2663` | 缺失扩展能力返 `-32021`、128 位高熵 `taskId`、`input_required` 中途追问与 `tasks/update` 续跑、终态不可变、属主隔离与 `ttlMs` 超时失效 |
| `EventBroker` + `EventHarnessConsumer` | `SEP-3415` | `whsec_` 24 至 64 字节密钥校验、HTTPS 与防 SSRF 私网拦截、Standard Webhooks HMAC 签名与时间窗校验、单订阅内 `eventId` 去重、跨订阅同 `eventId` 不误杀、`truncated: true` 缺口感知与同轨迹/隔离轨迹续跑 |

## 几个最值得细看的实现片段

先看 `StatelessMcpServer` 中对 `x-mcp-header` 和 `SEP-1303` 参数校验的处理逻辑。当请求体进入 `tools/call` 时，服务端先核验所有标注了 `x-mcp-header` 的自定义请求头是否与请求体里的实际参数一致（防代理网关与后端解析分裂），不符直接回 HTTP `400` 和 `-32020`；只有通过传输层校验后，才做业务参数完整性校验，一旦缺少必填字段，不再抛 `-32602` 协议错误，而是返回 HTTP `200` 加上 `isError: true` 的工具执行错误，把缺失字段名清晰地交还给模型自纠错：

```python
# Validate x-mcp-header mirrored parameters (SEP-2243)
for arg_name, hdr_suffix in spec.x_mcp_headers.items():
    hdr_key = f"mcp-param-{hdr_suffix.lower()}"
    if arg_name in arguments and arguments[arg_name] is not None:
        if hdr_key not in norm_headers:
            err = McpProtocolError(400, ERR_HEADER_MISMATCH, f"Missing header Mcp-Param-{hdr_suffix}")
            return err.http_status, err.to_jsonrpc(req_id)
        decoded = decode_mcp_header_value(norm_headers[hdr_key])
        if decoded != expected_str:
            err = McpProtocolError(400, ERR_HEADER_MISMATCH, f"Mcp-Param-{hdr_suffix} mismatch")
            return err.http_status, err.to_jsonrpc(req_id)

# Validate required input arguments per SEP-1303 (tool execution error isError=True)
missing_fields = [f for f in required_fields if f not in arguments]
if missing_fields:
    return 200, {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "resultType": "complete",
            "isError": True,
            "content": [{"type": "text", "text": f"Input validation error: missing required fields {missing_fields}"}],
        },
    }
```

再看 `MrtrReplica` 如何只靠一把集群共享 HMAC 密钥，就在完全无状态的两个副本（`replica-a` 与 `replica-b`）之间安全完成多往返追问：

```python
state_payload = {
    "principal": principal,
    "method": "tools/call",
    "paramsDigest": params_digest,
    "initialReqId": req_id,
    "exp": now_s + 300.0,
}
```

当重试打到 `replica-b` 时，代码依次校验签名、`principal`、`now_s <= state["exp"]`、`paramsDigest`（防止把 `v2.4.0` 偷换成 `v9.9.9-evil`）以及 `req_id != state["initialReqId"]`（遵循重试必须更换 JSON-RPC `id` 的要求）。五道门禁任意一道没过立刻抛出异常。

再看 `EventHarnessConsumer` 对 `eventId` 去重与双轨迹续跑的处理。规范明确规定去重作用域是单条订阅（`sub_id`），而不是全局，因此已见集合按 `sub_id` 分桶；验证完 Standard Webhooks HMAC 签名与时间窗后，若同一个 `eventId` 落在同一条订阅上则直接返回 `None` 丢弃，落在另一条不同订阅上则照常投递，并按 `same_thread` 或 `isolated` 路由到对应轨迹：

```python
seen_for_sub = self._seen.setdefault(sub_id, set())
if event_id in seen_for_sub:
    return None  # Duplicate within the same subscription
seen_for_sub.add(event_id)
if occ.get("cursor"):
    self.cursors[sub_id] = occ["cursor"]
traj_id = f"thread:{sub_id}" if continuation_mode == "same_thread" else f"isolated:{sub_id}:{event_id}"
```

## 本地复现与实测输出

在本书仓库根目录运行下面这条命令，无需安装任何第三方包，几毫秒内就能跑完全部 21 个单元测试：

```bash
python3 -m unittest discover -s examples -v
```

在当前快照环境实测结果如下（全部 21 项通过，耗时约 0.001 秒）：

```text
test_09_code_mode_slashes_turns_and_context_bytes ... ok
test_07_list_cache_ttl_public_vs_private_and_invalidation ... ok
test_08_oauth21_audience_binding_and_step_up_scope_challenge ... ok
test_19_webhook_ssrf_and_secret_validation ... ok
test_20_webhook_hmac_verification_eventid_dedup_and_continuation_modes ... ok
test_21_poll_cursor_and_truncated_gap_detection ... ok
test_16_task_requires_capability_declaration ... ok
test_17_task_lifecycle_midflight_input_and_terminal_immutability ... ok
test_18_task_owner_isolation_and_ttl_expiry ... ok
test_10_mrtr_roundtrip_resumes_on_different_replica ... ok
test_11_mrtr_rejects_tampered_state_wrong_principal_expired_and_same_id ... ok
test_12_mrtr_rejects_client_without_elicitation_capability ... ok
test_13_skills_list_and_unlisted_skills_get ... ok
test_14_skills_digest_verification_and_approval_revocation_on_update ... ok
test_15_skills_blocks_read_outside_retained_manifest ... ok
test_01_header_base64_sentinel_roundtrip ... ok
test_02_discover_and_unsupported_version_negotiation ... ok
test_03_header_body_mismatch_rejected_with_32020 ... ok
test_04_x_mcp_header_validation_passes_and_catches_spoofing ... ok
test_05_unknown_method_returns_404_and_32601 ... ok
test_06_sep1303_input_validation_error_is_tool_execution_error ... ok

----------------------------------------------------------------------
Ran 21 tests in 0.001s

OK
```

读者在搭建自己的 MCP Server、企业网关或 Agent Harness 时，可以直接把 `examples/test_mini_mcp_2027.py` 里的 21 个测试场景改写成自家集成测试清单：只要这 21 条协议不变量全绿，至少在无状态路由、缓存隔离、OAuth 边界、MRTR 防重放、Skills 验签、Tasks 状态机和 Events 安全消费上，就已经稳稳站在了 `2026-07-28` 规范与 2027 扩展栈的正确起跑线上。
