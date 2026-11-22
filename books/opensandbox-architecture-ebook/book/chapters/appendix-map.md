# 附录 A：源码阅读地图

以下路径与行数均核对于 `opensandbox-group/OpenSandbox` 的 `release-1.3.0-rc.2` 提交 `45043e7f0df258b6b76ef6753dd9b1819d59a332`（2026-11-19）。

## 三条阅读路线

**路线一，快速跑通调用方视角**，依次浏览 `README.md`、`docs/getting-started/index.md`、`docs/getting-started/configuration.md`、`docs/api/index.md`、目标语言 SDK 的 README 与 `cli/README.md`，弄清创建参数、endpoint 携带请求头、快照模式和命令 SSE 解析方式。

**路线二，评估或自建执行层**，先通读 `docs/architecture/index.md`、控制面 `server.md` / `operator.md`、数据面 `execd.md` / `node-agent.md`、网络 `ingress.md` / `egress.md`、`docs/api/implementation-compatibility.md` 与 `ROADMAP.md`，再按目标拓扑细读 `single-host-network.md` 或 `docs/architecture/fast-sandbox/*`。

**路线三，动手改源码或接新后端**，顺着每章末尾的源码入口和下方按职责切分的路径表深入对应包目录。

## 按职责划分的核心入口

### 控制面（Python，`server/`）

| 路径 | 行数 | 看什么 |
|---|---:|---|
| `server/opensandbox_server/main.py` | 367 | lifespan 启动断言、中间件与路由挂载 |
| `server/opensandbox_server/startup_guard.py` | 117 | 无 API key 时的确认守卫 |
| `server/opensandbox_server/config.py` | ~1300 | 全局配置模型与默认开启的 `runtime.execd_run_as_init` |
| `server/opensandbox_server/api/lifecycle.py` | 547 | 生命周期与快照路由及校验顺序 |
| `server/opensandbox_server/api/diagnostics.py` | 112 | 必填 `scope` 的日志、事件与上下文路由（取代已删 `devops.py`） |
| `server/opensandbox_server/api/pool.py` | 279 | 池管理路由、`WorkloadProvider` 懒装配与 `501` 拒绝 |
| `server/opensandbox_server/api/proxy.py` | 844 | 维护模式控制面代理、`Location` 重写与 WebSocket |
| `server/opensandbox_server/api/schema.py` | 2054 | OpenAPI 契约的 Pydantic 落地（含 `Snapshot.mode`） |
| `server/opensandbox_server/services/sandbox_service.py` | 345 | 跨运行时抽象接口与共用元数据校验 |
| `server/opensandbox_server/services/factory.py` | 66 | `runtime.type` 到服务实现的装配 |
| `server/opensandbox_server/services/composite_service.py` | 184 | `fsb-` 前缀分发与快照后端分流 |
| `server/opensandbox_server/services/validators.py` | 673 | 请求侧集中校验与 `subPath` 防遍历 |
| `server/opensandbox_server/services/snapshot_service.py` | 999 | 公开快照状态机、`mode` 持久化与恢复扫描 |
| `server/opensandbox_server/repositories/` | 多文件 | SQLite 与 PostgreSQL 两套目录存储 |
| `server/opensandbox_server/tenants/` | 多文件 | file / http 租户提供方与异常 TTL 回退 |
| `server/opensandbox_server/integrations/idle_pause/` | 多文件 | 空闲暂停扫描、分布式锁与执行 |
| `server/opensandbox_server/integrations/renew_intent/` | 多文件 | 访问续期意图产生与消费 |

### Docker 后端

| 路径 | 看什么 |
|---|---|
| `server/opensandbox_server/services/docker/docker_service.py` | 生命周期主流程与 `_sandbox_lifecycle_lock` 续期锁 |
| `server/opensandbox_server/services/docker/container_ops.py` | `execd` 暂存、`EXECD_INIT=1` 注入与磁盘/PID 限额 |
| `server/opensandbox_server/services/docker/networking.py` | 网络模式、`publish_host` 与 sidecar 绑定 |
| `server/opensandbox_server/services/docker/port_allocator.py` | 宿主端口分配与回收 |
| `server/opensandbox_server/services/docker/metadata.py` | `opensandbox.io/` 保留标签校验 |
| `server/opensandbox_server/services/docker/snapshot_runtime.py` | 本地 `docker commit` 与暂停态快照 |
| `server/opensandbox_server/services/docker/windows_profile.py` | Windows 容器行为特化 |

### Kubernetes 后端与控制器

| 路径 | 看什么 |
|---|---|
| `server/opensandbox_server/services/k8s/workload_provider.py` | `WorkloadProvider` SPI 与池服务扩展点（446 行） |
| `server/opensandbox_server/services/k8s/batchsandbox_provider.py` | `Completed -> SANDBOX_COMPLETED` 映射与 pause/resume 校验（1217 行） |
| `kubernetes/apis/sandbox/v1alpha1/batchsandbox_types.go` | `BatchSandbox` 规格、状态与 `Completed` 终态 |
| `kubernetes/apis/sandbox/v1alpha1/pool_types.go` | 两级容量规格与回收策略 |
| `kubernetes/apis/sandbox/v1alpha1/sandboxsnapshot_types.go` | 内部与公开快照 CRD |
| `kubernetes/internal/controller/batchsandbox_controller.go` | Pod 扩缩、任务调度、`resourceVersion` 状态更新与过期清理 |
| `kubernetes/internal/controller/pool_controller.go` | 暖缓冲收敛、分配注解与模板滚动 |
| `kubernetes/internal/controller/sandboxsnapshot_controller.go` | commit Job 与镜像 digest 验收 |

### Fast Sandbox 适配

| 路径 | 看什么 |
|---|---|
| `server/opensandbox_server/services/fast_sandbox/service.py` | 模板沙箱创建、暂停/恢复与签名端点 |
| `server/opensandbox_server/services/fast_sandbox/snapshot_runtime.py` | FastPath `full`/`rootfsOnly` 提交与 watch 收敛（497 行） |
| `manifests/charts/fast-sandbox/values.yaml` | `fastpathHTTPPort`、`fastletProxy.resources` 与镜像版本 |
| `docs/architecture/fast-sandbox/` | 架构、调度、网络、检查点、存储、Firecracker 与性能说明 |

### 数据面（Go，`components/execd/`）

| 路径 | 看什么 |
|---|---|
| `components/execd/pkg/runtime/initmode_linux.go` | OSEP-0018 单一 `waitid` reaper、信号转发与 PID 1 退出（801 行） |
| `components/execd/pkg/runtime/hardening_linux.go` | `opensandbox-launcher` 降权链与能力探测（685 行） |
| `components/execd/pkg/runtime/output_limits.go` | 16 MiB UTF-8 边界行切分 |
| `components/execd/pkg/web/controller/command.go` | 前台 SSE、`CommandTimeout` 与后台日志 |
| `components/execd/pkg/web/controller/filesystem_upload.go` | 同目录暂存与原子 `rename` |
| `components/execd/pkg/web/controller/pty_ws.go` | PTY 持有者/观看者模型与子协议鉴权 |
| `components/execd/pkg/telemetry/init.go` | OTLP CPU/内存/磁盘指标导出与 2 秒挂载超时 |
| `components/execd/pkg/isolation/` | bubblewrap 隔离会话 |
| `components/execd/pkg/binding/` | 池化复用时的 per-allocation 原子绑定 |

### 网络、观测与实验性兼容层

| 路径 | 看什么 |
|---|---|
| `components/ingress/pkg/proxy/header.go` | 路由头、续期头、`skip` 哨兵与逐跳头清单 |
| `components/ingress/pkg/proxy/connectivity/tracker.go` | 上游连接分类与 `network-readiness` 影子信号 |
| `components/egress/pkg/policy/policy.go` | 规则模型、端口范围与尾点域名归一化 |
| `components/egress/pkg/policy/always_rules.go` | 平台 `deny.always`/`allow.always` 与端口地板 |
| `components/egress/pkg/credentialvault/` | Vault 存储、Exchange 解析与脱敏 |
| `components/egress/mitmscripts/` | 请求改写、64 头透传上限与路径歧义守卫 |
| `components/egress/fastsandbox_actions.go` | Fast Sandbox subject 状态机、`data-plane-ready` 与排水围栏 |
| `components/nodeagent/pkg/marker/` | 三态 `finalized` 完整性标记 |
| `components/nodeagent/pkg/pipeline/` | 有界队列、背压与 `vm-console` / 日志投递 |
| `components/e2b-shim/` | OSEP-0027 实验性 E2B 兼容骨架（仅配置与探针，暂无业务路由） |

## 文档与契约目录

| 目录 | 当前规模（v1.3.0-rc.2） | 内容 |
|---|---|---|
| `specs/` | 4 份 OpenAPI 契约 | 生命周期（124 KB）、execd（119 KB）、egress（29 KB）、诊断（16 KB） |
| `docs/architecture/` | 分层架构说明 | control-plane、data-plane、network、fast-sandbox |
| `docs/guides/` | 25 篇运维指南 | 含新增 `privilege-reduction.md`、`network-security.md` 等 |
| `oseps/` | 28 份设计提案 | 覆盖 `0001` 至 `0028`（详见附录 B） |
| `sdks/` 与 `cli/` | 5 语言 SDK + MCP + `osb` CLI | 均与 `specs/` 契约保持同步 |
