const SECTION_SLOTS = {
  global: [1, 2],
  personal: [3, 4],
};

export function buildTagSectionPayload(tags, drafts, section) {
  const slots = SECTION_SLOTS[section];
  if (!slots) throw new TypeError("未知标签分区");
  const selected = tags.filter((tag) => slots.includes(Number(tag.slot)));
  if (selected.length !== 2) throw new TypeError(`${section} 标签槽位不完整`);
  return selected.map((tag) => {
    const label = String(drafts[tag.id] || "").trim();
    if (!label) throw new TypeError(`${section} 标签名称不能为空`);
    return {
      ...(section === "global" ? { id: tag.id } : {}),
      slot: Number(tag.slot),
      label,
    };
  });
}
