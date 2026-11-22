# 上游源码快照记录

| 字段 | 值 |
| --- | --- |
| 上游仓库 | `https://github.com/arcships/dim` |
| 固定提交 | `88a3df3669405730cbee6bfd113a7f75cf520677` |
| 工作区版本 | `0.1.0-alpha.27` |
| 快照日期 | `2026-11-22` |
| 上游许可证 | MIT（内置第三方归属见上游 `NOTICE` 与 `licenses/`） |
| Rust 工具链 | `1.100`（Rust Edition `2024`） |
| 插件运行时基线 | `rutis = "0.23.0"`（Session 协议 5） |
| 模型协议库基线 | `aimux-* = "=0.6.1"` |
| 内嵌脚本引擎 | `rquickjs = "0.14"` |

## 实测工作区统计

- Cargo 工作区成员：28 个（`crates/` 下 23 个业务与协议 crate，`xtask` 与 `xtask/check` 共 2 个工具 crate，`tests/` 下 3 个集成与扩展测试 crate；`vendor/grok-build` 位于 `exclude`）。
- 真实装配服务目录（`docs/architecture/rust-agent/generated/catalog.md`）：111 个服务（process 58、session 31、branch 22），其中 75 个为跨语言 `data` 数据面，36 个为 `internal(reason)`。
- 分支级 Capability（注入 `dispatcher` 或 `timeline` 的部件）：20 个。
- 内置运行时插件包（`runtime/plugins/`）：3 个（`plugin-authoring`、`workflow`、`light-ocr`）。
- 架构决策记录（`docs/adr/`）：26 份（`001` 至 `026`）。

## 核验范围说明

本书全部路径、类型名、事件名、服务名与约束条款均直接核对自上述固定提交的源码、设计文档与生成目录。书中的 `examples/mini_dim.py` 为零依赖 Python 3.10+ 离线教学模型，用于验证时间总线、分支配额、审批状态机与重启重建等核心不变量，不代表对上游 Rust 二进制的线上压测或真实大模型评测。
