# 构建验证记录

日期：2026-11-22。本版比较端点：**v1.6.0**，固定提交 `faac585884ae2c013a6b1400f3e9e77f10b34bac`（上游发布于 2026-11-20），保留 `v0.9.4`、`v0.99.2`、`v1.3.0` 与 `v1.4.0` 对照。

## 已执行验证

- `python3 make_diagrams.py`：生成 13 张主题自适应 SVG，内置文字宽度与边界断言，无标签裁剪或越界。
- `python3 build_book.py`：生成 `index.html`、`book.zh-CN.md` 与 `pi-architecture.zh-CN.epub`（12 章、13 张内嵌 SVG、40 条固定标签来源索引，EPUB 3 对含 `<svg` 的 `book.xhtml` 显式声明 `properties="svg"`）。
- `chromium --headless=new --disable-gpu --no-sandbox --no-pdf-header-footer --print-to-pdf=...`：导出 A4 排版版 `pi-architecture.zh-CN.pdf`。
- `python3 -m unittest discover -s examples -v`：20 项 Python 3.10+ 单元测试全部通过，覆盖树形会话投影与截断配对、Code Mode `pi-schema` 转换与 worker FIFO 排队、`StreamingJsonParser` 增量解析与拷贝信用、TUI `Container` 只读缓存与 `argsPending` 按帧合并、以及 MCP exposure 与多语种 `reachesTools` 发现。
- `node examples/run-checks.mjs`：6 个 JavaScript 教学脚本全部通过。
- `python3 verify_book.py`：验证 12 章、13 张可访问 SVG、零 em-dash/en-dash、唯一 ID、相对本地链接、EPUB 3 XML 与 `properties="svg"`、教学代码一致性及 PDF 文件头和体积。
- `check-punctuation.sh --lang zh`：全部章节与 `README.md` 标点检查通过。

## 未执行范围

- 上游仓库完整 Vitest 测试套件与真实 provider 集成测试。
- 付费模型在线调用、真实图片分类器服务或真实 MCP 服务器联调。
- Cloudflare Workers、Durable Object、SSH / `pi-env` 远程部署。
- 沙箱安全审计或权限绕过渗透测试。
