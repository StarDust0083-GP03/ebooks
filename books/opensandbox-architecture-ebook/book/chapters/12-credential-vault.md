# 凭证保管：让 Agent 用得上密钥，却拿不到密钥

把模型 API key、Git 令牌或对象存储 AK/SK 塞进沙箱环境变量，是最快让 Agent 跑通的做法，也是最容易漏勺的做法。密钥一旦进了容器，环境变量、`/proc/self/environ`、历史日志、甚至一段被提示注入的测试脚本都能顺手把它读出来发往外网。

Credential Vault 的目标很直接：**让沙箱里的进程照常发出带鉴权的 HTTPS 请求，但沙箱自身从头到尾拿不到真实凭据。**

## 请求流与本地 Unix Socket 硬依赖

整套流程靠沙箱内假值与沙箱外注入打配合：

1. 控制面为沙箱挂载 egress sidecar（或 Fast Sandbox 共享 egress）；
2. 宿主机侧的可信调用方通过 Credential Vault API 把凭据来源与绑定规则写进 egress；
3. 沙箱内的工具（Claude Code、git、curl、npm）带着假环境变量照常运行（如 `ANTHROPIC_API_KEY=fake`），不改工具链本身；
4. 沙箱发出 HTTPS 请求，被 egress 透明 MITM 拦截并检查 scheme、host、port、method、path；
5. 恰好命中一条绑定时，代理在出站前注入真实认证头或替换占位符；
6. 响应内容与 Vault 回读接口一律脱敏，绝不回显秘密明文。

MITM 进程读取活跃 Vault 状态走 sidecar 内部私有 Unix domain socket，沙箱进程无法通过任何代理路由触达。每次新连接用不透明快照 tag 携带 `If-None-Match` 条件查询：未变更时回 `304 Not Modified`，直接复用进程内不可变快照、避免重复传输明文；更新后回 `200 OK` 与新 `ETag`，且写操作在切换 tag 后才确认，下一个流立刻生效、没有 TTL 滞后。删除再重建 Vault 时公开 revision 可能回到 `1`，但内部不透明 tag 会换新，绝不会误用已删快照里的旧秘密。

这个私有 socket 对所有被 Credential Proxy 拦截的流量都是**硬可用性依赖**。遇到超时、连接拒绝、畸形快照或非法 `ETag`，代理立刻清空受影响明文缓存并 fail-closed：已被安全缓冲的小请求返回本地 `503`；已经进入流式传输或长度未知的请求直接断开连接，宁可中断也绝不让未注入凭据的请求漏发到上游。

## 静态绑定、占位符替换与路径歧义守卫

对于静态凭据（inline source），绑定对象的 `auth` 支持五种渲染方式：

| 认证类型 | 行为 |
|---|---|
| `bearer` | 注入 `Authorization: Bearer <credential>` |
| `basic` | 注入 `Authorization: Basic <credential>`（值需已为 base64 编码的 `user:pass`） |
| `apiKey` | 注入到指定头名（如 `x-api-key`） |
| `customHeaders` | 用多个凭据分别渲染多个自定义头 |
| `passthrough` | 不自动加头，专供占位符替换使用 |

当上游接口要求凭据放在路径、查询串或请求体里时，可以打开精确、字面、大小写敏感的 `substitutions`。所有替换基于原始请求文本单遍完成（防止一个秘密意外触发二次替换），替换后自动重算 `Content-Length` 并移除 `Transfer-Encoding`；其中 `path` 替换会做 URL 编码并拦截歧义段，`header` 跳过逐跳与安全敏感头，`body` 仅处理 UTF-8 JSON 与表单并跳过压缩体与 multipart。

凡是会触发凭据注入的请求，还要过一道**路径歧义守卫**：在任意层百分号解码中出现 `..`、反斜杠（`\`、`%5c`）或解码迭代不收敛的请求，小请求直接回 `403`，超过约 1 MiB 流式阈值或 chunked 请求直接断连。唯独编码斜杠（如 `%2f`、`%252f`）网开一面，只要**每一层解码结果都命中同一条绑定**就放行，既兼容 npm 作用域包（`/@scope%2fname`）与内部 PyPI 代理的双重编码路径，又堵住借二次解码跨出绑定边界的逃逸。

## OSEP-0025：Credential Vault Exchange 动态凭据解析

把静态长期密钥塞进 Vault，在面对短期 STS 令牌或请求级签名（如 AWS SigV4、阿里云 ACS 签名）时依然不够用。`1.2.1` 至 `1.3.0-rc.2` 在服务端、egress 和全部五种 SDK 上落地了 **Credential Vault Exchange**（OSEP-0025）：

- **按请求实时换取，零落盘、零缓存**。绑定里不再存静态明文，而是指向一个 UDS 或受信 HTTPS 的外部 exchange resolver。每次命中绑定时，egress 把派生出的沙箱身份和目标请求元数据发给 resolver，实时拿回本次请求专用的认证头，Vault 本身既不落盘也不缓存解析出的动态秘密，任何解析失败一律 fail-closed。
- **支持出向请求头透传以完成重签**。`1.3.0-rc.2` 补齐了 OSEP-0025 的最后一块拼图：egress 把出向请求头透传给 exchange resolver（剔除结构性与逐跳头黑名单，上限 **64 个请求头**、单值上限 **8 KiB**，越界直接以 **`407` fail-closed** 拒掉），让 resolver 能基于原始请求头完成 SigV4、ACS 等签名重算。
- **诚实写明当前边界**。上游文档明确标注了两条限制：一是 sidecar 的实验性 live-revision 运行时暂不支持 exchange；二是 resolver 调用方的 Ed25519/JWKS 身份鉴权目前仍停留在设计阶段、尚未实现，生产上必须靠受保护的本地 UDS 或仅对受信 egress 开放的 HTTPS 加受信 CA 守住 resolver，且绝不能在沙箱的出向白名单里放行 resolver 地址。

## 前置安全要求与后端暂停差异

要启用 Credential Vault，必须满足四项硬前提：服务端配置 `[egress].mode = "dns+nft"`（纯 DNS 过滤无法防直连 IP 绕过，Vault 会直接拒绝激活）；创建沙箱时附带出向 `networkPolicy` 且强烈推荐 `defaultAction = "deny"`（兼容期内 `allow` 仍可跑但会打安全警告）；沙箱 Pod 不得混装透明服务网格 sidecar；需要严格限制作用面时可开启默认关闭的三个安全旋钮：`OPENSANDBOX_EGRESS_CREDENTIAL_VAULT_REQUIRE_TLS`、`TRUSTED_PROXY_CIDRS` 与 `REQUIRE_SCOPED_MATCH`（强制每条绑定写明非空 `methods` 和 `paths` 且禁用 `/*`）。

暂停再恢复之后，Vault 状态在不同后端上行为截然不同，极易引发线上误判：

- **Kubernetes 容器后端与 Docker**：Vault 保存在 egress sidecar 进程内存里，不在容器 rootfs 中。Kubernetes 暂停会销毁旧 Pod，恢复后新拉起的 sidecar **Vault 是空的**；Docker 重启或替换 sidecar 同样清空 Vault。恢复到 `Running` 后，可信控制面必须重新调用 Vault API 注入凭据与绑定，绝不能把真实密钥塞进沙箱 `metadata`、环境变量或快照里凑数。
- **Fast Sandbox**：服务端经 FastPath relay 把完整 Vault revision 同步推给共享 egress，egress 在内存外同时写入 Pod 级 `emptyDir` 的每沙箱明文文件，暂停时在 teardown 前导出进检查点并在恢复时带运行时围栏重新导入。代价是这块 `emptyDir` 卷成为凭据信任面的一部分，生产建议配 `medium: Memory` 避免落宿主机磁盘。

## 源码入口

| 路径 | 看什么 |
|---|---|
| `components/egress/pkg/credentialvault/` | Vault 存储、不透明 tag、Exchange 解析与脱敏逻辑 |
| `components/egress/mitmscripts/` | 请求头注入、占位符单遍替换、64 头/8 KiB 透传上限与路径守卫 |
| `server/opensandbox_server/api/credential_vault.py` | 服务端 Vault 路由与 Fast Sandbox relay 转发 |
| `docs/guides/credential-vault.md` | 认证类型、路径守卫、Exchange 边界与暂停后重载规范 |
| `oseps/0012-credential-vault.md` | Credential Vault 原始设计提案 |
| `oseps/0025-credential-vault-provider-spi.md` | 动态凭据交换（Exchange）与请求头签名支持 |
