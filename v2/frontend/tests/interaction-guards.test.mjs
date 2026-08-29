import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  createExclusiveGuard,
  createLatestRequestGuard,
  createLifetimeGuard,
} from "../src/async-guards.js";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const app = fs.readFileSync(path.join(frontendRoot, "src/App.jsx"), "utf8");
const dataCenter = fs.readFileSync(path.join(frontendRoot, "src/features/reports/DataCenter.jsx"), "utf8");

test("exclusive guard rejects a second synchronous submission and reopens after completion", () => {
  const guard = createExclusiveGuard();

  assert.equal(guard.acquire(), true);
  assert.equal(guard.acquire(), false, "同一事件循环内的第二次提交必须被拒绝");
  guard.release();
  assert.equal(guard.acquire(), true, "请求结束后必须允许用户重试");
});

test("latest-request guard invalidates every older response", () => {
  const guard = createLatestRequestGuard();
  const first = guard.next();
  assert.equal(guard.isCurrent(first), true);

  const second = guard.next();
  assert.equal(guard.isCurrent(first), false, "先返回的旧请求不得覆盖新状态");
  assert.equal(guard.isCurrent(second), true);
});

test("lifetime guard blocks work that resolves after its owning session unmounts", () => {
  const lifetime = createLifetimeGuard();
  assert.equal(lifetime.isActive(), true);

  lifetime.end();
  assert.equal(lifetime.isActive(), false, "旧会话卸载后不得再触发下载或提示");
});

test("lifetime guard reactivates when StrictMode repeats effect setup after its probe cleanup", () => {
  const lifetime = createLifetimeGuard();
  lifetime.end();
  lifetime.begin();
  assert.equal(lifetime.isActive(), true, "StrictMode 的模拟卸载不得永久关闭仍挂载的会话");
});

test("production handlers are wired to the tested guards", () => {
  assert.match(app, /createExclusiveGuard/);
  assert.match(app, /createLatestRequestGuard/);
  assert.match(app, /createLifetimeGuard/);
  assert.match(dataCenter, /createLifetimeGuard/);
  for (const name of ["savingGuard", "backupGuard", "handoverWithdrawGuard"]) {
    assert.match(app, new RegExp(`${name}\\.acquire\\(\\)`));
    assert.match(app, new RegExp(`${name}\\.release\\(\\)`));
  }
  for (const name of ["upcomingRequests", "handoverRequests"]) {
    assert.match(app, new RegExp(`${name}\\.next\\(\\)`));
    assert.match(app, new RegExp(`${name}\\.isCurrent\\(requestNumber\\)`));
  }
  assert.match(app, /sessionLifetime\.isActive\(\)/);
  assert.match(dataCenter, /sessionLifetime\.isActive\(\)/);
  assert.match(app, /sessionLifetime\.begin\(\)/);
  assert.match(dataCenter, /sessionLifetime\.begin\(\)/);
});
