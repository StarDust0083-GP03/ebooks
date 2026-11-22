# Dim 架构解剖：时间总线、分支路由与全插件运行时

这是一本基于上游源码快照编写的中文架构技术书，拆解 `arcships/dim`（Rust 事件驱动时间线与全插件 Agent v4 运行时 + `rutis 0.23.0`）的核心设计与工程边界。

- 上游仓库：`https://github.com/arcships/dim`
- 固定提交：`88a3df3669405730cbee6bfd113a7f75cf520677`（工作区版本 `0.1.0-alpha.27`）
- 快照日期：`2026-11-22`
- 全书篇幅：前言 + 13 章正文 + 附录 A（共 15 节），附 6 幅主题适配内联 SVG 架构图与 18 个零依赖 Python 不变量单元测试

## 目录结构

| 路径 | 说明 |
| --- | --- |
| `book/SUMMARY.md`、`book/chapters/*.md` | 全书 15 节 Markdown 源码 |
| `book/Dim架构解剖.md` | 合并后的单文件完整 Markdown |
| `wiki/` | 多页静态阅读站（`index.html`、`chapters/*.html`、`search.html`）与离线单文件合订本（`wiki/book.html`） |
| `wiki/assets/diagrams/*.svg` | 6 幅内联架构图，由 `scripts/make_diagrams.py` 生成 |
| `exports/dim-architecture.pdf`、`exports/dim-architecture.epub` | 打印级 PDF 与 EPUB 3 电子书（同步副本位于 `wiki/exports/`） |
| `examples/mini_dim.py`、`examples/test_mini_dim.py` | 零依赖 Python 3.10+ 教学模型与 18 个单元测试 |
| `sources/UPSTREAM.md`、`RESEARCH_NOTES.md` | 上游提交锁定记录与实测源码统计 |

## 本地构建与核验

```sh
python3 scripts/make_diagrams.py
node wiki/build.mjs
python3 scripts/make_epub.py
chromium --headless=new --disable-gpu --no-sandbox --no-pdf-header-footer \
  --print-to-pdf=exports/dim-architecture.pdf wiki/book.html
mkdir -p wiki/exports
cp exports/dim-architecture.pdf exports/dim-architecture.epub wiki/exports/
python3 scripts/check_links.py
python3 -m unittest discover -s examples -v
```

也可以直接运行 `bash scripts/build.sh` 完成全量构建与校验。

## 声明与许可

本书属于独立源码分析，非 `arcships/dim` 官方文档。本书正文、图表脚本与教学代码以 [MIT 协议](LICENSE) 发布，上游归属说明见 [NOTICE](NOTICE)。
