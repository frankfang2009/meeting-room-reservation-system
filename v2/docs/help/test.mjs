#!/usr/bin/env node

import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { join, relative, resolve } from "node:path";
import { tmpdir } from "node:os";
import { renderInline, renderPlainText } from "./markdown-inline.mjs";

const wikiArticles = new Map([
  ["bk-conflict", { id: "bk-conflict", title: "时段被占了怎么办？（草稿保留全解）" }],
]);

assert.equal(
  renderInline("查看 [[bk-conflict]]", wikiArticles, "fixture.md"),
  '查看 <a class="article-link" href="#/a/bk-conflict">时段被占了怎么办？（草稿保留全解）</a>',
  "[[id]] 应使用目标文章标题生成站内链接",
);
assert.equal(
  renderInline("查看 [[bk-conflict|草稿处理方法]]", wikiArticles, "fixture.md"),
  '查看 <a class="article-link" href="#/a/bk-conflict">草稿处理方法</a>',
  "[[id|显示文字]] 应保留自定义显示文字",
);
assert.equal(
  renderInline("语法示例 `[[bk-conflict]]`", wikiArticles, "fixture.md"),
  "语法示例 <code>[[bk-conflict]]</code>",
  "行内代码中的 Wiki Link 不应解析",
);
assert.throws(
  () => renderInline("查看 [[missing-article]]", wikiArticles, "content/demo.md"),
  /Wiki Link 指向不存在的文章：content\/demo\.md -> missing-article/,
  "未知 Wiki Link 目标必须阻止构建",
);
assert.equal(
  renderPlainText("字段规则见 [[bk-conflict|草稿处理方法]]。", wikiArticles, "fixture.md"),
  "字段规则见 草稿处理方法。",
  "搜索文本应只保留 Wiki Link 的显示文字",
);

const ROOT = fileURLToPath(new URL(".", import.meta.url));
const V2_ROOT = resolve(ROOT, "../../..");

function walkMarkdown(dir) {
  return readdirSync(dir).sort().flatMap((name) => {
    const full = join(dir, name);
    return statSync(full).isDirectory() ? walkMarkdown(full) : (name.endsWith(".md") ? [full] : []);
  });
}

const contentFiles = walkMarkdown(join(ROOT, "content"));
assert.equal(contentFiles.length, 55, "内容源必须保持 55 篇");
const contentUpdated = [];
const contentIds = contentFiles.map((file) => {
  const text = readFileSync(file, "utf8");
  const match = text.match(/^id:\s*([^\n]+)/m);
  assert.ok(match, `文章缺少 id：${file}`);
  assert.ok(/^[a-z0-9-]+$/.test(match[1].trim()), `文章 id 只能使用小写字母、数字和连字符：${file}`);
  const summaryMatch = text.match(/^summary:\s*([^\n]+)$/m);
  assert.ok(summaryMatch, `文章缺少列表摘要 summary：${file}`);
  const summary = summaryMatch[1].trim().replace(/^['"]|['"]$/g, "");
  const summaryLength = [...summary].length;
  assert.ok(summaryLength >= 24 && summaryLength <= 50, `文章 summary 应为 24–50 个字符：${file}（当前 ${summaryLength}）`);
  assert.doesNotMatch(summary, /\[\[|\]\]|[*_`#]|…|\.\.\./, `文章 summary 不得包含 Markdown、Wiki Link 或省略号：${file}`);
  assert.match(text, /^updated:\s*\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$/m, `文章需标记 ISO 格式的事实复核日期：${file}`);
  contentUpdated.push(text.match(/^updated:\s*(\d{4}-\d{2}-\d{2})$/m)[1]);
  assert.match(text, /^##\s+/m, `文章需要可扫描的小节：${file}`);
  return match[1].trim();
});

const readArticle = (...parts) => readFileSync(join(ROOT, "content", ...parts), "utf8");
const upgradeArticle = readArticle("admin", "ad-upgrade.md");
assert.match(upgradeArticle, /自动[^\n]*24 小时/, "升级文章应区分 24 小时自动周期检查");
assert.match(upgradeArticle, /手动[^\n]*60 秒/, "升级文章应写明手动检查的 60 秒限频");

const metricsArticle = readArticle("data", "dt-metrics.md");
assert.doesNotMatch(metricsArticle, /全单位页面展示人员/, "数据中心不得声称全单位页面提供人员分布");
assert.match(metricsArticle, /全单位页面展示时段、房间和单位标签分布/, "数据中心应列出实际存在的全单位分布页");

const loggedOutArticle = readArticle("account", "ac-logged-out.md");
assert.match(loggedOutArticle, /账号被停用[^\n]*管理员[^\n]*重新启用/, "停用账号必须先由管理员重新启用，不能直接重新登录");

const dataLocationArticle = readArticle("security", "sec-where.md");
assert.doesNotMatch(dataLocationArticle, /数据的去向只有一个/, "安全文章不得承诺客户端不会产生用户主动创建的副本");
assert.match(dataLocationArticle, /导出[^\n]*客户端[^\n]*副本/, "安全文章应说明导出等主动操作会在客户端产生副本");

const saveFailureArticle = readArticle("troubleshooting", "tr-save-fail.md");
assert.doesNotMatch(saveFailureArticle, /\[\[tr-offline(?:\||\]\])/, "登录业务页保存失败不得跳转到仅适用于公开大屏的离线文章");
assert.match(saveFailureArticle, /\[\[tr-cannot-open(?:\||\]\])/, "登录业务页保存失败应跳转到系统页面无法访问的排查文章");

const evidenceMatrix = readFileSync(join(ROOT, "ARTICLE-EVIDENCE.md"), "utf8");
const evidenceRows = [...evidenceMatrix.matchAll(/^\|\s*`([^`]+)`\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(R[123])\s*\|\s*(.*?)\s*\|$/gm)]
  .map((match) => ({ id: match[1], sourceCode: match[2], sourceTests: match[3], level: match[4], boundary: match[5] }));
assert.equal(evidenceRows.length, 55, "证据矩阵必须包含 55 条结构化文章记录");
assert.equal(new Set(evidenceRows.map(({ id }) => id)).size, 55, "证据矩阵不得重复文章 ID");
assert.deepEqual(new Set(evidenceRows.map(({ id }) => id)), new Set(contentIds), "证据矩阵必须与 55 篇内容源一一对应");

function verifyEvidenceRefs(row, field, expectedTestPath) {
  const refs = [...field.matchAll(/`([^`]+)#([^`]+)`/g)].map((match) => ({ path: match[1], fragment: match[2] }));
  assert.ok(refs.length > 0, `证据矩阵 ${row.id} 缺少精确的${expectedTestPath ? "测试" : "代码"}路径和符号`);
  for (const ref of refs) {
    assert.match(ref.path, /^v2\//, `证据路径必须相对于 V2 仓库：${row.id} -> ${ref.path}`);
    if (expectedTestPath) assert.match(ref.path, /(?:^|\/)tests?(?:\/|$)|\.test\./, `自动化证据必须指向测试文件：${row.id} -> ${ref.path}`);
    const absolutePath = resolve(V2_ROOT, ref.path);
    assert.ok(!relative(V2_ROOT, absolutePath).startsWith(".."), `证据路径不得越出 V2 仓库：${row.id} -> ${ref.path}`);
    assert.ok(existsSync(absolutePath), `证据文件不存在：${row.id} -> ${ref.path}`);
    assert.match(readFileSync(absolutePath, "utf8"), new RegExp(ref.fragment.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")), `证据符号或测试名称不存在：${row.id} -> ${ref.path}#${ref.fragment}`);
  }
}

for (const row of evidenceRows) {
  verifyEvidenceRefs(row, row.sourceCode, false);
  verifyEvidenceRefs(row, row.sourceTests, true);
  assert.ok(row.boundary.trim().length >= 4, `证据矩阵 ${row.id} 必须保留 R3 或事实边界`);
}
assert.doesNotMatch(readFileSync(join(ROOT, "content", "security", "sec-audit.md"), "utf8"), /审计日志\*\*永久保留\*\*/, "审计文章不得承诺永久保留");
assert.doesNotMatch(readFileSync(join(ROOT, "content", "handover", "ho-no-response.md"), "utf8"), /自动退回你名下/, "交接超时文章不得把当前过滤行为写成自动归属变更");

const buildOutput = mkdtempSync(join(tmpdir(), "meeting-room-help-"));
const outputPath = join(buildOutput, "index.html");
const build = spawnSync(process.execPath, ["build.mjs", "--output", outputPath], {
  cwd: ROOT,
  encoding: "utf8",
});

assert.equal(build.status, 0, build.stderr || build.stdout);
assert.match(build.stdout, /55 篇文章 · 9 个分类/);

const html = readFileSync(outputPath, "utf8");
const dataMatch = html.match(/var DATA = (\{[\s\S]*?\});\n/);
assert.ok(dataMatch, "成品应内嵌帮助中心数据");
const outputData = JSON.parse(dataMatch[1]);
const loginOutput = outputData.articles.find((article) => article.id === "qs-login");
assert.equal(loginOutput.summary, "了解账号由谁创建、首次登录需要准备什么，以及登录失败时如何处理。", "成品应保留人工编写的文章摘要");
assert.match(loginOutput.text, /账号由本单位管理员创建/, "搜索索引仍应保留完整正文，而不是被摘要替换");

assert.match(html, /class="home-intro"/, "首页应使用宽幅引导区");
assert.match(html, /id="home-search"/, "首页应有独立的大搜索入口");
assert.match(html, /浏览全部分类/, "首页应明确标出分类入口");
assert.match(html, /class="category-shell"/, "分类页应使用宽幅无侧栏布局");
assert.match(html, /class="article-shell"/, "文章页应使用居中的阅读布局");
assert.match(html, /class="search-shell"/, "搜索结果应使用独立宽幅布局");
assert.match(html, /article-list-copy[^\n]+esc\(a\.summary\)/, "分类页应展示人工摘要");
assert.doesNotMatch(html, /a\.text\.slice\(0,\s*72\)/, "分类页不得继续截取正文前 72 个字符");
assert.match(html, /snippet\(r\.a\.text,/, "搜索结果应继续使用关键词附近的正文片段");
// DATA 以 \u003c 编码内联，Wiki Link 断言先经 JSON 解码再检查解码后的文章 HTML。
const articleHtmlAll = outputData.articles.map((article) => article.html).join("\n");
assert.match(articleHtmlAll, /<a class="article-link" href="#\/a\/bk-create">如何创建预约？<\/a>/, "正文 Wiki Link 应生成文章 hash 路由");
assert.doesNotMatch(html, /\[\[bk-create\|如何创建预约？\]\]/, "成品不得残留已解析的 Wiki Link 源码");
assert.match(articleHtmlAll, /<a class="article-link" href="#\/a\/tr-cannot-open">打不开系统页面<\/a>/, "表格单元格内的自定义 Wiki Link 应完整解析");
assert.doesNotMatch(html, /\[\[[a-z0-9-]+(?:\|[^\]]*)?\]\]/, "成品任何位置都不得残留 Wiki Link 源码");
assert.doesNotMatch(html, /"text":"[^"]*\[\[/, "搜索索引不得暴露 Wiki Link 源码");
const dataLine = html.split("\n").find((line) => line.includes("var DATA = "));
assert.ok(dataLine, "成品应内嵌帮助中心数据");
assert.ok(!dataLine.includes("<"), "DATA 内联 JSON 必须把 < 编码为 \\u003c，杜绝 </script> 逃逸");
const expectedContentDate = contentUpdated.reduce((latest, value) => (value > latest ? value : latest));
assert.match(
  html,
  new RegExp(`${expectedContentDate}</footer>`),
  "页脚内容日期必须从文章最大 updated 派生（单一来源）",
);
assert.match(html, /\.home-intro\s*\{\s*max-width:\s*none;\s*\}/, "首页搜索区应跟随 1240px 首页容器自适应");
assert.match(html, /\.pager\s*\{\s*width:\s*min\(100%,\s*780px\);/, "文章翻页区应与 780px 正文列对齐");
assert.doesNotMatch(html, /class="page-side"/, "新版不得保留常驻左侧目录");
assert.doesNotMatch(html, /<(?:script|link|img)[^>]+(?:src|href)="https?:\/\//i, "单文件帮助中心不得加载外部资源");
assert.doesNotMatch(html, /\b(?:fetch|XMLHttpRequest)\s*\(/, "单文件帮助中心不得发起网络请求");
assert.doesNotMatch(html, /data-helpful|这篇文章是否解决了你的问题/, "没有反馈接收端时不得展示假反馈控件");

rmSync(buildOutput, { recursive: true, force: true });

console.log("✅ help-center layout contract passed");
