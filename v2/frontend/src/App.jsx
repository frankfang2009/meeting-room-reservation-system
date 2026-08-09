import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowClockwise,
  Asterisk,
  CalendarBlank,
  CaretLeft,
  CaretRight,
  CheckCircle,
  CircleNotch,
  ClockCounterClockwise,
  Database,
  DoorOpen,
  DownloadSimple,
  Eye,
  EyeSlash,
  FunnelSimple,
  Key,
  LockSimple,
  MagnifyingGlass,
  PencilSimple,
  Plus,
  Pulse,
  SlidersHorizontal,
  SpeakerHigh,
  User,
  UserCircle,
  UsersThree,
  WarningCircle,
  WifiSlash,
  X,
} from "@phosphor-icons/react";
import { api, unwrapItems } from "./api.js";
import {
  bookingPayload,
  canManageBooking,
  dateKey,
  durationFromRange,
  endFromDuration,
  findFirstAvailableStart,
  generateTimeSlots,
  overlaps,
  rebaseBookingEdit,
  reminderDisplayMessage,
  shiftDate,
  validateBookingForm,
} from "./domain.js";

const DURATION_STEPS = [30, 60, 90, 120, 150, 180];
const TAG_COLORS = [
  { color: "#D97757", surface: "#F7ECE7", line: "#E8C8BC" },
  { color: "#C29A4A", surface: "#F6F1E4", line: "#E5D4AD" },
  { color: "#7B9275", surface: "#EEF1EA", line: "#C9D3C4" },
  { color: "#71879A", surface: "#EBEFF1", line: "#C5D0D7" },
];
const NAV_ITEMS = [
  { id: "calendar", label: "预约日历", Icon: CalendarBlank },
  { id: "mine", label: "我的预约", Icon: User },
  { id: "history", label: "预约记录", Icon: ClockCounterClockwise },
  { id: "rooms", label: "笔录室", Icon: DoorOpen, permission: "manageRooms" },
  { id: "users", label: "用户管理", Icon: UsersThree, permission: "manageUsers" },
  { id: "system", label: "系统状态", Icon: Pulse, permission: "manageSystem" },
];
const EMPTY_BOOKING = {
  roomId: "",
  date: "",
  start: "",
  duration: 60,
  partyName: "",
  caseNumber: "",
  purpose: "",
  notes: "",
  tagId: "",
};

function parseDate(value) {
  const [year, month, day] = String(value).split("-").map(Number);
  return new Date(year, month - 1, day);
}

function dateLabel(value) {
  const date = value instanceof Date ? value : parseDate(value);
  const weekdays = ["星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"];
  return date.getFullYear() + "年" + (date.getMonth() + 1) + "月" + date.getDate() + "日 · " + weekdays[date.getDay()];
}

function monthKey(value) {
  const date = value instanceof Date ? value : new Date(value);
  return date.getFullYear() + "-" + String(date.getMonth() + 1).padStart(2, "0");
}

function tagStyle(tag) {
  return {
    "--tag-color": tag?.color || TAG_COLORS[0].color,
    "--tag-surface": tag?.surface || TAG_COLORS[0].surface,
    "--tag-line": tag?.line || TAG_COLORS[0].line,
  };
}

function normalizeTag(tag, index) {
  const slot = Number(tag?.slot || index + 1);
  const palette = TAG_COLORS[Math.max(0, Math.min(3, slot - 1))];
  return {
    id: tag?.id || "tag-" + slot,
    slot,
    label: tag?.label || tag?.name || "标签 " + slot,
    ...palette,
  };
}

function itemName(user) {
  return user?.name || user?.username || "当前用户";
}

function useDocumentTitle(title) {
  useEffect(() => {
    document.title = title + " · 会议室预约系统";
  }, [title]);
}

function useFocusTrap(ref, active, onClose, dismissable = true) {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    if (!active || !ref.current) return undefined;
    const node = ref.current;
    const previous = document.activeElement;
    const selector = "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])";
    const first = node.querySelector("[data-initial-focus]") || node.querySelector(selector);
    window.requestAnimationFrame(() => first?.focus());
    const handleKey = (event) => {
      if (event.key === "Escape" && dismissable) {
        event.preventDefault();
        onCloseRef.current?.();
        return;
      }
      if (event.key !== "Tab") return;
      const controls = [...node.querySelectorAll(selector)].filter((element) => !element.hidden);
      if (!controls.length) return;
      const firstControl = controls[0];
      const lastControl = controls.at(-1);
      if (event.shiftKey && document.activeElement === firstControl) {
        event.preventDefault();
        lastControl.focus();
      } else if (!event.shiftKey && document.activeElement === lastControl) {
        event.preventDefault();
        firstControl.focus();
      }
    };
    node.addEventListener("keydown", handleKey);
    return () => {
      node.removeEventListener("keydown", handleKey);
      previous?.focus?.();
    };
  }, [active, dismissable, ref]);
}

function LoadingScreen({ label = "正在连接系统" }) {
  return (
    <main className="login-page">
      <section className="login-panel" role="status">
        <div className="login-title-row">
          <span className="login-title-dot" aria-hidden="true" />
          <h1>{label}</h1>
        </div>
        <p className="login-account-feedback"><CircleNotch className="spin" size={20} />请稍候</p>
      </section>
      <figure className="login-illustration" aria-hidden="true">
        <img src="/assets/login/claude-doorway-time.png" alt="" />
      </figure>
    </main>
  );
}

function FatalScreen({ error, onRetry }) {
  useDocumentTitle("连接失败");
  return (
    <main className="login-page">
      <section className="login-panel" role="alert">
        <div className="login-title-row">
          <span className="login-title-dot" aria-hidden="true" />
          <h1>暂时无法使用</h1>
        </div>
        <p className="login-account-feedback"><WarningCircle size={20} />{error || "无法连接系统服务"}</p>
        <button className="login-submit" type="button" onClick={onRetry}>重新连接</button>
      </section>
      <figure className="login-illustration" aria-hidden="true">
        <img src="/assets/login/claude-doorway-time.png" alt="" />
      </figure>
    </main>
  );
}

function Login({ onAuthenticated }) {
  const [credentials, setCredentials] = useState({ username: "", password: "" });
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [visible, setVisible] = useState(false);
  useDocumentTitle("登录");

  async function submit(event) {
    event.preventDefault();
    const nextErrors = {};
    if (!credentials.username.trim()) nextErrors.username = "请输入用户名";
    if (!credentials.password) nextErrors.password = "请输入密码";
    setErrors(nextErrors);
    setMessage("");
    if (Object.keys(nextErrors).length) return;
    setBusy(true);
    try {
      const result = await api.login(credentials.username.trim(), credentials.password);
      onAuthenticated(result);
    } catch (error) {
      if (error.code === "ACCOUNT_DISABLED") setMessage("该账号已停用，请联系管理员。");
      else if (error.code === "INVALID_CREDENTIALS" || error.status === 401) setErrors({ password: "用户名或密码不正确，请重新输入。" });
      else setMessage(error.message || "登录失败，服务暂时不可用，请稍后重试。");
    } finally {
      setBusy(false);
    }
  }

  function update(field, value) {
    setCredentials((current) => ({ ...current, [field]: value }));
    setErrors((current) => ({ ...current, [field]: "" }));
    setMessage("");
  }

  return (
    <main className="login-page">
      <section className="login-panel" aria-labelledby="login-heading">
        <div className="login-title-row">
          <span className="login-title-dot" aria-hidden="true" />
          <h1 id="login-heading">登录</h1>
        </div>
        <form className="login-form" onSubmit={submit} noValidate aria-busy={busy}>
          <label className="login-field" htmlFor="login-username">
            <span>用户名</span>
            <input id="login-username" autoComplete="username" value={credentials.username} disabled={busy}
              aria-invalid={Boolean(errors.username)} onChange={(event) => update("username", event.target.value)} />
          </label>
          {errors.username && <p className="login-field-error" role="alert"><WarningCircle size={18} />{errors.username}</p>}
          <label className="login-field login-password-field" htmlFor="login-password">
            <span>密码</span>
            <span className="login-password-control">
              <input id="login-password" type={visible ? "text" : "password"} autoComplete="current-password"
                value={credentials.password} disabled={busy} className={errors.password ? "invalid" : ""}
                aria-invalid={Boolean(errors.password)} onChange={(event) => update("password", event.target.value)} />
              <button className="login-password-toggle" type="button" aria-label={visible ? "隐藏密码" : "显示密码"}
                onClick={() => setVisible((current) => !current)} disabled={busy}>
                {visible ? <EyeSlash size={21} /> : <Eye size={21} />}
              </button>
            </span>
          </label>
          <div className="login-feedback-slot" aria-live="polite">
            {errors.password && <p className="login-field-error" role="alert"><WarningCircle size={18} />{errors.password}</p>}
            {message && <p className="login-account-feedback" role="alert"><WarningCircle size={18} />{message}</p>}
          </div>
          <button className="login-submit" type="submit" disabled={busy}>
            {busy ? <><CircleNotch className="login-spinner spin" size={20} />正在登录…</> : "登录"}
          </button>
        </form>
      </section>
      <figure className="login-illustration" aria-hidden="true">
        <img src="/assets/login/claude-doorway-time.png" alt="" />
      </figure>
    </main>
  );
}

function Setup({ onComplete }) {
  const [step, setStep] = useState(0);
  const [admin, setAdmin] = useState({ username: "", name: "", department: "", password: "", confirmPassword: "" });
  const [rooms, setRooms] = useState([{ name: "笔录室 1" }]);
  const [hours, setHours] = useState({ start: "08:30", end: "17:30" });
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const [complete, setComplete] = useState(false);
  const steps = ["安全说明", "管理员", "笔录室", "工作时间", "完成"];
  useDocumentTitle("首次配置");

  function validate() {
    const next = {};
    if (step === 1) {
      if (!admin.username.trim()) next.username = "请输入用户名";
      if (!admin.name.trim()) next.name = "请输入姓名";
      if (!admin.department.trim()) next.department = "请输入部门";
      if (admin.password.length < 8) next.password = "密码至少需要 8 个字符";
      if (admin.password !== admin.confirmPassword) next.confirmPassword = "两次输入的密码不一致";
    }
    if (step === 2 && !rooms.some((room) => room.name.trim())) next.rooms = "请至少填写一个笔录室";
    if (step === 3 && hours.end <= hours.start) next.hours = "结束时间必须晚于开始时间";
    setErrors(next);
    return !Object.keys(next).length;
  }

  async function advance() {
    if (!validate()) return;
    if (step < 4) {
      setStep((current) => current + 1);
      return;
    }
    setBusy(true);
    try {
      await api.completeSetup({
        admin: {
          username: admin.username.trim(),
          password: admin.password,
          name: admin.name.trim(),
          department: admin.department.trim(),
        },
        rooms: rooms.filter((room) => room.name.trim()).map((room) => ({ name: room.name.trim() })),
        workStart: hours.start,
        workEnd: hours.end,
      });
      setComplete(true);
    } catch (error) {
      setErrors(error.fields && Object.keys(error.fields).length ? error.fields : { submit: error.message });
    } finally {
      setBusy(false);
    }
  }

  function stage() {
    if (complete) return (
      <div className="setup-copy setup-complete-copy" role="status">
        <CheckCircle size={42} weight="thin" />
        <h1>首次配置已完成</h1>
        <p>系统会以局域网模式重新启动。服务恢复后即可使用刚创建的管理员账号登录。</p>
        <button className="setup-primary-button" type="button" onClick={onComplete}>前往登录</button>
      </div>
    );
    if (step === 0) return (
      <div className="setup-copy">
        <LockSimple size={38} weight="thin" />
        <h1>在这台电脑上建立全新系统</h1>
        <p>首次配置只允许从本机完成。系统不会读取或迁移任何旧版本账号、预约或数据库。</p>
      </div>
    );
    if (step === 1) return (
      <div className="setup-copy">
        <h1>创建第一位管理员</h1>
        <p>系统不会提供默认账号或密码文件。</p>
        <div className="setup-admin-form">
          {[
            ["username", "用户名", "username"],
            ["name", "姓名", "name"],
            ["department", "所属部门", "organization"],
            ["password", "密码", "new-password"],
            ["confirmPassword", "确认密码", "new-password"],
          ].map(([field, label, autoComplete]) => (
            <label className="setup-field" key={field}>
              <span>{label}</span>
              <input type={field.includes("Password") || field === "password" ? "password" : "text"}
                autoComplete={autoComplete} value={admin[field]} aria-invalid={Boolean(errors[field])}
                onChange={(event) => { setAdmin((current) => ({ ...current, [field]: event.target.value })); setErrors((current) => ({ ...current, [field]: "" })); }} />
              {errors[field] && <small role="alert">{errors[field]}</small>}
            </label>
          ))}
        </div>
      </div>
    );
    if (step === 2) return (
      <div className="setup-copy">
        <h1>添加笔录室</h1>
        <p>至少添加一间，之后可由管理员继续调整。</p>
        <div className="setup-room-list">
          {rooms.map((room, index) => (
            <div className="setup-room-row" key={index}>
              <input aria-label={"笔录室 " + (index + 1)} value={room.name}
                onChange={(event) => setRooms((current) => current.map((item, itemIndex) => itemIndex === index ? { name: event.target.value } : item))} />
              {rooms.length > 1 && <button type="button" aria-label="移除笔录室" onClick={() => setRooms((current) => current.filter((_, itemIndex) => itemIndex !== index))}><X size={18} /></button>}
            </div>
          ))}
          <button className="setup-add-room" type="button" onClick={() => setRooms((current) => [...current, { name: "" }])}><Plus size={18} />添加笔录室</button>
          {errors.rooms && <small role="alert">{errors.rooms}</small>}
        </div>
      </div>
    );
    if (step === 3) return (
      <div className="setup-copy">
        <h1>设置工作时间</h1>
        <p>预约以 30 分钟为一个时段，单次最长 180 分钟。</p>
        <div className="setup-hours-grid">
          <label className="setup-field"><span>开始时间</span><input type="time" step="1800" value={hours.start} onChange={(event) => setHours((current) => ({ ...current, start: event.target.value }))} /></label>
          <label className="setup-field"><span>结束时间</span><input type="time" step="1800" value={hours.end} onChange={(event) => setHours((current) => ({ ...current, end: event.target.value }))} /></label>
        </div>
        {errors.hours && <small role="alert">{errors.hours}</small>}
      </div>
    );
    return (
      <div className="setup-copy">
        <CheckCircle size={38} weight="thin" />
        <h1>确认并完成</h1>
        <p>将创建管理员 <strong>{admin.name}</strong>、{rooms.filter((room) => room.name.trim()).length} 间笔录室，工作时间为 {hours.start}–{hours.end}。</p>
        <p>完成后服务需要短暂重启，之后才会开放局域网访问。</p>
      </div>
    );
  }

  return (
    <main className="setup-page">
      <aside className="setup-support">
        <div className="setup-brand"><Asterisk size={32} /></div>
        <ol className="setup-steps">
          {steps.map((label, index) => <li className={index === step ? "active" : index < step ? "complete" : ""} key={label}><span>{index < step ? <CheckCircle size={18} /> : index + 1}</span>{label}</li>)}
        </ol>
      </aside>
      <div className="setup-workspace">
        <div className="setup-stage">
          {stage()}
          {!complete && <div className="setup-actions">
            {step > 0 && <button className="setup-secondary-button" type="button" disabled={busy} onClick={() => setStep((current) => current - 1)}>返回</button>}
            <button className="setup-primary-button" type="button" disabled={busy} onClick={advance}>
              {busy ? <><CircleNotch className="spin" size={18} />正在保存</> : step === 4 ? "完成配置" : "继续"}
            </button>
          </div>}
          {errors.submit && <p className="login-account-feedback" role="alert">{errors.submit}</p>}
        </div>
      </div>
    </main>
  );
}

function PublicDisplay() {
  const [payload, setPayload] = useState(null);
  const [state, setState] = useState("loading");
  const [message, setMessage] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const payloadRef = useRef(null);
  useDocumentTitle("今日引导");

  const load = useCallback(async (manual = false) => {
    if (manual) setRefreshing(true);
    try {
      const next = await api.getPublicDisplay();
      payloadRef.current = next;
      setPayload(next);
      setState(next.status === "online" ? "normal" : "stale");
      if (manual) setMessage("数据连接已恢复");
    } catch (error) {
      setState("offline");
      if (!payloadRef.current) setMessage(error.message || "无法连接局域网服务");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(() => load(), 30000);
    return () => window.clearInterval(timer);
  }, [load]);

  const date = payload?.serverDate ? parseDate(payload.serverDate) : null;
  const dateText = date ? dateLabel(date).replace(" · ", "  ") : "正在读取日期";
  const timeText = payload?.serverTime || "--:--";
  const updated = payload?.lastUpdatedAt ? new Date(payload.lastUpdatedAt).toLocaleTimeString("zh-CN", { hour12: false }) : "--:--:--";
  return (
    <div className={"public-display public-display-" + state} aria-label="公开引导大屏">
      <header className="public-display-header">
        <h1>今日引导</h1>
        <p className="public-display-date">{dateText}</p>
        <div className="public-display-time-block">
          <time>{timeText}</time>
          <span className="public-display-health" role="status">{state === "loading" ? <CircleNotch className="spin" size={18} /> : <i aria-hidden="true" />}{state === "normal" ? "数据正常" : state === "stale" ? "数据可能已过期" : state === "loading" ? "正在读取数据" : "网络已断开"}</span>
        </div>
      </header>
      <main className="public-display-main">
        {(state === "stale" || state === "offline") && <section className="public-display-alert" role="alert">
          <span className="public-display-alert-copy">{state === "offline" ? <WifiSlash size={22} /> : <WarningCircle size={22} />}<span><strong>{state === "offline" ? "网络已断开" : "数据可能已过期"}</strong>{payload ? "当前保留最后一次成功获取的脱敏名单。" : message}</span></span>
          <button type="button" disabled={refreshing} onClick={() => load(true)}><ArrowClockwise className={refreshing ? "spin" : ""} size={20} />{refreshing ? "正在连接" : "重新连接"}</button>
        </section>}
        <section className="public-display-rows" aria-label="笔录室引导名单" aria-busy={state === "loading"}>
          {(payload?.rooms || []).map((room) => <article className="public-display-row" key={room.id}>
            <h2>{room.name}</h2><p className="public-display-action">请前往</p>
            <p className="public-display-current"><strong>{room.current?.maskedPartyName || "暂无安排"}</strong><time>{room.current ? room.current.start + "–" + room.current.end : "当前无安排"}</time></p>
            <p className="public-display-next"><span>下一位</span><span className="public-display-next-person"><strong>{room.next?.maskedPartyName || "暂无安排"}</strong><time>{room.next ? room.next.start + "–" + room.next.end : "暂无后续安排"}</time></span></p>
          </article>)}
          {!payload && state === "loading" && <div className="public-display-loading"><CircleNotch className="spin" size={30} /><span>正在读取今日安排</span></div>}
        </section>
      </main>
      <footer className="public-display-footer"><p><SpeakerHigh size={24} />请留意屏幕引导，按提示前往对应笔录室</p><p>最后更新&nbsp; {updated}<span aria-hidden="true">·</span>姓名已脱敏</p></footer>
      {message && state === "normal" && <div className="public-display-recovery visible" role="status"><CheckCircle size={20} weight="fill" />{message}</div>}
    </div>
  );
}

function Drawer({ open, heading, onClose, children, className = "" }) {
  const ref = useRef(null);
  useFocusTrap(ref, open, onClose, true);
  return (
    <>
      <button className={"drawer-backdrop " + (open ? "visible" : "")} aria-label="关闭侧栏" aria-hidden={!open} tabIndex={-1} onClick={onClose} />
      <aside ref={ref} className={"booking-drawer " + (open ? "open " : "") + className} aria-hidden={!open} aria-label="操作侧栏" role="dialog" aria-modal={open || undefined}>
        {open && <><div className="drawer-topline"><span>{heading}</span><button className="drawer-close" aria-label="关闭" onClick={onClose}><X size={20} /></button></div>{children}</>}
      </aside>
    </>
  );
}

function SessionExpired({ onRecovered }) {
  const ref = useRef(null);
  const [editing, setEditing] = useState(false);
  const [credentials, setCredentials] = useState({ username: "", password: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useFocusTrap(ref, true, null, false);
  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.getSession();
      await api.login(credentials.username.trim(), credentials.password);
      onRecovered();
    } catch (caught) {
      setError(caught.message || "重新登录失败");
    } finally {
      setBusy(false);
    }
  }
  return <div className="session-expired-layer"><section ref={ref} className="session-expired-dialog" role="dialog" aria-modal="true" aria-labelledby="session-expired-heading">
    <span className="session-expired-icon" aria-hidden="true"><LockSimple size={22} /></span>
    <h2 id="session-expired-heading">登录已过期</h2>
    <p>为保护账户安全，请重新登录。当前未保存内容将保留在本页。</p>
    {!editing ? <button type="button" data-initial-focus onClick={() => setEditing(true)}>重新登录</button> :
      <form className="session-reauth-form" onSubmit={submit}>
        <input data-initial-focus aria-label="用户名" autoComplete="username" placeholder="用户名" value={credentials.username} onChange={(event) => setCredentials((current) => ({ ...current, username: event.target.value }))} />
        <input aria-label="密码" type="password" autoComplete="current-password" placeholder="密码" value={credentials.password} onChange={(event) => setCredentials((current) => ({ ...current, password: event.target.value }))} />
        {error && <small role="alert">{error}</small>}
        <button type="submit" disabled={busy}>{busy ? "正在登录…" : "登录并继续"}</button>
      </form>}
  </section></div>;
}

function BookingForm({ form, setForm, errors, rooms, tags, settings, busy, failure, conflict, onSubmit, onDismissFailure, onUseLatest, onContinueDraft }) {
  const maxDuration = Number(settings.maxDurationMinutes || 180);
  const durations = DURATION_STEPS.filter((value) => value <= maxDuration);
  return <form className="booking-form" onSubmit={onSubmit} noValidate aria-busy={busy}>
    <div className="booking-form-time"><strong>{form.start || "选择时段"}{form.start ? "–" + endFromDuration(form.start, form.duration) : ""}</strong><span>{form.date ? dateLabel(form.date) : "请选择日期"}</span></div>
    {Object.keys(errors).length > 0 && <p className="form-error-summary" role="alert">请检查 {Object.keys(errors).length} 个字段</p>}
    <div className="booking-schedule-grid">
      <label><span>日期</span><input type="date" value={form.date} aria-invalid={Boolean(errors.date)} onChange={(event) => setForm((current) => ({ ...current, date: event.target.value }))} />{errors.date && <small>{errors.date}</small>}</label>
      <label><span>笔录室</span><select value={form.roomId} aria-invalid={Boolean(errors.roomId)} onChange={(event) => setForm((current) => ({ ...current, roomId: event.target.value }))}><option value="">请选择</option>{rooms.filter((room) => room.isActive !== false).map((room) => <option value={room.id} key={room.id}>{room.name}</option>)}</select>{errors.roomId && <small>{errors.roomId}</small>}</label>
      <label className="booking-start-field"><span>开始时间</span><input type="time" step={Number(settings.slotMinutes || 30) * 60} value={form.start} aria-invalid={Boolean(errors.start)} onChange={(event) => setForm((current) => ({ ...current, start: event.target.value }))} />{errors.start && <small>{errors.start}</small>}</label>
    </div>
    <div className="duration-field">
      <div className="duration-label"><span>预约时长</span><strong>{form.duration} 分钟</strong></div>
      <input className="duration-range" type="range" min={durations[0] || 30} max={durations.at(-1) || 180} step="30" value={form.duration} onChange={(event) => setForm((current) => ({ ...current, duration: Number(event.target.value) }))} />
    </div>
    <div className="booking-info-section"><h3>预约信息</h3>
      <label><span>预约对象</span><input data-initial-focus value={form.partyName} aria-invalid={Boolean(errors.partyName)} onChange={(event) => setForm((current) => ({ ...current, partyName: event.target.value }))} />{errors.partyName && <small>{errors.partyName}</small>}</label>
      <label><span>案号</span><input value={form.caseNumber} aria-invalid={Boolean(errors.caseNumber)} onChange={(event) => setForm((current) => ({ ...current, caseNumber: event.target.value }))} />{errors.caseNumber && <small>{errors.caseNumber}</small>}</label>
      <label><span>事项</span><input value={form.purpose} aria-invalid={Boolean(errors.purpose)} onChange={(event) => setForm((current) => ({ ...current, purpose: event.target.value }))} />{errors.purpose && <small>{errors.purpose}</small>}</label>
      <fieldset className="tag-choice-row"><legend>标签</legend>{tags.map((tag) => <label style={tagStyle(tag)} key={tag.id}><input type="radio" name="tag" checked={form.tagId === tag.id} onChange={() => setForm((current) => ({ ...current, tagId: tag.id }))} /><i />{tag.label}</label>)}</fieldset>
      {errors.tagId && <small>{errors.tagId}</small>}
      <label><span>备注</span><textarea value={form.notes} onChange={(event) => setForm((current) => ({ ...current, notes: event.target.value }))} /></label>
    </div>
    {failure && <div className="save-failure-panel" role="alert"><strong>保存失败</strong><p>未能保存本次修改，你填写的内容已保留。</p><button type="button" onClick={onDismissFailure}>稍后处理</button></div>}
    {conflict?.type === "revision" && <div className="modified-conflict-panel" role="alert"><h3>预约内容已发生变化</h3><p>其他用户已更新这场预约。你的草稿仍然保留。</p><button type="button" onClick={onUseLatest}>使用最新内容</button><button type="button" onClick={onContinueDraft}>返回继续调整</button></div>}
    <div className="drawer-fixed-footer">{busy ? <div className="booking-saving-strip" role="status"><CircleNotch className="spin" size={20} /><span><strong>正在保存预约</strong>请稍候</span></div> : <button className="primary-button" type="submit">{failure ? "重试保存" : "保存预约"}</button>}</div>
  </form>;
}

function BookingDetails({ booking, tag, canManage, onEdit, onCancel, onClose }) {
  return <div className="booking-details">
    <div className="drawer-hero"><h2>{booking.start}–{booking.end}</h2><p>{dateLabel(booking.date)}</p>{booking.status && <span className="drawer-status"><i />{booking.status === "active" ? "已预约" : booking.status}</span>}</div>
    <dl>
      <div><dt>笔录室</dt><dd>{booking.roomName}</dd></div>
      <div><dt>预约者</dt><dd>{booking.owner?.name || booking.ownerName || "未知用户"}</dd></div>
      <div><dt>事项</dt><dd>{booking.purpose}</dd></div>
      <div><dt>标签</dt><dd className="detail-tag" style={tagStyle(tag)}><i />{booking.tagLabel || tag?.label}</dd></div>
      <div><dt>当事人</dt><dd>{booking.partyName}</dd></div>
      <div><dt>案号</dt><dd>{booking.caseNumber}</dd></div>
      {booking.notes && <div><dt>备注</dt><dd>{booking.notes}</dd></div>}
    </dl>
    {canManage ? <div className="booking-detail-actions"><button className="edit-booking-button" onClick={onEdit}>修改预约</button><button className="cancel-booking-button" onClick={onCancel}>取消预约</button></div> : <button className="secondary-button booking-detail-close" onClick={onClose}>关闭</button>}
  </div>;
}

function MainApp({ session, onLoggedOut }) {
  const [bootstrap, setBootstrap] = useState(null);
  const [activeView, setActiveView] = useState("mine");
  const [currentDate, setCurrentDate] = useState(() => new Date());
  const [bookings, setBookings] = useState([]);
  const [upcoming, setUpcoming] = useState([]);
  const [history, setHistory] = useState([]);
  const [historyMonth, setHistoryMonth] = useState(() => monthKey(new Date()));
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyOwner, setHistoryOwner] = useState("");
  const [historyRoom, setHistoryRoom] = useState("");
  const [historyTag, setHistoryTag] = useState("");
  const [historyFilterOpen, setHistoryFilterOpen] = useState(false);
  const [drawer, setDrawer] = useState(null);
  const [bookingForm, setBookingForm] = useState(EMPTY_BOOKING);
  const [bookingErrors, setBookingErrors] = useState({});
  const [saveState, setSaveState] = useState("idle");
  const [conflict, setConflict] = useState(null);
  const [loading, setLoading] = useState({ bootstrap: true, calendar: true, mine: true, history: true });
  const [networkOffline, setNetworkOffline] = useState(false);
  const [sessionExpired, setSessionExpired] = useState(false);
  const [unauthorizedMessage, setUnauthorizedMessage] = useState("");
  const [successNotice, setSuccessNotice] = useState(null);
  const [toast, setToast] = useState("");
  const [calendarFilterOpen, setCalendarFilterOpen] = useState(false);
  const [calendarTagFilter, setCalendarTagFilter] = useState("");
  const [tagEditing, setTagEditing] = useState(false);
  const [tagDrafts, setTagDrafts] = useState({});
  const [users, setUsers] = useState([]);
  const [rooms, setRooms] = useState([]);
  const [system, setSystem] = useState(null);
  const [preferencesDraft, setPreferencesDraft] = useState(null);
  const [dueReminder, setDueReminder] = useState(null);
  const [preservedDraft, setPreservedDraft] = useState(null);
  const mainRef = useRef(null);
  const role = bootstrap?.currentUser?.role || session.currentUser?.role;
  const currentUser = bootstrap?.currentUser || session.currentUser;
  const permissions = bootstrap?.permissions || {};
  const settings = bootstrap?.settings || { workStart: "08:30", workEnd: "17:30", slotMinutes: 30, maxDurationMinutes: 180 };
  const tags = useMemo(() => [...(bootstrap?.globalTags || []), ...(bootstrap?.personalTags || [])].map(normalizeTag).sort((a, b) => a.slot - b.slot), [bootstrap]);
  const timeSlots = useMemo(() => {
    try { return generateTimeSlots(settings.workStart, settings.workEnd, settings.slotMinutes || 30); }
    catch { return generateTimeSlots("08:30", "17:30", 30); }
  }, [settings.workEnd, settings.workStart, settings.slotMinutes]);
  useDocumentTitle({ mine: "我的预约", calendar: "预约日历", history: "预约记录", rooms: "笔录室", users: "用户管理", system: "系统状态", settings: "个人设置", unauthorized: "无权限" }[activeView] || "会议室预约系统");

  const handleError = useCallback((error, fallback) => {
    if (error?.status === 401 || error?.code === "SESSION_EXPIRED" || error?.code === "SESSION_REQUIRED") {
      setSessionExpired(true);
      return;
    }
    if (error?.status === 403 && error?.code === "FORBIDDEN") {
      setUnauthorizedMessage(error.message || "当前账户没有访问该页面的权限");
      setActiveView("unauthorized");
      return;
    }
    if (error?.code === "NETWORK_ERROR") setNetworkOffline(true);
    setToast(error?.message || fallback || "请求未能完成");
  }, []);

  const loadBootstrap = useCallback(async () => {
    setLoading((current) => ({ ...current, bootstrap: true }));
    try {
      const value = await api.getBootstrap();
      setBootstrap(value);
      setRooms(value.rooms || []);
      setUsers(value.users || []);
      setPreferencesDraft(value.preferences || {});
      setTagDrafts(Object.fromEntries([...(value.globalTags || []), ...(value.personalTags || [])].map((tag, index) => [tag.id || "tag-" + (tag.slot || index + 1), tag.label || tag.name || "标签 " + (tag.slot || index + 1)])));
      setNetworkOffline(false);
    } catch (error) {
      handleError(error, "无法读取系统配置");
    } finally {
      setLoading((current) => ({ ...current, bootstrap: false }));
    }
  }, [handleError]);

  const loadCalendar = useCallback(async () => {
    setLoading((current) => ({ ...current, calendar: true }));
    try {
      const result = await api.getReservations(dateKey(currentDate));
      setBookings(unwrapItems(result));
      setNetworkOffline(false);
    } catch (error) {
      handleError(error, "无法读取预约日历");
    } finally {
      setLoading((current) => ({ ...current, calendar: false }));
    }
  }, [currentDate, handleError]);

  const loadUpcoming = useCallback(async () => {
    setLoading((current) => ({ ...current, mine: true }));
    try {
      const result = await api.getUpcoming();
      setUpcoming(unwrapItems(result));
    } catch (error) {
      handleError(error, "无法读取我的预约");
    } finally {
      setLoading((current) => ({ ...current, mine: false }));
    }
  }, [handleError]);

  const loadHistory = useCallback(async () => {
    setLoading((current) => ({ ...current, history: true }));
    try {
      const result = await api.getHistory({ month: historyMonth, ownerId: role === "admin" ? historyOwner : undefined, roomId: historyRoom, tagId: historyTag, query: historyQuery.trim() });
      setHistory(unwrapItems(result));
    } catch (error) {
      handleError(error, "无法读取预约记录");
    } finally {
      setLoading((current) => ({ ...current, history: false }));
    }
  }, [handleError, historyMonth, historyOwner, historyQuery, historyRoom, historyTag, role]);

  useEffect(() => { loadBootstrap(); }, [loadBootstrap]);
  useEffect(() => { if (bootstrap) loadCalendar(); }, [bootstrap, loadCalendar]);
  useEffect(() => { if (bootstrap) loadUpcoming(); }, [bootstrap, loadUpcoming]);
  useEffect(() => { if (bootstrap) loadHistory(); }, [bootstrap, loadHistory]);
  useEffect(() => {
    if (!bootstrap?.preferences?.bookingReminder && !bootstrap?.preferences?.bookingChangeNotifications) return undefined;
    let cancelled = false;
    const check = async () => {
      try {
        const result = await api.getDueReminders();
        if (!cancelled) setDueReminder(unwrapItems(result)[0] || null);
      } catch (error) {
        if (error.status === 401) setSessionExpired(true);
      }
    };
    check();
    const timer = window.setInterval(check, 60000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [bootstrap]);
  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToast(""), 4200);
    return () => window.clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    if (mainRef.current) {
      if (drawer || sessionExpired) { mainRef.current.setAttribute("inert", ""); mainRef.current.setAttribute("aria-hidden", "true"); }
      else { mainRef.current.removeAttribute("inert"); mainRef.current.removeAttribute("aria-hidden"); }
    }
  }, [drawer, sessionExpired]);

  if (loading.bootstrap && !bootstrap) return <LoadingScreen label="正在读取工作台" />;
  if (!bootstrap) return <FatalScreen error="无法读取系统配置" onRetry={loadBootstrap} />;

  const activeRooms = rooms.filter((room) => room.isActive !== false).sort((a, b) => Number(a.sortOrder || 0) - Number(b.sortOrder || 0));
  const bookingFor = (roomId, start, end) => bookings.find((booking) => booking.roomId === roomId && booking.status !== "cancelled" && overlaps(booking, start, end));
  const tagFor = (booking) => tags.find((tag) => tag.id === booking?.tagId) || normalizeTag({ id: booking?.tagId, label: booking?.tagLabel, slot: 1 }, 0);

  function navigate(view) {
    const item = NAV_ITEMS.find((nav) => nav.id === view);
    if (item?.permission && !permissions[item.permission]) {
      setToast("当前账户无权访问" + item.label);
      return;
    }
    setDrawer(null);
    setUnauthorizedMessage("");
    setActiveView(view);
  }

  function openCreate(roomId, start, bookingDate = dateKey(currentDate)) {
    setBookingErrors({});
    setSaveState("idle");
    setConflict(null);
    setBookingForm({
      ...EMPTY_BOOKING,
      ...(preservedDraft || {}),
      roomId,
      start,
      date: bookingDate,
      duration: Number(preservedDraft?.duration || bootstrap.preferences?.defaultDuration || 60),
      tagId: preservedDraft?.tagId || tags[0]?.id || "",
    });
    setPreservedDraft(null);
    setDrawer({ type: "create" });
  }

  async function openDefaultCreate() {
    const preferredRoom = activeRooms.find((room) => room.id === bootstrap.preferences?.defaultRoomId) || activeRooms[0];
    navigate("calendar");
    if (!preferredRoom) return;
    setLoading((current) => ({ ...current, calendar: true }));
    try {
      const now = new Date();
      const currentTime = String(now.getHours()).padStart(2, "0") + ":" + String(now.getMinutes()).padStart(2, "0");
      for (let offset = 0; offset < 14; offset += 1) {
        const day = shiftDate(now, offset);
        const dayKey = dateKey(day);
        const result = await api.getReservations(dayKey);
        const dayBookings = unwrapItems(result);
        const start = findFirstAvailableStart({
          bookings: dayBookings,
          roomId: preferredRoom.id,
          slots: timeSlots,
          notBefore: offset === 0 ? currentTime : "",
        });
        if (!start) continue;
        setCurrentDate(day);
        setBookings(dayBookings);
        setNetworkOffline(false);
        openCreate(preferredRoom.id, start, dayKey);
        return;
      }
      setToast("未来两周内没有可用时段，请在日历中选择其他笔录室");
    } catch (error) {
      handleError(error, "无法读取可用时段");
    } finally {
      setLoading((current) => ({ ...current, calendar: false }));
    }
  }

  function openDetails(booking, readOnly = false) {
    setDrawer({ type: "details", booking, readOnly });
  }

  function openEdit(booking) {
    setBookingErrors({});
    setSaveState("idle");
    setConflict(null);
    setBookingForm({
      roomId: booking.roomId,
      date: booking.date,
      start: booking.start,
      duration: durationFromRange(booking.start, booking.end),
      partyName: booking.partyName || "",
      caseNumber: booking.caseNumber || "",
      purpose: booking.purpose || "",
      notes: booking.notes || "",
      tagId: booking.tagId || "",
    });
    setDrawer({ type: "edit", booking });
  }

  async function saveBooking(event) {
    event.preventDefault();
    const errors = validateBookingForm(bookingForm);
    setBookingErrors(errors);
    if (Object.keys(errors).length) return;
    setSaveState("saving");
    setConflict(null);
    try {
      const saved = drawer.type === "edit"
        ? await api.updateReservation(drawer.booking.id, bookingPayload(bookingForm, drawer.booking.revision))
        : await api.createReservation(bookingPayload(bookingForm));
      setDrawer(null);
      setSuccessNotice({ action: drawer.type === "edit" ? "预约已更新" : "预约已创建", booking: saved });
      await Promise.all([loadCalendar(), loadUpcoming(), loadHistory()]);
      setSaveState("idle");
    } catch (error) {
      if (error.code === "SLOT_CONFLICT") {
        setConflict({ type: "slot", conflicts: error.conflicts });
        setDrawer((current) => ({ ...current, type: "slot-conflict" }));
      } else if (error.code === "REVISION_CONFLICT") {
        const rebased = rebaseBookingEdit(bookingForm, error.current);
        setBookingForm(rebased.draft);
        setDrawer((current) => ({ ...current, booking: rebased.baseline }));
        setConflict({ type: "revision", current: rebased.baseline });
        setSaveState("idle");
      } else if (error.code === "VALIDATION_ERROR") {
        setBookingErrors(error.fields || {});
        setSaveState("idle");
      } else {
        if (error.status === 401) setSessionExpired(true);
        setSaveState("failed");
      }
    }
  }

  async function cancelBooking() {
    setSaveState("saving");
    try {
      await api.cancelReservation(drawer.booking.id, drawer.booking.revision);
      setDrawer(null);
      setToast("预约已取消");
      await Promise.all([loadCalendar(), loadUpcoming(), loadHistory()]);
    } catch (error) {
      if (error.code === "REVISION_CONFLICT") {
        setDrawer({ type: "details", booking: error.current });
        setToast("预约已被其他用户修改，已显示最新内容");
      } else handleError(error, "取消预约失败");
    } finally {
      setSaveState("idle");
    }
  }

  async function saveTags() {
    try {
      if (role === "admin") {
        await api.updateGlobalTags(tags.filter((tag) => tag.slot <= 2).map((tag) => ({ id: tag.id, slot: tag.slot, label: tagDrafts[tag.id]?.trim() })));
      }
      await api.updatePreferences({
        ...bootstrap.preferences,
        personalTags: tags.filter((tag) => tag.slot >= 3).map((tag) => ({ slot: tag.slot, label: tagDrafts[tag.id]?.trim() })),
      });
      await loadBootstrap();
      setTagEditing(false);
      setToast("标签名称已保存");
    } catch (error) {
      handleError(error, "保存标签失败");
    }
  }

  async function acknowledgeReminder() {
    if (!dueReminder) return;
    const acknowledged = dueReminder;
    try {
      await api.acknowledgeReminder(acknowledged.reservationId || acknowledged.id, acknowledged.revision, acknowledged.kind);
    } catch (error) {
      handleError(error, "无法确认提醒");
      return;
    }
    setDueReminder(null);
    if (acknowledged.kind === "change") {
      await Promise.all([loadCalendar(), loadUpcoming(), loadHistory()]);
    }
    try {
      const result = await api.getDueReminders();
      setDueReminder(unwrapItems(result)[0] || null);
    } catch (error) {
      if (error.status === 401) setSessionExpired(true);
    }
  }

  function renderMine() {
    const items = upcoming.filter((booking) => booking.ownerId === currentUser.id);
    const next = items[0];
    return <main className="main-canvas bookings-canvas"><header className="page-header bookings-header"><h1>我的预约</h1><button className="filter-trigger" aria-label="刷新我的预约" onClick={loadUpcoming}><ArrowClockwise size={19} /></button></header>
      <div className="bookings-layout">
        {loading.mine ? <div className="bookings-empty" role="status"><CircleNotch className="spin" size={30} /><p>正在读取预约</p></div> :
          next ? <button className="next-booking" onClick={() => openDetails(next)}><span className="next-booking-time"><span className="next-booking-time-value">{next.start}</span><span className="next-booking-time-separator">–</span><span className="next-booking-time-value">{next.end}</span></span><span className="next-booking-room" style={tagStyle(tagFor(next))}><i />{next.roomName}</span><CaretRight className="next-booking-caret" size={25} /></button> :
          <div className="bookings-empty booking-zero-state"><CalendarBlank size={44} weight="thin" /><h2>还没有预约</h2><p>创建预约后，最近的一场显示在这里。</p><button className="empty-primary-action" onClick={openDefaultCreate}>前往预约日历</button></div>}
        {items.length > 1 && <section className="later-section"><h2>之后</h2><div className="appointment-list">{items.slice(1).map((booking) => <button className="appointment-row" key={booking.id} onClick={() => openDetails(booking)}><span className="row-date">{parseDate(booking.date).getDate()}日<small>{dateLabel(booking.date).split("· ")[1]}</small></span><span className="row-time">{booking.start}–{booking.end}</span><span className="row-room">{booking.roomName}</span><CaretRight className="row-caret" size={20} /></button>)}</div></section>}
      </div>
    </main>;
  }

  function moveCalendarFocus(event) {
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
    const cells = [...event.currentTarget.querySelectorAll("button.slot:not([disabled])")];
    if (!cells.length) return;
    const currentIndex = Math.max(0, cells.indexOf(event.target));
    let nextIndex = currentIndex;
    if (event.key === "ArrowLeft") nextIndex -= 1;
    if (event.key === "ArrowRight") nextIndex += 1;
    if (event.key === "ArrowUp") nextIndex -= activeRooms.length;
    if (event.key === "ArrowDown") nextIndex += activeRooms.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = cells.length - 1;
    nextIndex = Math.max(0, Math.min(cells.length - 1, nextIndex));
    event.preventDefault();
    cells[nextIndex].focus();
  }

  function renderCalendar() {
    return <main className="main-canvas calendar-canvas">
      <header className="page-header calendar-header"><div><h1>预约日历</h1><p>{dateLabel(currentDate)}</p></div>
        <div className="header-actions"><button className="icon-button" aria-label="前一天" disabled={networkOffline} onClick={() => setCurrentDate((date) => shiftDate(date, -1))}><CaretLeft size={19} /></button><button className="today-button" disabled={networkOffline} onClick={() => setCurrentDate(new Date())}>今天</button><button className="icon-button" aria-label="后一天" disabled={networkOffline} onClick={() => setCurrentDate((date) => shiftDate(date, 1))}><CaretRight size={19} /></button>
          <div className="filter-wrap"><button className={"icon-button calendar-filter-trigger " + (calendarFilterOpen ? "pressed" : "") + (calendarTagFilter ? " filtered" : "")} aria-label="查看标签颜色并筛选" aria-expanded={calendarFilterOpen} onClick={() => setCalendarFilterOpen((open) => !open)}><FunnelSimple size={20} /></button>
            {calendarFilterOpen && <div className="tag-palette-popover" role="group"><div className="popover-heading"><span>{tagEditing ? "编辑标签名称" : "标签颜色"}</span><button onClick={() => setTagEditing((editing) => !editing)}>{tagEditing ? "取消" : <><PencilSimple size={13} />编辑</>}</button></div>
              {tagEditing ? <div className="tag-edit-panel">{tags.map((tag) => <label className="tag-edit-row" style={tagStyle(tag)} key={tag.id}><i /><input value={tagDrafts[tag.id] || ""} readOnly={tag.slot <= 2 && role !== "admin"} onChange={(event) => setTagDrafts((current) => ({ ...current, [tag.id]: event.target.value }))} /></label>)}<button className="tag-edit-save" onClick={saveTags}>完成</button></div> :
                <div className="tag-palette-list"><button className={"tag-filter-option " + (!calendarTagFilter ? "active" : "")} onClick={() => setCalendarTagFilter("")}>全部</button>{tags.map((tag) => <button className={"tag-filter-option " + (calendarTagFilter === tag.id ? "active" : "")} style={tagStyle(tag)} key={tag.id} onClick={() => setCalendarTagFilter((current) => current === tag.id ? "" : tag.id)}><i /><span>{tag.label}</span></button>)}</div>}
            </div>}
          </div>
        </div>
      </header>
      {successNotice && <section className="calendar-success-notice" role="status"><CheckCircle size={20} /><p><strong>{successNotice.action}</strong><span>·</span>{successNotice.booking.roomName}<span>·</span>{successNotice.booking.start}–{successNotice.booking.end}</p><button onClick={() => openDetails(successNotice.booking)}>查看</button><button className="calendar-success-close" aria-label="关闭" onClick={() => setSuccessNotice(null)}><X size={16} /></button></section>}
      {networkOffline && <section className="calendar-network-banner" role="status"><span className="calendar-network-icon"><WifiSlash size={18} /></span><div><strong>网络连接已断开</strong><p>当前显示最后一次成功获取的数据。</p></div><button onClick={loadCalendar}><ArrowClockwise size={16} />重新连接</button></section>}
      <section className="calendar-section"><div className="calendar-meta"><p>{loading.calendar ? "正在读取预约数据" : activeRooms.length ? "选择空白时段以创建预约" : "请先启用或创建笔录室"}</p></div>
        {loading.calendar && !bookings.length ? <div className="calendar-loading-state" role="status"><CircleNotch className="spin" size={28} /><span>正在读取预约数据</span></div> :
          !activeRooms.length ? <div className="calendar-zero-state"><DoorOpen size={48} weight="thin" /><div><h2>当前没有可预约的笔录室</h2><p>{permissions.manageRooms ? "请先启用或创建至少一个笔录室。" : "请联系管理员启用笔录室。"}</p>{permissions.manageRooms && <button onClick={() => navigate("rooms")}>前往笔录室管理</button>}</div></div> :
          <div className="schedule-viewport"><div className="schedule" style={{ "--room-count": activeRooms.length }} role="grid" tabIndex={0} onKeyDown={moveCalendarFocus} aria-label={dateLabel(currentDate) + "预约日历；使用方向键在时段间移动"}>
            <div className="schedule-head"><div />{activeRooms.map((room) => <div className="room-heading" key={room.id}>{room.name}</div>)}</div>
            <div className="schedule-body">{timeSlots.map(([start, end]) => <div className="schedule-row" key={start}><div className="time-label">{start}</div>{activeRooms.map((room) => {
              const booking = bookingFor(room.id, start, end);
              if (booking && booking.start !== start) return <div className="slot occupied-slot" aria-hidden="true" key={room.id + start} />;
              if (booking) {
                const tag = tagFor(booking);
                return <button className={"slot booked-slot " + (calendarTagFilter && calendarTagFilter !== booking.tagId ? "tag-muted" : "")} style={{ ...tagStyle(tag), "--booking-span": Math.max(1, Math.round(durationFromRange(booking.start, booking.end) / Number(settings.slotMinutes || 30))) }} key={room.id + start} tabIndex={-1} onClick={() => openDetails(booking)} aria-label={room.name + " " + booking.start + "至" + booking.end + "，预约者" + (booking.owner?.name || "未知用户") + "，当事人" + booking.partyName + "，案号" + booking.caseNumber}><span className="booking-title"><i />{booking.owner?.name || "未知用户"} · 已预约</span><span className="booking-case">案号 {booking.caseNumber}</span></button>;
              }
              return <button className="slot available-slot" disabled={networkOffline} key={room.id + start} tabIndex={-1} onClick={() => openCreate(room.id, start)} aria-label={room.name + " " + start + "至" + end + " 可预约"}><span className="slot-affordance"><Plus size={18} /><span>{start} · 新建预约</span></span></button>;
            })}</div>)}</div>
          </div></div>}
      </section>
    </main>;
  }

  function stepMonth(delta) {
    const [year, month] = historyMonth.split("-").map(Number);
    setHistoryMonth(monthKey(new Date(year, month - 1 + delta, 1)));
  }

  function renderHistory() {
    const selectedOwner = users.find((user) => user.id === historyOwner);
    const ownerPersonalTags = historyOwner
      ? (selectedOwner?.personalTags || []).map(normalizeTag)
      : tags.filter((tag) => tag.slot >= 3).map((tag) => ({ ...tag, label: "个人标签 " + tag.slot }));
    const historyTags = [
      ...tags.filter((tag) => tag.slot <= 2),
      ...ownerPersonalTags,
    ];
    return <main className="main-canvas history-canvas"><header className="page-header history-header"><h1>预约记录</h1><div className="history-header-actions"><div className="filter-wrap"><button className={"filter-trigger history-tool-button " + (historyFilterOpen ? "pressed" : "")} aria-label="筛选预约记录" onClick={() => setHistoryFilterOpen((open) => !open)}><SlidersHorizontal size={20} /></button>{historyFilterOpen && <div className="history-filter-popover">
      <div className="popover-heading"><span>筛选记录</span><button onClick={() => { setHistoryOwner(""); setHistoryRoom(""); setHistoryTag(""); setHistoryQuery(""); }}>重置</button></div>
      {role === "employee" ? <p>仅显示本人的预约记录</p> : <label><span>预约者</span><select value={historyOwner} onChange={(event) => { setHistoryOwner(event.target.value); if (historyTag === "tag-3" || historyTag === "tag-4") setHistoryTag(""); }}><option value="">全单位预约</option>{users.filter((user) => user.enabled !== false).map((user) => <option value={user.id} key={user.id}>{user.name}</option>)}</select></label>}
      <label><span>笔录室</span><select value={historyRoom} onChange={(event) => setHistoryRoom(event.target.value)}><option value="">全部笔录室</option>{rooms.map((room) => <option value={room.id} key={room.id}>{room.name}</option>)}</select></label>
      <label><span>标签</span><select value={historyTag} onChange={(event) => setHistoryTag(event.target.value)}><option value="">全部标签</option>{historyTags.map((tag) => <option value={tag.id} key={tag.id} disabled={tag.slot >= 3 && role === "admin" && !historyOwner}>{tag.label}</option>)}</select></label>
    </div>}</div></div></header>
      <div className="history-layout"><div className="history-month-nav"><button className="history-month-step" aria-label="上一个月" onClick={() => stepMonth(-1)}><CaretLeft size={21} /></button><button className="history-month-step" aria-label="下一个月" onClick={() => stepMonth(1)}><CaretRight size={21} /></button><label className="history-month-select"><input type="month" value={historyMonth} onChange={(event) => setHistoryMonth(event.target.value)} /></label><span className="history-count">{history.length} 场</span><div className="history-inline-search"><MagnifyingGlass size={18} /><input type="search" value={historyQuery} placeholder="搜索案号、当事人" onChange={(event) => setHistoryQuery(event.target.value)} /></div></div>
        <section className="history-list">{loading.history ? <div className="history-empty"><CircleNotch className="spin" size={28} /><p>正在读取预约记录</p></div> : history.length ? history.map((booking) => <button className="history-row" key={booking.id} onClick={() => openDetails(booking, true)}><span className="history-row-date"><strong>{String(parseDate(booking.date).getDate()).padStart(2, "0")}</strong><small>{dateLabel(booking.date).split("· ")[1]}</small></span><span className="history-row-main"><strong>{booking.start}–{booking.end}<em>{booking.roomName}</em></strong><small>{booking.caseNumber}</small></span><span className="history-row-tag" style={tagStyle(tagFor(booking))}><i /></span><CaretRight size={18} /></button>) : <div className="history-empty history-zero-state"><ClockCounterClockwise size={42} weight="thin" /><h2>这个月还没有预约记录</h2><p>切换月份，或调整搜索和筛选条件。</p></div>}</section>
      </div>
    </main>;
  }

  function openRoom(room = null) {
    setDrawer({ type: room ? "room-edit" : "room-create", room, form: room ? { name: room.name, sortOrder: Number(room.sortOrder || 1), isActive: room.isActive !== false } : { name: "", sortOrder: rooms.length + 1, isActive: true } });
  }

  async function saveRoom(event) {
    event.preventDefault();
    if (!drawer.form.name.trim()) return;
    try {
      if (drawer.type === "room-edit") await api.updateRoom(drawer.room.id, drawer.form);
      else await api.createRoom(drawer.form);
      setDrawer(null); setToast("笔录室已保存"); await loadBootstrap();
    } catch (error) { handleError(error, "保存笔录室失败"); }
  }

  async function deleteRoom() {
    if (drawer?.type !== "room-edit") return;
    try {
      await api.deleteRoom(drawer.room.id);
      setDrawer(null);
      setToast("笔录室已删除");
      await loadBootstrap();
    } catch (error) {
      handleError(error, error.code === "ROOM_HAS_FUTURE_BOOKINGS" ? "该笔录室仍有未结束预约，不能删除" : "删除笔录室失败");
    }
  }

  function renderRooms() {
    return <main className="main-canvas rooms-canvas"><header className="page-header rooms-header"><div><h1>笔录室</h1><p>管理预约日历中可使用的笔录室</p></div><button className="room-create-button" aria-label="添加笔录室" data-tooltip="添加笔录室" onClick={() => openRoom()}><Plus size={22} /></button></header>
      <section className="room-overview">{[...rooms].sort((a, b) => Number(a.sortOrder) - Number(b.sortOrder)).map((room) => <article className="room-column" key={room.id}><div className="room-column-heading"><span className="room-sort-order">{String(room.sortOrder || 0).padStart(2, "0")}</span><h2><button className="room-title-button" onClick={() => openRoom(room)}><span>{room.name}</span><CaretRight size={22} /></button></h2><span className={"room-state " + (room.isActive !== false ? "active" : "inactive")}><i />{room.isActive !== false ? "启用" : "停用"}</span></div><dl className="room-metrics"><div><dt><CalendarBlank size={20} />今天</dt><dd>{room.todayCount || 0} 场</dd></div><div><dt><ClockCounterClockwise size={20} />未来</dt><dd>{room.futureCount || 0} 场</dd></div></dl><div className="room-next-booking"><span>下一场</span><strong>{room.nextBooking || "暂无安排"}</strong></div></article>)}</section>
    </main>;
  }

  function openUser(user = null) {
    setDrawer({ type: user ? "user-edit" : "user-create", user, form: user ? { name: user.name, username: user.username, department: user.department, role: user.role, enabled: user.enabled !== false, password: "" } : { name: "", username: "", department: "", role: "employee", enabled: true, password: "" } });
  }

  async function saveUser(event) {
    event.preventDefault();
    try {
      if (drawer.type === "user-edit") await api.updateUser(drawer.user.id, { name: drawer.form.name.trim(), department: drawer.form.department.trim(), role: drawer.form.role, enabled: drawer.form.enabled });
      else await api.createUser({ ...drawer.form, name: drawer.form.name.trim(), username: drawer.form.username.trim(), department: drawer.form.department.trim() });
      setDrawer(null); setToast("用户已保存"); await loadBootstrap();
    } catch (error) { handleError(error, error.code === "LAST_ADMIN_REQUIRED" ? "必须保留至少一名启用管理员" : "保存用户失败"); }
  }

  async function resetPassword(event) {
    event.preventDefault();
    try { await api.resetUserPassword(drawer.user.id, drawer.form.password); setDrawer(null); setToast("密码已重置"); }
    catch (error) { handleError(error, "重置密码失败"); }
  }

  function renderUsers() {
    return <main className="main-canvas users-canvas"><header className="page-header users-header"><div><h1>用户管理</h1><p>管理可登录系统的用户与权限</p></div><button className="users-create-button" onClick={() => openUser()}><span className="users-create-icon"><Plus size={18} /></span>新建用户</button></header>
      <section className="user-roster"><div className="user-roster-heading"><span>用户</span><span>部门</span><span>角色</span><span>状态</span></div><div className="user-roster-rows">{users.map((user) => <button className="user-roster-row" key={user.id} onClick={() => openUser(user)}><span className="user-primary"><strong>{user.name}</strong><small>{user.username}</small></span><span className="user-department">{user.department}</span><span className={"user-role " + user.role}>{user.role === "admin" ? "管理员" : "普通员工"}</span><span className={"user-status " + (user.enabled !== false ? "enabled" : "disabled")}><i />{user.enabled !== false ? "启用" : "已停用"}</span><CaretRight className="user-row-edit" size={17} /></button>)}</div></section>
    </main>;
  }

  async function loadSystem() {
    try { setSystem(await api.getSystem()); } catch (error) { handleError(error, "读取系统状态失败"); }
  }

  useEffect(() => { if (activeView === "system" && permissions.manageSystem) loadSystem(); }, [activeView]);

  async function createBackup() {
    try { await api.createBackup(); setToast("备份已完成"); await loadSystem(); } catch (error) { handleError(error, "备份失败"); }
  }

  async function downloadDiagnostics() {
    try {
      const blob = await api.getDiagnostics();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = "meeting-room-diagnostic.json"; anchor.click();
      URL.revokeObjectURL(url);
      setToast("脱敏诊断信息已导出");
    } catch (error) { handleError(error, "导出诊断失败"); }
  }

  function renderSystem() {
    const healthy = system?.health === "healthy";
    const services = [
      { id: "api", label: "预约服务", value: system?.apiStatus || "正在检查", tone: system?.apiStatus === "online" ? "normal" : "warning" },
      { id: "display", label: "公开大屏", value: system?.displayStatus || "正在检查", tone: system?.displayStatus === "online" ? "normal" : "warning" },
    ];
    return <main className="main-canvas system-canvas"><header className="page-header system-header"><div><h1>系统状态</h1><p>查看本机服务和局域网连接情况</p></div><button className="system-export-button" onClick={downloadDiagnostics}><DownloadSimple size={20} /><span>导出诊断信息</span></button></header>
      <div className="system-status-content"><section className={"system-health-summary " + (system ? healthy ? "normal" : "warning" : "normal")}><span className="system-health-dot" /><div><h2>{system ? healthy ? "系统运行正常" : "系统需要注意" : "正在检查系统"}</h2><p>最后检查：刚刚</p></div></section>
        <section className="system-status-group"><h2>运行环境</h2><div className="system-status-list">{[["程序版本", system?.productVersion || bootstrap.productVersion], ["数据库版本", system?.databaseVersion || "—"], ["局域网地址", system?.lanAddress || "—"]].map(([label, value]) => <div className="system-status-row" key={label}><span>{label}</span><strong>{value}</strong><span /></div>)}<button className="system-status-row system-backup-row" onClick={() => setDrawer({ type: "backup" })}><span>最近备份</span><strong>{system?.lastBackupAt || "尚无备份"}</strong><CaretRight size={17} /></button></div></section>
        <section className="system-status-group system-service-group"><h2>服务连接</h2><div className="system-status-list">{services.map((service) => <div className={"system-status-row system-service-row " + (service.status || service.tone || "")} key={service.id || service.label}><span>{service.label}</span><strong><i />{service.value || service.status}</strong><span /></div>)}</div></section>
      </div>
    </main>;
  }

  async function savePreferences(event) {
    event.preventDefault();
    try {
      const saved = await api.updatePreferences(preferencesDraft);
      setBootstrap((current) => ({
        ...current,
        currentUser: saved.profile || current.currentUser,
        preferences: saved.preferences || saved,
        personalTags: saved.personalTags || current.personalTags,
      }));
      setPreferencesDraft(saved.preferences || saved);
      setToast("个人设置已保存");
    } catch (error) { handleError(error, "保存个人设置失败"); }
  }

  function renderSettings() {
    const draft = preferencesDraft || {};
    const update = (field, value) => setPreferencesDraft((current) => ({ ...current, [field]: value }));
    return <main className="main-canvas settings-canvas"><header className="page-header settings-header"><div><h1>个人设置</h1><p>管理你的工作资料与预约偏好</p></div><div className="settings-header-actions"><button className="settings-logout-button" type="button" onClick={logout}>退出登录</button><button className="settings-save-button" type="submit" form="personal-settings-form">保存更改</button></div></header>
      <form id="personal-settings-form" className="settings-layout" onSubmit={savePreferences}>
        <section className="settings-section settings-profile-section"><h2>个人资料</h2><div className="settings-field-grid"><label className="settings-field"><span>姓名</span><input value={draft.name ?? currentUser.name ?? ""} onChange={(event) => update("name", event.target.value)} /></label><label className="settings-field"><span>所属部门</span><input value={draft.department ?? currentUser.department ?? ""} onChange={(event) => update("department", event.target.value)} /></label></div></section>
        <section className="settings-section settings-preferences-section"><h2>预约偏好</h2><div className="settings-choice-list"><label className="settings-choice-row"><span>默认预约时长</span><span className="settings-select-wrap"><select value={draft.defaultDuration || 60} onChange={(event) => update("defaultDuration", Number(event.target.value))}>{DURATION_STEPS.map((value) => <option value={value} key={value}>{value}分钟</option>)}</select><CaretRight size={18} /></span></label><label className="settings-choice-row"><span>默认笔录室</span><span className="settings-select-wrap"><select value={draft.defaultRoomId || ""} onChange={(event) => update("defaultRoomId", event.target.value)}><option value="">不指定</option>{activeRooms.map((room) => <option value={room.id} key={room.id}>{room.name}</option>)}</select><CaretRight size={18} /></span></label></div></section>
        <section className="settings-section settings-notifications-section"><h2>通知</h2><div className="settings-notification-list"><label className="settings-notification-row"><span><strong>预约变更</strong><small>页面打开时，修改或取消预约会通知我</small></span><input className="settings-switch" type="checkbox" checked={Boolean(draft.bookingChangeNotifications)} onChange={(event) => update("bookingChangeNotifications", event.target.checked)} /></label><label className="settings-notification-row"><span><strong>预约提醒</strong><small>页面打开时，开始前30分钟提醒我</small></span><input className="settings-switch" type="checkbox" checked={Boolean(draft.bookingReminder)} onChange={(event) => update("bookingReminder", event.target.checked)} /></label></div></section>
      </form>
    </main>;
  }

  function renderUnauthorized() {
    return <main className="main-canvas unauthorized-canvas"><header className="page-header unauthorized-header"><div><h1>受限页面</h1><p>当前账户权限不足</p></div></header><section className="unauthorized-state" role="alert"><span className="unauthorized-icon"><LockSimple size={36} /></span><h2>无权限访问此页面</h2><p>{unauthorizedMessage || "当前账户没有访问该页面的权限。"}</p><button type="button" onClick={() => navigate("mine")}>返回我的预约</button></section></main>;
  }

  function drawerHeading() {
    return { create: "新建预约", edit: "修改预约", details: "预约详情", cancel: "取消预约", "slot-conflict": "时段已被占用", "room-create": "添加笔录室", "room-edit": "管理笔录室", "user-create": "新建用户", "user-edit": "编辑用户", "user-reset": "重置密码", backup: "最近备份" }[drawer?.type] || "";
  }

  function renderDrawer() {
    if (!drawer) return null;
    const enabledAdminCount = users.filter((user) => user.role === "admin" && user.enabled !== false).length;
    const lastAdminProtected = drawer.type === "user-edit"
      && drawer.user.role === "admin"
      && drawer.user.enabled !== false
      && enabledAdminCount <= 1;
    if (drawer.type === "create" || drawer.type === "edit") return <BookingForm form={bookingForm} setForm={setBookingForm} errors={bookingErrors} rooms={rooms} tags={tags} settings={settings} busy={saveState === "saving"} failure={saveState === "failed"} conflict={conflict} onSubmit={saveBooking} onDismissFailure={() => setSaveState("idle")} onContinueDraft={() => setConflict(null)} onUseLatest={() => {
      const latest = conflict?.current;
      if (!latest) return;
      setBookingForm({ roomId: latest.roomId, date: latest.date, start: latest.start, duration: durationFromRange(latest.start, latest.end), partyName: latest.partyName, caseNumber: latest.caseNumber, purpose: latest.purpose, notes: latest.notes || "", tagId: latest.tagId });
      setDrawer({ type: "edit", booking: latest }); setConflict(null);
    }} />;
    if (drawer.type === "slot-conflict") return <div className="booking-conflict-panel"><WarningCircle size={30} /><h2>预约刚被别人占用</h2><p>{bookingForm.roomId && rooms.find((room) => room.id === bookingForm.roomId)?.name} · {bookingForm.start}–{endFromDuration(bookingForm.start, bookingForm.duration)}</p><div className="conflict-draft-summary"><strong>你填写的内容已保留</strong><span>预约对象、案号、标签和备注不会丢失。</span></div><button className="primary-button" onClick={() => { setPreservedDraft(bookingForm); setDrawer(null); setToast("草稿已保留，请选择新的空白时段"); }}>返回日历重新选择</button><button className="secondary-button" onClick={() => { setPreservedDraft(bookingForm); setDrawer(null); }}>保留草稿并关闭</button><button className="quiet-button" onClick={loadCalendar}>重新检查这个时段</button></div>;
    if (drawer.type === "details") {
      const booking = drawer.booking;
      return <BookingDetails booking={booking} tag={tagFor(booking)} canManage={!drawer.readOnly && booking.canEdit !== false && canManageBooking({ role, currentUserId: currentUser.id, booking }) && booking.status !== "cancelled"} onEdit={() => openEdit(booking)} onCancel={() => setDrawer({ type: "cancel", booking })} onClose={() => setDrawer(null)} />;
    }
    if (drawer.type === "cancel") return <div className="cancel-booking-confirm"><WarningCircle size={32} /><h2>确认取消这场预约？</h2><p>{drawer.booking.roomName} · {drawer.booking.date} · {drawer.booking.start}–{drawer.booking.end}</p><button className="cancel-booking-button" disabled={saveState === "saving"} onClick={cancelBooking}>{saveState === "saving" ? "正在取消…" : "确认取消预约"}</button><button className="secondary-button" onClick={() => setDrawer({ type: "details", booking: drawer.booking })}>返回</button></div>;
    if (drawer.type === "room-create" || drawer.type === "room-edit") return <form className="room-form" onSubmit={saveRoom}><label><span>笔录室名称</span><input data-initial-focus value={drawer.form.name} onChange={(event) => setDrawer((current) => ({ ...current, form: { ...current.form, name: event.target.value } }))} /></label><label><span>排序</span><input type="number" min="1" value={drawer.form.sortOrder} onChange={(event) => setDrawer((current) => ({ ...current, form: { ...current.form, sortOrder: Number(event.target.value) } }))} /></label><label className="settings-notification-row"><span><strong>允许预约</strong><small>停用后保留既有预约，但不再出现在新预约日历</small></span><input className="settings-switch" type="checkbox" checked={drawer.form.isActive} onChange={(event) => setDrawer((current) => ({ ...current, form: { ...current.form, isActive: event.target.checked } }))} /></label>{drawer.type === "room-edit" && <button className="password-reset-link" type="button" onClick={deleteRoom}><X size={18} />删除笔录室</button>}<div className="drawer-fixed-footer"><button className="primary-button" type="submit">保存笔录室</button></div></form>;
    if (drawer.type === "user-create" || drawer.type === "user-edit") return <form className="user-editor-form" onSubmit={saveUser}><label><span>姓名</span><input data-initial-focus value={drawer.form.name} onChange={(event) => setDrawer((current) => ({ ...current, form: { ...current.form, name: event.target.value } }))} /></label><label><span>用户名</span><input disabled={drawer.type === "user-edit"} autoComplete="username" value={drawer.form.username} onChange={(event) => setDrawer((current) => ({ ...current, form: { ...current.form, username: event.target.value } }))} /></label><label><span>所属部门</span><input value={drawer.form.department} onChange={(event) => setDrawer((current) => ({ ...current, form: { ...current.form, department: event.target.value } }))} /></label><label><span>角色</span><select disabled={lastAdminProtected} value={drawer.form.role} onChange={(event) => setDrawer((current) => ({ ...current, form: { ...current.form, role: event.target.value } }))}><option value="employee">普通员工</option><option value="admin">管理员</option></select></label>{drawer.type === "user-create" && <label><span>初始密码</span><input type="password" autoComplete="new-password" value={drawer.form.password} onChange={(event) => setDrawer((current) => ({ ...current, form: { ...current.form, password: event.target.value } }))} /></label>}<label className="settings-notification-row"><span><strong>启用账号</strong></span><input className="settings-switch" type="checkbox" disabled={lastAdminProtected} checked={drawer.form.enabled} onChange={(event) => setDrawer((current) => ({ ...current, form: { ...current.form, enabled: event.target.checked } }))} /></label>{lastAdminProtected && <p className="user-protection-note" role="note">必须至少保留一名启用的管理员。请先创建或启用另一名管理员。</p>}{drawer.type === "user-edit" && <button className="password-reset-link" type="button" onClick={() => setDrawer({ type: "user-reset", user: drawer.user, form: { password: "" } })}><Key size={18} />重置密码</button>}<div className="drawer-fixed-footer"><button className="primary-button" type="submit">保存修改</button></div></form>;
    if (drawer.type === "user-reset") return <form className="password-reset-form" onSubmit={resetPassword}><div className="password-reset-copy"><span className="password-reset-icon"><Key size={24} /></span><h2>为 {drawer.user.name} 设置新密码</h2></div><label><span>新密码</span><input data-initial-focus type="password" autoComplete="new-password" value={drawer.form.password} onChange={(event) => setDrawer((current) => ({ ...current, form: { password: event.target.value } }))} /></label><div className="password-reset-actions"><button className="primary-button" type="submit">确认重置</button></div></form>;
    if (drawer.type === "backup") return <div className="system-backup-detail"><Database size={30} /><h2>最近备份</h2><p>{system?.lastBackupAt || "尚未创建备份"}</p><p>备份仅包含本机 V2 数据；导出诊断不会包含预约内容或凭据。</p><button className="primary-button" onClick={createBackup}>立即备份</button></div>;
    return null;
  }

  async function logout() {
    try { await api.logout(); } catch { /* session may already be gone */ }
    onLoggedOut();
  }

  return <div className={"app-shell " + (drawer ? "drawer-open" : "")}>
    <div ref={mainRef} className="app-main-region">
      <aside className="icon-rail" aria-label="主导航"><button className="brand-mark tooltip-right" data-tooltip="回到我的预约" aria-label="回到我的预约" onClick={() => navigate("mine")}><Asterisk size={34} /></button><nav className="rail-nav">{NAV_ITEMS.filter((item) => !item.permission || permissions[item.permission]).map(({ id, label, Icon }) => <button className={"rail-button tooltip-right " + (activeView === id ? "active" : "")} data-tooltip={label} aria-label={label} aria-current={activeView === id ? "page" : undefined} key={id} onClick={() => navigate(id)}><Icon size={25} /></button>)}</nav><button className={"avatar-button tooltip-right " + (activeView === "settings" ? "active" : "")} data-tooltip={itemName(currentUser) + " · 个人设置"} aria-label={itemName(currentUser) + "，个人设置"} onClick={() => navigate("settings")}><UserCircle size={42} weight="thin" /></button></aside>
      {activeView === "mine" && renderMine()}{activeView === "calendar" && renderCalendar()}{activeView === "history" && renderHistory()}{activeView === "rooms" && permissions.manageRooms && renderRooms()}{activeView === "users" && permissions.manageUsers && renderUsers()}{activeView === "system" && permissions.manageSystem && renderSystem()}{activeView === "settings" && renderSettings()}{activeView === "unauthorized" && renderUnauthorized()}
    </div>
    <Drawer open={Boolean(drawer)} heading={drawerHeading()} onClose={() => setDrawer(null)} className={drawer?.type?.startsWith("user") ? "user-drawer" : ""}>{renderDrawer()}</Drawer>
    {toast && <div className="toast visible" role="status" aria-live="polite"><CheckCircle size={20} weight="fill" /><span>{toast}</span><button aria-label="关闭提示" onClick={() => setToast("")}><X size={16} /></button></div>}
    {dueReminder && <div className="toast visible reminder-toast" role="status"><ClockCounterClockwise size={20} /><span>{reminderDisplayMessage(dueReminder)}</span><button onClick={acknowledgeReminder}>知道了</button></div>}
    {sessionExpired && <SessionExpired onRecovered={async () => { setSessionExpired(false); await loadBootstrap(); await Promise.all([loadCalendar(), loadUpcoming(), loadHistory()]); }} />}
  </div>;
}

export function App() {
  const publicRoute = window.location.pathname === "/display" || window.location.pathname.endsWith("/display/");
  const [phase, setPhase] = useState(publicRoute ? "public" : "loading");
  const [session, setSession] = useState(null);
  const [fatal, setFatal] = useState("");

  const start = useCallback(async () => {
    if (publicRoute) return;
    setPhase("loading");
    setFatal("");
    try {
      const value = await api.getSession();
      setSession(value);
      if (!value.setupComplete) setPhase("setup");
      else if (!value.authenticated) setPhase("login");
      else setPhase("app");
    } catch (error) {
      setFatal(error.message || "无法连接系统服务");
      setPhase("fatal");
    }
  }, [publicRoute]);

  useEffect(() => { start(); }, [start]);
  if (phase === "public") return <PublicDisplay />;
  if (phase === "loading") return <LoadingScreen />;
  if (phase === "fatal") return <FatalScreen error={fatal} onRetry={start} />;
  if (phase === "setup") return <Setup onComplete={start} />;
  if (phase === "login") return <Login onAuthenticated={async () => { const value = await api.getSession(); setSession(value); setPhase("app"); }} />;
  return <MainApp session={session} onLoggedOut={() => { setSession(null); setPhase("login"); }} />;
}
