# 源码驱动技术电子书书架

集中托管全部 13 本源码驱动中文技术电子书的统一仓库与 GitHub Pages 阅读站。每本书保留各自的固定上游快照、Markdown 书稿、内联 SVG 架构图、离线单文件合订本、EPUB 3 / PDF 导出以及零依赖教学实现代码。

- 统一在线阅读书架：<https://stardust0083-gp03.github.io/ebooks/>

## 收录书目

### Agent 运行时架构

| 书名 | 上游项目 | 固定快照 | 目录 |
| --- | --- | --- | --- |
| **MCP 2027：从远程工具调用到 Agent 运行时协议** | [philschmid.de/mcp-2027](https://www.philschmid.de/mcp-2027) / [modelcontextprotocol](https://github.com/modelcontextprotocol/modelcontextprotocol) | `2026-07-28` (`2bbace55`) | `books/mcp-2027-architecture-ebook/` |
| **Dim 架构解剖** | [arcships/dim](https://github.com/arcships/dim) | `0.1.0-alpha.27` (`88a3df36`) | `books/dim-architecture-ebook/` |
| **Prime Agent 架构解剖** | [PrimeIntellect-ai/prime-agent](https://github.com/PrimeIntellect-ai/prime-agent) | `v0.10.5` (`783acb5d`) | `books/prime-agent-architecture-ebook/` |
| **Infinity 架构解剖** | [hydro-project/infinity](https://github.com/hydro-project/infinity) | `e1a3e4b3` | `books/infinity-architecture-ebook/` |
| **Pi 架构演进** | [earendil-works/pi](https://github.com/earendil-works/pi) | `v1.6.0` (`faac5858`) | `books/pi-mono-architecture-book/` |

### 沙箱与多 Agent 平台

| 书名 | 上游项目 | 固定快照 | 目录 |
| --- | --- | --- | --- |
| **OpenSandbox 架构解剖** | [opensandbox-group/OpenSandbox](https://github.com/opensandbox-group/OpenSandbox) | `v1.3.0-rc.2` (`45043e7f`) | `books/opensandbox-architecture-ebook/` |
| **Octop 架构解剖** | [TencentCloud/Octop](https://github.com/TencentCloud/Octop) | `v1.0.3b7` (`ecd2cdbc`) | `books/octop-architecture-ebook/` |
| **Claude Code 游戏工作室** | [Donchitos/Claude-Code-Game-Studios](https://github.com/Donchitos/Claude-Code-Game-Studios) | `v1.3.0` (`abc0de79`) | `books/claude-code-game-studios-ebook/` |

### AI 工程与基础专题

| 书名 | 上游项目 | 状态 | 目录 |
| --- | --- | --- | --- |
| **RAG 工程实战** | [NirDiamant/RAG_Techniques](https://github.com/NirDiamant/RAG_Techniques) | `2fd7bd0f` (47 Notebooks) | `books/rag-techniques-from-scratch/` |
| **Agent Memory 从零到生产** | 综合专题 | 2026-11 重编版 | `books/agent-memory-from-scratch/` |
| **LangChain × LangGraph** | [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) | 重写配图版 | `books/langchain-langgraph-from-scratch/` |
| **AI Infra 从一张 GPU 到生产集群** | 综合专题 | 重写配图版 | `books/ai-infra-from-scratch/` |
| **AI Engineer 实战手册** | [AI-Engineer-from-scrach](https://github.com/StarDust0083-GP03/AI-Engineer-from-scrach) | 重写配图版 | `books/ai-engineer-from-scratch/` |

## 本地构建与校验

```bash
python3 scripts/build_bookshelf.py --verify
python3 -m http.server 8000 --directory _site --bind 127.0.0.1
```

推送到 `main` 分支后，`.github/workflows/pages.yml` 会自动聚合全部 13 本书并部署至 GitHub Pages。

## 许可

本仓库原创正文、架构图与教学代码采用 MIT 许可；各上游开源项目的代码与文档版权归其原作者所有。
