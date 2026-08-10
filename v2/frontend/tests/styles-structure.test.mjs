import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sourceRoot = path.join(root, "src");
const stylesRoot = path.join(sourceRoot, "styles");
const manifestPath = path.join(sourceRoot, "styles.css");
const expectedFiles = [
  "foundation.css",
  "login.css",
  "setup.css",
  "shell.css",
  "dashboard.css",
  "history.css",
  "rooms.css",
  "calendar.css",
  "drawer-shell.css",
  "booking-forms.css",
  "settings.css",
  "system.css",
  "users.css",
  "public-display.css",
  "runtime-states.css",
  "responsive.css",
  "production-flows.css",
  "system-extensions.css",
  "accessibility.css",
];
const frozenSourceSha256 = "179832db7fa9d04094bdfcddb7d9d048f1854cb5de7a156bf36e4a170bc8e4db";

function importedFiles() {
  const manifest = fs.readFileSync(manifestPath, "utf8");
  return [...manifest.matchAll(/^@import "\.\/styles\/([^"]+)";$/gm)]
    .map((match) => match[1]);
}

test("global CSS manifest preserves the frozen cascade order", () => {
  assert.deepEqual(importedFiles(), expectedFiles);
  const manifestLines = fs.readFileSync(manifestPath, "utf8").trim().split("\n");
  assert.equal(manifestLines.length, expectedFiles.length + 1);
});

test("every feature stylesheet is registered exactly once without nested imports", () => {
  const actualFiles = fs.readdirSync(stylesRoot)
    .filter((name) => name.endsWith(".css"))
    .sort();
  assert.deepEqual(actualFiles, [...expectedFiles].sort());
  for (const name of actualFiles) {
    assert.doesNotMatch(fs.readFileSync(path.join(stylesRoot, name), "utf8"), /@import\s/);
  }
});

test("split styles reconstruct the frozen source byte for byte", () => {
  const source = expectedFiles
    .map((name) => fs.readFileSync(path.join(stylesRoot, name)))
    .reduce((chunks, content) => Buffer.concat([chunks, content]), Buffer.alloc(0));
  assert.equal(crypto.createHash("sha256").update(source).digest("hex"), frozenSourceSha256);
});

test("React keeps one global CSS entrypoint", () => {
  const main = fs.readFileSync(path.join(sourceRoot, "main.jsx"), "utf8");
  assert.match(main, /import "\.\/styles\.css";/);
  assert.doesNotMatch(main, /import "\.\/styles\//);
});
