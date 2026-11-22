# 总体架构：单进程宿主与四套可复用运行库

很多团队一启动多 Agent 平台项目，图纸上先画出一排框：API 网关、任务队列、Worker 集群、记忆服务、文件服务、通道服务，光把本地开发环境拉起来就要折腾半天 Docker Compose。Octop 反其道而行之，在 `docs/adr/001-single-process-model.md` 里定下一条铁律：核心平台必须是一个 Python 进程、一个安装包，装完只配模型密钥就能跑，不强制依赖 Redis、RabbitMQ 或独立 Worker。

单进程不等于代码揉成一团。相反，为了既保住单机安装极简，又能把底层能力复用给其他项目，Octop 把整个架构切成了五层，而且在仓库根目录的 `AGENTS.md` 里写死了模块之间的依赖禁令。

![Octop 五层模块化单体边界](../../wiki/assets/diagrams/host-layers.svg)

这张分层图把 Octop 的边界划得很直白：所有依赖严格向内流动，传输层调领域层，领域层绝不反向依赖 HTTP 路由或 CLI。整座仓库里只有 `src/octop/launch.py` 这一个模块允许同时引入 `infra/server` 和 `api/app`，用来把后端领域对象树和 FastAPI 装配到 uvicorn 里。

## 为什么拆成宿主加四套运行库

看 `pyproject.toml` 的依赖清单，能清楚看到 Octop 把重型垂直能力抽成了四个独立发版的库：

- `octop-harness`（`>=1.0.11`）承担基于 LangGraph 的 Agent 循环、工具调用、技能加载、存储后端抽象与中间件机制，以及记忆和浏览器工具在回合内的接线。
- `octop-gateway`（`>=1.0.4`）负责飞书、钉钉、企微、微信、Telegram、Discord 等 IM 平台的协议适配、媒体上传与群聊唤醒规则。
- `octop-memory`（`>=1.0.5`）管长期记忆的提取、召回、排序、数据库表结构与压缩维护。
- `octop-browser`（`>=1.0.0`）管 Chrome CDP 连接、浏览器 Profile、DOM 操作与 `browser_tool`。

Octop 宿主仓库自己只干一件事：把这四个库包装成多用户产品。用户认证、角色与用户组、专家注册表、线程管理、连接器凭证加密、知识库检索、定时任务、跨实例 Bridge 和 React Dashboard 全部留在 `src/octop/` 和 `dashboard/` 里。`AGENTS.md` 明确要求，遇到属于这四个库的行为缺陷，必须去对应运行库里修并升级 `pyproject.toml` 的版本下限，禁止把实现复制粘贴进宿主仓库。

这种拆法把演进节奏解开了：运行库可以独立跑单测、独立升级模型协议或 IM 适配，宿主则专注产品权限、控制面数据和工作台体验。

## 读懂 infra/ 目录里的硬边界

初看 `src/octop/infra/`，容易因为目录名把它当成传统 DDD 里的纯基础设施层。其实 Octop 的核心业务编排几乎都在 `infra/` 里：`AgentManager` 管进程内全部专家的生命周期与装配，`Gateway` 管通道连接和 WebSocket Hub，`GlobalProcessor` 管所有入口汇入后的统一回合处理，`CronManager` 管定时调度，`SharedServices` 把各张表的仓储对象打包注入上层。

为了让这个大目录不退化成网状互相引用，仓库定下了几条刚性红线：

- `infra/` 绝不能引入 `api/`、`cli/` 或 `launch.py`。
- `infra/db/repos/` 只写 SQL，只能依赖 `infra/db/_base` 和 `infra/utils/`，不能反向引入 `agents/` 或 `gateway/`。
- `infra/utils/` 只放无领域依赖的纯辅助函数（路径、ULID、SSRF 校验、宿主机目录规则等）。
- `api/routers/` 保持薄层，只做 HTTP 参数校验、权限提取、调 `infra/` 和映射 `OctopError`，不藏业务规则。
- `dashboard/` 前端 SPA 只通过 `/api` HTTP 和 WebSocket 与后端通信，绝不直接读 SQLite 文件，构建后的静态资源打包进 `src/octop/dashboard/` 随 wheel 一并分发。

Click CLI 也按同样思路分层，不复制业务逻辑。纯配置增删（如 `user`、`provider`、`cron` 列表）走离线仓储直读写本地库；需要真实运行时能力的命令（如 `chats send`、`chats repl`、`cron run-now`、`acp`）则在进程内拉起一个短生命周期的嵌入式 `OctopServer`；涉及操作系统或扫码绑定的操作直接走外部子进程。

## 单进程如何平滑过渡到多副本集群

早期 `v1.0.3b1` 时期，ADR 002 虽然支持把控制面换到 PostgreSQL，但单进程内存假设导致它明确不承诺多实例同写：两个进程各自拉起 APScheduler，同一条定时任务就会跑两次；两个进程各自连飞书长连接，消息会乱抢。

到了 `v1.0.3b3` 至 `v1.0.3b6`，Octop 并没有为了多副本推翻单进程模型去上 Redis 和 Celery，而是顺着共享 PostgreSQL 补齐了三块拼图：

1. **内容平面入库（ADR 003）**，PostgreSQL 模式下技能包源、知识库原文和 `pgvector` 向量不再绑在单机磁盘上，全部进共享库，每台实例看到同一份资产。
2. **同行租约（`infra/db/lease.py`）**，定时任务和 IM 通道在数据库行上抢占 90 秒租约，持有者每 30 秒续约、退出时主动释放，其他实例每 25 秒巡检接管过期行，保证后台任务和长连接单活。
3. **入口按 Cookie 粘滞（`docs/cluster_CN.md`）**，浏览器发起的一轮流式对话由接入的那台实例在内存里持有并推送实时帧，前置反向代理或 Kubernetes Ingress 用 Cookie 把同一浏览器固定到同一副本。

这套组合很有工程节制感：单机用户依然零外部依赖、开箱即用；小团队需要滚动升级或横向扩展对话吞吐时，换上共享 PostgreSQL、配好对象存储或 PG 工作区、套上官方 Helm Chart 就能跑多副本，不需要把单体拆成十几个微服务。代价也很坦诚：单台副本挂掉时正在进行的实时流会断一次，刷新后由新副本从共享库恢复历史，它换来的是部署简洁，而不是无感透明的进程内状态迁移。

## 源码入口

- [`docs/architecture.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/architecture.md)
- [`AGENTS.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/AGENTS.md)
- [`docs/adr/001-single-process-model.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/adr/001-single-process-model.md)
- [`docs/adr/002-database-backends.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/adr/002-database-backends.md)
- [`docs/adr/003-skill-package-kb-pg-storage.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/adr/003-skill-package-kb-pg-storage.md)
- [`src/octop/infra/server.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/server.py)
