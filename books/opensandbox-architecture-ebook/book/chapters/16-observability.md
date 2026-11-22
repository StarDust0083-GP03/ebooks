# 可观测性与诊断：让短命沙箱留下可核验的证据

传统后端服务排查故障，登录机器捞过去半小时的日志就行。沙箱平台的观测难在对象极短命：一个编码任务跑了 90 秒，建容器、装依赖、崩掉、被回收，进程和文件系统转眼烟消云散。如果此时用户只甩来一句「Agent 执行挂了」，手头往往连 Pod 名字都找不回来。

OpenSandbox 用五层互不重叠的证据链来接住这个问题，并且在 `release-1.3.0` 里把废弃的旧排障入口清理干净，同时补齐了 `execd` 磁盘遥测和 Node Agent 的虚拟机控制台采集。

## 第一、二层：状态字段、诊断上下文与请求 ID

最廉价的证据就在日常 API 响应里：沙箱对象的 `state`、`reason`、`message` 和 `lastTransitionAt` 能立刻区分是镜像拉取失败、池容量耗尽，还是主进程正常退出（`SANDBOX_COMPLETED`）。服务端挂在最外层的 `RequestIdMiddleware` 会透传或自动生成 `X-Request-ID`，串起控制面、网关、控制器和代理的同一条调用链。

需要查底层宿主对象时，`GET /v1/sandboxes/{id}/diagnostics/context` 返回运行时后端名和对应标识符：Docker 返回容器 ID，Kubernetes 返回命名空间、工作负载名与已分配 Pod 名（可在 Pod 分配前查询），Fast Sandbox 返回 `fast-sandbox` 加空 map（适配层不假装持有集群外运行时标识）。

## 第三层：收口后的 Diagnostics API（移除旧 `devops` 路由）

早期版本同时存在契约化的 Diagnostics API 和一套面向手工排障的纯文本 `api/devops.py` 路由（对应 CLI 的 `osb devops`）。两套口径并存极易漂移，**`release-1.3.0-rc.1` 彻底删除了 `api/devops.py`、`diagnostics/inspect`、`diagnostics/summary`、无 `scope` 的纯文本回退参数（`tail`/`since`/`container`/`limit`）以及 `osb devops` 命令群**。

现在的诊断通道全部收口在 `server/opensandbox_server/api/diagnostics.py`（112 行）和 `specs/diagnostic-api.yml` 之下：

```text
GET /v1/sandboxes/{id}/diagnostics/context
GET /v1/sandboxes/{id}/diagnostics/logs?scope=...    （scope 必填，省略返 422）
GET /v1/sandboxes/{id}/diagnostics/events?scope=...  （scope 必填，省略返 422）
```

各后端支持的 `scope` 边界明确：Docker 与 Kubernetes 容器后端对日志支持 `container`/`all`、对事件支持 `runtime`/`all`；Fast Sandbox 只支持事件（`runtime`/`all`），日志返回 `501 DIAGNOSTICS_NOT_IMPLEMENTED`。

响应统一返回 JSON 描述符，包含 `delivery`（`inline` 直接带 `content`，`url` 带 `contentUrl` 与过期时间）、`truncated` 布尔标记和可选 `warnings`。调用方必须牢记两条纪律：SDK 不会替你自动拉取 `contentUrl`；把日志拿去做根因分析前，先看 `truncated` 与 `warnings`，别把截断输出误读成完整日志。CLI 用户现统一迁移至 `osb diagnostics logs <id> --scope container -o raw`、`osb diagnostics events <id> --scope runtime -o raw` 和 `osb diagnostics context <id>`。

## 第四层：OTLP 指标与低基数纪律

Go 组件共用一套 OTLP 环境变量配置，Python 控制面走 `[otel]` 配置段。各组件覆盖的问题边界互补：

| 组件 | 关键可观测信号 |
|---|---|
| 生命周期服务器 | 创建延迟、HTTP 状态码分布、请求量；CRD 未装时快照 informer 仅记单条带名告警（`1.3.0-rc.2` 消除每 30 秒刷屏 traceback） |
| Kubernetes 控制器 | 池分配路径指标、缓冲水位与 controller-runtime 队列指标 |
| `execd` | CPU、内存、执行耗时、OOM 观测；**`1.3.0-rc.2` 新增 OTLP 磁盘用量仪表（`execd.system.disk.usage_bytes` / `total_bytes`，单挂载 stat 限时 2 秒防网络盘卡死）与累计 IO 计数器（`execd.system.disk.io.bytes` / `io.operations`，优先 cgroup 回退 `/proc/diskstats`）**；`/metrics/watch` 修正为精确 1 秒推送 |
| `egress` | 策略拒绝数、DNS 查询与解析失败数、nftables 更新错误、实验性 credential-bound 迁移计数与延迟直方图；`1.3.0-rc.2` 上游 TLS 失败日志带连接 ID 与固定原因码（如 `self_signed`）且不泄漏主机名或凭据 |
| `ingress` | 请求路由延迟、上游 TCP 连通性分类（影子 `network-readiness`） |
| `node-agent` | 采集记录数、字节数、有意丢弃、重试次数与有界队列深度 |

整套指标体系死守两条硬纪律：一是**把策略正常拒绝和系统故障拆成独立计数器**（例如 egress 拒绝与 DNS 解析器故障绝不混计），否则值班告警会颠倒因果；二是**严控 label 基数**，绝不把用户查询域名、目的 IP、沙箱 ID 或 Pod UID 放进指标标签，细节留给日志。

## 第五层：Node Agent 让日志活过沙箱寿命

沙箱一旦销毁，前面四层里依赖活体对象的接口都会变成 `404`。可选 DaemonSet 组件 `components/nodeagent/` 负责把节点上短命沙箱的输出提前落进持久对象存储，并附带可验证的完整性结案报告。

除了 kubelet 容器日志与 eBPF 系统调用源，`1.2.1` 还为 Node Agent 新增了可选的 **`vm-console` 采集源**：读取节点本地 VM 元数据与崩溃安全偏移量，持续收集 Fast Sandbox guest 内核与 init 控制台日志，不依赖集群级 Sandbox watch。同时 Fast Sandbox 第一方 Kubernetes 组件自 `1.2.1` 起默认向 `/var/log/fast-sandbox` 写带轮转的日志文件（单文件 100 MiB、保留 10 份、7 天过期、gzip 压缩），按需开启 stderr 镜像。

Node Agent 的四个核心工程决定极具参考价值：

- **编译期源与 sink 注册表**：运行时绝不动态加载外部插件，未知名称直接令 Agent 进入不就绪状态，堵死采集进程变成集群 RCE 入口的风险。
- **有界队列与显式背压/丢弃**：默认 `block` 模式在后端变慢时反压源端不丢数据；`drop` 模式锁死内存上限，但把受影响区间诚实记入流完整性标记。全局与每沙箱双层预算防止单个话痨沙箱饿死整机。
- **失败即降级就绪，拒绝崩溃重启循环**：配置非法、恢复状态损坏或目标桶不合规（如开启版本控制、WORM 或存在重叠生命周期规则）时，进程保持存活但 `readyz` 失败并报明原因（`1.3.0-rc.1` 修复了 IPv6 主集群下 readyz 探针未给 IPv6 地址加方括号导致的误报），绝不静默清零偏移重采。
- **两阶段事务与三态 `finalized` 完整性标记**：每条流按 `<cluster>/<namespace>/<sandbox_id>/<pod_uid>/` 组织，附带不可变的 `sandbox.finalized.<revision>.json`，给出每个分片的大小与校验和，并标明三种诚实状态：`complete`（无已知缺口，至少一次投递可能含重复）、`complete-with-drops`（已记录有意丢弃区间）或 `incomplete`（如进程重启期间无法证明未遗漏短命日志轮转）。采集凭据刻意**不授予 delete 权限**，清理留给独立离线工具。

## 源码入口

| 路径 | 看什么 |
|---|---|
| `server/opensandbox_server/api/diagnostics.py` | 必填 `scope` 的日志、事件与上下文路由（取代旧 `devops.py`） |
| `server/opensandbox_server/services/diagnostics.py` | 诊断上下文结构与 `DiagnosticResult` 描述符 |
| `components/execd/pkg/telemetry/init.go` | OTLP CPU/内存/磁盘用量与 IO 指标导出 |
| `components/nodeagent/pkg/marker/` | 三态 `finalized` 完整性标记与校验和生成 |
| `docs/architecture/data-plane/node-agent.md` | 有界队列、fail-closed 就绪、`vm-console` 与对象布局 |
| `docs/guides/telemetry.md` | 全组件 OTLP 配置与指标清单 |
