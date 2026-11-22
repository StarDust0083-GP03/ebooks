# Octop 架构解剖：从单机宿主到多副本运行时

基于 TencentCloud/Octop `v1.0.3b7`（提交 `ecd2cdbce3a54dee0f424f021d0a144d9669685e`，上游发布于 2026-11-18）的中文源码架构电子书。全书包含前言、17 章正文、附录、6 张主题自适应内联 SVG 架构图，以及一套零依赖 Python 教学模型与 18 个单元测试。

内容覆盖五层模块化单体边界、四套独立运行库（`octop-harness`、`octop-gateway`、`octop-memory`、`octop-browser`）、托管专家与 6 个本地回环 MCP、连续相同工具调用熔断、共享专家私有与 `contributed` 线程隔离、ADR 003 PostgreSQL 内容平面与 `history_v2` 退场、PostgreSQL 孤儿 checkpoint 只统计修复、Docker 宿主机挂载策略、知识库重排序与实体图谱、RFC 8414 MCP OAuth 发现、TOTP/Passkey MFA、Electron 45 桌面壳，以及 Helm 多副本与 90s/30s/25s 同行租约。

## 产物目录

- `wiki/index.html`：分章静态阅读站，支持全文搜索、字号调节与深浅主题切换。
- `wiki/book.html`：内嵌样式、脚本与 SVG 图表的离线合订本。
- `exports/octop-architecture.pdf`：A5 排版 PDF（同步分发至 `wiki/exports/`）。
- `exports/octop-architecture.epub`：EPUB 3 电子书（同步分发至 `wiki/exports/`）。
- `book/Octop架构解剖.md`：全书合并 Markdown 书稿。
- `sources/UPSTREAM.md` 与 `sources/inventory.json`：锁定到 `v1.0.3b7` 的 92 个源码路径、SHA-256 与行数清单。
- `examples/mini_host.py` 与 `examples/test_mini_host.py`：零依赖 SQLite 教学模型与 18 个单元测试。

## 本地构建与核验

```bash
bash scripts/build.sh
```

也可以按步骤单独执行：

```bash
python3 scripts/make_diagrams.py
node wiki/build.mjs
python3 scripts/make_epub.py
python3 scripts/check_links.py
python3 examples/mini_host.py
python3 -m unittest discover -s examples -v
```

本书为独立源码研究，非腾讯云官方文档。所有结论均来自固定提交的静态源码核对与离线教学模型测试，不代表真实生产环境压测或安全审计。
