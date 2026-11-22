# Summary

0. [前言：为什么一台树莓派能装下七万五千个 Agent](chapters/00-preface.md)
1. [阻塞式 Agent 的死结与无阻塞切片不变量](chapters/01-blocking-trap.md)
2. [21 个 Crate 的分层版图与类型化 ID 体系](chapters/02-workspace-topology.md)
3. [切片执行循环与三阶段 HistoryManager](chapters/03-slice-and-history.md)
4. [响应式工具协议 RAP：断连回调、订阅与 MCP 桥接](chapters/04-rap-protocol.md)
5. [线程树、上下文继承与位置索引压缩](chapters/05-threading-compaction.md)
6. [单线程本地调度器、延迟队列与抢占打断](chapters/06-local-actor-deferral.md)
7. [模型供应协议、跨进程隔离与错误分类契约](chapters/07-provider-error-boundary.md)
8. [堆叠沙箱：Jujutsu 工作区、Git Worktree 与 OS 写隔离](chapters/08-stacked-sandboxes.md)
9. [本地守护进程、SSH 远程代理与跨主机热迁移](chapters/09-daemon-remote-migration.md)
10. [零闲置云端部署：SQS FIFO、Lambda、Aurora DSQL 与持久定时器](chapters/10-serverless-aws.md)
11. [多端接入：终端 TUI、ACP 编辑器协议与 Hydro 数据流 Slack 机器人](chapters/11-clients-and-hydro-dataflow.md)
12. [失败模式、安全边界与架构取舍](chapters/12-tradeoffs-and-limits.md)
13. [零依赖 Python 教学实现：复现切片、延迟队列与堆叠沙箱](chapters/13-mini-infinity.md)

- [附录：源码索引、实测统计与核心术语对照](chapters/appendix-sources.md)
