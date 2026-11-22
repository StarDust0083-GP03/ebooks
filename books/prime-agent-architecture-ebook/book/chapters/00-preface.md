# 前言：把长程自主编程收进一个持久 Python REPL

跑过超过两小时的编程 Agent 会话的人，大概率都见过同一种退化过程：前二十分钟干脆利落，读几个文件、跑一轮测试、改两处函数都没毛病。等到上下文逼近窗口上限，几十条原始终端日志和重复读入的代码块把消息历史撑满，压缩一次之后模型开始忘记刚才查过的路径、反复重跑同一条命令，终端断连或子任务报错还能把整个会话卡在半路。

`PrimeIntellect-ai/prime-agent` 给出的解法和主流多工具脚手架走的是另一条路。翻开 `crates/pa-core/src/prompts/layers/core.md` 和 `crates/pa-core/src/tools/mod.rs`，注册给大模型的 JSON 工具表面上只有一个：`python_repl`。文件编辑、后台命令、子 Agent 派生、MCP 调用、上下文压缩、周期心跳和持续改进，全部收进持久 CPython 进程里，以 Python 异步函数的方式被模型按需编排。

为了撑住这套跑在长时间跨度里的设计，项目在 0.10.x 阶段把宿主从 TypeScript 重写进了 10 个 Rust crate 组成的工作区（`Cargo.toml` 标注版本 `0.10.5`，共计 1,053 个 `.rs` 源文件、524,791 行 Rust 代码），下面接一层约 3.3 万行代码的 Python RLM 运行时（`prime-agent-runtime/`）以及 14 个内置技能包（`skills/`）。它没有把所有会话塞进单一进程，而是用守护进程 Supervisor 托管独立的 per-session Worker，再由 Worker 管一个带快照能力的 Python 内核。

## 这本书怎么读

全书围绕 Prime Agent 解决的四类工程问题展开：

第 1 至 4 章拆解最底层的执行模型。为什么传统 JSON 工具调用在长程任务里容易失速，10 个 Rust crate 如何维持单向依赖与进程隔离，Turn 循环怎样用 Claim-First 机制挡住并发抢占，`python_repl` 内核如何通过 Protocol v3 把标准流、原始文件描述符、中断信号和 `dill` 快照管稳。

第 5 至 8 章看编排、通信与持久状态。`bash` 与 `edit` 怎样作为程序化工具运行，递归子 Agent 树怎么受限于深度上限与 Spawn 账本，为什么机器通知必须先落进持久信箱 `rlm.inbox` 而不是直接往上下文里灌，以及会话 JSONL 树、流式全文搜索、热更新事务和 ACP 外部协议如何配合。

第 9 至 12 章转向长期运行的稳定性与人机界面。静态可缓存提示词如何与可演化的 Continual Harness 解耦，自动压缩如何计算双底线阈值并保留 REPL 内存，MCP 认证与协议执行为什么要在宿主和内核之间劈开，以及 TUI 如何用首帧零网络规则和卡片折叠守住交互延迟。

第 13 章集中讨论安全边界、成本账本和本书附带的零依赖 Python 教学模型（`examples/mini_prime_agent.py`）。附录 A 汇总全部源码路径、核心常量表、术语对照和本地核验命令。

## 研究口径与边界

本书属于独立源码架构研究，不是 Prime Intellect 官方文档。所有模块边界、数据结构、常量取值、线协议字段和行为描述，均直接核验自固定快照：

| 项 | 核验值 |
| --- | --- |
| 上游仓库 | `https://github.com/PrimeIntellect-ai/prime-agent` |
| 固定提交 | `783acb5d72a9b23f97fa4a793a67d421b3ec402d` |
| 提交标题 | `feat(inbox): show what rlm.inbox.read returned in the python_repl card (#4123)` |
| 工作区版本 | `0.10.5`（对应最近正式标签 `v0.10.5`，发布于 `2026-11-18T19:15:02Z`） |
| 快照采集日期 | `2026-11-21` |

书中的代码行为来自静态源码阅读与仓库内测试用例核对；第 13 章及 `examples/` 中的 Python 教学模型用于离线复现关键不变量，仅使用 Python 标准库，已在本地通过全部单元测试。本书没有在真实计费大模型 API 或生产多租户集群上执行端到端压测，涉及模型表现或安全声明的地方，一律标明来自上游文档、论文引用还是源码中的显式防御逻辑。
