export function bookingDefaultsSummary(defaults = {}, rooms = [], tags = []) {
  const duration = Number(defaults.defaultDuration) || 60;
  const room = rooms.find((item) => item.id === defaults.defaultRoomId);
  const tag = tags.find((item) => Number(item.slot) === Number(defaults.defaultTagSlot));
  return `${duration}分钟 · ${room?.name || "不指定笔录室"} · ${tag?.label || "不指定标签"}`;
}
