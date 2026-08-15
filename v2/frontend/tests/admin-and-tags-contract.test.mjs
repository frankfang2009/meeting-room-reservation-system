import assert from "node:assert/strict";
import test from "node:test";

import {
  adminApiFieldErrors,
  validatePasswordReset,
  validateRoomAdminForm,
  validateSystemSettingsForm,
  validateUserAdminForm,
} from "../src/features/admin/validation.js";
import { buildTagSectionPayload } from "../src/features/tags/tag-drafts.js";

test("administrator forms expose stable field-level validation", () => {
  assert.deepEqual(validateRoomAdminForm({ name: "", sortOrder: 0 }), {
    name: "请输入笔录室名称",
    sortOrder: "排序号必须是 1–10000 之间的整数",
  });
  assert.deepEqual(validateUserAdminForm({ name: "", username: "a b", password: "short" }, { creating: true }), {
    name: "请输入姓名",
    username: "用户名至少 3 个字符且不能包含空格",
    password: "密码长度必须为 8–256 个字符",
  });
  assert.deepEqual(validatePasswordReset("long-enough"), {});
  assert.deepEqual(validateSystemSettingsForm({ workStart: "08:30", workEnd: "17:30" }, 30), {});
  assert.deepEqual(validateSystemSettingsForm({ workStart: "08:15", workEnd: "08:00" }, 30), {
    workStart: "开始时间必须按 30 分钟对齐",
    workEnd: "结束时间必须晚于开始时间",
  });
  assert.deepEqual(adminApiFieldErrors({ code: "USERNAME_EXISTS" }), { username: "该用户名已存在" });
});

test("unit and personal tag sections build independent payloads", () => {
  const tags = [1, 2, 3, 4].map((slot) => ({ id: `tag-${slot}`, slot }));
  const drafts = { "tag-1": "单位一", "tag-2": "单位二", "tag-3": "个人三", "tag-4": "个人四" };
  assert.deepEqual(buildTagSectionPayload(tags, drafts, "global"), [
    { id: "tag-1", slot: 1, label: "单位一" },
    { id: "tag-2", slot: 2, label: "单位二" },
  ]);
  assert.deepEqual(buildTagSectionPayload(tags, drafts, "personal"), [
    { slot: 3, label: "个人三" },
    { slot: 4, label: "个人四" },
  ]);
  assert.throws(
    () => buildTagSectionPayload(tags, { ...drafts, "tag-3": " " }, "personal"),
    /名称不能为空/,
  );
});
