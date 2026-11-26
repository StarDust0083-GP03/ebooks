#!/usr/bin/env python3
"""Assemble all 12 ebooks into a single unified static bookshelf site under _site/."""

from __future__ import annotations

import argparse
import html
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
BOOKS_DIR = ROOT / "books"
SITE_DIR = ROOT / "_site"

CATALOG = [
    {
        "slug": "mcp-2027-architecture-ebook",
        "title": "MCP 2027：从远程工具调用到 Agent 运行时协议",
        "subtitle": "从 philschmid.de/mcp-2027 到 2026-07-28 无状态规范、Code Mode、Skills、Tasks、Events 与 MCP Apps",
        "category": "runtime",
        "category_label": "Agent 运行时架构",
        "upstream": "philschmid.de/mcp-2027 + modelcontextprotocol",
        "upstream_url": "https://www.philschmid.de/mcp-2027",
        "version": "2026-07-28 · 2bbace55",
        "updated": "2026-11-27",
        "stats": "16 节 · 6 幅 SVG · 21 项单测",
        "summary": "以 Philipp Schmid 的 2027 判断为引线，对照官方 2026-07-28 规范与 25 项核心 SEP，拆解无状态 HTTP 头路由、Code Mode 与强类型输出、MRTR 追问、OAuth 2.1/CIMD、Skills SHA-256 同步、Tasks、Events、MCP Apps 与 Harness 即 MCP Server。",
        "layout": "wiki",
        "pdf": "exports/mcp-2027-architecture.pdf",
        "epub": "exports/mcp-2027-architecture.epub",
    },
    {
        "slug": "dim-architecture-ebook",
        "title": "Dim 架构解剖",
        "subtitle": "Rust 事件驱动时间线、rutis 跨语言插件与分支路由",
        "category": "runtime",
        "category_label": "Agent 运行时架构",
        "upstream": "arcships/dim",
        "upstream_url": "https://github.com/arcships/dim",
        "version": "0.1.0-alpha.27 · 88a3df36",
        "updated": "2026-11-22",
        "stats": "15 节 · 6 幅 SVG · 18 项单测",
        "summary": "从 28 个 Rust Crate、Time Bus 只认序号的事实流、分支默认路由与配额保护，一路拆到 rutis 跨语言 Data Face、会话内自写插件热重载与 ADR 权限模型。",
        "layout": "wiki",
        "pdf": "exports/dim-architecture.pdf",
        "epub": "exports/dim-architecture.epub",
    },
    {
        "slug": "prime-agent-architecture-ebook",
        "title": "Prime Agent 架构解剖",
        "subtitle": "Rust 宿主与 Python RLM 递归编码代理运行时",
        "category": "runtime",
        "category_label": "Agent 运行时架构",
        "upstream": "PrimeIntellect-ai/prime-agent",
        "upstream_url": "https://github.com/PrimeIntellect-ai/prime-agent",
        "version": "v0.10.5 · 783acb5d",
        "updated": "2026-11-21",
        "stats": "15 节 · 6 幅 SVG · 19 项单测",
        "summary": "拆解 Rust TUI/宿主与 Python RLM 运行时的双层分工、持久 python_repl 内核与输出截断、rlm.inbox 消息队列、子代理注册表、技能自演化与安全边界。",
        "layout": "wiki",
        "pdf": "exports/prime-agent-architecture.pdf",
        "epub": "exports/prime-agent-architecture.epub",
    },
    {
        "slug": "infinity-architecture-ebook",
        "title": "Infinity 架构解剖",
        "subtitle": "面向原则化并发的 Rust Agent 运行时与 RAP 工具协议",
        "category": "runtime",
        "category_label": "Agent 运行时架构",
        "upstream": "hydro-project/infinity",
        "upstream_url": "https://github.com/hydro-project/infinity",
        "version": "e1a3e4b3",
        "updated": "2026-11-22",
        "stats": "15 节 · 6 幅 SVG · 19 项单测",
        "summary": "围绕 UC Berkeley Hydro 团队的 21 个 Rust Crate，讲清无阻塞切片、三阶段 HistoryManager、RAP 协议、LocalSet 与 DeferQueue、jj/git 堆叠写隔离沙箱及 Serverless 架构。",
        "layout": "wiki",
        "pdf": "exports/infinity-architecture.pdf",
        "epub": "exports/infinity-architecture.epub",
    },
    {
        "slug": "pi-mono-architecture-book",
        "title": "Pi 架构演进",
        "subtitle": "v0.9.4 / v0.99.2 到 v1.6.0：从工具循环到 Code Mode",
        "category": "runtime",
        "category_label": "Agent 运行时架构",
        "upstream": "earendil-works/pi",
        "upstream_url": "https://github.com/earendil-works/pi",
        "version": "v1.6.0 · faac5858",
        "updated": "2026-11-22",
        "stats": "12 章 · 13 幅 SVG · 20 项单测",
        "summary": "追踪 Pi 从早期线性会话演进到树形会话、QuickJS Code Mode、按需 MCP、pi-schema 校验、大工具调用增量流式解析与 v1.6.0 TUI 不可变渲染缓存的全过程。",
        "layout": "pi-flat",
        "pdf": "pi-architecture.zh-CN.pdf",
        "epub": "pi-architecture.zh-CN.epub",
    },
    {
        "slug": "opensandbox-architecture-ebook",
        "title": "OpenSandbox 架构解剖",
        "subtitle": "从一次创建请求到沙箱里的进程（v1.3.0-rc.2）",
        "category": "infra",
        "category_label": "沙箱与多 Agent 平台",
        "upstream": "opensandbox-group/OpenSandbox",
        "upstream_url": "https://github.com/opensandbox-group/OpenSandbox",
        "version": "v1.3.0-rc.2 · 45043e7f",
        "updated": "2026-11-22",
        "stats": "23 节 · 6 幅 SVG · 31 项单测",
        "summary": "沿着 POST /v1/sandboxes 走遍控制面、Docker/K8s/Fast Sandbox 三种后端、OSEP-0018 execd-as-init 默认拓扑、rootfsOnly 快照、BatchSandbox Completed 终态与 Credential Vault。",
        "layout": "wiki",
        "pdf": "exports/opensandbox-architecture.pdf",
        "epub": "exports/opensandbox-architecture.epub",
    },
    {
        "slug": "octop-architecture-ebook",
        "title": "Octop 架构解剖",
        "subtitle": "自托管多用户多 Agent 平台的模块化单体实践（v1.0.3b7）",
        "category": "infra",
        "category_label": "沙箱与多 Agent 平台",
        "upstream": "TencentCloud/Octop",
        "upstream_url": "https://github.com/TencentCloud/Octop",
        "version": "v1.0.3b7 · ecd2cdbc",
        "updated": "2026-11-22",
        "stats": "19 节 · 6 幅 SVG · 18 项单测",
        "summary": "拆解 Octop 的权限隔离、托管专家 ACP 会话与 6 组本地 MCP 桥、连续工具循环熔断、PostgreSQL 内容面与孤儿 checkpoint 清理、RFC 8414 MCP OAuth 与多副本租约集群。",
        "layout": "wiki",
        "pdf": "exports/octop-architecture.pdf",
        "epub": "exports/octop-architecture.epub",
    },
    {
        "slug": "claude-code-game-studios-ebook",
        "title": "Claude Code 游戏工作室",
        "subtitle": "从一堆提示词到一条可验收的流水线（v1.3.0）",
        "category": "infra",
        "category_label": "沙箱与多 Agent 平台",
        "upstream": "Donchitos/Claude-Code-Game-Studios",
        "upstream_url": "https://github.com/Donchitos/Claude-Code-Game-Studios",
        "version": "v1.3.0 · abc0de79",
        "updated": "2026-11-22",
        "stats": "19 节 · 6 幅 SVG · 17 项单测",
        "summary": "剥开 49 个角色、78 个技能与 15 条 Hook 的外壳，看清四层约束里哪几处真正硬拦截、5 级配置瀑布链、/prototype 先玩一把双轨入口与四态证据门禁。",
        "layout": "wiki",
        "pdf": "exports/claude-code-game-studios.pdf",
        "epub": "exports/claude-code-game-studios.epub",
    },
    {
        "slug": "rag-techniques-from-scratch",
        "title": "RAG 工程实战",
        "subtitle": "从文档问答到可验证的检索系统",
        "category": "engineering",
        "category_label": "AI 工程与基础专题",
        "upstream": "NirDiamant/RAG_Techniques",
        "upstream_url": "https://github.com/NirDiamant/RAG_Techniques",
        "version": "2fd7bd0f (47 Notebooks)",
        "updated": "2026-11-22",
        "stats": "19 节 · 6 幅 SVG · 20 项单测",
        "summary": "以企业研发知识库与支付规范助手为主线，覆盖 OCR 4.1 块感知切分、BM25 与 RRF 融合、HyDE/HyPE 查询路由、CRAG 纠错、LangChain 1.x / LlamaIndex 0.14 迁移与上线权限边界。",
        "layout": "wiki",
        "pdf": "exports/rag-techniques-from-scratch.pdf",
        "epub": "exports/rag-techniques-from-scratch.epub",
    },
    {
        "slug": "agent-memory-from-scratch",
        "title": "Agent Memory 从零到生产",
        "subtitle": "从最小记忆实现到 Mem0、Graphiti 与生产边界",
        "category": "engineering",
        "category_label": "AI 工程与基础专题",
        "upstream": "独立源码与论文综合",
        "upstream_url": "",
        "version": "2026-11 大白话重编版",
        "updated": "2026-11-22",
        "stats": "19 节 · 配套教学代码与单测",
        "summary": "拆解工作记忆、情节记忆、语义记忆与双时间知识图谱，对照 Mem0 与 Graphiti 的存储检索机制，补齐遗忘策略、冲突更新与生产边界。",
        "layout": "wiki",
        "pdf": "exports/agent-memory-from-scratch.pdf",
        "epub": "exports/agent-memory-from-scratch.epub",
    },
    {
        "slug": "langchain-langgraph-from-scratch",
        "title": "LangChain × LangGraph",
        "subtitle": "从工具调用到可恢复的状态图 Agent",
        "category": "engineering",
        "category_label": "AI 工程与基础专题",
        "upstream": "langchain-ai/langgraph",
        "upstream_url": "https://github.com/langchain-ai/langgraph",
        "version": "重写配图版",
        "updated": "2026-10-29",
        "stats": "20 节 · 内嵌架构图 · 离线示例",
        "summary": "从最小工具循环写起，一路拆到 StateGraph Reducer、Pregel 超步快照、Checkpointer 与 Store 分工、流式事件、人工审批中断恢复与 MCP 集成。",
        "layout": "wiki",
        "pdf": "",
        "epub": "",
    },
    {
        "slug": "ai-infra-from-scratch",
        "title": "AI Infra 从一张 GPU 到生产集群",
        "subtitle": "推理引擎、量化、分布式训练与批调度",
        "category": "engineering",
        "category_label": "AI 工程与基础专题",
        "upstream": "AI Infra 专题",
        "upstream_url": "",
        "version": "重写配图版",
        "updated": "2026-10-29",
        "stats": "22 节 · 19 幅图 · 实验脚本",
        "summary": "从显存账本、KV Cache、16/8/4 位量化与连续批处理讲起，延伸到梯度累积、Checkpoint 一致性与集群 Gang Scheduling。",
        "layout": "wiki",
        "pdf": "",
        "epub": "",
    },
    {
        "slug": "ai-engineer-from-scratch",
        "title": "AI Engineer 实战手册",
        "subtitle": "从工具调用、RAG、上下文裁剪到多 Agent 工程",
        "category": "engineering",
        "category_label": "AI 工程与基础专题",
        "upstream": "AI-Engineer-from-scrach",
        "upstream_url": "https://github.com/StarDust0083-GP03/AI-Engineer-from-scrach",
        "version": "重写配图版",
        "updated": "2026-10-29",
        "stats": "25 节 · 配图合订本",
        "summary": "面向工程落地的 AI 应用全栈手记，串联模型调用、证据链检索、消息组上下文裁剪、评估回归与循环工程。",
        "layout": "flat",
        "pdf": "",
        "epub": "",
    },
]

PI_ASSETS = [
    "index.html",
    "book.zh-CN.md",
    "pi-architecture.zh-CN.epub",
    "pi-architecture.zh-CN.pdf",
    "reader-polish.css",
    "reader-enhancements.js",
    "diagrams",
]


def stage_book(entry: dict) -> dict:
    slug = entry["slug"]
    src_root = BOOKS_DIR / slug
    dst_root = SITE_DIR / slug
    if not src_root.exists():
        raise FileNotFoundError(f"Missing book directory: {src_root}")

    if dst_root.exists():
        shutil.rmtree(dst_root)
    dst_root.mkdir(parents=True, exist_ok=True)

    layout = entry["layout"]
    if layout == "wiki":
        wiki_src = src_root / "wiki"
        if not wiki_src.exists():
            raise FileNotFoundError(f"Missing wiki directory: {wiki_src}")
        shutil.copytree(
            wiki_src,
            dst_root,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("node_modules", "build.mjs", "verify.py", "package.json", "package-lock.json", "__pycache__"),
        )
        exports_src = src_root / "exports"
        if exports_src.exists():
            exports_dst = dst_root / "exports"
            exports_dst.mkdir(parents=True, exist_ok=True)
            for item in exports_src.iterdir():
                if item.is_file() and item.suffix.lower() in {".pdf", ".epub"}:
                    shutil.copy2(item, exports_dst / item.name)
    elif layout == "pi-flat":
        for name in PI_ASSETS:
            src_item = src_root / name
            if src_item.is_dir():
                shutil.copytree(src_item, dst_root / name, dirs_exist_ok=True)
            elif src_item.is_file():
                shutil.copy2(src_item, dst_root / name)
    elif layout == "flat":
        shutil.copytree(
            src_root,
            dst_root,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.py", "node_modules"),
        )
        if slug == "ai-engineer-from-scratch":
            gh_base = "https://github.com/StarDust0083-GP03/AI-Engineer-from-scrach/blob/rewrite/agent-ebooks/"
            for html_file in dst_root.glob("*.html"):
                text = html_file.read_text(encoding="utf-8")
                updated = re.sub(r'href="\.\./([^"]+)"', lambda m: f'href="{gh_base}{m.group(1)}"', text)
                if updated != text:
                    html_file.write_text(updated, encoding="utf-8")
    else:
        raise ValueError(f"Unknown layout: {layout}")

    info = dict(entry)
    info["has_book_html"] = (dst_root / "book.html").exists()
    info["pdf_href"] = f"{slug}/{entry['pdf']}" if entry["pdf"] and (dst_root / entry["pdf"]).exists() else ""
    info["epub_href"] = f"{slug}/{entry['epub']}" if entry["epub"] and (dst_root / entry["epub"]).exists() else ""
    info["read_href"] = f"{slug}/index.html"
    info["book_href"] = f"{slug}/book.html" if info["has_book_html"] else ""
    return info


def render_index(books: list[dict]) -> str:
    categories = [
        ("all", f"全部 ({len(books)})"),
        ("runtime", f"Agent 运行时架构 ({sum(1 for b in books if b['category'] == 'runtime')})"),
        ("infra", f"沙箱与多 Agent 平台 ({sum(1 for b in books if b['category'] == 'infra')})"),
        ("engineering", f"AI 工程与基础专题 ({sum(1 for b in books if b['category'] == 'engineering')})"),
    ]
    filter_buttons = "".join(
        f'<button class="filter-btn{" active" if key == "all" else ""}" data-filter="{key}">{html.escape(label)}</button>'
        for key, label in categories
    )

    cards = []
    for idx, b in enumerate(books, 1):
        links = [f'<a class="link-primary" href="{b["read_href"]}">在线阅读</a>']
        if b["book_href"]:
            links.append(f'<a href="{b["book_href"]}">单文件合订本</a>')
        if b["epub_href"]:
            links.append(f'<a href="{b["epub_href"]}">EPUB</a>')
        if b["pdf_href"]:
            links.append(f'<a href="{b["pdf_href"]}">PDF</a>')

        upstream_html = (
            f'<a class="upstream" href="{html.escape(b["upstream_url"])}" target="_blank" rel="noopener">{html.escape(b["upstream"])}</a>'
            if b["upstream_url"]
            else f'<span class="upstream">{html.escape(b["upstream"])}</span>'
        )
        search_blob = html.escape(
            f"{b['title']} {b['subtitle']} {b['upstream']} {b['summary']} {b['version']} {b['category_label']}".lower()
        )
        cards.append(
            f'''<article class="book-card" data-category="{b["category"]}" data-search="{search_blob}">
  <div class="card-top">
    <span class="book-no">{idx:02d}</span>
    <span class="book-cat">{html.escape(b["category_label"])}</span>
    <span class="book-date">{html.escape(b["updated"])}</span>
  </div>
  <h2><a href="{b["read_href"]}">{html.escape(b["title"])}</a></h2>
  <p class="book-sub">{html.escape(b["subtitle"])}</p>
  <p class="book-summary">{html.escape(b["summary"])}</p>
  <dl class="book-meta">
    <div><dt>上游</dt><dd>{upstream_html}</dd></div>
    <div><dt>快照</dt><dd><code>{html.escape(b["version"])}</code></dd></div>
    <div><dt>规模</dt><dd>{html.escape(b["stats"])}</dd></div>
  </dl>
  <div class="card-actions">{"".join(links)}</div>
</article>'''
        )

    return f'''<!doctype html>
<html lang="zh-CN" data-theme="auto">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="description" content="StarDust0083-GP03 源码驱动中文技术电子书统一书架：涵盖 Agent 运行时、沙箱、多 Agent 平台、RAG、Memory 与 AI Infra。">
<title>源码驱动技术电子书书架 · StarDust0083-GP03</title>
<style>
:root {{
  --paper: #f8f5ef;
  --paper-2: #f1ece2;
  --ink: #1b1a17;
  --muted: #5c564c;
  --line: #dcd4c6;
  --accent: #8f3d1e;
  --forest: #245744;
  --font-sans: "IBM Plex Sans", "Noto Sans CJK SC", "PingFang SC", system-ui, -apple-system, sans-serif;
  --font-serif: "Noto Serif CJK SC", "Songti SC", Georgia, serif;
  --font-mono: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
}}
@media (prefers-color-scheme: dark) {{
  html[data-theme="auto"] {{
    --paper: #141413;
    --paper-2: #1c1b19;
    --ink: #ebe6dc;
    --muted: #a49c8e;
    --line: #302d27;
    --accent: #d9825b;
    --forest: #6ab597;
  }}
}}
html[data-theme="dark"] {{
  --paper: #141413;
  --paper-2: #1c1b19;
  --ink: #ebe6dc;
  --muted: #a49c8e;
  --line: #302d27;
  --accent: #d9825b;
  --forest: #6ab597;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font-family: var(--font-sans);
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}}
a {{ color: inherit; }}
.topbar {{
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.85rem clamp(1.2rem, 4vw, 3rem);
  background: var(--paper);
  border-bottom: 1px solid var(--line);
}}
.brand {{
  display: flex;
  align-items: center;
  gap: 0.65rem;
  text-decoration: none;
  font-weight: 600;
  letter-spacing: 0.01em;
}}
.brand-mark {{
  display: inline-grid;
  place-items: center;
  width: 1.7rem;
  height: 1.7rem;
  border: 1px solid var(--ink);
  font-family: var(--font-mono);
  font-size: 0.82rem;
}}
.top-tools {{
  display: flex;
  align-items: center;
  gap: 0.6rem;
}}
.top-btn {{
  padding: 0.35rem 0.7rem;
  border: 1px solid var(--line);
  background: var(--paper-2);
  color: var(--ink);
  font: inherit;
  font-size: 0.85rem;
  text-decoration: none;
  cursor: pointer;
}}
.top-btn:hover {{ border-color: var(--ink); }}
.shell {{
  max-width: 1180px;
  margin: 0 auto;
  padding: clamp(2rem, 5vw, 3.6rem) clamp(1.2rem, 4vw, 3rem) 4rem;
}}
.hero {{
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(240px, 0.8fr);
  gap: 2rem;
  padding-bottom: 2rem;
  border-bottom: 1px solid var(--line);
}}
.kicker {{
  margin: 0 0 0.6rem;
  font-family: var(--font-mono);
  font-size: 0.8rem;
  color: var(--accent);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}}
.hero h1 {{
  margin: 0;
  font-family: var(--font-serif);
  font-size: clamp(2rem, 4.2vw, 3rem);
  line-height: 1.18;
  font-weight: 700;
}}
.hero-dek {{
  margin: 1rem 0 0;
  max-width: 60ch;
  color: var(--muted);
  font-size: 1.02rem;
}}
.hero-ledger {{
  align-self: end;
  padding: 1.1rem 1.25rem;
  background: var(--paper-2);
  border-left: 2px solid var(--accent);
  font-size: 0.9rem;
}}
.hero-ledger dl {{
  margin: 0;
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.4rem 1rem;
}}
.hero-ledger dt {{ color: var(--muted); }}
.hero-ledger dd {{ margin: 0; font-family: var(--font-mono); }}
.controls {{
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin: 1.75rem 0 1.5rem;
}}
.filters {{
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}}
.filter-btn {{
  padding: 0.42rem 0.85rem;
  border: 1px solid var(--line);
  background: transparent;
  color: var(--muted);
  font: inherit;
  font-size: 0.88rem;
  cursor: pointer;
}}
.filter-btn.active, .filter-btn:hover {{
  border-color: var(--ink);
  color: var(--ink);
  background: var(--paper-2);
}}
.search-box {{
  width: min(320px, 100%);
  padding: 0.48rem 0.75rem;
  border: 1px solid var(--line);
  background: var(--paper-2);
  color: var(--ink);
  font: inherit;
  font-size: 0.9rem;
}}
.shelf {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(330px, 1fr));
  gap: 1.25rem;
}}
.book-card {{
  display: flex;
  flex-direction: column;
  padding: 1.25rem 1.3rem 1.15rem;
  background: var(--paper-2);
  border: 1px solid var(--line);
}}
.book-card[hidden] {{ display: none; }}
.card-top {{
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 0.55rem;
  font-family: var(--font-mono);
  font-size: 0.78rem;
  color: var(--muted);
}}
.book-no {{
  color: var(--accent);
  font-weight: 600;
}}
.book-date {{ margin-left: auto; }}
.book-card h2 {{
  margin: 0;
  font-family: var(--font-serif);
  font-size: 1.32rem;
  line-height: 1.3;
}}
.book-card h2 a {{ text-decoration: none; }}
.book-card h2 a:hover {{ text-decoration: underline; text-underline-offset: 3px; }}
.book-sub {{
  margin: 0.3rem 0 0.75rem;
  font-size: 0.9rem;
  color: var(--forest);
  font-weight: 500;
}}
.book-summary {{
  margin: 0 0 1rem;
  font-size: 0.9rem;
  color: var(--muted);
  flex: 1;
}}
.book-meta {{
  margin: 0 0 1rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--line);
  display: grid;
  gap: 0.25rem;
  font-size: 0.82rem;
}}
.book-meta div {{
  display: grid;
  grid-template-columns: 3.1rem minmax(0, 1fr);
  gap: 0.5rem;
}}
.book-meta dt {{ color: var(--muted); }}
.book-meta dd {{ margin: 0; overflow-wrap: anywhere; }}
.book-meta code {{ font-family: var(--font-mono); font-size: 0.79rem; }}
.upstream {{ text-decoration: underline; text-decoration-color: var(--line); text-underline-offset: 2px; }}
.card-actions {{
  display: flex;
  flex-wrap: wrap;
  gap: 0.45rem;
}}
.card-actions a {{
  padding: 0.35rem 0.68rem;
  border: 1px solid var(--line);
  text-decoration: none;
  font-size: 0.83rem;
  background: var(--paper);
}}
.card-actions a:hover {{ border-color: var(--ink); }}
.card-actions a.link-primary {{
  background: var(--ink);
  color: var(--paper);
  border-color: var(--ink);
  font-weight: 500;
}}
.empty-msg {{
  display: none;
  padding: 2rem 0;
  color: var(--muted);
}}
.footer {{
  margin-top: 3rem;
  padding-top: 1.25rem;
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: 0.85rem;
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 0.75rem;
}}
@media (max-width: 780px) {{
  .hero {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<header class="topbar">
  <a class="brand" href="index.html"><span class="brand-mark">BK</span><span>源码电子书书架</span></a>
  <div class="top-tools">
    <a class="top-btn" href="https://github.com/StarDust0083-GP03/ebooks" target="_blank" rel="noopener">GitHub 仓库</a>
    <button class="top-btn" id="theme-toggle" type="button">深浅色</button>
  </div>
</header>
<main class="shell">
  <section class="hero">
    <div>
      <p class="kicker">Source-Grounded Engineering Ebooks</p>
      <h1>把开源项目的真实骨架，<br>拆成能直接读通的书。</h1>
      <p class="hero-dek">收录全部 {len(books)} 本源码驱动中文技术电子书。每本书固定上游提交快照，配备内联 SVG 架构图、离线单文件合订本、EPUB 3、PDF 与零依赖教学实验代码。</p>
    </div>
    <aside class="hero-ledger">
      <dl>
        <dt>收录书目</dt><dd>{len(books)} 本</dd>
        <dt>最近更新</dt><dd>2026-11-27</dd>
        <dt>部署形态</dt><dd>统一单仓 · 全相对路径</dd>
      </dl>
    </aside>
  </section>

  <section class="controls" aria-label="筛选与搜索">
    <div class="filters">{filter_buttons}</div>
    <input class="search-box" id="shelf-search" type="search" placeholder="筛选书名、上游仓库或主题..." aria-label="筛选电子书">
  </section>

  <section class="shelf" id="shelf">
    {"".join(cards)}
  </section>
  <p class="empty-msg" id="empty-msg">没有匹配的电子书，换个关键词试试。</p>

  <footer class="footer">
    <span>独立源码研究与工程笔记，非各项目官方文档。</span>
    <span>MIT License · StarDust0083-GP03/ebooks</span>
  </footer>
</main>
<script>
(() => {{
  const root = document.documentElement;
  const saved = localStorage.getItem("bookshelf-theme");
  if (saved === "light" || saved === "dark") root.dataset.theme = saved;
  document.getElementById("theme-toggle").addEventListener("click", () => {{
    const next = root.dataset.theme === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    localStorage.setItem("bookshelf-theme", next);
  }});
  const buttons = [...document.querySelectorAll(".filter-btn")];
  const cards = [...document.querySelectorAll(".book-card")];
  const input = document.getElementById("shelf-search");
  const empty = document.getElementById("empty-msg");
  let activeCat = "all";
  function apply() {{
    const q = input.value.trim().toLowerCase();
    let shown = 0;
    for (const card of cards) {{
      const catOk = activeCat === "all" || card.dataset.category === activeCat;
      const textOk = !q || card.dataset.search.includes(q);
      const visible = catOk && textOk;
      card.hidden = !visible;
      if (visible) shown += 1;
    }}
    empty.style.display = shown ? "none" : "block";
  }}
  for (const btn of buttons) {{
    btn.addEventListener("click", () => {{
      activeCat = btn.dataset.filter;
      for (const b of buttons) b.classList.toggle("active", b === btn);
      apply();
    }});
  }}
  input.addEventListener("input", apply);
}})();
</script>
</body>
</html>
'''


LINK_RE = re.compile(r'(?:href|src)="([^"]+)"')


def verify_site(books: list[dict]) -> None:
    broken: list[tuple[str, str]] = []
    pages_to_check = [SITE_DIR / "index.html"]
    for b in books:
        slug_dir = SITE_DIR / b["slug"]
        for rel in ("index.html", "search.html", "book.html"):
            p = slug_dir / rel
            if p.exists():
                pages_to_check.append(p)
        chapters_dir = slug_dir / "chapters"
        if chapters_dir.is_dir():
            pages_to_check.extend(sorted(chapters_dir.glob("*.html")))

    for page in pages_to_check:
        text = page.read_text(encoding="utf-8")
        for link in LINK_RE.findall(text):
            parts = urlsplit(link)
            if parts.scheme or link.startswith("#") or link.startswith("//"):
                continue
            raw = unquote(parts.path)
            if not raw:
                continue
            target = (page.parent / raw).resolve()
            try:
                target.relative_to(SITE_DIR.resolve())
            except ValueError:
                broken.append((str(page.relative_to(SITE_DIR)), link))
                continue
            if not target.exists():
                broken.append((str(page.relative_to(SITE_DIR)), link))

    if broken:
        for p, l in broken[:40]:
            print(f"BROKEN {p}: {l}", file=sys.stderr)
        raise SystemExit(f"Found {len(broken)} broken link(s) across unified bookshelf site")
    print(f"VERIFIED: {len(books)} books, {len(pages_to_check)} HTML pages checked, 0 broken links")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="Verify all local links across _site/")
    args = parser.parse_args()

    SITE_DIR.mkdir(parents=True, exist_ok=True)
    staged = [stage_book(entry) for entry in CATALOG]
    (SITE_DIR / "index.html").write_text(render_index(staged), encoding="utf-8")
    (SITE_DIR / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Built unified bookshelf in {SITE_DIR} with {len(staged)} books")
    if args.verify:
        verify_site(staged)


if __name__ == "__main__":
    main()
