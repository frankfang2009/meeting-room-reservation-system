export const DEFAULT_REMINDER_TEMPLATE = "【笔录提醒】{当事人姓名}您好，您预约的笔录时间为{日期} {开始时间}，地点：{笔录室}，请提前到达。如有变动我们会再联系您。";

export const REMINDER_TEMPLATE_VARIABLES = [
  { key: "partyName", token: "{当事人姓名}", label: "当事人姓名" },
  { key: "date", token: "{日期}", label: "日期" },
  { key: "start", token: "{开始时间}", label: "开始时间" },
  { key: "end", token: "{结束时间}", label: "结束时间" },
  { key: "roomName", token: "{笔录室}", label: "笔录室" },
];

export const REMINDER_TEMPLATE_PREVIEW_FIELDS = {
  partyName: "张女士",
  date: "2026年8月18日",
  start: "09:00",
  end: "10:00",
  roomName: "笔录室 1",
};

export function renderReminderTemplate(template, fields = {}) {
  let result = String(template || DEFAULT_REMINDER_TEMPLATE);
  for (const variable of REMINDER_TEMPLATE_VARIABLES) {
    result = result.replaceAll(variable.token, String(fields[variable.key] ?? ""));
  }
  return result;
}

export function insertReminderVariable(template, token, selectionStart, selectionEnd) {
  if (!REMINDER_TEMPLATE_VARIABLES.some((variable) => variable.token === token)) {
    throw new TypeError("不支持的提醒变量");
  }
  const value = String(template || "");
  const start = Number.isInteger(selectionStart) ? selectionStart : value.length;
  const end = Number.isInteger(selectionEnd) ? selectionEnd : start;
  const normalizedStart = Math.max(0, Math.min(start, value.length));
  const normalizedEnd = Math.max(normalizedStart, Math.min(end, value.length));
  const nextValue = value.slice(0, normalizedStart) + token + value.slice(normalizedEnd);
  return { value: nextValue, cursor: normalizedStart + token.length };
}
