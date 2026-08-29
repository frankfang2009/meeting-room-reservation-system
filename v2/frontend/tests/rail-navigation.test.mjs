import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const app = fs.readFileSync(path.join(frontendRoot, "src/App.jsx"), "utf8");
const shell = fs.readFileSync(path.join(frontendRoot, "src/styles/shell.css"), "utf8");
const responsive = fs.readFileSync(path.join(frontendRoot, "src/styles/responsive.css"), "utf8");

test("primary, help, and account rail actions share one 24px icon system", () => {
  assert.match(app, /<Icon size=\{24\} weight="regular" \/>/);
  assert.match(app, /<Question size=\{24\} weight="regular" \/>/);
  assert.match(app, /<UserCircle size=\{24\} weight="regular" \/>/);
  assert.doesNotMatch(app, /<UserCircle size=\{42\}/);
});

test("the role-filtered rail uses one compact vertical rhythm without placeholder rows", () => {
  assert.match(app, /NAV_ITEMS\.filter\(\(item\) => !item\.permission \|\| permissions\[item\.permission\]\)\.map/);
  assert.match(shell, /\.rail-nav \{[\s\S]*?gap: 12px;[\s\S]*?padding-top: 18px;/);
  assert.match(shell, /\.brand-mark,[\s\S]*?\.avatar-button \{[\s\S]*?width: 46px;[\s\S]*?height: 46px;/);
  assert.match(shell, /\.avatar-button\.active:hover \{[\s\S]*?background: color-mix\(in srgb, var\(--terracotta\) 8%, transparent\);/);
  assert.match(responsive, /@media \(max-height: 820px\)[\s\S]*?\.rail-nav \{[\s\S]*?gap: 8px;[\s\S]*?padding-top: 10px;/);
  assert.match(responsive, /@media \(max-height: 820px\)[\s\S]*?\.brand-mark,[\s\S]*?\.avatar-button \{[\s\S]*?height: 46px;[\s\S]*?\.brand-mark \{[\s\S]*?height: 72px;/);
});
