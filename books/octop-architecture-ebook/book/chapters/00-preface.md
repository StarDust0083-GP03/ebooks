# 前言：看懂一个 Agent 宿主如何拼起来

调通一个大模型 SDK 往往只需要几十行 Python，但把它做成一个家庭或小团队长期跑在自己机器上的多用户助手，真正消耗工程量的事情几乎全在模型循环之外。谁能看哪位专家的对话、浏览器关掉后长任务怎么继续跑、工具调进死循环怎么刹住、切到 PostgreSQL 多副本之后定时任务和飞书通道怎么避免跑两遍、托管外部编程 Agent 时怎么把知识库和插件递给它，这些才是决定平台能不能稳住的骨架。

TencentCloud/Octop 的价值正在这层宿主设计。它把四套独立发布的运行库（`octop-harness`、`octop-gateway`、`octop-memory`、`octop-browser`）和一个 React 前端，装进同一个 Python 包里。读它的源码时，与其按目录从头翻到尾，不如追着真实故障和边界问题看：谁持有状态、哪条边界真拦得住越权、哪项能力在单机和集群模式下表现完全不同。

## 分析快照与研究边界

全书基于 TencentCloud/Octop 的 `v1.0.3b7` 标签，对应提交 `ecd2cdbce3a54dee0f424f021d0a144d9669685e`，上游发布日期为 2026-11-18，本次源码采集日期为 2026-11-22。`pyproject.toml` 固定的四个核心运行库版本下限分别为 `octop-harness[all]>=1.0.11`、`octop-memory>=1.0.5`、`octop-gateway>=1.0.4` 和 `octop-browser>=1.0.0`。文末与附录里的 92 个源码链接全部锁死在该提交上，不会随默认分支变动失效。

本书的工作边界交代在前面，正文不再每节重复：

- 书里的模块职责、调用链、常量、表结构与边界条件，均来自对固定快照代码与文档的静态核对。
- 本书未在真实生产环境中对 Octop 执行模型调用评测、并发压测或渗透测试；提到仓库中的 `pytest` 或前端测试文件时，只表示源码提供了对应验证入口。
- 四个外部运行库独立发版，本书聚焦 Octop 宿主如何装配、调用和约束它们，不替上游运行库背书其全部内部实现。
- 第 17 章配套的 `examples/mini_host.py` 是零依赖 Python 教学模型，用来离线运行并验证 6 条核心架构不变量，18 个单元测试全部在本机实跑通过。

## 从 v1.0.3b1 到 v1.0.3b7 的变化导读

如果你的印象还停留在早期的 `v1.0.3b1`，这六个小版本里落地的架构改动相当密集，好几条旧结论在 `v1.0.3b7` 里已经过时：

| 版本 | 核心架构变化 | 对应章节 |
| --- | --- | --- |
| `v1.0.3b2` | 账号加入 TOTP 与 Passkey 二次验证及无密码登录；新增 RAGFlow 知识库连接器；虚拟桌面和系统浏览器改为默认不启动；改密码退出其他设备 | 第 10、12、15 章 |
| `v1.0.3b3` | 落地 ADR 003，PostgreSQL 模式接管技能包源与知识库（含 `pgvector`）；引入 90s/30s/25s 同行租约协调多实例 Cron 与 IM 通道；拆分 Docker 精简/完整镜像；新增火山引擎 TOS 后端；Windows SQLite 关闭每句 fsync | 第 6、7、9、15 章 |
| `v1.0.3b4` | 工作台新增腾讯云 CVM 运维页，多实例下授权存入控制面；Docker 默认 `:latest` 改为精简版，完整版移至 `:full` | 第 12、15 章 |
| `v1.0.3b5` | 桌面端从 Wails 全面切换为 Electron 45 壳；下线 `history_v2`，启动时单次迁回 `thread_messages`；共享专家支持主动共享对话；自定义 MCP 支持填写 OAuth 应用凭证；新增 `octop migrate openclaw/hermes` | 第 3、5、6、10、15 章 |
| `v1.0.3b6` | 发布多副本 Helm Chart 与集群运维文档；新增托管专家（`kind="managed"`），把 OpenCode、Claude Code、Codex、CodeBuddy 等 ACP Runner 当一等专家运行；知识库加入实体图谱与二阶段重排序；管理员可限制创建专家时可选的存储后端；修复 Cursor 重叠审批卡死 | 第 4、7、9、12、14、15 章 |
| `v1.0.3b7` | 托管专家支持固定会话模式，并通过 6 个回环 MCP 服务接入连接器、知识库、专家回调、文件、定时任务与已启用插件；工具调用上限改为仅拦截连续相同调用（默认 40 次）；Docker 部署的 Octop 保留宿主机目录挂载进 Docker 专家，裸机安装继续剔除 `volumes`；PostgreSQL 孤儿 checkpoint 清理改为只统计不删除；带路径的 MCP OAuth 按 RFC 8414 发现元数据 | 第 4、6、7、8、10、14 章 |

## 阅读之前先避开三个常见误判

阅读 `README_CN.md` 时，容易看到早期留下的概念印象，对照 `docs/architecture.md` 和 `src/octop/infra/server.py` 后有三个事实必须先对齐。

第一，`AgentManager`、`Gateway`、`CronManager` 都是进程级单例，按数据行字段区分用户和专家，早就不再为每个用户维护一套独立的管理器对象。

第二，聊天主通道已经从旧版 SSE 迁到双向 WebSocket（`/api/agents/{agent_id}/chat/ws`），哪怕前端个别辅助文件仍保留历史命名，也只有 HITL 恢复路由 `POST /api/agents/{agent_id}/chat/hitl/resume` 还在用 SSE 推送续跑流。

第三，选择 SQLite 还是 PostgreSQL 不再只是换一张连接表。随着 ADR 003 与同行租约落地，PostgreSQL 模式下技能包源、知识库原文和 `pgvector` 向量、专家记忆以及通道和定时任务租约都迁进了共享库，但每台实例的本机工作区和飞书去重表仍留在各自磁盘上，需要按数据类别分别盘点。

## 怎么读这本书

如果你是第一次拆解自托管 Agent 平台，按目录顺读最省力：前四章看宿主如何把多入口和四套运行库装进一个进程，第 5 到第 9 章啃透身份、多层存储、工作区后端、请求链路和知识记忆，第 10 到第 14 章看扩展机制、多 Agent 团队、安全边界、前端状态机和托管 ACP/Bridge，最后三章落到多副本部署、架构取舍和可运行的最小宿主代码。

如果你已经在维护类似的 Agent 平台，可以直接跳到第 8 章看一次 WebSocket 回合的完整处理，再对照第 6 章的存储分层、第 4 章的托管专家与熔断中间件，以及第 15 章的多副本租约，最后翻附录的源码索引直达对应文件。

## 源码入口

- [`pyproject.toml`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/pyproject.toml)
- [`README_CN.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/README_CN.md)
- [`docs/architecture.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/architecture.md)
- [`AGENTS.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/AGENTS.md)
