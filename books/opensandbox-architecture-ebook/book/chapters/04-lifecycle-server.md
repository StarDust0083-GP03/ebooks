# 控制平面：一台 FastAPI 服务器的四条不变量

很多控制面服务写着写着就会烂掉：路由函数里塞满 `if runtime == "docker"`，数据库里养着一张和底层容器永远对不上账的沙箱状态表，线上漏配了一把 API key 却照常启动，等安全团队找上门才发现裸奔了一周。

`server/` 目录承载了 OpenSandbox 的生命周期控制面（基于 FastAPI）。读这几万行 Python 代码不需要逐行记路由，抓住它死守的四条不变量，整个目录结构立刻清晰。

## 目录分层一览

```text
server/opensandbox_server/
  main.py          启动 lifespan、中间件注册、路由挂载与启动期断言（367 行）
  startup_guard.py 无 API key 时的交互确认与环境变量逃生舱（117 行）
  config.py        服务端配置模型，含默认开启的 runtime.execd_run_as_init
  api/             lifecycle（547 行）、proxy（844 行）、diagnostics（112 行）、pool（279 行）、schema（2054 行）
  middleware/      auth、request_id、date_header、http_metrics
  services/        SandboxService 抽象与 docker/、k8s/、fast_sandbox/ 适配
  repositories/    仅持久化 snapshots/ 与 templates/ 两张目录账本
  tenants/         file / http 租户提供方
  integrations/    renew_intent、idle_pause、otel
```

在 `release-1.3.0-rc.1` 里，早期面向人工排障的 `api/devops.py` 已经彻底移除，统一收口到带必填 `scope` 参数的 `api/diagnostics.py`。同时沙箱、模板与快照的列表分页和元数据 patch 校验在 `1.3.0-rc.2` 抽成了跨后端共用实现，保留元数据键被拒绝时在三个后端统一返回 `SANDBOX::INVALID_METADATA_LABEL`。

## 不变量一：API 层绝不触碰运行时细节

所有路由处理函数只认识 `SandboxService` 抽象接口。以 `api/lifecycle.py` 的创建路由为例，真正执行的有效逻辑不到 30 行：校验 `extensions`、检查续期策略冲突、归一化 `networkPolicy` 动作、必要时解析 `snapshotId` 对应的源镜像与后端，随后直接委派给 `sandbox_service.create_sandbox(request)`。

里面没有 Docker client，没有 Kubernetes API 调用，也没有针对某个后端的条件分支。把 API 层收成纯粹的校验与委派边界，单元测试只需替换一个内存版 `SandboxService` 就能验证整套契约逻辑，不需要拉起 Docker daemon 或 Kind 集群。

## 不变量二：沙箱不是数据库里的一行

服务端数据库（默认 SQLite `~/.opensandbox/opensandbox.db`，可切换 PostgreSQL）只持久化两类平台自身拥有的目录数据：**快照元数据**（`repositories/snapshots/`）和**模板目录**（`repositories/templates/`）。

沙箱本体不在库里。Docker 容器、Kubernetes 的 `BatchSandbox` / `Pool` 以及 `sandbox.fast.io` 的 CR 才是唯一状态真相，每次 `GET` 和 `LIST` 都向底层运行时读取。这带来两个直接后果：

- **换存储不等于迁移沙箱**。从 SQLite 切到 PostgreSQL 只需搬运快照和模板目录，不影响正在跑的工作负载。
- **进程重启靠运行时元数据自愈**。Docker 后端重启时从容器标签 `opensandbox.io/created-at` 重建过期定时器，若恢复过期守护遇到持久异常（除并发已删容器外），从 `1.2.1` 起直接阻止启动，避免出现永不过期的孤儿容器；暂停与恢复过渡期的时间戳暂存在 `~/.opensandbox/metadata/_transition/`，重启后只有与观测到的真实容器状态一致才采纳，不一致直接丢弃。

## 不变量三：不安全配置必须在启动期炸掉

单个请求成功，往往掩盖了最致命的隔离误配。少配 API key、租户命名空间无权限、声明的安全运行时未安装，或者在配置里把签名密钥打进错误日志，都会让问题潜伏到事故爆发。

`main.py` 的 lifespan 在接流量前串联了一组硬断言，任何一条不过直接 `os._exit(1)`：

- **鉴权确认**：未配 `server.api_key` 且未启用多租户时，交互式 TTY 要求人工键入 `YES`（30 秒超时），非交互环境（Docker、Kubernetes、CI）必须显式声明 `OPENSANDBOX_INSECURE_SERVER=YES`，否则拒绝启动。
- **租户与命名空间校验**：校验租户配置合法性，在 Kubernetes 模式下逐一验证可枚举的租户命名空间存在且可访问；租户提供方的 `ttl` 若配置为 `null`、负数或 `NaN`，在 `1.3.0-rc.2` 会安全回落到 30 秒默认缓存时间，不再触发 lookup 崩坏或永久缓存。
- **安全运行时校验**：校验 `[secure_runtime]` 声明的 `gvisor` / `kata` / `firecracker` 在当前宿主机或集群中真实可用。
- **配置报错脱敏**：`1.3.0-rc.1` 修复了配置校验报错的密钥回显，包括 `OPENSANDBOX_SECURE_ACCESS_KEYS` 环境变量路径，防止安全签名密钥落入启动日志。

此外，`config.py` 中的 `runtime.execd_run_as_init` 自 `1.3.0-rc.1` 起默认为 `true`，控制面会在创建容器或渲染 Fast Sandbox 模板时自动注入 `EXECD_INIT=1`，让 `execd` 接管沙箱进程树。

## 不变量四：状态机语义只在控制面收口

`resume()` 是暂停态专属操作，不是通用重启器。已经自然退出或异常失败的沙箱处于 `Terminated` 或 `Failed`，调用方应当新建沙箱而不是原地复活。

控制面的主迁移链路保持清晰：

```text
创建：Pending -> Running
暂停：Running -> Pausing -> Paused -> Resuming -> Running
正常完成（BatchSandbox exit 0）：Running -> Terminated（reason = SANDBOX_COMPLETED）
删除：任意活跃态 -> Stopping -> 清理完成后 404
异常：任意态 -> Failed（带 reason 与 message）
```

请求级校验全部集中在 `services/validators.py`（673 行），因为 SDK 往往不强制部分上限或跨字段约束，服务端必须充当最终权威。比如 `1.3.0-rc.1` 在所有运行时统一拦截带 Windows 反斜杠遍历的 `volumeMounts.subPath`（如 `foo\..\secret` 或前导反斜杠），封堵了 Windows 节点上的卷根逃逸；`1.2.1` 起封闭请求对象里出现未知键直接返 `422`。

## 维护模式下的控制面代理层

`api/proxy.py`（844 行）实现了 `/sandboxes/{id}/proxy/{port}`，用来在客户端既无法直连沙箱、又没有 ingress 网关时兜底转发 HTTP 和 WebSocket。

从 `1.2.1` 开始，官方文档已经把服务端代理数据路径标记为**维护模式**：既有流量继续兼容，但生产部署应当迁移到独立的 `ingress` 网关。即便如此，这段代理逻辑依然守着克制的转发规则：剥离逐跳头与上游 `Server` 头，保留上游 `Date`（缺失时再补当前时间），只把单斜杠开头的相对 `Location` 重写到同一沙箱代理前缀下，绝不改写绝对 URL 或 `//host/path`，也不替客户端跟随重定向，避免把凭据带到过期目标。

## 单活默认与极窄的双副本能力

默认 Helm Chart 的 `server.replicaCount` 为 `1`，升级采用 `Recreate` 策略，先停旧实例再起新实例，升级窗口内控制面 API 短暂不可用。

换用 PostgreSQL 后端并不代表获得了通用水平扩展的生命周期高可用，它只打开了一个极窄的能力：**两个副本协同推进 Kubernetes 公开快照创建**。实现上没有引入分布式锁或 leader election，而是靠三条现成的不变量配合：

1. **先观察再创建**：`SandboxSnapshot` CR 名称由快照 ID 确定性派生，副本先查后建，撞到 `409 AlreadyExists` 当作已提交；
2. **终态写入走 CAS**：数据库把行更新到终态时校验期望前态，双副本并发收尾时一个成功、另一个空操作；
3. **周期恢复扫描**：某副本中途崩溃留下的非终态记录，由存活副本定期扫表并沿同一套 CR 观察路径推进收口。

文档明确声明这不提供跨数据库、Kubernetes 与镜像仓库的 exactly-once 保证，恢复周期只决定接管延迟。

## 源码入口

| 路径 | 看什么 |
|---|---|
| `server/opensandbox_server/main.py` | lifespan 启动断言、中间件链与路由注册 |
| `server/opensandbox_server/startup_guard.py` | 无 API key 时的确认逻辑 |
| `server/opensandbox_server/config.py` | `runtime.execd_run_as_init` 默认值与全局配置模型 |
| `server/opensandbox_server/api/lifecycle.py` | 创建、续期、暂停、快照路由与校验顺序 |
| `server/opensandbox_server/services/validators.py` | 跨后端请求校验、`subPath` 防遍历与保留元数据校验 |
| `server/opensandbox_server/api/proxy.py` | 维护模式下的 HTTP/WebSocket 反向代理 |
| `docs/architecture/control-plane/server.md` | 控制面分层与单活/双副本边界说明 |
