# RAG 工程实战

从文档问答到可验证的检索系统。

本书以 [NirDiamant/RAG_Techniques](https://github.com/NirDiamant/RAG_Techniques) 提交 `2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6`（2026-11-02）为技术地图，围绕企业研发知识库与支付规范助手这条工程主线独立编写。全书包含 16 章正文、3 份附录、6 张主题自适应内联 SVG 架构图和一套零依赖 Python 教学实现，完整索引上游 47 份 Notebook 与 21 份可运行脚本。

## 阅读与下载

- [多页阅读站](wiki/index.html)：支持全文搜索、字号调节、深浅色主题与目录导航
- [离线合订本（单文件 HTML）](wiki/book.html)（根目录镜像：[ebook.html](ebook.html)）：内联样式与脚本，无运行时网络依赖
- [PDF 电子书](exports/rag-techniques-from-scratch.pdf)：A5 印刷排版，含内联矢量架构图
- [EPUB 3 电子书](exports/rag-techniques-from-scratch.epub)（根目录镜像：[`RAG工程实战.epub`](RAG工程实战.epub)）：声明 `properties="svg"`，兼容主流电子书阅读器
- [全书合并 Markdown](book/RAG工程实战.md) 与 [分章目录](book/SUMMARY.md)

## 全书结构

| 章节范围 | 核心内容 |
| --- | --- |
| 00 至 02 | 开篇故障场景、五类需求边界、写入链与查询链架构 |
| 03 至 04 | 解析验收、Mistral OCR 4.1 三段对照、块感知切分与上下文标题 |
| 05 至 08 | 离线基线、LangChain 1.x / LlamaIndex 0.14 迁移、RRF 身份对齐、HyDE/HyPE 与证据打包 |
| 09 | 固定题集、双阶段 Recall/完整性、DeepEval `GEval` logprobs 陷阱与无密钥 CI |
| 10 至 13 | CRAG 有界三态纠错、层次摘要与 RAPTOR、图谱检索与多模态 Agent 护栏 |
| 14 至 15 | 全路径权限、提示注入、原子快照、模型停服漂移与一次请求串起全书 |
| 附录 A 至 C | 47 份 Notebook 与 21 份脚本映射、源码快照与术语、完整离线实现 |

## 运行离线教学代码

仅需 Python 3.10 以上，无需第三方依赖或模型 API Key：

```bash
python3 examples/mini_rag.py
python3 examples/mini_rag.py --query "回调处理顺序" --mode bm25 --k 1 --window 1
python3 examples/mini_rag.py --query xyzunfindable
python3 examples/evaluate.py
python3 -m unittest discover -s examples -v
```

`examples/mini_rag.py` 实现了 BM25、词频余弦、按 ID 对齐的 RRF、OCR 块感知切分（`block_aware_split`）、离线 HyPE 问题代理（`PromptIndex`）、CRAG 有界纠错路由（`crag_route`）、前置权限与版本过滤、邻域扩展和含标签预算打包，并附 20 项单元测试。程序只输出检索与证据轨迹，不调用大模型生成自然语言答案。

## 构建站点与导出

需要 Node.js 22+、Python 3.10+ 与 Chromium（用于生成 PDF）：

```bash
bash scripts/build.sh
```

脚本依次生成 6 张 SVG 架构图、多页站与单文件合订本、EPUB 3、A5 PDF，并将 PDF 与 EPUB 同步到 `wiki/exports/`，最后运行链接检查（`scripts/check_links.py`）、结构核验（`wiki/verify.py`）和全部 20 项单元测试。仓库内置 `.github/workflows/pages.yml`，推送到 `main` 即可自动构建并发布到 GitHub Pages。

## 来源、边界与许可

- 上游源码固定到 `NirDiamant/RAG_Techniques` 提交 `2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6`（2026-11-02），本地核对日期 2026-11-22，详细变更记录见 [sources/UPSTREAM.md](sources/UPSTREAM.md) 与 [附录 B](book/chapters/appendix-sources.md)。
- 本书原创正文、架构图与 `examples/` 教学代码采用 [MIT License](LICENSE)。
- 上游 `NirDiamant/RAG_Techniques` 采用自定义非商业许可证，商业使用其受许可材料需原作者事先书面授权；本仓库仅作静态核对与路径引用，不分发或重新授权第三方材料。
