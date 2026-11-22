# 源码索引、关键常量表、术语与核验命令

本附录集中收录本书所依据的固定快照元数据、源码目录映射、关键源码常量表、中英文术语对照以及本地核验命令，方便直接对照上游代码逐条查证。

## 上游快照元数据

| 字段 | 值 |
| --- | --- |
| 仓库地址 | `https://github.com/PrimeIntellect-ai/prime-agent` |
| 固定提交 SHA | `783acb5d72a9b23f97fa4a793a67d421b3ec402d` |
| 提交说明 | `feat(inbox): show what rlm.inbox.read returned in the python_repl card (#4123)` |
| 工作区版本 | `0.10.5`（`Cargo.toml` `[workspace.package]`，MIT License，Rust 2021） |
| 最近正式版本 | `v0.10.5`（`2026-11-18T19:15:02Z`） |
| 快照采集日期 | `2026-11-21` |
| Rust 源码规模 | 10 个 crate，`crates/*/src` 下 1,053 个 `.rs` 文件、524,791 行 |
| Python 运行时规模 | `prime-agent-runtime/` 下 22 个 `.py` 文件（含测试）、33,135 行；其中 `src/rlm/` 核心源码 13,749 行 |
| 内置技能包数量 | `skills/` 下 14 个目录（10 个可执行 Python 技能 + 4 个 Markdown 技能） |

## 核心源码路径索引

| 架构模块 | 关键文件与目录 |
| --- | --- |
| 工作区配置与 Lint 门禁 | `Cargo.toml`、`AGENTS.md`、`vendor/crossterm/PRIME_AGENT_PATCH.md` |
| Agent Turn 循环与 Claim-First | `crates/pa-agent/src/agent.rs`、`crates/pa-agent/src/agent_loop/run.rs` |
| 共享线协议与领域词表 | `crates/pa-types/src/daemon/`、`crates/pa-types/src/inbox_notice.rs`、`crates/pa-types/src/session.rs` |
| `python_repl` 工具定义与截断 | `crates/pa-core/src/tools/ipython.rs`、`crates/pa-core/src/kernel/manager/requests.rs`、`crates/pa-core/src/kernel/manager/execution.rs`、`crates/pa-core/src/kernel/shared.rs` |
| Protocol v3 CPython 内核 | `prime-agent-runtime/src/rlm/repl.py`、`prime-agent-runtime/src/rlm/repl.md`、`prime-agent-runtime/src/rlm/__init__.py` |
| 异步 `bash()` 与持久进程 | `prime-agent-runtime/src/rlm/bash.py`、`prime-agent-runtime/src/rlm/_winjob.py` |
| 技能加载与安全边界 | `crates/pa-core/src/skills/mod.rs`、`skills/*/SKILL.md` |
| 递归子 Agent 与 Spawn 账本 | `crates/pa-core/src/session_engine/rlm_host.rs`、`crates/pa-daemon/src/rlm_children/`、`crates/pa-daemon/src/rlm_ledger.rs`、`crates/pa-daemon/src/supervisor_parent_death.rs` |
| 声明式状态机工厂 | `prime-agent-runtime/src/rlm/factory.py`、`prime-agent-runtime/src/rlm/machines/` |
| 持久信箱与跨进程通信 | `crates/pa-core/src/session_engine/inbox.rs`、`crates/pa-daemon/src/agent_inbox.rs`、`crates/pa-daemon/src/agent_messaging.rs`、`crates/pa-daemon/src/peer_client.rs` |
| 会话搜索、热更新与 ACP | `crates/pa-daemon/src/session_content_search.rs`、`crates/pa-daemon/src/update_prepare.rs`、`crates/pa-daemon/src/update_restore.rs`、`crates/pa-daemon/src/acp/` |
| 分层提示词与 Continual Harness | `crates/pa-core/src/prompts/system_prompt.rs`、`crates/pa-core/src/prompts/layers/`、`crates/pa-core/src/refinement/`、`crates/pa-core/src/session_engine/harness_digest.rs`、`prime-agent-runtime/src/rlm/harness.py` |
| 自动压缩、目标、心跳与自主门禁 | `crates/pa-core/src/session_engine/compaction.rs`、`crates/pa-core/src/session_engine/goal_boundary.rs`、`crates/pa-core/src/cron/`、`crates/pa-core/src/autonomous/gates.rs` |
| MCP 宿主认证与内核客户端 | `crates/pa-core/src/mcp/`、`crates/pa-daemon/src/mcp_connections.rs`、`prime-agent-runtime/src/rlm/mcp.py` |
| TUI 工具卡片与可观测性 | `crates/pa-tui/src/tool_card/ipython.rs`、`crates/pa-daemon/src/streaming.rs`、`crates/pa-core/src/agent_traces/`、`crates/pa-core/src/session_engine/semantic_edges/` |

## 关键源码常量速查

| 常量名 | 核验值 | 所在位置 | 作用 |
| --- | --- | --- | --- |
| `PROTOCOL_VERSION` | `3` | `rlm/repl.py` | Python REPL 线协议版本号 |
| `DEFAULT_MAX_OUTPUT_CHARS` | `65,536` | `pa-core/src/kernel/shared.rs` | 单单元格 stdout/stderr 3:1 头尾截断上限 |
| `MAX_BACKGROUND_OUTPUT_CHARS` | `65,536` | `pa-core/src/kernel/shared.rs` | 未归属后台原始 fd 输出缓冲上限 |
| `_STREAM_FRAME_TEXT_CAP` | `65,536` | `rlm/repl.py` | 单条 Python 流事件帧最大字符数 |
| `_RESULT_TEXT_CAP` | `1,048,576` | `rlm/repl.py` | 单次表达式 `repr` 或异常文本传输上限 |
| `_PAYLOAD_CAP` | `16 MiB` | `rlm/repl.py` | `emit()` 与 `host_request()` 单帧 JSON 上限 |
| `DEFAULT_PYTHON_CELL_TIMEOUT_MS` | `60,000` 毫秒 | `pa-core/src/settings/types.rs` | 单元格默认超时时间 |
| `PYTHON_REPL_TIMEOUT_MAX_S` | `180` 秒 | `pa-core/src/tools/ipython.rs` | 模型单次调用允许申请的最大超时 |
| `KERNEL_SHUTDOWN_TIMEOUT_MS` | `5,000` 毫秒 | `pa-core/src/kernel/shared.rs` | 宿主等待内核退出的强杀时限 |
| `_SHUTDOWN_TIMEOUT`（MCP） | `2.5` 秒 | `rlm/mcp.py` | MCP 客户端关闭时限（严格低于宿主 5 秒线） |
| `_YIELD_AFTER_SECONDS` | `1.0` 秒 | `rlm/bash.py` | 接入完成通知时 `await bash()` 自动降级等待窗口 |
| `DEFAULT_SNAPSHOT_MAX_VARIABLE_BYTES` | `16 MiB` | `rlm/repl.py` | 快照单变量上限与压缩裁剪阈值 |
| `DEFAULT_SNAPSHOT_MAX_BYTES` | `256 MiB` | `rlm/repl.py` | 快照总字节数上限 |
| `DEFAULT_RLM_MAX_DEPTH` | `2` | `pa-core/src/session_engine/rlm_host.rs` | 默认递归子 Agent 最大深度 |
| `RLM_PROGRESS_NOTE_MAX_LENGTH` | `512`（UTF-16 码元） | `pa-core/src/session_engine/rlm_host.rs` | 子 Agent 进度条单条最大长度 |
| `RLM_PROGRESS_NOTE_MIN_INTERVAL_MS` | `10,000` 毫秒 | `pa-core/src/session_engine/rlm_host.rs` | 进度条最小发送间隔 |
| `RLM_LEDGER_MAX_BYTES` / `MAX_RECORDS` | `32 MiB` / `100,000` | `pa-daemon/src/rlm_ledger.rs` | Spawn 账本失败闭合读取上限 |
| `DEFAULT_RESERVE_TOKENS` | `16,384` | `pa-core/src/session_engine/compaction.rs` | 自动压缩默认预留 Token |
| `DEFAULT_KEEP_RECENT_TOKENS` | `20,000` | `pa-core/src/session_engine/compaction.rs` | 压缩后默认保留的近期历史 Token |
| `COMPACT_THRESHOLD_RATIO` | `0.95` | `pa-core/src/session_engine/compaction.rs` | 压缩默认窗口比例上限 |
| `COMBINED_LIMIT_HEADROOM_FLOOR` | `4,096` | `pa-core/src/session_engine/compaction.rs` | 联合输入输出上限最小安全余量 |
| `MAX_GATE_OUTPUT_CHARS` | `6,000` | `pa-core/src/autonomous/gates.rs` | 写入续跑提示的门禁失败输出上限 |
| `_MAX_TOOL_PAGES` | `25` 页 | `rlm/mcp.py` | MCP `tools/list` 分页最大页数 |

## 核心术语表

- **RLM（Recursive Language Model，递归语言模型）**：将上下文与大块数据视为持久 REPL 里的变量，并通过程序化函数调用按需派生递归子 Agent 的运行时范式。
- **PTC（Programmatic Tool Calling，程序化工具调用）**：模型不直接输出几十种零散 JSON 工具调用，而是通过唯一的 `python_repl` 工具编写 Python 代码调用异步函数、循环和过滤数据。
- **Continual Harness（持续脚手架）**：独立于不可变静态系统提示词之外的持久化状态层（含 `prompt`、`memory`、`skill`、`subagent`、`factory` 五类条目），支持通过 `/refine` 做小步、有证据的增量更新与回滚。
- **Claim-First**：在消费任何待办队列前必须先原子获取 `RunClaim` 所有权槽位，避免并发启动时丢失排队消息。
- **核家族（Nuclear Family）**：单个 Agent 允许直接观测和发信的范围，严格限定为其唯一父节点、同级兄弟节点（所有根会话互为兄弟）与直接子节点。
- **持久信箱（Agent Inbox）**：由守护进程维护的每会话 `inbox.jsonl` 日志存储，把 6 类机器通知与唤醒提示分离，由模型通过 `rlm.inbox.read()` 按条件消费。
- **ACP（Agent Client Protocol）**：基于 stdio JSON-RPC 的标准化外部客户端接入协议，Prime Agent 在此基础上扩展了 `_session/steering` 运行中转向能力。

## 本地核验与复现命令

在上游仓库快照目录中核对提交、统计和配置边界：

```bash
git rev-parse HEAD
rg -n "DEFAULT_MAX_OUTPUT_CHARS|DEFAULT_RLM_MAX_DEPTH|DEFAULT_RESERVE_TOKENS" crates/
rg -n "SourceScope::Project" crates/pa-core/src/skills/
```

在本书仓库目录中运行教学模型单元测试、重新生成图表、构建站点与检验链接：

```bash
python3 -m unittest discover -s examples -v
python3 scripts/make_diagrams.py
node wiki/build.mjs
python3 scripts/make_epub.py
python3 scripts/check_links.py
```
