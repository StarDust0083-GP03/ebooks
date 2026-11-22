# 运维与失败：升级顺序、单活红线与真实边界

评估一个基础设施项目能不能进生产，看功能列表用处不大，真正管用的是看它把哪些升级顺序、中断窗口和未收口限制提前写清楚了。OpenSandbox 的部署与发布文档在这方面非常直白，这一篇把最容易在上线和升级时翻车的约束集中理一遍。

## 集群基线与不可颠倒的安装顺序

从 `release-1.3.0-rc.2` 起，所有子 Chart（`base`、`controller`、`fast-sandbox` 等）把 Kubernetes 最低版本门槛统一上调至 **`>=1.22.0-0`**（官方支持范围 `v1.22` 至 `v1.34`），Helm 会直接拒绝在 1.21 集群上安装。

手工分步安装必须遵守依赖顺序：

```text
base -> opensandbox-controller -> fast-sandbox（可选） -> ingress-gateway -> opensandbox-server -> 可选组件（node-agent / console）
```

1. **`base`**：安装 `sandbox.opensandbox.io` 与 `sandbox.fast.io` 的 CRD 与基础 RBAC（含 `1.3.0-rc.2` 为 FSEP-0003 Fastlet 控制面鉴权新增的 token-minting Role 与匿名 issuer 发现权限），每集群装一次。
2. **`opensandbox-controller`**：运行 `BatchSandbox`、`Pool`、`SandboxSnapshot` 三个 reconciler，默认开启 leader election（`1.2.1` 起支持自定义选举时长且强制 `lease > renew > 1.2 * retry`，并支持命名空间级缓存与 `controller.extraArgs`）。
3. **`fast-sandbox`（可选）**：必须在 server 之前就位，因为 server 启动时就要连向 FastPath gRPC（`:9090`）并 watch `sandbox.fast.io` 对象。
4. **`ingress-gateway`**：Kubernetes 生产部署的**必需组件**。沙箱 Pod 只有内网 ClusterIP，跳过网关会导致沙箱能建起来却谁也访问不到。
5. **`opensandbox-server`**：生命周期控制面，最后启动确保依赖的 CRD、网关地址与 FastPath 均已可达。

使用顶层 umbrella chart（`manifests/charts/opensandbox`）时，子 chart 打包目录被 git 忽略，每次渲染前需先执行 `helm dependency build manifests/charts/opensandbox`。发布镜像同步推送至 Docker Hub、GHCR 与阿里云 ACR，均带 Cosign 无密钥签名与 provenance 证明；生产环境应按镜像 digest 固定，不要依赖早已冻结的旧 `server/v*` 标签。

## 升级至 v1.3.0 的七条必查清单

从 `1.2.x` 滚动到 `1.3.0` 系列，下面几处兼容性与顺序约束一旦踩空就会直接报错或触发异常行为：

1. **BatchSandbox `Completed` 升级顺序**：必须先应用新 CRD 再升级控制器，否则旧 CRD 会以校验错误拒绝控制器写入 `Completed` 相位（Helm 一体安装已自动处理）。
2. **`execd-as-init` 成为默认**：`runtime.execd_run_as_init` 现默认为 `true`。依赖经典后台拓扑的存量容器需显式改回 `false`；Fast Sandbox 黄金镜像保留构建时烘进去的拓扑直到重建。
3. **旧 DevOps 路由与 CLI 移除**：`diagnostics/inspect`、`diagnostics/summary`、无 `scope` 参数调用以及 `osb devops` 全部下线，遗漏 `scope` 现在返回 `422`；调用脚本需切换至 `osb diagnostics` 与 `osb sandbox get`。
4. **原生 S3 与 OCI 模板须重建才能做 `rootfsOnly` 快照**：新版构建器强制注入 guest-control 并校验 freeze/thaw，旧模板捕获 `rootfsOnly` 会 fail-closed 失败。
5. **Fast Sandbox Chart 与伴随镜像同步升级**：FSEP-0003 鉴权下，缺基础 Chart 新 RBAC 的 Fastlet 会拒连控制面；同时全功能 `--role=all` 控制器在 `1.3.0-rc.2` 被 Chart 强制单副本并改用 `Recreate` 升级策略（`replicaCount > 1` 直接渲染报错），升级时会有短暂 FastPath 变更中断；如需为 Fastlet Proxy 调优资源，可通过 `controller.fastletProxy.resources` 配置并同步计入 `ResourceAware` 内存预算。
6. **保留旧版托管 Firecracker 二进制**：托管 VMM 目录升级到 1.17.0 后，不要删除或改名 1.16.1 二进制；已有检查点与快照恢复时严格按 manifest 记录的精确版本拉起，缺版本直接失败。
7. **规则与错误名变化**：egress 带根尾点的规则（如 `pypi.org.`）现在真正生效，升级前需复核默认放行策略下的尾点拒绝规则；`execd` 前台超时错误名从 `CommandExecError` 改为 `CommandTimeout`；空字符串重命名返回 `400`。

## 单活红线、故障矩阵与三笔容量账

生命周期服务器默认 `replicaCount: 1` 加 `Recreate` 升级策略。多副本唯一受支持的拓扑是 **PostgreSQL + Kubernetes 运行时**，且仅用于协调公开快照创建，不等于通用控制面高可用；SQLite 和 Docker 快照执行不支持多活。

Fast Sandbox 控制面（FastPath + Reconcilers）同样坚持单副本无 leader election：控制面或服务器重启时，运行中的 microVM 与数据面流量不受影响，未完成意图由 CR 重放补齐；唯一会造成运行现场丢失的场景是 Fastlet Pod 意外消失，默认在 `recoveryTimeoutSeconds`（60 秒）后交由人工按 `failurePolicy` 处置，不做自动跨机盲目迁移。

运维预算里有三笔容易漏算的账：

- **Kubernetes 快照仓库凭据配在控制器侧**：镜像仓库地址模板与推仓 Secret 配置在 `opensandbox-controller` 启动参数里，不在服务器 `~/.sandbox.toml` 中；而且删除快照记录绝不删除远端镜像或对象存储文件，必须外挂独立的生命周期清理策略。
- **Fast Sandbox 节点 XFS reflink 状态盘**：状态根必须落在 `mkfs.xfs -m reflink=1` 分区上，按「已缓存模板 rootfs + guest 内存快照 + 活跃沙箱可写层」核算后起步留 50% 余量，并维持高于 10 GiB 的空闲磁盘地板。给已跑沙箱的节点新挂状态盘前，必须先排空该节点并重启 runtime Pod。
- **日志轮转与观测资源基线**：Fast Sandbox 后端组件日志自 `1.2.1` 起默认写 `/var/log/fast-sandbox` 轮转文件，不再直接吐到 `kubectl logs`（除非显式开启 stderr 镜像）；Node Agent 的 Chart 资源值目前仍是起点，尚未完成大规模 soak 定型。

## 排障顺序

线上一旦告警，按证据成本从低到高走六步最快定位现场：

1. 查 `GET /v1/sandboxes/{id}` 的 `state`、`reason`、`message`，先分清没建起、已正常完成（`SANDBOX_COMPLETED`）还是异常失败；
2. 查底层对象状态：Kubernetes 看 `kubectl describe batchsandbox` 的 Conditions（尤其 `PoolAllocationPending`、`PauseFailed`、`ResumeFailed`）并核对 `observedGeneration`；Fast Sandbox 看 `sandbox.fast.io` CR 的 `status.runtime`；
3. 查 `GET /v1/sandboxes/{id}/endpoints/{port}` 返回的地址形态与请求头，确认恢复后是否重新解析；
4. 调数据面 `/ping` 验证连通性，不行再拉取 `diagnostics/logs?scope=container` 和 `diagnostics/events?scope=runtime`，先核对 `truncated` 与 `warnings`；
5. 看指标与结构化日志：egress 区分策略拒绝与解析器故障，上游 TLS 失败看固定原因码（如 `self_signed`）；ingress 看 `/status.ok/network-readiness` 影子信号；
6. 对照已知边界清单：池满 `429`、并发 20 过载、K8s 恢复后 Vault 需重注、`DELETE 204` 与 `404` 不代表进程已终止。

## 源码入口

| 路径 | 看什么 |
|---|---|
| `docs/deployment/index.md` | 组件安装依赖顺序、单副本声明与 PostgreSQL 快照拓扑 |
| `manifests/charts/base/Chart.yaml` | `kubeVersion: ">=1.22.0-0"` 与基础 CRD/RBAC 定义 |
| `manifests/charts/controller/values.yaml` | leader election、Pod 恢复与快照仓库参数 |
| `manifests/charts/server/values.yaml` | 单副本 `Recreate` 默认策略 |
| `manifests/charts/fast-sandbox/templates/controller-deployment.yaml` | 单副本校验、`fastletProxy.resources` 与 `fastpathHTTPPort` 参数装配 |
| `docs/community/release-verification.md` | Cosign 无密钥签名与发布镜像校验步骤 |
