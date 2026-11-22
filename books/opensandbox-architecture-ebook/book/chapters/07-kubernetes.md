# Kubernetes 后端：BatchSandbox、预热池与 Completed 终态

在 Kubernetes 集群里直接按请求新建 Pod，创建延迟的大头不在业务代码，而在调度链路本身：写 API Server、调度器选节点、kubelet 拉镜像、初始化容器、等待探针就绪。哪怕一切顺利，拉起几十个沙箱也动辄几十秒。

更隐蔽的坑出在状态收敛上。一个跑完脚本以退出码 0 正常退出的沙箱，如果控制器只认 Ready Pod 数量，就会把 `status.phase` 从 `Succeed` 退回 `Pending`，让所有调用方误以为沙箱还在运行直到 TTL 过期被删；或者陈旧的 informer 缓存刚好在续期成功后发起一次到期删除，把刚延寿的沙箱静默抹掉。

OpenSandbox 的 Kubernetes 后端由三个 CRD 控制器与服务端 provider 配合运转。这一篇拆解它的池化容量模型，以及 `1.2.1` 至 `1.3.0-rc.2` 里补齐的终态机与并发防线。

## 三个 CRD 与注解通信

`kubernetes/` 下的 operator 围绕三个自定义资源运转，彼此不走私下 RPC，全靠对象注解和 Pod 内 task-executor 协作：

| CRD | 职责 | 关键字段 |
|---|---|---|
| `BatchSandbox` | 描述单个或多个同构沙箱实例 | `replicas`、`template`、`poolRef`、`expireTime`、`pause`、`taskTemplate` |
| `Pool` | 维护可瞬间认领的预热 Pod 缓冲池 | `template`、`capacitySpec`、`scaleStrategy`、`recycleStrategy` |
| `SandboxSnapshot` | 记录暂停/恢复与公开快照的镜像提交过程 | 目标 `BatchSandbox` 名、提交后的镜像 digest |

Pool reconciler 把暖 Pod 认领给 `BatchSandbox` 后，把结果写进 `sandbox.opensandbox.io/alloc-status` 和 `sandbox.opensandbox.io/endpoints` 注解，并给 Pod 打上 `pool-name` 与 `pool-revision` 标签；BatchSandbox reconciler 里的 `TaskScheduler` 再把具体命令作为 task 下发给 Pod 内的 task-executor。用注解传状态虽然缺强 schema 约束，但运维直接敲 `kubectl describe` 就能看到完整现场。

## 两级容量模型与常数时间认领

Pool 把容量切成两个嵌套区间：

```text
poolMin ─────────────── poolMax        总池规模（含正在启动与已分配的 Pod）
        bufferMin ─── bufferMax        可用缓冲区（仅限 Running + Ready 且未分配的 Pod）
```

控制器持续把可用缓冲区维持在 `[bufferMin, bufferMax]` 之间。还在拉镜像或未就绪的 Pod 计入总池，绝不计入可用缓冲区，因此池子不会承诺自己交不出来的容量。每次扩缩受 `maxUnavailable`（默认 25%）节流，防止突发流量引发 Pod 雪崩。从 `1.3.0-rc.1` 起，服务端校验直接以 `422` 拒绝 `bufferMin > bufferMax` 的非法规格，彻底堵住了控制器来回建删 Pod 的震荡死循环。

认领一个池 Pod 是常数时间写注解操作，不经过 kube-scheduler。归还时的清理代价由 `recycleStrategy` 决定：

| 回收策略 | 动作 | 风险与权限依赖 |
|---|---|---|
| `Delete`（默认） | 直接销毁旧 Pod、补建干净新 Pod | 最干净，重建成本由后台缓冲吸收 |
| `Restart` | 原地重启容器进程 | 省掉调度开销；需 `pods/exec: create` RBAC（`1.3.0-rc.1` 修复了 Helm Chart 遗漏该权限导致重启回收 403 的缺陷） |
| `Noop` | 原样归还 | 最快，但文件系统与进程状态会泄漏给下一次分配，仅限沙箱自清理场景 |

池化换来毫秒级分配的同时，也锁死了三条 API 约束：请求里的 `entrypoint` 和 `env` 改走分配后的 task 下发，不会改写 Pod spec；`volumes` 与 `networkPolicy` 不能和 `poolRef` 同用，必须提前配进 Pool 模板；池模板 Pod 必须内置 task-executor、bootstrap 与 `execd`。

当可用缓冲区耗尽时，控制器在 `BatchSandbox` 上写入 `PoolAllocationPending` 条件（reason `PoolCapacityExhausted`）并每 5 秒重试；服务端通过 watch 在 `pool_acquisition_timeout_seconds`（默认 30 秒）窗口内有限等待，超时后返回可重试的 `429`，既不无限排队也不盲目重读 API。

## 粘滞 `Completed` 终态与并发防线

`1.3.0-rc.2` 在控制器和服务端同步落地了三项关键状态机加固：

**1. 粘滞 `Completed` 终态终结状态悬空**。过去 `BatchSandbox` 的 `status.phase` 只有 `Pending`、`Succeed`（代表至少一个 Pod Running+Ready，属于稳态运行）、`Pausing`/`Paused`/`Resuming` 和 `Failed`。一旦主容器以退出码 0 正常退出，Pod 不再 Ready，控制器就把相位退回 `Pending`，导致上层一直把已结束的沙箱当成运行中直到 `expireTime` 删除对象。

现在 CRD 新增了粘滞的 `Completed` 终态。主容器正常退出后，`BatchSandbox` 永久锁定在 `Completed`，控制器跳过 Pod 替换与任务调度，拒绝任何后续 pause/resume 意图并挂出 `SandboxCompleted` 条件；服务端 `batchsandbox_provider.py` 将 `Completed` 直接映射为公开状态 `Terminated`、原因码 `SANDBOX_COMPLETED`，附带退出详情与完成时间，对已完成沙箱调用暂停或恢复直接报错拒绝。升级集群时必须**先应用新 CRD 再升级控制器**，否则旧 CRD 会拒绝写入 `Completed` 相位。

**2. 状态写入带 `resourceVersion` 前置条件**。控制器更新 `BatchSandbox` 状态时携带 `resourceVersion` 乐观锁，防止陈旧的运行时视图覆盖刚写下的 `PauseFailed` 或 `ResumeFailed` 条件。此前这两个条件一旦被覆盖，服务端的暂停/恢复重试路径就会静默失效。

**3. 到期删除带 UID 与 `resourceVersion` 校验，且删除时清净内存态**。服务端执行到期删除时携带对象 UID 与 `resourceVersion` 前置条件，陈旧 informer 副本再也不可能误删刚完成续期的沙箱；同时控制器在 `BatchSandbox` 删除或同名替换时，会彻底遗忘该对象的全部内存状态（扩缩预期、Pod 恢复预算与任务调度器），避免新沙箱继承前任残留；控制器重启时也会先恢复池分配记录再执行驱逐（`1.2.1`），保护在跑沙箱 Pod 不被误杀。

## 暂停、恢复与持久化红线

Kubernetes 容器后端的暂停走镜像提交路径：把单副本（`spec.replicas=1`）沙箱的容器 rootfs 通过源节点上的 commit Job 打包成 OCI 镜像推送到配置好的 registry，确认每个容器都回报了完整镜像 digest 并将内部 `SandboxSnapshot` 置为 `Succeed` 后，再释放 Pod 与池槽位。

如果 commit Job 丢失或结果不完整（`CommitResultUnavailable` / `CommitResultIncomplete`），`1.2.1` 起控制器会明确保留原运行时 Pod，向 `BatchSandbox` 写入 `PauseFailed` 条件供调用方重试，绝不删掉唯一可恢复的运行现场。恢复时复用同一个 `BatchSandbox` 对象，把模板镜像替换成最新快照镜像并重建 Pod；哪怕旧 Pod 终止略晚于 `Paused`，控制器也会等旧 Pod 彻底消失再拉新 Pod。

容器后端的公开快照依然只支持 `mode="full"`（推送 OCI 镜像到 registry，不含内存），收到 `mode="rootfsOnly"` 会在服务端预检阶段直接返回 `409`。另外，所有 Helm Chart 在 `1.3.0-rc.2` 将 Kubernetes 最低版本门槛上调至 `>=1.22.0-0`（支持范围 `v1.22` 至 `v1.34`）。

## 怎么读池化性能对比数字

上游 `operator.md` 列出了一组交付 100 个沙箱的总耗时对照：`agent-sandbox` 在并发 1 / 10 / 50 下分别耗时 76.4 s、23.2 s、33.9 s，而启用预热池的 `BatchSandbox` 总耗时 0.92 s。

这组数字量的是**预热认领与现建调度**之间的两个数量级落差，而不是两个社区项目的绝对优劣，因为 `agent-sandbox` 走的是标准现建 Pod 路径，而它自身也具备独立的池化机制。

## 源码入口

| 路径 | 看什么 |
|---|---|
| `kubernetes/apis/sandbox/v1alpha1/batchsandbox_types.go` | `BatchSandbox` 规格、状态与 `Completed` 相位定义 |
| `kubernetes/apis/sandbox/v1alpha1/pool_types.go` | 两级容量字段与回收策略枚举 |
| `kubernetes/apis/sandbox/v1alpha1/sandboxsnapshot_types.go` | 内部与公开快照 CRD 结构 |
| `server/opensandbox_server/services/k8s/batchsandbox_provider.py` | `Completed -> Terminated (SANDBOX_COMPLETED)` 映射、pause/resume 校验与 `EXECD_INIT=1` 注入 |
| `server/opensandbox_server/api/pool.py` | `bufferMin > bufferMax` 422 校验与 `WorkloadProvider` SPI 委派 |
| `docs/architecture/control-plane/operator.md` | 容量模型、回收策略与状态条件解读 |
| `docs/getting-started/configuration.md` | 创建等待超时与池耗尽等待窗口配置 |
