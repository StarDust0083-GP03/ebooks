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

const UPSTREAM_SHA = '2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6';
const UPSTREAM_SHORT = UPSTREAM_SHA.slice(0, 12);
const UPSTREAM_DATE = '2026-11-02';
const SNAPSHOT_DATE = '2026-11-22';

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

function rewriteRelativeLinks(md, mode) {
  // Rewrite local chapter .md links (preserve external http/https URLs untouched!)
  md = md.replace(/\]\(([^)]+)\.md(#[^)]*)?\)/g, (match, target, hash = '') => {
    if (/^[a-z]+:\/\//i.test(target)) return match;
    const base = target.split('/').pop();
    return mode === 'single' ? `](#${base})` : `](${base}.html${hash})`;
  });
  // Rewrite repo-relative code/data links so published wiki/ has zero broken out-of-tree links
  md = md.replace(/\]\(\.\.\/\.\.\/(?:examples|sources)\/([^)]+)\)/g, (_m, _file) => {
    return mode === 'single' ? '](#appendix-code)' : '](appendix-code.html)';
  });
  return md;
}

function renderMarkdown(md, mode = 'site') {
  let html = marked.parse(rewriteRelativeLinks(md, mode));
  const ids = new Map();
  html = html.replace(/<h([23])>([\s\S]*?)<\/h\1>/g, (_, level, inside) => {
    const base = slugify(inside);
    const n = ids.get(base) || 0;
    ids.set(base, n + 1);
    const id = n ? `${base}-${n + 1}` : base;
    return `<h${level} id="${id}">${inside}<a class="anchor" href="#${id}" aria-label="链接到本节">#</a></h${level}>`;
  });
  html = html.replace(/<table>/g, '<div class="table-wrap"><table>').replace(/<\/table>/g, '</table></div>');
  return injectDiagrams(html);
}

const records = items.map((item) => {
  const md = fs.readFileSync(path.join(chapterDir, item.file), 'utf8');
  const bodyMd = md.replace(/^# .+\n/, '');
  const text = plain(bodyMd);
  const description = text.slice(0, 120);
  const html = renderMarkdown(bodyMd, 'site');
  const singleHtml = renderMarkdown(bodyMd, 'single');
  const headings = [...html.matchAll(/<h2 id="([^"]+)">([\s\S]*?)<a class="anchor"/g)].map(m => ({
    id: m[1], title: m[2].replace(/<[^>]+>/g, ''),
  }));
  return {...item, md, html, singleHtml, text, description, headings, href: `chapters/${item.slug}.html`};
});

function topbar(prefix = '') {
  return `<div class="progress" aria-hidden="true"></div>
<a class="skip" href="#main">跳到正文</a>
<header class="topbar">
  <a class="brand" href="${prefix}index.html"><span class="brand-mark">RAG</span><span>RAG 工程实战</span></a>
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
<meta name="color-scheme" content="light dark"><meta name="description" content="从文档解析、切分、混合检索到纠错、图谱、评估与上线边界的 RAG 工程实战指南"><meta name="author" content="独立源码研究">
<title>${escapeHtml(titleText(title))} · RAG 工程实战</title>
<link rel="stylesheet" href="${prefix}assets/reader.css">
</head><body data-chapter="${chapter}">
${topbar(prefix)}${body}${menu(prefix)}
<footer class="site-footer">独立源码研究，非官方文档 · RAG_Techniques ${UPSTREAM_SHORT}（${UPSTREAM_DATE}）· 核对 ${SNAPSHOT_DATE}</footer>
<script src="${prefix}assets/reader.js"></script>${extraScripts}
</body></html>`;
}

const descriptions = {
  '00-opening': '从答错的回调提问出发，交代系统红线、上游快照与研究边界',
  '01-requirements': '五类边界请求、响应状态协议、版本删除需求与验收题集',
  '02-architecture': '写入链与查询链解耦、Chunk 不可变模型与存储选型',
  '03-ingestion': '解析验收、Mistral OCR 4.1 三段对照与 CSV/JSON 语义保留',
  '04-chunking': '小块召回大块阅读、块感知护表、语义与命题切分边界',
  '05-baseline': '中文双字组、BM25 与词频余弦，以及 LangChain 1.x / LlamaIndex 0.14 迁移坑',
  '06-fusion-reranking': 'RRF 按 ID 对齐、上游数组错位静态核对与重排边界',
  '07-query-enhancement': '查询改写拆解、HyDE 假设文档禁区与离线 HyPE 代理索引',
  '08-context': '同文档版本邻域扩展、RSE、防丢否定词压缩与含标签预算打包',
  '09-evaluation': '候选与注入双阶段指标、6 题实测与 DeepEval GEval logprobs 陷阱',
  '10-corrective': 'Self-RAG 论文与教程差异、CRAG 有界三态状态机与内网拒答',
  '11-hierarchy-memory': '摘要导航、RAPTOR 递归刷新代价、MemoRAG 与跨权限串味',
  '12-graph': '受控两步检索 vs 建图、有向带出处边与 5 条 Graph RAG 路线对比',
  '13-multimodal-agent': 'Captioning vs ColPali 区域引用、Agent 护栏与停止条件',
  '14-production': '全路径 ACL、提示注入防线、原子快照、模型停服漂移与超时预算',
  '15-wrap-up': '用一次回调请求串起全书，附按故障现象选技术的排查表',
  'appendix-map': '47 份上游 Notebook 精确路径索引与 21 份可运行脚本清单',
  'appendix-sources': '提交 2fd7bd0f 增量审计、8 篇论文边界、术语表与许可说明',
  'appendix-code': '零依赖 Python 3.10+ 完整源码、评估脚本与教学语料快照',
};

for (let i = 0; i < records.length; i++) {
  const r = records[i], prev = records[i - 1], next = records[i + 1];
  const rail = `<aside class="chapter-rail"><div class="rail-label">${r.appendix ? 'Appendix' : `Chapter ${r.label}`}</div><div class="rail-title">${titleText(r.title)}</div><ol>${r.headings.map(h => `<li><a href="#${h.id}">${escapeHtml(h.title)}</a></li>`).join('')}</ol></aside>`;
  const nav = `<nav class="chapter-nav" aria-label="章节导航">${prev ? `<a href="${prev.slug}.html"><small>上一章</small><strong>${titleText(prev.title)}</strong></a>` : ''}${next ? `<a class="next" href="${next.slug}.html"><small>下一章</small><strong>${titleText(next.title)}</strong></a>` : ''}</nav>`;
  const body = `<main id="main" class="reader-layout">${rail}<article class="chapter"><header class="chapter-head"><div class="chapter-number">${r.appendix ? `附录 ${r.label}` : `第 ${r.label} 章`}</div><h1>${titleHtml(r.title)}</h1><div class="chapter-meta">RAG_Techniques 快照 ${UPSTREAM_DATE} · 约 ${Math.max(2, Math.ceil(r.text.length / 500) * 2)} 分钟</div></header><div class="prose">${r.html}</div>${nav}</article></main>`;
  fs.writeFileSync(path.join(outDir, `${r.slug}.html`), shell({title: r.title, body, prefix: '../', chapter: true}));
}

const chapterRows = records.map(r => `<a class="chapter-row" href="${r.href}"><span class="chapter-no">${r.label}</span><span><h3>${titleHtml(r.title)}</h3><p>${escapeHtml(descriptions[r.slug] || r.description)}</p></span><span class="arrow">→</span></a>`).join('');
const home = `<main id="main">
<section class="hero"><div><p class="kicker">源码驱动的 RAG 工程实战 · 2026-11 重编版</p><h1>RAG 工程实战<br><em>从文档问答到可验证的检索系统</em></h1><p class="dek">47 份上游 Notebook、21 份脚本、6 张架构图、20 项零依赖单元测试。以企业支付规范助手为主线，从解析切分一路拆到 RRF、HyPE、CRAG、评估与上线权限边界。</p></div>
<aside class="hero-aside"><strong>先定位故障层，再挑检索技术</strong>独立拆解 NirDiamant/RAG_Techniques 最新快照，涵盖 Mistral OCR 4.1、LangChain 1.x / LlamaIndex 0.14 迁移、DeepEval logprobs 陷阱与可复现离线实验。<dl class="snapshot"><dt>上游提交</dt><dd>${UPSTREAM_SHORT}</dd><dt>上游日期</dt><dd>${UPSTREAM_DATE}</dd><dt>核对日期</dt><dd>${SNAPSHOT_DATE}</dd></dl></aside></section>
<section class="home-grid"><aside class="home-intro"><h2>16 章正文 + 3 份附录</h2><p>需求与架构 → 解析与切分 → 检索、融合与增强 → 评估、纠错与图谱 → 生产权限与模型漂移。</p><a href="chapters/00-opening.html">开始阅读 →</a><br><a href="book.html">离线合订本 →</a><br><a href="search.html">全文搜索 →</a><br><a href="exports/rag-techniques-from-scratch.pdf">PDF 下载 →</a><br><a href="exports/rag-techniques-from-scratch.epub">EPUB 下载 →</a></aside><div class="contents">${chapterRows}</div></section></main>`;
fs.writeFileSync(path.join(here, 'index.html'), shell({title: '目录', body: home}));

const searchBody = `<main id="main" class="search-page"><p class="kicker">全书搜索</p><h1>查一条机制，<br>回到源码与测试。</h1><label for="search" class="skip">搜索全书</label><input id="search" class="search-input" type="search" autocomplete="off" placeholder="RRF / HyPE / OCR 4.1 / CRAG / GEval / ACL"><section id="results" aria-live="polite"><p class="search-hint">输入概念、方法或代码符号，比如 RRF、HyPE、OCR 4.1、CRAG、GEval、visible。</p></section></main>`;
const searchIndex = records.map(r => ({number: r.label, title: r.title, href: r.href, text: r.text}));
fs.writeFileSync(path.join(assetsDir, 'search-index.json'), JSON.stringify(searchIndex, null, 2));
fs.writeFileSync(path.join(assetsDir, 'search-index.js'), `window.BOOK_SEARCH=${JSON.stringify(searchIndex)};\n`);
fs.writeFileSync(path.join(here, 'search.html'), shell({title: '搜索', body: searchBody, extraScripts: '<script src="assets/search-index.js"></script><script src="assets/search.js"></script>'}));

function volumeHtml(record) {
  const prefix = record.slug + '--';
  // Namespace heading/element IDs and fragment hrefs; keep cross-chapter #slug anchors intact!
  const chapterSlugs = new Set(records.map(r => r.slug));
  let out = record.singleHtml.replace(/\bid="([^"]+)"/g, (_, id) => `id="${prefix}${id}"`);
  out = out.replace(/url\(#([^)]+)\)/g, (_, id) => `url(#${prefix}${id})`);
  out = out.replace(/href="#([^"]+)"/g, (_, id) => chapterSlugs.has(id) ? `href="#${id}"` : `href="#${prefix}${id}"`);
  out = out.replace(/aria-labelledby="([^"]+)"/g, (_, ids) =>
    `aria-labelledby="${ids.split(/\s+/).map(id => prefix + id).join(' ')}"`);
  return out;
}

const combined = records.map(r => `<section class="book-section" id="${r.slug}"><header class="chapter-head"><div class="chapter-number">${r.appendix ? `附录 ${r.label}` : `第 ${r.label} 章`}</div><h1>${titleHtml(r.title)}</h1></header><div class="prose">${volumeHtml(r)}</div></section>`).join('');
const cover = `<section class="volume-cover"><p>源码驱动的 RAG 工程实战 · 2026-11 重编版</p><h1>RAG 工程实战</h1><h2>从文档问答到可验证的检索系统</h2><p>16 章正文 · 3 份附录 · 6 张架构图 · 20 项单元测试</p><p>NirDiamant/RAG_Techniques<br>${UPSTREAM_SHA}<br>上游提交 ${UPSTREAM_DATE} · 本地核对 ${SNAPSHOT_DATE}</p><p>独立源码分析与零依赖离线教学实现，非官方文档。基于静态源码核对、标准库检索模型与离线评估，不代表真实生产压测。</p></section>`;
const volumeToc = `<section class="volume-toc"><h1>全书目录</h1><ol>${records.map(r => `<li><a href="#${r.slug}">${titleHtml(r.title)}</a></li>`).join('')}</ol></section>`;
let volume = shell({title: '离线合订本', body: `<main id="main" class="reader-layout volume"><article class="chapter">${cover}${volumeToc}${combined}</article></main>`, chapter: true});
volume = volume.replace('<link rel="stylesheet" href="assets/reader.css">', `<style>${fs.readFileSync(path.join(assetsDir, 'reader.css'), 'utf8')}</style>`)
  .replace('<script src="assets/reader.js"></script>', `<script>${fs.readFileSync(path.join(assetsDir, 'reader.js'), 'utf8')}</script>`);
for (const r of records) volume = volume.replaceAll(`href="${r.href}"`, `href="#${r.slug}"`);
volume = volume.replaceAll('href="index.html"', 'href="#main"').replace('<a class="tool-button" href="search.html" aria-label="搜索">搜索</a>', '<span class="tool-button">Ctrl+F 查找</span>');
fs.writeFileSync(path.join(here, 'book.html'), volume);
fs.writeFileSync(path.join(root, 'ebook.html'), volume);

const mergedMd = `# RAG 工程实战：从文档问答到可验证的检索系统\n\n上游快照 ${UPSTREAM_SHA} · ${UPSTREAM_DATE} · 本地核对 ${SNAPSHOT_DATE}\n\n` +
  records.map(r => r.md
    .replaceAll('../../wiki/assets/diagrams/', '../wiki/assets/diagrams/')
    .replaceAll('../../examples/', '../examples/')
    .replaceAll('../../sources/', '../sources/')
  ).join('\n\n---\n\n') + '\n';
fs.writeFileSync(path.join(bookDir, 'RAG工程实战.md'), mergedMd);

console.log(`Built ${records.length} chapters, index, search, wiki/book.html, ebook.html, and merged Markdown.`);
