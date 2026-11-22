import fs from 'node:fs';
import path from 'node:path';
import { marked } from 'marked';

const here = path.dirname(new URL(import.meta.url).pathname);
const root = path.resolve(here, '..');
const bookDir = path.join(root, 'book');
const chapterDir = path.join(bookDir, 'chapters');
const outDir = path.join(here, 'chapters');
const assetsDir = path.join(here, 'assets');
const diagramDir = path.join(assetsDir, 'diagrams');
fs.mkdirSync(outDir, { recursive: true });
fs.mkdirSync(assetsDir, { recursive: true });

// Remove any stale HTML chapters from scaffold
for (const f of fs.readdirSync(outDir)) {
  if (f.endsWith('.html')) fs.unlinkSync(path.join(outDir, f));
}

marked.setOptions({ gfm: true, breaks: false });

const summary = fs.readFileSync(path.join(bookDir, 'SUMMARY.md'), 'utf8');
const chapterMatches = [...summary.matchAll(/^\d+\. \[([^\]]+)\]\(chapters\/([^)]+)\)/gm)];
const appendixMatches = [...summary.matchAll(/^- \[([^\]]+)\]\(chapters\/([^)]+)\)/gm)];
const items = [...chapterMatches, ...appendixMatches].map((m, i) => ({
  title: m[1],
  file: m[2],
  slug: m[2].replace(/\.md$/, ''),
  label: i < chapterMatches.length ? String(i).padStart(2, '0') : String.fromCharCode(65 + i - chapterMatches.length),
  appendix: i >= chapterMatches.length,
}));
if (items.length !== 15) throw new Error(`Expected 15 sections, found ${items.length}`);

function injectDiagrams(html) {
  return html.replace(/<p><img src="[^"]*\/diagrams\/([a-z0-9-]+)\.svg" alt="([^"]*)"\s*\/?><\/p>/g, (_, name, caption) => {
    const file = path.join(diagramDir, `${name}.svg`);
    if (!fs.existsSync(file)) throw new Error(`Missing diagram: ${name}`);
    return `<figure class="concept-diagram">${fs.readFileSync(file, 'utf8')}<figcaption>${escapeHtml(caption)}</figcaption></figure>`;
  });
}

const escapeHtml = (s) => s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const titleHtml = (s) => escapeHtml(s).replace(/`([^`]+)`/g, '<code>$1</code>');
const titleText = (s) => s.replace(/`/g, '');
const plain = (md) => md
  .replace(/<figure[\s\S]*?<\/figure>/g, ' ')
  .replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1')
  .replace(/```[\s\S]*?```/g, ' ')
  .replace(/`([^`]+)`/g, '$1')
  .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
  .replace(/^#{1,6}\s+/gm, '')
  .replace(/[>*_|]/g, ' ')
  .replace(/\s+/g, ' ')
  .trim();
const slugify = (s) => s.replace(/<[^>]+>/g, '').trim().toLowerCase()
  .replace(/[^\p{Letter}\p{Number}]+/gu, '-').replace(/^-|-$/g, '') || 'section';

function renderMarkdown(md) {
  let html = marked.parse(md);
  const ids = new Map();
  html = html.replace(/<h([23])>([\s\S]*?)<\/h\1>/g, (_, level, inside) => {
    const base = slugify(inside); const n = ids.get(base) || 0; ids.set(base, n + 1);
    const id = n ? `${base}-${n + 1}` : base;
    return `<h${level} id="${id}">${inside}<a class="anchor" href="#${id}" aria-label="链接到本节">#</a></h${level}>`;
  });
  html = html.replace(/<table>/g, '<div class="table-wrap"><table>').replace(/<\/table>/g, '</table></div>');
  return html;
}

const records = items.map((item) => {
  const md = fs.readFileSync(path.join(chapterDir, item.file), 'utf8');
  const bodyMd = md.replace(/^# .+\n/, '');
  const text = plain(bodyMd);
  const description = text.slice(0, 120);
  const html = injectDiagrams(renderMarkdown(bodyMd));
  const headings = [...html.matchAll(/<h2 id="([^"]+)">([\s\S]*?)<a class="anchor"/g)].map(m => ({
    id: m[1], title: m[2].replace(/<[^>]+>/g, ''),
  }));
  return {...item, md, html, text, description, headings, href: `chapters/${item.slug}.html`};
});

function topbar(prefix = '') {
  return `<div class="progress" aria-hidden="true"></div>
<a class="skip" href="#main">跳到正文</a>
<header class="topbar">
  <a class="brand" href="${prefix}index.html"><span class="brand-mark">∞</span><span>Infinity 架构解剖</span></a>
  <nav class="tools" aria-label="阅读工具">
    <a class="tool-button" href="${prefix}search.html" aria-label="搜索">搜索</a>
    <button class="tool-button" data-font-down aria-label="减小字号">A−</button>
    <button class="tool-button" data-font-up aria-label="增大字号">A+</button>
    <button class="tool-button" data-theme><span class="theme-label">深色</span></button>
    <button class="tool-button" data-menu-open aria-label="打开目录">目录</button>
  </nav>
</header>`;
}

function menu(prefix = '') {
  return `<dialog class="menu-panel" data-menu-panel>
  <div class="menu-head"><span>全书目录</span><button class="tool-button" data-menu-close>关闭</button></div>
  <nav class="menu-list">${records.map(r => `<a href="${prefix}${r.href}"><span>${r.label}</span>${titleHtml(r.title)}</a>`).join('')}</nav>
</dialog>`;
}

function shell({title, body, prefix = '', chapter = false, extraScripts = ''}) {
  return `<!doctype html>
<html lang="zh-CN" data-theme="auto"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><meta name="description" content="从无阻塞执行切片、RAP 响应式工具协议到堆叠沙箱与跨主机热迁移，解剖 hydro-project/infinity 的 Rust Agent 运行时架构"><meta name="author" content="独立源码研究">
<title>${escapeHtml(titleText(title))} · Infinity 架构解剖</title>
<link rel="stylesheet" href="${prefix}assets/reader.css">
</head><body data-chapter="${chapter}">
${topbar(prefix)}${body}${menu(prefix)}
<footer class="site-footer">独立源码研究，非官方文档 · hydro-project/infinity · 快照 e1a3e4b36e5f · 2026-11-05</footer>
<script src="${prefix}assets/reader.js"></script>${extraScripts}
</body></html>`;
}

const descriptions = {
  '00-preface': '无阻塞切片思路、三层工程组件与本书研究边界',
  '01-blocking-trap': '阻塞循环的成本死结、切片不变量、80k Agent 基准与同步回环例外',
  '02-workspace-topology': '21 个 Crate 五层拓扑、strkind 强类型 ID 与会话即根线程 ThreadRef',
  '03-slice-and-history': 'prepare/complete/execute 落盘屏障、三阶段未落盘状态、溢出止损与双去重',
  '04-rap-protocol': '断连回调、五类 RapCallback、生命周期通知、参考服务与 MCP 桥接',
  '05-threading-compaction': 'child_of 线程栈校验、Fresh 边界、合成事件调用与位置索引压缩',
  '06-local-actor-deferral': '单线程 LocalSet 路由、FuturesUnordered、延迟队列、is_passive 与抢占打断',
  '07-provider-error-boundary': 'ErrorClass 契约、Bedrock 60s 建连超时与细粒度工具流、Unix Socket 供应器',
  '08-stacked-sandboxes': 'jj/git 堆叠工作区、bwrap 与 sandbox-exec 写边界、write-orig 审批与 EFS 后端',
  '09-daemon-remote-migration': '256 MiB 帧边界、懒加载 JSON 持久化、SSH 远程代理与 git bundle 跨机热迁移',
  '10-serverless-aws': 'SQS FIFO 无锁分组、Aurora DSQL + DynamoDB 双存储与 900s 双轨持久定时器',
  '11-clients-and-hydro-dataflow': '内联 TUI 与无头模式、infinity acp 编辑器桥接与 hydro_lang Slack 数据流',
  '12-tradeoffs-and-limits': '沙箱只限写不限读、已提交历史溢出 TODO、本地与云端落差及适用边界',
  '13-mini-infinity': '零依赖 Python 教学模型与 19 个单元测试复现五大核心不变量',
  'appendix-sources': '21 个 Crate 规模实测、源码定位速查、核心术语表与本地核验命令'
};

for (let i = 0; i < records.length; i++) {
  const r = records[i], prev = records[i - 1], next = records[i + 1];
  const rail = `<aside class="chapter-rail"><div class="rail-label">${r.appendix ? 'Appendix' : `Chapter ${r.label}`}</div><div class="rail-title">${titleText(r.title)}</div><ol>${r.headings.map(h => `<li><a href="#${h.id}">${escapeHtml(h.title)}</a></li>`).join('')}</ol></aside>`;
  const nav = `<nav class="chapter-nav" aria-label="章节导航">${prev ? `<a href="${prev.slug}.html"><small>上一章</small><strong>${titleText(prev.title)}</strong></a>` : ''}${next ? `<a class="next" href="${next.slug}.html"><small>下一章</small><strong>${titleText(next.title)}</strong></a>` : ''}</nav>`;
  const body = `<main id="main" class="reader-layout">${rail}<article class="chapter"><header class="chapter-head"><div class="chapter-number">${r.appendix ? `附录 ${r.label}` : `第 ${r.label} 章`}</div><h1>${titleHtml(r.title)}</h1><div class="chapter-meta">hydro-project/infinity 快照 e1a3e4b36e5f · 约 ${Math.ceil(r.text.length / 500) * 2} 分钟</div></header><div class="prose">${r.html}</div>${nav}</article></main>`;
  fs.writeFileSync(path.join(outDir, `${r.slug}.html`), shell({title:r.title, body, prefix:'../', chapter:true}));
}

const chapterRows = records.map(r => `<a class="chapter-row" href="${r.href}"><span class="chapter-no">${r.label}</span><span><h3>${titleHtml(r.title)}</h3><p>${escapeHtml(descriptions[r.slug] || r.description)}</p></span><span class="arrow">→</span></a>`).join('');
const home = `<main id="main">
<section class="hero"><div><p class="kicker">源码驱动的架构解剖 · hydro-project/infinity</p><h1>Infinity 架构解剖<br><em>无阻塞 Agent 运行时与响应式工具协议</em></h1><p class="dek">21 个 Rust Crate、57,969 行源码。从无阻塞执行切片、三阶段历史缓冲与 RAP 断连回调，到堆叠沙箱、跨机热迁移与零闲置 AWS Lambda 部署。</p></div>
<aside class="hero-aside"><strong>让等待中的 Agent 退回纯数据</strong>独立拆解 UC Berkeley Hydro 团队的 Infinity 运行时、RAP 协议与 Infinity Code，附带 6 张架构图、零依赖 Python 教学模型与 19 项单元测试。<dl class="snapshot"><dt>上游</dt><dd>hydro-project/infinity</dd><dt>提交</dt><dd>e1a3e4b36e5f</dd><dt>快照日期</dt><dd>2026-11-05</dd></dl></aside></section>
<section class="home-grid"><aside class="home-intro"><h2>前言 + 13 章 + 附录</h2><p>切片与三阶段历史 → RAP 协议与线程树 → 本地调度与供应器 → 堆叠沙箱、热迁移与 Serverless。</p><a href="chapters/00-preface.html">开始阅读 →</a><br><a href="book.html">离线合订本 →</a><br><a href="search.html">全文搜索 →</a><br><a href="exports/infinity-architecture.pdf">PDF →</a><br><a href="exports/infinity-architecture.epub">EPUB →</a></aside><div class="contents">${chapterRows}</div></section></main>`;
fs.writeFileSync(path.join(here, 'index.html'), shell({title:'目录', body:home}));

const searchBody = `<main id="main" class="search-page"><p class="kicker">全书搜索</p><h1>查一条不变量，<br>回到源码。</h1><label for="search" class="skip">搜索全书</label><input id="search" class="search-input" type="search" autocomplete="off" placeholder="HistoryManager / RAP / DeferQueue / ThreadRef / bwrap / SQS"><section id="results" aria-live="polite"><p class="search-hint">输入类型、模块或概念，例如 unvalidated_items、prepare_callback、safe_spawn_point、write-orig。</p></section></main>`;
const searchIndex = records.map(r => ({number:r.label,title:r.title,href:r.href,text:r.text}));
fs.writeFileSync(path.join(assetsDir, 'search-index.json'), JSON.stringify(searchIndex, null, 2));
fs.writeFileSync(path.join(assetsDir, 'search-index.js'), `window.BOOK_SEARCH=${JSON.stringify(searchIndex)};\n`);
fs.writeFileSync(path.join(here, 'search.html'), shell({title:'搜索', body:searchBody, extraScripts:'<script src="assets/search-index.js"></script><script src="assets/search.js"></script>'}));

function volumeHtml(record) {
  const prefix = record.slug + '--';
  return record.html.replace(/\bid="([^"]+)"/g, (_, id) => `id="${prefix}${id}"`)
    .replace(/href="#([^"]+)"/g, (_, id) => `href="#${prefix}${id}"`)
    .replace(/aria-labelledby="([^"]+)"/g, (_, ids) =>
      `aria-labelledby="${ids.split(/\s+/).map(id => prefix + id).join(' ')}"`);
}
const combined = records.map(r => `<section class="book-section" id="${r.slug}"><header class="chapter-head"><div class="chapter-number">${r.label}</div><h1>${titleHtml(r.title)}</h1></header><div class="prose">${volumeHtml(r)}</div></section>`).join('');
const cover = `<section class="volume-cover"><p>源码驱动的架构解剖 · hydro-project/infinity</p><h1>Infinity 架构解剖</h1><h2>面向高并发 Agent 的无阻塞运行时与响应式工具协议</h2><p>前言 · 13 章 · 附录</p><p>hydro-project/infinity<br>e1a3e4b36e5f6143e03a59a7ad86e1abacfd22e8<br>上游提交 2026-11-05 · 采集核验 2026-11-22</p><p>独立源码分析，非官方文档。基于静态源码拆解与离线教学模型核验，不代表真实云端生产压测。</p></section>`;
const volumeToc = `<section class="volume-toc"><h1>全书目录</h1><ol>${records.map(r => `<li><a href="#${r.slug}">${titleHtml(r.title)}</a></li>`).join('')}</ol></section>`;
let volume = shell({title:'离线合订本', body:`<main id="main" class="reader-layout volume"><article class="chapter">${cover}${volumeToc}${combined}</article></main>`, chapter:true});
volume = volume.replace('<link rel="stylesheet" href="assets/reader.css">', `<style>${fs.readFileSync(path.join(assetsDir,'reader.css'),'utf8')}</style>`)
  .replace('<script src="assets/reader.js"></script>', `<script>${fs.readFileSync(path.join(assetsDir,'reader.js'),'utf8')}</script>`);
for (const r of records) volume = volume.replaceAll(`href="${r.href}"`, `href="#${r.slug}"`);
volume = volume.replaceAll('href="index.html"', 'href="#main"').replace('<a class="tool-button" href="search.html" aria-label="搜索">搜索</a>', '<span class="tool-button">Ctrl+F 查找</span>');
fs.writeFileSync(path.join(here, 'book.html'), volume);
fs.writeFileSync(
  path.join(bookDir, 'Infinity架构解剖.md'),
  '# Infinity 架构解剖：面向高并发 Agent 的无阻塞运行时与响应式工具协议\n\n快照 e1a3e4b36e5f6143e03a59a7ad86e1abacfd22e8 · hydro-project/infinity（2026-11-05）· 采集核验于 2026-11-22\n\n' +
  records.map(r => r.md.replaceAll('../../wiki/assets/diagrams/', '../wiki/assets/diagrams/')).join('\n\n')
);

console.log(`Built ${records.length} chapters, index, search, book.html, and Infinity架构解剖.md`);
