#!/usr/bin/env node
// 帮助中心构建器：content/**/*.md → output/index.html（单文件、完全离线、零外链）
// 用法：node build.mjs   （无任何 npm 依赖）

import { readdirSync, readFileSync, writeFileSync, mkdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { renderInline, renderPlainText } from "./markdown-inline.mjs";

const ROOT = fileURLToPath(new URL(".", import.meta.url));
const CONTENT_DIR = join(ROOT, "content");
const V2_ROOT = resolve(ROOT, "../..");
const version = readFileSync(join(V2_ROOT, "VERSION"), "utf8").trim();
if (!/^\d+\.\d+\.\d+$/.test(version)) throw new Error(`v2/VERSION 不是 SemVer：${version}`);
const PRODUCT_VERSION = `V${version}`;
const outputArgument = process.argv.indexOf("--output");
if (outputArgument < 0 || !process.argv[outputArgument + 1]) throw new Error("缺少参数：--output");
const outputPath = resolve(process.cwd(), process.argv[outputArgument + 1]);

// 内联进 <script> 的 JSON 必须把 < 预编码为 \u003c，防止文章内容组成 </script> 逃逸。
const inlineJson = (value) => JSON.stringify(value).replace(/</g, "\\u003c");

const CATEGORIES = [
  { id: "quick-start", name: "快速上手", desc: "第一次使用，从这里开始", icon: "book" },
  { id: "booking", name: "预约与日历", desc: "创建、修改、取消与时段冲突", icon: "calendar" },
  { id: "reminders", name: "提醒与通知", desc: "临近提醒、变更通知与提示音", icon: "bell" },
  { id: "handover", name: "工作交接", desc: "把预约交给同事，或接手预约", icon: "swap" },
  { id: "account", name: "账号与登录", desc: "密码、登录状态与个人资料", icon: "user" },
  { id: "data", name: "记录与统计", desc: "预约记录、数据中心与导出", icon: "chart" },
  { id: "admin", name: "管理员专区", desc: "用户、笔录室、备份与系统", icon: "shield" },
  { id: "security", name: "安全与隐私", desc: "数据在哪、谁能看到什么", icon: "lock" },
  { id: "troubleshooting", name: "故障排查", desc: "打不开、连不上、要恢复", icon: "wrench" },
];

const ROLE_LABELS = { employee: "员工", admin: "管理员" };

const ICONS = {
  book: '<path d="M2 4h6a4 4 0 0 1 4 4v12a3 3 0 0 0-3-3H2z"/><path d="M22 4h-6a4 4 0 0 0-4 4v12a3 3 0 0 1 3-3h7z"/>',
  calendar: '<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M8 2v4M16 2v4M3 9h18"/>',
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
  swap: '<path d="M17 2l4 4-4 4"/><path d="M21 6H9"/><path d="M7 22l-4-4 4-4"/><path d="M3 18h12"/>',
  user: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  chart: '<path d="M18 20V10"/><path d="M12 20V4"/><path d="M6 20v-6"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  wrench: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/>',
  chevron: '<path d="M9 18l6-6-6-6"/>',
  arrowRight: '<path d="M5 12h14"/><path d="M13 6l6 6-6 6"/>',
  arrowLeft: '<path d="M19 12H5"/><path d="M11 18l-6-6 6-6"/>',
  arrowUpRight: '<path d="M7 17L17 7"/><path d="M7 7h10v10"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/>',
};

function icon(name) {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (ICONS[name] || ICONS.book) + "</svg>";
}

// ---------- frontmatter（极简 YAML 子集：key: value / key: [a, b]） ----------
function parseFrontmatter(raw, file) {
  const match = /^---\n([\s\S]*?)\n---\n?([\s\S]*)$/.exec(raw);
  if (!match) throw new Error("缺少 frontmatter：" + file);
  const meta = {};
  for (const line of match[1].split("\n")) {
    const kv = /^([\w-]+):\s*(.*)$/.exec(line);
    if (!kv) continue;
    const [, key, value] = kv;
    if (value.startsWith("[") && value.endsWith("]")) {
      meta[key] = value.slice(1, -1).split(",").map((s) => s.trim().replace(/^['"]|['"]$/g, "")).filter(Boolean);
    } else {
      meta[key] = value.replace(/^['"]|['"]$/g, "");
    }
  }
  return { meta, body: match[2] };
}

// ---------- markdown 子集 → HTML（标题/段落/列表/表格/引用/加粗/行内码/链接） ----------
function splitTableRow(row) {
  const source = row.trim().replace(/^\|/, "").replace(/\|$/, "");
  const cells = [];
  let cell = "";
  let inWikiLink = false;
  let inCode = false;

  for (let index = 0; index < source.length; index += 1) {
    if (!inCode && source.startsWith("[[", index)) {
      inWikiLink = true;
      cell += "[[";
      index += 1;
      continue;
    }
    if (!inCode && inWikiLink && source.startsWith("]]", index)) {
      inWikiLink = false;
      cell += "]]";
      index += 1;
      continue;
    }
    if (!inWikiLink && source[index] === "`") {
      inCode = !inCode;
      cell += source[index];
      continue;
    }
    if (!inWikiLink && !inCode && source[index] === "|") {
      cells.push(cell.trim());
      cell = "";
      continue;
    }
    cell += source[index];
  }

  cells.push(cell.trim());
  return cells;
}

function markdownToHtml(md, articlesById, source) {
  const lines = md.replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let paragraph = [];
  let index = 0;
  const inline = (text) => renderInline(text, articlesById, source);
  const flush = () => {
    if (paragraph.length) out.push("<p>" + inline(paragraph.join(" ")) + "</p>");
    paragraph = [];
  };
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { flush(); index += 1; continue; }
    if (/^###\s+/.test(line)) { flush(); out.push("<h3>" + inline(line.replace(/^###\s+/, "")) + "</h3>"); index += 1; continue; }
    if (/^##\s+/.test(line)) { flush(); out.push("<h2>" + inline(line.replace(/^##\s+/, "")) + "</h2>"); index += 1; continue; }
    if (/^>\s?/.test(line)) {
      flush();
      const quote = [];
      while (index < lines.length && /^>\s?/.test(lines[index])) { quote.push(lines[index].replace(/^>\s?/, "")); index += 1; }
      out.push("<blockquote><p>" + inline(quote.join(" ")) + "</p></blockquote>");
      continue;
    }
    if (/^\|/.test(line)) {
      flush();
      const rows = [];
      while (index < lines.length && /^\|/.test(lines[index])) { rows.push(lines[index]); index += 1; }
      if (rows.length >= 2 && /^\|[\s:|-]+\|?$/.test(rows[1].trim())) {
        const cells = rows.map(splitTableRow);
        let html = "<table><thead><tr>" + cells[0].map((c) => "<th>" + inline(c) + "</th>").join("") + "</tr></thead><tbody>";
        for (let r = 2; r < cells.length; r += 1) html += "<tr>" + cells[r].map((c) => "<td>" + inline(c) + "</td>").join("") + "</tr>";
        out.push(html + "</tbody></table>");
      } else {
        out.push("<p>" + rows.map((r) => inline(r)).join("<br>") + "</p>");
      }
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      flush();
      const items = [];
      while (index < lines.length && /^[-*]\s+/.test(lines[index])) { items.push(lines[index].replace(/^[-*]\s+/, "")); index += 1; }
      out.push("<ul>" + items.map((it) => "<li>" + inline(it) + "</li>").join("") + "</ul>");
      continue;
    }
    if (/^\d+\.\s+/.test(line)) {
      flush();
      const items = [];
      while (index < lines.length && /^\d+\.\s+/.test(lines[index])) { items.push(lines[index].replace(/^\d+\.\s+/, "")); index += 1; }
      out.push("<ol>" + items.map((it) => "<li>" + inline(it) + "</li>").join("") + "</ol>");
      continue;
    }
    paragraph.push(line.trim());
    index += 1;
  }
  flush();
  return out.join("\n");
}

// ---------- 读取内容 ----------
function walk(dir) {
  const entries = [];
  for (const name of readdirSync(dir).sort()) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) entries.push(...walk(full));
    else if (name.endsWith(".md")) entries.push(full);
  }
  return entries;
}

const warnings = [];
const articles = [];
const seenIds = new Set();

for (const file of walk(CONTENT_DIR)) {
  const { meta, body } = parseFrontmatter(readFileSync(file, "utf8"), relative(ROOT, file));
  const id = meta.id;
  if (!id || !meta.title || !meta.summary || !meta.category) throw new Error("frontmatter 需要 id/title/summary/category：" + file);
  if (!/^[a-z0-9-]+$/.test(id)) throw new Error("文章 id 只能使用小写字母、数字和连字符：" + id);
  const summary = String(meta.summary).trim();
  const summaryLength = [...summary].length;
  if (summaryLength < 24 || summaryLength > 50) throw new Error("summary 需要 24–50 个字符：" + file + "（当前 " + summaryLength + "）");
  if (/\[\[|\]\]|[*_`#]|…|\.\.\./.test(summary)) throw new Error("summary 不得包含 Markdown、Wiki Link 或省略号：" + file);
  if (seenIds.has(id)) throw new Error("文章 id 重复：" + id);
  seenIds.add(id);
  if (!CATEGORIES.some((c) => c.id === meta.category)) warnings.push("未知分类 " + meta.category + "：" + id);
  const roles = Array.isArray(meta.roles) ? meta.roles : String(meta.roles || "employee,admin").split(",").map((s) => s.trim()).filter(Boolean);
  articles.push({
    id,
    title: meta.title,
    summary,
    category: meta.category,
    roles,
    tags: Array.isArray(meta.tags) ? meta.tags : [],
    related: Array.isArray(meta.related) ? meta.related : [],
    updated: String(meta.updated || "").trim(),
    order: Number(meta.order || 500),
    body: body.trim(),
    text: "",
    source: relative(ROOT, file),
  });
}

// 内容日期单一来源：从文章 frontmatter 的最大 updated 派生，不再手工维护常量。
const CONTENT_DATE = articles.reduce((latest, article) => (article.updated > latest ? article.updated : latest), "");
if (!/^\d{4}-\d{2}-\d{2}$/.test(CONTENT_DATE)) throw new Error("内容日期无法从文章 updated 派生（存在缺失或格式错误的 updated frontmatter）");
for (const article of articles) {
  if (!article.updated) article.updated = CONTENT_DATE;
}

const articleIndex = new Map(articles.map((article) => [article.id, { id: article.id, title: article.title }]));
for (const article of articles) {
  article.html = markdownToHtml(article.body, articleIndex, article.source);
  article.text = renderPlainText(article.body, articleIndex, article.source);
  delete article.body;
  delete article.source;
}

for (const article of articles) {
  for (const rel of article.related) {
    if (!seenIds.has(rel)) warnings.push("related 指向不存在的文章：" + article.id + " -> " + rel);
  }
}

articles.sort((a, b) => (a.category === b.category ? a.order - b.order || a.title.localeCompare(b.title, "zh") : CATEGORIES.findIndex((c) => c.id === a.category) - CATEGORIES.findIndex((c) => c.id === b.category)));

const data = {
  categories: CATEGORIES.map(({ id, name, desc, icon: iconName }) => ({ id, name, desc, icon: iconName, count: articles.filter((a) => a.category === id).length })),
  articles: articles.map((a) => ({ ...a, categoryName: (CATEGORIES.find((c) => c.id === a.category) || {}).name || a.category })),
};

// ---------- 页面模板（全部内联，无外链资源） ----------
const clientJs = String.raw`
(function () {
  if (new URLSearchParams(location.search).get('embedded') === '1') document.body.dataset.embedded = 'true';
  var DATA = __DATA__;
  var byId = {};
  DATA.articles.forEach(function (a) { byId[a.id] = a; });
  var catById = {};
  DATA.categories.forEach(function (c) { catById[c.id] = c; });

  function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
  function svgIcon(name) { return ICONS[name] || ICONS.book; }

  function roleChips(article) {
    if (article.roles.length >= 2) return '<span class="chip chip-all">全员</span>';
    return article.roles.map(function (r) { return '<span class="chip">' + esc(r === 'admin' ? '管理员' : '员工') + '</span>'; }).join('');
  }

  function breadcrumb(parts) {
    return '<nav class="crumbs" aria-label="面包屑">' + parts.map(function (p, i) {
      return (i < parts.length - 1 && p.href) ? '<a href="' + p.href + '">' + esc(p.label) + '</a>' : '<span>' + esc(p.label) + '</span>';
    }).join('<i>' + svgIcon('chevron') + '</i>') + '</nav>';
  }

  function articleBody(article) {
    var cat = catById[article.category] || { id: article.category, name: article.category };
    var list = DATA.articles.filter(function (a) { return a.category === article.category; });
    var pos = list.findIndex(function (a) { return a.id === article.id; });
    var prev = list[pos - 1], next = list[pos + 1];
    var related = article.related.map(function (id) { return byId[id]; }).filter(Boolean);

    var html = '<div class="article-shell">';
    html += breadcrumb([{ label: '帮助中心', href: '#/' }, { label: cat.name, href: '#/c/' + cat.id }, { label: article.title }]);
    html += '<article class="article"><div class="article-meta">' + roleChips(article) + '<span class="meta-date">更新于 ' + esc(article.updated) + '</span></div>';
    html += '<h1>' + esc(article.title) + '</h1>';
    html += '<div class="article-content">' + article.html + '</div>';
    if (related.length) {
      html += '<section class="related"><h2>相关文章</h2><ul>' +
        related.map(function (a) { return '<li><a href="#/a/' + a.id + '"><span><strong>' + esc(a.title) + '</strong><small>' + esc((catById[a.category] || {}).name || a.category) + '</small></span><i>' + svgIcon('chevron') + '</i></a></li>'; }).join('') +
        '</ul></section>';
    }
    html += '</article>';
    html += '<nav class="pager">';
    html += prev ? '<a class="pager-prev" href="#/a/' + prev.id + '">' + svgIcon('arrowLeft') + '<span>上一篇<strong>' + esc(prev.title) + '</strong></span></a>' : '<span></span>';
    html += next ? '<a class="pager-next" href="#/a/' + next.id + '"><span>下一篇<strong>' + esc(next.title) + '</strong></span>' + svgIcon('arrowRight') + '</a>' : '<span></span>';
    html += '</nav>';
    html += '<div class="support-box"><strong>仍然需要帮助？</strong><span>请先联系本单位系统管理员；管理员无法解决时，再联系部署维护人员。</span></div>';
    html += '</div>';
    return html;
  }

  function renderHome() {
    var html = '<div class="home">';
    html += '<div class="home-intro"><p class="eyebrow">帮助中心</p><h1>需要什么帮助？</h1>';
    html += '<div class="home-search-wrap"><span class="home-search-mark">' + svgIcon('search') + '</span><input id="home-search" data-help-search type="search" placeholder="搜索问题，例如：登录状态保留多久" autocomplete="off" aria-label="搜索帮助文章"><span class="home-search-count">' + DATA.articles.length + ' 篇文章</span><div class="search-hint"></div></div></div>';
    html += '<div class="role-lanes">';
    html += '<a class="role-lane" href="#/a/qs-first-booking"><span><small>普通员工</small><strong>五分钟创建第一场预约</strong></span><i>' + svgIcon('arrowUpRight') + '</i></a>';
    html += '<a class="role-lane" href="#/a/ad-checklist"><span><small>管理员</small><strong>新部署的第一次配置</strong></span><i>' + svgIcon('arrowUpRight') + '</i></a>';
    html += '</div>';
    html += '<div class="category-heading"><h2>浏览全部分类</h2><span>' + DATA.categories.length + ' 个分类</span></div><div class="category-grid">';
    DATA.categories.forEach(function (cat) {
      html += '<a class="category-item" href="#/c/' + cat.id + '"><span class="cat-icon">' + svgIcon(cat.icon) + '</span><span class="cat-copy"><strong>' + esc(cat.name) + '</strong><small>' + esc(cat.desc) + '</small></span><span class="cat-count">' + cat.count + ' 篇</span></a>';
    });
    html += '</div></div>';
    return html;
  }

  function renderCategory(catId) {
    var cat = catById[catId];
    if (!cat) return renderHome();
    var list = DATA.articles.filter(function (a) { return a.category === catId; });
    var html = '<div class="category-shell">';
    html += breadcrumb([{ label: '帮助中心', href: '#/' }, { label: cat.name }]);
    html += '<header class="cat-header"><span class="cat-icon big">' + svgIcon(cat.icon) + '</span><div><p>' + cat.count + ' 篇文章</p><h1>' + esc(cat.name) + '</h1><span>' + esc(cat.desc) + '</span></div></header>';
    html += '<ul class="article-list" aria-label="' + esc(cat.name) + '文章列表">';
    list.forEach(function (a) {
      html += '<li><a href="#/a/' + a.id + '"><span class="article-list-copy"><strong>' + esc(a.title) + '</strong><small>' + esc(a.summary) + '</small></span><span class="article-list-role">' + roleChips(a) + '</span><i>' + svgIcon('chevron') + '</i></a></li>';
    });
    html += '</ul><a class="back-categories" href="#/">' + svgIcon('arrowLeft') + '返回全部分类</a></div>';
    return html;
  }

  function renderArticle(id) {
    var article = byId[id];
    if (!article) return renderHome();
    return articleBody(article);
  }

  function snippet(text, query) {
    var at = text.toLowerCase().indexOf(query.toLowerCase());
    if (at < 0) return esc(text.slice(0, 90)) + '…';
    var from = Math.max(0, at - 30);
    return (from > 0 ? '…' : '') + esc(text.slice(from, at + 70)) + '…';
  }

  function searchArticles(query) {
    var q = query.trim().toLowerCase();
    if (!q) return [];
    var terms = q.split(/\s+/);
    return DATA.articles.map(function (a) {
      var title = a.title.toLowerCase(), tags = a.tags.join(' ').toLowerCase(), body = a.text.toLowerCase();
      var score = 0;
      terms.forEach(function (t) {
        if (title.includes(t)) score += 8;
        if (title.includes(t) && a.title.toLowerCase().indexOf(t) === 0) score += 4;
        if (tags.includes(t)) score += 4;
        if (body.includes(t)) score += 1;
      });
      return { a: a, score: score };
    }).filter(function (r) { return r.score > 0; })
      .sort(function (x, y) { return y.score - x.score; })
      .slice(0, 15);
  }

  function renderSearch(query) {
    var results = searchArticles(query);
    var html = '<div class="search-shell">';
    html += breadcrumb([{ label: '帮助中心', href: '#/' }, { label: '搜索“' + query + '”' }]);
    html += '<div class="results-search-wrap"><span>' + svgIcon('search') + '</span><input id="results-search" data-help-search type="search" value="' + esc(query) + '" autocomplete="off" aria-label="搜索帮助文章"><div class="search-hint"></div></div>';
    html += '<div class="search-heading"><h1>“' + esc(query) + '”的搜索结果</h1><p>' + results.length + ' 篇相关文章</p></div>';
    if (!results.length) {
      html += '<div class="empty-search">没有找到相关文章。可以换个说法再试，例如“密码”“提醒”或“交接”。</div>';
    }
    html += '<ul class="search-list">';
    results.forEach(function (r) {
      html += '<li><a href="#/a/' + r.a.id + '"><span class="result-category">' + esc((catById[r.a.category] || {}).name || r.a.category) + '</span><strong>' + esc(r.a.title) + '</strong><p>' + snippet(r.a.text, query.trim().split(/\s+/)[0]) + '</p><i>' + svgIcon('chevron') + '</i></a></li>';
    });
    html += '</ul><div class="search-tip">' + svgIcon('info') + '<span><strong>没有找到？</strong>试试更短的关键词，例如“密码”“时段”或“备份”。</span></div></div>';
    return html;
  }

  var main = document.getElementById('main');
  var searchBox = document.getElementById('search');

  function route() {
    var hash = location.hash || '#/';
    closeSuggests();
    if (hash.indexOf('#/a/') === 0) { document.body.dataset.view = 'article'; main.innerHTML = renderArticle(hash.slice(4)); }
    else if (hash.indexOf('#/c/') === 0) { document.body.dataset.view = 'category'; main.innerHTML = renderCategory(hash.slice(4)); }
    else if (hash.indexOf('#/q/') === 0) {
      // 畸形 hash（例如 #/q/%）会让 decodeURIComponent 抛 URIError；回退为空查询而不是崩溃。
      var q = '';
      try { q = decodeURIComponent(hash.slice(4)); } catch (error) { q = ''; }
      document.body.dataset.view = 'search';
      searchBox.value = q;
      main.innerHTML = renderSearch(q);
    }
    else { document.body.dataset.view = 'home'; searchBox.value = ''; main.innerHTML = renderHome(); }
    window.scrollTo(0, 0);
  }

  function hintFor(input) {
    var wrap = input.closest('.search-wrap, .home-search-wrap, .results-search-wrap');
    return wrap ? wrap.querySelector('.search-hint') : null;
  }

  function closeSuggests() {
    document.querySelectorAll('.search-hint').forEach(function (hint) {
      hint.style.display = 'none';
      hint.innerHTML = '';
    });
  }

  function updateSuggest(input) {
    var hint = hintFor(input);
    if (!hint) return;
    var q = input.value.trim();
    if (!q) { hint.style.display = 'none'; hint.innerHTML = ''; return; }
    var results = searchArticles(q).slice(0, 6);
    hint.style.display = 'block';
    hint.innerHTML = results.length
      ? results.map(function (r) { return '<a href="#/a/' + r.a.id + '">' + esc(r.a.title) + '<small>' + esc((catById[r.a.category] || {}).name || r.a.category) + '</small></a>'; }).join('')
      : '<p class="none">没有匹配，回车查看全部搜索建议</p>';
  }

  document.addEventListener('input', function (event) {
    if (event.target.matches('[data-help-search]')) updateSuggest(event.target);
  });
  document.addEventListener('keydown', function (event) {
    if (!event.target.matches('[data-help-search]')) return;
    if (event.key === 'Enter' && event.target.value.trim()) {
      location.hash = '#/q/' + encodeURIComponent(event.target.value.trim());
    }
    if (event.key === 'Escape') closeSuggests();
  });
  document.addEventListener('click', function (event) {
    if (!event.target.closest('.search-wrap, .home-search-wrap, .results-search-wrap')) closeSuggests();
  });

  window.addEventListener('hashchange', route);
  route();
})();
`.replace("__DATA__", () => inlineJson(data));

const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>会议室预约系统 · 帮助中心</title>
<style>
:root {
  /* 与产品 foundation.css 同源的纸色设计令牌 */
  --ink: #141413; --ink-2: #3d3d3a; --muted: #66655f; --faint: #6d6c66;
  --paper: #f5f4ed; --paper-soft: #f0eee6; --paper-raised: #faf9f5;
  --line: rgba(20, 20, 19, 0.075); --line-strong: rgba(20, 20, 19, 0.16);
  --accent: #d97757; --accent-text: #a4523d; --accent-soft: #faede7; --accent-line: #efc4b5;
  --radius: 9px; --radius-s: 7px;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html { -webkit-text-size-adjust: 100%; }
body { font-family: "PingFang SC", "HarmonyOS Sans SC", "Microsoft YaHei", "Noto Sans CJK SC", system-ui, sans-serif; color: var(--ink); background: var(--paper); font-size: 15px; line-height: 1.75; -webkit-font-smoothing: antialiased; }
a { color: var(--accent-text); text-decoration: none; }
a:hover { text-decoration: underline; }
a:focus-visible, button:focus-visible, input:focus-visible { outline: 2px solid var(--ink-2); outline-offset: 3px; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.9em; background: var(--paper-soft); border: 1px solid var(--line); border-radius: 5px; padding: 1px 5px; }
svg { width: 22px; height: 22px; }
body { min-height: 100vh; display: flex; flex-direction: column; }
button, input { font: inherit; }
button { color: inherit; }

.topbar { position: sticky; top: 0; z-index: 20; background: rgba(245, 244, 237, 0.94); backdrop-filter: blur(8px); border-bottom: 1px solid var(--line); }
.topbar-inner { width: min(100%, 1240px); margin: 0 auto; min-height: 72px; padding: 12px 32px; display: flex; align-items: center; gap: 28px; }
.brand { display: flex; align-items: center; gap: 11px; font-weight: 500; font-size: 15px; color: var(--ink); white-space: nowrap; }
.brand:hover { text-decoration: none; }
.brand-dot { width: 12px; height: 12px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 0 4px var(--accent-soft); }
.brand small { display: block; font-weight: 400; font-size: 11.5px; color: var(--faint); letter-spacing: 0.02em; }
.search-wrap { position: relative; flex: 1; max-width: 440px; margin: 0 auto; }
#search { width: 100%; height: 40px; border: 1px solid var(--line-strong); border-radius: 999px; padding: 8px 16px 8px 40px; font-size: 14px; font-family: inherit; color: var(--ink); outline: none; background: var(--paper-raised); transition: border-color .15s, box-shadow .15s; }
#search::placeholder, #home-search::placeholder { color: var(--faint); }
#search:focus { border-color: var(--ink-2); box-shadow: 0 0 0 3px rgba(20, 20, 19, .06); }
.search-mark { position: absolute; left: 14px; top: 50%; transform: translateY(-50%); color: var(--faint); display: flex; pointer-events: none; }
.search-mark svg { width: 17px; height: 17px; }
.search-hint { position: absolute; z-index: 30; top: calc(100% + 8px); left: 0; right: 0; background: var(--paper-raised); border: 1px solid var(--line-strong); border-radius: 10px; box-shadow: 0 12px 32px rgba(20, 24, 40, 0.1); display: none; overflow: hidden; }
.search-hint a { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 10px 14px; color: var(--ink); font-size: 14px; border-bottom: 1px solid var(--line); background: var(--paper-raised); }
.search-hint a:last-child { border-bottom: 0; }
.search-hint a:hover { background: var(--accent-soft); text-decoration: none; }
.search-hint a small { color: var(--faint); font-size: 12px; white-space: nowrap; }
.search-hint .none { padding: 11px 14px; color: var(--muted); font-size: 13.5px; }
.topbar-status { margin-left: auto; font-size: 12px; color: var(--faint); white-space: nowrap; display: flex; align-items: center; gap: 7px; }
.offline-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); }
body[data-view="home"] .topbar .search-wrap,
body[data-view="search"] .topbar .search-wrap { display: none; }

#main { flex: 1; min-height: 60vh; }
.home { width: min(88%, 1240px); margin: 0 auto; padding: 64px 0 76px; }
.home-intro { max-width: none; }
.eyebrow { margin-bottom: 9px; color: var(--accent-text); font-size: 13px; font-weight: 500; letter-spacing: .05em; }
.home-intro h1 { margin: 0 0 24px; font-size: clamp(34px, 4.2vw, 50px); line-height: 1.15; font-weight: 500; letter-spacing: -.04em; }
.home-search-wrap { position: relative; min-height: 66px; display: flex; align-items: center; gap: 14px; padding: 0 10px; border-bottom: 2px solid var(--ink); }
.home-search-mark { display: flex; color: var(--muted); }
.home-search-mark svg { width: 22px; height: 22px; }
#home-search { flex: 1; min-width: 0; border: 0; outline: none; background: transparent; color: var(--ink); font-size: 17px; }
.home-search-count { color: var(--faint); white-space: nowrap; font-size: 13px; }
.role-lanes { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin: 42px 0 50px; }
.role-lane { min-height: 82px; display: flex; align-items: center; justify-content: space-between; gap: 18px; border-top: 1px solid var(--line-strong); border-bottom: 1px solid var(--line-strong); padding: 14px 4px; color: var(--ink); }
.role-lane:hover { color: var(--accent-text); text-decoration: none; }
.role-lane span { display: flex; flex-direction: column; gap: 5px; }
.role-lane small { color: var(--muted); font-size: 12px; }
.role-lane strong { font-size: 15px; font-weight: 500; }
.role-lane i { display: flex; color: var(--faint); }
.role-lane i svg { width: 18px; height: 18px; }
.category-heading { display: flex; align-items: end; justify-content: space-between; margin-bottom: 10px; }
.category-heading h2 { font-size: 20px; font-weight: 500; }
.category-heading span { color: var(--faint); font-size: 13px; }
.category-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); column-gap: 34px; }
.category-item { position: relative; min-width: 0; min-height: 110px; display: grid; grid-template-columns: 50px minmax(0, 1fr); align-items: center; gap: 14px; border-bottom: 1px solid var(--line); color: var(--ink); }
.category-item:hover { text-decoration: none; }
.category-item:hover .cat-copy strong { color: var(--accent-text); }
.cat-icon { width: 50px; height: 50px; border-radius: 11px; display: grid; place-items: center; color: var(--accent-text); background: var(--accent-soft); }
.cat-icon svg { width: 22px; height: 22px; }
.cat-copy { min-width: 0; display: flex; flex-direction: column; gap: 5px; padding-right: 32px; }
.cat-copy strong { font-weight: 500; }
.cat-copy small { color: var(--muted); font-size: 12.5px; line-height: 1.5; }
.cat-count { position: absolute; top: 20px; right: 0; color: var(--faint); font-size: 12px; }

.crumbs { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; font-size: 12.5px; color: var(--faint); margin-bottom: 30px; }
.crumbs i { display: flex; color: var(--line-strong); }
.crumbs i svg { width: 14px; height: 14px; }
.crumbs a { color: var(--muted); }
.crumbs a:hover { color: var(--accent-text); }
.crumbs span:last-child { color: var(--muted); }

.category-shell, .search-shell { width: min(88%, 1120px); margin: 0 auto; padding: 42px 0 72px; }
.cat-header { display: flex; align-items: center; gap: 22px; margin-bottom: 36px; }
.cat-header .cat-icon { width: 80px; height: 80px; border-radius: 14px; flex: 0 0 auto; }
.cat-header .cat-icon svg { width: 30px; height: 30px; }
.cat-header p { color: var(--accent-text); font-size: 13px; font-weight: 500; letter-spacing: .04em; }
.cat-header h1 { margin: 3px 0 5px; font-size: 34px; line-height: 1.2; font-weight: 500; letter-spacing: -0.025em; }
.cat-header span { color: var(--muted); }
.article-list { list-style: none; overflow: hidden; border: 1px solid var(--line-strong); border-radius: 13px; background: var(--paper-raised); }
.article-list li { border-bottom: 1px solid var(--line); }
.article-list li:last-child { border-bottom: 0; }
.article-list a { min-height: 88px; display: grid; grid-template-columns: minmax(0, 1fr) auto 20px; align-items: center; gap: 16px; padding: 16px 22px; color: var(--ink); }
.article-list a:hover { background: var(--paper-soft); text-decoration: none; }
.article-list-copy { min-width: 0; display: flex; flex-direction: column; gap: 5px; }
.article-list-copy strong { font-weight: 500; }
.article-list-copy small { color: var(--muted); font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.article-list-role { display: flex; }
.article-list a > i { display: flex; color: var(--faint); }
.article-list a > i svg { width: 18px; height: 18px; }
.back-categories { width: fit-content; margin-top: 26px; display: flex; align-items: center; gap: 9px; color: var(--muted); }
.back-categories svg { width: 18px; height: 18px; }

.article-shell { width: min(88%, 1040px); margin: 0 auto; padding: 42px 0 72px; }
.article-shell > .crumbs { margin-bottom: 34px; }
.article { width: min(100%, 780px); margin: 0 auto; }
.article h1 { margin: 15px 0 12px; font-size: clamp(32px, 4vw, 44px); font-weight: 500; letter-spacing: -0.035em; line-height: 1.2; }
.article-meta { display: flex; align-items: center; gap: 9px; }
.meta-date { font-size: 12px; color: var(--faint); }
.chip { display: inline-block; font-size: 11px; line-height: 1; padding: 5px 9px; border-radius: 999px; background: var(--paper-soft); color: var(--muted); border: 1px solid var(--line-strong); }
.chip-all { background: var(--accent-soft); color: var(--accent-text); border-color: transparent; }
.article-content { margin-top: 26px; padding-top: 28px; border-top: 1px solid var(--line); font-size: 15.5px; }
.article-content > p:first-child { margin-top: 0; color: var(--muted); font-size: 17px; line-height: 1.75; }
.article-content h2 { font-size: 20px; font-weight: 500; margin: 32px 0 10px; }
.article-content h3 { font-size: 16px; font-weight: 500; margin: 24px 0 8px; }
.article-content p, .article-content ul, .article-content ol, .article-content table, .article-content blockquote { margin: 12px 0; }
.article-content ul, .article-content ol { padding-left: 23px; }
.article-content li { margin: 5px 0; }
.article-content blockquote { border-left: 3px solid var(--accent); background: var(--accent-soft); border-radius: 0 8px 8px 0; padding: 14px 18px; }
.article-content blockquote p { margin: 0; color: var(--ink-2); }
.article-content table { border-collapse: collapse; width: 100%; font-size: 13.8px; }
.article-content th { text-align: left; background: var(--paper-soft); font-weight: 500; }
.article-content th, .article-content td { border: 1px solid var(--line-strong); padding: 10px 12px; vertical-align: top; }
.article-content strong { font-weight: 600; }
.related { margin-top: 38px; padding-top: 24px; border-top: 1px solid var(--line); }
.related h2 { font-size: 17px; font-weight: 500; margin-bottom: 10px; }
.related ul { list-style: none; border-top: 1px solid var(--line); }
.related li { border-bottom: 1px solid var(--line); }
.related li a { min-height: 66px; display: flex; align-items: center; justify-content: space-between; gap: 18px; color: var(--ink); }
.related li a:hover { text-decoration: none; }
.related li a:hover strong { color: var(--accent-text); }
.related li span { display: flex; flex-direction: column; gap: 3px; }
.related li strong { font-weight: 500; }
.related li small { color: var(--faint); font-size: 12px; }
.related li i { display: flex; color: var(--faint); }
.related li svg { width: 17px; height: 17px; }
.pager { width: min(100%, 780px); margin: 44px auto 0; display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
.pager a { min-height: 76px; border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; color: var(--faint); background: var(--paper-raised); display: flex; align-items: center; gap: 12px; }
.pager a:hover { border-color: var(--line-strong); text-decoration: none; }
.pager a span { display: flex; flex-direction: column; }
.pager a strong { color: var(--ink); font-size: 13.5px; font-weight: 500; margin-top: 3px; }
.pager a svg { width: 18px; height: 18px; }
.pager-next { justify-content: flex-end; text-align: right; }
.support-box { width: min(100%, 780px); margin: 28px auto 0; background: var(--paper-soft); padding: 16px 18px; display: flex; flex-direction: column; gap: 3px; font-size: 13.5px; color: var(--muted); }
.support-box strong { color: var(--ink); font-weight: 500; }

.results-search-wrap { position: relative; max-width: 900px; min-height: 64px; display: flex; align-items: center; gap: 13px; padding: 0 7px; border-bottom: 2px solid var(--ink); }
.results-search-wrap > span { display: flex; color: var(--muted); }
.results-search-wrap input { flex: 1; min-width: 0; border: 0; outline: none; background: transparent; color: var(--ink); font-size: 20px; }
.search-heading { margin: 42px 0 14px; display: flex; align-items: end; justify-content: space-between; gap: 18px; }
.search-heading h1 { font-size: 29px; font-weight: 500; letter-spacing: -0.02em; }
.search-heading p { color: var(--faint); }
.search-list { list-style: none; border-top: 1px solid var(--line-strong); }
.search-list li { border-bottom: 1px solid var(--line); }
.search-list a { position: relative; min-height: 116px; display: flex; flex-direction: column; align-items: flex-start; gap: 5px; padding: 18px 48px 18px 0; color: var(--ink); }
.search-list a:hover { text-decoration: none; }
.search-list a:hover strong { color: var(--accent-text); }
.search-list strong { font-size: 17px; font-weight: 500; }
.search-list p { color: var(--muted); font-size: 13.5px; line-height: 1.55; }
.result-category { color: var(--accent-text); font-size: 12.5px; }
.search-list i { position: absolute; right: 8px; top: 50%; transform: translateY(-50%); display: flex; color: var(--faint); }
.search-list i svg { width: 18px; height: 18px; }
.empty-search, .search-tip { margin-top: 26px; background: var(--paper-soft); color: var(--muted); padding: 16px 18px; }
.search-tip { display: flex; align-items: flex-start; gap: 11px; }
.search-tip svg { width: 18px; height: 18px; color: var(--accent-text); flex: 0 0 auto; margin-top: 3px; }
.search-tip strong { color: var(--ink); font-weight: 500; }
.site-footer { border-top: 1px solid var(--line); color: var(--faint); font-size: 12px; text-align: center; padding: 22px 24px 28px; }
body[data-embedded="true"] .topbar,
body[data-embedded="true"] .site-footer { display: none; }
body[data-embedded="true"] .home,
body[data-embedded="true"] .category-shell,
body[data-embedded="true"] .search-shell,
body[data-embedded="true"] .article-shell { padding-top: 36px; }

@media (max-width: 860px) {
  .topbar-inner { padding-inline: 22px; gap: 16px; }
  .topbar-status { font-size: 0; }
  .topbar-status .offline-dot { display: block; }
  .category-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .role-lanes { grid-template-columns: 1fr; gap: 0; }
  .article-list-copy small { white-space: normal; }
}
@media (max-width: 620px) {
  .topbar-inner { min-height: 70px; padding: 11px 16px; flex-wrap: wrap; }
  .topbar .search-wrap { order: 3; flex-basis: 100%; max-width: none; }
  .brand { flex: 1; }
  body[data-view="home"] .topbar-status,
  body[data-view="search"] .topbar-status { display: flex; }
  .home, .category-shell, .search-shell, .article-shell { width: 92%; }
  .home { padding-top: 42px; }
  .home-intro h1 { font-size: 36px; }
  .home-search-count { display: none; }
  .category-grid { grid-template-columns: 1fr; }
  .category-item { min-height: 96px; }
  .cat-header { align-items: flex-start; }
  .cat-header .cat-icon { width: 64px; height: 64px; }
  .cat-header h1 { font-size: 29px; }
  .article-list a { grid-template-columns: minmax(0, 1fr) 18px; padding-inline: 17px; }
  .article-list-role { display: none; }
  .crumbs { overflow: hidden; flex-wrap: nowrap; white-space: nowrap; }
  .crumbs span:last-child { overflow: hidden; text-overflow: ellipsis; }
  .article h1 { font-size: 34px; }
  .article-content { font-size: 15px; }
  .article-content table { display: block; max-width: 100%; overflow-x: auto; }
  .pager { grid-template-columns: 1fr; }
  .search-heading { align-items: flex-start; flex-direction: column; gap: 5px; }
  .search-heading h1 { font-size: 25px; }
}
@media print {
  .topbar, .pager, .support-box, .related, .site-footer { display: none !important; }
  .article-shell { width: auto; padding: 0; }
  body { background: #fff; }
  .article-content { font-size: 12pt; }
}
</style>
</head>
<body>
<header class="topbar">
  <div class="topbar-inner">
    <a class="brand" href="#/"><span class="brand-dot"></span><span>会议室预约系统<small>帮助中心</small></span></a>
    <div class="search-wrap">
      <span class="search-mark">${icon("search")}</span>
      <input id="search" data-help-search type="search" placeholder="搜索帮助文章" autocomplete="off" aria-label="搜索帮助文章">
      <div class="search-hint"></div>
    </div>
    <span class="topbar-status"><span class="offline-dot"></span>完全离线 · 适用 ${PRODUCT_VERSION}</span>
  </div>
</header>
<main id="main"></main>
<footer class="site-footer">完全离线运行，不连接任何外部服务、不发送任何数据 · 内容基于 ${PRODUCT_VERSION} 实际界面与源码编写 · ${CONTENT_DATE}</footer>
<script>
var ICONS = ${inlineJson(Object.fromEntries(Object.entries(ICONS).map(([k, v]) => [k, icon(k)])))};
var VERSION = ${inlineJson(PRODUCT_VERSION)};
${clientJs}
</script>
</body>
</html>
`;

// 全部校验与警告必须在产出成品前完成：任何警告都直接失败，不写出 index.html，
// 避免留下"看似成功"的坏成品（调用方按非零退出码判定构建失败）。
if (warnings.length) {
  console.error("⚠️ 帮助内容校验未通过，拒绝写出成品：");
  for (const w of warnings) console.error("  - " + w);
  process.exit(1);
}

mkdirSync(dirname(outputPath), { recursive: true });
writeFileSync(outputPath, html);

const sizeKb = (statSync(outputPath).size / 1024).toFixed(1);
console.log(`✅ ${articles.length} 篇文章 · ${CATEGORIES.length} 个分类 · ${data.categories.map((c) => c.id + ":" + c.count).join(" ")}`);
console.log(`→ ${relative(V2_ROOT, outputPath)} (${sizeKb} KB)`);
