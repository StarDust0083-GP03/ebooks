# 前言：从狂热、退潮到 2027 拐点

2025 年下半年，不少工程团队把刚接好的 MCP Server 拆回了命令行脚本。理由很实际：往系统提示里塞进四五十个工具的 JSON Schema，还没开始干活，上下文窗口就吃掉一大截；遇到跨系统的多步查询，模型只能一轮调一个工具，把几百行原始 JSON 反复塞进对话历史，既慢又贵。到了 2026 年初，Agent Skills 流行起来，很多开发者直接转向本地 CLI 配合 `SKILL.md` 的渐进披露，社区里关于 MCP 过于沉重、迟早被 CLI 取代的争论此起彼伏。

但如果把视线从开发者本机移到真实生产流量上，数据讲的是另一件事。Philipp Schmid 在 2026 年 11 月 26 日发布的文章《Why I'm Bullish on MCP for 2027》中列出了几组公开业务数据：在 Linear 和 Notion，Agent 创建的 issue（约 240 万条/周）和页面已经占到总量的一半左右，Notion 报告其 MCP 流量增长了 60 倍；在 Atlassian，MCP 每个工作日处理超过 500 万次工具调用，其中 44% 的用户不在研发团队，CLI 对这批非工程角色从来就不是可选项；在 Neon，基于 MCP 的 Agent 用量保持 14% 的周环比增长；连 Apple 也开始为 Pages、Numbers 和 Keynote 内置本地 MCP Server。

这两股看似矛盾的体验，其实对应着两代不同形态的 MCP。早期引起抱怨的是 2024 年底到 2025 年的有状态协议与原始 tool calling 用法：必须维护连接握手和粘性会话，列表结果无法跨会话缓存，客户端一股脑把全量 Schema 喂给模型，且只能由用户发问后被动拉取。到 `2026-07-28` 正式规范落地后，协议底层做了大手术，把会话和握手从核心拿掉，补齐了 HTTP 路由头、列表 TTL 缓存、无状态多往返追问和官方扩展机制；客户端侧则演化出 Code Mode、Skills over MCP、MCP Tasks、MCP Events 和 MCP Apps，把原本零散的胶水层收拢到了同一套协议边界里。

## CLI 与 MCP 的真实分工

把 CLI 和 MCP 对立成二选一，往往是因为默认了执行者是坐在终端前的软件工程师。在本地代码仓库里，给 Agent 一个终端、几个命令行工具和几份本地 Markdown 说明书确实够快，零协议封装，管道组合也很顺手。

一旦业务跨出本机终端，CLI 的短板很快会暴露出来。第一是凭证边界：CLI 和裸 `curl` 往往要求把长效 API Key 写进磁盘配置文件或环境变量，权限粒度粗，且和 Agent 执行的代码共享同一个进程环境，提示注入一旦突破命令限制就能直接读走密钥。第二是人群覆盖：财务、法务、运营、客服和产品团队的桌面环境里没有终端，也没有人会维护本地包管理器和环境变量，Atlassian 那 44% 的非研发调用量正是靠浏览器 OAuth 登录和零配置远程接入跑起来的。第三是多模态与强类型契约：命令行标准输出本质上是无结构文本流，遇到需要同时返回结构化 JSON、图片、音频或可交互视图的场景，只能靠脆弱的正则解析和临时文件路径硬凑。

2027 年真正成型的格局，是客户端把 MCP 从远程工具调用层升级成 Agent 运行时协议。本地进程继续用 `stdio` 或命令行，远程 SaaS、企业内部系统、长程异步任务和事件订阅则通过无状态 Streamable HTTP 与官方扩展接入，再由宿主里的 Code Mode 沙箱把强类型 MCP 接口编译成可执行函数，把两边的优势拼到了一起。

## 全书阅读路线

全书一共分为四个部分，从协议内核一路拆到运行时扩展与多代理编排：

1. **无状态内核与契约基座（第 1 章至第 3 章）**，复盘 MCP 从 `2024-11-05` 到 `2026-07-28` 的五个正式版本，讲清为什么要彻底删除 `Mcp-Session-Id` 与 `initialize` 握手、如何用 `Mcp-Method`/`Mcp-Name`/`x-mcp-header` 对齐标准 HTTP 网关、如何用 `ttlMs` 和 `cacheScope` 消除子代理列表风暴，以及 `outputSchema`、`structuredContent` 与 `SEP-1303` 如何建立机器可消费的强类型契约。
2. **执行模型、交互与身份边界（第 4 章至第 6 章）**，对比普通串行 tool calling、CLI 管道与 Code Mode 的上下文开销；拆解 `SEP-2322` 多往返请求（MRTR）和 `SEP-1036` URL Mode Elicitation 如何在无粘性会话的集群里完成人工确认与敏感信息录入；梳理 OAuth 2.1、RFC 9728 受保护资源元数据、CIMD URL 客户端注册、企业 IdP 托管授权（ID-JAG）以及 RFC 9207 `iss` 校验。
3. **官方扩展栈与 Agent 运行时（第 7 章至第 11 章）**，解析 `SEP-2133` 与 `SEP-3392` 的扩展治理生命周期，随后逐章拆解四项决定 2027 形态的核心扩展：Skills over MCP（`io.modelcontextprotocol/skills`）、MCP Tasks（`io.modelcontextprotocol/tasks`）、MCP Events（`io.modelcontextprotocol/events`）以及 MCP Apps（`io.modelcontextprotocol/ui`）。
4. **工程缺口、多代理编排与代码核验（第 12 章至第 14 章）**，直面发现机制（Registry 与 Server Cards `SEP-2127`）、意图可观测性和纵深安全（Prompt Injection、Untrusted Webhook、沙箱与 iframe、本地安装 `SEP-1024`）的现实短板；展开 Phil Schmid 提出的 Harness-as-MCP-Server 协作架构与 `ask_agent` 反模式；最后用零依赖 Python 标准库代码和 21 个单元测试，把全书最关键的协议不变量在本地跑通。

## 事实口径与研究边界

本书是一份独立架构研究，不是 MCP 官方文档。为了保证每条协议细节经得起核查，全书严格区分三类信息来源：

- **上游行业数据与趋势判断**：引用自 Philipp Schmid 2026 年 11 月 26 日文章 `https://www.philschmid.de/mcp-2027`，文中涉及具体公司数据或作者观点处均明确归属来源。
- **官方规范与 SEP 条款**：全部核对自 `modelcontextprotocol/modelcontextprotocol` 仓库固定快照（提交 `2bbace557bbf2cb5d03d6f9b48905415cd7607b0`，提交日期 `2026-11-24`），并明确标注每项能力处于 `2026-07-28` 正式核心规范、Stable 扩展、Beta 扩展还是 `draft` 草案阶段。
- **离线教学模型**：第 14 章及 `examples/` 目录下的 Python 运行时与 21 个单元测试用来验证协议状态机、签名校验与边界拦截逻辑，不代表线上集群压测或第三方商业服务实测。

所有源码路径、SEP 速查表与本地核验命令汇总在附录和 `sources/UPSTREAM.md` 中，读者可以随时对照官方快照反向校验。
