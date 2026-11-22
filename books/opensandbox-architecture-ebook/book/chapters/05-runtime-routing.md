# 运行时路由：一个接口装下三种后端

上一篇定下了规矩：API 层不碰任何运行时细节。可真正落到代码里，Docker 操作的是本地容器、Kubernetes 操作的是自定义资源和预热 Pod、Fast Sandbox 操作的是远端 FastPath gRPC 服务。三种完全异构的底层，怎么放进同一个抽象类里而不把接口撑散？

答案在 `server/opensandbox_server/services/` 下的四份文件里：`sandbox_service.py`、`factory.py`、`composite_service.py` 和 `k8s/workload_provider.py`。

## `SandboxService`：按调用方动作组织的抽象接口

翻开 `sandbox_service.py`（345 行），随着 `1.3.0-rc.1` 彻底移除旧的 `devops` 路由与 `get_sandbox_inspect`，抽象接口的方法分布变得更加干净：

```text
生命周期（8 个）：create_sandbox / list_sandboxes / get_sandbox / delete_sandbox
                pause_sandbox / resume_sandbox / renew_expiration / patch_sandbox_metadata
访问入口（1 个）：get_endpoint
诊断通道（5 个）：get_sandbox_diagnostic_context / get_sandbox_log_diagnostics /
                get_sandbox_event_diagnostics / get_sandbox_logs / get_sandbox_events
```

此外还有两个默认可空实现的成员 `set_tenant_provider` 和 `close`，加上一组共用辅助方法（UUID4 沙箱 ID 生成、端口范围校验、保留元数据 patch 校验与合并）。

接口完全按调用方能发起的业务动作组织，而不是按某个后端恰好有的命令来切。`set_tenant_provider` 留成空实现而非抽象方法，说明抽象类能统一方法签名，但没法抹平依赖差异，Docker 后端本就不需要租户提供方。

## 工厂函数：66 行决定运行态装配

`services/factory.py` 只有 66 行，逻辑没有弯绕：

```python
implementations = {
    "docker": DockerSandboxService,
    "kubernetes": KubernetesSandboxService,
}
if selected_type not in implementations:
    raise ValueError(...)
if selected_type == "kubernetes":
    implementation = KubernetesSandboxService(config=active_config)
    return CompositeSandboxService(
        implementation,
        FastSandboxService(active_config, k8s_client=implementation.k8s_client),
    )
return implementations[selected_type](config=active_config)
```

这里藏着三个刻意的工程决定：配置项 `runtime.type` 只认 `docker` 和 `kubernetes`，Fast Sandbox 不另设配置枚举；`FastSandboxService` 直接复用 `KubernetesSandboxService` 初始化好的 `k8s_client`，避免维护两套集群连接；遇到不认识的 `runtime.type` 立刻抛错退出，绝不降级成默认 Docker。

## 组合路由器：无状态前缀分发

`CompositeSandboxService`（184 行）把容器分支和模板分支包成一个 `SandboxService`，路由逻辑浓缩在两段判断里：

```python
def _backend(self, sandbox_id: str) -> KubernetesSandboxService | FastSandboxService:
    return self._fsb if sandbox_id.startswith("fsb-") else self._kubernetes

async def create_sandbox(self, request: CreateSandboxRequest) -> CreateSandboxResponse:
    if (request.template_id or "").strip():
        return await self._fsb.create_sandbox(request)
    if (request.snapshot_id or "").strip() and request.resolved_snapshot_backend == "fsb":
        return await self._fsb.create_sandbox(request)
    return await self._kubernetes.create_sandbox(request)
```

创建时看意图来源：传了 `templateId`，或者引用了 `fsb` 后端产出的快照，就走 `FastSandboxService`，其余走 `KubernetesSandboxService`。创建完成后，后续一切查询、暂停、续期、删除、查 endpoint 只看 ID 前缀：以 `fsb-` 开头就走模板后端，否则走容器后端。

这套做法最大的好处是**零状态**：不需要维护一张 `sandbox_id -> backend` 的数据库映射表，控制面崩溃重启后路由信息依然完好无损地写在 ID 里。代价也很明确：前缀成了硬编码的隐式契约，任何后端一旦漏加或误加 `fsb-` 前缀，请求不会立刻报错而是静默落进错误分支；如果未来要再接入第三种并行后端，单靠前缀就撑不住了。

## 快照跨后端路由：让数据自己说话

按 `snapshotId` 创建沙箱时，控制面不会按当前默认后端瞎猜，而是在路由前由 `services/snapshot_restore.py` 查询持久化快照表，从 `restore_config.backend` 读出该快照究竟由谁产出，并挂到请求的 `resolved_snapshot_backend` 上。

Docker 快照是本机 OCI 镜像，Kubernetes 快照是推到远端仓库的镜像，Fast Sandbox 快照是对象存储或 OverlayBD 里的内容寻址制品集（含 `full` 与 `rootfsOnly` 两种模式）。让快照记录自带归属元数据，就能准确把恢复请求送回唯一能读懂它的后端。

## 新增的 `WorkloadProvider` SPI：把 Pool 路由从硬编码里解耦

在 Kubernetes 容器分支内部，还藏着更细的一层抽象：`kubernetes.workload_provider` 可以选默认的自有 CRD `batchsandbox`，也可以选社区的 `agent-sandbox`（`kubernetes-sigs/agent-sandbox`）。

早期版本里，`/v1/pools` 路由和池分配逻辑直接写死了对 `pools.sandbox.opensandbox.io` CRD 的查询，导致集群选用非 BatchSandbox 的工作负载 provider 时会撞上一堆硬编码报错。`1.3.0-rc.2` 把池管理与分配彻底移到 `services/k8s/workload_provider.py`（446 行）的 `WorkloadProvider` SPI 之后：

- `BatchSandboxProvider` 覆盖 `create_pool_service`，正常提供池 CRUD 与缓冲容量管理；
- 未实现池服务的 provider 沿用基类默认行为，`api/pool.py` 懒创建时直接返回清晰的 `501 KUBERNETES::POOL_NOT_SUPPORTED`，且整进程共享同一份 `PoolService` 和 `K8sClient`，不再像旧版那样按请求反复重写进程全局的 kube 配置。

## 三种后端的能力对照与失败约定

同一个接口下，各后端动作和不支持时的失败方式保持诚实区分：

| 操作 | Docker | Kubernetes 容器 | Fast Sandbox |
|---|---|---|---|
| `create` | 拉镜像、建容器、注入 execd、分配宿主端口 | 建 BatchSandbox 或从 Pool 认领、watch 等就绪 | FastPath gRPC 提交 intent，CR 收敛 |
| `pause` | 冻结主容器与 sidecar（保留内存） | commit rootfs 镜像并推仓、释放 Pod | 写完整检查点落 store、释放 Fastlet |
| `resume` | 先解冻 sidecar、再解冻主容器 | 改写模板镜像、重建 Pod | 任意合格 Fastlet 恢复检查点并校准时钟 |
| `create_snapshot` | 仅 `full`（支持运行中或暂停态）；`rootfsOnly` 返 `409` | 仅 `full`；`rootfsOnly` 返 `409` | `full` 与 `rootfsOnly`（原生 S3 与 OCI 均支持） |
| `diagnostics` | 日志与事件均支持（必填 `scope`） | 日志与事件均支持（必填 `scope`） | 仅支持 `runtime` 事件，日志未实现（`501`） |
| `pools` | 不支持 | BatchSandbox 支持；其他 provider 返 `501` | 由 fast-sandbox 独立池管理 |

此外，安全运行时配置也在 `services/runtime_resolver.py` 完成跨后端翻译：

```python
DEFAULT_DOCKER_RUNTIMES = {"gvisor": "runsc", "kata": "kata-runtime"}
DEFAULT_K8S_RUNTIME_CLASSES = {"gvisor": "gvisor", "kata": "kata-qemu", "firecracker": "kata-fc"}
```

同样一行 `[secure_runtime] type = "kata"`，在 Docker 侧变成 `--runtime kata-runtime`，在 Kubernetes 侧变成 `runtimeClassName: kata-qemu`，调用方无需感知底层名词差异。

## 源码入口

| 路径 | 看什么 |
|---|---|
| `server/opensandbox_server/services/sandbox_service.py` | 抽象方法定义与共用元数据校验逻辑 |
| `server/opensandbox_server/services/factory.py` | 配置到后端实例的装配入口 |
| `server/opensandbox_server/services/composite_service.py` | `fsb-` 前缀分发与快照后端分流 |
| `server/opensandbox_server/services/k8s/workload_provider.py` | `WorkloadProvider` SPI 与池服务扩展点 |
| `server/opensandbox_server/api/pool.py` | `501 KUBERNETES::POOL_NOT_SUPPORTED` 与共享 `PoolService` |
| `server/opensandbox_server/services/runtime_resolver.py` | 安全运行时的跨后端映射与启动自检 |
| `server/opensandbox_server/services/snapshot_restore.py` | 按快照元数据反查归属后端 |
