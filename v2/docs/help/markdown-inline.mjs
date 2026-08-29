function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function renderInline(text, articlesById, source = "Markdown") {
  const codeSpans = [];
  const wikiAnchors = [];
  const raw = String(text).replace(/`([^`]+)`/g, (_match, code) => {
    const token = `\uE000CODE${codeSpans.length}\uE001`;
    codeSpans.push(`<code>${escapeHtml(code)}</code>`);
    return token;
  });

  // Wiki Link 在全局转义前解析，锚点整体走占位符回填：
  // 无论使用目标标题还是自定义显示文字，label 都恰好经过一次 escapeHtml。
  const withWiki = raw.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_match, rawId, customLabel) => {
    const id = rawId.trim();
    if (!/^[a-z0-9-]+$/.test(id)) {
      throw new Error(`Wiki Link 目标格式无效：${source} -> ${id}`);
    }
    const target = articlesById.get(id);
    if (!target) {
      throw new Error(`Wiki Link 指向不存在的文章：${source} -> ${id}`);
    }
    const label = customLabel === undefined ? target.title : customLabel.trim();
    if (!label) {
      throw new Error(`Wiki Link 显示文字不能为空：${source} -> ${id}`);
    }
    wikiAnchors.push(`<a class="article-link" href="#/a/${id}">${escapeHtml(label)}</a>`);
    return `\uE002WIKI${wikiAnchors.length - 1}\uE003`;
  });

  let html = escapeHtml(withWiki);
  html = html.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_match, label, url) => {
    const safe = /^(https?:|#)/.test(url) ? url : "#";
    return `<a href="${safe}">${label}</a>`;
  });
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\uE000CODE(\d+)\uE001/g, (_match, index) => codeSpans[Number(index)]);
  html = html.replace(/\uE002WIKI(\d+)\uE003/g, (_match, index) => wikiAnchors[Number(index)]);
  return html;
}

export function renderPlainText(markdown, articlesById, source = "Markdown") {
  const codeSpans = [];
  let text = String(markdown).replace(/`([^`]+)`/g, (_match, code) => {
    const token = `\uE000CODE${codeSpans.length}\uE001`;
    codeSpans.push(code);
    return token;
  });

  text = text.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_match, rawId, customLabel) => {
    const id = rawId.trim();
    if (!/^[a-z0-9-]+$/.test(id)) {
      throw new Error(`Wiki Link 目标格式无效：${source} -> ${id}`);
    }
    const target = articlesById.get(id);
    if (!target) {
      throw new Error(`Wiki Link 指向不存在的文章：${source} -> ${id}`);
    }
    const label = customLabel === undefined ? target.title : customLabel.trim();
    if (!label) {
      throw new Error(`Wiki Link 显示文字不能为空：${source} -> ${id}`);
    }
    return label;
  });
  text = text
    .replace(/\[([^\]]+)\]\([^)\s]+\)/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/^\s*#{1,6}\s+/gm, "")
    .replace(/^\s*>\s?/gm, "")
    .replace(/^\s*(?:[-*]|\d+\.)\s+/gm, "")
    .replace(/\|/g, " ")
    .replace(/\uE000CODE(\d+)\uE001/g, (_match, index) => codeSpans[Number(index)])
    .replace(/\s+/g, " ")
    .trim();
  return text;
}
