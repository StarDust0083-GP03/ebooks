# Fast Sandbox：模板、Fastlet 与 Firecracker

前面两篇都以容器为调度单位。想把每个沙箱放进独立 KVM microVM、又要把创建延迟压到百毫秒以内，容器的现拉现启思路就撞墙了：现起一台 Linux 虚拟机加初始化用户态，怎么调优都要秒级；而每台 microVM 外再挂一个独立 egress sidecar，节点内存密度马上就垮掉。

Fast Sandbox 换了一套设计单位：**模板（Template）**。运行时、镜像、入口命令、环境变量和参考机型在创建请求到达之前就已经编成不可变的 golden image；创建请求只表达意图：用哪个模板、归谁、活多久。

## 在 OpenSandbox 里的位置与组件边界

Fast Sandbox 挂载在 `kubernetes` 运行时下，作为 `CompositeSandboxService` 维护的组合分支：创建时看 `templateId` 或 `fsb` 快照归属，创建后看 `fsb-` ID 前缀。

控制协议分两段：OpenSandbox 服务器通过 FastPath v2 gRPC（默认 `:9090`；`1.3.0-rc.2` 支持通过 `controller.fastpathHTTPPort` 额外开启可选的 HTTP/1.1 gRPC-Web 端口，默认 `0` 关闭）提交沙箱与快照意图，FastPath 和 Reconciler 到节点上预热 Pod（Fastlet）则走 JSON over HTTP（`/api/v2/fastlet/*`）。FastPath、Fastlet、CRD 与节点代理的实现位于独立的 `opensandbox-group/fast-sandbox` 仓库，本仓库拥有控制面适配层、路由与共享 egress 接入。

九个核心组件的分工如下：

| 组件 | 核心职责 | 工程落点 |
|---|---|---|
| FastPath | 控制面入口，内存维护候选放置视图 | 常数时间选址，重启后从 CR 重建状态 |
| Reconcilers | 驱动模板构建、池维护、沙箱收敛与快照发布 | 单副本运行，无 leader election |
| `sandbox.fast.io` CRD | 持久化沙箱意图、放置、策略绑定与观测状态 | 意图活过任何控制面进程崩溃 |
| Fastlet | 宿主机上预热的容器 Pod，内部托管多台 microVM | 预置网络槽位，复用共享 egress 与 DNS |
| runtime agent | 节点级 Firecracker 生命周期守护进程（经 Unix socket 控制） | 自检宿主能力后自动给节点打标签 |
| DART | 节点间 4 MiB 块级 P2P 制品分发守护进程 | `cache -> peer -> origin`，摊薄源站带宽 |
| 共享 egress 进程 | 每 Fastlet 一个进程，按源 IP 管理 N 个 subject | 无每沙箱 sidecar 仍坚持 fail-closed |
| 模板构建器 | 把 OCI 镜像与 guest 接线编译成 golden image | 注入 guest init、execd 与 freeze/thaw 能力 |
| janitor | 清理失败操作遗留的孤儿资源 | 负责兜底擦除，收敛负责补齐 |

## 模板双层结构与不可变制品

模板拆成两份状态：服务端数据库（SQLite/PostgreSQL）保存租户私有定义与归属，`SandboxTemplate` CR 驱动远端构建器异步出包。

构建器把普通 OCI 镜像编译成内容寻址的不可变 golden image，过程中会写入 guest init、烘进 `execd` 与 `EXECD_INIT=1` 拓扑、固化业务 `entrypoint` 和字面环境变量，并记录目标 Firecracker VMM 版本。从 `1.2.1` 到 `1.3.0-rc.2`，模板构建链路增加了三项硬约束：

- **强制注入 guest-control 并校验 freeze/thaw**。无论 OCI/OverlayBD 模板还是原生 S3 模板，构建期都会强制注入 guest 控制代理并实测文件系统冻结与解冻，校验不过直接阻断构建；旧版本构建的模板无法原地改元数据凑数，必须重建才能支持 `rootfsOnly` 快照捕获。
- **支持托管多版本 Firecracker（1.16.1 / 1.17.0）**。新构建默认选中宿主机托管目录里安装的最高版本，已有制品恢复时严格按 manifest 记录的精确 VMM 版本启动，宿主机缺失对应二进制时显式失败，绝不静默回退到最新版。
- **OCI 制品要求 SHA-256 固定**，模板构建状态同时暴露分阶段时间戳与耗时（镜像拉取、rootfs 准备、guest 就绪、快照与发布），便于定位慢构建。

因为工作负载在构建期已经定死，创建请求带 `entrypoint`、`env`、`volumes` 或 `credentialProxy` 都会被适配层直接拒绝；更新模板产出新 digest，已运行沙箱继续沿用接纳时的旧版本。正在实现中的 OSEP-0028 准备为 `rootfsOnly` 快照创建开放通过 `resourceLimits` 覆写 CPU 和内存的通道。

## 创建热路径、路由签名与安全加固

一次模板创建从提交到就绪沿着四步推进：服务器校验后经 FastPath 提交 intent；FastPath 按镜像缓存亲和、归一化负载、稳定哈希在内存候选池选出 Fastlet；在选中的 Fastlet 上原子分配预置网络槽位（netns、veth、独立网关 IP）并写全 CR（意图、初始策略、pool 引用）；节点 runtime agent 从 vmstate 检查点拉起 Firecracker microVM。

网络槽位和 CR 都先于 microVM 启动，保证沙箱绝不会出现先通网后补防火墙的时间窗。入向访问由 ingress 的 `FastSandboxProvider` 承接，服务器本地签发绑定命名空间、沙箱 ID 和端口的 `f1.` 路由 scope（`opensandbox-fsb-route-v1`），签发时不调用 FastPath，路由未发布时 ingress 返回 `503` 加 `Retry-After` 而非 `404`，避免向调用方泄漏未就绪与不存在的差别。

`1.2.1` 至 `1.3.0-rc.2` 补齐了四块面向生产的运维与安全边界：

- **FSEP-0003 Fastlet 控制面鉴权落地**。Fastlet 使用 Kubernetes ServiceAccount 令牌访问控制 API，由控制器通过 JWKS 验签并从自身命名空间派生默认调用者白名单，基础 Chart 同步授予受限的 token-minting Role。升级时必须同步升级 Chart 与镜像，否则开启鉴权的 Fastlet 会 fail-closed 拒连。
- **Guest 墙钟恢复校准**。Firecracker microVM 没有硬件 RTC，早期从检查点恢复或崩溃续跑后，guest 内部时钟仍停留在做快照那一刻，直接导致 TLS 证书校验失败、令牌误判过期和定时任务错乱。`1.3.0-rc.2` 在恢复与崩溃恢复后自动校准 guest 墙钟。
- **Fastlet Proxy 资源可配置与单副本硬约束**。`controller.fastletProxy.resources` 允许在 Helm Chart 中自定义 Fastlet Proxy 的 CPU/内存请求与上限，并计入 `ResourceAware` 内存预算；同时 `ResourceAware` 直跑 Firecracker 的 VMM 进程放进有界 cgroup v2 子树，防止单个池内 OOM 波及整个 Fastlet。全功能（`--role=all`）控制器被 Chart 强制锁死单副本并采用 `Recreate` 策略，防止两个控制器并发调和同一个 `SandboxPool`。
- **原生 S3 与 OCI 双路径 `rootfsOnly` 快照**。`1.3.0-rc.2` 把 `rootfsOnly` 公开快照扩展到了原生 S3 模板，同时去掉了手工维护的 `FAST_SANDBOX_ROOTFS_QUALIFICATION` 配置，改为从节点托管 VMM 目录与 StateRoot 自动推导捕获资质；节点不合格时快照直接失败，绝不静默降级生成 `full` 快照。具体对照见第 13 篇。

## 存储、宿主自检与性能数字的真实边界

Fast Sandbox 把存储收束成一种形态：对象存储或 OCI 仓库里的内容寻址制品，加上节点私有的 XFS + reflink CoW 状态根，以及节点间 P2P 分发守护进程 DART。`1.2.1` 为对象存储模板打开了 free-page reporting 以把 guest 释放的内存还给宿主，为 OCI 存储增加了可复用暖 ublk 设备与本地 checkpoint blob 复用。

节点准入是全自动的：`firecracker-runtime` DaemonSet 核验内核 >= 5.10、`/dev/kvm`、`/dev/net/tun`、cgroup v2、XFS reflink 探测（失败即拒不降级）、充足的 `/dev/shm` 以及默认 10 GiB 空闲磁盘和 2 GiB 空闲内存，全部通过才打上 `sandbox.fast.io/kvm=true` 标签接收调度。每台 VM 分配独立的 veth 与独立网关地址，绕开 `arp_ignore=1` 下共享网关丢 ARP 的坑；跨 VM 流量从 Pod 视角属于二层桥流量，因此权威防火墙执行点设在 pod-netns 的 forward 链而非 guest 内部。

上游性能文档记录了 2026-10-08 在 ACK 两节点集群（Firecracker 1.16.1、模板已缓存）的实测：

| 场景 | 成功率 | p50 | p90 | p99 |
|---|---:|---:|---:|---:|
| SDK 创建，串行 100 次 | 100/100 | 64.0 ms | 93.8 ms | 108.3 ms |
| SDK 创建，并发 10 | 100/100 | 102.7 ms | 120.1 ms | 125.4 ms |
| SDK 创建，并发 20 | 51/100 | 136.7 ms | 163.0 ms | 197.0 ms |

引用这组数据时必须带着三个前提：SDK 创建口径覆盖整条请求链直到 guest `/ping` 返回 200（同窗口纯 runtime 创建 p50 为 33.5 到 50.0 ms）；并发 20 一行剔除了 48 次 `429 CapacityRejected` 和 1 次超时，文档明确说明它不能证明 20 路并发可靠；同节点恢复的纯 runtime 耗时中位数虽仅 35.49 ms，但 SDK 端到端恢复中位数仍高达 62.66 秒，瓶颈落在网关、endpoint 生效与客户端重试等待之间，尚未完全收口。

## 源码入口

| 路径 | 看什么 |
|---|---|
| `server/opensandbox_server/services/fast_sandbox/snapshot_runtime.py` | FastPath 快照提交、去掉 OCI 预检与状态 watch |
| `docs/architecture/fast-sandbox/index.md` | 九组件全景与控制/数据解耦原则 |
| `docs/architecture/fast-sandbox/scheduling.md` | 候选排序、Fastlet 接纳与 `failurePolicy` |
| `docs/architecture/fast-sandbox/networking.md` | `f1.` 签名路由与共享 egress subject 生命周期 |
| `docs/architecture/fast-sandbox/checkpoints.md` | `full` 检查点、`rootfsOnly` 与快照发布 |
| `docs/architecture/fast-sandbox/firecracker.md` | 宿主自检地板、每 VM 独立网关与 netfilter 边界 |
| `manifests/charts/fast-sandbox/values.yaml` | `fastpathHTTPPort`、`fastletProxy.resources` 与镜像配置 |
| `oseps/0028-rootfs-only-create-overrides.md` | RootfsOnly 创建期覆写 CPU/内存的设计提案 |
