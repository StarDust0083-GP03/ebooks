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
if (items.length !== 16) throw new Error(`Expected 16 sections, found ${items.length}`);

// Clean stale chapter html files from scaffold
for (const old of fs.readdirSync(outDir)) {
  if (old.endsWith('.html') && !items.some(it => `${it.slug}.html` === old)) {
    fs.unlinkSync(path.join(outDir, old));
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
  <a class="brand" href="${prefix}index.html"><span class="brand-mark">MCP</span><span>MCP 2027 架构与工程实战</span></a>
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
<meta name="color-scheme" content="light dark"><meta name="description" content="基于 MCP 2026-07-28 正式规范与 2027 官方扩展栈的深度中文架构拆解"><meta name="author" content="独立规范研究">
<title>${escapeHtml(titleText(title))} · MCP 2027</title>
<link rel="stylesheet" href="${prefix}assets/reader.css">
</head><body data-chapter="${chapter}">
${topbar(prefix)}${body}${menu(prefix)}
<footer class="site-footer">独立架构研究，非官方文档 · MCP Spec 2026-07-28 · 快照 2bbace557bbf · 2026-11-27</footer>
<script src="${prefix}assets/reader.js"></script>${extraScripts}
</body></html>`;
}

const descriptions = {
  '00-preface': '2026 真实生产数据、CLI 与 MCP 的边界分工、全书事实口径',
  '01-evolution': '五个正式版本演进、为什么彻底砍掉会话与 SEP-2577 弃用取舍',
  '02-stateless-http': '无握手自包含请求、SEP-2243 头镜像校验、ttlMs/cacheScope 列表缓存',
  '03-primitives-schemas': 'Tools/Resources/Prompts 控制权、outputSchema 与 SEP-1303 自愈错误',
  '04-code-mode': '类型化桩代码、沙箱内存跨服务 Join、多模态与沙箱外 OAuth 优势',
  '05-mrtr-elicitation': 'SEP-2322 无状态多往返追问、AEAD requestState 与 URL Mode 防钓鱼',
  '06-oauth-auth': 'OAuth 2.1、RFC 9728、CIMD 无预注册、企业 IdP ID-JAG 与 iss 校验',
  '07-extensions-framework': 'SEP-2133 与 SEP-3392 四阶段成熟度生命周期与官方客户端支持矩阵',
  '08-skills-over-mcp': 'skill:// URI、渐进披露、SHA-256 清单热更新与五条防投毒红线',
  '09-tasks-async': 'resultType: task、高熵句柄、中途追问 tasks/update 与 TTL 属主校验',
  '10-events-always-on': 'Webhook/Push/Poll 三模式、HMAC 验签、cursor 去重与双轨迹续跑',
  '11-mcp-apps': '预声明 ui:// 资源、受限 iframe 沙箱、postMessage JSON-RPC 与三种形态',
  '12-discovery-observability-security': 'Registry、Server Cards、意图可观测性缺口与五重纵深安全防线',
  '13-harness-as-mcp': '子代理即 Tasks、Swarm Board 工具、Events 唤醒与 ask_agent 反模式',
  '14-mini-mcp-lab': '570 行零依赖 Python 标准库教学运行时与 21 项协议不变量单元测试',
  'appendix-sources': '固定快照路径索引、25 项关键 SEP 速查表、术语与全套核验命令',
};

for (let i = 0; i < records.length; i++) {
  const r = records[i], prev = records[i - 1], next = records[i + 1];
  const rail = `<aside class="chapter-rail"><div class="rail-label">${r.appendix ? 'Appendix' : `Chapter ${r.label}`}</div><div class="rail-title">${titleText(r.title)}</div><ol>${r.headings.map(h => `<li><a href="#${h.id}">${escapeHtml(h.title)}</a></li>`).join('')}</ol></aside>`;
  const nav = `<nav class="chapter-nav" aria-label="章节导航">${prev ? `<a href="${prev.slug}.html"><small>上一章</small><strong>${titleText(prev.title)}</strong></a>` : ''}${next ? `<a class="next" href="${next.slug}.html"><small>下一章</small><strong>${titleText(next.title)}</strong></a>` : ''}</nav>`;
  const body = `<main id="main" class="reader-layout">${rail}<article class="chapter"><header class="chapter-head"><div class="chapter-number">${r.appendix ? `附录 ${r.label}` : `第 ${r.label} 章`}</div><h1>${titleHtml(r.title)}</h1><div class="chapter-meta">MCP Spec 2026-07-28 快照 2026-11-24 · 约 ${Math.ceil(r.text.length / 500) * 2} 分钟</div></header><div class="prose">${r.html}</div>${nav}</article></main>`;
  fs.writeFileSync(path.join(outDir, `${r.slug}.html`), shell({title:r.title, body, prefix:'../', chapter:true}));
}

const chapterRows = records.map(r => `<a class="chapter-row" href="${r.href}"><span class="chapter-no">${r.label}</span><span><h3>${titleHtml(r.title)}</h3><p>${escapeHtml(descriptions[r.slug] || r.description)}</p></span><span class="arrow">→</span></a>`).join('');
const home = `<main id="main">
<section class="hero"><div><p class="kicker">规范驱动的架构解剖 · 面向 2027 运行时栈</p><h1>MCP 2027<br><em>从远程工具调用到 Agent 运行时协议</em></h1><p class="dek">无状态 HTTP 内核、Code Mode、MRTR、OAuth 2.1 与企业 IdP、Skills、Tasks、Events、Apps。对照 2026-07-28 正式规范与官方扩展，剥开炒作与口水仗，讲清真实协议边界与工程取舍。</p></div>
<aside class="hero-aside"><strong>一手规范核验，配可执行测试模型</strong>以 Philipp Schmid 2026-11-26 文章为切入，逐项核对 modelcontextprotocol 官方快照，附 6 张内联架构图与 21 个零依赖 Python 单元测试。<dl class="snapshot"><dt>规范版本</dt><dd>2026-07-28 + draft</dd><dt>固定提交</dt><dd>2bbace557bbf</dd><dt>快照日期</dt><dd>2026-11-24</dd></dl></aside></section>
<section class="home-grid"><aside class="home-intro"><h2>前言 + 14 章 + 附录</h2><p>无状态传输与契约 → Code Mode、MRTR 与授权 → 四大官方扩展 → 安全、多代理编排与代码核验。</p><a href="chapters/00-preface.html">开始阅读 →</a><br><a href="book.html">离线合订本 →</a><br><a href="search.html">全文搜索 →</a><br><a href="exports/mcp-2027-architecture.pdf">PDF →</a><br><a href="exports/mcp-2027-architecture.epub">EPUB →</a></aside><div class="contents">${chapterRows}</div></section></main>`;
fs.writeFileSync(path.join(here, 'index.html'), shell({title:'目录', body:home}));

const searchBody = `<main id="main" class="search-page"><p class="kicker">全书搜索</p><h1>查一条协议规则，<br>回到规范。</h1><label for="search" class="skip">搜索全书</label><input id="search" class="search-input" type="search" autocomplete="off" placeholder="Code Mode / MRTR / SEP-2567 / CIMD / skills / tasks / events"><section id="results" aria-live="polite"><p class="search-hint">输入 SEP 编号、方法名或概念，例如 Mcp-Method、requestState、ttlMs、ID-JAG、whsec_、ask_agent。</p></section></main>`;
const searchIndex = records.map(r => ({number:r.label,title:r.title,href:r.href,text:r.text}));
fs.writeFileSync(path.join(assetsDir, 'search-index.json'), JSON.stringify(searchIndex, null, 2));
fs.writeFileSync(path.join(assetsDir, 'search-index.js'), `window.BOOK_SEARCH=${JSON.stringify(searchIndex)};\n`);
fs.writeFileSync(path.join(here, 'search.html'), shell({title:'搜索', body:searchBody, extraScripts:'<script src="assets/search-index.js"></script><script src="assets/search.js"></script>'}));

function volumeHtml(record) {
  const prefix = record.slug + '--';
  return record.html
    .replace(/\bid="([^"]+)"/g, (_, id) => `id="${prefix}${id}"`)
    .replace(/href="#([^"]+)"/g, (_, id) => `href="#${prefix}${id}"`)
    .replace(/url\(#([^)]+)\)/g, (_, id) => `url(#${prefix}${id})`)
    .replace(/aria-labelledby="([^"]+)"/g, (_, ids) =>
      `aria-labelledby="${ids.split(/\s+/).map(id => prefix + id).join(' ')}"`);
}
const combined = records.map(r => `<section class="book-section" id="${r.slug}"><header class="chapter-head"><div class="chapter-number">${r.label}</div><h1>${titleHtml(r.title)}</h1></header><div class="prose">${volumeHtml(r)}</div></section>`).join('');
const cover = `<section class="volume-cover"><p>规范驱动的架构解剖 · 面向 2027 运行时栈</p><h1>MCP 2027</h1><h2>从远程工具调用到 Agent 运行时协议</h2><p>前言 · 14 章 · 附录</p><p>MCP Specification 2026-07-28 + draft<br>2bbace557bbf2cb5d03d6f9b48905415cd7607b0<br>上游规范 2026-11-24 · 主文 philschmid.de/mcp-2027 (2026-11-26)</p><p>独立规范研究，非官方文档。基于静态规范核验、SEP 原文对照与零依赖离线教学测试模型。</p></section>`;
const volumeToc = `<section class="volume-toc"><h1>全书目录</h1><ol>${records.map(r => `<li><a href="#${r.slug}">${titleHtml(r.title)}</a></li>`).join('')}</ol></section>`;
let volume = shell({title:'离线合订本', body:`<main id="main" class="reader-layout volume"><article class="chapter">${cover}${volumeToc}${combined}</article></main>`, chapter:true});
volume = volume.replace('<link rel="stylesheet" href="assets/reader.css">', `<style>${fs.readFileSync(path.join(assetsDir,'reader.css'),'utf8')}</style>`)
  .replace('<script src="assets/reader.js"></script>', `<script>${fs.readFileSync(path.join(assetsDir,'reader.js'),'utf8')}</script>`);
for (const r of records) volume = volume.replaceAll(`href="${r.href}"`, `href="#${r.slug}"`);
volume = volume.replaceAll('href="index.html"', 'href="#main"').replace('<a class="tool-button" href="search.html" aria-label="搜索">搜索</a>', '<span class="tool-button">Ctrl+F 查找</span>');
fs.writeFileSync(path.join(here, 'book.html'), volume);
fs.writeFileSync(
  path.join(bookDir, 'MCP2027架构与工程实战.md'),
  '# MCP 2027：从远程工具调用到 Agent 运行时协议\n\nMCP Spec 2026-07-28 + draft（快照 `2bbace557bbf2cb5d03d6f9b48905415cd7607b0`，2026-11-24）· 主引文章 `philschmid.de/mcp-2027`（2026-11-26）\n\n'
  + records.map(r => r.md.replaceAll('../../wiki/assets/diagrams/', '../wiki/assets/diagrams/')).join('\n\n')
);

console.log(`Built ${records.length} chapters, index, search, and book.html`);
