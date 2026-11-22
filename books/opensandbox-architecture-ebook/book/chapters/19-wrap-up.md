# 完结篇：跟着一次请求走完全书

把前面十八篇拆开的零件重新装回一条时间线，回到第 1 篇那个修 bug 的编码 Agent。它要完成一次真实任务：申请沙箱、跑单元测试、按白名单装一个依赖、改代码复测、视情况留存快照或拿走 diff，最后销毁现场。

## 第 0 毫秒：提交创建意图

客户端携带 `OPEN-SANDBOX-API-KEY` 发出 `POST /v1/sandboxes`：

```json
{
  "image": { "uri": "python:3.12" },
  "entrypoint": ["sleep", "infinity"],
  "resourceLimits": { "cpu": "2", "memory": "2Gi" },
  "timeout": 1800,
  "networkPolicy": {
    "defaultAction": "deny",
    "egress": [
      {"action": "allow", "target": "pypi.org"},
      {"action": "allow", "target": "*.github.com"}
    ]
  },
  "credentialProxy": { "enabled": true },
  "lifecycle": {
    "idlePolicy": { "idleTimeoutSeconds": 600, "onIdle": "pause", "wakeOnAccess": true }
  },
  "metadata": { "task": "fix-issue-418" }
}
```

如果这是一次面向 Fast Sandbox 模板的极速任务，请求体里则换成 `templateId`，去掉 `entrypoint`、`volumes` 与 `credentialProxy`，负载定义全部留在不可变 golden image 里。

## 控制面校验与路由分流

请求先过 `RequestIdMiddleware` 挂上 `X-Request-ID`，再进 `AuthMiddleware`：单租户比对 `server.api_key`，多租户经 `TenantProvider` 解析租户命名空间写入 `ContextVar`（第 14 篇）。

`api/lifecycle.py` 走完 `validators.py` 的集中校验（封闭对象拒接未知键、`subPath` 防遍历、`extensions` 与续期策略防冲突、`networkPolicy` 动作归一化）后，把请求交给 `SandboxService`（第 4 篇）。在 `runtime.type = "kubernetes"` 下，`CompositeSandboxService` 看到没有 `templateId` 也没有 `fsb` 快照来源，把请求路由给 `KubernetesSandboxService`（第 5 篇）。

## 容量认领与默认 `EXECD_INIT=1` 启动

服务端创建 `BatchSandbox` 对象，写进绝对过期时间 `expireTime`、资源上限、egress sidecar 注入以及 `EXECD_INIT=1` 环境变量（`runtime.execd_run_as_init = true` 默认生效）。

若命中预热 `Pool`，Pool reconciler 在常数时间内把一个已处于 Running+Ready 的暖 Pod 标注给该 `BatchSandbox`，并由 `TaskScheduler` 把入口命令作为 task 下发；池缓冲区打空时写 `PoolAllocationPending`，服务端在有限窗口内等不到即返可重试的 `429`（第 7 篇）。服务端借助 watch 等到工作负载就绪后返回响应。

此时沙箱内部同步到位三件事：

- `bootstrap.sh` 执行 `exec execd --init -- <entrypoint>`，由 `execd` 担任容器 PID 1 统一回收孤儿进程、透传信号与退出码；若镜像配置了 `EXECD_ISOLATION_CONFIG`，`opensandbox-launcher` 将降权地板覆盖到入口进程与所有后续命令（第 9、15 篇）；
- 池化复用场景下，`execd` 原子应用 per-allocation 绑定（环境、令牌、沙箱 ID），完成前业务端点一律返 `503`（第 9 篇）；
- 仅 egress sidecar 持有 `NET_ADMIN`，装好 DNS 代理与 `dns+nft` 默认拒绝规则（第 11 篇）。

## 解析入口、流式执行与无密钥鉴权

客户端调用 `GET /v1/sandboxes/{id}/endpoints/44772`，拿回网关地址与必需请求头（开启 `secureAccess` 时同时携带访问凭据），先调 `/ping` 确认数据面就绪，再经 `ingress`（HTTP/1.1 或 WebSocket 一等路径）直连 `execd`（第 10 篇）。

执行单测走 `POST /command`。`execd` 通过 SSE 持续吐出原始行尾的 `stdout`/`stderr`（单行超 16 MiB 按 UTF-8 边界分段不炸内存），正常结束回 `execution_complete` 与 `exit_code`，前台超时明确报 `CommandTimeout`，断流则视为结果不确定（第 9 篇）。

随后 Agent 运行 `pip install` 或访问私有制品库：DNS 代理按归一化域名放行 `pypi.org` 并授予 nftables 有限 IP 租约；涉及内部认证接口时，沙箱里只留假密钥，透明 MITM 在沙箱外通过本地 Vault 私有 socket 注入认证头，或经 OSEP-0025 Exchange resolver 透传受限出向头（<=64 头、<=8 KiB）按请求实时重签，密钥明文从不落盘也不进容器；若代码试图探测 `169.254.169.254`，平台 `deny.always` 永远压过租户策略直接拦截（第 11、12 篇）。

## 中途休眠、收尾交付与故障索引

任务中途如果连续 10 分钟无访问，空闲暂停扫描器自动挂起沙箱释放算力；新请求到来时网关按统一唤醒预算自动拉起并要求客户端重查 endpoint（若是 Kubernetes 容器后端，可信控制面还要在恢复后重新注入内存 Vault 凭据）。期间正常业务流量则经 `renew-intent` 推动绝对 TTL 延寿，Docker 侧有每沙箱生命周期锁防续期与过期删容器撞车，Kubernetes 侧到期删除带 UID 与 `resourceVersion` 校验（第 6、7、13 篇）。

任务结束按需求走三条收尾路：

- **自然退出**：在 BatchSandbox 上主容器以 exit 0 结束，进入粘滞 `Completed` 终态，对外报 `Terminated`（`SANDBOX_COMPLETED`）并拒绝 pause/resume，不再跌回 `Pending`；
- **留存起点**：调用 `POST /v1/sandboxes/{id}/snapshots`，Docker/Kubernetes 走 `full` 提交 OCI 镜像，Fast Sandbox 可按需选择 `full`（带内存快速恢复）或 `rootfsOnly`（覆盖原生 S3 与 OCI 的 KB 级冷启动制品）；
- **直接销毁**：调 `DELETE` 拿回 `204`（只表示接受删除），后续 `GET` 先看 `Stopping` 再变 `404`（不等于确认进程终止）。若开启了 Node Agent，短命沙箱的容器日志或 `vm-console` 日志连同三态 `finalized` 完整性标记早已落进对象存储（第 13、16 篇）。

出问题时，用下面这张索引表能直接跳到第一现场：

| 现场症状 | 第一排查点 | 对应章节 |
|---|---|---|
| 创建返回 `429` | `BatchSandbox` 的 `PoolAllocationPending` / `PoolCapacityExhausted` | 第 7 篇 |
| 跑完脚本却长期显示运行或拒接暂停 | 核对 CRD 是否已升级并支持 `Completed` / `SANDBOX_COMPLETED` | 第 7、13 篇 |
| 命令流无声中断或超时报错名变更 | 检查是否收到 `execution_complete` 或新的 `CommandTimeout` | 第 9 篇 |
| 装依赖或上游 HTTPS 报 `502` / 不通 | 查 egress 策略拒绝与 DNS 解析失败计数、尾点规则、TLS 固定原因码 | 第 11 篇 |
| 凭据注入报 `403` / `407` / `503` | 检查路径歧义守卫、Exchange 64 头上限、Vault socket 存活或 K8s 恢复后重载 | 第 12 篇 |
| `rootfsOnly` 快照报 `409` 或失败 | 容器后端不支持（`409`）；FSB 检查模板是否已重建、节点资质是否合格 | 第 8、13 篇 |
| `diagnostics/logs` 返回 `422` | `1.3.0` 强制要求 `scope` 参数（如 `container`/`all`），旧 `devops` 路由已删 | 第 16 篇 |
| 沙箱已删找不到历史输出 | 查 Node Agent 对象存储目录下的 `sandbox.finalized.<rev>.json` 标记 | 第 16 篇 |

## 如果要动手改源码

- **新增一种运行时后端**：实现 `SandboxService` 抽象方法、约定唯一 ID 路由规则、在 `factory.py` 或 `CompositeSandboxService` 注册，并把能力差异写进 `docs/api/implementation-compatibility.md`（第 5 篇）。
- **调整出向策略或凭据注入**：从 `components/egress/pkg/policy/` 与 `mitmscripts/` 切入，守住 `deny.always -> allow.always -> ports.cap -> 用户策略` 优先级与 fail-closed 底线（第 11、12 篇）。
- **扩展沙箱内执行能力**：先确认该能力落在 `execd` 哪一层（命令、文件、PTY 还是 bubblewrap 隔离会话），核对 `EXECD_INIT=1` 进程树回收、`opensandbox-launcher` 降权以及预热池复用时的 per-allocation 绑定重置（第 9、15 篇）。

把一条 `POST /v1/sandboxes` 走到头，最值得带走的是贯穿全仓的工程习惯：**在能保证的地方写死契约、锁和启动断言，在不能保证的地方留下可验证的错误码、状态字段和完整性标记。**
