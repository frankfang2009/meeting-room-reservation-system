import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const v2Root = path.resolve(frontendRoot, "..");
const app = fs.readFileSync(path.join(frontendRoot, "src/App.jsx"), "utf8");
const packageJson = JSON.parse(fs.readFileSync(path.join(frontendRoot, "package.json"), "utf8"));

test("help center is an authenticated utility view and a low-emphasis login action", () => {
  // 近恒真正则修复：必须命中 @phosphor-icons/react 导入块中的 Question 一行，
  // 而不是任意位置出现的 Question 字样。
  assert.match(app, /^\s{2}Question,$/m);
  assert.match(app, /activeView === "help"/);
  assert.match(app, /aria-label="打开帮助中心"/);
  assert.match(app, /className="rail-utility"/);
  assert.match(app, /className="login-help-link"/);
  assert.match(app, /<HelpCenter/);
});

test("authenticated help opens the same complete static reader without an intermediate home", () => {
  const component = fs.readFileSync(path.join(frontendRoot, "src/features/help/HelpCenter.jsx"), "utf8");
  const styles = fs.readFileSync(path.join(frontendRoot, "src/styles/help-center.css"), "utf8");
  assert.match(component, /className="main-canvas help-reader-canvas"/);
  assert.match(component, /className="help-reader-frame"/);
  assert.match(component, /\/help\/\?embedded=1/);
  assert.doesNotMatch(component, /help-center-canvas|help-experimental|HELP_CATEGORIES|QUICK_LINKS|搜索问题，例如：如何交接预约/);
  assert.match(styles, /\.help-reader-toolbar/);
  assert.doesNotMatch(component, /\bfetch\s*\(|localStorage|sessionStorage|api\./);
});

test("production build includes the public offline help artifact", () => {
  assert.match(packageJson.scripts.build, /docs\/help\/build\.mjs/);
  assert.equal(fs.existsSync(path.join(v2Root, "docs/help/test.mjs")), true);
  assert.equal(fs.existsSync(path.join(v2Root, "docs/help/content")), true);
});
