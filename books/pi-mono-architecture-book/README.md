# Pi 架构演进中文电子书

对照 **v0.9.4 / v0.99.2 → v1.6.0**（保留 v1.4.0 与 v1.3.0 作为历史参照），从源码拆解 Pi 的 Agent 循环、树形会话、Code Mode、MCP、零 `eval` 校验库 `pi-schema`、流式工具解析、TUI 只读渲染缓存以及实验性 Durable 运行时。

## 阅读与下载入口

- [网页版电子书](index.html)：中文目录、12 章正文、13 张主题自适应 SVG 架构图与交互式载荷演示。
- [PDF 排版版](pi-architecture.zh-CN.pdf)：A4 打印版，保留可点击目录与来源链接。
- [EPUB 3 电子书](pi-architecture.zh-CN.epub)：内嵌 SVG 并声明 `properties="svg"`，无外部网络依赖。
- [Markdown 合并原稿](book.zh-CN.md)：便于全文检索与二次排版。
- [官方源码标签 v1.6.0](https://github.com/earendil-works/pi/tree/v1.6.0)：固定提交 `faac585884ae2c013a6b1400f3e9e77f10b34bac`（上游发布于 2026-11-20）。

## 全书目录（12 章）

1. 先把版本区间划清楚（v0.9.4、v0.99.2、v1.4.0 到 v1.6.0 与文件级差异解读）。
2. v0.9.4 当年是怎么跑起来的（Agent、transport、位于 `ai` 包的循环与线性会话）。
3. v1.6.0 的包地图与三条边界（普通 CLI 依赖、`pi-schema` 位置与三条工程边界）。
4. 一次请求的完整生命周期（树形会话、上下文投影、压缩预算、流式 `read`、空闲缓存预热与 Anthropic 消息线程）。
5. Code Mode 到底改变了什么（执行侧控制流、字段筛选与字符账）。
6. 拆开沙箱，顺着源码追一次调用（QuickJS/WASM、worker 桥、`ctx.executeTool()` 与 `pi-schema`）。
7. 工具太多时，如何按需发现而不塞满提示（MCP exposure、多语种 CJK 与 `$ref` 搜索、`reachesTools`）。
8. 返回值、失败处理、并发与状态边界（`exit_code`、`isError`、分支 `store`、worker 排队与 `pi.exec()`）。
9. 扩展、渲染缓存、模型与集成界面（`StreamingJsonParser`、TUI `Container` 只读缓存破坏性变更、扩展更新日志、GPT-6.2 与 Qwen Token Plan、`pi auth` 与自更新命令）。
10. 从 v0.99.2 到 v1.6.0，到底演进了什么（分版本增量与六处高影响迁移核对）。
11. 独立沙箱、云端隔离与 Durable 重放（独立 `pi-codemode`、Cloudflare 独立 Worker、确定性重放与 `pi-env`）。
12. 迁移路线、动手实验与核对工具（v1.6.0 检查表、Python 教学模型与 20 项单元测试、Node 模拟脚本与术语表）。

## 架构图清单（13 张 SVG）

`diagrams/` 中保存全部可独立打开的主题自适应 SVG：

- `architecture-comparison.svg`：v0.9.4 与 v1.6.0 普通 CLI 分层对照。
- `old-stack.svg`：v0.9.4 运行链。
- `current-stack.svg`：v1.6.0 普通 CLI 运行时地图。
- `cli-package-dependencies.svg`：v1.6.0 普通 CLI 直接与传递内部依赖（含 `pi-schema`）。
- `request-sequence.svg`：Code Mode 跨层调用时序。
- `information-flow.svg`：普通调用与执行侧筛选信息流对比。
- `sandbox-boundary.svg`：主线程、worker、WASM 与工具校验边界。
- `discovery.svg`：direct、codemode、deferred 三条工具发现路径。
- `branch-store.svg`：分支相对 `store` 重放与外部文件分离。
- `streaming-and-render-cache.svg`：v1.5.0 增量流式参数解析与 v1.6.0 TUI 只读渲染缓存链路。
- `deployment.svg`：Node/Bun、Cloudflare 独立 Worker 与 inline 三种部署方式。
- `durable-replay.svg`：Durable journal 与确定性重放流程。
- `durable-package-dependencies.svg`：Durable、`env`、`chord` 与底层共享包关系。

## 源码基线与本地复现

核查日期：2026-11-22。详细版本提交与区间统计见 [sources/UPSTREAM.md](sources/UPSTREAM.md)，40 条官方源码引用见 [sources/source-index.json](sources/source-index.json)。本版不再向仓库追加大体积源码 `.zip` 归档文件。

运行全部教学验证与构建检查：

```sh
python3 make_diagrams.py
python3 build_book.py
chromium --headless=new --disable-gpu --no-sandbox --no-pdf-header-footer \
  --print-to-pdf="$PWD/pi-architecture.zh-CN.pdf" "file://$PWD/index.html"
python3 verify_book.py
python3 -m unittest discover -s examples -v
node examples/run-checks.mjs
```

验证范围与边界记录在 [QA.md](QA.md)。本地教学模型和单元测试均在离线模拟环境下运行，不调用付费模型、不部署云端或 SSH 环境，也不替代沙箱安全审计。
