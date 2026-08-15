import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_REMINDER_TEMPLATE,
  insertReminderVariable,
  REMINDER_TEMPLATE_VARIABLES,
  renderReminderTemplate,
} from "../src/features/reminders/reminder-template.js";

const fields = {
  partyName: "李女士",
  date: "2026年8月20日",
  start: "09:30",
  end: "10:30",
  roomName: "笔录室 2",
};

test("default reminder template renders the approved five booking fields", () => {
  assert.equal(
    renderReminderTemplate(DEFAULT_REMINDER_TEMPLATE, fields),
    "【笔录提醒】李女士您好，您预约的笔录时间为2026年8月20日 09:30，地点：笔录室 2，请提前到达。如有变动我们会再联系您。",
  );
  assert.deepEqual(
    REMINDER_TEMPLATE_VARIABLES.map((item) => item.token),
    ["{当事人姓名}", "{日期}", "{开始时间}", "{结束时间}", "{笔录室}"],
  );
});

test("custom reminder templates replace repeats without adding sensitive fields", () => {
  assert.equal(
    renderReminderTemplate("{当事人姓名}：{开始时间}到{结束时间}，地点{笔录室}；日期{日期}。再次确认：{开始时间}。", fields),
    "李女士：09:30到10:30，地点笔录室 2；日期2026年8月20日。再次确认：09:30。",
  );
  const variableSource = JSON.stringify(REMINDER_TEMPLATE_VARIABLES);
  for (const forbidden of ["案号", "用途", "备注", "caseNumber", "purpose", "notes"]) {
    assert.equal(variableSource.includes(forbidden), false);
  }
});

test("variable chips insert at the current selection and return the next cursor", () => {
  assert.deepEqual(insertReminderVariable("您好，请到达。", "{笔录室}", 3, 5), {
    value: "您好，{笔录室}达。",
    cursor: 8,
  });
  assert.throws(
    () => insertReminderVariable("提醒", "{案号}", 2, 2),
    /不支持的提醒变量/,
  );
});
