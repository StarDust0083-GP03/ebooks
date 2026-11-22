# Infinity 架构解剖：面向高并发 Agent 的无阻塞运行时与响应式工具协议

这是一本面向工程师的独立源码解剖电子书，完整拆解 UC Berkeley Hydro 团队开源的 [`hydro-project/infinity`](https://github.com/hydro-project/infinity)（固定快照 `e1a3e4b36e5f6143e03a59a7ad86e1abacfd22e8`，`2026-11-05`）。

全书包含前言、13 个正文主章节和源码附录，配 6 张主题自适应内联 SVG 架构图，以及一份零依赖 Python 教学模型与 19 个单元测试。

## 核心内容

- **无阻塞切片与休眠**（第 1、3 章）：切片绝不阻塞外部工具的不变量、`HistoryManager` 三阶段未落盘状态（`unvalidated_items` / `pending_items` / `turn_buffer`）、落盘先于派发的持久化屏障、毒消息溢出止损与双重去重。
- **21 个 Crate 分层与强类型 ID**（第 2 章）：五层依赖版图、`strkind` 类型化 ID（`ThreadId`、`ToolCallId`、`ProviderCallId`、`ChoiceId`）与 `ThreadRef`。
- **响应式工具协议 RAP 与线程树**（第 4、5 章）：`/.well-known/rap-toolset` 发现、五类 `RapCallback` 回调、`infinity-mcp-bridge`、`child_of` 线程栈校验、`Fresh` 边界与位置索引上下文压缩。
- **本地调度与跨进程供应器**（第 6、7 章）：单线程 `LocalSet` 与 `FuturesUnordered` 路由器、`DeferQueue` 与 `is_passive()`、`ErrorClass` 结构化错误分类、Bedrock 60s 建连超时与 Unix Socket 供应器。
- **堆叠沙箱、热迁移与 Serverless**（第 8、9、10、11 章）：Jujutsu 工作区与 Git worktree 堆叠、macOS `sandbox-exec` 与 Linux `bwrap` 写隔离、`git bundle` 跨主机热迁移、SQS FIFO + Aurora DSQL + DynamoDB + 900s 双轨持久定时器、CLI TUI、`infinity acp` 与 `hydro_lang` Slack 数据流。
- **边界复盘与可执行教学模型**（第 12、13 章）：源码中的安全边界、已知 `TODO` 与本地/云端能力落差，外加 `examples/mini_infinity.py` 与 19 项单元测试。

## 产物目录

- 多页静态阅读站：`wiki/index.html`、`wiki/chapters/*.html`、`wiki/search.html`
- 离线单文件合订本：`wiki/book.html`
- 合并全文 Markdown：`book/Infinity架构解剖.md`
- EPUB 3 电子书：`exports/infinity-architecture.epub`
- A5 打印版 PDF：`exports/infinity-architecture.pdf`
- 教学模型与单测：`examples/mini_infinity.py`、`examples/test_mini_infinity.py`

## 本地构建与核验

```bash
# 运行 Python 教学模型测试（无需第三方依赖）
python3 -m unittest discover -s examples -v

# 全量构建站点、EPUB、PDF 并执行链接与结构检查
bash scripts/build.sh
```

## 许可与声明

本书正文、架构图与教学代码采用 MIT 许可证发布（见 `LICENSE`）。上游分析对象 `hydro-project/infinity` 采用 Apache-2.0 许可证，详见 `NOTICE` 与 `sources/UPSTREAM.md`。本书为独立源码研究，非 Hydro 团队官方文档。
