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

// Remove any stale HTML chapters left over from template
const expectedSlugs = new Set(items.map(i => `${i.slug}.html`));
for (const f of fs.readdirSync(outDir)) {
  if (f.endsWith('.html') && !expectedSlugs.has(f)) fs.unlinkSync(path.join(outDir, f));
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
  <a class="brand" href="${prefix}index.html"><span class="brand-mark">PA</span><span>Prime Agent 架构解剖</span></a>
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
<meta name="color-scheme" content="light dark"><meta name="description" content="源码拆解 PrimeIntellect-ai/prime-agent v0.10.5：Rust 多进程宿主、Protocol v3 Python REPL、持久信箱、递归子 Agent 与 Continual Harness"><meta name="author" content="独立源码研究">
<title>${escapeHtml(titleText(title))} · Prime Agent 架构解剖</title>
<link rel="stylesheet" href="${prefix}assets/reader.css">
</head><body data-chapter="${chapter}">
${topbar(prefix)}${body}${menu(prefix)}
<footer class="site-footer">独立源码研究，非官方文档 · Prime Agent v0.10.5 · 快照 783acb5d72a9 · 2026-11-21</footer>
<script src="${prefix}assets/reader.js"></script>${extraScripts}
</body></html>`;
}

const descriptions = {
  '00-preface': '单工具 REPL、多进程托管与本书快照研究边界',
  '01-why-rlm': '上下文线性膨胀如何拖垮长程任务，以及 RLM 与 PTC 的解法',
  '02-workspace-topology': '10 个 Rust crate 单向依赖、Supervisor 与 per-session Worker 隔离',
  '03-agent-loop': 'RunClaim 并发防丢、Steering/Follow-Up/Continuation 三级队列与 end_turn',
  '04-python-repl': 'Protocol v3 双通道排空、3:1 头尾截断、SIGINT 中断与 dill v2 快照',
  '05-bash-edit-skills': '1 秒降级 bash()、persistent=True 接管、精确 edit 与仓库技能隔离',
  '06-subagents-delegation': '最大深度 2、核家族边界、仅追加 rlm_ledger 与声明式 rlm.factory',
  '07-agent-inbox': '6 类机器通知入箱、inbox.jsonl 断写重放、严格过滤与点对点消息',
  '08-session-search-acp': 'JSONL 会话树、流式全文检索、五态热更新事务与 ACP steering 扩展',
  '09-prompt-continual-harness': '字节稳定静态前缀、[harness-digest] 指纹追加与 /refine 后台精炼',
  '10-context-compaction-autonomy': '双底线压缩公式、/goal 续跑、心跳去重与带 Git 快照的质量门禁',
  '11-mcp-bridge': '宿主管 OAuth 与目录、内核跑 stdio/HTTP 协议、分页上限与脱敏',
  '12-tui-observability': 'pa-tui 零引擎耦合、首帧挂起 Sink 测试、250ms crossterm 补丁与卡片直显',
  '13-security-cost-mini-model': '进程隔离不等于安全沙箱、模型计费账本与 19 个单测的教学模型',
  'appendix-sources': 'v0.10.5 源码路径索引、关键常量表、术语对照与本地复验命令',
};

for (let i = 0; i < records.length; i++) {
  const r = records[i], prev = records[i - 1], next = records[i + 1];
  const rail = `<aside class="chapter-rail"><div class="rail-label">${r.appendix ? 'Appendix' : `Chapter ${r.label}`}</div><div class="rail-title">${titleText(r.title)}</div><ol>${r.headings.map(h => `<li><a href="#${h.id}">${escapeHtml(h.title)}</a></li>`).join('')}</ol></aside>`;
  const nav = `<nav class="chapter-nav" aria-label="章节导航">${prev ? `<a href="${prev.slug}.html"><small>上一章</small><strong>${titleText(prev.title)}</strong></a>` : ''}${next ? `<a class="next" href="${next.slug}.html"><small>下一章</small><strong>${titleText(next.title)}</strong></a>` : ''}</nav>`;
  const body = `<main id="main" class="reader-layout">${rail}<article class="chapter"><header class="chapter-head"><div class="chapter-number">${r.appendix ? `附录 ${r.label}` : `第 ${r.label} 章`}</div><h1>${titleHtml(r.title)}</h1><div class="chapter-meta">Prime Agent v0.10.5 快照 2026-11-21 · 约 ${Math.ceil(r.text.length / 500) * 2} 分钟</div></header><div class="prose">${r.html}</div>${nav}</article></main>`;
  fs.writeFileSync(path.join(outDir, `${r.slug}.html`), shell({title:r.title, body, prefix:'../', chapter:true}));
}

const chapterRows = records.map(r => `<a class="chapter-row" href="${r.href}"><span class="chapter-no">${r.label}</span><span><h3>${titleHtml(r.title)}</h3><p>${escapeHtml(descriptions[r.slug] || r.description)}</p></span><span class="arrow">→</span></a>`).join('');
const home = `<main id="main">
<section class="hero"><div><p class="kicker">源码驱动的架构解剖 · v0.10.5</p><h1>Prime Agent 架构解剖<br><em>Rust 宿主与自改进 RLM 运行时</em></h1><p class="dek">10 个 Rust crate、52 万行源码、1 套 Protocol v3 持久 CPython 内核、14 个内置技能。直击源码，拆开单工具收口、持久信箱、递归子 Agent、无损热更新与真实安全边界。</p></div>
<aside class="hero-aside"><strong>只信源码里的真实边界</strong>逐模块核验 PrimeIntellect-ai/prime-agent v0.10.5，附 6 张架构图与可运行的零依赖 Python 教学模型。<dl class="snapshot"><dt>版本</dt><dd>0.10.5</dd><dt>提交</dt><dd>783acb5d72a9</dd><dt>快照日期</dt><dd>2026-11-21</dd></dl></aside></section>
<section class="home-grid"><aside class="home-intro"><h2>前言 + 13 章 + 附录</h2><p>RLM 与拓扑 → 循环与 REPL → 技能、子 Agent 与信箱 → 提示、压缩、MCP、TUI 与安全。</p><a href="chapters/00-preface.html">开始阅读 →</a><br><a href="book.html">离线合订本 →</a><br><a href="search.html">全文搜索 →</a><br><a href="exports/prime-agent-architecture.pdf">PDF →</a><br><a href="exports/prime-agent-architecture.epub">EPUB →</a></aside><div class="contents">${chapterRows}</div></section></main>`;
fs.writeFileSync(path.join(here, 'index.html'), shell({title:'目录', body:home}));

const searchBody = `<main id="main" class="search-page"><p class="kicker">全书搜索</p><h1>查一条机制，<br>回到源码。</h1><label for="search" class="skip">搜索全书</label><input id="search" class="search-input" type="search" autocomplete="off" placeholder="python_repl / rlm.inbox / HeadTailBuffer / Claim-First / MCP"><section id="results" aria-live="polite"><p class="search-hint">输入模块、常量或机制名称，例如 rlm.inbox、HeadTailBuffer、dill、compaction、ACP。</p></section></main>`;
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
const cover = `<section class="volume-cover"><p>源码驱动的架构解剖</p><h1>Prime Agent 架构解剖</h1><h2>Rust 宿主与自改进 RLM 运行时</h2><p>前言 · 13 章 · 附录</p><p>Prime Agent v0.10.5<br>783acb5d72a9b23f97fa4a793a67d421b3ec402d<br>上游标签 2026-11-18 · 采集日期 2026-11-21</p><p>独立源码研究，非官方文档。基于静态源码拆解、仓库测试核验与零依赖离线教学模型，不代表商业生产压测。</p></section>`;
const volumeToc = `<section class="volume-toc"><h1>全书目录</h1><ol>${records.map(r => `<li><a href="#${r.slug}">${titleHtml(r.title)}</a></li>`).join('')}</ol></section>`;
let volume = shell({title:'离线合订本', body:`<main id="main" class="reader-layout volume"><article class="chapter">${cover}${volumeToc}${combined}</article></main>`, chapter:true});
volume = volume.replace('<link rel="stylesheet" href="assets/reader.css">', `<style>${fs.readFileSync(path.join(assetsDir,'reader.css'),'utf8')}</style>`)
  .replace('<script src="assets/reader.js"></script>', `<script>${fs.readFileSync(path.join(assetsDir,'reader.js'),'utf8')}</script>`);
for (const r of records) volume = volume.replaceAll(`href="${r.href}"`, `href="#${r.slug}"`);
volume = volume.replaceAll('href="index.html"', 'href="#main"').replace('<a class="tool-button" href="search.html" aria-label="搜索">搜索</a>', '<span class="tool-button">Ctrl+F 查找</span>');
fs.writeFileSync(path.join(here, 'book.html'), volume);
fs.writeFileSync(path.join(bookDir, 'PrimeAgent架构解剖.md'), '# Prime Agent 架构解剖：Rust 宿主与自改进 RLM 运行时\n\n快照 783acb5d72a9b23f97fa4a793a67d421b3ec402d · Prime Agent v0.10.5（2026-11-18）· 采集于 2026-11-21\n\n'+records.map(r=>r.md.replaceAll('../../wiki/assets/diagrams/', '../wiki/assets/diagrams/')).join('\n\n'));

console.log(`Built ${records.length} sections, index, search, book.html, and PrimeAgent架构解剖.md`);
