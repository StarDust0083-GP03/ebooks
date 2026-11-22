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
if (items.length !== 19) throw new Error(`Expected 19 sections, found ${items.length}`);

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

function injectDiagrams(html) {
  return html.replace(/<p><img src="[^"]*\/diagrams\/([a-z0-9-]+)\.svg" alt="([^"]*)"\s*\/?><\/p>/g, (_, name, caption) => {
    const file = path.join(diagramDir, `${name}.svg`);
    if (!fs.existsSync(file)) throw new Error(`Missing diagram: ${name}`);
    return `<figure class="concept-diagram">${fs.readFileSync(file, 'utf8')}<figcaption>${escapeHtml(caption)}</figcaption></figure>`;
  });
}

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
  <a class="brand" href="${prefix}index.html"><span class="brand-mark">O</span><span>Octop 架构解剖</span></a>
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
<meta name="color-scheme" content="light dark"><meta name="description" content="基于 Octop v1.0.3b7 源码的架构解剖：单进程宿主、托管专家、多层存储、多副本租约与安全边界"><meta name="author" content="独立源码研究">
<title>${escapeHtml(titleText(title))} · Octop 架构解剖</title>
<link rel="stylesheet" href="${prefix}assets/reader.css">
</head><body data-chapter="${chapter}">
${topbar(prefix)}${body}${menu(prefix)}
<footer class="site-footer">独立源码研究，非官方文档 · Octop v1.0.3b7 · 快照 ecd2cdbce3a5 · 2026-11-22</footer>
<script src="${prefix}assets/reader.js"></script>${extraScripts}
</body></html>`;
}

const descriptions = {
  '00-preface': 'v1.0.3b1 → v1.0.3b7 变化导读、快照元数据与阅读路线',
  '01-requirements': '从多用户自托管的失败模式推导八条骨架需求与验收线',
  '02-architecture': '五层依赖禁令、四套独立运行库与向多副本集群的过渡',
  '03-lifecycle': 'launch.py 装配树、首次安装延迟绑库、history_v2 退场与逆序退出',
  '04-agent-runtime': '原生专家装配、托管专家、中间件排序与连续相同工具调用熔断',
  '05-identity': '四种标识、共享专家私有线程隔离、contributed 对话与安全删序',
  '06-storage': '六类数据平面、ADR 003、SQLite/PG 细节与 PG 孤儿只统计修复',
  '07-workspace': 'BackendWorkspace、火山 TOS、Docker 容器宿主机挂载分流与 bwrap 边界',
  '08-turn-path': 'WebSocket 先订阅后 await、模型路由瀑布、熔断与断线取消语义',
  '09-memory-rag': 'PG 命名空间安全瘦身、pgvector、二阶段重排序与实体图谱扩展',
  '10-extensions': 'Skill、Plugin、Connector、RFC 8414 MCP OAuth 与 6 个回环 MCP',
  '11-teams': '主持人编排、独立成员 checkpoint、job_id 幂等记账与停止语义',
  '12-security': 'TOTP/Passkey MFA、三权分离、Cursor 重叠审批串行化与云凭证隔离',
  '13-dashboard': '模块级 chatStore、wsResumeGate 五道门禁与历史竞态治理',
  '14-interop': '入站/出站 ACP、托管专家回环能力与 Bridge 机机隧道边界',
  '15-operations': 'Electron 45 壳、Docker 精简/完整镜像、Helm 集群与 90s/30s/25s 租约',
  '16-tradeoffs': '五个经受迭代的好选择、高密度核心模块与拆分 Worker 的真实门槛',
  '17-mini-host': '零依赖 Python 教学模型与 18 个单元测试验证六条核心边界',
  'appendix-sources': '92 个 v1.0.3b7 固定源码入口、核心术语表与本地核验命令',
};

for (let i = 0; i < records.length; i++) {
  const r = records[i], prev = records[i - 1], next = records[i + 1];
  const rail = `<aside class="chapter-rail"><div class="rail-label">${r.appendix ? 'Appendix' : `Chapter ${r.label}`}</div><div class="rail-title">${titleText(r.title)}</div><ol>${r.headings.map(h => `<li><a href="#${h.id}">${escapeHtml(h.title)}</a></li>`).join('')}</ol></aside>`;
  const nav = `<nav class="chapter-nav" aria-label="章节导航">${prev ? `<a href="${prev.slug}.html"><small>上一章</small><strong>${titleText(prev.title)}</strong></a>` : ''}${next ? `<a class="next" href="${next.slug}.html"><small>下一章</small><strong>${titleText(next.title)}</strong></a>` : ''}</nav>`;
  const body = `<main id="main" class="reader-layout">${rail}<article class="chapter"><header class="chapter-head"><div class="chapter-number">${r.appendix ? `附录 ${r.label}` : `第 ${r.label} 章`}</div><h1>${titleHtml(r.title)}</h1><div class="chapter-meta">Octop v1.0.3b7 快照 2026-11-18 · 约 ${Math.ceil(r.text.length / 500) * 2} 分钟</div></header><div class="prose">${r.html}</div>${nav}</article></main>`;
  fs.writeFileSync(path.join(outDir, `${r.slug}.html`), shell({title:r.title, body, prefix:'../', chapter:true}));
}

const chapterRows = records.map(r => `<a class="chapter-row" href="${r.href}"><span class="chapter-no">${r.label}</span><span><h3>${titleHtml(r.title)}</h3><p>${escapeHtml(descriptions[r.slug] || r.description)}</p></span><span class="arrow">→</span></a>`).join('');
const home = `<main id="main">
<section class="hero"><div><p class="kicker">源码驱动的架构解剖 · v1.0.3b7 全面重编</p><h1>Octop 架构解剖<br><em>从单机宿主到多副本运行时</em></h1><p class="dek">拆解一个自托管多用户 Agent 平台如何把四套独立运行库装进单进程宿主，又如何在不引入外部消息队列的前提下，走到托管专家、PG 内容平面与多副本租约。</p></div>
<aside class="hero-aside"><strong>追着故障与边界读源码</strong>基于 TencentCloud/Octop v1.0.3b7 完整重编，涵盖托管专家回环 MCP、连续工具调用熔断、PG 孤儿安全清理、容器挂载分流与 Helm 集群租约。<dl class="snapshot"><dt>版本</dt><dd>v1.0.3b7</dd><dt>提交</dt><dd>ecd2cdbce3a5</dd><dt>上游发布</dt><dd>2026-11-18</dd></dl></aside></section>
<section class="home-grid"><aside class="home-intro"><h2>前言 + 17 章 + 附录</h2><p>宿主分层与装配 → 身份、存储与工作区 → 对话链路、知识库与扩展 → 团队、安全、前端、ACP/Bridge 与多副本运维。</p><a href="chapters/00-preface.html">开始阅读 →</a><br><a href="book.html">离线合订本 →</a><br><a href="search.html">全文搜索 →</a><br><a href="exports/octop-architecture.pdf">PDF →</a><br><a href="exports/octop-architecture.epub">EPUB →</a></aside><div class="contents">${chapterRows}</div></section></main>`;
fs.writeFileSync(path.join(here, 'index.html'), shell({title:'目录', body:home}));

const searchBody = `<main id="main" class="search-page"><p class="kicker">全书搜索</p><h1>查一条边界，<br>回到源码。</h1><label for="search" class="skip">搜索全书</label><input id="search" class="search-input" type="search" autocomplete="off" placeholder="managed / ConsecutiveToolCall / lease / RFC 8414 / orphan"><section id="results" aria-live="polite"><p class="search-hint">输入组件、机制或修复，例如 托管专家、回环 MCP、pgvector、omit_docker_volumes、wsResumeGate。</p></section></main>`;
const searchIndex = records.map(r => ({number:r.label,title:titleText(r.title),href:r.href,text:r.text}));
fs.writeFileSync(path.join(assetsDir, 'search-index.json'), JSON.stringify(searchIndex, null, 2));
fs.writeFileSync(path.join(assetsDir, 'search-index.js'), `window.BOOK_SEARCH=${JSON.stringify(searchIndex)};\n`);
fs.writeFileSync(path.join(here, 'search.html'), shell({title:'搜索', body:searchBody, extraScripts:'<script src="assets/search-index.js"></script><script src="assets/search.js"></script>'}));

// Namespace all IDs (headings and inline SVG defs/markers) per chapter in book.html.
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
const cover = `<section class="volume-cover"><p>源码驱动的架构解剖 · v1.0.3b7 全面重编版</p><h1>Octop 架构解剖</h1><h2>从单机宿主到多副本运行时</h2><p>前言 · 17 章 · 附录 · 6 张内联架构图</p><p>TencentCloud/Octop v1.0.3b7<br>ecd2cdbce3a54dee0f424f021d0a144d9669685e<br>上游发布 2026-11-18 · 采集日期 2026-11-22</p><p>独立源码分析，非官方文档。基于静态源码核对与离线教学模型验证，不代表生产压测或安全审计。</p></section>`;
const volumeToc = `<section class="volume-toc"><h1>全书目录</h1><ol>${records.map(r => `<li><a href="#${r.slug}">${titleHtml(r.title)}</a></li>`).join('')}</ol></section>`;
let volume = shell({title:'离线合订本', body:`<main id="main" class="reader-layout volume"><article class="chapter">${cover}${volumeToc}${combined}</article></main>`, chapter:true});
volume = volume.replace('<link rel="stylesheet" href="assets/reader.css">', `<style>${fs.readFileSync(path.join(assetsDir,'reader.css'),'utf8')}</style>`)
  .replace('<script src="assets/reader.js"></script>', `<script>${fs.readFileSync(path.join(assetsDir,'reader.js'),'utf8')}</script>`);
for (const r of records) volume = volume.replaceAll(`href="${r.href}"`, `href="#${r.slug}"`);
volume = volume.replaceAll('href="index.html"', 'href="#main"').replace('<a class="tool-button" href="search.html" aria-label="搜索">搜索</a>', '<span class="tool-button">Ctrl+F 查找</span>');
fs.writeFileSync(path.join(here, 'book.html'), volume);
fs.writeFileSync(
  path.join(bookDir, 'Octop架构解剖.md'),
  '# Octop 架构解剖：从单机宿主到多副本运行时\n\n快照 ecd2cdbce3a54dee0f424f021d0a144d9669685e · Octop v1.0.3b7（2026-11-18）· 采集于 2026-11-22\n\n'
    + records.map(r => r.md.replaceAll('../../wiki/assets/diagrams/', '../wiki/assets/diagrams/')).join('\n\n')
);

console.log(`Built ${records.length} chapters, index, search, book.html, and Octop架构解剖.md`);
