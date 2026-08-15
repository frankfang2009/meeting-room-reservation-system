import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { SessionIsolationBoundary } from "../src/session-isolation.js";

function render(blocked, message = "请重新登录") {
  return renderToStaticMarkup(createElement(
    SessionIsolationBoundary,
    {
      blocked,
      reauthentication: createElement("section", { role: "dialog" }, message),
    },
    createElement("div", null,
      createElement("code", null, "ADMIN_TOKEN_PLAINTEXT"),
      createElement("div", null, "管理员审计和用户列表"),
      createElement("textarea", { defaultValue: "未保存的管理员预约草稿" }),
    ),
  ));
}

test("expired-session render removes old administrator state from the document", () => {
  const markup = render(true);
  assert.match(markup, /data-session-blocked="true"/);
  assert.match(markup, /请重新登录/);
  assert.doesNotMatch(markup, /ADMIN_TOKEN_PLAINTEXT|管理员审计|管理员预约草稿/);
});

test("failed reauthentication remains isolated and successful scope remount renders only new state", () => {
  const failed = render(true, "重新登录失败，请核对账号后重试");
  assert.match(failed, /重新登录失败/);
  assert.doesNotMatch(failed, /ADMIN_TOKEN_PLAINTEXT|管理员审计|管理员预约草稿/);

  const verifiedNewScope = renderToStaticMarkup(createElement(
    SessionIsolationBoundary,
    { blocked: false, reauthentication: null },
    createElement("div", null, "普通员工工作台"),
  ));
  assert.match(verifiedNewScope, /普通员工工作台/);
  assert.doesNotMatch(verifiedNewScope, /ADMIN_TOKEN_PLAINTEXT|管理员审计/);
});
