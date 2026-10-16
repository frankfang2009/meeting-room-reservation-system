const TAG_COLORS = [
  { color: "#D97757", surface: "#F7ECE7", line: "#E8C8BC", fold: "#D7B3A4" },
  { color: "#C29A4A", surface: "#F6F1E4", line: "#E5D4AD", fold: "#DCC79A" },
  { color: "#7B9275", surface: "#EEF1EA", line: "#C9D3C4", fold: "#C5D2C1" },
  { color: "#71879A", surface: "#EBEFF1", line: "#C5D0D7", fold: "#B9C8D2" },
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
  const palette = TAG_COLORS[Math.max(0, Math.min(3, Number(tag?.slot || 1) - 1))] || TAG_COLORS[0];
  return {
    "--tag-color": tag?.color || palette.color,
    "--tag-surface": tag?.surface || palette.surface,
    "--tag-line": tag?.line || palette.line,
    "--tag-fold": tag?.fold || palette.fold,
  };
}

// 日历上“我的 / 他人”只认预约对象里的 ownerId，不靠角色推断。
export function calendarBookingOwnership(booking, currentUserId) {
  if (!currentUserId || !booking?.ownerId) return "unknown";
  return booking.ownerId === currentUserId ? "mine" : "other";
}

// 短卡（约 30 分钟，或只占一行）用更小的折角，避免压住圆点和标题。
export function calendarFoldSize({ durationMinutes, slotMinutes = 30 } = {}) {
  const duration = Number(durationMinutes);
  const slot = Number(slotMinutes);
  if (!Number.isFinite(duration) || duration <= 0) return 12;
  const step = Number.isFinite(slot) && slot > 0 ? slot : 30;
  const span = Math.max(1, Math.round(duration / step));
  return span <= 1 || duration <= 30 ? 8 : 12;
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

export function handoverLedgerSections({ incoming = [], outgoing = [] } = {}) {
  const incomingItems = Array.isArray(incoming) ? incoming : [];
  const outgoingItems = Array.isArray(outgoing) ? outgoing : [];
  const sections = [];

  if (incomingItems.length) {
    const fromName = incomingItems[0]?.fromUser?.name || "同事";
    sections.push({
      id: "incoming",
      eyebrow: incomingItems.length === 1 ? `待我确认 · 来自 ${fromName}` : `待我确认 · ${incomingItems.length} 条`,
      consequence: "接受后，你将成为新的预约者。",
      items: incomingItems,
    });
  }

  if (outgoingItems.length) {
    const toName = outgoingItems[0]?.toUser?.name || "对方";
    sections.push({
      id: "outgoing",
      eyebrow: outgoingItems.length === 1 ? `我发起的 · 等待 ${toName}确认` : `我发起的 · ${outgoingItems.length} 条处理中`,
      consequence: "对方确认前，预约仍归你。",
      items: outgoingItems,
    });
  }

  return sections;
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
  if (event?.type === "handover") {
    const from = event?.before?.ownerName || "原预约者";
    const to = event?.after?.ownerName || "新预约者";
    return `预约者由 ${from} 交接给 ${to}`;
  }
  const labels = { roomId: "笔录室", date: "日期", start: "开始时间", end: "结束时间", partyName: "预约对象", caseNumber: "案号", purpose: "事项", notes: "备注", tagId: "标签" };
  const changed = Object.keys(labels).filter((key) => event?.before?.[key] !== event?.after?.[key]);
  return changed.length ? `${changed.map((key) => labels[key]).join("、")}已修改` : "预约内容已更新";
}
