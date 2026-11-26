# 超越远程工具：把 Harness 做成 MCP Server 与架构反模式

2026 年做多代理（Multi-Agent）系统的团队，几乎都重复干过一件吃力不讨好的事：给主代理和子代理之间自己画一套专有通信协议。有人用 Redis Pub/Sub 拼任务队列，有人自己写了一套 HTTP 回调网关管子代理生命周期，有人又另造一套共享黑板 API 给同伴代理同步进度。结果每换一个外部编排器或者想把两支团队的 Agent 接在一起，又得从头写一遍胶水适配器、鉴权转发和超时重试逻辑。

Philipp Schmid 在《Why I'm Bullish on MCP for 2027》的结尾给出了一个非常犀利的判断：**2027 年胜出的 Agent Harness 不仅要做好 MCP 客户端集成，Harness 自己也应该暴露成一台 MCP Server。**与其另起炉灶发明一套私有跨代理协议，不如让多代理协作直接复用它们本来就精通的那同一套 MCP 原语。下面这张图把 Harness-as-MCP-Server 的编排平面，连同前文拆解的四重安全防线与三大工程缺口收束在了一张总览里。

![Harness 作为 MCP Server 的协作模型与四重安全边界](../../wiki/assets/diagrams/harness-swarm-security.svg)

图里左侧展示了同一套 MCP 协议如何向下连业务系统、向上做多代理协作；右侧对应每新增一层扩展能力就必须同步补齐的四重安全边界；底部则提醒架构师在 2027 年落地时持续盯着发现层与意图可观测性两块未完拼图。

## 用现成 MCP 原语拼出多代理协作平面

把一个 Agent Harness 暴露成 MCP Server 后，多代理系统里最麻烦的三件事，全部能严丝合缝地映射到我们前面几章刚拆过的标准协议能力上，不需要发明任何一个专有新字段：

1. **子代理派发即 MCP Tasks（`io.modelcontextprotocol/tasks`）**，主代理或外部协调者调用类似 `spawn_subagent` 的工具，Harness 立即返回一个 `resultType: "task"` 的高熵 `taskId` 句柄。子代理在后台跑几分钟甚至半小时，协调者按 `pollIntervalMs` 轮询进度或通过 `subscriptions/listen` 接收 `notifications/tasks`；子代理中途卡在需要人工审批或信息补全时，状态自然切到 `input_required`，经 `tasks/update` 拿到输入后继续跑；任务超时或不再需要时，通过 `tasks/cancel` 协作取消。子代理崩了、协调者重启了，句柄都在，状态机清晰可追。
2. **共享协作看板（Swarm Board）即强类型 Tools**，多个并行工作的子代理需要共享中间发现、认领未分配子任务、避免重复踩同一个坑。Harness 直接暴露一组带 `inputSchema` 与 `outputSchema` 的看板工具（例如 `board.post_finding`、`board.claim_work`、`board.list_open_items`），配合显式 `board_id` 句柄（遵循 `SEP-2567`），子代理甚至可以用 Code Mode 在沙箱里一次性拉取并过滤整个看板的未认领项，再写回结构化结论。
3. **跨代理唤醒即 MCP Events（`io.modelcontextprotocol/events`）**，协调者或同伴代理不必傻傻地开一堆高频定时器空转。Harness 暴露 `task.completed`、`board.finding_posted`、`review.requested` 等标准事件类型；当某个子代理把测试跑绿、或者在看板上贴出一条阻断级缺陷时，Harness 通过 Push（本机/同集群）或 Webhook（跨团队/跨云常驻）瞬间唤醒协调者或负责修复的同伴代理，并且天然自带 `cursor` 断点续传与 `eventId` 去重。

这种设计最实在的工程红利，是**复用既有鉴权、网关与工具链**。给一个跨部门协作代理划定权限，直接用第 6 章的 OAuth 2.1 Scope 和企业 IdP ID-JAG 策略管控（比如只允许外部伙伴代理认领特定队列的任务、只读某块看板）；监控和限流直接复用第 2 章的标准 `Mcp-Method`/`Mcp-Name` HTTP 网关；连给新同事解释怎么把新子代理接进来，都可以直接用第 8 章的 Skills over MCP 下发协作 Playbook。

## 必须避开的架构反模式：别把 SaaS 包成单个 `ask_agent` 工具

看到这里，有些 SaaS 团队可能会动一个极具诱惑力的念头：既然 Harness 可以做成 MCP Server，干脆把整个 CRM、工单系统或数据库平台也藏在一个自带内部小模型的 `ask_crm_agent(prompt: string)` 单一端点后面，省得设计那么多精细工具。

Philipp Schmid 在文章最后一段直接给这种做法判了死刑：**好的 MCP Server 是面向 Agent 暴露工具，而不是把 Agent 伪装成工具（Good MCP servers expose tools for agents, not agents as tools）。**

把整个常规 SaaS 产品包成一个单一的 `ask_agent` 端点，在实际工程里会立刻踩中四颗大雷：

1. **严重的上下文饥饿（Context Starvation）**，用户手里的主 Agent 掌握着完整的对话历史、本机代码仓库、Slack 讨论、Sentry 报错和另一台业务系统的数据；而被关在黑盒里的 `ask_crm_agent` 只能看到主 Agent 临时塞进 `prompt` 参数的那两句话。它既不知道用户真正的约束，也没法参考其他系统的数据，只能盲人摸象般给出泛泛回答。
2. **彻底废掉 Code Mode 与跨系统组合能力**，单一自然语言接口没有精细的 `outputSchema`，主 Agent 无法在沙箱里把 CRM 客户列表和 Stripe 账单、Linear 工单做内存 Join，只能退回低效的自然语言传话游戏，每传一次就多一层信息衰减和幻觉放大。
3. **双层模型推理叠高延迟、成本和不可控性**，主模型花一轮组织提问，黑盒里的次级模型再花五轮调内部 API 并润色成散文，主模型再花一轮把散文读懂并提取字段，端到端延迟翻倍，出错时几乎无法定位到底是主模型没问清，还是黑盒代理自己编错了。
4. **破坏统一的审计与最小权限边界**，原本清晰可审计的 `list_customers`（只读）和 `refund_invoice`（破坏性写操作，需人工确认）全部被揉进了同一个 `ask_agent` 字符串入口里，宿主和企业网关根本无法在调用前分辨这次自然语言提问背后到底会触发只读查询还是删库改单。

把这两种模式什么时候该用、什么时候绝不能用，压成一张选型判断表：

| 场景特征 | 推荐架构形态 | 典型例子 |
| --- | --- | --- |
| 常规 SaaS、数据库、内部微服务、运维 API | **暴露清晰、原子、强类型的 Tools + Resources + Skills + Events** | Linear、Notion、Sentry、Stripe、GitHub、Atlassian、Spanner |
| 需要在宿主的 Code Mode 里与其他系统数据做过滤与 Join | **必须提供 `outputSchema` 与 `structuredContent`** | 跨工单、监控、账单系统的联合排障与报表 |
| 多步骤、耗时数分钟至数小时的自主子任务编排 | **Harness-as-MCP-Server（用 MCP Tasks + Swarm Board + Events）** | 派生后台编码子代理、自动化安全审计代理、跨团队/跨公司长程协作流程 |
| 拥有严密私有知识推理流程、仅需向外交付最终异步结论的独立组织边界 | **谨慎采用 Agent-as-MCP-Server，且显式走 MCP Tasks + 结构化入参出参** | 外部合规审核代理、跨企业供应链议价代理 |

一条简单好记的实战铁律：**只要对方是用户自己的主 Agent 需要随时调取数据并与其他系统自由拼装的业务能力，就老老实实暴露干净、强类型、权限分明的工具与结构化数据；只有当任务跨越了长程异步执行边界、跨子代理编排边界或跨团队/跨公司组织边界时，才把整个 Agent Harness 作为 MCP Server 暴露出来。**

到这里，全书的协议原理、扩展机制与架构取舍已经全部拆解完毕。但架构理解不能只停在纸面上，下一章我们直接运行本书配套的零依赖 Python 教学运行时和 21 个单元测试，把无状态头校验、TTL 缓存隔离、Code Mode 字节对比、跨副本 MRTR 续传、Skills SHA-256 审批撤销、Tasks 状态机以及 Events HMAC 防重放与 SSRF 拦截逐一跑通。
