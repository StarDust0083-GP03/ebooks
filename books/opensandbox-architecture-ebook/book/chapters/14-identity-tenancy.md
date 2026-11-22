# 身份与多租户：三种凭据，两层边界

多团队共用一套沙箱平台时，最容易混为一谈的是「管理权限」和「进入权限」。很多接入方拿到控制面的 API key，顺手就拿它去直连沙箱里的 `execd`，或者以为在 Kubernetes 里开了多租户配置，资源配额和预热池也就自动按租户隔开了。

OpenSandbox 的身份模型恰恰反过来：**每一层只守自己那一亩三分地，不造一个包打天下的全局授权中心。**

## 三种互不替代的凭据

| 凭据 | 携带方式 | 保护对象 |
|---|---|---|
| 生命周期 API key | `OPEN-SANDBOX-API-KEY` 请求头 | 控制面管理操作（创建、列举、删除、暂停、续期、模板、快照） |
| `execd` 访问令牌 | `X-EXECD-ACCESS-TOKEN` 请求头或 PTY 子协议 | 沙箱内部数据面（命令、文件、代码、PTY、指标） |
| endpoint 凭据（`secureAccess` / `f1.` 签名 / sidecar auth） | `GET /endpoints/{port}` 响应头打包下发 | 经网关触达沙箱应用端口或调用 egress 策略接口 |

文档在数据面契约里直截了当地提醒：不要硬编码 `execd` 地址，也不要假设持有生命周期 API key 就能直接进入沙箱。单租户部署只需要在配置里填一把 `server.api_key`，除 `/health`、`/version`、`/docs`、`/redoc`、`/openapi.json` 外的接口全部校验此头；未配 key 时必须通过交互式 `YES` 或 `OPENSANDBOX_INSECURE_SERVER=YES` 显式确认，防止无鉴权实例静默上线。配置报错日志在 `1.3.0-rc.1` 做了脱敏加固，包括 `OPENSANDBOX_SECURE_ACCESS_KEYS` 在内的安全签名密钥不再被校验报错回显出来。

## 多租户：仅限 Kubernetes，身份留在提供方

多租户是可选能力，且**只支持 Kubernetes 运行时**（Docker 模式下配置 `[tenants]` 会在启动期直接致命退出）。

租户表不写死在服务器主配置里，而是通过 `TenantProvider` 对接已有身份系统，支持两种实现：

- **file 提供方**：读取本地 TOML，变更约 2 秒热重载；
- **http 提供方**：拉取远端身份接口并按 TTL 缓存；当远端不可达且缓存超过 `max_stale_seconds` 时返回 `503 TENANT_PROVIDER_UNAVAILABLE`。`1.3.0-rc.2` 修补了一处配置边界漏洞：如果远端返回的 `ttl` 为 `null`、负数、`NaN` 或 `Infinity`，不再导致查询报错或把过期租户永久锁死在缓存里，而是安全回退到 30 秒默认 TTL。

启动期有三条硬断言把配置锁死：`[tenants]` 与 `server.api_key` 互斥，同时存在直接拒绝启动；不同租户的 API key 必须唯一；所有可枚举的租户命名空间在启动阶段就校验存在且可访问（Fast Sandbox 的 CR reader 保持惰性初始化，在首次读取时核验集群凭据与权限，不拖垮纯容器部署）。

请求进入开启多租户的服务器后按六步流转：

1. `middleware/auth.py` 经 `TenantProvider` 将 `OPEN-SANDBOX-API-KEY` 解析为 `TenantEntry`（未知 key 返 `401`）；
2. 租户名与目标命名空间写入当前请求作用域的 `ContextVar`；
3. 创建、查询、列举、删除等生命周期操作全部绑定到解析出的命名空间；
4. `list` 仅返回已认证租户命名空间内的对象；
5. 服务器代理路由（`/sandboxes/{id}/proxy/...`）在多租户模式下同样强制校验 API key（区别于单租户默认行为）；
6. 创建时在工作负载上打上 `opensandbox.io/tenant=<name>` 标签。当多个租户刻意共享同一个命名空间时，读、删、代理请求对其他租户的带标签沙箱返回 `404` 实现软隔离，历史未打标签的旧沙箱在自然过期前仍保持可见，避免升级瞬间把存量任务变成黑洞。

在 Fast Sandbox 分支里，带请求上下文的操作从 `ContextVar` 读租户命名空间，而没有 HTTP 上下文的后台任务（janitor、空闲暂停扫描、快照 watch 收敛）则回落到按沙箱 ID 反查对象归属。此外，Fast Sandbox 节点与控制面之间在 `1.3.0-rc.2` 落地了 **FSEP-0003 Fastlet 控制面鉴权**：Fastlet 使用 Kubernetes ServiceAccount 令牌访问控制 API，由控制器经 JWKS 验签并默认只放行同命名空间调用方，把节点到控制面的通道也纳入了强身份校验。

## 命名空间配额与两个明确未覆盖的盲区

多租户文档对控制面自身能做什么交代得很坦率：**服务器本身不执行资源配额或网络隔离，硬隔离全部委托给 Kubernetes 原生对象**。每租户命名空间通过 `ResourceQuota` 管 CPU、内存、存储与 Pod 总数上限，通过 `LimitRange` 设容器默认限额，通过 `RoleBinding` 控 API 权限，再叠加每沙箱的 egress sidecar 管出向流量。同时 `1.3.0-rc.1` 修复了开发者控制台（Console）的租户过滤视图，不再把集群系统 Pod 和事件混进单租户视图里。

但有两个现实边界必须在架构评审时提前摆上桌面：

- **`/v1/pools` 池管理路由目前不是租户作用域的**，所有请求统一落在服务器配置的默认命名空间里。预热池当前属于集群共享基础设施；确实需要每租户独立池管理的环境，只能按租户拆成多套独立的服务器实例。
- **局部身份特性不等于租户边界**。`execd` 的 `/v1/filesystem/{uid}/{gid}` 只解决单沙箱内多会话的文件属主隔离，没有独立 mount namespace；`secureAccess` 的 endpoint 头证明调用方有权访问该端口，但不区分支配它的具体人类身份；Credential Vault 隔的是密钥明文而非租户。每一层都有明确边界，不能混用。

## 源码入口

| 路径 | 看什么 |
|---|---|
| `server/opensandbox_server/middleware/auth.py` | API key 校验、租户解析与 `ContextVar` 注入 |
| `server/opensandbox_server/tenants/file_provider.py` | 本地文件租户提供方与热重载逻辑 |
| `server/opensandbox_server/tenants/` | HTTP 租户提供方、异常 TTL 30 秒回退与启动期命名空间校验 |
| `server/opensandbox_server/api/pool.py` | 池管理路由默认命名空间行为 |
| `docs/guides/multi-tenancy.md` | 六步请求流、共享命名空间软隔离与 Kubernetes 配额分工 |
