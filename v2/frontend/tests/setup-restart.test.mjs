import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { runSetupRestartTransition, SetupRestartStatus } from "../src/setup-restart.js";

test("rendered setup status covers waiting, failure with retry, and ready transition", () => {
  const waiting = renderToStaticMarkup(createElement(SetupRestartStatus, { state: "waiting" }));
  assert.match(waiting, /data-restart-state="waiting"/);
  assert.match(waiting, /自动进入登录页/);
  assert.doesNotMatch(waiting, /重新检查服务/);

  const failed = renderToStaticMarkup(createElement(SetupRestartStatus, { state: "failed", onRetry: () => {} }));
  assert.match(failed, /data-restart-state="failed"/);
  assert.match(failed, /① 启动系统/);
  assert.match(failed, /重新检查服务/);

  const ready = renderToStaticMarkup(createElement(SetupRestartStatus, { state: "ready" }));
  assert.match(ready, /data-restart-state="ready"/);
  assert.match(ready, /正在进入登录页/);
});

test("production setup transition drives waiting to ready only after stable LAN health", async () => {
  const states = [];
  let enteredApplication = false;
  const health = [
    { ok: true, status: "ready", setup_complete: true, bind_mode: "loopback" },
    { ok: true, status: "ready", setup_complete: true, bind_mode: "lan" },
    { ok: true, status: "ready", setup_complete: true, bind_mode: "lan" },
  ];
  await runSetupRestartTransition({
    probe: async () => health.shift(),
    pause: async () => {},
    attempts: 3,
    stableChecks: 2,
    onState: (state) => states.push(state),
    onReady: () => { enteredApplication = true; },
  });
  assert.deepEqual(states, ["waiting", "ready"]);
  assert.equal(enteredApplication, true);
});

test("failed setup restart stays failed and a retry can later reach ready", async () => {
  const firstStates = [];
  await assert.rejects(runSetupRestartTransition({
    probe: async () => { throw new Error("offline"); },
    pause: async () => {},
    attempts: 2,
    onState: (state) => firstStates.push(state),
  }), (error) => error.code === "SERVICE_RESTART_TIMEOUT");
  assert.deepEqual(firstStates, ["waiting", "failed"]);

  const retryStates = [];
  await runSetupRestartTransition({
    probe: async () => ({ ok: true, status: "ready", setup_complete: true, bind_mode: "lan" }),
    pause: async () => {},
    attempts: 2,
    stableChecks: 2,
    onState: (state) => retryStates.push(state),
  });
  assert.deepEqual(retryStates, ["waiting", "ready"]);
});
