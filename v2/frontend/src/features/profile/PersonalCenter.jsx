import { CaretRight, CircleNotch } from "@phosphor-icons/react";
import { activityDuration, emptyActivity } from "./activity.js";
import { normalizeTag, tagStyle } from "../../ui/presentation.js";

function userName(user) {
  return user?.name || user?.username || "当前用户";
}

function userInitial(user) {
  return Array.from(userName(user).trim())[0]?.toLocaleUpperCase() || "人";
}

function ActivityView({ activity, onReload, state }) {
  const value = activity || emptyActivity();
  const duration = activityDuration(value.summary.totalDurationMinutes);
  const summaryItems = [
    ["本月完成", value.summary.currentMonthCompleted, "场"],
    ["累计完成", value.summary.totalCompleted, "场"],
    ["累计时长", duration.value, duration.unit],
    ["活跃天数", value.summary.activeDays, "天"],
  ];
  return <div id="personal-center-activity-panel" className="profile-activity-content" role="tabpanel" aria-labelledby="personal-center-activity-tab">
    {state === "loading" && !activity ? <div className="profile-activity-loading" role="status"><CircleNotch className="spin" size={20} />正在读取活动概览</div> :
      state === "failed" && !activity ? <div className="profile-activity-loading profile-activity-error" role="alert"><span>活动概览暂时无法读取</span><button type="button" onClick={onReload}>重新加载</button></div> : <div className="profile-activity-lower">
      <section className="profile-activity-overview" aria-labelledby="profile-overview-heading"><h2 id="profile-overview-heading">活动概览</h2><dl>
        <div><dt>平均时长</dt><dd>{value.overview.averageDurationMinutes ? `${value.overview.averageDurationMinutes}分钟` : "—"}</dd></div>
        <div><dt>最常用笔录室</dt><dd>{value.overview.favoriteRoom || "—"}</dd></div>
        <div><dt>常用标签</dt><dd>{value.overview.favoriteTag || "—"}</dd></div>
      </dl></section>
      <section className="profile-activity-data" aria-labelledby="profile-data-heading"><h2 id="profile-data-heading">活动数据</h2><dl>{summaryItems.map(([label, number, unit]) => <div key={label}><dt>{label}</dt><dd><strong>{number}</strong><span>{unit}</span></dd></div>)}</dl></section>
    </div>}
  </div>;
}

function PreferencesView({ activeRooms, draft, durationSteps, onChange, onSave, onUiChange, uiDraft }) {
  const personalTags = [3, 4].map((slot) => normalizeTag((draft.personalTags || []).find((tag) => Number(tag.slot) === slot), slot - 1));
  const updatePersonalTag = (slot, label) => onChange("personalTags", personalTags.map((tag) => tag.slot === slot ? { id: tag.id, slot, label } : { id: tag.id, slot: tag.slot, label: tag.label }));
  return <form id="personal-settings-form" className="settings-layout" role="tabpanel" aria-labelledby="personal-center-preferences-tab" onSubmit={onSave}>
    <section className="settings-section settings-profile-section"><h2>个人资料</h2><div className="settings-field-grid"><label className="settings-field"><span>姓名</span><input value={draft.name ?? ""} onChange={(event) => onChange("name", event.target.value)} /></label><label className="settings-field"><span>所属部门</span><input value={draft.department ?? ""} onChange={(event) => onChange("department", event.target.value)} /></label></div></section>
    <section className="settings-section settings-preferences-section"><h2>预约偏好</h2><div className="settings-choice-list"><label className="settings-choice-row"><span>默认预约时长</span><span className="settings-select-wrap"><select value={draft.defaultDuration || 60} onChange={(event) => onChange("defaultDuration", Number(event.target.value))}>{durationSteps.map((option) => <option value={option} key={option}>{option}分钟</option>)}</select><CaretRight size={18} /></span></label><label className="settings-choice-row"><span>默认笔录室</span><span className="settings-select-wrap"><select value={draft.defaultRoomId || ""} onChange={(event) => onChange("defaultRoomId", event.target.value)}><option value="">不指定</option>{activeRooms.map((room) => <option value={room.id} key={room.id}>{room.name}</option>)}</select><CaretRight size={18} /></span></label></div></section>
    <section className="settings-section settings-personalization-section"><div className="settings-section-copy"><h2>使用习惯</h2><p>以下选项只保存在当前浏览器，并按登录账号隔离。</p></div><div className="settings-choice-list"><label className="settings-choice-row"><span>登录后默认打开</span><span className="settings-select-wrap"><select value={uiDraft.defaultView} onChange={(event) => onUiChange("defaultView", event.target.value)}><option value="mine">我的预约</option><option value="calendar">预约日历</option></select><CaretRight size={18} /></span></label></div></section>
    <section className="settings-section settings-personal-tags-section"><div className="settings-section-copy"><h2>个人标签</h2><p>标签仅用于解释和显示你自己的预约。</p></div><div className="settings-tag-grid">{personalTags.map((tag) => <label className="settings-field settings-tag-field" style={tagStyle(tag)} key={tag.slot}><span><i />标签 {tag.slot}</span><input maxLength={40} value={tag.label} onChange={(event) => updatePersonalTag(tag.slot, event.target.value)} /></label>)}</div></section>
    <section className="settings-section settings-notifications-section"><h2>通知</h2><div className="settings-notification-list"><label className="settings-notification-row"><span><strong>预约变更</strong><small>页面打开时，修改或取消预约会通知我</small></span><input className="settings-switch" type="checkbox" checked={Boolean(draft.bookingChangeNotifications)} onChange={(event) => onChange("bookingChangeNotifications", event.target.checked)} /></label><label className="settings-notification-row"><span><strong>预约提醒</strong><small>页面打开时，开始前30分钟提醒我</small></span><input className="settings-switch" type="checkbox" checked={Boolean(draft.bookingReminder)} onChange={(event) => onChange("bookingReminder", event.target.checked)} /></label></div></section>
  </form>;
}

export function PersonalCenter({
  activeRooms,
  activity,
  activityState,
  currentUser,
  draft,
  durationSteps,
  onActivityReload,
  onChange,
  onLogout,
  onSave,
  onTabChange,
  onUiChange,
  tab,
  uiDraft,
}) {
  return <main className="main-canvas settings-canvas personal-center-canvas">
    <header className="page-header settings-header personal-center-header"><div><h1>个人中心</h1><p>查看工作概览与管理工作偏好</p></div>{tab === "preferences" && <div className="settings-header-actions"><button className="settings-logout-button" type="button" onClick={onLogout}>退出登录</button><button className="settings-save-button" type="submit" form="personal-settings-form">保存更改</button></div>}</header>
    <section className="personal-center-identity" aria-label="当前用户"><span className="personal-center-avatar" aria-hidden="true">{userInitial(currentUser)}</span><span><strong>{userName(currentUser)}</strong><small>{currentUser?.department || "未设置部门"}</small></span></section>
    <div className="personal-center-tab-row"><div className="personal-center-tabs" role="tablist" aria-label="个人中心页面"><button id="personal-center-activity-tab" type="button" role="tab" aria-controls="personal-center-activity-panel" aria-selected={tab === "activity"} className={tab === "activity" ? "active" : ""} onClick={() => onTabChange("activity")}>我的活动</button><button id="personal-center-preferences-tab" type="button" role="tab" aria-controls="personal-settings-form" aria-selected={tab === "preferences"} className={tab === "preferences" ? "active" : ""} onClick={() => onTabChange("preferences")}>偏好设置</button></div>{tab === "activity" && <button className="personal-center-preference-link" type="button" onClick={() => onTabChange("preferences")}>偏好设置<CaretRight size={18} /></button>}</div>
    {tab === "activity" ? <ActivityView activity={activity} onReload={onActivityReload} state={activityState} /> : <PreferencesView activeRooms={activeRooms} draft={draft} durationSteps={durationSteps} onChange={onChange} onSave={onSave} onUiChange={onUiChange} uiDraft={uiDraft} />}
  </main>;
}
