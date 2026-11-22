# 入向网络：endpoint 解析、ingress 与访问续期

单机上连沙箱里的 Web 服务很轻松，记个 `localhost:40001/proxy/8080` 就完事了。一上 Kubernetes 集群，麻烦立刻堆上来：沙箱 Pod 只有内网 ClusterIP，暂停恢复后 IP 必然换掉，浏览器开着交互终端走 WebSocket 不能被网关擅自改写关闭码，前置 TLS 代理一开 HTTP/2 还会把 WebSocket 升级挡在门外。

OpenSandbox 把入向问题拆成两半：对外用 `GET /v1/sandboxes/{id}/endpoints/{port}` 屏蔽拓扑差异，对内用无状态的 `components/ingress/` 反向代理统一承接集群流量。

## 三种访问形态，服务端代理转入维护模式

endpoint 接口始终返回「可达地址 + 必须原样携带的请求头」，背后的物理路径分三种：

| 形态 | 适用场景 | 定位 |
|---|---|---|
| 直连 | Docker 单宿主端口映射或 Pod IP 直接可达 | 单机或同网段主路径 |
| ingress 网关 | Kubernetes 与 Fast Sandbox 集群部署 | 集群生产主路径 |
| 控制面代理 | 直连与网关均不可达的兼容环境 | **维护模式退路**（`1.2.1` 起建议迁移至 ingress） |

服务端 `/sandboxes/{id}/proxy/{port}` 在 `1.2.1` 被明确划进维护模式：存量流量照常工作，但吞吐与隔离的演进重点全部放在独立部署的 `ingress` 组件上。

## 无 per-sandbox 配置的 ingress 路由

`components/ingress/` 面对成百上千个短命沙箱，坚持**零每沙箱静态配置**。它直接 watch 底层工作负载发布出来的状态，把路由实时缓存在内存里：

- `BatchSandbox` 读取 `sandbox.opensandbox.io/endpoints` 注解；
- `agent-sandbox` 读取 `status.serviceFQDN`；
- Fast Sandbox 路由由 `FastSandboxProvider` 校验签名 `f1.` scope 并向 FastPath 解析实际 Fastlet 目标。

客户端标识「沙箱 ID + 端口」有两种模式：

- **头模式（默认）**：携带 `OpenSandbox-Ingress-To: <sandbox-id>-<port>`，或通过主机名子域编码 `<sandbox-id>-<port>.example.com`（Fast Sandbox 不支持通配主机名模式，因为命名空间、ID 与签名塞不进单个 DNS label）；
- **URI 模式**：路径前两段编码为 `/<sandbox-id>/<port>/<rest>`，照顾无法自定义请求头的客户端。

`1.3.0-rc.1` 在头模式与 URI 模式的端口解析里收紧了校验，带前导加号的端口段（如 `+8080`、`+08080`）直接拒掉，不再被宽松解析后静默路由；历史主机名里的端口也要求为 `1` 到 `65535` 之间的规范十进制数。

转发动作克制而严格：剥除内部路由头与续期控制头、注入 `X-Forwarded-*`、过滤逐跳头、**原样保留路径与查询串**（不做合并 `//` 或解码 `%2F` 这类善意规范化），并且**绝不跟随后端返回的重定向**，防止过期 `Location` 把沙箱凭据误发给无关主机。

## 三类错误码与 WebSocket 一等路径

ingress 对路由阶段的失败坚持三分法，确保调用方能做出正确决策：

| 状态码 | 含义 | 调用方动作 |
|---|---|---|
| `404` | 沙箱确实不存在 | 停止重试 |
| `503` | 沙箱存在但尚未就绪，或 Fast Sandbox 返回 `FailedPrecondition`（附 `Retry-After`） | 按退避重试 |
| `502` | 集群 API 或上游基础设施故障 | 触发运维告警 |

一旦流量真正抵达沙箱，响应码和响应体全部原样透传。WebSocket 在这里作为一等主路径处理：遵循 RFC 6455 over HTTP/1.1，后端握手预算 45 秒，双向单条消息上限 64 MiB，不缓冲对端，原样透传子协议、`Set-Cookie` 以及应用关闭码（比如策略拒绝 `1008` 或自定义码 `4001+` 绝不被改成正常关闭 `1000`）。`1.3.0-rc.1` 还修掉了一处中继细节：当后端拒绝 WebSocket 握手且错误体大于 1 KiB 时，代理会丢弃陈旧的 `Content-Length`，保证完整报错体直达客户端。

由于 `ingress` 本身只说明文 HTTP/1.1、不做 TLS 终结，生产环境通常在前面挂 L7 TLS 代理。这里有一条硬约束：RFC 8441 的 WebSocket-over-HTTP/2 不被接受，绝不能把 HTTP/2 转发给 `ingress` 本身。最稳妥的部署是从面向浏览器的 ALPN 列表去掉 `h2`，或者让前置代理对 WebSocket 升级回退 HTTP/1.1。

## 两个健康端点：把进程存活和网络体感拆开

`ingress` 有意暴露了两个语义分开的健康端点：

- `/status.ok`：标准存活与就绪探针，控制负载均衡摘流；
- `/status.ok/network-readiness`：由 `pkg/proxy/connectivity/tracker.go` 驱动的**影子信号**，统计近期打开的上游 TCP 连接分布（success、timeout、unreachable、refused），当 timeout 与 unreachable 在多个不同目标上占主导时输出 `DEGRADED`，但 HTTP 状态码永远返回 `200` 且**绝不影响流量调度**，指标里也不含目标 IP 或沙箱 ID。

把「进程还能不能干活」和「集群跨节点网络看起来健不健康」拆成两个信号，避免了一次底层网络抖动把所有健康网关 Pod 一起摘掉引发雪崩。

## `secureAccess`、唤醒预算与访问驱动续期

在 Kubernetes 加 ingress 模式下开启 `secureAccess` 后，控制面为 endpoint 签发访问凭据并打包在响应头里。这些头本身就是沙箱访问凭据，绝不能塞进 URL 或共享缓存。若经控制面代理改写地址，`api/proxy.py` 只剥除 `OpenSandbox-Ingress-To` 路由头，保留其他鉴权头。

流量穿过 ingress 时还会顺手驱动两条生命周期联动：

- **访问即续期（OSEP-0009）**：解析出目标沙箱后，网关按沙箱节流发布一条尽力而为的 `renew-intent` 事件（经 Redis 送到控制面，服务器代理路径也遵守同样规则）。监控探针不想替空闲沙箱续命时，可以带上精确头 `OpenSandbox-Access-Renew: skip`（转发前会被剥除）；长连 WebSocket 仅在握手请求上触发，不按帧续期。
- **访问唤醒（OSEP-0024）**：配合空闲暂停策略，命中已暂停沙箱的请求会进入唤醒排队等待。`1.2.1` 把唤醒等待统一收进单一的排队加等待预算，并强制校验 `--wake-park-budget` 必须小于 `--wake-max-flight-lifetime`，同时修复了集群同时混跑常规容器沙箱和 Fast Sandbox 时唤醒路由在启动期 panic 的缺陷；观测探针可通过 `OpenSandbox-Skip-Activity: skip` 避免重置自己测量的空闲计时器。

## 源码入口

| 路径 | 看什么 |
|---|---|
| `components/ingress/pkg/proxy/header.go` | 路由头、续期头、`skip` 哨兵与逐跳头常量 |
| `components/ingress/pkg/proxy/connectivity/tracker.go` | 上游连接分类与 `network-readiness` 影子降级判定 |
| `server/opensandbox_server/api/proxy.py` | 维护模式控制面代理与路由头剥离 |
| `docs/architecture/network/ingress.md` | 路由模式、错误三分法、WebSocket 规则与前置 TLS 约束 |
| `docs/guides/secure-access.md` | 签名路由与 endpoint 凭据安全边界 |
