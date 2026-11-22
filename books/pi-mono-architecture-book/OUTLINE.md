# 研究提纲与证据映射

本书以固定发布标签源码为唯一事实基线，覆盖 `v0.9.4`、`v0.99.2`、`v1.3.0`、`v1.4.0` 至 `v1.6.0`（提交 `faac585884ae2c013a6b1400f3e9e77f10b34bac`，2026-11-20），不混入标签之后的主分支开发提交。

## 章节与源码依据

1. 版本区间与差异规模：官方发布记录、固定提交与实测文件级差异统计。
2. 旧版架构（v0.9.4）：`packages/agent/src/agent.ts`、`packages/ai/src/agent/agent-loop.ts`、`session-manager.ts`。
3. 当前包地图（v1.6.0）：各包 `package.json`、`packages/schema/`、`docs/how-pi-works.md`。
4. 请求生命周期与会话：`docs/sdk.md`、`docs/session-format.md`、`docs/compaction.md`、`docs/sessions.md`、`packages/ai/src/cache-warming.ts`。
5. Code Mode 信息模型：`docs/codemode.md`、`packages/codemode/README.md`。
6. 沙箱与校验调用链：`extensions/codemode/tool.ts`、`execute.ts`、`packages/codemode`、`packages/ai/src/utils/validation.ts`。
7. 工具发现与 MCP：`docs/mcp.md`、`docs/settings.md`、`reachesTools`、多语种检索与 `$ref` 索引。
8. 返回值、并发与状态：结果适配、`codemode-store`、worker 排队准入与 `pi.exec()`。
9. 扩展、流式解析、TUI 渲染缓存与模型：`streaming-json.ts`、`tui.ts`、`assistant-message.ts`、`tool-execution.ts`、`packages.md`、`cli.md`、`catalog/families/openai.ts`、`agent-session-runtime.ts`。
10. v0.99.2 到 v1.6.0 增量：各包 `CHANGELOG.md` 与六处高影响迁移核对点。
11. 独立、云端与持久执行：`packages/codemode`、`packages/durable`、`packages/env`。
12. 迁移清单与本地实验：v1.6.0 检查表、`examples/pi_runtime_model.py`（20 项单元测试）、`examples/run-checks.mjs`、`examples/sdk-codemode-mcp.ts`。

## 架构图（13 张）

旧新分层对照、旧运行链、当前运行时、普通 CLI 包依赖（含 `pi-schema`）、请求时序、信息流对比、沙箱边界、工具发现、分支 `store`、流式解析与 TUI 只读渲染缓存、三种部署方式、Durable 确定性重放、Durable 与 `env` 包关系。全部使用主题自适应静态 SVG，同时服务网页、PDF 与 EPUB 3。

## 核查事实底线

- `v0.9.x` 与 `v0.99.x` 严格区分，Code Mode 始于 `v0.99.0`。
- 普通 CLI 直接依赖五个内部包，`pi-schema` 与 `telemetry` 位于底层，Durable、`env`、`chord` 属于独立运行链。
- `v1.5.0` 的 `pi-schema` 校验统一了 JSON Schema 宽松转换，不再执行 TypeBox 非 JSON Schema 特性。
- `v1.5.0` 的 `StreamingJsonParser` 采用线性 `append()` 与开路径拷贝信用额度；`v1.6.0` 的 `Container.render()` 返回 `readonly string[]` 并复用未变子树的缓存引用，调用方修改前必须先拷贝。
- `gpt-6.2-sol` 与 `gpt-6.2-astra` 拒绝 `none`/`minimal` 思考强度，不支持 thinking `off`。
- 脚本失败不提交 `store`，但外部文件系统副作用不会回滚；JS 沙箱不等于 OS 权限沙箱。
