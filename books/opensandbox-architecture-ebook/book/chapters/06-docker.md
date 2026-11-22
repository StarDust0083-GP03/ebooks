# Docker 后端：单端口复用与续期竞态锁

把单机 Docker 后端当成简单的容器封装，上手写两天就会撞上三个具体问题：一台宿主机同时跑 30 个沙箱、每个沙箱里的用户镜像随时可能新开 8080、5900、8888 端口，宿主端口怎么分配才不打架；刚给沙箱续完命、客户端拿到了 `200 OK`，下一毫秒后台过期定时器却把活生生的容器和工作区删光了；用户传了 2 GiB 磁盘配额限制，容器却照常把宿主机根分区写爆。

OpenSandbox 的 Docker 后端围绕这几类现场问题做了一套很实在的单机方案。

## 用一个宿主端口装下容器里所有服务

用户镜像会监听哪些端口，控制面事先不可能知道。如果为每个服务单独映射宿主端口，不仅宿主端口迅速枯竭，防火墙规则和容器重建后的端口追踪也会一团糟。

Docker 后端的解法很干脆：**每个沙箱只绑定一个宿主端口**，指向容器内监听 `44772` 的 `execd`，并在容器标签 `opensandbox.io/embedding-proxy-port` 里记下分配到的宿主端口号。`execd` 内置了一层轻量反向代理，把发往 `/proxy/{port}/...` 的请求直接转到容器回环地址 `127.0.0.1:{port}`：

```text
客户端 -> host:40xxx/proxy/8080/app  -> execd (44772) -> 127.0.0.1:8080/app
       -> host:40xxx/proxy/5900      -> execd         -> 127.0.0.1:5900
```

调用 `get_endpoint(sandbox_id, 8080)` 时，服务端返回 `{public_host}:{host_proxy_port}/proxy/8080`。因为 `execd` 代理完整保留 `Upgrade` 与 `Connection` 头，HTTP、SSE 和 WebSocket 共用这一个映射，VNC 远程桌面和交互终端都不需要额外开端口。对于无法处理路径前缀的特殊客户端（如某些内置 API 的镜像），后端还保留了可选的第二映射标签 `opensandbox.io/http-port` 直指 `8080`。

在 `1.3.0-rc.1` 里，当部署配置了 `[docker].publish_host` 把端口绑定到指定宿主地址时，服务端代理和 egress sidecar 就绪探针也会改拨这个地址，不再错误硬连默认回环口。

## 容器化部署服务器时的网络陷阱

`get_endpoint(..., resolve_internal=True)` 可以直接返回容器的 Docker 桥接 IP（如 `172.17.0.3:5900`），省掉宿主端口转发。但文档专门标出一条高频踩坑场景：当生命周期服务器自己跑在 Docker Compose 网络里，又通过挂载 `/var/run/docker.sock` 在默认 `bridge` 上建沙箱时，服务器容器根本路由不到默认 `bridge` 的容器 IP。

此时必须在配置里显式关掉内部解析并指定宿主机别名：

```toml
[proxy]
resolve_internal = false

[docker]
network_mode = "bridge"
host_ip = "host.docker.internal"
```

Linux 上还要给服务器容器补上 `extra_hosts: ["host.docker.internal:host-gateway"]`。另外在 Podman rootless 的 `pasta` / `slirp4netns` 用户态网络模式下，容器本身没有宿主可达的 IP，`resolve_internal=True` 会自动回退到宿主映射端口，和共享 egress sidecar 网络命名空间时的行为保持一致。

各种网络模式的约束很明确：

| 网络模式 | 行为 | 关键约束 |
|---|---|---|
| `bridge`（默认） | 独立桥接网络，映射单宿主端口 | 唯一支持 `networkPolicy` 的模式 |
| `host` | 共享宿主网络栈 | 实际单机难以多沙箱隔离，拒绝 `networkPolicy` |
| `none` | 完全断网 | 仅离线执行 |
| `pasta` / `slirp4netns` | 用户态网络 | 无宿主可路由容器 IP，强制走端口映射 |
| 自定义网络名 | 接入已有 Docker network | 启动期校验网络存在 |

## execd 注入与 `EXECD_INIT=1` 默认接管

用户给的普通镜像里并没有 `execd`。创建容器时，`services/docker/container_ops.py` 会从 `[runtime].execd_image` 把 `execd` 二进制、`opensandbox-launcher` 和 `bootstrap.sh` 暂存进沙箱容器。

从 `1.3.0-rc.1` 起，`runtime.execd_run_as_init` 默认为 `true`，`container_ops.py` 会自动往容器环境变量里追加 `EXECD_INIT=1`。容器启动后，`bootstrap.sh` 不再把 `execd` 丢到后台，而是以 `exec execd --init -- <entrypoint>` 直接替换成容器的 PID 1，接管孤儿进程回收、信号转发和进程降权地板。老镜像如果依赖旧的后台启动拓扑，可以把 `[runtime].execd_run_as_init` 显式设回 `false`。

## 修复续期与过期删除之间的竞态丢失

在单机模式下，沙箱的 TTL 过期靠服务端进程内的定时器触发。早期版本存在一个隐蔽的工作区丢失竞态：客户端调用 `renew-expiration` 刚完成校验并返回 `200 OK`，同一毫秒内旧定时器刚好到期开火，顺手把还在运行的容器和元数据全删掉了，客户端手里还攥着一张续期成功的回执。

`1.3.0-rc.2` 在 `services/docker/docker_service.py` 引入了基于 `WeakValueDictionary` 的每沙箱生命周期锁 `_sandbox_lifecycle_lock(sandbox_id)`：

- `renew_expiration` 在加锁状态下进入 `_renew_expiration_locked`，串行校验容器存在、检查非手动清理、更新内存定时器、落盘持久化过期时间覆写并刷新容器标签；
- 过期清理、删除、暂停与恢复操作全部竞争同一把每沙箱锁。

结果只有两种确定落点：要么续期先拿到锁，把过期时间往后推、让旧定时器检查失效；要么过期清理先拿到锁把容器删掉，随后的续期请求拿到干净的 `404`。不会再出现返回 `200` 却丢掉活容器的中间态。

与此同时，服务端重启后的过期恢复也在 `1.2.1` 收紧为 fail-fast：启动时从容器标签 `opensandbox.io/created-at` 与过期覆写文件重建所有定时器，除并发已被移除的容器外，任何持久 daemon 异常都会直接阻止服务端启动。

## 磁盘配额、PID 上限与暂停态快照

最近两个版本补齐了 Docker 后端的三块资源与快照能力：

**可写层磁盘配额真正生效**，从 `1.2.1` 起，创建请求里的 `resourceLimits.disk`（以及 `storage`、`ephemeral-storage` 别名）会真实映射到 Docker 容器可写层配额；如果当前存储驱动不支持配额或传入非法值，直接返回 `400` 拒绝，不再静默忽略。需要注意的是配额只覆盖容器可写层，不覆盖 bind mount、命名卷和只读镜像层。

**`docker.pids_limit = 0` 语义修复**，`1.3.0-rc.1` 允许把 `pids_limit` 设为 `0` 按文档语义关闭 PID 上限，不再在配置校验阶段误报非法。

**支持对已暂停沙箱直接做 `full` 快照**，Docker 后端的暂停操作是容器级 `docker pause`（有 egress sidecar 时先冻结主容器再冻结 sidecar，恢复时先解冻 sidecar 再解冻主容器，避免主容器恢复后发出的第一批包绕过未就绪的防火墙）。从 `1.2.1` 起，处于 `Paused` 状态的 Docker 沙箱可以直接调用公开快照接口执行 `docker commit` 生成本地 OCI 镜像；不过 Docker 后端依然只支持 `mode="full"`，收到 `mode="rootfsOnly"` 会在预检阶段直接返回 `409 UNSUPPORTED_RUNTIME`。

## 容器标签就是半个数据库

既然控制面数据库里不存沙箱行，Docker 后端就把关键运行元数据写进容器标签：

| 容器标签 | 作用 |
|---|---|
| `opensandbox.io/created-at` | 创建时间真相与重启后过期重建基准 |
| `opensandbox.io/expires-at` | 续期后的绝对过期时间记录 |
| `opensandbox.io/embedding-proxy-port` | 单宿主代理端口映射记录 |
| `opensandbox.io/http-port` | 可选的 8080 无前缀直连端口 |
| `opensandbox.io/egress-sidecar-for` | 主容器与 egress sidecar 的绑定关系 |
| `opensandbox.io/volume-managed-by` | 卷归属标记，防止清理时误删外部卷 |

这也是为什么 `PATCH /metadata` 在所有后端都严格禁止用户写入 `opensandbox.io/` 保留前缀，并在 `1.3.0-rc.1` 统一返回标准错误码 `SANDBOX::INVALID_METADATA_LABEL`。

## 源码入口

| 路径 | 看什么 |
|---|---|
| `server/opensandbox_server/services/docker/docker_service.py` | 生命周期主流程、`_sandbox_lifecycle_lock` 与续期序列化 |
| `server/opensandbox_server/services/docker/container_ops.py` | execd 暂存注入、`EXECD_INIT=1` 与磁盘/PID 限制 |
| `server/opensandbox_server/services/docker/networking.py` | 网络模式校验、`publish_host` 与 sidecar 关联 |
| `server/opensandbox_server/services/docker/port_allocator.py` | 宿主端口分配与回收 |
| `server/opensandbox_server/services/docker/metadata.py` | 保留标签前缀与校验 |
| `server/opensandbox_server/services/docker/snapshot_runtime.py` | 本地 `docker commit` 与 `rootfsOnly` 409 预检 |
| `docs/architecture/network/single-host-network.md` | 单机单端口路由与容器化服务器配置陷阱 |
