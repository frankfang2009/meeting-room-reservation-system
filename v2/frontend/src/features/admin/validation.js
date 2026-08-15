export function validateRoomAdminForm(form = {}) {
  const errors = {};
  if (!String(form.name || "").trim()) errors.name = "请输入笔录室名称";
  if (!Number.isInteger(form.sortOrder) || form.sortOrder < 1 || form.sortOrder > 10000) {
    errors.sortOrder = "排序号必须是 1–10000 之间的整数";
  }
  return errors;
}

export function validateUserAdminForm(form = {}, { creating = false } = {}) {
  const errors = {};
  if (!String(form.name || "").trim()) errors.name = "请输入姓名";
  if (creating) {
    const username = String(form.username || "").trim();
    if (username.length < 3 || /\s/.test(username)) {
      errors.username = "用户名至少 3 个字符且不能包含空格";
    }
    Object.assign(errors, validatePasswordReset(form.password));
  }
  return errors;
}

export function validatePasswordReset(password) {
  const length = String(password || "").length;
  return length < 8 || length > 256
    ? { password: "密码长度必须为 8–256 个字符" }
    : {};
}

export function validateSystemSettingsForm(form = {}, slotMinutes = 30) {
  const errors = {};
  const parse = (field, label) => {
    const value = String(form[field] || "");
    if (!/^\d{2}:\d{2}$/.test(value)) {
      errors[field] = `${label}格式应为 HH:MM`;
      return null;
    }
    const [hours, minutes] = value.split(":").map(Number);
    if (hours > 23 || minutes > 59) {
      errors[field] = `${label}无效`;
      return null;
    }
    const total = hours * 60 + minutes;
    if (total % Number(slotMinutes || 30)) {
      errors[field] = `${label}必须按 ${Number(slotMinutes || 30)} 分钟对齐`;
    }
    return total;
  };
  const start = parse("workStart", "开始时间");
  const end = parse("workEnd", "结束时间");
  if (start !== null && end !== null && end <= start) {
    errors.workEnd = "结束时间必须晚于开始时间";
  }
  return errors;
}

export function adminApiFieldErrors(error) {
  if (error?.fields && typeof error.fields === "object") return error.fields;
  if (error?.code === "ROOM_NAME_EXISTS") return { name: "该名称已被其他笔录室使用" };
  if (error?.code === "USERNAME_EXISTS") return { username: "该用户名已存在" };
  return null;
}
