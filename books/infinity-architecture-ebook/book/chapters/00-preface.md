# 前言：为什么一台树莓派能装下七万五千个 Agent

线上跑过长周期 Coding Agent 或运维 Agent 的人，大概率撞过同一堵墙：模型只花了三秒钟决定触发一次 CI 构建，或者发出一条等待人工审批的工单，接下来的二十分钟甚至三天里，整个 Agent 进程却只能挂在那里干等。stdio 管道不能关，HTTP 会话不能断，协程栈和内存上下文不能放，扔进 AWS Lambda 还会撞上十五分钟执行上限。把并发量拉到几百个，内存和闲置算力账单立刻失控；如果再让多个子 Agent 同时改同一个代码仓库，工作目录里很快就会出现互相覆盖的脏文件。

UC Berkeley Hydro 团队开源的 `hydro-project/infinity` 从头拆解了这套阻塞假设。它把异步运行时的思想搬进了 Agent 系统：单次执行切片（slice）绝不阻塞在外部工具上。模型一旦吐出异步工具调用，运行时先把这一轮对话刷进持久化存储，发出一次即发即忘（fire-and-forget）的 HTTP POST，随后立刻退出当前切片。两轮之间，一个等待中的 Agent 在系统里只剩几行历史记录，没有驻留任务，没有调用栈，也没有打开的网络连接。等几分钟或几天后工具把结果推回回调地址，运行时再按线程 ID 重新加载历史、继续推理。

沿着这条主线，`infinity` 在同一个 Rust 工作区里搭出了三层互相咬合的工程组件：

- **Infinity Runtime**（`infinity-agent-core` 与 `infinity-agent-lambda`），负责切片执行、三阶段历史缓冲、去重、线程树、位置索引压缩，以及同一套核心逻辑在本地常驻进程与 AWS Lambda 无服务器环境之间的复用。
- **Reactive Agent Protocol（RAP）**（`rap-protocol`、`rap-client`、`infinity-rap-bridge` 与 `infinity-mcp-bridge`），用无状态 HTTP 触发加异步回调取代长连接会话，原生支持长耗时工具、事件订阅、OAuth 授权、人工确认弹窗、前端视图更新与跨机迁移，同时通过桥接层兼容现有 MCP 服务。
- **Infinity Code 与堆叠沙箱**（`infinity-daemon`、`infinity-agent-cli`、`sandbox-core`、`sandbox-local`、`sandbox-remote`），面向本地与远程机器的并发编码代理。每个线程拿到独立的 Jujutsu 工作区或 Git worktree，并通过 macOS `sandbox-exec` 或 Linux Bubblewrap 限制写权限，并行干活后再把子沙箱压缩合回父书签，宿主工作目录始终保持干净。

## 本书依据的源码快照与研究边界

全书所有模块路径、类型签名、协议字段、常数与设计取舍，全部直接核验自上游仓库的固定快照：

- 仓库：`https://github.com/hydro-project/infinity`（Apache-2.0 许可证）
- 提交 SHA：`e1a3e4b36e5f6143e03a59a7ad86e1abacfd22e8`（`2026-11-05`，`docs: explain Linux sandbox prerequisites (#188)`）
- 规模实测：21 个 Rust workspace crate，169 个 `.rs` 文件（57,969 行 Rust 代码），配套 `agent/`（AWS CDK）、`infinity-ui/`、`infinity-web/` 前端代码与 51 篇官方文档。

本书属于独立源码架构分析，不是官方文档。凡涉及性能数字（比如 80,000 个空闲 Agent 占约 8.4 GB RSS），均明确标注其来自上游基准代码 `crates/infinity-agent-core/examples/agent_scale.rs` 与文档图表 `docs/src/components/MemoryChart.tsx` 的记录，作者未在真实 AWS Bedrock 或生产 Lambda 集群上重新跑压测。第 13 章附带的零依赖 Python 教学实现与 19 个单元测试则在本地离线实际运行并通过，用来验证核心不变量而非替代生产运行时。

## 怎么读这本书

如果你关心高并发 Agent 为什么能压低空闲内存并无缝缩容到零，先读第 1 到第 3 章，把无阻塞切片、`HistoryManager` 的三阶段缓冲和落盘屏障看透。

如果你想设计异步工具协议、对接外部 Webhook 或改造现有 MCP 服务，重点看第 4、第 5 与第 7 章，里面拆解了 RAP 回调、事件子线程、位置索引压缩和供应器错误分类契约。

如果你想把多 Agent 并发改代码落到本机或远程开发机，直接看第 6、第 8、第 9 章，对照本地单线程路由器、`jj`/`git` 堆叠沙箱、Linux `bwrap` 写边界和跨机热迁移的完整实现。第 12 章集中列出源码中已经暴露的边界和未完成项，第 13 章提供可以直接单步调试的 Python 教学模型。
