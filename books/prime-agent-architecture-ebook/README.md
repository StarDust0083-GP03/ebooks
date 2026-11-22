# Prime Agent 架构解剖：Rust 宿主与自改进 RLM 运行时

一本源码驱动的中文架构电子书，拆解 `PrimeIntellect-ai/prime-agent`（v0.10.5，提交 `783acb5d72a9b23f97fa4a793a67d421b3ec402d`，采集日期 2026-11-21）。

全书包含前言、13 章正文与 1 份源码附录，配 6 张随主题自适应的内联 SVG 架构图，以及一套零第三方依赖的 Python 3.10+ 教学模型（19 个单元测试）。

## 交付产物

- 多页静态阅读站：`wiki/index.html`、`wiki/chapters/*.html`、`wiki/search.html`
- 离线单文件合订本：`wiki/book.html`（内联样式与脚本，锚点按章隔离，零重复 ID）
- 合并全文 Markdown：`book/PrimeAgent架构解剖.md`
- EPUB 3 电子书：`exports/prime-agent-architecture.epub`
- A5 排版打印版 PDF：`exports/prime-agent-architecture.pdf`
- 可运行教学模型：`examples/mini_prime_agent.py` 与 `examples/test_mini_prime_agent.py`

## 本地构建与核验

```bash
python3 scripts/make_diagrams.py
node wiki/build.mjs
python3 scripts/make_epub.py
chromium --headless=new --disable-gpu --no-sandbox --no-pdf-header-footer \
  --print-to-pdf=exports/prime-agent-architecture.pdf wiki/book.html
mkdir -p wiki/exports
cp exports/prime-agent-architecture.pdf exports/prime-agent-architecture.epub wiki/exports/
python3 scripts/check_links.py
python3 -m unittest discover -s examples -v
```

## 研究边界声明

本书属于独立静态源码分析与离线教学实验，非 Prime Intellect 官方文档。所有路径、符号、常量与行为均核验自固定快照 `783acb5d72a9b23f97fa4a793a67d421b3ec402d`，不代表真实计费模型压测结论。
