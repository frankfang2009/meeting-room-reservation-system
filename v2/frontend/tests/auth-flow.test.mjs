import assert from "node:assert/strict";
import test from "node:test";

import { reauthenticateContext, scopedAppKey } from "../src/auth-flow.js";

function clientFor(session, bootstrap, { failLogin = null } = {}) {
  const calls = [];
  let sessionReads = 0;
  return {
    calls,
    async getSession() {
      calls.push(`session:${sessionReads}`);
      sessionReads += 1;
      return sessionReads === 1 ? { authenticated: false } : session;
    },
    async login(username, password) {
      calls.push(`login:${username}:${password}`);
      if (failLogin) throw failLogin;
      return { authenticated: true };
    },
    async getBootstrap() {
      calls.push("bootstrap");
      return bootstrap;
    },
  };
}

const employeePermissions = {
  manageRooms: false,
  manageUsers: false,
  manageSystem: false,
  viewReports: true,
  viewOverallReports: false,
  viewOtherUserReports: false,
};
const serverClock = { serverDate: "2026-08-10", serverTime: "08:00:00" };

test("administrator to employee reauthentication validates bootstrap before changing remount key", async () => {
  const session = { authenticated: true, currentUser: { id: "employee-1", role: "employee" } };
  const client = clientFor(session, { currentUser: session.currentUser, permissions: employeePermissions, ...serverClock });
  const context = await reauthenticateContext(client, { username: " employee ", password: "secret" });
  assert.deepEqual(client.calls, ["session:0", "login:employee:secret", "session:1", "bootstrap"]);
  assert.equal(context.scopeKey, "employee-1:employee");
  assert.notEqual(scopedAppKey({ currentUser: { id: "admin-1", role: "admin" } }, 1), scopedAppKey(context.session, 2));
});

test("same user id role downgrade produces a different authenticated scope and remount key", async () => {
  const session = { authenticated: true, currentUser: { id: "admin-1", role: "employee" } };
  const client = clientFor(session, { currentUser: session.currentUser, permissions: employeePermissions, ...serverClock });
  const context = await reauthenticateContext(client, { username: "admin", password: "new-secret" });
  assert.equal(context.scopeKey, "admin-1:employee");
  assert.notEqual(scopedAppKey({ currentUser: { id: "admin-1", role: "admin" } }, 7), scopedAppKey(context.session, 8));
});

test("failed reauthentication never reads bootstrap or produces a new scope", async () => {
  const failure = Object.assign(new Error("bad credentials"), { code: "INVALID_CREDENTIALS", status: 401 });
  const client = clientFor(null, null, { failLogin: failure });
  await assert.rejects(
    reauthenticateContext(client, { username: "employee", password: "wrong" }),
    (error) => error === failure,
  );
  assert.deepEqual(client.calls, ["session:0", "login:employee:wrong"]);
});
