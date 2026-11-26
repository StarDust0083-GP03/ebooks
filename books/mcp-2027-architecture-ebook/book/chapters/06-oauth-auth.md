# 身份与授权体系：OAuth 2.1、CIMD 与企业 IdP

把远程 MCP Server 推向公司内部时，最先卡住的几乎从来不是工具写得好不好，而是安全团队的三个问题：第一，陌生客户端连上来之前既没有预分配的 `client_id`，企业 IdP 又为了防滥用关掉了动态客户端注册（DCR），怎么在不手工建几百个 OAuth 应用的前提下完成登录？第二，员工连了十几个 MCP Server，每个服务都要弹一次浏览器授权，员工离职时怎么保证一键回收全部 Agent 访问权限？第三，如果一个恶意 MCP Server 诱骗客户端把发给别家服务的 Token 拿来复用，或者用恶意授权服务器实施 Mix-Up 混肴攻击，协议层有没有硬防线？

`2026-07-28` 规范以及稳定版官方授权扩展仓库 `modelcontextprotocol/ext-auth`，已经把这一整套基于 IETF 标准协议栈的身份边界搭了起来。对 HTTP 传输层来说，授权是可选的，一旦启用就必须遵守这套规范；而本地 `stdio` 传输则明确**不应**套用 HTTP OAuth 流程，应从本地受控环境获取凭证。

## 标准协议栈与三方角色

MCP 没有自己发明一套专有鉴权协议，而是把九份成熟 IETF/OIDC 标准拼成了一条可互操作的链路（`docs/specification/2026-07-28/basic/authorization/index.mdx`）：

- **OAuth 2.1（draft-ietf-oauth-v2-1-13）与 RFC 6750**：要求 HTTPS、强制 PKCE（`S256`）、仅允许 `Authorization: Bearer <token>` 请求头（严禁把 Token 放进 URI 查询参数）、公开客户端强制 Refresh Token 轮换。
- **RFC 9728（OAuth 2.0 Protected Resource Metadata，`SEP-985`）**：明确 MCP Server 是 OAuth **资源服务器（Resource Server）**，通过元数据向客户端指明背后对应的授权服务器（Authorization Server）是谁。
- **RFC 8414 与 OpenID Connect Discovery 1.0**：客户端发现授权服务器端点与能力的标准机制。
- **RFC 8707（Resource Indicators）**：把 Access Token 严格绑定到目标 MCP Server 的规范 URI（Canonical URI），从协议源头杜绝令牌跨服务滥用和 Token Passthrough。
- **OAuth Client ID Metadata Documents（CIMD，`SEP-991`）**：用 HTTPS URL 作为 `client_id`，取代已弃用的 RFC 7591 动态注册。
- **RFC 9207（`SEP-2468`）**：在授权响应里校验 `iss` 参数，防 OAuth Mix-Up 攻击。

把 MCP Server 定位成标准 OAuth 资源服务器的好处非常直接：鉴权逻辑可以完全委托给专门的授权服务器（如 Okta、Auth0、Keycloak、Stytch、WorkOS 或自建 IdP），MCP Server 只需做好无状态的令牌验签、受众核对和作用域检查。

## 授权服务器发现与受众绑定

客户端第一次无令牌访问受保护的 MCP Server 时，整条发现与换票链按严格顺序走完：

1. **受保护资源发现（RFC 9728）**：MCP Server 返回 `HTTP 401 Unauthorized`，并在 `WWW-Authenticate` 头里给出 `resource_metadata` URL（以及可选的最小初始 `scope` 建议）。客户端必须优先解析该头里的地址；如果头里没带，则依次探测路径级 `/.well-known/oauth-protected-resource/<mcp-path>` 和根路径 `/.well-known/oauth-protected-resource`。
2. **授权服务器元数据发现（RFC 8414 / OIDC）**：拿到 `authorization_servers` 后，客户端必须按优先级依次尝试路径插入和追加的 `.well-known/oauth-authorization-server` 与 `.well-known/openid-configuration` 端点。拉回文档后，客户端**必须**按 RFC 8414 Section 3.3 核对文档里的 `issuer` 字段与构造 URL 所用的发行方标识完全一致，否则立刻拒收（防止恶意站点伪造诚实发行方的元数据）。客户端还**必须**检查 `code_challenge_methods_supported` 存在以确认支持 PKCE，缺失则拒绝继续。
3. **强制 `resource` 参数（RFC 8707）**：客户端在授权请求和换票请求中都**必须**带上目标 MCP Server 的规范 URI（如 `https://mcp.example.com/mcp`，不含 fragment，一般不带末尾斜杠）。MCP Server 在收到每条带 Bearer Token 的请求时，**必须**验证令牌的受众（`aud`）确实指向自己，不匹配就返回 `401`。

规范在 `2026-07-28` 补齐了一项容易踩坑的多授权服务器隔离要求（`SEP-2352`）：如果一个受保护资源列出多个授权服务器，或者后续切换了授权服务器，客户端**必须**按 `issuer` 标识分桶持久化客户端凭证和令牌，绝不能把 A 授权服务器颁发的 `client_id` 拿去 B 授权服务器复用。

## 从 DCR 到 CIMD（`SEP-991`）与防 Mix-Up（`SEP-2468`）

在早期的 MCP 规范里，面对客户端和服务端素不相识的场景，默认推荐的是 RFC 7591 动态客户端注册（DCR）。但运行一年后，DCR 暴露了两个现实顽疾：大量企业 IdP 出于防垃圾注册和合规考虑根本不开放公开 `/register` 接口；而在代理型 MCP Server 场景下，DCR 叠加静态上游 `client_id` 和浏览器 Consent Cookie，还会诱发严重的 Confused Deputy 攻击（攻击者注册一个指向自己域名的恶意客户端，利用用户浏览器里残留的同意 Cookie 静默跳过授权确认页，偷走授权码）。

于是 `2026-07-28` 正式将 DCR 标为 Deprecated（`PR #2858`），并把 **Client ID Metadata Documents（CIMD，`SEP-991`）** 扶正为无预置关系时的首选注册方案（最新 `draft/` 规范已将其引用升级到 `draft-ietf-oauth-client-id-metadata-document-03`）。

CIMD 的思路非常清爽：客户端把自己的元数据 JSON 托管在一个带路径的 HTTPS URL 上，比如 `https://app.example.com/oauth/client-metadata.json`，文档内包含 `client_id`（必须与文档 URL 逐字符一致）、`client_name` 和 `redirect_uris`。发授权请求时，直接把这个 URL 当作 `client_id` 传给授权服务器。授权服务器看到 URL 形态的 `client_id`，主动 GET 拉取并缓存这份文档，核验 `client_id` 一致性、`redirect_uri` 白名单以及自身信任策略（例如按域名白名单管控，并做好出站 SSRF 防护），无需预先建库、也无需跨授权服务器反复注册，同一个 URL `client_id` 天生跨所有支持 `client_id_metadata_document_supported: true` 的授权服务器通用。

客户端注册的推荐优先级由此收敛为四档：优先用预注册凭证，其次用 CIMD，再次回退到 DCR（且 OIDC 场景下按 `SEP-837` 必须显式指定 `application_type: "native"` 或 `"web"` 以免本地回调 URI 冲突），最后才提示用户手动填写。

但 CIMD 的静态文档无法为每个不同发行方配唯一的 `redirect_uri`，这让多 IdP 环境下的 OAuth Mix-Up 攻击风险抬头。为此，`2026-07-28` 并入了 **`SEP-2468`（RFC 9207 `iss` 校验）**：客户端重定向前必须在与 PKCE `code_verifier` 相同的单次请求记录里保存预期 `issuer`；收到回调时（包括错误回调），只要响应里带了 `iss`，或者授权服务器元数据声明了 `authorization_response_iss_parameter_supported: true`，客户端就**必须**在兑换授权码前用逐字符精确匹配（严禁大小写折叠、默认端口省略或路径归一化）核对 `iss`。声明支持却缺 `iss`、或者 `iss` 对不上，一律立刻中止，且不得把错误响应里的 `error_description` 展示给用户。

## 最小权限渐进提权与 Refresh Token 约定

MCP 遵循最小权限原则，并不鼓励客户端一上来就索要全部读写删权限。如果客户端拿着只读令牌调用了写操作工具，MCP Server 返回 `HTTP 403 Forbidden`，并在 `WWW-Authenticate` 头里明确给出本操作所需作用域：

```http
HTTP/1.1 403 Forbidden
WWW-Authenticate: Bearer error="insufficient_scope",
                         scope="files:write",
                         resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource"
```

服务端应当在单次挑战里一次性给出当前操作所需的全部作用域，避免逐个挤牙膏导致用户连点好几次授权页。客户端收到挑战后执行 **Step-Up 授权流**：计算已有作用域与新挑战作用域的并集重新发起授权（避免丢掉先前其他操作已获批的权限），并限制重试次数防止死循环。

对于长时运行所需的 Refresh Token，`SEP-2207` 补齐了 OAuth 2.1 与 OIDC 的互操作细节：需要刷新令牌的客户端应当在元数据 `grant_types` 里声明 `refresh_token`，并在授权服务器 `scopes_supported` 包含 `offline_access` 时主动追加该作用域；反过来，MCP Server（资源服务器）**不应**把 `offline_access` 放进 `WWW-Authenticate` 或受保护资源元数据中，因为刷新令牌是客户端与授权服务器之间的事，不是资源本身的访问权限。

## 企业级两大稳定授权扩展

当 MCP 离开个人浏览器、进入后台流水线和万人规模企业内网时，核心规范的交互式登录就不够用了。官方 `ext-auth` 仓库已经把两项扩展推到了 **Stable** 阶段：

**机器对机器授权（`io.modelcontextprotocol/oauth-client-credentials`，`SEP-1046`）**，面向 CI/CD 流水线、定时后台代理和服务端到服务端集成。客户端通过 `grant_type=client_credentials` 直接拿应用凭证换票，依然必须携带 `resource` 参数绑定目标 MCP Server。规范强烈推荐采用 **RFC 7523 JWT Bearer 断言**（私钥签发短效 JWT，且按 RFC 10070 规定 `aud` 只能填授权服务器 `issuer` 标识，不得填 token 端点 URL），避免在环境里存放一旦泄露就长期有效的明文 `client_secret`。官方 SDK 示例还特意要求配置 `expectedIssuer` 钉死颁发凭证的授权服务器，拒绝把密钥发给任何第三方广告的未知授权端点。

**企业托管授权（`io.modelcontextprotocol/enterprise-managed-authorization`，`SEP-990`）**，彻底解决文章开头安全团队问的第二个问题：员工不需要逐个去几十个 SaaS MCP Server 点同意，IT 管理员直接在企业 IdP（如 Okta、PingFederate、Descope、Athenz、ThunderID 等）里集中配置访问策略。流程基于 IETF 正在推进的 **ID-JAG（Identity Assertion JWT Authorization Grant）** 草案与 **RFC 8693 Token Exchange**：

1. 员工使用企业 SSO 登录 MCP Client，客户端拿到企业 IdP 签发的 OIDC ID Token 或 SAML 断言。
2. 客户端连接企业批准的 MCP Server 时，拿着该身份断言去企业 IdP 的 Token 端点发起 RFC 8693 令牌交换（`requested_token_type: urn:ietf:params:oauth:token-type:id-jag`），指定目标 MCP 授权服务器的 `audience` 和可选的 `resource`。
3. 企业 IdP 实时评估群组、角色和条件访问策略，策略通过才签发 ID-JAG；离职或无权限员工在这一步就被挡住，客户端拿不到任何票据。
4. 客户端用 `urn:ietf:params:oauth:grant-type:jwt-bearer` 拿着 ID-JAG 向 MCP 授权服务器直接换取访问令牌，全程无须弹窗跳转，且后续在企业 IdP 侧一键吊销即可全局生效。

截至 2026 年 11 月官方扩展文档记录，Okta（Cross App Access）、PingFederate 13.1+、Athenz、Descope、Janssen、ThunderID 已正式支持签发与校验 ID-JAG，Auth0、Keycloak、Stytch、WorkOS、Cloudflare `workers-oauth-provider` 和 FastMCP 4.0 也已进入可用或早期体验阶段。而在官方 2026 年 8 月路线图里，新筹建的 Agent Identity 工作组正沿着这条线继续往深处走：推动 **DPoP**（防截获令牌重放的持有证明）、**工作负载身份联邦（SEP-1933）**，以及面向无头子代理的窄权限委托（与 IETF WIMSE 协同）。

至此，MCP 的无状态传输、强类型原语、MRTR 追问和企业级身份底座已经全部立稳。接下来的问题是如何让协议既能快速长出 Skills、Tasks、Events、Apps 这些高迭代能力，又不至于把核心规范撑成一个臃肿难改的巨无霸。下一章我们看 `SEP-2133` 与 `SEP-3392` 的扩展治理框架。
