# 附录 A：源码索引、术语与核验命令

本附录汇总本书所依据的上游快照元数据、关键源码与文档索引、核心术语对照，以及可在本地复现的核验命令。

## 上游快照信息

| 项目 | 数值或标识 |
| --- | --- |
| 仓库地址 | `https://github.com/arcships/dim` |
| 固定提交 | `88a3df3669405730cbee6bfd113a7f75cf520677` |
| 工作区版本 | `0.1.0-alpha.27` |
| 快照采集日期 | `2026-11-22` |
| Rust 版本与 Edition | `1.100` / `2024` |
| 核心依赖基线 | `rutis = "0.23.0"`、`aimux-* = "=0.6.1"`、`rquickjs = "0.14"`、`tokio = "1.53.3"`、`axum = "0.8"`、`jiff = "0.2.46"` |
| 工作区实测规模 | 28 个 Cargo 成员（23 业务 crate + 2 xtask + 3 测试 crate）；111 个目录服务（75 `data` / 36 `internal`）；20 个分支 Capability；3 个内置运行时插件包；26 份 ADR |

## 关键源码与上游文档索引

| 主题 | 上游路径 |
| --- | --- |
| 工作区与依赖基线 | `Cargo.toml`、`AGENTS.md`、`README.md` |
| 时间总线与事件驱动契约 | `docs/architecture/rust-agent/time-bus.md`、`docs/architecture/rust-agent/event-driven-flows.md`、`docs/architecture/rust-agent/principles.md`、`docs/architecture/rust-agent/timeline-storage.md` |
| 事件类型与 13 保留域 | `crates/dim-agent-types/src/events.rs`、`crates/dim-agent-types/tests/fixtures/` |
| 运行时内核与分发器 | `crates/dim-agent-runtime/src/core/timeline.rs`、`crates/dim-agent-runtime/src/core/dispatcher.rs`、`crates/dim-agent-runtime/src/assembly.rs`、`crates/dim-agent-runtime/src/parts.rs` |
| 分支时间线与默认分支 | `docs/design/timeline-branches.md`、`docs/architecture/rust-agent/plugins/TimelineTree.md`、`docs/architecture/rust-agent/plugins/BranchTool.md`、`crates/dim-agent-capabilities/src/timeline_tree.rs` |
| rutis 装配与跨语言数据面 | `docs/design/rutis-usage.md`、`docs/design/plugin-contract.md`、`docs/design/service-faces.md`、`docs/architecture/rust-agent/generated/catalog.md`、`docs/adr/002-plugin-scope.md`、`docs/adr/015-scopes-are-plugins.md`、`docs/adr/025-rutis-0-23.md` |
| 内置运行时插件与自演化 | `runtime/default-plugins.json`、`runtime/runtimes.json`、`runtime/package.json`、`runtime/plugins/plugin-authoring/skills/plugin-authoring/SKILL.md`、`runtime/plugins/workflow/`、`runtime/plugins/light-ocr/` |
| 上下文准备与压缩检查点 | `docs/architecture/rust-agent/plugins/ContextPreparation.md`、`crates/dim-agent-capabilities/src/context_preparation.rs`、`crates/dim-agent-capabilities/src/history.rs` |
| 权限、审批与执行隔离 | `docs/architecture/rust-agent/plugins/Permission.md`、`docs/adr/003-approval-scope.md`、`docs/adr/009-permission-decides.md`、`docs/adr/010-sandbox-opt-in.md`、`docs/adr/012-session-grants.md`、`docs/adr/017-independent-permission-controls.md`、`docs/adr/018-permission-is-the-preset.md` |
| Code Mode 与 Aimux | `crates/dim-codemode/src/config.rs`、`crates/dim-codemode/src/interpreter.rs`、`crates/dim-codemode/src/session.rs`、`crates/dim-agent-aimux/README.md`、`crates/dim-agent-aimux/src/driver.rs` |
| App Server、CLI、Web 与 ACP | `crates/dim-app-server/README.md`、`crates/dim-cli/README.md`、`crates/dim-acp/src/gateway.rs`、`docs/design/acp-gateway-implementation.md` |
| 静态门禁与装配图生成 | `xtask/src/main.rs`、`xtask/check/src/lib.rs`、`xtask/check/src/boundaries.rs`、`xtask/check/src/literal_main.rs` |

## 核心术语速查

| 术语 | 在 Dim 中的精确含义 |
| --- | --- |
| Timeline（时间总线） | 单条分支的有序持久化事件流，分配单调 `sequence`，区隔 `committed()` 与 `published()` 双水位 |
| Capability（能力） | 注入 `dispatcher` 或 `timeline` 的分支级状态机部件，受时间总线规则约束；不注入这两者的只是普通部件 |
| `before` 钩子 | 仅限进程内 Rust 在分支提交锁内同步运行的草稿变形链，不改历史、不阻写入、出错回退原草稿 |
| Data Face（数据面） | 在目录共享且在 `host_key(name)` 挂有 `HostDispatch` 的跨语言公开服务，参数、返回值与错误全为 serde 数据 |
| 起点（root）与默认分支（default branch） | 起点恒为 id `main` 且永不结束；默认分支是当前会话对话路由位置，由写在 `main` 上的 `branch/default` 决定 |
| 脱离工作（detached work） | 越过发起它的 turn 继续运行的异步工作（如 `BackgroundExec`、协作任务），归发起能力自持并用活动持有保护空闲关机 |
| `RecoverySnapshot` | 存于 `timeline_snapshots` 的可丢弃派生加速缓存（带 SHA-256 摘要），损坏或缺失时自动回退完整事实重放 |

## 常用核验命令

上游仓库核验（在 `arcships/dim` 仓库根目录执行）：

```sh
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
cargo run --locked -p xtask-check
cargo run --locked -p xtask -- check
```

本书教学模型与出版产物核验（在本书仓库根目录执行）：

```sh
python3 -m unittest discover -s examples -v
python3 scripts/make_diagrams.py
node wiki/build.mjs
python3 scripts/make_epub.py
python3 scripts/check_links.py
```
