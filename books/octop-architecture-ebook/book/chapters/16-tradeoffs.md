# 架构取舍：单机够用到何时，复杂度卡在哪里

从启动装配、身份隔离、多层存储一路拆到多副本租约，整个 Octop 的技术选型呈现出一条非常鲜明的主线：宁可在宿主内部把边界和状态机写严实，也不为了纸面上的高大上架构过早引入外部中间件。看完 `v1.0.3b7` 的全部演进之后，可以客观盘一盘这套架构最值得抄的作业、当下真正聚集复杂度的地方，以及什么时候才真的该动刀拆分。

## 五个经受住版本迭代的好选择

把 `v1.0.3b1` 到 `v1.0.3b7` 的变化串起来看，有五项决策越往后走越显出威力：

1. **宿主与四大运行库硬切边界**：把 LangGraph 循环、IM 协议、长期记忆和浏览器 CDP 拆成独立发版的 Python 包，宿主只管用户、权限、控制面、装配和工作台。每次修底层运行库漏洞（比如记忆长会话分批提炼或 harness 协议升级），宿主改一行 `pyproject.toml` 就能平滑升级，代码库不至于在几个垂直领域同时腐烂。
2. **果断收口失败或过重的实验**：`v1.0.3b5` 彻底下线只支持 SQLite 的 `history_v2`，并用启动一次性迁移把老用户平滑接回 `thread_messages`；同版本把桌面壳从 Wails 切成 Electron 45；`v1.0.3b3` 把庞大的内置市场目录迁到远程市场。对一个高速迭代的开源平台来说，及时做减法比堆新功能稀缺得多。
3. **用 ADR 003 + 同行租约走完多副本第一步**：没有为了多实例一上来就强绑 Redis 和 Celery，而是把技能包与知识库并进共享 PostgreSQL、给 Cron 和 IM 通道加上 90s/30s/25s 同行租约、入口靠 Cookie 粘滞。既守住了单机 SQLite 的零依赖体验，又给了中小团队一套能直接上 Kubernetes Helm 的横向扩展方案。
4. **托管专家走回环 MCP 而非重写外部 Runner**：面对 Claude Code、OpenCode、Codex 等形态各异的外部编程 Agent，不试图魔改它们的内部循环，而是借 ACP 统一会话模式与审批队列，再把 Octop 的知识库、专家回调、文件、定时任务和插件封装成 6 个本地回环 MCP 递过去，集成成本压到极低。
5. **共享展示但隔离执行状态**：团队房间用主持人 `thread_id` 做共享展示，用 `conversation_id~member` 隔离成员 checkpoint 和工作区；共享专家默认隔离所有人的私人线程，只有显式标记 `contributed` 才开放阅读。

## 当下复杂度真正堆在哪里

没有哪套架构只有甜头没有代价。把 `v1.0.3b7` 的代码摊开量一遍，压力高度集中在三个超大核心模块和两类结构张力上：

**三个高密度核心模块**：`src/octop/infra/agents/manager.py` 已经接近五千行，`src/octop/infra/gateway/process/processor.py` 超过两千两百行，前端 `dashboard/src/pages/Chat/hooks/chatStore.ts` 同样体量可观。大文件本身不自动等于坏设计，但它意味着每次改动都要同时踩稳普通聊天、托管 ACP、专家团队、HITL 审批、模型故障转移、用量统计和 Bridge 远端代理七八条契约，任何一个角落漏测都容易引发回归。

**两类固有结构张力**：

- **多层存储的一致性与清理成本**：控制面、checkpoint、消息投影、长期记忆、知识索引和远程工作区各自落盘，跨层删除必须严格排顺序（先清 checkpoint 再删线程行），共享 PostgreSQL 上更是因为 checkpoint 表缺少原生外键归属，不得不把孤儿清理收成只统计不删除的保守策略。
- **进程内状态与多副本粘滞的边界**：虽然多副本已经能共享配置、知识库和单活通道，但正在进行的 WebSocket 流、团队在途 `TeamJobTracker`、取消信号和飞书本地去重表仍停留在单进程内存或本机磁盘里。副本重启或滚动发布时，在途流必然中断一次，需要刷新重新接轨。

## 治理顺序：先收契约，再谈拆分

如果你正基于 Octop 做二次开发，或者自己也在搭一套类似的自托管 Agent 平台，遇到性能或维护压力时，先做三件低风险高回报的事，远比拍脑袋拆微服务管用：

| 优先动作 | 解决的真实痛点 | 怎么算做到位 |
| --- | --- | --- |
| 拆分配置解析与生命周期 | 改一个工具或模型参数就要动几千行的 `manager.py` | 纯函数输入配置生成 `HarnessAgentConfig` 快照，脱离生命周期锁独立单测 |
| 固化多入口事件契约 | Web、IM、托管专家和团队流在前端或历史里各有一套特判 | 同一组事件流在多个入口和 `chatStore` 状态下断言产出完全一致 |
| 建立负面与恢复测试矩阵 | 正常问答跑得通，但越权、并发审批、断网和删库边界含糊 | 覆盖越权线程、重复审批、连续工具熔断、PG 孤儿只统计和停机备份恢复 |

## 什么时候才真的需要引入进程外队列或拆 Worker

先看真实测量数据（事件循环阻塞延迟、同时活跃回合数、文档解析/OCR CPU 占用、数据库锁等待和尾延迟），不要拿“注册用户破千”这种静态数字当拆分理由。只有出现下面四种信号之一时，进程外队列和独立 Worker 才值回它的运维票价：

1. **大量重 CPU 后台任务拖慢对话事件循环**：例如高频批量上传几百页扫描 PDF 做 OCR 和本地向量化。此时最稳的第一刀是只把文档解析与建索引抽成独立 Worker，不动用户、权限、专家和聊天主进程。
2. **要求任务跨进程重启不断线且必须可靠接续**：例如几十步的自动化巡检或长时代码生成，要求进程崩溃后由另一台节点自动捞起未完成派工，而不是靠人工刷新重试。
3. **团队异步派工需要跨实例分发与整树取消**：现有的进程内 `TeamJobTracker` 和仅取消主持人流的语义无法满足强一致任务树控制。
4. **执行沙箱需要独立的弹性伸缩池**：代码执行和浏览器自动化资源消耗波动极大，与轻量控制面混部会互相挤占内存。

记住一个前提：加一套进程外队列绝不只是多装一个 Redis。你必须同步设计持久 `job_id`、租约与 fencing token、失败重试分类、外部写操作幂等键、取消信号跨节点传播和流式帧跨实例路由（Pub/Sub）。如果目前单机 SQLite 或者 2~3 副本 PostgreSQL + Cookie 粘滞已经跑得稳稳当当，保留模块化单体就是最省钱也最容易排障的架构。

## 源码入口

- [`docs/adr/001-single-process-model.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/adr/001-single-process-model.md)
- [`docs/adr/002-database-backends.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/adr/002-database-backends.md)
- [`docs/adr/003-skill-package-kb-pg-storage.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/adr/003-skill-package-kb-pg-storage.md)
- [`src/octop/infra/agents/manager.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/manager.py)
- [`src/octop/infra/gateway/process/processor.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/gateway/process/processor.py)
- [`dashboard/src/pages/Chat/hooks/chatStore.ts`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/dashboard/src/pages/Chat/hooks/chatStore.ts)
