import { CalendarBlank, CaretRight, Key, PencilSimple, Trash, WarningCircle } from "@phosphor-icons/react";

export function RoomAdminForm({ drawer, deleteBusy, onSubmit, onFieldChange, onCancel, onDelete }) {
  const editing = drawer.type === "room-edit";
  return <form className="room-form" onSubmit={onSubmit} noValidate>
    <label className="field"><span>名称</span><input data-initial-focus value={drawer.form.name} aria-invalid={Boolean(drawer.errors?.name)} aria-describedby={drawer.errors?.name ? "room-name-error" : undefined} onChange={(event) => onFieldChange("name", event.target.value)} />{drawer.errors?.name && <small id="room-name-error" role="alert">{drawer.errors.name}</small>}</label>
    <label className="field"><span>排序号</span><input type="number" min="1" max="10000" value={drawer.form.sortOrder} aria-invalid={Boolean(drawer.errors?.sortOrder)} aria-describedby={drawer.errors?.sortOrder ? "room-sort-error" : undefined} onChange={(event) => onFieldChange("sortOrder", Number(event.target.value))} />{drawer.errors?.sortOrder ? <small id="room-sort-error" role="alert">{drawer.errors.sortOrder}</small> : <small className="field-hint">数字越小，在预约日历中越靠前</small>}</label>
    <label className="room-availability-toggle"><span><strong>在预约日历中启用</strong><small>停用后隐藏，已有预约不会自动取消。</small></span><input type="checkbox" checked={drawer.form.isActive} onChange={(event) => onFieldChange("isActive", event.target.checked)} /></label>
    <label className="room-availability-toggle room-display-toggle"><span><strong>在公开大屏显示</strong><small>关闭后，该笔录室及其预约不会出现在公开引导中。</small></span><input type="checkbox" checked={drawer.form.showOnDisplay} onChange={(event) => onFieldChange("showOnDisplay", event.target.checked)} /></label>
    <div className="room-form-actions"><button className="submit-button" type="submit">{editing ? "保存修改" : "创建笔录室"}</button><button className="secondary-button" type="button" onClick={onCancel}>取消</button></div>
    {editing && <button className="room-delete-button" type="button" disabled={deleteBusy} onClick={onDelete}><Trash size={17} />{deleteBusy ? "正在检查预约…" : "删除笔录室"}</button>}
  </form>;
}

export function RoomDeleteConfirmation({ room, busy, onConfirm, onBack }) {
  return <div className="room-delete-confirmation">
    <div className="room-delete-confirmation-copy"><span className="room-delete-kicker">可以删除</span><h2>删除“{room.name}”？</h2><p>当前没有未结束预约。删除后无法恢复，但历史预约仍会保留原笔录室名称。</p></div>
    <div className="room-delete-confirmation-note" role="note"><WarningCircle size={18} /><span>这项操作只删除笔录室，不会删除历史预约。</span></div>
    <div className="room-delete-confirmation-actions"><button className="confirm-cancel-button" type="button" data-initial-focus disabled={busy} onClick={onConfirm}>{busy ? "正在删除…" : "确认删除"}</button><button className="secondary-button" type="button" disabled={busy} onClick={onBack}>返回设置</button></div>
  </div>;
}

export function RoomDeleteBlocked({ room, bookings, total, onOpenBooking, onBack }) {
  const hiddenCount = Math.max(0, Number(total || 0) - bookings.length);
  return <div className="room-delete-blocked">
    <div className="room-delete-blocked-copy"><span className="room-delete-kicker">{total} 场待处理</span><h2>先调整预约，再删除“{room.name}”</h2><p>以下预约仍在这个笔录室。逐项调整到其他笔录室，或取消不再需要的预约。</p></div>
    <div className="room-delete-booking-list" aria-label="阻止删除的预约">{bookings.map((booking, index) => <button type="button" data-initial-focus={index === 0 || undefined} key={booking.id} onClick={() => onOpenBooking(booking)}><span className="room-delete-booking-date"><CalendarBlank size={17} /><strong>{booking.date}</strong><small>{booking.start}–{booking.end}</small></span><span className="room-delete-booking-summary"><strong>{booking.partyName}</strong><small>{booking.owner?.name || "未知预约者"} · 案号 {booking.caseNumber}</small></span><span className="room-delete-booking-action"><PencilSimple size={16} />{booking.canEdit ? "调整预约" : "查看预约"}</span></button>)}</div>
    {hiddenCount > 0 && <p className="room-delete-more">还有 {hiddenCount} 场预约未显示；处理完当前列表后再次删除即可继续查看。</p>}
    <button className="secondary-button room-delete-blocked-back" type="button" data-initial-focus={!bookings.length || undefined} onClick={onBack}>返回笔录室设置</button>
  </div>;
}

export function UserAdminForm({ drawer, lastAdminProtected, onSubmit, onFieldChange, onReset }) {
  const editing = drawer.type === "user-edit";
  return <form className="user-form" onSubmit={onSubmit} noValidate>
    <div className="user-form-scroll"><section className="user-form-section"><h2>账户信息</h2>
      <label className="field"><span>用户名</span><input data-initial-focus={!editing || undefined} readOnly={editing} className={editing ? "readonly" : ""} autoComplete="username" value={drawer.form.username} aria-invalid={Boolean(drawer.errors?.username)} aria-describedby={drawer.errors?.username ? "user-username-error" : undefined} onChange={(event) => onFieldChange("username", event.target.value)} />{drawer.errors?.username ? <small id="user-username-error" role="alert">{drawer.errors.username}</small> : editing && <small className="field-hint">用户名创建后不可修改</small>}</label>
      <label className="field"><span>姓名</span><input data-initial-focus={editing || undefined} value={drawer.form.name} aria-invalid={Boolean(drawer.errors?.name)} aria-describedby={drawer.errors?.name ? "user-name-error" : undefined} onChange={(event) => onFieldChange("name", event.target.value)} />{drawer.errors?.name && <small id="user-name-error" role="alert">{drawer.errors.name}</small>}</label>
      <label className="field"><span>所属部门</span><input value={drawer.form.department} aria-invalid={Boolean(drawer.errors?.department)} aria-describedby={drawer.errors?.department ? "user-department-error" : undefined} onChange={(event) => onFieldChange("department", event.target.value)} />{drawer.errors?.department && <small id="user-department-error" role="alert">{drawer.errors.department}</small>}</label>
      {!editing && <label className="field"><span>初始密码</span><input type="password" autoComplete="new-password" value={drawer.form.password} aria-invalid={Boolean(drawer.errors?.password)} aria-describedby={drawer.errors?.password ? "user-password-error" : undefined} onChange={(event) => onFieldChange("password", event.target.value)} />{drawer.errors?.password && <small id="user-password-error" role="alert">{drawer.errors.password}</small>}</label>}
    </section><section className="user-form-section user-permissions-section"><h2>权限与状态</h2><fieldset className="user-role-options" aria-invalid={Boolean(drawer.errors?.role)}><legend>角色</legend><label className={lastAdminProtected ? "blocked" : ""}><input type="radio" name="user-role" value="admin" checked={drawer.form.role === "admin"} disabled={lastAdminProtected} onChange={(event) => onFieldChange("role", event.target.value)} /><span><strong>管理员</strong><small>可管理用户、笔录室与全单位预约</small></span></label><label className={lastAdminProtected ? "blocked" : ""}><input type="radio" name="user-role" value="employee" checked={drawer.form.role === "employee"} disabled={lastAdminProtected} onChange={(event) => onFieldChange("role", event.target.value)} /><span><strong>普通员工</strong><small>仅管理本人的预约与偏好</small></span></label>{drawer.errors?.role && <small role="alert">{drawer.errors.role}</small>}</fieldset><label className={`user-enabled-toggle ${lastAdminProtected ? "blocked" : ""}`}><span><strong>启用账户</strong><small>停用后无法登录，历史记录仍保留。</small></span><input type="checkbox" disabled={lastAdminProtected} checked={drawer.form.enabled} onChange={(event) => onFieldChange("enabled", event.target.checked)} /></label>{editing && <button className="user-reset-link" type="button" onClick={onReset}><Key size={18} /><span>重置密码</span><CaretRight size={15} /></button>}</section></div>
    <div className="user-form-footer"><button className="submit-button" type="submit">{editing ? "保存修改" : "创建用户"}</button></div>
    {lastAdminProtected && <div className="user-protection-note blocking" role="note"><WarningCircle size={18} /><p><strong>当前账户是最后一名启用管理员</strong><span>请先启用或创建另一名管理员，再更改角色或停用。</span></p></div>}
  </form>;
}
