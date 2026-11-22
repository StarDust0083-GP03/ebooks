# 需求推导：脱离模型 SDK 之后要守住什么

把大模型接进产品时，第一周通常顺风顺水：上传一份文档、拼好系统提示词、流式吐字，原型就能演示。麻烦往往在第二周同时冒出来。同事问能不能共用你调好的那位研究专家，但又不能互相看见私有聊天记录；长工具调用跑了三分钟，用户切了个标签页或者合上笔记本，后台任务该不该直接掐断；Agent 准备调用外发或删文件工具时，审批按钮点快了两次会不会发出两份；好不容易配好的一套系统，换台机器或者加个副本后配置全散了。

这些问题没有一个能靠换更强的模型解决。它们决定了 Octop 必须长成什么样的宿主架构，也决定了每一条代码边界到底在防什么。

## 产品定位与信任边界

Octop 面向的典型场景是个人、家庭和小团队的自托管 AI 助手。这个定位划出了很清楚的能力边界：它需要支持多用户、角色权限、用户组资源可见性和企业 LDAP/OIDC 登录，但它的默认假设仍是可信小团队内的自托管环境，不等同于面向陌生公网租户的强隔离多租户 SaaS。

在权限设计上，系统里至少存在四类行为主体，各自需要什么权力、不该顺带拿到什么权力，在源码里分得非常明确：

| 主体角色 | 核心操作需求 | 必须拦住的越权动作 |
| --- | --- | --- |
| 普通成员 | 聊天、上传附件、管理自己的专家与连接器 | 读取他人的私有线程，或修改他人共享出来的专家配置 |
| 专家所有者 | 配置模型、技能、知识库、后端及分享范围 | 分享专家后把自己的历史私有会话一并暴露给他人 |
| 管理员 | 完成首次初始化、管理用户组、限制允许的存储后端 | 无审计地混用个人云凭证，或绕过容器宿主挂载约束 |
| 外部协作者 | 通过 IM 通道或跨实例 Bridge 调用授权专家 | 用本机用户编号冒充对端身份，或借隧道打穿管理接口 |

源码里管理员在多处路由拥有 bypass 逻辑（例如 `api/common/agent.py` 和 `api/deps.py` 中的权限检查），这是为了降低小团队运维成本做的刻意取舍。部署到跨部门场景时，要把哪些能力留给管理员、哪些云账号凭证按用户隔离（如 `v1.0.3b6` 起腾讯云 CVM 登录严格按用户隔离），当成架构前提来评估。

## 八条核心需求如何推导出系统骨架

从真实使用中的失败模式往回推，Octop 现在的模块分布几乎都能找到对应的工程动因：

| 面对的工程问题 | Octop 采取的架构方案 | 源码落点 |
| --- | --- | --- |
| 装完还要配 Redis 和 Celery 劝退个人用户 | 单 Python 包打包全部后端与 React SPA，默认 SQLite 零外部依赖 | `docs/adr/001-single-process-model.md`、`pyproject.toml` |
| Web、CLI、IM、Cron 各自写一套聊天循环必分叉 | 所有入口翻译成统一 `InboundMessage`，汇入 `GlobalProcessor` | `api/routers/chat/turn.py`、`infra/gateway/process/processor.py` |
| 分享专家方便协作，但私有对话不能串台 | 专家可见性与线程可见性分两步校验，支持显式共享单条对话 | `api/common/agent.py`、`infra/gateway/threads.py` |
| 刷新页面或弱网断连不能把长任务杀掉 | 回合归宿主进程持有，WebSocket 只做订阅与弱恢复 | `api/routers/chat/ws.py`、`dashboard/.../wsResumeGate.ts` |
| 专家文件既要能落本机，也要能放对象存储或沙箱 | 内容读写统一经过 `BackendWorkspace`，禁止业务代码直读写本地路径 | `docs/agent-backend-file-io.md`、`infra/backend/adapter.py` |
| 图恢复、聊天展示、长期记忆与文档检索节奏不同 | 拆成控制面、checkpoint、消息投影、Memory、知识索引多层存储 | `docs/adr/002-database-backends.md`、`docs/adr/003-skill-package-kb-pg-storage.md` |
| 多位专家协作和外部编程 Agent 需要接入同一工作台 | 团队主持人共享房间但隔离成员 checkpoint；托管专家通过回环 MCP 消费宿主能力 | `docs/expert-teams.md`、`infra/agents/managed.py` |
| 单机扛不住或升级要不断服，但又不想重写全套分布式队列 | 多副本共享 PostgreSQL，前置 Cookie 粘滞，Cron 与通道走数据库同行租约 | `docs/cluster_CN.md`、`infra/db/lease.py` |

这张对照表在日常改需求时非常顺手。比如要接入一个新的 IM 平台，变动应该停在通道适配和 `octop-gateway`，不该去改 `AgentManager`；如果要调整共享专家的会话可见性，变动就必须落在服务端鉴权和线程表模型上，只改前端列表过滤毫无意义。

## 用可核对的行为取代空泛指标

给一个 Agent 平台写验收标准，一上来就拍脑袋写一个并发 QPS 数字往往没什么用。模型耗时动辄几秒到几十秒，真正让人在生产里踩坑的是状态和边界失真。对照 Octop 的核心路径，至少有七条行为验收线必须盯死：

1. 用户上传的附件先进入受控工作区 `inbound/`，模型与工具只读取工作区引用，不拿浏览器预览地址当文件权威。
2. 浏览器断开 WebSocket 后，宿主里的在途回合继续执行；重新订阅后能接上后续流，历史刷新不会把屏幕上正在生成的回复冲掉。
3. 共享专家允许授权成员开启自己的对话，但拿着他人的 `thread_id` 请求历史或订阅时，除非该线程被显式标记为共享对话，否则服务端必须拒绝。
4. 危险工具触发人工审批暂停后，审批决定必须绑定具体的 `pending_id` 和参数快照，用户连点两次批准不会把外部动作投递两遍。
5. 模型陷入死循环反复调用同一个工具、传同一组参数时，宿主按连续相同次数熔断并结束回合，但不能误杀正常换参数探索的多步任务。
6. 模型或工具中途报错时，历史与界面明确记录失败回合，不能把一段报错文本混成普通成功回复。
7. 服务重启或切换数据库维护后，已提交的对话历史仍可读；清理孤儿 checkpoint 时绝不能误伤共享库里其他智能体的活跃对话。

这七条线几乎每一条都对应着 Octop 在 `v1.0.3b1` 到 `v1.0.3b7` 之间真实修过的线上问题。后面每一章拆源码时，我们都会拿具体代码去对照这些边界到底是怎么落地的。

## 非功能指标必须拆成可定位的段

在 Agent 平台里笼统说系统慢或不稳定，几乎没法排障。延迟至少要拆成四段来看：WebSocket 建连与首帧确认、模型首 token 等待、单次工具执行耗时、回合结束后的历史与轨迹落盘耗时。稳定性同样要拆成具体故障点：弱网断线重连、上游模型超时与自动切换、本地磁盘空间不足时的 SQLite 压缩跳过、进程优雅退出时租约释放、以及知识库索引或重排序超时降级。

记录一次线上问题时，把用户 ID、`agent_id`、`session_key`、`thread_id`、选中的模型引用和实际落盘结果一起抓出来，排障就能直接定位到某一层，而不用对着一张回复空白的截图猜半天。

## 源码入口

- [`README_CN.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/README_CN.md)
- [`docs/architecture.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/architecture.md)
- [`src/octop/api/common/agent.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/common/agent.py)
- [`src/octop/api/routers/chat/ws.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/routers/chat/ws.py)
