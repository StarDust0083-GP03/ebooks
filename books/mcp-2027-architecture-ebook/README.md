# MCP 2027：从远程工具调用到 Agent 运行时协议

一部基于一手规范快照与真实业务采用数据编写的中文架构电子书。以 Philipp Schmid 于 2026 年 11 月 26 日发布的文章《Why I'm Bullish on MCP for 2027》为切入点，逐层核对 Model Context Protocol 官方规范仓库（`2026-07-28` 正式版、`draft` 草案与全部官方扩展/SEP，固定提交 `2bbace557bbf2cb5d03d6f9b48905415cd7607b0`），完整拆解 MCP 如何从有状态远程工具接口演进为面向 2027 的无状态 Agent 运行时协议。

## 内容结构（前言 + 14 章 + 附录）

- **第 00 章** 前言：从狂热、退潮到 2027 拐点
- **第 01 章** 协议演进史：从 `2024-11-05` 到 `2026-07-28`（会话移除与 `SEP-2577` 弃用取舍）
- **第 02 章** 无状态内核与 HTTP 标准化（`SEP-2567` / `SEP-2575` / `SEP-2243` / `SEP-2549`）
- **第 03 章** 核心原语与强类型契约（Tools / Resources / Prompts、`outputSchema`、`structuredContent`、`SEP-1303`）
- **第 04 章** Code Mode：终结上下文膨胀与 N 轮串行调用
- **第 05 章** 多往返请求（MRTR）与无状态追问（`SEP-2322` 与 `SEP-1036`）
- **第 06 章** 身份与授权体系：OAuth 2.1、CIMD 与企业 IdP（RFC 9728 / `SEP-991` / `SEP-990` / `SEP-1046` / `SEP-2468`）
- **第 07 章** 扩展机制与治理边界（`SEP-2133` 与 `SEP-3392`）
- **第 08 章** Skills 进入 MCP：`io.modelcontextprotocol/skills`（`SEP-2640`）
- **第 09 章** 长程异步执行：MCP Tasks（`io.modelcontextprotocol/tasks`，`SEP-2663`）
- **第 10 章** 常驻与多人协作代理：MCP Events（`io.modelcontextprotocol/events`，`SEP-3415`）
- **第 11 章** 产品原生智能体化：MCP Apps（`io.modelcontextprotocol/ui`，`SEP-1865`）
- **第 12 章** 未解难题：发现、可观测性与纵深安全（Registry、Server Cards `SEP-2127`、意图遥测与 `SEP-1024`）
- **第 13 章** 超越远程工具：把 Harness 做成 MCP Server 与架构反模式
- **第 14 章** 动手验证：零依赖 Python MCP 2027 教学运行时与 21 项单元测试
- **附录** 规范索引、关键 SEP 速查表与核验命令

## 交付物与本地验证

- **静态阅读站**：`wiki/index.html`、`wiki/chapters/*.html`、`wiki/search.html`
- **离线单文件版**：`wiki/book.html`（CSS/JS/SVG 全部内联）
- **电子书导出**：`exports/mcp-2027-architecture.epub`（EPUB 3）、`exports/mcp-2027-architecture.pdf`（印刷级 PDF）、`book/MCP2027架构与工程实战.md`
- **架构图**：`wiki/assets/diagrams/*.svg`（6 张主题自适应内联 SVG）
- **可执行教学模型**：`examples/mini_mcp_2027.py` 与 `examples/test_mini_mcp_2027.py`

```bash
# 运行 21 个零依赖协议不变量单元测试
python3 -m unittest discover -s examples -v

# 全量构建站点、图解、EPUB、PDF 与链接核验
bash scripts/build.sh
```

## 许可协议

本书正文与教学代码采用 MIT 协议发布（见 `LICENSE`），上游来源归属与研究边界说明见 `sources/UPSTREAM.md` 与 `NOTICE`。
