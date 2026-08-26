import { useRef } from "react";
import { CaretRight, CircleNotch } from "@phosphor-icons/react";
import {
  DEFAULT_REMINDER_TEMPLATE,
  insertReminderVariable,
  REMINDER_TEMPLATE_PREVIEW_FIELDS,
  REMINDER_TEMPLATE_VARIABLES,
  renderReminderTemplate,
} from "../reminders/reminder-template.js";
import { normalizeTag, tagStyle } from "../../ui/presentation.js";

function userName(user) {
  return user?.name || user?.username || "当前用户";
}

function userInitial(user) {
  return Array.from(userName(user).trim())[0]?.toLocaleUpperCase() || "人";
}

function PreferencesView({ activeRooms, draft, durationSteps, errors = {}, onChange, onSave, onUiChange, tags, uiDraft }) {
  const reminderTemplateRef = useRef(null);
  const personalTags = [3, 4].map((slot) => normalizeTag((draft.personalTags || []).find((tag) => Number(tag.slot) === slot), slot - 1));
  const updatePersonalTag = (slot, label) => onChange("personalTags", personalTags.map((tag) => tag.slot === slot ? { id: tag.id, slot, label } : { id: tag.id, slot: tag.slot, label: tag.label }));
  const reminderTemplate = draft.reminderTemplate || DEFAULT_REMINDER_TEMPLATE;
  const insertTemplateVariable = (token) => {
    const textarea = reminderTemplateRef.current;
    const result = insertReminderVariable(
      reminderTemplate,
      token,
      textarea?.selectionStart,
      textarea?.selectionEnd,
    );
    if (result.value.length > 200) return;
    onChange("reminderTemplate", result.value);
    window.requestAnimationFrame(() => {
      textarea?.focus({ preventScroll: true });
      textarea?.setSelectionRange(result.cursor, result.cursor);
    });
  };
  return <form id="personal-settings-form" className="settings-layout" aria-label="个人设置" onSubmit={onSave}>
    <section className="settings-section settings-profile-section"><h2>个人资料</h2><div className="settings-field-grid"><label className="settings-field"><span>姓名</span><input name="profile-name" value={draft.name ?? ""} aria-invalid={Boolean(errors.name)} aria-describedby={errors.name ? "profile-name-error" : undefined} onChange={(event) => onChange("name", event.target.value)} />{errors.name && <small id="profile-name-error" className="settings-field-error" role="alert">{errors.name}</small>}</label><label className="settings-field"><span>所属部门</span><input value={draft.department ?? ""} onChange={(event) => onChange("department", event.target.value)} /></label></div></section>
    <section className="settings-section settings-preferences-section"><h2>预约偏好</h2><div className="settings-choice-list"><label className="settings-choice-row"><span>默认预约时长</span><span className="settings-select-wrap"><select value={draft.defaultDuration || 60} onChange={(event) => onChange("defaultDuration", Number(event.target.value))}>{durationSteps.map((option) => <option value={option} key={option}>{option}分钟</option>)}</select><CaretRight size={18} /></span></label><label className="settings-choice-row"><span>默认笔录室</span><span className="settings-select-wrap"><select value={draft.defaultRoomId || ""} onChange={(event) => onChange("defaultRoomId", event.target.value)}><option value="">不指定</option>{activeRooms.map((room) => <option value={room.id} key={room.id}>{room.name}</option>)}</select><CaretRight size={18} /></span></label><label className="settings-choice-row"><span>默认标签</span><span className="settings-select-wrap"><select value={draft.defaultTagSlot ?? ""} onChange={(event) => onChange("defaultTagSlot", event.target.value ? Number(event.target.value) : null)}><option value="">不指定</option>{tags.map((tag) => <option value={tag.slot} key={tag.id}>{tag.label}</option>)}</select><CaretRight size={18} /></span></label></div></section>
    <section className="settings-section settings-personalization-section"><div className="settings-section-copy"><h2>使用习惯</h2><p>以下选项只保存在当前浏览器，并按登录账号隔离。</p></div><div className="settings-choice-list"><label className="settings-choice-row"><span>登录后默认打开</span><span className="settings-select-wrap"><select value={uiDraft.defaultView} onChange={(event) => onUiChange("defaultView", event.target.value)}><option value="mine">我的预约</option><option value="calendar">预约日历</option></select><CaretRight size={18} /></span></label></div></section>
    <section className="settings-section settings-personal-tags-section"><div className="settings-section-copy"><h2>个人标签</h2><p>标签仅用于解释和显示你自己的预约。</p></div><div className="settings-tag-grid">{personalTags.map((tag) => <label className="settings-field settings-tag-field" style={tagStyle(tag)} key={tag.slot}><span><i />标签 {tag.slot}</span><input maxLength={40} value={tag.label} onChange={(event) => updatePersonalTag(tag.slot, event.target.value)} /></label>)}</div></section>
    <section className="settings-section settings-notifications-section"><h2>通知</h2><div className="settings-notification-list"><label className="settings-notification-row"><span><strong>预约变更</strong><small>页面打开时，修改或取消预约会通知我</small></span><input className="settings-switch" type="checkbox" checked={Boolean(draft.bookingChangeNotifications)} onChange={(event) => onChange("bookingChangeNotifications", event.target.checked)} /></label><div className="settings-notification-row"><label htmlFor="booking-reminder-switch"><strong>预约提醒</strong><small>页面打开时，开始前 {draft.reminderLeadMinutes || 30} 分钟提醒我</small></label><span className="settings-notification-controls"><span className="settings-reminder-lead"><select aria-label="提醒提前量" value={draft.reminderLeadMinutes || 30} disabled={!draft.bookingReminder} onChange={(event) => onChange("reminderLeadMinutes", Number(event.target.value))}>{[15, 30, 60].map((minutes) => <option value={minutes} key={minutes}>提前 {minutes} 分钟</option>)}</select><CaretRight size={16} /></span><input id="booking-reminder-switch" className="settings-switch" type="checkbox" checked={Boolean(draft.bookingReminder)} onChange={(event) => onChange("bookingReminder", event.target.checked)} /></span></div><label className="settings-notification-row"><span><strong>提醒提示音</strong><small>临近提醒与到达提醒到达时播放一声温和的轻提示</small></span><input id="reminder-sound-switch" className="settings-switch" type="checkbox" checked={draft.reminderSound !== false} onChange={(event) => onChange("reminderSound", event.target.checked)} /></label></div></section>
    <section className="settings-section settings-reminder-template-section"><div className="settings-section-copy"><h2>对外提醒模板</h2><p>仅复制到剪贴板，由您自行发送。</p></div><label className="settings-template-field"><span>模板内容 <small>{reminderTemplate.length}/200</small></span><textarea ref={reminderTemplateRef} maxLength={200} rows={4} value={reminderTemplate} aria-invalid={Boolean(errors.reminderTemplate)} aria-describedby={errors.reminderTemplate ? "reminder-template-error" : "reminder-template-help"} onChange={(event) => onChange("reminderTemplate", event.target.value)} />{errors.reminderTemplate && <small id="reminder-template-error" className="settings-field-error" role="alert">{errors.reminderTemplate}</small>}</label><div id="reminder-template-help" className="settings-template-variables" aria-label="可插入变量">{REMINDER_TEMPLATE_VARIABLES.map((variable) => <button type="button" onClick={() => insertTemplateVariable(variable.token)} key={variable.key}>{variable.token}</button>)}</div><div className="settings-template-preview"><span>示例预览</span><p>{renderReminderTemplate(reminderTemplate, REMINDER_TEMPLATE_PREVIEW_FIELDS)}</p></div><button className="settings-template-restore" type="button" onClick={() => onChange("reminderTemplate", DEFAULT_REMINDER_TEMPLATE)}>恢复默认</button>
    </section>
  </form>;
}

export function PersonalCenter({
  activeRooms,
  currentUser,
  draft,
  durationSteps,
  errors = {},
  onChange,
  onLogout,
  onSave,
  onUiChange,
  saving = false,
  tags,
  uiDraft,
}) {
  return <main className="main-canvas settings-canvas personal-center-canvas" tabIndex={0}>
    <header className="page-header settings-header personal-center-header"><div><h1>个人中心</h1><p>管理个人资料与工作偏好</p></div><div className="personal-center-header-side"><section className="personal-center-identity" aria-label="当前用户"><span className="personal-center-avatar" aria-hidden="true">{userInitial(currentUser)}</span><span><strong>{userName(currentUser)}</strong><small>{currentUser?.department || "未设置部门"}</small></span></section><div className="settings-header-actions"><button className="settings-logout-button" type="button" disabled={saving} onClick={onLogout}>退出登录</button><button className="settings-save-button" type="submit" form="personal-settings-form" disabled={saving}>{saving ? <><CircleNotch className="spin" size={17} />正在保存</> : "保存更改"}</button></div></div></header>
    <PreferencesView activeRooms={activeRooms} draft={draft} durationSteps={durationSteps} errors={errors} onChange={onChange} onSave={onSave} onUiChange={onUiChange} tags={tags} uiDraft={uiDraft} />
  </main>;
}
