# 验证记录

核对日期：2026-11-22。上游快照：`NirDiamant/RAG_Techniques` 提交 `2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6`（2026-11-02）。

## 自动化构建与校验结果

- `python3 scripts/make_diagrams.py`：生成 6 张 540pt viewBox 主题自适应内联 SVG 架构图，全部通过字符宽度边界校验，零文本裁切。
- `node wiki/build.mjs`：生成 19 个章节页、目录页、全文搜索页、`wiki/book.html`、`ebook.html` 与 `book/RAG工程实战.md`。
- `python3 scripts/make_epub.py`：生成 `exports/rag-techniques-from-scratch.epub`（95,254 字节，同步至 `RAG工程实战.epub`），19 章 XHTML 全部通过 XML 解析，6 个含图章节均声明 `properties="svg"`。
- Chromium Headless PDF 导出：生成 `exports/rag-techniques-from-scratch.pdf`（3,628,077 字节，有效 `%PDF` 文件头），并已同步到 `wiki/exports/`。
- `python3 scripts/check_links.py`：22 个 `wiki/` 页面全部零重复 ID，所有相对链接与锚点均留在 `wiki/` 内部且目标存在，PDF/EPUB 导出完整。
- `python3 wiki/verify.py`：23 个 HTML 页面、6 张 SVG、47 份 Notebook + 21 份脚本元数据、EPUB 3 包装与 SVG 属性、PDF 及零破折号检查全部通过。
- `python3 -m unittest discover -s examples -v`：20 项离线单元测试全部通过。
- `python3 examples/evaluate.py`：6 题 4 组配置离线回归完成，结果保存于 `sources/offline-evaluation.json`。
- 中文标点门禁（`check-punctuation.sh --lang zh`）：全部 19 篇章节与 `README.md` 均返回 `punctuation: ok`，正文中零 em-dash（U+2014）与 en-dash（U+2013）。

## 验证边界

- 离线测试覆盖词面检索、权限与版本前置过滤、块感知切分、离线代理映射、有界纠错状态机与打包预算，不包含远程大语言模型、语义 embedding、Cross-Encoder 或 OCR 托管接口实测。
- EPUB 完成了压缩存储顺序、XML 合法性、内部资源与锚点、OPF spine 及 `properties="svg"` 结构校验，未在全部实体商业阅读器上逐台实机验证。
- 外部 GitHub 与 arXiv 链接已保留原始 URL 未被改写，未在无网沙箱环境中发起在线存活探测。
