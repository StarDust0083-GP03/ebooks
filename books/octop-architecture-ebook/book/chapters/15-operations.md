# 部署与运维：Electron 壳、镜像分层与多副本租约

自托管软件最容易在“装得上”和“扛得住”之间劈叉：给个人用的桌面安装包要做到点两下就能跑，给小团队用的容器集群又要做到不停服滚动升级、不丢数据、飞书和定时任务不双活乱撞。到了 `v1.0.3b7`，Octop 在打包形态和集群运维上已经把这两条线收口得非常清楚。

## 三种交付形态的真实边界

在 `v1.0.3b3` 到 `v1.0.3b6` 之间，Octop 对桌面端、容器镜像和 NAS 包做了一次彻底的分层瘦身：

| 分发形态 | 核心构成 | 包含的扩展能力 | 注意事项 |
| --- | --- | --- | --- |
| 桌面安装器 / 绿色便携包 | Electron 45 壳 + 绿色 Python 运行时 + Dashboard | 仅捆绑 `browser` 扩展，桌面控制、OCR 与本地嵌入不内置 | `v1.0.3b5` 彻底移除 Wails；桌面壳只管拉起随机本地回环端口的 Python sidecar、托盘和窗口，登录过期自动续上，拒绝进程内自更新 |
| Docker 精简版（`:1.0.3b7` / `:latest`） | Python 核心 + Dashboard + Playwright | 轻量运行，默认镜像标签自 `v1.0.3b4` 起指向精简版 | 宿主机目录挂进 Docker 专家仅在容器部署模式下保留（见第 7 章） |
| Docker 完整版（`:1.0.3b7-full` / `:full`） | 精简版 + 虚拟桌面 + OCR + 本地 ONNX 嵌入 | 含桌面流控、`rapidocr`、`fastembed` | 虚拟桌面和系统浏览器自 `v1.0.3b2` 起默认不随进程启动，按需开启 |
| 飞牛 fnOS NAS 包 | Docker 精简 / 完整 `.fpk` 与原生 x86/ARM `.fpk` | 覆盖带或不带 Docker 的 NAS 环境 | 原生包不依赖 Docker daemon |

还要注意桌面端和远程桌面的名字撞车：`desktop/` 目录是本机 Electron 客户端壳（基于 pnpm 12.15 + Node 24 构建，运行时采用 Electron 45 内置 Node 24.22 与 Chromium 156），负责托管 sidecar 进程树并在收到 `Ctrl+C`/`SIGTERM`/`SIGHUP` 时优雅清理子进程组；而 `src/octop/infra/control/desktop/` 才是给 Agent 看屏幕和点鼠标用的远程虚拟桌面流控模块。

对于已经在使用其他开源助手的老用户，`v1.0.3b5` 还提供了 `octop migrate openclaw` 和 `octop migrate hermes`（`src/octop/infra/migration/`），可以把原有配置直接迁成一位 Octop 专家，降低切换成本。

## 多副本集群：Cookie 粘滞加同行租约

如果一台 SQLite 实例已经撑满，或者希望升级时先拉起新版本再停旧版本，`v1.0.3b6` 发布的官方 Helm Chart（`deploy/helm/octop/`）和集群指南（`docs/cluster_CN.md`）给出了不引入外部消息队列的多副本解法。

![多副本集群的 Cookie 粘滞与数据库同行租约](../../wiki/assets/diagrams/cluster-lease.svg)

整套集群架构由三条硬规则托住：

1. **所有共享资产收进同一台 PostgreSQL 16 + `pgvector`**：用户、角色、模型、连接器、通道、定时任务、JWT 密钥、技能包源文件、知识库原文与向量、专家记忆（每专家按 `agent_<id>` 命名空间隔离）以及多实例共享的腾讯云 CVM 授权全部在库里。注意数据库层面仍然是单点，高可用靠托管 PostgreSQL 或主从复制解决，Octop 本身不替代数据库故障转移。
2. **前置入口必须按 Cookie 粘滞，绝不按来源 IP 粘滞**：浏览器的实时流和轨迹只由接手这轮对话的那台实例在内存里持有并推送。反向代理或 Kubernetes Ingress 用 Cookie 把同一浏览器固定到同一台副本；一旦按来源 IP 粘滞，所有经过公司出口 NAT 或统一代理的流量都会被砸进同一台副本。当某台副本下线，已打开的页面会断连一次，刷新后切到存活副本并从 PostgreSQL 读回历史。
3. **每台副本独享自己的本机数据目录（`/data/.octop`），严禁 NFS 共享写**：本机工作区和飞书去重表 `feishu-dedup.sqlite` 都是单机本地文件，用 NFS 挂给多台同写只会把 SQLite 写坏。集群里管理员应通过“限制可选存储后端”开关，引导新建专家使用 PostgreSQL、S3、COS、TOS 或 OpenSandbox 工作区，防止 Pod 重建把临时盘里的工作区文件清空。此外，多副本首次冷启动必须给每台配置完全一致且符合强密码规则（至少 8 位且含字母和数字）的 `OCTOP_ADMIN_USERNAME` 与 `OCTOP_DEFAULT_PASSWORD`，否则各台会各自掉进安装向导生成互不相认的向导口令。

## 定时任务与 IM 通道如何做到多副本单活

多开两三台 Octop 副本时，为什么同一条飞书机器人不会连出三条长连接，同一条每天早九点的定时任务也不会跑三遍？答案藏在 `src/octop/infra/db/lease.py` 只有 35 行的三个常量里：

```python
LEASE_SECONDS = 90
RENEW_SECONDS = 30
RECONCILE_SECONDS = 25
```

`CronManager`（`infra/cron/manager.py`）和通道管理器在启动时都会生成进程级 `instance_id`，并按数据库自身时钟（PostgreSQL 用 `clock_timestamp()`，SQLite 用 `strftime('%s','now')`）在 `cron_jobs` 和通道记录的同一行上抢占租约：

- 只有抢到行的那台副本真正运行该定时任务或保持该 IM 通道连接；持有者每 30 秒续租一次，把过期时间往后推 90 秒。
- 进程优雅退出（`stop()`）时主动释放自己持有的租约行；如果进程意外硬崩，最多等 90 秒租约自动过期。
- 其他存活副本每 25 秒跑一轮 `reconcile` 巡检，发现过期或无主行立刻抢占接管。唯一需要留心的小窗口是飞书去重表留在原机器的 `feishu-dedup.sqlite` 上，通道刚切到新副本的极短窗口内，飞书重发的同一条消息可能会被新副本再处理一次。

另外，`CronManager` 在任务行为上也在 `v1.0.3b6` 做了细化：`task_type` 依然区分直接推送固定文案（`text`，不花模型费用）与唤醒专家生成后再推（`agent`）；而投递目标 `session_key` 除了默认指向所有者的工作台会话，还可以通过 `infra/cron/session_target.py` 指定 `latest`（跟随最新对话）或 `thread:<thread_id>`（固定续写某一条历史对话），后台标签页收到的定时提醒则改为原生系统通知（`v1.0.3b4`）。

## 备份边界与分层测试矩阵

备份之前先按模式对账，别把导出一段聊天 JSON 当成整站灾备：

- **SQLite 单机模式**：需要停机或按一致快照策略同时备份 `config.json`、控制面 `octop.db`、密钥目录、各专家工作区（含 `memory.sqlite`）以及本地 `knowledge/` 和 `skill-packages/`。直接 `cp` 正在高频写入的 WAL SQLite 文件容易拿到损坏快照。
- **PostgreSQL 集群模式**：一次 `pg_dump` 就能把控制面、技能包源、知识库原文与向量、专家记忆和云凭证一网打尽；如果专家工作区放在 S3/COS/TOS，再配上对象存储侧的版本或跨区复制即可。

仓库里的自动化验证分成了清晰的层次（入口见 `Makefile` 与 `.github/workflows/ci.yml`）：

| 验证层级 | 典型命令 / 标记 | 能守住什么 | 守不住什么 |
| --- | --- | --- | --- |
| 静态质量门禁 | `make lint`、`make typecheck`（`mypy --strict`）、`npx tsc -b` | 代码风格、严格类型契约、前后端编译 | 运行时并发与外部接口变化 |
| 单元测试 | `uv run pytest tests/unit -x -q` | 路由、熔断中间件、租约算法、权限与状态门禁 | 真实网络与数据库方言差异 |
| 集成测试 | `uv run pytest tests/integration -x -q` | JWT/MFA、资源边界、WS 订阅、服务生命周期 | 多节点故障与真实 LLM 稳定性 |
| PostgreSQL 专项 | `-m postgresql`（配 `OCTOP_TEST_DATABASE_URL`） | 迁移脚本、PG Store、pgvector 检索、命名空间维护 | 未模拟的硬件/网络抖动 |
| 真实联调 | `-m live`（默认跳过） | 指定供应商与真实凭证端到端连通 | 所有第三方供应商的长期契约 |

仓库的 `.githooks` 预提交钩子默认把 `make all` 和前端构建设为合入门槛，而且 CI 同时跑在 Linux 和 Windows 上。对运维团队来说，上线前再补上六次隔离环境故障演练，比一句口头高可用靠谱得多：模拟模型在工具调用前失败、外部工具超时重试、客户端弱网断线重连、审批挂起时重启服务、磁盘接近写满时触发 SQLite 压缩跳过、以及从备份整机恢复并核对附件与记忆关联。

## 源码入口

- [`desktop/README.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/desktop/README.md)
- [`docs/cluster_CN.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/cluster_CN.md)
- [`deploy/docker/Dockerfile`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/deploy/docker/Dockerfile)
- [`deploy/docker/docker-compose.yml`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/deploy/docker/docker-compose.yml)
- [`deploy/docker/docker-compose.cluster.yml`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/deploy/docker/docker-compose.cluster.yml)
- [`deploy/helm/octop/Chart.yaml`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/deploy/helm/octop/Chart.yaml)
- [`Makefile`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/Makefile)
- [`.github/workflows/ci.yml`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/.github/workflows/ci.yml)
- [`src/octop/infra/db/lease.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/db/lease.py)
- [`src/octop/infra/cron/manager.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/cron/manager.py)
- [`src/octop/infra/cron/job.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/cron/job.py)
- [`src/octop/infra/cron/session_target.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/cron/session_target.py)
