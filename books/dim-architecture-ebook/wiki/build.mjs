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

// Remove any stale chapter HTML from previous templates
for (const existing of fs.readdirSync(outDir)) {
  if (existing.endsWith('.html') && !items.some(it => `${it.slug}.html` === existing)) {
    fs.unlinkSync(path.join(outDir, existing));
  }
}

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
  <a class="brand" href="${prefix}index.html"><span class="brand-mark">D</span><span>Dim 架构解剖</span></a>
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
<meta name="color-scheme" content="light dark"><meta name="description" content="从时间总线、分支路由、rutis 跨语言数据面到权限与压缩重放，源码级解剖 arcships/dim Agent v4 架构"><meta name="author" content="独立源码研究">
<title>${escapeHtml(titleText(title))} · Dim 架构解剖</title>
<link rel="stylesheet" href="${prefix}assets/reader.css">
</head><body data-chapter="${chapter}">
${topbar(prefix)}${body}${menu(prefix)}
<footer class="site-footer">独立源码研究，非官方文档 · arcships/dim v0.1.0-alpha.27 · 快照 88a3df366940 · 2026-11-22</footer>
<script src="${prefix}assets/reader.js"></script>${extraScripts}
</body></html>`;
}

const descriptions = {
  '00-preface': '核心骨架、阅读路线与源码研究边界',
  '01-why-not-a-chat-loop': '并发竞态、重启重放、上下文污染与胶水爆炸四堵墙',
  '02-workspace-and-scopes': '28 个 Cargo 成员分层、三层作用域与 ADR 015 瘦身',
  '03-time-bus': '13 个保留域、双水位隔离与 SQLite WAL 物理压缩',
  '04-event-driven-contracts': 'Dispatcher、before 同步变形链、副作用不重放与 consume 协议',
  '05-timeline-branches': '起点 main 与可移动默认分支、保护路径配额与三键状态块',
  '06-rutis-and-data-faces': 'rutis 0.23.0 单行装配、75 个数据面与 36 个内部面实盘',
  '07-self-evolving-plugins': 'plugin-authoring 技能、会话内热写 TS 插件与热重载边界',
  '08-context-compaction-and-replay': '两条上下文窄门、不可变摘要事实与 SHA-256 可丢弃快照',
  '09-detached-work-and-permissions': '脱离工作活动持有、审批竞态与 ADR 003 到 018 的真实演进',
  '10-codemode-and-aimux': 'rquickjs 无外设沙箱、Aimux 缓存断点与同源重定向防护',
  '11-server-cli-web-and-protocols': 'App Server RPC v3、UI Leaf、Web 工作区与 ACP / MCP 网关',
  '12-tradeoffs-and-boundaries': 'xtask-check 静态门禁、literal_main 检查与七处已知工程代价',
  '13-teaching-model': '零依赖 Python 教学模型与 18 个核心不变量单元测试',
  'appendix-sources': 'v0.1.0-alpha.27 源码索引、核心术语与本地核验命令',
};

for (let i = 0; i < records.length; i++) {
  const r = records[i], prev = records[i - 1], next = records[i + 1];
  const rail = `<aside class="chapter-rail"><div class="rail-label">${r.appendix ? 'Appendix' : `Chapter ${r.label}`}</div><div class="rail-title">${titleText(r.title)}</div><ol>${r.headings.map(h => `<li><a href="#${h.id}">${escapeHtml(h.title)}</a></li>`).join('')}</ol></aside>`;
  const nav = `<nav class="chapter-nav" aria-label="章节导航">${prev ? `<a href="${prev.slug}.html"><small>上一章</small><strong>${titleText(prev.title)}</strong></a>` : ''}${next ? `<a class="next" href="${next.slug}.html"><small>下一章</small><strong>${titleText(next.title)}</strong></a>` : ''}</nav>`;
  const body = `<main id="main" class="reader-layout">${rail}<article class="chapter"><header class="chapter-head"><div class="chapter-number">${r.appendix ? `附录 ${r.label}` : `第 ${r.label} 章`}</div><h1>${titleHtml(r.title)}</h1><div class="chapter-meta">arcships/dim v0.1.0-alpha.27 · 快照 2026-11-22 · 约 ${Math.ceil(r.text.length / 500) * 2} 分钟</div></header><div class="prose">${r.html}</div>${nav}</article></main>`;
  fs.writeFileSync(path.join(outDir, `${r.slug}.html`), shell({title: r.title, body, prefix: '../', chapter: true}));
}

const chapterRows = records.map(r => `<a class="chapter-row" href="${r.href}"><span class="chapter-no">${r.label}</span><span><h3>${titleHtml(r.title)}</h3><p>${escapeHtml(descriptions[r.slug] || r.description)}</p></span><span class="arrow">→</span></a>`).join('');
const home = `<main id="main">
<section class="hero"><div><p class="kicker">源码驱动的架构解剖 · Agent v4 + rutis 0.23</p><h1>Dim 架构解剖<br><em>时间总线、分支路由与全插件运行时</em></h1><p class="dek">28 个 Cargo 工作区成员、111 个真实装配服务、20 个分支 Capability、26 份 ADR。逐层拆解 Rust 事件驱动时间线如何取代普通对话循环，把并发审批、并行分支与跨语言自演化插件收进同一套秩序。</p></div>
<aside class="hero-aside"><strong>不猜设计意图，直接核对源码与生成目录</strong>基于 arcships/dim v0.1.0-alpha.27 固定快照，附 6 幅主题适配架构图与 18 个零依赖 Python 不变量单测。<dl class="snapshot"><dt>版本</dt><dd>0.1.0-alpha.27</dd><dt>提交</dt><dd>88a3df366940</dd><dt>快照日期</dt><dd>2026-11-22</dd></dl></aside></section>
<section class="home-grid"><aside class="home-intro"><h2>前言 + 13 章 + 附录</h2><p>时间总线与分发 → 分支与默认路由 → rutis 数据面与热写插件 → 压缩、权限、沙箱与多端协议。</p><a href="chapters/00-preface.html">开始阅读 →</a><br><a href="book.html">离线合订本 →</a><br><a href="search.html">全文搜索 →</a><br><a href="exports/dim-architecture.pdf">PDF →</a><br><a href="exports/dim-architecture.epub">EPUB →</a></aside><div class="contents">${chapterRows}</div></section></main>`;
fs.writeFileSync(path.join(here, 'index.html'), shell({title: '目录', body: home}));

const searchBody = `<main id="main" class="search-page"><p class="kicker">全书搜索</p><h1>查一条契约，<br>回到源码。</h1><label for="search" class="skip">搜索全书</label><input id="search" class="search-input" type="search" autocomplete="off" placeholder="Timeline / branch/default / rutis / HostDispatch / ADR 018 / rquickjs"><section id="results" aria-live="polite"><p class="search-hint">输入服务名、事件键、ADR 或概念，例如 branch/default、consume、HostDispatch、RecoverySnapshot、literal_main。</p></section></main>`;
const searchIndex = records.map(r => ({number: r.label, title: r.title, href: r.href, text: r.text}));
fs.writeFileSync(path.join(assetsDir, 'search-index.json'), JSON.stringify(searchIndex, null, 2));
fs.writeFileSync(path.join(assetsDir, 'search-index.js'), `window.BOOK_SEARCH=${JSON.stringify(searchIndex)};\n`);
fs.writeFileSync(path.join(here, 'search.html'), shell({title: '搜索', body: searchBody, extraScripts: '<script src="assets/search-index.js"></script><script src="assets/search.js"></script>'}));

function volumeHtml(record) {
  const prefix = record.slug + '--';
  return record.html.replace(/\bid="([^"]+)"/g, (_, id) => `id="${prefix}${id}"`)
    .replace(/href="#([^"]+)"/g, (_, id) => `href="#${prefix}${id}"`)
    .replace(/aria-labelledby="([^"]+)"/g, (_, ids) =>
      `aria-labelledby="${ids.split(/\s+/).map(id => prefix + id).join(' ')}"`);
}
const combined = records.map(r => `<section class="book-section" id="${r.slug}"><header class="chapter-head"><div class="chapter-number">${r.label}</div><h1>${titleHtml(r.title)}</h1></header><div class="prose">${volumeHtml(r)}</div></section>`).join('');
const cover = `<section class="volume-cover"><p>源码驱动的架构解剖 · Agent v4 + rutis 0.23</p><h1>Dim 架构解剖</h1><h2>时间总线、分支路由与全插件运行时</h2><p>前言 · 13 章 · 附录</p><p>arcships/dim 0.1.0-alpha.27<br>88a3df3669405730cbee6bfd113a7f75cf520677<br>快照日期 2026-11-22</p><p>独立源码分析，非官方文档。基于固定提交静态源码核验与离线教学模型，不代表线上生产压测。</p></section>`;
const volumeToc = `<section class="volume-toc"><h1>全书目录</h1><ol>${records.map(r => `<li><a href="#${r.slug}">${titleHtml(r.title)}</a></li>`).join('')}</ol></section>`;
let volume = shell({title: '离线合订本', body: `<main id="main" class="reader-layout volume"><article class="chapter">${cover}${volumeToc}${combined}</article></main>`, chapter: true});
volume = volume.replace('<link rel="stylesheet" href="assets/reader.css">', `<style>${fs.readFileSync(path.join(assetsDir, 'reader.css'), 'utf8')}</style>`)
  .replace('<script src="assets/reader.js"></script>', `<script>${fs.readFileSync(path.join(assetsDir, 'reader.js'), 'utf8')}</script>`);
for (const r of records) volume = volume.replaceAll(`href="${r.href}"`, `href="#${r.slug}"`);
volume = volume.replaceAll('href="index.html"', 'href="#main"').replace('<a class="tool-button" href="search.html" aria-label="搜索">搜索</a>', '<span class="tool-button">Ctrl+F 查找</span>');
fs.writeFileSync(path.join(here, 'book.html'), volume);
fs.writeFileSync(
  path.join(bookDir, 'Dim架构解剖.md'),
  '# Dim 架构解剖：时间总线、分支路由与全插件运行时\n\n快照 `88a3df3669405730cbee6bfd113a7f75cf520677` · `arcships/dim` `0.1.0-alpha.27` · 采集于 2026-11-22\n\n'
  + records.map(r => r.md.replaceAll('../../wiki/assets/diagrams/', '../wiki/assets/diagrams/')).join('\n\n')
);

console.log(`Built ${records.length} sections, index, search, book.html, and Dim架构解剖.md`);
