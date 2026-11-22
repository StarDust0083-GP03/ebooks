# 隔离与加固：从 runc、进程降权到 microVM

安全评审里最常听到的一句空话是「我们已经跑在容器里了」。跑不可信的 AI 生成代码，只靠标准容器命名空间往往挡不住两类最现实的威胁：一类是通过内核漏洞或宿主挂载越权逃出容器；另一类是根本不逃逸，直接在沙箱内部利用残留 capabilities 或共享工作区提权、改写启动脚本或偷走守护进程令牌。

OpenSandbox 把防御拆成四层，并且要求**每一层都能对外如实汇报当前到底处于 `active`、`degraded` 还是 `unsupported`**，不让调用方对着配置文件盲信。

## 纵深防御的四层分工

| 防护层 | 核心机制 | 挡住的威胁 | 开启方式 |
|---|---|---|---|
| 1. 沙箱边界 | 资源限额、安全运行时（runc / gVisor / Kata / Firecracker） | 容器逃逸、越权访问宿主机 | 服务端 `[secure_runtime]` 或 Fast Sandbox 模板 |
| 2. 沙箱内加固地板 | `EXECD_INIT=1` + `opensandbox-launcher`（cap drop、`no_new_privs`、seccomp、可选 Landlock） | 沙箱内提权、读 `/proc/1/environ` 偷令牌、越界写文件系统 | 镜像内置或挂载 TOML（`EXECD_ISOLATION_CONFIG`） |
| 3. 隔离会话 | per-execution bubblewrap 命名空间与受控 bind mount | 同一沙箱内并发或串行任务互相污染 | 每次执行调用 `/v1/isolated/*` |
| 4. eBPF 审计 | 基于沙箱 cgroup 记录 `exec`、`connect` 与权限变更 | 事后取证（仅审计不阻断） | 单独发布的 `execd-ebpf` 变体镜像 |

## 安全运行时选型与硬性不兼容矩阵

服务端通过 `[secure_runtime].type` 全局配置隔离运行时，并在启动阶段由 `validate_secure_runtime_on_startup` 强校验可用性（不可用立刻退出）。上游 `docs/guides/secure-container.md` 给出的开销与能力对照，比单纯背安全等级有用得多：

| 运行时 | 隔离原理 | 启动开销 | 内存开销 | 系统调用 | GPU | systemd / 特权 |
|---|---|---|---|---|---|---|
| `runc`（默认） | Linux 命名空间 + cgroup | ~0 ms | 极小 | 完整 | 支持 | 支持 |
| `gVisor` | 用户态内核拦截 syscall | ~10 到 50 ms | ~50 MB | 部分 | 需额外配置 | systemd 需额外配置 |
| `Kata (QEMU)` | 轻量虚拟机（QEMU） | ~500 ms | ~20 到 50 MB | 完整 | 支持 | 支持 |
| `Kata (CLH)` | Cloud Hypervisor 虚拟机 | ~200 ms | ~10 到 20 MB | 完整 | 支持 | 支持 |
| `Kata (Firecracker)` | Firecracker microVM | ~125 ms | ~5 MB | 受限 | 不支持 | 不支持 |

选型时真正卡脖子的，往往是分散在不同组件里的几条**硬性不兼容**：

- **gVisor 与 egress 包级策略不兼容**。egress 的 iptables 与原生 nftables 重定向路径都依赖 gVisor 未实现的 netfilter 特性；需要安全运行时叠加出向防火墙，必须选 Kata 或 Fast Sandbox。
- **gVisor 与 Kubernetes rootfs 快照不兼容**。Kubernetes 的 rootfs 快照提交器明确拒绝 `gVisor` RuntimeClass。选 gVisor 等于同时放弃 `dns+nft` 出向策略与暂停/恢复。
- **egress 与透明服务网格（Istio / Envoy）冲突**。两者会争抢同一网络命名空间的出向流量改写，必须把沙箱 Pod 排除在 mesh 自动注入之外，或改用 CNI 级平台出向控制。
- **Fast Sandbox 的防火墙执行点不在 guest 内部**。跨 VM 流量在 Pod 视角属于二层桥流量，权威规则装在 pod-netns 的 forward 链上，guest 内部防火墙不构成安全边界。
- **Firecracker 强依赖宿主硬件与文件系统能力**。缺 `/dev/kvm`、`/dev/net/tun`、cgroup v2、XFS reflink 或 10 GiB 磁盘余量，节点自检不通过，不会获得调度标签。

## OSEP-0018 加固地板：让降权覆盖镜像入口进程

在 `1.3.0-rc.1` 之前，即便在镜像里配置了沙箱内降权，由于 `execd` 只是后台进程，用户镜像自己的 `entrypoint` 依然拿着容器上限权限在跑。现在 `runtime.execd_run_as_init = true` 成为默认配置，配合 `docs/guides/privilege-reduction.md` 和仓内预置的 `components/execd/configs/isolation.hardened.toml`：

```toml
[hardening]
enabled = true

[landlock]
enabled = true
```

用户只需把这两行 TOML 烘进镜像（推荐，避免宿主机可写挂载被 UID 0 工作负载篡改）或经 Docker 只读 bind mount / Kubernetes ConfigMap 加请求环境变量投递，设置 `EXECD_ISOLATION_CONFIG=/etc/opensandbox/isolation.toml`，`execd --init` 就会让**包括镜像 entrypoint、`/command`、Shell 会话和 PTY 在内的全部用户代码进程**穿过 `opensandbox-launcher` 的顺序敏感降权链：清掉 execd 凭据环境、裁剪 bounding capabilities、锁死 `no_new_privs`、设定 UID 与保留能力、按需装上 Landlock 文件系统白名单（要求 Linux >= 5.13）、最后加载内置 seccomp 拒绝列表再 `execve`。

设计上有两条非常清醒的边界：

- **配置错即致命，特性缺即降级**。TOML 语法错、capability 拼写错或把保留调用 `execve` 写进 seccomp 拒绝表，会直接令 `execd` 启动退出；但老内核或当前 Fast Sandbox guest 内核缺少 Landlock 时，该层按 fail-open 契约跳过，并通过 `sbx.isolation.capabilities().hardening` 诚实报告 `landlock.state = "unsupported"`，其余层继续生效。如果运维开启了 `[hardening]` 却手动关掉了 `execd_run_as_init`，探针也会诚实上报 `degraded` 并提示存在开关漂移。
- **默认依然关闭加固地板**。隔离边界由平台在容器/VM 层先兜底，进程降权由镜像构建者按需开启，避免一刀切把依赖特殊 syscall 的存量镜像全搞挂。

此外，`1.3.0-rc.1` 在所有运行时统一拦截了含 Windows 反斜杠遍历或前导反斜杠的 `volumeMounts.subPath`，封堵了 Windows 节点的卷根逃逸；生命周期钩子则继续明确定性为平台配置的可信初始化代码，是便利设施而非安全边界。

## 一份诚实的威胁边界表

放到真实安全评审里，这套体系**能挡住的**包括：跨沙箱互访、容器向宿主机逃逸（强度取决于所选运行时）、沙箱内进程关闭或绕开出向防火墙（无 `NET_ADMIN`）、代码从环境或 `/proc/1/environ` 偷取真实外部凭据（Vault 外置 + `non-dumpable`），以及单沙箱内多任务互相污染（隔离会话）。

它**挡不住的**同样必须明说：通过已经被白名单放行的域名往外传数据（egress 是目的地控制，不是 DLP 数据防泄漏）；提示注入诱导 Agent 合法删光自己的工作区；部署刻意启用共享命名空间时的软隔离退化；默认镜像未启用 `execd-ebpf` 时的细粒度内核行为审计缺失；以及平台自己写错的生命周期钩子代码。

## 源码入口

| 路径 | 看什么 |
|---|---|
| `docs/guides/secure-container.md` | 运行时开销、配置与兼容性矩阵 |
| `docs/guides/privilege-reduction.md` | OSEP-0018 加固地板、TOML 模板与能力探测用法 |
| `docs/guides/network-security.md` | 出向策略边界、规则优先级与配置约束 |
| `components/execd/pkg/runtime/hardening_linux.go` | `opensandbox-launcher` 降权链与 `degraded`/`unsupported` 上报 |
| `components/execd/pkg/isolation/` | bubblewrap 隔离会话与路径解析校验 |
| `oseps/0004-secure-container-runtime.md` | 安全容器运行时提案 |
