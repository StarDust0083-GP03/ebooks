# 多往返请求（MRTR）与无状态追问

设想一个线上发布工具 `deploy_release`。用户让 Agent 把 `v2.4.0` 发到生产环境，客户端把 `tools/call` 发给了负载均衡器后面的副本 A。副本 A 检查策略后发现：生产发布必须先让操作人二次确认，或者需要先去第三方平台补录一个一次性审批凭证。

在 `2025-11-25` 及以前的协议里，服务端要想反过来问客户端要信息，必须顺着正在打开的 HTTP SSE 响应流，倒着发一条服务端发起的 JSON-RPC 请求（`elicitation/create`），然后副本 A 的工作线程就得死死挂在那里干等。问题马上就来了：客户端回答后发回的 POST 必须精准路由回同一台副本 A，否则新落地的副本 B 根本不知道刚才那条挂起的协程在哪；如果用户喝了杯咖啡两分钟才点确认，中间网关或 Serverless 早就把长连接掐断了。

`2026-07-28` 通过 **`SEP-2322`（Multi Round-Trip Requests，简称 MRTR）** 彻底终结了服务端主动发请求的历史。新规则非常干脆：服务端绝不在任何流上发送独立的 JSON-RPC Request；当处理请求需要额外输入时，直接结束本次请求并返回一个 `InputRequiredResult`，等客户端收集完输入，再带着原参数和服务端签发的不透明状态令牌重试一次。下面这张图把 MRTR 无状态追问与沙箱外 OAuth 2.1 的完整边界串在了一起。

![MRTR 无状态多往返追问与沙箱外 OAuth 2.1 授权流程](../../wiki/assets/diagrams/mrtr-oauth-flow.svg)

图里第 1 到第 3 步发生在副本 A 上：副本 A 校验完 OAuth 2.1 Bearer Token 后发现缺少人工确认，于是直接签发带完整性保护的 `requestState`，随 `InputRequiredResult` 一起返回，然后立即释放当前 HTTP 连接，内存中不留任何会话状态。第 4 步由宿主在本地完成表单弹窗或跳转浏览器执行带外授权。第 5 步客户端换一个新的 JSON-RPC `id` 重试原请求，哪怕负载均衡器把重试打到了另一台毫无上下文的副本 B，副本 B 也能靠验签 `requestState` 重建上下文并直接产出最终结果。

## `resultType`、`InputRequiredResult` 与允许触发的边界

为了把正常完成和需要补充输入这两种结果清晰分开，`2026-07-28` 规定**所有**协议结果都必须带上 `resultType` 鉴别字段（`docs/specification/2026-07-28/basic/patterns/mrtr.mdx`）：

- `"complete"`，表示本次 RPC 已经执行完毕，返回标准结果。如果客户端连上了旧版本服务端、结果里缺了该字段，客户端必须按 `"complete"` 处理。
- `"input_required"`，表示本次请求需要客户端补充输入后再重试，即 `InputRequiredResult`。（此外，第 9 章的 Tasks 扩展还引入了 `"task"`。）

规范严格限定了只有三个客户端方法允许返回 `InputRequiredResult`：**`tools/call`**、**`resources/read`** 和 **`prompts/get`**，其他任何方法都不允许触发 MRTR。而且服务端在返回 `inputRequests` 之前，必须检查本次请求 `_meta.io.modelcontextprotocol/clientCapabilities` 里客户端是否明确声明支持对应能力（比如 `elicitation` 里的 `form` 或 `url` 模式）。如果客户端没声明，服务端绝不能强行下发，而是应当视情况降级，或者返回 `-32021`（`MissingRequiredClientCapability`）明确告知缺少哪些能力。

看一组完整的往返报文。第一次请求（`id: 1`）触发了表单追问：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "resultType": "input_required",
    "inputRequests": {
      "confirm_prod": {
        "method": "elicitation/create",
        "params": {
          "mode": "form",
          "message": "Confirm deploying v2.4.0 to prod?",
          "requestedSchema": {
            "type": "object",
            "properties": {
              "approved": { "type": "boolean" }
            },
            "required": ["approved"]
          }
        }
      }
    },
    "requestState": "eyJwcm...signature"
  }
}
```

客户端弹窗征得用户同意后，发起第二次独立请求。注意规范的两条硬要求：第一，重试请求的 JSON-RPC `id` **必须不同于**首次请求（这里变成 `id: 2`），因为它们是两个完全独立的请求；第二，如果服务端返回了 `requestState`，客户端必须一字不差地原样回传，**不得**解析、篡改，也不得把它套用到并行发出的其他请求上。如果服务端没返回 `requestState`，客户端重试时则不得凭空带上该字段。

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "deploy_release",
    "arguments": { "version": "v2.4.0" },
    "inputResponses": {
      "confirm_prod": {
        "action": "accept",
        "content": { "approved": true }
      }
    },
    "requestState": "eyJwcm...signature"
  }
}
```

如果客户端漏填了必需的回答，规范建议服务端继续返回新的 `InputRequiredResult` 重新索要，而不是直接抛协议错误；如果客户端多塞了服务端不认识的额外字段，服务端应当静默忽略。同时回想第 2 章的缓存规则：任何 `resultType: "input_required"` 中间结果，以及携带了 `inputResponses` 或 `requestState` 的重试响应，都绝对禁止写入客户端缓存。

## 把 `requestState` 当作不可信外部输入来防

因为 `requestState` 完整绕道了客户端，恶意或被攻破的客户端完全可能篡改、重放、跨账号移植或者调包参数。`mrtr.mdx` 给服务端列出了非常具体的密码学防护清单：

1. **强制完整性保护**：只要 `requestState` 会影响鉴权、资源访问或业务逻辑，服务端**必须**使用 HMAC 或 AEAD（如 AES-GCM）保护其完整性和机密性，验签失败直接拒绝。只有在篡改顶多导致请求失败的无害场景下，才允许省略完整性校验。
2. **绑定三要素防重放**：受保护的 `requestState` 载荷内应当编码三项数据并在收到重试时逐一核验：
   - **当前已鉴权主体（`principal` / `sub`）**：拒绝由其他账号呈递的状态令牌；
   - **短有效期（TTL）**：拒绝超过过期窗口的陈旧令牌；
   - **原始请求指纹（方法名加关键参数摘要）**：防止攻击者拿着审批 `v2.4.0` 拿到的 `requestState`，在重试时偷偷把参数调包成 `v9.9.9-evil`。
3. **一次性消费必须由服务端落库保证**：规范特意加了一段警告，上述三项只能收窄重放窗口、防跨人和防调包，本身**不能**保证严格一次性消费（Single-use）。像一次性核销、转账扣款这类不能被重放两次的操作，服务端必须在共享存储里记录消费状态（如 nonce 或幂等键）。

这套防线已经在第 14 章的 `MrtrReplica` 与 `test_10`/`test_11` 中完整跑通：副本 A 签发的 `requestState` 在副本 B 上正常续跑，而篡改签名、换账号重放、超时、调包参数和复用 JSON-RPC `id` 五种攻击全部被准确拦截。

## Form Mode 与 URL Mode Elicitation（`SEP-1036`）

MRTR 承接的交互请求里，因为 Roots 和 Sampling 已在 `2026-07-28` 正式弃用，真正面向 2027 的主角是 **Elicitation**（`docs/specification/2026-07-28/client/elicitation.mdx`）。它分两种模式，安全边界划得泾渭分明。

**Form Mode（表单模式）**用于采集非敏感的结构化信息，数据会经由客户端回传服务端。为了让各种桌面、网页和命令行宿主都能稳定渲染表单，`requestedSchema` 被刻意限制在扁平对象（Flat Object）子集内，属性只允许 `string`（支持 `email`、`uri`、`date`、`date-time` 格式）、`number`/`integer`、`boolean`，以及单选或多选枚举（`SEP-1330` 规范了带 `title` 的 `oneOf`/`anyOf` 与无标题的 `enum` 写法，`SEP-1034` 补齐了所有原始类型的 `default` 默认值）。深度嵌套对象和普通数组被明确禁止。用户的响应统一采用三态模型：`"accept"`（提交并附带 `content`）、`"decline"`（明确拒绝）和 `"cancel"`（关闭弹窗或按 Esc 放弃）。

规范划了一条不可逾越的红线：**服务端绝不能用 Form Mode 向用户索要密码、API Key、访问令牌或支付凭证**。凡是涉及敏感秘密或第三方系统授权的交互，都必须走 **URL Mode（`SEP-1036`）**。

在 URL Mode 中，服务端只返回一个带 `mode: "url"`、`message` 和目标 `url` 的 `elicitation/create` 请求，客户端向用户展示完整域名并征得明确同意后，通过安全隔离的系统浏览器（比如 iOS 上允许 `SFSafariViewController`，禁止客户端可窥探内容的 `WKWebView`）打开页面。用户直接在可信网页里输入敏感凭证或走完第三方 OAuth 流程，凭证直接存在 MCP Server 后端，**全程不经过 LLM 上下文，也不经过 MCP Client**。客户端重试原请求时，`inputResponses` 里只有 `{"action": "accept"}`（没有 `content`），表示用户同意打开链接；至于带外操作有没有完成，由服务端根据回传的 `requestState` 和后端状态自行判断。

这里有一个 `2026-07-28` 相比 `2025-11-25` 的关键精简：旧版曾经定义过 `elicitationId` 和服务端主动推送的 `notifications/elicitation/complete` 通知。在全面转向 MRTR 后，客户端靠重试原请求就能得知带外流程结果，服务端需要关联状态时直接把自己的标识放进 `requestState` 即可，于是这两个冗余元素被彻底删掉了。

## 防钓鱼与防令牌透传：URL Mode 的两道必考安全题

URL Mode 把用户引到了外部浏览器，立刻引入了两个经典安全陷阱，规范在 `elicitation.mdx` 里花了不少篇幅给出标准解法：

第一是**禁止拿 URL Mode 替代 MCP 自身授权，更禁止 Token Passthrough**。MCP Client 访问 MCP Server 的身份授权必须走下一章讲的标准 OAuth 2.1；URL Mode 只用来处理 MCP Server 作为第三方 API 客户端时的外部授权，而且服务端绝不能把客户端发来的 MCP Bearer Token 透传给第三方 API，也不能把第三方凭证发回给 MCP Client。

第二是**防链接转发钓鱼（Account Takeover）**。设想恶意用户 Alice 触发了一个第三方授权 URL，她自己不点，而是把链接发给同一台 MCP Server 的受害者 Bob。如果服务端直接把用户送到第三方 OAuth 授权页，Bob 点完授权后，第三方回调回来，服务端错把 Bob 的第三方令牌绑到了 Alice 的账号下，导致账号接管。标准防线是：服务端给的 URL 不应该是直接指向第三方授权页的裸链接，而是先指向 MCP Server 自己的受保护入口（例如 `https://mcp.example.com/connect?...`），且 URL 里禁止包含敏感信息或预鉴权令牌。浏览器打开该页面后，服务端必须先校验浏览器里的登录态（如 Session Cookie 对应的 `sub` 身份）与发起 MRTR 的 MCP 鉴权 `sub` 完全一致，确认是同一个人操作后，才把浏览器重定向到第三方授权服务器。同时，客户端侧禁止预抓取 URL 元数据、必须高亮展示完整域名并对 Punycode 混淆域名发出警告。

理清了服务端如何向用户安全追问之后，下一章我们把目光转向整个 MCP 远程服务的入口闸门：OAuth 2.1、受保护资源元数据、CIMD 无预注册发现与企业 IdP 集中管控。
