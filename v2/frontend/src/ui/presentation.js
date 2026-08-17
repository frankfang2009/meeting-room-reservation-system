const TAG_COLORS = [
  { color: "#D97757", surface: "#F7ECE7", line: "#E8C8BC" },
  { color: "#C29A4A", surface: "#F6F1E4", line: "#E5D4AD" },
  { color: "#7B9275", surface: "#EEF1EA", line: "#C9D3C4" },
  { color: "#71879A", surface: "#EBEFF1", line: "#C5D0D7" },
];

export function parseDate(value) {
  const [year, month, day] = String(value).split("-").map(Number);
  return new Date(year, month - 1, day);
}

export function dateLabel(value) {
  const date = value instanceof Date ? value : parseDate(value);
  const weekdays = ["星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"];
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日 · ${weekdays[date.getDay()]}`;
}

// 相对日期措辞以服务器业务日为基准，客户端时钟不参与判断。
export function relativeDayLabel(value, businessDateValue) {
  if (!value || !businessDateValue) return "";
  const date = value instanceof Date ? value : parseDate(value);
  const base = businessDateValue instanceof Date ? businessDateValue : parseDate(businessDateValue);
  const difference = Math.round(
    (Date.UTC(date.getFullYear(), date.getMonth(), date.getDate())
      - Date.UTC(base.getFullYear(), base.getMonth(), base.getDate())) / 86400000,
  );
  return { 0: "今天", 1: "明天", 2: "后天" }[difference] || "";
}

export function monthKey(value) {
  const date = value instanceof Date ? value : new Date(value);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

export function tagStyle(tag) {
  return {
    "--tag-color": tag?.color || TAG_COLORS[0].color,
    "--tag-surface": tag?.surface || TAG_COLORS[0].surface,
    "--tag-line": tag?.line || TAG_COLORS[0].line,
  };
}

export function normalizeTag(tag, index) {
  const slot = Number(tag?.slot || index + 1);
  const palette = TAG_COLORS[Math.max(0, Math.min(3, slot - 1))];
  return {
    id: tag?.id || `tag-${slot}`,
    slot,
    label: tag?.label || tag?.name || `标签 ${slot}`,
    ...palette,
  };
}

export function itemName(user) {
  return user?.name || user?.username || "当前用户";
}

export function reservationStatusLabel(status) {
  return { active: "已预约", cancelled: "已取消" }[status] || "状态未知";
}

export function formatLocalDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", { hour12: false });
}

export function reservationEventSummary(event) {
  if (event?.type === "created") return "预约已创建";
  if (event?.type === "cancelled") return "预约已取消";
  const labels = { roomId: "笔录室", date: "日期", start: "开始时间", end: "结束时间", partyName: "预约对象", caseNumber: "案号", purpose: "事项", notes: "备注", tagId: "标签" };
  const changed = Object.keys(labels).filter((key) => event?.before?.[key] !== event?.after?.[key]);
  return changed.length ? `${changed.map((key) => labels[key]).join("、")}已修改` : "预约内容已更新";
}
