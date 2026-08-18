import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  ArrowClockwise,
  ArrowRight,
  ArrowsLeftRight,
  Asterisk,
  CalendarBlank,
  ChartBar,
  CaretDown,
  CaretLeft,
  CaretRight,
  CheckCircle,
  Circle,
  CircleNotch,
  Clock,
  ClockCounterClockwise,
  CopySimple,
  Database,
  DoorOpen,
  DownloadSimple,
  Eye,
  EyeSlash,
  FunnelSimple,
  Info,
  Key,
  LockSimple,
  MagnifyingGlass,
  PencilSimple,
  Plus,
  Pulse,
  ShieldCheck,
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
import { readAuthenticatedContext, reauthenticateContext, scopedAppKey } from "./auth-flow.js";
import {
  clearSessionBookingDraft,
  consumeSessionBookingDraft,
  SessionIsolationBoundary,
  writeSessionBookingDraft,
} from "./session-isolation.js";
import { runSetupRestartTransition, SetupRestartStatus } from "./setup-restart.js";
import {
  adminApiFieldErrors,
  validatePasswordReset,
  validateRoomAdminForm,
  validateSystemSettingsForm,
  validateUserAdminForm,
} from "./features/admin/validation.js";
import { RoomAdminForm, RoomDeleteBlocked, RoomDeleteConfirmation, UserAdminForm } from "./features/admin/AdminForms.jsx";
import { PersonalCenter } from "./features/profile/PersonalCenter.jsx";
import { DataCenter } from "./features/reports/DataCenter.jsx";
import { readUiPreferences, writeUiPreferences } from "./features/profile/ui-preferences.js";
import { renderReminderTemplate } from "./features/reminders/reminder-template.js";
import { playArrivalChime } from "./features/reminders/arrival-chime.js";
import { buildTagSectionPayload } from "./features/tags/tag-drafts.js";
import {
  dateLabel,
  formatLocalDateTime,
  itemName,
  monthKey,
  normalizeTag,
  parseDate,
  relativeDayLabel,
  reservationEventSummary,
  reservationStatusLabel,
  tagStyle,
} from "./ui/presentation.js";
import {
  arrivalReminderText,
  bookingCountdownMinutes,
  bookingTagContext,
  bookingPayload,
  calendarFocusTarget,
  calendarTimeSlots,
  calendarTimeLineOffset,
  canManageBooking,
  clampDurationToWorkday,
  dateKey,
  defaultBookingTagId,
  durationFromRange,
  endFromDuration,
  findFirstAvailableStart,
  generateTimeSlots,
  hasBookingStarted,
  isWithinWorkingHours,
  isDrawerAllowed,
  maximumAvailableDuration,
  noticeDiffRows,
  noticeIdentitySummary,
  overlaps,
  mapSetupFieldErrors,
  projectServerClock,
  rebaseBookingEdit,
  reservationConflictDifferences,
  reservationEventLabel,
  setupStepForField,
  shiftDate,
  shiftDateByYears,
  userFacingError,
  validateAuthenticatedContext,
  validateBookingForm,
  validateSetupUsername,
} from "./domain.js";

const DURATION_STEPS = [30, 60, 90, 120, 150, 180];
const NAV_ITEMS = [
  { id: "calendar", label: "预约日历", Icon: CalendarBlank },
  { id: "mine", label: "我的预约", Icon: User },
  { id: "handovers", label: "工作交接", Icon: ArrowsLeftRight },
  { id: "history", label: "预约记录", Icon: ClockCounterClockwise },
  { id: "data-center", label: "数据中心", Icon: ChartBar, permission: "viewReports" },
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

function ToastIcon({ tone }) {
  const Icon = tone === "success" ? CheckCircle : tone === "error" ? WarningCircle : Info;
  return <Icon size={20} weight="fill" aria-hidden="true" />;
}

function ChangeNoticeItem({ item, busy, initialFocus, showMeta, showActions, onView, onAcknowledge }) {
  const identity = noticeIdentitySummary(item);
  const diffRows = noticeDiffRows(item.diffs);
  const cancelled = item.changeType === "cancelled";
  return <li className="notice-modal-item">
    {showMeta && <p className="notice-item-meta">{item.actorName || "其他用户"} · {formatLocalDateTime(item.occurredAt)}</p>}
    <h3>{cancelled ? "你的预约已被取消" : <>你的预约发生了 <em>{diffRows.length}</em> 项变更</>}</h3>
    {identity
      ? <dl className="notice-item-identity" aria-label="原预约信息">
        <div><dt>当事人</dt><dd>{identity.partyName}</dd></div>
        <div><dt>事项</dt><dd>{identity.purpose}</dd></div>
        <div className="notice-identity-schedule"><dt>原预约</dt><dd>{identity.originalSchedule || "时间与笔录室暂不可用"}</dd></div>
      </dl>
      : <p className="notice-identity-unavailable">原预约信息暂不可用，请查看预约确认详情。</p>}
    {cancelled
      ? <p className="notice-item-cancelled">该场预约已取消，原时段不再保留。</p>
      : <section className="notice-item-changes" aria-label="具体调整">
        <h4>具体调整</h4>
        <dl className="notice-item-diffs">{diffRows.map((row) => <div key={row.key}>
          <Circle className="notice-diff-marker" size={7} weight="fill" aria-hidden="true" />
          <dt>{row.label}</dt>
          <dd className="notice-diff-before">{row.from || "（空）"}</dd>
          <ArrowRight className="notice-diff-arrow" size={15} aria-hidden="true" />
          <dd className="notice-diff-after">{row.to || "（空）"}</dd>
        </div>)}</dl>
      </section>}
    {showActions && <div className="notice-item-actions">
      <button type="button" disabled={busy} onClick={() => onView(item)}>查看预约</button>
      <button type="button" className="notice-item-ack" data-initial-focus={initialFocus || undefined} disabled={busy} onClick={() => onAcknowledge([item])}>{busy ? "正在确认…" : "我知道了"}</button>
    </div>}
  </li>;
}

function auditActionLabel(action) {
  const labels = {
    "auth.login_succeeded": "登录成功", "auth.login_failed": "登录失败", "auth.logout": "退出登录", "auth.session_expired": "会话已过期",
    "setup.completed": "首次设置完成", "settings.updated": "修改工作时间", "room.created": "创建笔录室", "room.updated": "修改笔录室", "room.deleted": "删除笔录室",
    "user.created": "创建用户", "user.updated": "修改用户", "user.password_reset": "重置用户密码",
    "preferences.updated": "修改个人设置", "tags.global_updated": "修改单位标签",
    "backup.requested": "请求备份", "backup.succeeded": "备份完成", "backup.failed": "备份失败",
    "token.created": "创建接口令牌", "token.revoked": "撤销接口令牌",
    "report.csv_exported": "导出办件明细",
  };
  return labels[action] || "其他安全操作";
}

function auditTargetTypeLabel(targetType) {
  return {
    room: "笔录室",
    user: "用户",
    session: "登录会话",
    system: "系统",
    tag: "单位标签",
    api_token: "接口令牌",
    report: "数据报表",
  }[targetType] || "系统对象";
}

function auditOutcomeLabel(value) {
  return {
    succeeded: "成功",
    failed: "失败",
    requested: "已请求",
    authenticated: "身份已验证",
    user_requested: "用户主动操作",
    invalid_credentials: "账号或密码错误",
    account_disabled: "账户已停用",
    rate_limited: "尝试过于频繁",
    idle_timeout: "空闲超时",
    absolute_timeout: "登录时限已到",
  }[value] || "已记录";
}

const AUDIT_ACTION_OPTIONS = [
  ["auth.login_succeeded", "登录成功"], ["auth.login_failed", "登录失败"], ["auth.logout", "退出登录"], ["auth.session_expired", "会话已过期"],
  ["setup.completed", "首次设置完成"], ["settings.updated", "修改工作时间"], ["room.created", "创建笔录室"], ["room.updated", "修改笔录室"], ["room.deleted", "删除笔录室"],
  ["user.created", "创建用户"], ["user.updated", "修改用户"], ["user.password_reset", "重置用户密码"], ["preferences.updated", "修改个人设置"],
  ["tags.global_updated", "修改单位标签"], ["backup.requested", "请求备份"], ["backup.succeeded", "备份完成"], ["backup.failed", "备份失败"],
  ["token.created", "创建接口令牌"], ["token.revoked", "撤销接口令牌"],
  ["report.csv_exported", "导出办件明细"],
];
const AUDIT_OUTCOME_OPTIONS = [
  ["succeeded", "成功"], ["failed", "失败"], ["requested", "已请求"], ["authenticated", "身份已验证"],
  ["user_requested", "用户主动操作"], ["invalid_credentials", "账号或密码错误"], ["account_disabled", "账户已停用"],
  ["rate_limited", "尝试过于频繁"], ["idle_timeout", "空闲超时"], ["absolute_timeout", "登录时限已到"],
];
const AUDIT_TARGET_OPTIONS = [["room", "笔录室"], ["user", "用户"], ["session", "登录会话"], ["system", "系统"], ["tag", "单位标签"], ["api_token", "接口令牌"], ["report", "数据报表"]];

async function copyText(value) {
  if (!value) throw new Error("没有可复制的内容");
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return;
    } catch {
      // Trusted LAN HTTP may not expose the async clipboard API. Use the
      // browser's selection-based fallback before asking for manual copying.
    }
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  try {
    textarea.select();
    textarea.setSelectionRange(0, value.length);
    if (!document.execCommand("copy")) throw new Error("浏览器拒绝复制");
  } finally {
    textarea.remove();
  }
}

function toApiTimestamp(value) {
  if (!value) return "";
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.getTime()) ? "" : timestamp.toISOString();
}

async function fetchAllReservations(dateFrom, dateTo = dateFrom, { signal } = {}) {
  const items = [];
  const seenCursors = new Set();
  let cursor = "";
  do {
    const page = await api.getReservations(dateFrom, dateTo, { pageSize: 100, cursor, signal });
    items.push(...unwrapItems(page));
    cursor = page?.nextCursor || "";
    if (cursor && seenCursors.has(cursor)) throw new Error("预约分页游标重复");
    if (cursor) seenCursors.add(cursor);
  } while (cursor);
  return items;
}

function useDocumentTitle(title) {
  useEffect(() => {
    document.title = title + " · 会议室预约系统";
  }, [title]);
}

function useFocusTrap(ref, active, onClose, dismissable = true, focusKey = "", backgroundRef = null) {
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);
  useLayoutEffect(() => {
    if (!active || !ref.current) return undefined;
    const node = ref.current;
    const previous = document.activeElement;
    const background = backgroundRef?.current;
    const selector = "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])";
    const first = node.querySelector("[data-initial-focus]") || node.querySelector(selector);
    background?.setAttribute("inert", "");
    background?.setAttribute("aria-hidden", "true");
    const focusFrame = window.requestAnimationFrame(() => first?.focus({ preventScroll: true }));
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
      window.cancelAnimationFrame(focusFrame);
      node.removeEventListener("keydown", handleKey);
      background?.removeAttribute("inert");
      background?.removeAttribute("aria-hidden");
      if (previous?.isConnected) previous.focus?.({ preventScroll: true });
    };
  }, [active, backgroundRef, dismissable, focusKey, ref]);
}

function useDismissiblePopover(active, onClose) {
  const triggerRef = useRef(null);
  const popoverRef = useRef(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);
  useEffect(() => {
    if (!active) return undefined;
    const handlePointerDown = (event) => {
      if (triggerRef.current?.contains(event.target) || popoverRef.current?.contains(event.target)) return;
      onCloseRef.current?.();
    };
    const handleKeyDown = (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onCloseRef.current?.();
      window.requestAnimationFrame(() => triggerRef.current?.focus({ preventScroll: true }));
    };
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [active]);
  const closeAndRestoreFocus = useCallback(() => {
    onCloseRef.current?.();
    window.requestAnimationFrame(() => triggerRef.current?.focus({ preventScroll: true }));
  }, []);
  return { triggerRef, popoverRef, closeAndRestoreFocus };
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
        <img src="/assets/login/schedule-portal.svg" alt="" />
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
        <img src="/assets/login/schedule-portal.svg" alt="" />
      </figure>
    </main>
  );
}

function RecoveryScreen({ error, onRetry }) {
  useDocumentTitle("系统恢复");
  return (
    <main className="login-page recovery-page">
      <section className="login-panel recovery-panel" role="alert" aria-labelledby="recovery-heading">
        <span className="recovery-mark" aria-hidden="true"><Database size={28} /></span>
        <div className="login-title-row">
          <span className="login-title-dot" aria-hidden="true" />
          <h1 id="recovery-heading">系统需要恢复</h1>
        </div>
        <p className="recovery-copy">系统检测到本机数据需要由管理员恢复，已停止业务写入。请在服务器电脑运行“⑥ 从备份恢复”；若仍失败，请把恢复代码、请求编号和 _程序文件\logs 交给维护人员。</p>
        {(error?.recoveryCode || error?.requestId) && <dl className="recovery-reference">{error.recoveryCode && <div><dt>恢复代码</dt><dd>{error.recoveryCode}</dd></div>}{error.requestId && <div><dt>请求编号</dt><dd>{error.requestId}</dd></div>}</dl>}
        <button className="login-submit" type="button" onClick={onRetry}><ArrowClockwise size={18} />重新检查</button>
      </section>
      <figure className="login-illustration" aria-hidden="true"><img src="/assets/login/schedule-portal.svg" alt="" /></figure>
    </main>
  );
}

function Login({ onAuthenticated, onRecovery }) {
  const [credentials, setCredentials] = useState({ username: "", password: "" });
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [visible, setVisible] = useState(false);
  const usernameRef = useRef(null);
  const passwordRef = useRef(null);
  useDocumentTitle("登录");

  useEffect(() => {
    if (busy) return;
    const target = errors.username ? usernameRef.current : errors.password ? passwordRef.current : null;
    target?.focus({ preventScroll: true });
  }, [busy, errors.password, errors.username]);

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
      if (error.code === "SYSTEM_RECOVERY_REQUIRED") onRecovery(error);
      else if (error.code === "ACCOUNT_DISABLED") setMessage("该账号已停用，请联系管理员。");
      else if (error.code === "INVALID_CREDENTIALS" || error.status === 401) setErrors({ password: "用户名或密码不正确，请重新输入。" });
      else setMessage(userFacingError(error, "登录失败，请稍后重试"));
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
            <input ref={usernameRef} id="login-username" autoComplete="username" value={credentials.username} disabled={busy}
              aria-invalid={Boolean(errors.username)} aria-describedby={errors.username ? "login-username-error" : undefined} onChange={(event) => update("username", event.target.value)} />
          </label>
          {errors.username && <p id="login-username-error" className="login-field-error" role="alert"><WarningCircle size={18} />{errors.username}</p>}
          <label className="login-field login-password-field" htmlFor="login-password">
            <span>密码</span>
            <span className="login-password-control">
              <input ref={passwordRef} id="login-password" type={visible ? "text" : "password"} autoComplete="current-password"
                value={credentials.password} disabled={busy} className={errors.password ? "invalid" : ""}
                aria-invalid={Boolean(errors.password)} aria-describedby={errors.password ? "login-password-error" : undefined} onChange={(event) => update("password", event.target.value)} />
              <button className="login-password-toggle" type="button" aria-label={visible ? "隐藏密码" : "显示密码"}
                onClick={() => setVisible((current) => !current)} disabled={busy}>
                {visible ? <EyeSlash size={21} /> : <Eye size={21} />}
              </button>
            </span>
          </label>
          <div className="login-feedback-slot" aria-live="polite">
            {errors.password && <p id="login-password-error" className="login-field-error" role="alert"><WarningCircle size={18} />{errors.password}</p>}
            {message && <p className="login-account-feedback" role="alert"><WarningCircle size={18} />{message}</p>}
          </div>
          <button className="login-submit" type="submit" disabled={busy}>
            {busy ? <><CircleNotch className="login-spinner spin" size={20} />正在登录…</> : "登录"}
          </button>
        </form>
      </section>
      <figure className="login-illustration" aria-hidden="true">
        <img src="/assets/login/schedule-portal.svg" alt="" />
      </figure>
    </main>
  );
}

function Setup({ onComplete, onRecovery }) {
  const [step, setStep] = useState(0);
  const [admin, setAdmin] = useState({ username: "", name: "", department: "", password: "", confirmPassword: "" });
  const [rooms, setRooms] = useState([{ name: "笔录室 1" }]);
  const [hours, setHours] = useState({ start: "08:30", end: "17:30" });
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const [complete, setComplete] = useState(false);
  const [restartState, setRestartState] = useState("idle");
  const [errorFocusToken, setErrorFocusToken] = useState(0);
  const stageRef = useRef(null);
  const steps = ["安全说明", "管理员", "笔录室", "工作时间", "完成"];
  useDocumentTitle("首次配置");

  useEffect(() => {
    if (!complete || restartState !== "waiting") return undefined;
    let cancelled = false;
    runSetupRestartTransition({
      probe: api.getServiceHealth,
      onState: (state) => { if (!cancelled) setRestartState(state); },
      onReady: () => { if (!cancelled) onComplete(); },
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [complete, onComplete, restartState]);

  useEffect(() => {
    if (!errorFocusToken) return undefined;
    const frame = window.requestAnimationFrame(() => {
      stageRef.current?.querySelector('[aria-invalid="true"]')?.focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [errorFocusToken, step]);

  function clearErrors(...fields) {
    setErrors((current) => {
      const next = { ...current };
      fields.forEach((field) => delete next[field]);
      return next;
    });
  }

  function validate() {
    const next = Object.fromEntries(Object.entries(errors).filter(([field]) => setupStepForField(field) === step));
    if (step === 1) {
      const usernameError = validateSetupUsername(admin.username);
      if (usernameError) next.username = usernameError;
      if (!admin.name.trim()) next.name = "请输入姓名";
      if (!admin.department.trim()) next.department = "请输入部门";
      if (admin.password.length < 8) next.password = "密码至少需要 8 个字符";
      if (admin.password !== admin.confirmPassword) next.confirmPassword = "两次输入的密码不一致";
    }
    if (step === 2) {
      rooms.forEach((room, index) => {
        if (!room.name.trim()) next[`rooms.${index}.name`] = "请输入笔录室名称";
      });
      if (!rooms.length) next.rooms = "请至少填写一个笔录室";
    }
    if (step === 3) {
      if (!/^\d{2}:(00|30)$/.test(hours.start)) next.workStart = "开始时间必须按 30 分钟对齐";
      if (!/^\d{2}:(00|30)$/.test(hours.end)) next.workEnd = "结束时间必须按 30 分钟对齐";
      if (!next.workStart && !next.workEnd && hours.end <= hours.start) next.workEnd = "结束时间必须晚于开始时间";
    }
    setErrors((current) => ({
      ...Object.fromEntries(Object.entries(current).filter(([field]) => setupStepForField(field) !== step && field !== "submit")),
      ...next,
    }));
    if (Object.keys(next).length) setErrorFocusToken((current) => current + 1);
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
        rooms: rooms.map((room) => ({ name: room.name.trim() })),
        workStart: hours.start,
        workEnd: hours.end,
      });
      setComplete(true);
      setRestartState("waiting");
    } catch (error) {
      if (error.code === "SYSTEM_RECOVERY_REQUIRED") onRecovery(error);
      else {
        const mapped = mapSetupFieldErrors(error.fields, error.message);
        setErrors(mapped.errors);
        if (mapped.step !== null) setStep(mapped.step);
        setErrorFocusToken((current) => current + 1);
      }
    } finally {
      setBusy(false);
    }
  }

  function stage() {
    if (complete) return <SetupRestartStatus
      state={restartState}
      onRetry={() => setRestartState("waiting")}
      waitingIndicator={<CircleNotch className="spin" size={42} />}
      failureIndicator={<WarningCircle size={42} weight="thin" />}
    />;
    if (step === 0) return (
      <section className="setup-welcome" aria-labelledby="setup-welcome-heading">
        <ShieldCheck size={46} weight="thin" aria-hidden="true" />
        <h2 id="setup-welcome-heading">欢迎使用会议室预约系统</h2>
        <p>这是全新系统的首次设置。完成后，再使用创建的管理员账号登录。</p>
        <div className="setup-safety-list">
          <div><ShieldCheck size={22} aria-hidden="true" /><span><strong>请在服务器电脑上完成</strong><small>创建的第一个账号将拥有系统管理权限。</small></span></div>
          <div><Database size={22} aria-hidden="true" /><span><strong>建立全新的 V2 数据</strong><small>不会读取或迁移旧版本账号、预约或数据库。</small></span></div>
        </div>
        <button className="setup-primary-button setup-welcome-button" type="button" onClick={advance}>开始设置</button>
      </section>
    );
    if (step === 1) return (
      <div className="setup-admin-form">
        <div className="setup-admin-grid">
          {[
            ["username", "用户名", "username"],
            ["name", "姓名", "name"],
            ["department", "所属部门", "organization"],
          ].map(([field, label, autoComplete]) => (
            <label className="setup-field" key={field}>
              <span>{label}</span>
              <input type={field.includes("Password") || field === "password" ? "password" : "text"}
                autoComplete={autoComplete} value={admin[field]} aria-invalid={Boolean(errors[field])}
                aria-describedby={errors[field] ? `setup-${field}-error` : undefined}
                onChange={(event) => { setAdmin((current) => ({ ...current, [field]: event.target.value })); clearErrors(field, "submit"); }} />
              {errors[field] && <small className="setup-error" id={`setup-${field}-error`} role="alert">{errors[field]}</small>}
            </label>
          ))}
        </div>
        <div className="setup-password-section">
          <div className="setup-password-grid">
            {[
              ["password", "密码", "new-password"],
              ["confirmPassword", "确认密码", "new-password"],
            ].map(([field, label, autoComplete]) => (
              <label className="setup-field" key={field}>
                <span>{label}</span>
                <input type="password" autoComplete={autoComplete} value={admin[field]} aria-invalid={Boolean(errors[field])}
                  aria-describedby={errors[field] ? `setup-${field}-error` : undefined}
                  onChange={(event) => { setAdmin((current) => ({ ...current, [field]: event.target.value })); clearErrors(field, "submit"); }} />
                {errors[field] && <small className="setup-error" id={`setup-${field}-error`} role="alert">{errors[field]}</small>}
              </label>
            ))}
          </div>
          <p className="setup-security-hint"><ShieldCheck size={21} aria-hidden="true" />系统不会提供默认账号或密码文件</p>
        </div>
      </div>
    );
    if (step === 2) return (
      <div>
        <div className="setup-room-intro"><DoorOpen size={38} weight="thin" /><div><h2>添加笔录室</h2>
        <p>至少添加一间，之后可由管理员继续调整。</p>
        </div></div>
        <div className="setup-room-list">
          {rooms.map((room, index) => {
            const field = `rooms.${index}.name`;
            return <label className="setup-field" key={index}>
              <span>笔录室 {index + 1}</span>
              <span className="setup-room-control">
                <input aria-label={"笔录室 " + (index + 1)} value={room.name}
                  aria-invalid={Boolean(errors[field])} aria-describedby={errors[field] ? `setup-room-${index}-error` : undefined}
                  onChange={(event) => { setRooms((current) => current.map((item, itemIndex) => itemIndex === index ? { name: event.target.value } : item)); clearErrors(field, "rooms", "submit"); }} />
                {rooms.length > 1 && <button type="button" aria-label="移除笔录室" onClick={() => { setRooms((current) => current.filter((_, itemIndex) => itemIndex !== index)); clearErrors(field, "rooms", "submit"); }}><X size={18} /></button>}
              </span>
              {errors[field] && <small className="setup-error" id={`setup-room-${index}-error`} role="alert">{errors[field]}</small>}
            </label>;
          })}
          <button className="setup-add-room" type="button" onClick={() => setRooms((current) => [...current, { name: "" }])}><Plus size={18} />添加笔录室</button>
          {errors.rooms && <small className="setup-error setup-section-error" role="alert">{errors.rooms}</small>}
        </div>
      </div>
    );
    if (step === 3) return (
      <div className="setup-hours-form">
        <div className="setup-time-grid">
          <label className="setup-field"><span>开始时间</span><input type="time" step="1800" value={hours.start} aria-invalid={Boolean(errors.workStart)} aria-describedby={errors.workStart ? "setup-work-start-error" : undefined} onChange={(event) => { setHours((current) => ({ ...current, start: event.target.value })); clearErrors("workStart", "submit"); }} />{errors.workStart && <small className="setup-error" id="setup-work-start-error" role="alert">{errors.workStart}</small>}</label>
          <label className="setup-field"><span>结束时间</span><input type="time" step="1800" value={hours.end} aria-invalid={Boolean(errors.workEnd)} aria-describedby={errors.workEnd ? "setup-work-end-error" : undefined} onChange={(event) => { setHours((current) => ({ ...current, end: event.target.value })); clearErrors("workEnd", "submit"); }} />{errors.workEnd && <small className="setup-error" id="setup-work-end-error" role="alert">{errors.workEnd}</small>}</label>
        </div>
        <div className="setup-rule-list"><div><span>时间粒度</span><strong>30 分钟</strong></div><div><span>单次最长预约</span><strong>180 分钟</strong></div></div>
        <p className="setup-rule-note">可预约时段将按工作时间生成，完成设置后仍可由管理员查看系统状态。</p>
      </div>
    );
    return (
      <section className="setup-confirm" aria-labelledby="setup-confirm-heading">
        <h2 id="setup-confirm-heading">确认后将完成首次设置</h2>
        <div className="setup-confirm-list">
          <div><span>首个管理员</span><strong>{admin.name}</strong><small>{admin.username} · {admin.department}</small></div>
          <div><span>笔录室</span><strong>{rooms.filter((room) => room.name.trim()).map((room) => room.name).join("、")}</strong><small>共 {rooms.filter((room) => room.name.trim()).length} 间</small></div>
          <div><span>工作时间</span><strong>{hours.start}–{hours.end}</strong><small>30 分钟粒度 · 最长 180 分钟</small></div>
        </div>
        <p className="setup-security-hint"><ShieldCheck size={21} aria-hidden="true" />完成后服务将重启并切换为局域网模式</p>
      </section>
    );
  }

  return (
    <main className="setup-page">
      <aside className="setup-guide" aria-label="首次设置进度">
        <div className="setup-guide-copy"><p className="setup-eyebrow">首次设置 <span>·</span> {step + 1} / 5</p><h1>{complete ? "配置完成" : ["安全地开始使用", "创建首个管理员", "创建笔录室", "设置工作时间", "确认基础配置"][step]}</h1><p>{complete ? "系统已准备就绪" : "几分钟完成必要的基础配置"}</p></div>
        <ol className="setup-steps">
          {steps.map((label, index) => <li className={index === step && !complete ? "active" : index < step || complete ? "done" : ""} aria-current={index === step && !complete ? "step" : undefined} key={label}><span className="setup-step-number">{String(index + 1).padStart(2, "0")}</span><span className="setup-step-mark">{(index < step || complete) && <CheckCircle size={22} />}{index === step && !complete && <Asterisk size={20} />}</span><span>{label}</span></li>)}
        </ol>
      </aside>
      <div className="setup-workspace">
        <div className="setup-stage" ref={stageRef}>
          {stage()}
          {!complete && step > 0 && <div className="setup-actions">
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
  const [clockAnchor, setClockAnchor] = useState(null);
  const [clockTick, setClockTick] = useState(() => new Date().getTime());
  const [recoveryError, setRecoveryError] = useState(null);
  const payloadRef = useRef(null);
  const failureCountRef = useRef(0);
  const lastSuccessRef = useRef(0);
  useDocumentTitle("今日引导");

  const load = useCallback(async (manual = false) => {
    if (manual) setRefreshing(true);
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10000);
    try {
      const next = await api.getPublicDisplay(controller.signal);
      const receivedAt = new Date().getTime();
      const recovered = failureCountRef.current > 0;
      payloadRef.current = next;
      failureCountRef.current = 0;
      lastSuccessRef.current = receivedAt;
      setPayload(next);
      setClockAnchor({ serverDate: next.serverDate, serverTime: next.serverTime, receivedAt });
      setClockTick(receivedAt);
      setRecoveryError(null);
      setState(next.status === "online" ? "normal" : "stale");
      setMessage(recovered || manual ? "连接已恢复，数据已更新" : "");
    } catch (error) {
      if (error?.code === "SYSTEM_RECOVERY_REQUIRED") {
        setRecoveryError(error);
        return;
      }
      failureCountRef.current += 1;
      const age = lastSuccessRef.current ? new Date().getTime() - lastSuccessRef.current : Number.POSITIVE_INFINITY;
      const offline = !payloadRef.current || failureCountRef.current >= 3 || age >= 90000;
      setState(offline ? "offline" : "stale");
      setMessage(userFacingError(error, "无法连接局域网服务"));
    } finally {
      window.clearTimeout(timeout);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(() => load(), 30000);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      const now = new Date().getTime();
      setClockTick(now);
      if (!lastSuccessRef.current || !payloadRef.current) return;
      const age = now - lastSuccessRef.current;
      if (age >= 90000) setState("offline");
      else if (age >= 45000) setState("stale");
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  if (recoveryError) return <RecoveryScreen error={recoveryError} onRetry={() => { setRecoveryError(null); load(true); }} />;
  const clock = clockAnchor ? projectServerClock(clockAnchor, clockTick) : null;
  const date = clock?.date ? parseDate(clock.date) : null;
  const dateText = date ? dateLabel(date).replace(" · ", "  ") : "正在读取日期";
  const timeText = clock?.time || "--:--";
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
          {payload && payload.rooms.length === 0 && <div className="public-display-empty"><DoorOpen size={44} weight="thin" /><strong>当前暂无公开引导的笔录室</strong><span>请留意现场工作人员指引</span></div>}
        </section>
      </main>
      <footer className="public-display-footer"><p><SpeakerHigh size={24} />请留意屏幕引导，按提示前往对应笔录室</p><p>最后更新&nbsp; {updated}<span aria-hidden="true">·</span>姓名已脱敏</p></footer>
      {message && state === "normal" && <div className="public-display-recovery visible" role="status"><CheckCircle size={20} weight="fill" />{message}</div>}
    </div>
  );
}

function Drawer({ open, heading, onBack, onClose, children, className = "", backgroundRef = null }) {
  const ref = useRef(null);
  useFocusTrap(ref, open, onClose, true, heading, backgroundRef);
  return (
    <>
      <button className={"drawer-backdrop " + (open ? "visible" : "")} aria-label="关闭侧栏" aria-hidden={!open} tabIndex={-1} onClick={onClose} />
      <aside ref={ref} className={"booking-drawer " + (open ? "open " : "") + className} aria-hidden={!open} aria-label="操作侧栏" role="dialog" aria-modal={open || undefined}>
        {open && <><div className="drawer-topline"><span className="drawer-topline-leading">{onBack && <button className="drawer-back" type="button" aria-label="返回待处理预约" onClick={onBack}><CaretLeft size={19} /></button>}<span>{heading}</span></span><button className="drawer-close" aria-label="关闭" onClick={onClose}><X size={20} /></button></div>{children}</>}
      </aside>
    </>
  );
}

function SessionExpired({ onRecovered, onRecovery }) {
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
      const context = await reauthenticateContext(api, credentials);
      await onRecovered(context);
    } catch (caught) {
      if (caught.code === "SYSTEM_RECOVERY_REQUIRED") onRecovery(caught);
      else setError(userFacingError(caught, "重新登录失败，请核对账号后重试"));
    } finally {
      setBusy(false);
    }
  }
  return <div className="session-expired-layer"><section ref={ref} className="session-expired-dialog" role="dialog" aria-modal="true" aria-labelledby="session-expired-heading">
    <span className="session-expired-icon" aria-hidden="true"><LockSimple size={22} /></span>
    <h2 id="session-expired-heading">登录已过期</h2>
    <p>为保护账户安全，请重新登录。本标签页内的预约草稿已保留，使用同一账号重新登录后可以恢复。</p>
    {!editing ? <button type="button" data-initial-focus onClick={() => setEditing(true)}>重新登录</button> :
      <form className="session-reauth-form" onSubmit={submit}>
        <input data-initial-focus aria-label="用户名" autoComplete="username" placeholder="用户名" value={credentials.username} onChange={(event) => setCredentials((current) => ({ ...current, username: event.target.value }))} />
        <input aria-label="密码" type="password" autoComplete="current-password" placeholder="密码" value={credentials.password} onChange={(event) => setCredentials((current) => ({ ...current, password: event.target.value }))} />
        {error && <small role="alert">{error}</small>}
        <button type="submit" disabled={busy}>{busy ? "正在登录…" : "登录并继续"}</button>
      </form>}
  </section></div>;
}

function BookingForm({ form, setForm, errors, rooms, tags, settings, maximumDuration, editing, busy, failure, conflict, conflictCheck, onSubmit, onFieldChange, onDismissFailure, onUseLatest, onContinueDraft, onRecheckConflict }) {
  const roomName = rooms.find((room) => room.id === form.roomId)?.name || "请选择笔录室";
  const minimum = DURATION_STEPS[0];
  const scaleMaximum = DURATION_STEPS.at(-1);
  const maximum = Math.max(minimum, Math.min(scaleMaximum, Number(maximumDuration ?? scaleMaximum)));
  const progressFor = (value) => ((Number(value) - minimum) / (scaleMaximum - minimum)) * 100;
  const selectedProgress = progressFor(Math.min(maximum, Math.max(minimum, Number(form.duration))));
  const availableProgress = progressFor(maximum);
  useEffect(() => {
    if (Number(form.duration) <= maximum) return;
    setForm((current) => Number(current.duration) > maximum ? { ...current, duration: maximum } : current);
    onFieldChange?.("duration");
  }, [form.duration, maximum, onFieldChange, setForm]);
  const updateField = (field, value) => {
    setForm((current) => {
      const next = { ...current, [field]: value };
      if (field === "start") {
        next.duration = clampDurationToWorkday({
          desired: current.duration,
          start: value,
          workEnd: settings.workEnd,
          maxDuration: settings.maxDurationMinutes,
          slotMinutes: settings.slotMinutes,
        });
      }
      return next;
    });
    onFieldChange?.(field);
    if (field === "start") onFieldChange?.("duration");
  };
  const conflictDifferences = reservationConflictDifferences(form, conflict?.current, { rooms, tags });
  const durationPicker = <fieldset className="duration-field"><div className="duration-field-heading"><legend>预约时长</legend><output>{form.duration} 分钟</output></div><div className="duration-slider-shell" style={{ "--duration-available-progress": availableProgress + "%", "--duration-selected-progress": selectedProgress + "%" }}><div className={`duration-slider-track ${maximum < scaleMaximum ? "is-limited" : ""}`}><span className="duration-slider-available" /><span className="duration-slider-selected" />{DURATION_STEPS.slice(1, -1).map((step) => { const state = step > maximum ? "unavailable" : step <= Number(form.duration) ? "reached" : "available"; return <button className={`duration-slider-stop ${state} ${step === Number(form.duration) ? "selected" : ""}`} style={{ left: progressFor(step) + "%" }} type="button" disabled={busy || step > maximum} aria-label={`${step} 分钟${step > maximum ? "，当前不可用" : ""}`} onClick={() => updateField("duration", step)} key={step}><i aria-hidden="true" /></button>; })}<span className="duration-slider-knob" aria-hidden="true" /></div><input className="duration-range-input" aria-label="预约时长" aria-invalid={Boolean(errors.duration)} aria-valuemax={maximum} aria-valuetext={`${form.duration} 分钟，当前最多 ${maximum} 分钟`} type="range" min={minimum} max={scaleMaximum} step="30" value={form.duration} onChange={(event) => updateField("duration", Math.min(maximum, Number(event.target.value)))} /></div>{errors.duration && <small className="duration-error">{errors.duration}</small>}</fieldset>;
  return <form className={`booking-form ${editing ? "booking-form-edit" : "booking-form-new"}`} onSubmit={onSubmit} noValidate aria-busy={busy}>
    <div className="booking-form-scroll">
      {editing ? <div className="booking-edit-identity"><p>{form.partyName || "未填写姓名"} <span>·</span> {form.caseNumber || "未填写案号"}</p></div> : <div className="booking-create-summary"><p>{form.date ? dateLabel(form.date) : "请选择日期"}<span>·</span>{roomName}</p><h2>{form.start || "选择时段"}{form.start ? "–" + endFromDuration(form.start, form.duration) : ""}</h2></div>}
      {editing && <section className="booking-form-section booking-arrangement-section" aria-labelledby="booking-arrangement-heading"><h3 id="booking-arrangement-heading">安排</h3><div className="booking-schedule-fields"><label className="field"><span>日期</span><input type="date" value={form.date} aria-invalid={Boolean(errors.date)} onChange={(event) => updateField("date", event.target.value)} />{errors.date && <small>{errors.date}</small>}</label><label className="field"><span>笔录室</span><select value={form.roomId} aria-invalid={Boolean(errors.roomId)} onChange={(event) => updateField("roomId", event.target.value)}><option value="">请选择</option>{rooms.filter((room) => room.isActive !== false).map((room) => <option value={room.id} key={room.id}>{room.name}</option>)}</select>{errors.roomId && <small>{errors.roomId}</small>}</label><label className="field"><span>开始时间</span><input type="time" step={Number(settings.slotMinutes || 30) * 60} value={form.start} aria-invalid={Boolean(errors.start)} onChange={(event) => updateField("start", event.target.value)} />{errors.start && <small>{errors.start}</small>}</label></div>{durationPicker}</section>}
      {!editing && durationPicker}
      <section className="booking-form-section booking-information-section" aria-labelledby="booking-information-heading"><h3 id="booking-information-heading">预约信息</h3>
        {Object.keys(errors).length > 0 && <div className="booking-validation-summary" role="alert"><WarningCircle size={17} weight="fill" /><span>请检查 {Object.keys(errors).length} 个字段</span></div>}
        <label className="field"><span>预约对象</span><input data-initial-focus value={form.partyName} aria-invalid={Boolean(errors.partyName)} onChange={(event) => updateField("partyName", event.target.value)} />{errors.partyName && <small>{errors.partyName}</small>}</label>
        <label className="field"><span>案号</span><input value={form.caseNumber} aria-invalid={Boolean(errors.caseNumber)} onChange={(event) => updateField("caseNumber", event.target.value)} />{errors.caseNumber && <small>{errors.caseNumber}</small>}</label>
        <label className="field"><span>事项</span><input value={form.purpose} aria-invalid={Boolean(errors.purpose)} onChange={(event) => updateField("purpose", event.target.value)} />{errors.purpose && <small>{errors.purpose}</small>}</label>
        <fieldset className="tag-field"><legend>标签</legend><div className="tag-choice-grid">{tags.map((tag) => <button type="button" className={`tag-choice ${form.tagId === tag.id ? "selected" : ""}`} style={tagStyle(tag)} aria-pressed={form.tagId === tag.id} title={tag.label} key={tag.id} onClick={() => updateField("tagId", tag.id)}><i /><span>{tag.label}</span></button>)}</div></fieldset>
        {errors.tagId && <small className="duration-error">{errors.tagId}</small>}
        <label className="field booking-notes-field"><span>备注 <em>选填</em></span><textarea rows="3" value={form.notes} onChange={(event) => updateField("notes", event.target.value)} /></label>
      </section>
    </div>
    {conflict?.type === "revision" ? <div className="booking-modified-panel" role="alert"><div className="booking-modified-heading"><WarningCircle size={19} /><div><strong>预约内容已发生变化</strong><p>其他用户已更新这场预约。你的草稿仍然保留，请比较后决定。</p></div></div>{conflictDifferences.length ? <div className="booking-modified-comparison"><div className="booking-modified-comparison-head"><span>字段</span><strong>你的修改</strong><strong>最新预约</strong></div>{conflictDifferences.map((item) => <div className="booking-modified-comparison-row" key={item.label}><span>{item.label}</span><span>{item.localValue}</span><span>{item.serverValue}</span></div>)}</div> : <p>服务器版本已更新，但当前字段值没有可见差异。</p>}<div className="booking-modified-actions"><button className="submit-button" type="button" disabled={conflictCheck.busy} onClick={onUseLatest}>使用最新内容</button><button className="secondary-button" type="button" disabled={conflictCheck.busy} onClick={onContinueDraft}>返回继续调整</button><button className="booking-modified-recheck" type="button" disabled={conflictCheck.busy} onClick={onRecheckConflict}><ArrowClockwise className={conflictCheck.busy ? "spin" : ""} size={16} />{conflictCheck.busy ? "正在重新检查" : "重新检查"}</button><p className={`booking-modified-message ${conflictCheck.message ? "visible" : ""}`} role="status">{conflictCheck.message}</p></div></div> : <div className="booking-form-footer">{failure && <div className="booking-save-failure" role="alert"><WarningCircle size={19} /><span><strong>保存失败</strong><small>未能保存本次修改，你填写的内容已保留。</small></span></div>}{busy ? <div className="booking-saving-status" role="status"><CircleNotch className="spin" size={19} /><span><strong>正在保存预约</strong><small>请稍候</small></span></div> : <div className={`booking-form-actions ${failure ? "has-secondary" : ""}`}><button className="submit-button" type="submit">{failure ? "重试保存" : editing ? "保存修改" : "创建预约"}</button>{failure && <button className="secondary-button" type="button" onClick={onDismissFailure}>稍后处理</button>}</div>}</div>}
  </form>;
}

function BookingDetails({ booking, tag, dateSubtitle, canCopyReminder, canHandover, handoverPending, handoverLabel, onHandover, canEdit, canCancel, events, eventsState, eventsAllowed, onCopyReminder, onEdit, onCancel, onClose, onRetryEvents }) {
  return <div className="booking-details">
    <div className="selection-summary"><h2>{booking.start}–{booking.end}</h2><p>{dateSubtitle || dateLabel(booking.date)}</p>{booking.status && <span className={`drawer-status ${booking.status}`}><i />{reservationStatusLabel(booking.status)}</span>}</div>
    <dl>
      <div><dt>笔录室</dt><dd>{booking.roomName}</dd></div>
      <div><dt>预约者</dt><dd>{booking.owner?.name || booking.ownerName || "未知用户"}</dd></div>
      <div><dt>事项</dt><dd>{booking.purpose}</dd></div>
      <div><dt>标签</dt><dd className="detail-tag" style={tagStyle(tag)}><i />{booking.tagLabel || tag?.label}</dd></div>
      <div><dt>当事人</dt><dd>{booking.partyName}</dd></div>
      <div><dt>案号</dt><dd>{booking.caseNumber}</dd></div>
      {booking.notes && <div><dt>备注</dt><dd>{booking.notes}</dd></div>}
    </dl>
    {eventsAllowed && <section className="booking-event-timeline" aria-labelledby="booking-events-heading" aria-busy={eventsState === "loading"}>
      <h3 id="booking-events-heading">变更记录</h3>
      {eventsState === "loading" ? <p className="booking-events-note"><CircleNotch className="spin" size={17} />正在读取</p> : eventsState === "error" ? <p className="booking-events-note booking-events-error">暂时无法读取变更记录<button type="button" onClick={onRetryEvents}>重新读取</button></p> : events.length ? <ol>{events.map((event) => <li key={event.id}><i aria-hidden="true" /><div><strong>{reservationEventLabel(event.type)}</strong><p>{reservationEventSummary(event)}</p><small>{event.actor?.name || "系统"} · {formatLocalDateTime(event.occurredAt || event.occurredAtUtc)} · 版本 {event.revision}</small></div></li>)}</ol> : <p className="booking-events-note">暂无变更记录</p>}
    </section>}
    {(canCopyReminder || canHandover || handoverPending || canEdit || canCancel) ? <div className="booking-detail-actions">{canEdit && <button className="edit-booking-button" onClick={onEdit}>修改预约</button>}{canHandover && <button className="handover-booking-button" onClick={onHandover}><ArrowsLeftRight size={17} />{handoverLabel || "交接预约"}</button>}{handoverPending && <button className="handover-booking-button pending" type="button" disabled><ArrowsLeftRight size={17} />交接处理中</button>}{canCopyReminder && <button className="copy-reminder-button" onClick={onCopyReminder}><CopySimple size={17} />复制提醒信息</button>}{canCancel && <button className="cancel-booking-button" onClick={onCancel}>取消预约</button>}{!canEdit && !canCancel && <button className="secondary-button booking-detail-close" onClick={onClose}>关闭</button>}</div> : <button className="secondary-button booking-detail-close" onClick={onClose}>关闭</button>}
  </div>;
}

function MainApp({ session, initialBootstrap, onAuthenticatedContext, onLoggedOut, onRecovery }) {
  const initialReceivedAt = useRef(Date.now()).current;
  const initialBusinessDate = parseDate(initialBootstrap.serverDate);
  const initialUiPreferences = useRef(readUiPreferences(initialBootstrap?.currentUser?.id || session.currentUser?.id)).current;
  const [bootstrap, setBootstrap] = useState(initialBootstrap || null);
  const [activeView, setActiveView] = useState(initialUiPreferences.defaultView);
  const [businessClockAnchor, setBusinessClockAnchor] = useState(() => ({
    serverDate: initialBootstrap.serverDate,
    serverTime: initialBootstrap.serverTime,
    receivedAt: initialReceivedAt,
  }));
  const [businessClockTick, setBusinessClockTick] = useState(initialReceivedAt);
  const [currentDate, setCurrentDate] = useState(initialBusinessDate);
  const [bookings, setBookings] = useState([]);
  const [calendarDataDate, setCalendarDataDate] = useState("");
  const [upcoming, setUpcoming] = useState([]);
  const [historySections, setHistorySections] = useState([]);
  const [historyPage, setHistoryPage] = useState({ nextCursor: null, pageSize: 50, total: 0 });
  const [historyLoadingMore, setHistoryLoadingMore] = useState("");
  const [historyMonth, setHistoryMonth] = useState(() => monthKey(initialBusinessDate));
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyScope, setHistoryScope] = useState("unit");
  const [historyOwner, setHistoryOwner] = useState("");
  const [historyRoom, setHistoryRoom] = useState("");
  const [historyStatus, setHistoryStatus] = useState("");
  const [historyTag, setHistoryTag] = useState("");
  const [historyFilterOpen, setHistoryFilterOpen] = useState(false);
  const [historySearchOpen, setHistorySearchOpen] = useState(false);
  const [historyMonthOpen, setHistoryMonthOpen] = useState(false);
  const [drawer, setDrawer] = useState(null);
  const [bookingForm, setBookingForm] = useState(EMPTY_BOOKING);
  const [bookingErrors, setBookingErrors] = useState({});
  const [saveState, setSaveState] = useState("idle");
  const [conflict, setConflict] = useState(null);
  const [conflictCheck, setConflictCheck] = useState({ busy: false, message: "" });
  const [loading, setLoading] = useState({ bootstrap: true, calendar: true, mine: true, history: true });
  const [networkOffline, setNetworkOffline] = useState(false);
  const [sessionExpired, setSessionExpired] = useState(false);
  const [unauthorizedMessage, setUnauthorizedMessage] = useState("");
  const [successNotice, setSuccessNotice] = useState(null);
  const [toast, setToastState] = useState(null);
  const setToast = useCallback((message, tone = "info") => {
    setToastState(message ? { message, tone } : null);
  }, []);
  const [calendarFilterOpen, setCalendarFilterOpen] = useState(false);
  const [calendarTagFilter, setCalendarTagFilter] = useState("");
  const [bookingFilterOpen, setBookingFilterOpen] = useState(false);
  const [bookingRooms, setBookingRooms] = useState(() => new Set((initialBootstrap?.rooms || []).map((room) => room.id)));
  const [bookingTags, setBookingTags] = useState(() => new Set([...(initialBootstrap?.globalTags || []), ...(initialBootstrap?.personalTags || [])].map((tag) => tag.id)));
  const [moreBookingsOpen, setMoreBookingsOpen] = useState(false);
  const [tagEditing, setTagEditing] = useState(false);
  const [tagSaving, setTagSaving] = useState("");
  const [tagDrafts, setTagDrafts] = useState(() => Object.fromEntries([...(initialBootstrap?.globalTags || []), ...(initialBootstrap?.personalTags || [])].map((tag, index) => [tag.id || "tag-" + (tag.slot || index + 1), tag.label || tag.name || "标签 " + (tag.slot || index + 1)])));
  const [users, setUsers] = useState(() => initialBootstrap?.users || []);
  const [rooms, setRooms] = useState(() => initialBootstrap?.rooms || []);
  const [userSearchOpen, setUserSearchOpen] = useState(false);
  const [userQuery, setUserQuery] = useState("");
  const [system, setSystem] = useState(null);
  const [systemLoading, setSystemLoading] = useState(false);
  const [updateChecking, setUpdateChecking] = useState(false);
  const [systemSettingsSaving, setSystemSettingsSaving] = useState(false);
  const [auditItems, setAuditItems] = useState([]);
  const [auditPage, setAuditPage] = useState({ nextCursor: null, pageSize: 50, total: 0 });
  const [auditFilters, setAuditFilters] = useState({ action: "", outcome: "", actorId: "", targetType: "", targetId: "", dateFrom: "", dateTo: "" });
  const [auditFilterOpen, setAuditFilterOpen] = useState(false);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditLoadingMore, setAuditLoadingMore] = useState(false);
  const [auditHidden, setAuditHidden] = useState(false);
  const [auditUnreadCount, setAuditUnreadCount] = useState(0);
  const [tokens, setTokens] = useState([]);
  const [tokenRevokingId, setTokenRevokingId] = useState("");
  const [roomDeleteBusy, setRoomDeleteBusy] = useState(false);
  const [bookingEvents, setBookingEvents] = useState([]);
  const [bookingEventsState, setBookingEventsState] = useState("idle");
  const [preferencesDraft, setPreferencesDraft] = useState(() => initialBootstrap?.preferences || null);
  const [preferencesSaving, setPreferencesSaving] = useState(false);
  const [preferencesErrors, setPreferencesErrors] = useState({});
  const [uiPreferencesDraft, setUiPreferencesDraft] = useState(initialUiPreferences);
  const [dueReminders, setDueReminders] = useState({ changes: [], upcoming: [], handovers: [] });
  const [handoverBoard, setHandoverBoard] = useState({ incoming: [], outgoing: [] });
  const [handoverDirectory, setHandoverDirectory] = useState([]);
  const [handoverDirectoryState, setHandoverDirectoryState] = useState("idle");
  const [handoverActionBusy, setHandoverActionBusy] = useState(false);
  const [deferredHandoverIds, setDeferredHandoverIds] = useState(() => new Set());
  const [noticeAckBusy, setNoticeAckBusy] = useState(false);
  const [arrivalNotice, setArrivalNotice] = useState(null);
  const seenUpcomingIdsRef = useRef(new Set());
  const reminderPollFailedRef = useRef(false);
  const [preservedDraft, setPreservedDraft] = useState(null);
  const [calendarEnterDirection, setCalendarEnterDirection] = useState("");
  const mainRef = useRef(null);
  const noticeModalRef = useRef(null);
  const calendarCanvasRef = useRef(null);
  const calendarAutoScrollRef = useRef({ inCalendar: false, day: "", requested: 0, handled: 0 });
  const previousCalendarDayRef = useRef("");
  const eventRequestRef = useRef(0);
  const calendarRequestRef = useRef(0);
  const calendarAbortRef = useRef(null);
  const calendarDataDateRef = useRef("");
  const historyRequestRef = useRef(0);
  const historyUserSelectRef = useRef(null);
  const auditRequestRef = useRef(0);
  const auditHiddenRef = useRef(false);
  const knownAuditIdsRef = useRef(new Set());
  const sessionDraftRef = useRef(null);
  const bookingFilterPopover = useDismissiblePopover(bookingFilterOpen, () => setBookingFilterOpen(false));
  const calendarFilterPopover = useDismissiblePopover(calendarFilterOpen, () => setCalendarFilterOpen(false));
  const historySearchPopover = useDismissiblePopover(historySearchOpen, () => setHistorySearchOpen(false));
  const historyFilterPopover = useDismissiblePopover(historyFilterOpen, () => setHistoryFilterOpen(false));
  const historyMonthPopover = useDismissiblePopover(historyMonthOpen, () => setHistoryMonthOpen(false));
  const userSearchPopover = useDismissiblePopover(userSearchOpen, () => setUserSearchOpen(false));
  const auditFilterPopover = useDismissiblePopover(auditFilterOpen, () => setAuditFilterOpen(false));
  const role = bootstrap?.currentUser?.role || session.currentUser?.role;
  const currentUser = bootstrap?.currentUser || session.currentUser;
  sessionDraftRef.current = {
    bookingForm,
    drawerType: drawer?.type,
    preservedDraft,
    userId: currentUser?.id,
  };
  const permissions = bootstrap?.permissions || {};
  const settings = bootstrap?.settings || { workStart: "08:30", workEnd: "17:30", slotMinutes: 30, maxDurationMinutes: 180 };
  const businessClock = useMemo(
    () => projectServerClock(businessClockAnchor, businessClockTick),
    [businessClockAnchor, businessClockTick],
  );
  const businessDate = useMemo(() => parseDate(businessClock.date), [businessClock.date]);
  const withRelativeDay = (dateText) => {
    const relative = relativeDayLabel(dateText, businessClock.date);
    return relative ? `${relative} · ${dateLabel(dateText)}` : dateLabel(dateText);
  };
  const calendarDateMinimum = dateKey(shiftDateByYears(businessDate, -2));
  const calendarDateMaximum = dateKey(shiftDateByYears(businessDate, 2));
  const tags = useMemo(() => [...(bootstrap?.globalTags || []), ...(bootstrap?.personalTags || [])].map(normalizeTag).sort((a, b) => a.slot - b.slot), [bootstrap]);
  const editTagContext = useMemo(() => bookingTagContext({
    booking: drawer?.type === "edit" ? drawer.booking : null,
    role,
    currentUserId: currentUser?.id,
    globalTags: bootstrap?.globalTags || [],
    currentPersonalTags: bootstrap?.personalTags || [],
    users,
  }), [bootstrap, currentUser?.id, drawer, role, users]);
  const editTags = useMemo(
    () => editTagContext.tags.map(normalizeTag).sort((left, right) => left.slot - right.slot),
    [editTagContext],
  );
  const workingTimeSlots = useMemo(() => {
    try { return generateTimeSlots(settings.workStart, settings.workEnd, settings.slotMinutes || 30); }
    catch { return generateTimeSlots("08:30", "17:30", 30); }
  }, [settings.workEnd, settings.workStart, settings.slotMinutes]);
  useDocumentTitle({ mine: "我的预约", calendar: "预约日历", handovers: "工作交接", history: "预约记录", "data-center": "数据中心", rooms: "笔录室", users: "用户管理", system: "系统状态", settings: "个人中心", unauthorized: "无权限" }[activeView] || "会议室预约系统");

  const expireSession = useCallback(() => {
    const latest = sessionDraftRef.current;
    const activeForm = ["create", "edit", "slot-conflict"].includes(latest?.drawerType)
      ? latest.bookingForm
      : null;
    writeSessionBookingDraft(window.sessionStorage, latest?.userId, {
      bookingForm: activeForm,
      preservedDraft: latest?.preservedDraft,
    });
    setSessionExpired(true);
  }, []);

  useEffect(() => {
    const recovered = consumeSessionBookingDraft(
      window.sessionStorage,
      currentUser?.id,
    );
    const draft = recovered?.bookingForm || recovered?.preservedDraft;
    if (!draft) return;
    setBookingForm(draft);
    setPreservedDraft(draft);
    setActiveView("calendar");
    setToast("已恢复未保存的预约草稿", "info");
  }, [currentUser?.id, setToast]);

  useEffect(() => {
    const timer = window.setInterval(() => setBusinessClockTick(Date.now()), 30000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    auditHiddenRef.current = auditHidden;
  }, [auditHidden]);

  // 进入日历或切到“今天”时，安静地把当前时间线带到视口上三分之一处；
  // 只在到达时执行一次，不随后续时钟跳动或用户滚动重复定位。
  useEffect(() => {
    const state = calendarAutoScrollRef.current;
    const day = dateKey(currentDate);
    const inCalendar = activeView === "calendar";
    if (inCalendar && day === businessClock.date && (!state.inCalendar || state.day !== day)) {
      state.requested += 1;
    }
    state.inCalendar = inCalendar;
    state.day = day;
  }, [activeView, businessClock.date, currentDate]);

  useEffect(() => {
    if (activeView !== "calendar" || dateKey(currentDate) !== businessClock.date) return undefined;
    const state = calendarAutoScrollRef.current;
    if (!state.requested || state.handled === state.requested) return undefined;
    const frame = window.requestAnimationFrame(() => {
      const canvas = calendarCanvasRef.current;
      const timeLine = canvas?.querySelector(".current-time-line");
      if (!canvas || !timeLine) return;
      const offset = timeLine.getBoundingClientRect().top
        - canvas.getBoundingClientRect().top
        + canvas.scrollTop
        - canvas.clientHeight / 3;
      canvas.scrollTop = Math.max(0, offset);
      state.handled = state.requested;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeView, businessClock.date, calendarDataDate, currentDate, loading.calendar]);

  // 日期切换按前后方向播放一次轻位移；首次进入不播放。
  useEffect(() => {
    const dayKey = dateKey(currentDate);
    const previous = previousCalendarDayRef.current;
    previousCalendarDayRef.current = dayKey;
    if (!previous || previous === dayKey) return;
    setCalendarEnterDirection(dayKey > previous ? "forward" : "backward");
  }, [currentDate]);

  const handleError = useCallback((error, fallback) => {
    if (error?.code === "SYSTEM_RECOVERY_REQUIRED") {
      onRecovery(error);
      return;
    }
    if (error?.status === 401 || error?.code === "SESSION_EXPIRED" || error?.code === "SESSION_REQUIRED") {
      expireSession();
      return;
    }
    if (error?.status === 403 && error?.code === "FORBIDDEN") {
      setUnauthorizedMessage(userFacingError(error, "当前账户没有访问该页面的权限"));
      setActiveView("unauthorized");
      return;
    }
    if (error?.code === "NETWORK_ERROR") setNetworkOffline(true);
    setToast(userFacingError(error, fallback), "error");
  }, [expireSession, onRecovery, setToast]);

  const loadBootstrap = useCallback(async () => {
    setLoading((current) => ({ ...current, bootstrap: true }));
    try {
      const value = await api.getBootstrap();
      if (
        value?.currentUser?.id !== session.currentUser?.id
        || value?.currentUser?.role !== session.currentUser?.role
      ) {
        const refreshedSession = await api.getSession();
        onAuthenticatedContext(validateAuthenticatedContext(refreshedSession, value));
        return;
      }
      validateAuthenticatedContext(session, value);
      const receivedAt = Date.now();
      setBootstrap(value);
      setBusinessClockAnchor({
        serverDate: value.serverDate,
        serverTime: value.serverTime,
        receivedAt,
      });
      setBusinessClockTick(receivedAt);
      setRooms(value.rooms || []);
      setUsers(value.users || []);
      setBookingRooms(new Set((value.rooms || []).map((room) => room.id)));
      setBookingTags(new Set([...(value.globalTags || []), ...(value.personalTags || [])].map((tag) => tag.id)));
      setPreferencesDraft(value.preferences || {});
      setTagDrafts(Object.fromEntries([...(value.globalTags || []), ...(value.personalTags || [])].map((tag, index) => [tag.id || "tag-" + (tag.slot || index + 1), tag.label || tag.name || "标签 " + (tag.slot || index + 1)])));
      setNetworkOffline(false);
    } catch (error) {
      handleError(error, "无法读取系统配置");
    } finally {
      setLoading((current) => ({ ...current, bootstrap: false }));
    }
  }, [handleError, onAuthenticatedContext, session]);

  const loadRooms = useCallback(async ({ silent = false } = {}) => {
    if (!permissions.manageRooms) return [];
    try {
      const nextRooms = unwrapItems(await api.getRooms());
      setRooms(nextRooms);
      setNetworkOffline(false);
      return nextRooms;
    } catch (error) {
      const requiresGlobalHandling = error?.status === 401
        || error?.status === 403
        || error?.code === "SYSTEM_RECOVERY_REQUIRED";
      if (!silent || requiresGlobalHandling) handleError(error, "无法更新笔录室数据");
      else if (error?.code === "NETWORK_ERROR") setNetworkOffline(true);
      return [];
    }
  }, [handleError, permissions.manageRooms]);

  const loadCalendar = useCallback(async () => {
    const requestedDate = dateKey(currentDate);
    const requestNumber = calendarRequestRef.current + 1;
    calendarRequestRef.current = requestNumber;
    calendarAbortRef.current?.abort();
    const controller = new AbortController();
    calendarAbortRef.current = controller;
    if (calendarDataDateRef.current !== requestedDate) setBookings([]);
    setLoading((current) => ({ ...current, calendar: true }));
    try {
      const nextBookings = await fetchAllReservations(
        requestedDate,
        requestedDate,
        { signal: controller.signal },
      );
      if (calendarRequestRef.current !== requestNumber) return;
      setBookings(nextBookings);
      calendarDataDateRef.current = requestedDate;
      setCalendarDataDate(requestedDate);
      setNetworkOffline(false);
    } catch (error) {
      if (error?.name === "AbortError" || calendarRequestRef.current !== requestNumber) return;
      handleError(error, "无法读取预约日历");
    } finally {
      if (calendarRequestRef.current === requestNumber) {
        setLoading((current) => ({ ...current, calendar: false }));
      }
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

  const loadHistory = useCallback(async ({ append = false, cursor = "", month = historyMonth } = {}) => {
    const requestNumber = historyRequestRef.current + 1;
    historyRequestRef.current = requestNumber;
    if (append) setHistoryLoadingMore(month);
    else setLoading((current) => ({ ...current, history: true }));
    try {
      const result = await api.getHistory({ month, ownerId: role === "admin" ? (historyScope === "mine" ? currentUser.id : historyOwner) : undefined, roomId: historyRoom, status: historyStatus, tagId: historyTag, query: historyQuery.trim(), pageSize: 50, cursor });
      if (historyRequestRef.current !== requestNumber) return;
      const nextItems = unwrapItems(result);
      const [year, monthNumber] = month.split("-").map(Number);
      const nextSection = {
        id: month,
        label: `${year}年${monthNumber}月`,
        items: nextItems,
        nextCursor: result?.nextCursor || null,
        pageSize: Number(result?.pageSize || 50),
        total: Number(result?.total || nextItems.length),
      };
      setHistorySections((current) => {
        if (!append) return [nextSection];
        const existingIndex = current.findIndex((section) => section.id === month);
        if (existingIndex < 0) return [...current, nextSection];
        return current.map((section, index) => index === existingIndex ? {
          ...nextSection,
          items: [...new Map([...section.items, ...nextItems].map((booking) => [booking.id, booking])).values()],
        } : section);
      });
      if (month === historyMonth) setHistoryPage({ nextCursor: nextSection.nextCursor, pageSize: nextSection.pageSize, total: nextSection.total });
    } catch (error) {
      if (historyRequestRef.current === requestNumber) handleError(error, "无法读取预约记录");
    } finally {
      if (historyRequestRef.current === requestNumber) {
        if (append) setHistoryLoadingMore("");
        else setLoading((current) => ({ ...current, history: false }));
      }
    }
  }, [currentUser.id, handleError, historyMonth, historyOwner, historyQuery, historyRoom, historyScope, historyStatus, historyTag, role]);

  const loadMoreHistory = useCallback((section) => {
    if (!section?.nextCursor || historyLoadingMore) return;
    loadHistory({ append: true, cursor: section.nextCursor, month: section.id });
  }, [historyLoadingMore, loadHistory]);

  const loadPreviousHistoryMonth = useCallback(() => {
    if (historyLoadingMore) return;
    const oldestMonth = historySections.at(-1)?.id || historyMonth;
    const [year, month] = oldestMonth.split("-").map(Number);
    loadHistory({ append: true, month: monthKey(new Date(year, month - 2, 1)) });
  }, [historyLoadingMore, historyMonth, historySections, loadHistory]);

  useEffect(() => { if (!initialBootstrap) loadBootstrap(); }, [initialBootstrap, loadBootstrap]);
  useEffect(() => { if (bootstrap) loadCalendar(); }, [bootstrap, loadCalendar]);
  useEffect(() => { if (bootstrap) loadUpcoming(); }, [bootstrap, loadUpcoming]);
  useEffect(() => { if (bootstrap) loadHistory(); }, [bootstrap, loadHistory]);
  useEffect(() => {
    if (bootstrap && activeView === "mine" && !sessionExpired) refreshHandoverBoard();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeView, bootstrap, sessionExpired]);
  useEffect(() => {
    if (drawer?.type !== "handover") return undefined;
    let cancelled = false;
    setHandoverDirectoryState("loading");
    api.getUserDirectory()
      .then((result) => {
        if (cancelled) return;
        setHandoverDirectory(result?.users || []);
        setHandoverDirectoryState("ready");
      })
      .catch((error) => {
        if (cancelled) return;
        setHandoverDirectoryState("ready");
        setHandoverDirectory([]);
        handleError(error, "无法读取人员列表");
      });
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drawer?.type]);
  useEffect(() => {
    if (activeView !== "rooms" || !permissions.manageRooms) return undefined;
    loadRooms();
    const timer = window.setInterval(() => loadRooms({ silent: true }), 30000);
    return () => window.clearInterval(timer);
  }, [activeView, loadRooms, permissions.manageRooms]);
  const refreshDueReminders = useCallback(async () => {
    try {
      const items = unwrapItems(await api.getDueReminders());
      const changes = items.filter((item) => item.kind === "change");
      const upcoming = items.filter((item) => item.kind === "upcoming");
      const handovers = items.filter((item) => item.kind === "handover");
      setDueReminders({ changes, upcoming, handovers });
      reminderPollFailedRef.current = false;
      // 到达时刻：首次进入提前窗口的临近提醒 → 一次性 toast + 轻提示音。
      const seen = seenUpcomingIdsRef.current;
      const fresh = upcoming.filter((item) => !seen.has(item.id));
      upcoming.forEach((item) => seen.add(item.id));
      const currentIds = new Set(upcoming.map((item) => item.id));
      for (const id of seen) if (!currentIds.has(id)) seen.delete(id);
      if (fresh.length) {
        setArrivalNotice({ booking: fresh[0], message: arrivalReminderText(fresh[0]) });
        if (bootstrap?.preferences?.reminderSound !== false) playArrivalChime();
      }
      return true;
    } catch (error) {
      // 连续失败只提示一次，成功后复位；避免每 60 秒的错误弹窗刷屏。
      if (!reminderPollFailedRef.current) {
        reminderPollFailedRef.current = true;
        handleError(error, "无法读取提醒");
      }
      return false;
    }
  }, [bootstrap, handleError]);
  useEffect(() => {
    // 交接请求是待办而非通知：即使两项提醒开关全关也保持轮询。
    let cancelled = false;
    const check = async () => {
      if (!cancelled) await refreshDueReminders();
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") check();
    };
    check();
    document.addEventListener("visibilitychange", onVisibilityChange);
    const timer = window.setInterval(check, 60000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [refreshDueReminders]);
  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToastState(null), 4200);
    return () => window.clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    if (!arrivalNotice) return undefined;
    const timer = window.setTimeout(() => setArrivalNotice(null), 5200);
    return () => window.clearTimeout(timer);
  }, [arrivalNotice]);
  // 需处理弹窗：变更通知 + 交接请求共用同一焦点陷阱。交接可以暂后处理，
  // 但绝不会因为 Esc 或关闭弹窗而被接受/拒绝；请求仍保留在「工作交接」。
  const visibleHandoverReminders = dueReminders.handovers.filter(
    (item) => !deferredHandoverIds.has(item.handoverRequestId),
  );
  const noticeHasHandovers = visibleHandoverReminders.length > 0;
  const noticeOnlyHandovers = noticeHasHandovers && dueReminders.changes.length === 0;
  const noticeModalOpen = (dueReminders.changes.length > 0 || noticeHasHandovers) && !drawer && !sessionExpired;
  const deferVisibleHandovers = () => {
    setDeferredHandoverIds((current) => new Set([
      ...current,
      ...visibleHandoverReminders.map((item) => item.handoverRequestId),
    ]));
    setToast("交接请求已保留在「工作交接」，可稍后处理", "info");
  };
  useFocusTrap(
    noticeModalRef,
    noticeModalOpen,
    () => {
      if (noticeHasHandovers) {
        deferVisibleHandovers();
        return;
      }
      if (!noticeAckBusy) void acknowledgeChangeNotices(dueReminders.changes);
    },
    true,
    dueReminders.changes.map((item) => item.eventId).join("|")
      + "#" + visibleHandoverReminders.map((item) => item.handoverRequestId).join("|"),
    mainRef,
  );
  useEffect(() => {
    if (!successNotice) return undefined;
    const timer = window.setTimeout(() => setSuccessNotice(null), 8000);
    return () => window.clearTimeout(timer);
  }, [successNotice]);
  useEffect(() => {
    setSuccessNotice(null);
  }, [currentDate]);
  useEffect(() => {
    if (activeView !== "system" || !permissions.manageSystem) return undefined;
    const refresh = () => Promise.all([loadSystem(true), loadAudit({ silent: true }), loadTokens(true)]);
    refresh();
    const timer = window.setInterval(() => Promise.all([loadSystem(true), loadAudit({ silent: true, preserveLoaded: true }), loadTokens(true)]), 30000);
    return () => window.clearInterval(timer);
  // The local refresh functions consume exactly the filter fields below.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeView, permissions.manageSystem, auditFilters.action, auditFilters.outcome, auditFilters.actorId, auditFilters.targetType, auditFilters.targetId, auditFilters.dateFrom, auditFilters.dateTo]);
  if (loading.bootstrap && !bootstrap) return <LoadingScreen label="正在读取工作台" />;
  if (!bootstrap) return <FatalScreen error="无法读取系统配置" onRetry={loadBootstrap} />;

  const activeRooms = rooms.filter((room) => room.isActive !== false).sort((a, b) => Number(a.sortOrder || 0) - Number(b.sortOrder || 0));
  const visibleCalendarBookings = calendarDataDate === dateKey(currentDate) ? bookings : [];
  let timeSlots = workingTimeSlots;
  try {
    timeSlots = calendarTimeSlots({
      workStart: settings.workStart,
      workEnd: settings.workEnd,
      slotMinutes: settings.slotMinutes,
      bookings: visibleCalendarBookings,
    });
  } catch { /* keep the validated working-hours fallback */ }
  const bookingFor = (roomId, start, end) => visibleCalendarBookings.find((booking) => booking.roomId === roomId && booking.status !== "cancelled" && overlaps(booking, start, end));
  const tagFor = (booking) => tags.find((tag) => tag.id === booking?.tagId) || normalizeTag({ id: booking?.tagId, label: booking?.tagLabel, slot: 1 }, 0);
  const bookingMaximumDuration = maximumAvailableDuration({
    bookings: visibleCalendarBookings,
    roomId: bookingForm.roomId,
    date: bookingForm.date,
    start: bookingForm.start,
    workEnd: settings.workEnd,
    maxDuration: settings.maxDurationMinutes,
    slotMinutes: settings.slotMinutes,
    excludeBookingId: drawer?.type === "edit" ? drawer.booking?.id : "",
  });
  const currentTimeOffset = calendarTimeLineOffset({
    selectedDate: dateKey(currentDate),
    serverDate: businessClock.date,
    serverTime: businessClock.time,
    workStart: settings.workStart,
    workEnd: settings.workEnd,
    visibleStart: timeSlots[0]?.[0] || settings.workStart,
    slotMinutes: settings.slotMinutes,
  });

  function navigate(view) {
    const item = NAV_ITEMS.find((nav) => nav.id === view);
    if (item?.permission && !permissions[item.permission]) {
      setToast("当前账户无权访问" + item.label, "error");
      return;
    }
    setDrawer(null);
    if (view !== "calendar") setSuccessNotice(null);
    setUnauthorizedMessage("");
    setActiveView(view);
  }

  function openPersonalCenter() {
    setPreferencesDraft(bootstrap?.preferences || {});
    setPreferencesErrors({});
    setUiPreferencesDraft(readUiPreferences(currentUser.id));
    navigate("settings");
  }

  function beginCreate({ roomId, start, bookingDate, draft = null }) {
    const desiredDuration = Number(draft?.duration || bootstrap.preferences?.defaultDuration || 60);
    const maximumDuration = maximumAvailableDuration({
      bookings,
      roomId,
      date: bookingDate,
      start,
      workEnd: settings.workEnd,
      maxDuration: settings.maxDurationMinutes,
      slotMinutes: settings.slotMinutes,
    });
    setBookingForm({
      ...EMPTY_BOOKING,
      ...(draft || {}),
      roomId,
      start,
      date: bookingDate,
      duration: Math.min(maximumDuration, clampDurationToWorkday({
        desired: desiredDuration,
        start,
        workEnd: settings.workEnd,
        maxDuration: settings.maxDurationMinutes,
        slotMinutes: settings.slotMinutes,
      })),
      tagId: defaultBookingTagId({
        tags,
        defaultTagSlot: bootstrap.preferences?.defaultTagSlot,
        draft,
      }),
    });
    setPreservedDraft(null);
    setDrawer({ type: "create" });
  }

  function openCreate(roomId, start, bookingDate = dateKey(currentDate)) {
    setSuccessNotice(null);
    if (hasBookingStarted({
      date: bookingDate,
      start,
      serverDate: businessClock.date,
      serverTime: businessClock.time,
    })) {
      setToast("该时段已经开始，请选择当前时间之后的空白时段", "error");
      return;
    }
    setBookingErrors({});
    setSaveState("idle");
    setConflict(null);
    if (preservedDraft) {
      setDrawer({
        type: "draft-relocation",
        draft: preservedDraft,
        target: { roomId, start, bookingDate },
      });
      return;
    }
    beginCreate({ roomId, start, bookingDate });
  }

  async function openDefaultCreate() {
    const preferredRoom = activeRooms.find((room) => room.id === bootstrap.preferences?.defaultRoomId) || activeRooms[0];
    if (!preferredRoom) {
      if (permissions.manageRooms) {
        setToast("当前没有可用笔录室，请先启用或创建笔录室", "info");
        navigate("rooms");
      } else {
        setToast("当前没有可用笔录室，请联系管理员启用后再预约", "info");
      }
      return;
    }
    navigate("calendar");
    setLoading((current) => ({ ...current, calendar: true }));
    try {
      const now = new Date(businessDate);
      const currentTime = businessClock.time.slice(0, 5);
      for (let offset = 0; offset < 14; offset += 1) {
        const day = shiftDate(now, offset);
        const dayKey = dateKey(day);
        const dayBookings = await fetchAllReservations(dayKey);
        const start = findFirstAvailableStart({
          bookings: dayBookings,
          roomId: preferredRoom.id,
          slots: workingTimeSlots,
          notBefore: offset === 0 ? currentTime : "",
        });
        if (!start) continue;
        setCurrentDate(day);
        setBookings(dayBookings);
        calendarDataDateRef.current = dayKey;
        setCalendarDataDate(dayKey);
        setNetworkOffline(false);
        openCreate(preferredRoom.id, start, dayKey);
        return;
      }
      setToast("未来两周内没有可用时段，请在日历中选择其他笔录室", "info");
    } catch (error) {
      handleError(error, "无法读取可用时段");
    } finally {
      setLoading((current) => ({ ...current, calendar: false }));
    }
  }

  async function loadBookingEvents(booking) {
    const requestNumber = eventRequestRef.current + 1;
    eventRequestRef.current = requestNumber;
    setBookingEvents([]);
    setBookingEventsState("loading");
    try {
      const result = await api.getReservationEvents(booking.id);
      if (eventRequestRef.current !== requestNumber) return;
      setBookingEvents(unwrapItems(result));
      setBookingEventsState("loaded");
    } catch (error) {
      if (eventRequestRef.current !== requestNumber) return;
      setBookingEventsState("error");
      if (error?.status === 401 || error?.code === "SYSTEM_RECOVERY_REQUIRED") handleError(error, "无法读取变更记录");
    }
  }

  async function openDetails(booking, readOnly = false, returnTo = null) {
    setSuccessNotice(null);
    const eventsAllowed = role === "admin" || booking.ownerId === currentUser.id;
    setBookingEvents([]);
    setBookingEventsState(eventsAllowed ? "loading" : "hidden");
    setDrawer({ type: "details", booking, readOnly, returnTo });
    if (eventsAllowed) await loadBookingEvents(booking);
  }

  function openEdit(booking, returnTo = null) {
    setSuccessNotice(null);
    const ownerTags = bookingTagContext({
      booking,
      role,
      currentUserId: currentUser.id,
      globalTags: bootstrap?.globalTags || [],
      currentPersonalTags: bootstrap?.personalTags || [],
      users,
    });
    const ownerTagError = !ownerTags.ownerTagsAvailable && ["tag-3", "tag-4"].includes(booking.tagId)
      ? { tagId: "原预约者的个人标签暂不可用。请重新读取工作台，或明确改选单位标签后再保存。" }
      : {};
    setBookingErrors(ownerTagError);
    setSaveState("idle");
    setConflict(null);
    setConflictCheck({ busy: false, message: "" });
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
    setDrawer({ type: "edit", booking, returnTo });
  }

  async function saveBooking(event) {
    event.preventDefault();
    const editing = drawer?.type === "edit";
    const returnTo = drawer?.returnTo || null;
    const errors = validateBookingForm(bookingForm, settings.slotMinutes);
    if (
      drawer?.type === "edit"
      && !editTagContext.ownerTagsAvailable
      && ["tag-3", "tag-4"].includes(bookingForm.tagId)
    ) {
      errors.tagId = "无法确认原预约者的个人标签含义，请改选单位标签或重新读取工作台";
    }
    setBookingErrors(errors);
    if (Object.keys(errors).length) {
      window.requestAnimationFrame(() => document.querySelector('.booking-drawer [aria-invalid="true"]')?.focus());
      return;
    }
    setSaveState("saving");
    setConflict(null);
    setConflictCheck({ busy: false, message: "" });
    try {
      const saved = editing
        ? await api.updateReservation(drawer.booking.id, bookingPayload(bookingForm, drawer.booking.revision))
        : await api.createReservation(bookingPayload(bookingForm));
      await Promise.all([loadCalendar(), loadUpcoming(), loadHistory(), loadRooms({ silent: true })]);
      if (editing && returnTo?.type === "room-delete-blocked") {
        await refreshRoomDeletionFlow(returnTo.room, returnTo);
        setSuccessNotice(null);
        setToast("预约已更新，可以继续处理笔录室删除", "success");
      } else {
        setDrawer(null);
        setSuccessNotice({ action: editing ? "预约已更新" : "预约已创建", booking: saved });
      }
      setSaveState("idle");
    } catch (error) {
      if (error.code === "SYSTEM_RECOVERY_REQUIRED") {
        handleError(error, "系统需要恢复");
        setSaveState("idle");
      } else if (error.code === "SLOT_CONFLICT") {
        setSuccessNotice(null);
        setConflict({ type: "slot", conflicts: error.conflicts });
        setConflictCheck({ busy: false, message: "" });
        setDrawer((current) => ({ ...current, type: "slot-conflict" }));
        await loadCalendar();
      } else if (error.code === "REVISION_CONFLICT") {
        setSuccessNotice(null);
        const rebased = rebaseBookingEdit(bookingForm, error.current);
        setBookingForm(rebased.draft);
        setDrawer((current) => ({ ...current, booking: rebased.baseline }));
        setConflict({ type: "revision", current: rebased.baseline });
        setConflictCheck({ busy: false, message: "" });
        setSaveState("idle");
      } else if (error.code === "VALIDATION_ERROR") {
        setBookingErrors(error.fields || {});
        setSaveState("idle");
        window.requestAnimationFrame(() => document.querySelector('.booking-drawer [aria-invalid="true"]')?.focus());
      } else if (error.code === "BOOKING_STARTED") {
        setSaveState("idle");
        if (drawer.type === "edit") {
          setBookingErrors({ start: "预约已经开始，不能再修改。如需停止，请返回详情取消预约" });
        } else {
          setPreservedDraft(bookingForm);
          setDrawer(null);
          setToast("该时段已经开始，预约内容已保留。请选择当前时间之后的空白时段", "error");
        }
        await loadCalendar();
      } else {
        if (error.status === 401) expireSession();
        setSaveState("failed");
      }
    }
  }

  async function recheckSlotConflict() {
    if (drawer?.type !== "slot-conflict" || conflictCheck.busy) return;
    setConflictCheck({ busy: true, message: "" });
    try {
      const nextBookings = await fetchAllReservations(bookingForm.date, bookingForm.date);
      const targetEnd = endFromDuration(bookingForm.start, bookingForm.duration);
      const occupied = nextBookings.some((booking) => (
        booking.roomId === bookingForm.roomId
        && booking.status !== "cancelled"
        && overlaps(booking, bookingForm.start, targetEnd)
      ));
      if (dateKey(currentDate) === bookingForm.date) {
        setBookings(nextBookings);
        calendarDataDateRef.current = bookingForm.date;
        setCalendarDataDate(bookingForm.date);
      }
      setConflictCheck({ busy: false, message: occupied ? "该时段仍被占用" : "该时段已可用，可以返回日历重新选择" });
    } catch (error) {
      setConflictCheck({ busy: false, message: "暂时无法重新检查，请稍后重试" });
      if (error?.status === 401 || error?.code === "SYSTEM_RECOVERY_REQUIRED") handleError(error, "无法重新检查这个时段");
    }
  }

  async function recheckRevisionConflict() {
    if (drawer?.type !== "edit" || conflict?.type !== "revision" || conflictCheck.busy) return;
    setConflictCheck({ busy: true, message: "" });
    try {
      const previousRevision = Number(conflict.current?.revision || 0);
      const latest = await api.getReservation(drawer.booking.id);
      const rebased = rebaseBookingEdit(bookingForm, latest);
      setBookingForm(rebased.draft);
      setDrawer((current) => ({ ...current, booking: rebased.baseline }));
      setConflict({ type: "revision", current: rebased.baseline });
      setConflictCheck({
        busy: false,
        message: Number(latest.revision || 0) > previousRevision
          ? "预约仍有新的变化，已更新最新内容"
          : "当前仍是已显示的最新版本",
      });
    } catch (error) {
      setConflictCheck({ busy: false, message: "暂时无法重新检查，请稍后重试" });
      if (error?.status === 401 || error?.code === "SYSTEM_RECOVERY_REQUIRED") handleError(error, "无法重新检查预约内容");
    }
  }

  async function cancelBooking() {
    const returnTo = drawer?.returnTo || null;
    setSaveState("saving");
    try {
      await api.cancelReservation(drawer.booking.id, drawer.booking.revision);
      setSuccessNotice(null);
      await Promise.all([loadCalendar(), loadUpcoming(), loadHistory(), loadRooms({ silent: true })]);
      if (returnTo?.type === "room-delete-blocked") {
        await refreshRoomDeletionFlow(returnTo.room, returnTo);
        setToast("预约已取消，可以继续处理笔录室删除", "success");
      } else {
        setDrawer(null);
        setToast("预约已取消", "success");
      }
    } catch (error) {
      if (error.code === "REVISION_CONFLICT") {
        openDetails(error.current, false, returnTo);
        setToast("预约已被其他用户修改，已显示最新内容", "info");
      } else handleError(error, "取消预约失败");
    } finally {
      setSaveState("idle");
    }
  }

  function tagPayload(section) {
    try {
      return buildTagSectionPayload(tags, tagDrafts, section);
    } catch {
      setToast(`${section === "global" ? "单位" : "个人"}标签名称不能为空`, "error");
      return null;
    }
  }

  async function saveGlobalTags() {
    if (role !== "admin") return;
    const payload = tagPayload("global");
    if (!payload) return;
    setTagSaving("global");
    try {
      const saved = await api.updateGlobalTags(payload);
      setBootstrap((current) => ({ ...current, globalTags: unwrapItems(saved) }));
      setToast("单位标签已保存；个人标签草稿未受影响", "success");
    } catch (error) {
      handleError(error, "保存单位标签失败；个人标签草稿仍保留");
    } finally {
      setTagSaving("");
    }
  }

  async function savePersonalTags() {
    const payload = tagPayload("personal");
    if (!payload) return;
    setTagSaving("personal");
    try {
      const saved = await api.updatePreferences({
        ...bootstrap.preferences,
        personalTags: payload,
      });
      setBootstrap((current) => ({
        ...current,
        currentUser: saved.profile || current.currentUser,
        personalTags: saved.personalTags || current.personalTags,
        preferences: {
          ...current.preferences,
          defaultDuration: saved.defaultDuration,
          defaultRoomId: saved.defaultRoomId,
          bookingChangeNotifications: saved.bookingChangeNotifications,
          bookingReminder: saved.bookingReminder,
        },
      }));
      setToast("个人标签已保存；单位标签草稿未受影响", "success");
    } catch (error) {
      handleError(error, "保存个人标签失败；单位标签草稿仍保留");
    } finally {
      setTagSaving("");
    }
  }

  async function acknowledgeChangeNotices(items) {
    const targets = (items || []).filter((item) => item?.eventId);
    if (!targets.length || noticeAckBusy) return false;
    setNoticeAckBusy(true);
    try {
      const results = await Promise.allSettled(
        targets.map((item) => api.acknowledgeChangeNotice(item.eventId)),
      );
      const rejected = results.find((result) => result.status === "rejected");
      if (rejected) {
        handleError(rejected.reason, "部分变更通知确认失败，稍后会重新出现");
      }
      await refreshDueReminders();
      await Promise.all([loadCalendar(), loadUpcoming(), loadHistory(), loadRooms({ silent: true })]);
      return !rejected;
    } finally {
      setNoticeAckBusy(false);
    }
  }

  async function viewChangeNotice(item) {
    if (await acknowledgeChangeNotices([item])) openDetails(item);
  }

  async function refreshHandoverBoard() {
    try {
      setHandoverBoard(await api.getHandoverRequests());
    } catch (error) {
      handleError(error, "无法读取交接请求");
    }
  }

  async function refreshAfterHandoverChange() {
    await Promise.all([refreshDueReminders(), refreshHandoverBoard()]);
    await Promise.all([loadCalendar(), loadUpcoming(), loadHistory(), loadRooms({ silent: true })]);
  }

  async function decideHandover(requestId, decision) {
    if (noticeAckBusy) return;
    setNoticeAckBusy(true);
    try {
      if (decision === "accept") await api.acceptHandover(requestId);
      else await api.declineHandover(requestId);
      setToast(decision === "accept" ? "已接受，预约已转入您名下" : "已拒绝，预约仍归原预约者", decision === "accept" ? "success" : "info");
      await refreshAfterHandoverChange();
    } catch (error) {
      handleError(error, "交接请求处理失败，请稍后重试");
      await refreshAfterHandoverChange();
    } finally {
      setNoticeAckBusy(false);
    }
  }

  async function withdrawHandoverRequest(requestId) {
    try {
      await api.withdrawHandover(requestId);
      setToast("已撤回交接请求", "info");
      await refreshAfterHandoverChange();
    } catch (error) {
      handleError(error, "撤回交接请求失败");
    }
  }

  async function sendHandover(reservationId, toUserId) {
    if (handoverActionBusy) return;
    setHandoverActionBusy(true);
    try {
      const result = await api.createHandover(reservationId, toUserId);
      if (result.assigned) {
        setToast("已指派，预约已转入对方名下", "success");
      } else {
        setToast("交接请求已发起，等待对方确认", "success");
      }
      setDrawer(null);
      await refreshAfterHandoverChange();
    } catch (error) {
      handleError(error, "交接发起失败");
    } finally {
      setHandoverActionBusy(false);
    }
  }

  function renderHandovers() {
    const incoming = handoverBoard.incoming;
    const outgoing = handoverBoard.outgoing;
    const total = incoming.length + outgoing.length;
    const reservationSummary = (request) => `${withRelativeDay(request.reservation.date)} · ${request.reservation.start}–${request.reservation.end} · ${request.reservation.roomName}`;
    return <main className="main-canvas handover-canvas" tabIndex={0}>
      <header className="page-header handover-page-header"><div><h1>工作交接</h1><p>预约者变更需要双方明确确认，处理记录会保留在预约时间线中。</p></div></header>
      <div className="handover-page-layout">
        <section className="handover-summary" aria-label="交接概览">
          <div><span>待我确认</span><strong>{incoming.length}</strong></div>
          <div><span>我发起的</span><strong>{outgoing.length}</strong></div>
          <p>{total ? `当前共有 ${total} 条进行中的工作交接` : "当前没有进行中的工作交接"}</p>
        </section>
        <section className="handover-ledger" aria-labelledby="incoming-handover-heading">
          <header><div><span className="handover-ledger-icon neutral" aria-hidden="true"><ArrowsLeftRight size={19} /></span><div><h2 id="incoming-handover-heading">待我确认</h2><p>接受后，你将成为新的预约者。</p></div></div><strong>{incoming.length}</strong></header>
          {incoming.length ? <div className="handover-ledger-list">{incoming.map((request) => <article className="handover-ledger-row incoming" key={request.id}>
            <div className="handover-ledger-party"><span>来自 {request.fromUser.name}</span><strong>{request.reservation.partyName}</strong><small>当事人</small></div>
            <div className="handover-ledger-detail"><strong>{request.reservation.purpose}</strong><span>{reservationSummary(request)}</span></div>
            <div className="handover-ledger-actions"><button type="button" className="handover-view" onClick={() => void openDetails(request.reservation, true)}>查看预约</button><button type="button" disabled={noticeAckBusy} onClick={() => void decideHandover(request.id, "decline")}>不接受</button><button type="button" className="handover-accept" disabled={noticeAckBusy} onClick={() => void decideHandover(request.id, "accept")}>接受交接</button></div>
          </article>)}</div> : <div className="handover-ledger-empty"><CheckCircle size={28} weight="thin" /><p>没有需要你确认的交接</p></div>}
        </section>
        <section className="handover-ledger" aria-labelledby="outgoing-handover-heading">
          <header><div><span className="handover-ledger-icon muted" aria-hidden="true"><Clock size={19} /></span><div><h2 id="outgoing-handover-heading">我发起的</h2><p>对方确认前，预约仍归你。</p></div></div><strong>{outgoing.length}</strong></header>
          {outgoing.length ? <div className="handover-ledger-list">{outgoing.map((request) => <article className="handover-ledger-row outgoing" key={request.id}>
            <div className="handover-ledger-party"><span>等待 {request.toUser.name} 确认</span><strong>{request.reservation.partyName}</strong><small>当事人</small></div>
            <div className="handover-ledger-detail"><strong>{request.reservation.purpose}</strong><span>{reservationSummary(request)}</span></div>
            <div className="handover-ledger-actions"><span className="handover-waiting">处理中</span><button type="button" className="handover-view" onClick={() => void openDetails(request.reservation, true)}>查看预约</button><button type="button" onClick={() => void withdrawHandoverRequest(request.id)}>撤回申请</button></div>
          </article>)}</div> : <div className="handover-ledger-empty"><Clock size={28} weight="thin" /><p>你还没有发起交接</p></div>}
        </section>
      </div>
    </main>;
  }

  function renderMine() {
    const ownItems = upcoming.filter((booking) => booking.ownerId === currentUser.id);
    const items = ownItems.filter((booking) => bookingRooms.has(booking.roomId) && (!booking.tagId || bookingTags.has(booking.tagId)));
    const next = items[0];
    const laterItems = items.slice(1);
    const visibleLaterItems = moreBookingsOpen ? laterItems : laterItems.slice(0, 2);
    const filtersActive = bookingRooms.size !== rooms.length || bookingTags.size !== tags.length;
    const resetMineFilters = () => {
      setBookingRooms(new Set(rooms.map((room) => room.id)));
      setBookingTags(new Set(tags.map((tag) => tag.id)));
      setMoreBookingsOpen(false);
    };
    const toggleMineFilter = (value, setter) => setter((current) => {
      const nextValues = new Set(current);
      if (nextValues.has(value)) nextValues.delete(value); else nextValues.add(value);
      return nextValues;
    });
    return <main className="main-canvas bookings-canvas" tabIndex={0}><header className="page-header bookings-header"><h1>我的预约</h1><div className="filter-wrap"><button ref={bookingFilterPopover.triggerRef} className={`filter-trigger ${bookingFilterOpen ? "pressed" : ""} ${filtersActive ? "filtered" : ""}`} aria-label="筛选我的预约" aria-expanded={bookingFilterOpen} aria-haspopup="true" onClick={() => setBookingFilterOpen((open) => !open)}><SlidersHorizontal size={19} /></button>{bookingFilterOpen && <div ref={bookingFilterPopover.popoverRef} className="booking-filter-popover" role="group" aria-label="筛选我的预约"><div className="popover-heading"><span>筛选预约</span><button onClick={resetMineFilters}>重置</button></div><p>笔录室</p>{rooms.map((room) => <label key={room.id}><input type="checkbox" checked={bookingRooms.has(room.id)} onChange={() => toggleMineFilter(room.id, setBookingRooms)} /><span>{room.name}</span></label>)}<p className="filter-section-label">标签</p>{tags.map((tag) => <label className="booking-tag-filter" style={tagStyle(tag)} key={tag.id}><input type="checkbox" checked={bookingTags.has(tag.id)} onChange={() => toggleMineFilter(tag.id, setBookingTags)} /><i /><span>{tag.label}</span></label>)}<div className="filter-result-count">当前显示 {items.length} 场</div></div>}</div></header>
      <div className="bookings-layout">
        {loading.mine ? <div className="bookings-skeleton" role="status" aria-label="正在读取预约"><div className="bookings-skeleton-hero" aria-hidden="true"><i className="time" /><i className="room" /></div><div className="bookings-skeleton-rows" aria-hidden="true"><div className="bookings-skeleton-row"><i className="date" /><i className="meta" /></div><div className="bookings-skeleton-row"><i className="date" /><i className="meta" /></div></div></div> :
          next ? <button className="next-booking" onClick={() => openDetails(next)}><span className="next-booking-time"><span className="next-booking-time-value">{next.start}</span><span className="next-booking-time-separator">–</span><span className="next-booking-time-value">{next.end}</span></span><span className="next-booking-room" style={tagStyle(tagFor(next))}><i />{next.roomName}</span><CaretRight className="next-booking-caret" size={25} /></button> :
          ownItems.length ? <div className="bookings-empty"><p>没有符合条件的预约</p><button onClick={resetMineFilters}>清除筛选</button></div> : <div className="bookings-empty booking-zero-state"><CalendarBlank size={44} weight="thin" /><h2>还没有预约</h2><p>创建预约后，最近的一场显示在这里。</p><button className="empty-primary-action" onClick={openDefaultCreate}>{!activeRooms.length && permissions.manageRooms ? "管理笔录室" : "前往预约日历"}</button></div>}
        {next && <section className="later-section"><h2>之后</h2><div className="appointment-list" id="later-bookings-list">{visibleLaterItems.map((booking, index) => <button className={`appointment-row ${index >= 2 ? "revealed-row" : ""}`} key={booking.id} onClick={() => openDetails(booking)}><span className="row-date">{String(parseDate(booking.date).getMonth() + 1).padStart(2, "0")}–{String(parseDate(booking.date).getDate()).padStart(2, "0")}<small>{relativeDayLabel(booking.date, businessClock.date) || dateLabel(booking.date).split("· ")[1]}</small></span><span className="row-time">{booking.start}–{booking.end}</span><span className="row-room">{booking.roomName}</span><CaretRight className="row-caret" size={20} /></button>)}{!laterItems.length && <p className="later-empty">没有更多预约</p>}</div></section>}
        {laterItems.length > 2 && <button className={`more-bookings-button ${moreBookingsOpen ? "expanded" : ""}`} aria-expanded={moreBookingsOpen} aria-controls="later-bookings-list" onClick={() => setMoreBookingsOpen((open) => !open)}><span>{moreBookingsOpen ? "收起预约" : "更多预约"}</span><CaretRight size={18} /></button>}
      </div>
    </main>;
  }

  function moveCalendarFocus(event) {
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
    const elements = [...event.currentTarget.querySelectorAll(".slot[data-calendar-row]")];
    const target = calendarFocusTarget(
      elements.map((element) => ({
        row: Number(element.dataset.calendarRow),
        column: Number(element.dataset.calendarColumn),
        enabled: element.matches("button:not([disabled])"),
      })),
      event.target?.dataset?.calendarRow === undefined ? null : {
        row: Number(event.target.dataset.calendarRow),
        column: Number(event.target.dataset.calendarColumn),
      },
      event.key,
    );
    if (!target) return;
    const next = elements.find(
      (element) => Number(element.dataset.calendarRow) === target.row
        && Number(element.dataset.calendarColumn) === target.column,
    );
    if (!(next instanceof HTMLButtonElement) || next.disabled) return;
    event.preventDefault();
    next.focus();
  }

  function renderCalendar() {
    const roomCountClass = activeRooms.length >= 3 ? "calendar-room-count-many" : `calendar-room-count-${activeRooms.length}`;
    const calendarPending = loading.calendar || calendarDataDate !== dateKey(currentDate);
    // 抽屉打开时，为它对应的来源时段格保持一枚安静的描边标记。
    const drawerSlotMarker = !drawer ? null
      : ["create", "edit", "slot-conflict"].includes(drawer.type)
        ? { roomId: bookingForm.roomId, start: bookingForm.start, date: bookingForm.date }
        : drawer.type === "draft-relocation"
          ? { roomId: drawer.target.roomId, start: drawer.target.start, date: drawer.target.bookingDate }
          : ["details", "cancel"].includes(drawer.type) && drawer.booking
            ? { roomId: drawer.booking.roomId, start: drawer.booking.start, date: drawer.booking.date }
            : null;
    const isSlotOrigin = (roomId, start) => Boolean(drawerSlotMarker)
      && dateKey(currentDate) === drawerSlotMarker.date
      && drawerSlotMarker.roomId === roomId
      && drawerSlotMarker.start === start;
    // 临近提醒画进日历：处于提前窗口内的本人预约块长出倒计时角标。
    const upcomingReminderIds = new Set(dueReminders.upcoming.map((item) => item.id));
    const countdownFor = (booking) => {
      if (!upcomingReminderIds.has(booking.id)) return null;
      try {
        const minutes = bookingCountdownMinutes({
          date: booking.date,
          start: booking.start,
          serverDate: businessClock.date,
          serverTime: businessClock.time,
        });
        if (!(minutes > 0)) return null;
        return { label: `${Math.max(1, Math.ceil(minutes))} 分后`, urgent: minutes <= 2 };
      } catch {
        return null;
      }
    };
    const todayReminderDot = dueReminders.upcoming.length > 0 && dateKey(currentDate) !== businessClock.date;
    return <main ref={calendarCanvasRef} className="main-canvas calendar-canvas" tabIndex={0}>
      <header className="page-header calendar-header"><div><h1>预约日历</h1><p>{withRelativeDay(currentDate)}</p></div>
        <div className="header-actions calendar-toolbar"><div className="calendar-day-navigation" role="group" aria-label="日期导航"><button aria-label="前一天" disabled={networkOffline || dateKey(currentDate) <= calendarDateMinimum} onClick={() => setCurrentDate((date) => shiftDate(date, -1))}><CaretLeft size={19} /></button><button className={`calendar-nav-today ${todayReminderDot ? "has-today-dot" : ""}`} disabled={networkOffline} aria-label={todayReminderDot ? "回到今天，今天有预约即将开始" : "回到今天"} onClick={() => setCurrentDate(new Date(businessDate))}>今天<span className="calendar-nav-dot" aria-hidden="true" /></button><button aria-label="后一天" disabled={networkOffline || dateKey(currentDate) >= calendarDateMaximum} onClick={() => setCurrentDate((date) => shiftDate(date, 1))}><CaretRight size={19} /></button></div>
          <label className={`calendar-date-picker ${networkOffline ? "disabled" : ""}`}><span>{dateKey(currentDate).replaceAll("-", "/")}</span><CalendarBlank size={18} aria-hidden="true" /><input type="date" min={calendarDateMinimum} max={calendarDateMaximum} aria-label="跳转到日期" disabled={networkOffline} value={dateKey(currentDate)} onInput={(event) => { const value = event.currentTarget.value; if (value >= calendarDateMinimum && value <= calendarDateMaximum) setCurrentDate(parseDate(value)); }} /></label>
          <div className="filter-wrap"><button ref={calendarFilterPopover.triggerRef} className={"icon-button calendar-filter-trigger " + (calendarFilterOpen ? "pressed" : "") + (calendarTagFilter ? " filtered" : "")} aria-label="查看标签颜色并筛选" aria-expanded={calendarFilterOpen} aria-haspopup="true" onClick={() => setCalendarFilterOpen((open) => !open)}><FunnelSimple size={20} /></button>
            {calendarFilterOpen && <div ref={calendarFilterPopover.popoverRef} className="tag-palette-popover" role="group"><div className="popover-heading"><span>{tagEditing ? "编辑标签名称" : "标签颜色"}</span><button onClick={() => setTagEditing((editing) => !editing)}>{tagEditing ? "完成编辑" : <><PencilSimple size={13} />编辑</>}</button></div>
              {tagEditing ? <div className="tag-edit-panel tag-edit-list"><section className="tag-edit-group"><div className="tag-edit-group-heading"><span>单位标签</span><small>{role === "admin" ? "全单位通用" : "仅管理员可修改"}</small></div>{tags.filter((tag) => tag.slot <= 2).map((tag) => <label className="tag-edit-row" style={tagStyle(tag)} key={tag.id}><i /><input aria-label={`单位标签 ${tag.slot}`} value={tagDrafts[tag.id] || ""} readOnly={role !== "admin"} onChange={(event) => setTagDrafts((current) => ({ ...current, [tag.id]: event.target.value }))} /></label>)}{role === "admin" && <button className="tag-edit-save" disabled={Boolean(tagSaving)} onClick={saveGlobalTags}>{tagSaving === "global" ? "正在保存单位标签…" : "保存单位标签"}</button>}</section><section className="tag-edit-group"><div className="tag-edit-group-heading"><span>个人标签</span><small>仅影响你的预约</small></div>{tags.filter((tag) => tag.slot >= 3).map((tag) => <label className="tag-edit-row" style={tagStyle(tag)} key={tag.id}><i /><input aria-label={`个人标签 ${tag.slot}`} value={tagDrafts[tag.id] || ""} onChange={(event) => setTagDrafts((current) => ({ ...current, [tag.id]: event.target.value }))} /></label>)}<button className="tag-edit-save" disabled={Boolean(tagSaving)} onClick={savePersonalTags}>{tagSaving === "personal" ? "正在保存个人标签…" : "保存个人标签"}</button></section></div> :
                <div className="tag-palette-list"><button className={"tag-filter-option " + (!calendarTagFilter ? "active" : "")} onClick={() => setCalendarTagFilter("")}>全部</button>{tags.map((tag) => <button className={"tag-filter-option " + (calendarTagFilter === tag.id ? "active" : "")} style={tagStyle(tag)} key={tag.id} onClick={() => setCalendarTagFilter((current) => current === tag.id ? "" : tag.id)}><i /><span>{tag.label}</span></button>)}</div>}
            </div>}
          </div>
        </div>
      </header>
      {successNotice && <section className="calendar-success-notice" role="status"><CheckCircle size={20} /><p><strong>{successNotice.action}</strong><span>·</span>{successNotice.booking.roomName}<span>·</span>{successNotice.booking.start}–{successNotice.booking.end}</p><button onClick={() => openDetails(successNotice.booking)}>查看</button><button className="calendar-success-close" aria-label="关闭" onClick={() => setSuccessNotice(null)}><X size={16} /></button></section>}
      {preservedDraft && <section className="calendar-draft-notice" role="status" aria-label="待续预约草稿"><PencilSimple size={20} /><div><strong>有一份待续草稿</strong><p>{preservedDraft.partyName || "未填写预约对象"} · {preservedDraft.caseNumber || "未填写案号"} · {rooms.find((room) => room.id === preservedDraft.roomId)?.name || "原笔录室"} {preservedDraft.date} {preservedDraft.start}</p><small>选择空白时段后，系统会先确认是否迁移这份草稿。</small></div><button type="button" onClick={() => { setPreservedDraft(null); setToast("预约草稿已清除", "info"); }}>清除草稿</button></section>}
      {networkOffline && <section className="calendar-network-banner" role="status"><span className="calendar-network-icon"><WifiSlash size={18} /></span><div><strong>网络连接已断开</strong><p>当前显示最后一次成功获取的数据。</p></div><button onClick={loadCalendar}><ArrowClockwise size={16} />重新连接</button></section>}
      <section key={dateKey(currentDate)} className={`calendar-section ${roomCountClass}${calendarEnterDirection ? ` calendar-enter-${calendarEnterDirection}` : ""}`}><div className="calendar-meta"><p>{calendarPending ? "正在读取预约数据" : activeRooms.length ? dateKey(currentDate) === businessClock.date ? "已开始的时段不可预约，请选择当前时间之后的空白时段" : "选择空白时段以创建预约" : "请先启用或创建笔录室"}</p></div>
        {calendarPending && activeRooms.length ? <div className="calendar-loading-state" style={{ "--room-count": activeRooms.length }} role="status" aria-label="正在读取预约数据">
          <div className="calendar-loading-head"><span />{activeRooms.map((room) => <strong key={room.id}>{room.name}</strong>)}</div>
          {timeSlots.map(([start], rowIndex) => <div className="calendar-loading-row" key={start}><span className="calendar-loading-time">{start}</span>{activeRooms.map((room, columnIndex) => <i key={room.id} aria-hidden="true" style={{ "--skeleton-width": `${44 + ((rowIndex + columnIndex) % 4) * 10}%` }} />)}</div>)}
        </div> :
          !activeRooms.length ? <div className="calendar-zero-state"><DoorOpen size={48} weight="thin" /><div><h2>当前没有可预约的笔录室</h2><p>{permissions.manageRooms ? "请先启用或创建至少一个笔录室。" : "请联系管理员启用笔录室。"}</p>{permissions.manageRooms && <button onClick={() => navigate("rooms")}>前往笔录室管理</button>}</div></div> :
          <div className="schedule-viewport"><div className="schedule" style={{ "--room-count": activeRooms.length }} role="grid" tabIndex={0} onKeyDown={moveCalendarFocus} aria-label={dateLabel(currentDate) + "预约日历；使用方向键在时段间移动"}>
            <div className="schedule-head"><div />{activeRooms.map((room) => <div className="room-heading" key={room.id}>{room.name}</div>)}</div>
            <div className="schedule-body">{currentTimeOffset !== null && <div className="current-time-line" style={{ top: currentTimeOffset + "px" }} role="separator" aria-label={`当前时间 ${businessClock.time.slice(0, 5)}`} />}{timeSlots.map(([start, end], rowIndex) => <div className="schedule-row" key={start}><div className="time-label">{start}</div>{activeRooms.map((room, columnIndex) => {
              const booking = bookingFor(room.id, start, end);
              if (booking && booking.start !== start) return <div className="slot occupied-slot" data-calendar-row={rowIndex} data-calendar-column={columnIndex} aria-hidden="true" key={room.id + start} />;
              if (booking) {
                const tag = tagFor(booking);
                const countdown = countdownFor(booking);
                return <button className={"slot booked-slot " + (isSlotOrigin(room.id, booking.start) ? "slot-origin " : "") + (countdown ? "slot-countdown " : "") + (countdown?.urgent ? "slot-countdown-urgent " : "") + (calendarTagFilter && calendarTagFilter !== booking.tagId ? "tag-muted" : "")} data-calendar-row={rowIndex} data-calendar-column={columnIndex} style={{ ...tagStyle(tag), "--booking-span": Math.max(1, Math.round(durationFromRange(booking.start, booking.end) / Number(settings.slotMinutes || 30))) }} key={room.id + start} tabIndex={-1} onClick={() => openDetails(booking)} aria-label={room.name + " " + booking.start + "至" + booking.end + "，预约者" + (booking.owner?.name || "未知用户") + "，当事人" + booking.partyName + "，案号" + booking.caseNumber + (countdown ? "，" + countdown.label + "开始" : "")}><span className="booking-title"><i />{booking.owner?.name || "未知用户"} · 已预约</span><span className="booking-case">案号 {booking.caseNumber}</span>{countdown && <span className="booking-countdown" aria-hidden="true">{countdown.label}</span>}</button>;
              }
              const slotStarted = hasBookingStarted({ date: dateKey(currentDate), start, serverDate: businessClock.date, serverTime: businessClock.time });
              const outsideWorkHours = !isWithinWorkingHours(start, end, settings.workStart, settings.workEnd);
              const unavailable = slotStarted || outsideWorkHours;
              return <button className={`slot available-slot ${isSlotOrigin(room.id, start) ? "slot-origin" : ""} ${slotStarted ? "past-slot" : ""} ${outsideWorkHours ? "outside-work-slot" : ""}`} data-calendar-row={rowIndex} data-calendar-column={columnIndex} disabled={networkOffline || unavailable} key={room.id + start} tabIndex={-1} onClick={() => openCreate(room.id, start)} aria-label={room.name + " " + start + "至" + end + (outsideWorkHours ? " 工作时间外，不可预约" : slotStarted ? " 已开始，不可预约" : " 可预约")}><span className="slot-affordance">{outsideWorkHours ? <span>{start} · 工作时间外</span> : slotStarted ? <span>{start} · 已开始</span> : <><Plus size={18} /><span>{start} · 新建预约</span></>}</span></button>;
            })}</div>)}</div>
          </div></div>}
      </section>
    </main>;
  }

  function stepMonth(delta) {
    const [year, month] = historyMonth.split("-").map(Number);
    const nextMonth = monthKey(new Date(year, month - 1 + delta, 1));
    const earliestMonth = monthKey(new Date(businessDate.getFullYear(), businessDate.getMonth() - 11, 1));
    const latestMonth = monthKey(businessDate);
    if (nextMonth < earliestMonth || nextMonth > latestMonth) return;
    setHistoryMonth(nextMonth);
  }

  function renderHistory() {
    const history = historySections.flatMap((section) => section.items);
    const effectiveHistoryUserId = role === "admin" ? (historyScope === "mine" ? currentUser.id : historyOwner) : currentUser.id;
    const selectedOwner = users.find((user) => user.id === effectiveHistoryUserId);
    const unitHistoryTags = tags.filter((tag) => tag.slot <= 2);
    const personalHistoryTags = effectiveHistoryUserId
      ? (selectedOwner?.personalTags || (effectiveHistoryUserId === currentUser.id ? bootstrap?.personalTags : []) || []).map(normalizeTag)
      : tags.filter((tag) => tag.slot >= 3).map((tag) => ({ ...tag, label: `个人标签 ${tag.slot}` }));
    const personalTagOwnerLabel = selectedOwner ? `${selectedOwner.name}的个人标签` : "选择用户后可筛选";
    const resetHistoryFilters = () => {
      setHistoryScope("unit");
      setHistoryOwner("");
      setHistoryRoom("");
      setHistoryStatus("");
      setHistoryTag("");
      setHistoryQuery("");
    };
    const historyMonths = Array.from({ length: 12 }, (_, index) => {
      const [year, month] = monthKey(businessDate).split("-").map(Number);
      const date = new Date(year, month - 1 - index, 1);
      const id = monthKey(date);
      return { id, label: `${date.getFullYear()}年${date.getMonth() + 1}月` };
    });
    const selectedMonthLabel = historyMonths.find((month) => month.id === historyMonth)?.label || historyMonth.replace("-", "年") + "月";
    const oldestHistoryMonth = historySections.at(-1)?.id || historyMonth;
    const [oldestHistoryYear, oldestHistoryMonthNumber] = oldestHistoryMonth.split("-").map(Number);
    const previousHistoryDate = new Date(oldestHistoryYear, oldestHistoryMonthNumber - 2, 1);
    const previousHistoryMonth = {
      id: monthKey(previousHistoryDate),
      label: `${previousHistoryDate.getFullYear()}年${previousHistoryDate.getMonth() + 1}月`,
    };
    return <main className="main-canvas history-canvas" tabIndex={0}><header className="page-header history-header"><h1>预约记录</h1><div className="history-header-actions" aria-label="预约记录工具"><div className="filter-wrap"><button ref={historySearchPopover.triggerRef} className={"filter-trigger history-tool-button " + (historySearchOpen || historyQuery ? "pressed" : "")} aria-label="搜索预约记录" aria-expanded={historySearchOpen} aria-haspopup="true" onClick={() => { setHistoryFilterOpen(false); setHistorySearchOpen((open) => !open); }}><MagnifyingGlass size={21} /></button>{historySearchOpen && <div ref={historySearchPopover.popoverRef} className="history-search-popover" role="search"><MagnifyingGlass size={18} /><input autoFocus value={historyQuery} aria-label="搜索案号、当事人或笔录室" placeholder="搜索案号、当事人或笔录室" onChange={(event) => setHistoryQuery(event.target.value)} />{historyQuery && <button aria-label="清除搜索" onClick={() => setHistoryQuery("")}><X size={16} /></button>}</div>}</div><div className="filter-wrap"><button ref={historyFilterPopover.triggerRef} className={"filter-trigger history-tool-button " + (historyFilterOpen || historyRoom || historyStatus || historyTag || historyOwner || historyScope === "mine" ? "pressed" : "")} aria-label="筛选预约记录" aria-expanded={historyFilterOpen} aria-haspopup="true" onClick={() => { setHistorySearchOpen(false); setHistoryFilterOpen((open) => !open); }}><SlidersHorizontal size={20} /></button>{historyFilterOpen && <div ref={historyFilterPopover.popoverRef} className="booking-filter-popover history-filter-popover" role="group" aria-label="筛选预约记录">
      {role === "admin" ? <section className="history-filter-section history-scope-section" aria-labelledby="history-scope-heading"><h2 id="history-scope-heading">预约范围</h2><div className="history-choice-options history-scope-options" role="radiogroup" aria-label="预约范围"><label><input type="radio" name="history-scope" checked={historyScope === "unit"} onChange={() => setHistoryScope("unit")} /><span>全单位预约</span></label><label><input type="radio" name="history-scope" checked={historyScope === "mine"} onChange={() => { setHistoryScope("mine"); setHistoryOwner(""); }} /><span>仅我的预约</span></label></div>{historyScope === "unit" && <div className="history-user-filter"><label htmlFor="history-user-select">用户</label><select id="history-user-select" ref={historyUserSelectRef} value={historyOwner} onChange={(event) => { setHistoryOwner(event.target.value); if (["tag-3", "tag-4"].includes(historyTag)) setHistoryTag(""); }}><option value="">全部用户</option>{users.filter((user) => user.enabled !== false).map((user) => <option value={user.id} key={user.id}>{user.name} · {user.department}</option>)}</select><small>{historyOwner ? "正在筛选该用户的预约记录" : "可查看全单位记录，选择用户后可筛选个人标签"}</small></div>}</section> : <div className="history-employee-scope"><span>预约范围</span><strong>仅显示本人的预约记录</strong></div>}
      <section className="history-filter-section" aria-labelledby="history-status-heading"><h2 id="history-status-heading">预约状态</h2><div className="history-choice-options history-status-options" role="radiogroup" aria-label="预约状态"><label><input type="radio" name="history-status" checked={!historyStatus} onChange={() => setHistoryStatus("")} /><span>全部</span></label><label><input type="radio" name="history-status" checked={historyStatus === "active"} onChange={() => setHistoryStatus("active")} /><span>正常预约</span></label><label><input type="radio" name="history-status" checked={historyStatus === "cancelled"} onChange={() => setHistoryStatus("cancelled")} /><span>已取消</span></label></div></section>
      <section className="history-filter-section" aria-labelledby="history-room-heading"><h2 id="history-room-heading">笔录室</h2><label><input type="radio" name="history-room" checked={!historyRoom} onChange={() => setHistoryRoom("")} /><span>全部笔录室</span></label>{rooms.map((room) => <label key={room.id}><input type="radio" name="history-room" checked={historyRoom === room.id} onChange={() => setHistoryRoom(room.id)} /><span>{room.name}</span></label>)}</section>
      <section className="history-filter-section history-tag-section" aria-labelledby="history-unit-tags-heading"><h2 id="history-unit-tags-heading">单位标签 <small>全单位通用</small></h2><label><input type="radio" name="history-tag" checked={!historyTag} onChange={() => setHistoryTag("")} /><span>全部标签</span></label>{unitHistoryTags.map((tag) => <label className="booking-tag-filter" style={tagStyle(tag)} key={tag.id}><input type="radio" name="history-tag" checked={historyTag === tag.id} onChange={() => setHistoryTag(tag.id)} /><i aria-hidden="true" /><span>{tag.label}</span></label>)}</section>
      <section className={`history-filter-section history-tag-section ${!effectiveHistoryUserId ? "disabled" : ""}`} aria-labelledby="history-personal-tags-heading"><h2 id="history-personal-tags-heading">个人标签 <small>{personalTagOwnerLabel}</small></h2>{personalHistoryTags.map((tag) => <label className="booking-tag-filter" style={tagStyle(tag)} key={tag.id}><input type="radio" name="history-tag" checked={historyTag === tag.id} disabled={!effectiveHistoryUserId} onChange={() => setHistoryTag(tag.id)} /><i aria-hidden="true" /><span>{tag.label}</span></label>)}{!effectiveHistoryUserId && <p className="history-personal-helper">筛选个人标签前，请先<button type="button" onClick={() => historyUserSelectRef.current?.focus()}>选择用户</button></p>}</section>
      <footer className="history-filter-footer"><button type="button" onClick={resetHistoryFilters}><ArrowClockwise size={16} />重置筛选</button><span>共 {historyPage.total} 条记录</span></footer>
    </div>}</div></div></header>
      <div className="history-layout"><div className="history-month-nav" aria-label="历史月份"><button className="history-month-step" aria-label="上一个月" disabled={historyMonth <= historyMonths.at(-1).id} onClick={() => stepMonth(-1)}><CaretLeft size={21} /></button><button className="history-month-step" aria-label="下一个月" disabled={historyMonth >= historyMonths[0].id} onClick={() => stepMonth(1)}><CaretRight size={21} /></button><div className="history-month-select"><button ref={historyMonthPopover.triggerRef} className="history-month-button" aria-label={`选择月份，当前${selectedMonthLabel}`} aria-expanded={historyMonthOpen} aria-haspopup="true" onClick={() => setHistoryMonthOpen((open) => !open)}><span>{selectedMonthLabel}</span><CaretDown size={17} /></button>{historyMonthOpen && <div ref={historyMonthPopover.popoverRef} className="history-month-menu" role="group" aria-label="可选月份">{historyMonths.map((month) => <button className={month.id === historyMonth ? "selected" : ""} aria-pressed={month.id === historyMonth} key={month.id} onClick={() => { setHistoryMonth(month.id); historyMonthPopover.closeAndRestoreFocus(); }}>{month.label}</button>)}</div>}</div><span className="history-count">{historyPage.total} 场</span></div>
        <section className="history-list">{loading.history ? <div className="history-skeleton" role="status" aria-label="正在读取预约记录">{[0, 1, 2, 3, 4].map((row) => <div className="history-skeleton-row" aria-hidden="true" key={row}><span className="day" /><span className="body"><i className="line" /><i className="case" /></span></div>)}</div> : history.length ? <>{historySections.map((section, sectionIndex) => <div className="history-month-section" key={section.id}>{sectionIndex > 0 && <div className="history-month-divider" role="separator">{section.label}</div>}{section.items.map((booking) => <button className={`history-row ${booking.status === "cancelled" ? "cancelled" : ""}`} key={booking.id} onClick={() => openDetails(booking, true)}><span className="history-date-anchor"><strong>{String(parseDate(booking.date).getDate()).padStart(2, "0")}</strong><small>{relativeDayLabel(booking.date, businessClock.date) || dateLabel(booking.date).split("· ")[1]}</small></span><span className="history-booking-summary"><strong><span className="history-time">{booking.start}–{booking.end}</span><i aria-hidden="true">·</i><span className="history-room">{booking.roomName}</span></strong><small>{booking.caseNumber}</small></span><span className={`history-row-end ${booking.status === "cancelled" ? "cancelled" : ""}`} style={tagStyle(tagFor(booking))}><span className="history-statuses">{booking.status === "cancelled" && <span className="history-cancelled-status">已取消</span>}{booking.handoverState && <span className={`history-handover-status ${booking.handoverState}`}>{booking.handoverState === "pending" ? "交接中" : "已交接"}</span>}{booking.status !== "cancelled" && !booking.handoverState && <i className="history-tag-dot" aria-hidden="true" />}</span><CaretRight size={23} /></span></button>)}{section.nextCursor && <button className="history-more" type="button" disabled={Boolean(historyLoadingMore)} onClick={() => loadMoreHistory(section)}>{historyLoadingMore === section.id ? <><CircleNotch className="spin" size={17} />正在加载</> : `加载更多 · 已显示 ${section.items.length} / ${section.total}`}</button>}</div>)}</> : <div className="history-empty history-zero-state"><ClockCounterClockwise size={42} weight="thin" /><h2>这个月还没有预约记录</h2><p>切换月份，或调整搜索和筛选条件。</p></div>}</section>
        {!loading.history && <button className="more-bookings-button history-more" type="button" disabled={Boolean(historyLoadingMore)} onClick={loadPreviousHistoryMonth}>{historyLoadingMore === previousHistoryMonth.id ? <><CircleNotch className="spin" size={17} /><span>正在加载{previousHistoryMonth.label}</span></> : <><span>加载{previousHistoryMonth.label}的记录</span><CaretRight size={18} /></>}</button>}
      </div>
    </main>;
  }

  function openRoom(room = null) {
    setDrawer({ type: room ? "room-edit" : "room-create", room, errors: {}, form: room ? { name: room.name, sortOrder: Number(room.sortOrder || 1), isActive: room.isActive !== false, showOnDisplay: room.showOnDisplay !== false } : { name: "", sortOrder: rooms.length + 1, isActive: true, showOnDisplay: true } });
  }

  function updateDrawerField(field, value) {
    setDrawer((current) => {
      if (!current) return current;
      const errors = { ...(current.errors || {}) };
      delete errors[field];
      return { ...current, errors, form: { ...current.form, [field]: value } };
    });
  }

  function showDrawerErrors(errors) {
    setDrawer((current) => current ? { ...current, errors } : current);
    window.requestAnimationFrame(() => document.querySelector('.booking-drawer [aria-invalid="true"]')?.focus());
  }

  async function saveRoom(event) {
    event.preventDefault();
    const errors = validateRoomAdminForm(drawer.form);
    if (Object.keys(errors).length) {
      showDrawerErrors(errors);
      return;
    }
    try {
      if (drawer.type === "room-edit") await api.updateRoom(drawer.room.id, drawer.form);
      else await api.createRoom(drawer.form);
      setDrawer(null); setToast("笔录室已保存", "success"); await loadBootstrap();
    } catch (error) {
      const fields = adminApiFieldErrors(error);
      if (fields) showDrawerErrors(fields);
      else handleError(error, "保存笔录室失败");
    }
  }

  function roomDeletionDrawer(room, impact) {
    const bookings = unwrapItems(impact);
    return Number(impact?.total || 0) > 0
      ? { type: "room-delete-blocked", room, bookings, total: Number(impact.total) }
      : { type: "room-delete-confirm", room };
  }

  async function refreshRoomDeletionFlow(room, fallback = null) {
    try {
      const impact = await api.getRoomDeletionImpact(room.id);
      setDrawer(roomDeletionDrawer(room, impact));
      return true;
    } catch (error) {
      if (fallback) setDrawer(fallback);
      handleError(error, "无法重新检查笔录室预约");
      return false;
    }
  }

  async function requestRoomDeletion() {
    if (drawer?.type !== "room-edit") return;
    const room = drawer.room;
    setRoomDeleteBusy(true);
    try {
      const impact = await api.getRoomDeletionImpact(room.id);
      setDrawer(roomDeletionDrawer(room, impact));
    } catch (error) {
      handleError(error, "无法检查笔录室预约");
    } finally {
      setRoomDeleteBusy(false);
    }
  }

  async function confirmRoomDeletion() {
    if (drawer?.type !== "room-delete-confirm") return;
    const room = drawer.room;
    setRoomDeleteBusy(true);
    try {
      await api.deleteRoom(room.id);
      setDrawer(null);
      setToast("笔录室已删除", "success");
      await loadBootstrap();
    } catch (error) {
      if (error.code === "ROOM_HAS_FUTURE_BOOKINGS") {
        setDrawer({
          type: "room-delete-blocked",
          room,
          bookings: error.conflicts || [],
          total: error.current?.total || error.conflicts?.length || 0,
        });
      } else {
        handleError(error, "删除笔录室失败");
      }
    } finally {
      setRoomDeleteBusy(false);
    }
  }

  function renderRooms() {
    const orderedRooms = [...rooms].sort((a, b) => Number(a.sortOrder) - Number(b.sortOrder));
    const roomCountClass = orderedRooms.length >= 3 ? "room-count-many" : `room-count-${orderedRooms.length}`;
    return <main className="main-canvas rooms-canvas" tabIndex={0}><header className="page-header rooms-header"><div><h1>笔录室</h1><p>管理预约日历中可使用的笔录室</p></div><button className="room-create-button" aria-label="添加笔录室" data-tooltip="添加笔录室" onClick={() => openRoom()}><Plus size={22} /></button></header>
      {orderedRooms.length ? <section className={`room-overview ${roomCountClass}`}>{orderedRooms.map((room) => <article className="room-column" key={room.id}><div className="room-column-heading"><span className="room-sort-order">{String(room.sortOrder || 0).padStart(2, "0")}</span><h2><button className="room-title-button" onClick={() => openRoom(room)}><span>{room.name}</span><CaretRight size={22} /></button></h2><span className={"room-state " + (room.isActive !== false ? "active" : "inactive")}><i />{room.isActive !== false ? "启用" : "停用"}</span></div><dl className="room-metrics"><div><dt><CalendarBlank size={20} />今天</dt><dd>{room.todayCount || 0} 场</dd></div><div><dt><ClockCounterClockwise size={20} />未来</dt><dd>{room.futureCount || 0} 场</dd></div></dl><div className="room-next-booking"><span>下一场</span><strong>{room.nextBooking || "暂无安排"}</strong></div></article>)}</section> : <section className="rooms-empty"><DoorOpen size={42} weight="thin" /><p>尚未创建笔录室</p><button onClick={() => openRoom()}>创建第一个笔录室</button></section>}
    </main>;
  }

  function openUser(user = null) {
    setDrawer({ type: user ? "user-edit" : "user-create", user, errors: {}, form: user ? { name: user.name, username: user.username, department: user.department, role: user.role, enabled: user.enabled !== false, password: "" } : { name: "", username: "", department: "", role: "employee", enabled: true, password: "" } });
  }

  async function saveUser(event) {
    event.preventDefault();
    const errors = validateUserAdminForm(drawer.form, { creating: drawer.type === "user-create" });
    if (Object.keys(errors).length) {
      showDrawerErrors(errors);
      return;
    }
    try {
      if (drawer.type === "user-edit") await api.updateUser(drawer.user.id, { name: drawer.form.name.trim(), department: drawer.form.department.trim(), role: drawer.form.role, enabled: drawer.form.enabled });
      else await api.createUser({ ...drawer.form, name: drawer.form.name.trim(), username: drawer.form.username.trim(), department: drawer.form.department.trim() });
      setDrawer(null); setToast("用户已保存", "success"); await loadBootstrap();
    } catch (error) {
      const fields = adminApiFieldErrors(error);
      if (fields) showDrawerErrors(fields);
      else handleError(error, error.code === "LAST_ADMIN_REQUIRED" ? "必须保留至少一名启用管理员" : "保存用户失败");
    }
  }

  async function resetPassword(event) {
    event.preventDefault();
    const errors = validatePasswordReset(drawer.form.password);
    if (Object.keys(errors).length) {
      showDrawerErrors(errors);
      return;
    }
    try {
      const result = await api.resetUserPassword(drawer.user.id, drawer.form.password);
      setDrawer(null);
      if (result?.reauthenticate) {
        setToast("密码已重置，请使用新密码重新登录", "success");
        expireSession();
      } else {
        setToast("密码已重置", "success");
      }
    }
    catch (error) {
      if (error.fields) showDrawerErrors(error.fields);
      else handleError(error, "重置密码失败");
    }
  }

  function renderUsers() {
    const query = userQuery.trim().toLocaleLowerCase("zh-CN");
    const visibleUsers = users.filter((user) => !query || [user.name, user.username, user.department].some((value) => String(value || "").toLocaleLowerCase("zh-CN").includes(query)));
    return <main className="main-canvas users-canvas" tabIndex={0}><header className="page-header users-header"><div><h1>用户管理</h1><p>管理可登录系统的用户与权限</p></div><div className="users-header-actions"><div ref={userSearchPopover.popoverRef} className={`user-search ${userSearchOpen ? "open" : ""}`}>{userSearchOpen && <input autoFocus type="search" value={userQuery} placeholder="搜索姓名、用户名或部门" aria-label="搜索用户" onChange={(event) => setUserQuery(event.target.value)} />}<button ref={userSearchPopover.triggerRef} type="button" aria-label={userSearchOpen ? "关闭搜索" : "搜索用户"} aria-expanded={userSearchOpen} aria-haspopup="true" onClick={() => { if (userSearchOpen && userQuery) setUserQuery(""); setUserSearchOpen((open) => !open); }}>{userSearchOpen ? <X size={19} /> : <MagnifyingGlass size={20} />}</button></div><button className="users-create-button" onClick={() => openUser()}><span className="users-create-icon"><Plus size={18} /></span>新建用户</button></div></header>
      <section className="user-roster"><div className="user-roster-heading"><span>用户</span><span>部门</span><span>角色</span><span>状态</span></div><div className="user-roster-rows">{visibleUsers.map((user) => <button className="user-roster-row" key={user.id} onClick={() => openUser(user)}><span className="user-primary"><strong>{user.name}</strong><small>{user.username}</small></span><span className="user-department">{user.department}</span><span className={"user-role " + user.role}>{user.role === "admin" ? "管理员" : "普通员工"}</span><span className={"user-status " + (user.enabled !== false ? "enabled" : "disabled")}><i />{user.enabled !== false ? "启用" : "已停用"}</span><CaretRight className="user-row-edit" size={17} /></button>)}</div></section>
    </main>;
  }

  async function loadSystem(silent = false) {
    if (!silent) setSystemLoading(true);
    try { setSystem(await api.getSystem()); }
    catch (error) { handleError(error, "读取系统状态失败"); }
    finally { if (!silent) setSystemLoading(false); }
  }

  async function saveSystemSettings(event) {
    event.preventDefault();
    if (drawer?.type !== "system-settings" || systemSettingsSaving) return;
    const errors = validateSystemSettingsForm(drawer.form, settings.slotMinutes);
    if (Object.keys(errors).length) {
      showDrawerErrors(errors);
      return;
    }
    setSystemSettingsSaving(true);
    try {
      const saved = await api.updateSystemSettings({
        workStart: drawer.form.workStart,
        workEnd: drawer.form.workEnd,
      });
      setBootstrap((current) => ({ ...current, settings: saved }));
      setSystem((current) => current ? { ...current, workStart: saved.workStart, workEnd: saved.workEnd } : current);
      await Promise.all([loadSystem(true), loadAudit({ silent: true })]);
      setDrawer(null);
      setToast("工作时间已更新，已有预约保持不变", "success");
    } catch (error) {
      if (error?.fields && Object.keys(error.fields).length) showDrawerErrors(error.fields);
      else handleError(error, "更新工作时间失败");
    } finally {
      setSystemSettingsSaving(false);
    }
  }

  async function loadAudit({ append = false, cursor = "", silent = false, preserveLoaded = false } = {}) {
    const requestNumber = auditRequestRef.current + 1;
    auditRequestRef.current = requestNumber;
    if (append) setAuditLoadingMore(true);
    else if (!silent) setAuditLoading(true);
    try {
      const hidden = auditHiddenRef.current;
      const result = await api.getAudit({
        pageSize: 50,
        cursor,
        action: hidden ? "" : auditFilters.action.trim(),
        outcome: hidden ? "" : auditFilters.outcome.trim(),
        actorId: hidden ? "" : auditFilters.actorId,
        targetType: hidden ? "" : auditFilters.targetType.trim(),
        targetId: hidden ? "" : auditFilters.targetId.trim(),
        dateFrom: hidden ? "" : toApiTimestamp(auditFilters.dateFrom),
        dateTo: hidden ? "" : toApiTimestamp(auditFilters.dateTo),
      });
      if (auditRequestRef.current !== requestNumber) return;
      const nextItems = unwrapItems(result);
      const hadKnownItems = knownAuditIdsRef.current.size > 0;
      const newlyReceived = nextItems.filter((item) => !knownAuditIdsRef.current.has(item.id));
      nextItems.forEach((item) => knownAuditIdsRef.current.add(item.id));
      if (hadKnownItems && auditHiddenRef.current && !append && newlyReceived.length) {
        setAuditUnreadCount((current) => current + newlyReceived.length);
      }
      setAuditItems((current) => {
        const combined = append ? [...current, ...nextItems] : preserveLoaded ? [...nextItems, ...current] : nextItems;
        return [...new Map(combined.map((item) => [item.id, item])).values()];
      });
      setAuditPage((current) => ({ nextCursor: preserveLoaded && current.nextCursor ? current.nextCursor : result?.nextCursor || null, pageSize: Number(result?.pageSize || 50), total: Number(result?.total || nextItems.length) }));
    } catch (error) { if (auditRequestRef.current === requestNumber) handleError(error, "读取安全审计失败"); }
    finally {
      if (auditRequestRef.current === requestNumber) {
        if (append) setAuditLoadingMore(false);
        else if (!silent) setAuditLoading(false);
      }
    }
  }

  async function loadTokens(silent = false) {
    try { setTokens(unwrapItems(await api.getTokens())); }
    catch (error) {
      if (!silent || error?.status === 401 || error?.code === "SYSTEM_RECOVERY_REQUIRED") handleError(error, "读取接口令牌失败");
    }
  }

  async function createBackup() {
    try {
      const result = await api.createBackup();
      setToast("备份已完成 · 序号 " + result.sequence, "success");
      await Promise.all([loadSystem(true), loadAudit({ silent: true })]);
    } catch (error) { handleError(error, "备份失败"); }
  }

  async function checkForUpdate() {
    if (updateChecking) return;
    setUpdateChecking(true);
    try {
      const result = await api.checkForUpdate();
      setSystem((current) => current ? { ...current, updateCheck: result } : current);
      if (result?.status === "available") setToast(`有新版本 ${result.latestVersion} 可用，可在下方打开发布页`, "info");
      else if (result?.status === "current") setToast("已是最新版本", "info");
      else setToast("暂时无法检查更新，请稍后再试", "error");
    } catch (error) {
      handleError(error, "暂时无法检查更新");
    } finally {
      setUpdateChecking(false);
    }
  }

  async function downloadDiagnostics() {
    try {
      const diagnostic = await api.getDiagnostics();
      const blob = new Blob([JSON.stringify(diagnostic, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      const stamp = String(diagnostic.generatedAtUtc || "diagnostic").replace(/[:.]/g, "-");
      anchor.href = url; anchor.download = `meeting-room-diagnostic-${stamp}.json`; anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setToast("脱敏诊断信息已导出 · " + formatLocalDateTime(diagnostic.generatedAtUtc), "success");
    } catch (error) { handleError(error, "导出诊断失败"); }
  }

  async function copyLanAddress() {
    if (!system?.lanAddress || system.bindMode !== "lan") return;
    try {
      await copyText(system.lanAddress);
      setToast("局域网地址已复制，可以直接发送给员工", "success");
    } catch {
      setToast("无法自动复制，请选中局域网地址后手动复制", "error");
    }
  }

  async function createIntegrationToken(event) {
    event.preventDefault();
    if (!drawer.form.name.trim() || !drawer.form.scopes.length) return;
    try {
      const created = await api.createToken({ name: drawer.form.name.trim(), scopes: drawer.form.scopes, expiresAt: drawer.form.expiresAt ? toApiTimestamp(drawer.form.expiresAt) : null });
      setDrawer({ type: "token-created", token: created });
      await Promise.all([loadTokens(true), loadAudit({ silent: true })]);
    } catch (error) { handleError(error, "创建接口令牌失败"); }
  }

  async function revokeIntegrationToken() {
    if (drawer?.type !== "token-revoke") return;
    const token = drawer.token;
    setTokenRevokingId(token.id);
    try {
      await api.revokeToken(token.id);
      await Promise.all([loadTokens(true), loadAudit({ silent: true })]);
      setDrawer(null);
      setToast("接口令牌已撤销", "success");
    } catch (error) { handleError(error, "撤销接口令牌失败"); }
    finally { setTokenRevokingId(""); }
  }

  function renderSystem() {
    const healthy = system?.health === "healthy";
    const services = system?.services || [];
    const shareableLanAddress = system?.bindMode === "lan" && Boolean(system?.lanAddress);
    const updateAuditFilter = (field, value) => {
      setAuditPage((current) => ({ ...current, nextCursor: null }));
      setAuditFilters((current) => ({ ...current, [field]: value }));
    };
    const toggleAuditVisibility = () => {
      const nextHidden = !auditHidden;
      auditHiddenRef.current = nextHidden;
      setAuditHidden(nextHidden);
      setAuditFilterOpen(false);
      if (!nextHidden) {
        setAuditUnreadCount(0);
        loadAudit();
      }
    };
    return <main className="main-canvas system-canvas" tabIndex={0}><header className="page-header system-header"><div><h1>系统状态</h1><p>查看本机服务、局域网连接与安全审计</p></div><div className="system-header-actions"><button className="system-recheck-inline" disabled={systemLoading} onClick={() => Promise.all([loadSystem(), loadAudit(), loadTokens()])}><ArrowClockwise className={systemLoading ? "spin" : ""} size={18} />{systemLoading ? "正在刷新" : "立即刷新"}</button><button className="system-export-button" onClick={downloadDiagnostics}><DownloadSimple size={20} /><span>导出诊断信息</span></button></div></header>
      <div className="system-status-content"><section className={"system-health-summary " + (system ? healthy ? "normal" : "warning" : "normal")}><span className="system-health-dot" /><div><h2>{system?.label || (system ? "系统需要注意" : "正在检查系统")}</h2><p>最后检查：{formatLocalDateTime(system?.lastCheckedAt)} · 每 30 秒自动刷新</p></div></section>
        <section className="system-status-group"><h2>运行环境</h2><div className="system-status-list">{[["程序版本", system?.productVersion || bootstrap.productVersion], ["数据库版本", system?.databaseVersion || "—"], ["局域网地址", system?.lanAddress || "—"], ["服务端口", system?.servicePort || "—"], ["服务范围", system?.bindMode === "lan" ? "局域网" : system?.bindMode === "loopback" ? "仅本机" : "—"], ["数据序号", system?.dataSequence ?? "—"], ["备份序号", system?.backupSequence ?? "—"]].map(([label, value]) => <div className="system-status-row" key={label}><span>{label}</span><span className={`system-status-value ${label === "局域网地址" ? "system-lan-address" : ""}`}><strong>{value}</strong>{label === "程序版本" && system?.updateCheck?.status === "available" && <span className="system-update-badge">{`有新版本 ${system.updateCheck.latestVersion}`}</span>}{label === "局域网地址" && shareableLanAddress && <button type="button" className="system-copy-address" aria-label="复制局域网地址" onClick={copyLanAddress}><CopySimple size={15} />复制</button>}</span><span /></div>)}{system?.updateCheck?.enabled && <div className="system-status-row system-update-row"><span>软件更新</span><span className="system-status-value system-update-state">{updateChecking ? <strong>正在检查更新…</strong> : system?.updateCheck?.status === "available" ? <a href={system.updateCheck.releaseUrl} target="_blank" rel="noreferrer">{`有新版本 ${system.updateCheck.latestVersion} · 查看发布页`}</a> : system?.updateCheck?.status === "current" ? <strong>已是最新版本</strong> : system?.updateCheck?.lastCheckedAtUtc ? <strong>暂时无法确认版本</strong> : <strong>尚未检查更新</strong>}<button type="button" className="system-update-check" disabled={updateChecking} onClick={checkForUpdate}><ArrowClockwise className={updateChecking ? "spin" : ""} size={15} />{updateChecking ? "检查中" : "检查更新"}</button></span><span /></div>}<button type="button" className="system-status-row system-action-row system-work-hours-row" onClick={() => setDrawer({ type: "system-settings", errors: {}, form: { workStart: system?.workStart || settings.workStart, workEnd: system?.workEnd || settings.workEnd } })}><span>工作时间</span><strong>{system?.workStart || settings.workStart}–{system?.workEnd || settings.workEnd}</strong><CaretRight size={17} /></button><button type="button" className="system-status-row system-action-row system-backup-row" onClick={() => setDrawer({ type: "backup" })}><span>最近备份</span><strong>{system?.lastBackupAt ? `${formatLocalDateTime(system.lastBackupAt)} · ${system.backupCaughtUp ? "已追平" : "待备份"}` : "尚无备份"}</strong><CaretRight size={17} /></button></div></section>
        <section className="system-status-group system-service-group"><h2>服务连接</h2><div className="system-status-list">{services.map((service) => <div className={"system-status-row system-service-row " + (service.status || "")} key={service.id || service.label}><span>{service.label}</span><strong><i />{service.value || service.status}</strong><span /></div>)}</div></section>
        <section className="system-status-group system-token-group"><div className="system-section-heading"><div><h2>只读接口令牌</h2><p>令牌明文仅在创建成功时显示一次</p></div><button onClick={() => setDrawer({ type: "token-create", form: { name: "", scopes: ["rooms:read"], expiresAt: "" } })}><Plus size={17} />新建令牌</button></div><div className="system-token-list">{tokens.length ? tokens.map((token) => <div className={"system-token-row " + (token.revokedAt ? "revoked" : "")} key={token.id}><span><strong>{token.name}</strong><small>{token.prefix}… · {token.scopes.join("、")}</small></span><span><small>{token.revokedAt ? "已撤销 " + formatLocalDateTime(token.revokedAt) : token.expiresAt ? "到期 " + formatLocalDateTime(token.expiresAt) : "长期有效"}</small>{!token.revokedAt && <button disabled={tokenRevokingId === token.id} onClick={() => setDrawer({ type: "token-revoke", token })}>{tokenRevokingId === token.id ? "正在撤销" : "撤销"}</button>}</span></div>) : <p className="system-empty-copy">尚未创建接口令牌</p>}</div></section>
        <section className={`system-status-group system-audit-group ${auditHidden ? "is-hidden" : ""}`}>
          <div className="system-section-heading system-audit-heading"><div><h2>安全审计{auditUnreadCount > 0 && <span className="system-audit-unread" aria-label={`${auditUnreadCount} 条未查看安全信息`}>{auditUnreadCount > 99 ? "99+" : auditUnreadCount}</span>}</h2><p>共 {auditPage.total} 条 · 时间显示已换算为本地时区</p></div><div className="system-audit-heading-actions"><button onClick={toggleAuditVisibility}>{auditHidden ? <Eye size={17} /> : <EyeSlash size={17} />}{auditHidden ? "显示" : "隐藏"}</button><button ref={auditFilterPopover.triggerRef} aria-expanded={auditFilterOpen} aria-haspopup="true" disabled={auditHidden} onClick={() => setAuditFilterOpen((open) => !open)}><SlidersHorizontal size={17} />筛选</button></div></div>
          {!auditHidden && <>{auditFilterOpen && <div ref={auditFilterPopover.popoverRef} className="system-audit-filters"><label><span>动作</span><select value={auditFilters.action} onChange={(event) => updateAuditFilter("action", event.target.value)}><option value="">全部动作</option>{AUDIT_ACTION_OPTIONS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label><span>结果</span><select value={auditFilters.outcome} onChange={(event) => updateAuditFilter("outcome", event.target.value)}><option value="">全部结果</option>{AUDIT_OUTCOME_OPTIONS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label><span>操作人</span><select value={auditFilters.actorId} onChange={(event) => updateAuditFilter("actorId", event.target.value)}><option value="">全部人员</option>{users.map((user) => <option value={user.id} key={user.id}>{user.name}</option>)}</select></label><label><span>对象类型</span><select value={auditFilters.targetType} onChange={(event) => updateAuditFilter("targetType", event.target.value)}><option value="">全部类型</option>{AUDIT_TARGET_OPTIONS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label><span>对象标识</span><input value={auditFilters.targetId} onChange={(event) => updateAuditFilter("targetId", event.target.value)} /></label><label><span>开始时间</span><input type="datetime-local" value={auditFilters.dateFrom} onChange={(event) => updateAuditFilter("dateFrom", event.target.value)} /></label><label><span>结束时间</span><input type="datetime-local" value={auditFilters.dateTo} onChange={(event) => updateAuditFilter("dateTo", event.target.value)} /></label><button className="system-audit-reset" onClick={() => setAuditFilters({ action: "", outcome: "", actorId: "", targetType: "", targetId: "", dateFrom: "", dateTo: "" })}>清除筛选</button></div>}<div className="system-audit-list" aria-busy={auditLoading}>{auditLoading ? <p className="system-empty-copy"><CircleNotch className="spin" size={18} />正在读取审计记录</p> : auditItems.length ? auditItems.map((item) => { const outcome = item.details?.result || item.details?.reason; return <article className="system-audit-row" key={item.id}><time>{formatLocalDateTime(item.occurredAtUtc)}</time><span><strong>{auditActionLabel(item.action)}</strong><small>{auditTargetTypeLabel(item.targetType)}操作</small></span><span><strong>{item.actor?.name || "系统"}</strong><small>{auditTargetTypeLabel(item.targetType)}{item.targetId ? " · " + item.targetId : ""}</small></span><em>{auditOutcomeLabel(outcome)}</em></article>; }) : <p className="system-empty-copy">当前筛选条件下没有审计记录</p>}{auditPage.nextCursor && <button className="system-audit-more" disabled={auditLoadingMore} onClick={() => loadAudit({ append: true, cursor: auditPage.nextCursor })}>{auditLoadingMore ? "正在加载…" : `加载更多 · 已显示 ${auditItems.length} / ${auditPage.total}`}</button>}</div></>}
        </section>
      </div>
    </main>;
  }

  async function savePreferences(event) {
    event.preventDefault();
    if (preferencesSaving) return;
    const submitted = { name: currentUser.name, department: currentUser.department, ...preferencesDraft };
    if (!submitted.name?.trim()) {
      setPreferencesErrors({ name: "请输入姓名" });
      window.requestAnimationFrame(() => document.querySelector('[name="profile-name"]')?.focus({ preventScroll: true }));
      return;
    }
    setPreferencesErrors({});
    setPreferencesSaving(true);
    try {
      const saved = await api.updatePreferences({ ...submitted, name: submitted.name.trim() });
      setBootstrap((current) => ({
        ...current,
        currentUser: saved.profile || current.currentUser,
        preferences: saved.preferences || saved,
        personalTags: saved.personalTags || current.personalTags,
      }));
      setPreferencesDraft(saved.preferences || saved);
      if (saved.personalTags) {
        setTagDrafts((current) => ({
          ...current,
          ...Object.fromEntries(saved.personalTags.map((tag) => [tag.id, tag.label])),
        }));
      }
      const nextUiPreferences = writeUiPreferences(currentUser.id, uiPreferencesDraft);
      setUiPreferencesDraft(nextUiPreferences);
      setToast("个人设置已保存", "success");
    } catch (error) {
      if (error?.fields) setPreferencesErrors(error.fields);
      handleError(error, "保存个人设置失败");
    } finally {
      setPreferencesSaving(false);
    }
  }

  function renderSettings() {
    const draft = preferencesDraft || {};
    const update = (field, value) => {
      setPreferencesDraft((current) => ({ ...current, [field]: value }));
      if (preferencesErrors[field]) setPreferencesErrors((current) => ({ ...current, [field]: "" }));
    };
    const updateUi = (field, value) => setUiPreferencesDraft((current) => ({ ...current, [field]: value }));
    return <PersonalCenter activeRooms={activeRooms} currentUser={currentUser} draft={{ name: currentUser.name, department: currentUser.department, ...draft }} durationSteps={DURATION_STEPS} errors={preferencesErrors} onChange={update} onLogout={logout} onSave={savePreferences} onUiChange={updateUi} saving={preferencesSaving} tags={tags} uiDraft={uiPreferencesDraft} />;
  }

  function renderDataCenter() {
    return <DataCenter businessDate={businessClock.date} currentUser={currentUser} globalTags={bootstrap?.globalTags || []} onError={handleError} personalTags={bootstrap?.personalTags || []} permissions={permissions} role={role} rooms={rooms} settings={settings} users={users} />;
  }

  function renderUnauthorized() {
    return <main className="main-canvas unauthorized-canvas" tabIndex={0}><header className="page-header unauthorized-header"><div><h1>受限页面</h1><p>当前账户权限不足</p></div></header><section className="unauthorized-state" role="alert"><span className="unauthorized-icon"><LockSimple size={36} /></span><h2>无权限访问此页面</h2><p>{unauthorizedMessage || "当前账户没有访问该页面的权限。"}</p><button type="button" onClick={() => navigate("mine")}>返回我的预约</button></section></main>;
  }

  function drawerHeading() {
    return { create: "新建预约", edit: "修改预约", details: "预约详情", cancel: "取消预约", "slot-conflict": "时段已被占用", "draft-relocation": "确认使用草稿", "handover": "交接预约", "room-create": "添加笔录室", "room-edit": "管理笔录室", "room-delete-confirm": "删除笔录室", "room-delete-blocked": "需要先处理预约", "user-create": "新建用户", "user-edit": "编辑用户", "user-reset": "重置密码", "system-settings": "修改工作时间", backup: "最近备份", "token-create": "新建接口令牌", "token-created": "令牌已创建", "token-revoke": "撤销接口令牌" }[drawer?.type] || "";
  }

  function renderDrawer() {
    if (!drawer) return null;
    if (!isDrawerAllowed(drawer.type, permissions)) return null;
    const enabledAdminCount = users.filter((user) => user.role === "admin" && user.enabled !== false).length;
    const lastAdminProtected = drawer.type === "user-edit"
      && drawer.user.role === "admin"
      && drawer.user.enabled !== false
      && enabledAdminCount <= 1;
    if (drawer.type === "create" || drawer.type === "edit") return <BookingForm form={bookingForm} setForm={setBookingForm} errors={bookingErrors} rooms={rooms} tags={drawer.type === "edit" ? editTags : tags} settings={settings} maximumDuration={bookingMaximumDuration} editing={drawer.type === "edit"} busy={saveState === "saving"} failure={saveState === "failed"} conflict={conflict} conflictCheck={conflictCheck} onSubmit={saveBooking} onFieldChange={(field) => setBookingErrors((current) => { if (!current[field]) return current; const next = { ...current }; delete next[field]; return next; })} onDismissFailure={() => setSaveState("idle")} onContinueDraft={() => { setConflict(null); setConflictCheck({ busy: false, message: "" }); }} onRecheckConflict={recheckRevisionConflict} onUseLatest={() => {
      const latest = conflict?.current;
      if (!latest) return;
      setBookingForm({ roomId: latest.roomId, date: latest.date, start: latest.start, duration: durationFromRange(latest.start, latest.end), partyName: latest.partyName, caseNumber: latest.caseNumber, purpose: latest.purpose, notes: latest.notes || "", tagId: latest.tagId });
      setDrawer((current) => ({ type: "edit", booking: latest, returnTo: current?.returnTo || null })); setConflict(null); setConflictCheck({ busy: false, message: "" });
    }} />;
    if (drawer.type === "slot-conflict") return <div className="booking-conflict-resolution"><div className="booking-conflict-scroll"><div className="booking-conflict-copy"><span className="booking-conflict-icon"><WarningCircle size={34} /></span><h2>预约刚被别人占用</h2><p>{bookingForm.roomId && rooms.find((room) => room.id === bookingForm.roomId)?.name} · {bookingForm.start}–{endFromDuration(bookingForm.start, bookingForm.duration)}</p></div><dl className="booking-conflict-draft"><div><dt><CheckCircle size={18} /></dt><dd>你填写的预约对象、案号、事项、标签和备注都已保留，背景日历已更新。</dd></div></dl></div><div className="booking-conflict-actions"><button className="submit-button" disabled={conflictCheck.busy} onClick={() => { setPreservedDraft(bookingForm); setDrawer(null); setToast("草稿已保留，请选择新的空白时段", "info"); }}>返回日历重新选择</button><button className="secondary-button" disabled={conflictCheck.busy} onClick={() => { setPreservedDraft(bookingForm); setDrawer(null); }}>保留草稿并关闭</button><button className="booking-conflict-recheck" disabled={conflictCheck.busy} onClick={recheckSlotConflict}><ArrowClockwise className={conflictCheck.busy ? "spin" : ""} size={16} />{conflictCheck.busy ? "正在重新检查" : "重新检查这个时段"}</button><p className={`booking-conflict-check-result ${conflictCheck.message ? "visible" : ""}`} role="status">{conflictCheck.message}</p></div></div>;
    if (drawer.type === "draft-relocation") {
      const originalRoom = rooms.find((room) => room.id === drawer.draft.roomId)?.name || "原笔录室";
      const targetRoom = rooms.find((room) => room.id === drawer.target.roomId)?.name || "新笔录室";
      return <div className="booking-draft-relocation"><div className="booking-draft-relocation-copy"><span><PencilSimple size={30} /></span><h2>要把保留的草稿移到这里吗？</h2><p>系统不会直接套用旧案号，请先确认本次预约对象和时段。</p></div><dl><div><dt>草稿内容</dt><dd><strong>{drawer.draft.partyName || "未填写预约对象"}</strong><span>{drawer.draft.caseNumber || "未填写案号"} · {drawer.draft.purpose || "未填写事项"}</span></dd></div><div><dt>原时段</dt><dd>{originalRoom} · {drawer.draft.date} {drawer.draft.start}</dd></div><div><dt>新时段</dt><dd>{targetRoom} · {drawer.target.bookingDate} {drawer.target.start}</dd></div></dl><div className="booking-draft-relocation-actions"><button data-initial-focus className="submit-button" type="button" onClick={() => beginCreate({ ...drawer.target, draft: drawer.draft })}>使用草稿预约此时段</button><button className="secondary-button" type="button" onClick={() => beginCreate(drawer.target)}>清除草稿并新建</button><button className="booking-conflict-recheck" type="button" onClick={() => setDrawer(null)}>返回日历</button></div></div>;
    }
    if (drawer.type === "details") {
      const booking = drawer.booking;
      const canManage = canManageBooking({ role, currentUserId: currentUser.id, booking }) && booking.status !== "cancelled";
      const canCopyReminder = canManage && booking.status === "active" && !hasBookingStarted({ date: booking.date, start: booking.start, serverDate: businessClock.date, serverTime: businessClock.time });
      const handoverPending = [...handoverBoard.incoming, ...handoverBoard.outgoing].some((request) => request.reservation.id === booking.id);
      const canHandover = !handoverPending && booking.status === "active" && !hasBookingStarted({ date: booking.date, start: booking.start, serverDate: businessClock.date, serverTime: businessClock.time }) && (booking.ownerId === currentUser.id || role === "admin");
      return <BookingDetails booking={booking} tag={tagFor(booking)} dateSubtitle={withRelativeDay(booking.date)} canCopyReminder={canCopyReminder} canHandover={canHandover} handoverPending={handoverPending} handoverLabel={role === "admin" && booking.ownerId !== currentUser.id ? "指派给同事" : "交接给同事"} onHandover={() => setDrawer({ type: "handover", booking, selectedUserId: "" })} canEdit={!drawer.readOnly && canManage && booking.canEdit === true} canCancel={!drawer.readOnly && canManage && booking.canCancel === true} events={bookingEvents} eventsState={bookingEventsState} eventsAllowed={role === "admin" || booking.ownerId === currentUser.id} onCopyReminder={async () => { const message = renderReminderTemplate(bootstrap?.preferences?.reminderTemplate, { partyName: booking.partyName, date: dateLabel(booking.date).split(" · ")[0], start: booking.start, end: booking.end, roomName: booking.roomName }); try { await copyText(message); setToast("提醒信息已复制，可在微信中粘贴发送", "success"); } catch { setToast("无法自动复制，请手动复制提醒信息", "error"); } }} onEdit={() => openEdit(booking, drawer.returnTo)} onCancel={() => setDrawer({ type: "cancel", booking, returnTo: drawer.returnTo })} onClose={() => setDrawer(null)} onRetryEvents={() => loadBookingEvents(booking)} />;
    }
    if (drawer.type === "handover") {
      const booking = drawer.booking;
      const adminForce = role === "admin" && booking.ownerId !== currentUser.id;
      const directory = handoverDirectory;
      const selectedUser = directory.find((user) => user.id === drawer.selectedUserId);
      return <div className="handover-picker">
        <div className="selection-summary"><h2>{booking.start}–{booking.end}</h2><p>{booking.roomName} · {withRelativeDay(booking.date)} · {booking.partyName}</p></div>
        <div className={`handover-picker-copy ${adminForce ? "admin" : ""}`}><h3>{adminForce ? "选择新的预约负责人" : "选择接手同事"}</h3><p>{adminForce ? `当前预约者为 ${booking.owner?.name || booking.ownerName || "未知用户"}。确认指派后立即生效，对方无需确认。` : "提交后由对方确认；在对方接受前，这场预约仍归你。"}</p></div>
        {handoverDirectoryState === "loading" ? <p className="handover-picker-note" role="status">正在读取人员…</p> :
          directory.length ? <ul className="handover-picker-list" aria-label="选择接手人">{directory.map((user) => <li key={user.id}><button type="button" className={drawer.selectedUserId === user.id ? "selected" : ""} aria-pressed={drawer.selectedUserId === user.id} onClick={() => setDrawer((current) => ({ ...current, selectedUserId: user.id }))}><span className="handover-person-copy"><strong>{user.name}</strong>{user.department ? <small>{user.department}</small> : null}</span><CheckCircle size={20} weight={drawer.selectedUserId === user.id ? "fill" : "regular"} aria-hidden="true" /></button></li>)}</ul> :
          <p className="handover-picker-note">当前没有可指派的其他人员。</p>}
        <div className="handover-picker-actions"><button className="secondary-button" type="button" disabled={handoverActionBusy} onClick={() => setDrawer({ type: "details", booking })}>返回预约</button><button className="primary-button" type="button" disabled={!selectedUser || handoverActionBusy} onClick={() => void sendHandover(booking.id, selectedUser.id)}>{handoverActionBusy ? "正在提交…" : selectedUser ? (adminForce ? `确认指派给 ${selectedUser.name}` : `向 ${selectedUser.name} 发起交接`) : (adminForce ? "选择新的负责人" : "选择接手同事")}</button></div>
      </div>;
    }
    if (drawer.type === "cancel") return <div className="booking-cancel-confirmation"><div className="selection-summary"><h2>{drawer.booking.start}–{drawer.booking.end}</h2><p>{drawer.booking.roomName} · {withRelativeDay(drawer.booking.date)}</p></div><div className="cancel-confirmation-copy"><h3>确定取消这场预约吗？</h3><p>取消后，该时段会立即重新开放。</p></div><div className="cancel-confirmation-actions"><button className="confirm-cancel-button" disabled={saveState === "saving"} onClick={cancelBooking}>{saveState === "saving" ? "正在取消…" : "确认取消预约"}</button><button className="secondary-button" onClick={() => openDetails(drawer.booking, false, drawer.returnTo)}>返回</button></div></div>;
    if (drawer.type === "room-create" || drawer.type === "room-edit") return <RoomAdminForm drawer={drawer} deleteBusy={roomDeleteBusy} onSubmit={saveRoom} onFieldChange={updateDrawerField} onCancel={() => setDrawer(null)} onDelete={requestRoomDeletion} />;
    if (drawer.type === "room-delete-confirm") return <RoomDeleteConfirmation room={drawer.room} busy={roomDeleteBusy} onConfirm={confirmRoomDeletion} onBack={() => openRoom(drawer.room)} />;
    if (drawer.type === "room-delete-blocked") return <RoomDeleteBlocked room={drawer.room} bookings={drawer.bookings} total={drawer.total} onOpenBooking={(booking) => { const returnTo = { ...drawer }; return booking.canEdit ? openEdit(booking, returnTo) : openDetails(booking, false, returnTo); }} onBack={() => openRoom(drawer.room)} />;
    if (drawer.type === "user-create" || drawer.type === "user-edit") return <UserAdminForm drawer={drawer} lastAdminProtected={lastAdminProtected} onSubmit={saveUser} onFieldChange={updateDrawerField} onReset={() => setDrawer({ type: "user-reset", user: drawer.user, errors: {}, form: { password: "" } })} />;
    if (drawer.type === "user-reset") return <form className="password-reset-form" onSubmit={resetPassword} noValidate><div className="password-reset-copy"><span className="password-reset-icon"><Key size={24} /></span><h2>为 {drawer.user.name} 设置新密码</h2><p>保存后旧密码立即失效。</p></div><label className="field"><span>新密码</span><input data-initial-focus type="password" autoComplete="new-password" value={drawer.form.password} aria-invalid={Boolean(drawer.errors?.password)} aria-describedby={drawer.errors?.password ? "reset-password-error" : undefined} onChange={(event) => updateDrawerField("password", event.target.value)} />{drawer.errors?.password && <small id="reset-password-error" role="alert">{drawer.errors.password}</small>}</label><div className="password-reset-actions"><button className="submit-button" type="submit">确认重置</button><button className="secondary-button" type="button" onClick={() => openUser(drawer.user)}>返回编辑</button></div></form>;
    if (drawer.type === "system-settings") return <form className="system-settings-form" onSubmit={saveSystemSettings} noValidate><div className="system-settings-copy"><Clock size={27} /><div><h2>调整可预约工作时间</h2><p>仅影响以后的可用时段；已有预约的日期和时间保持不变。</p></div></div><div className="system-settings-fields"><label><span>开始时间</span><input data-initial-focus type="time" step="1800" value={drawer.form.workStart} aria-invalid={Boolean(drawer.errors?.workStart)} aria-describedby={drawer.errors?.workStart ? "system-work-start-error" : undefined} onChange={(event) => updateDrawerField("workStart", event.target.value)} />{drawer.errors?.workStart && <small id="system-work-start-error" role="alert">{drawer.errors.workStart}</small>}</label><label><span>结束时间</span><input type="time" step="1800" value={drawer.form.workEnd} aria-invalid={Boolean(drawer.errors?.workEnd)} aria-describedby={drawer.errors?.workEnd ? "system-work-end-error" : undefined} onChange={(event) => updateDrawerField("workEnd", event.target.value)} />{drawer.errors?.workEnd && <small id="system-work-end-error" role="alert">{drawer.errors.workEnd}</small>}</label></div><div className="drawer-fixed-footer"><button className="primary-button" type="submit" disabled={systemSettingsSaving}>{systemSettingsSaving ? "正在保存…" : "保存工作时间"}</button></div></form>;
    if (drawer.type === "backup") return <div className="system-backup-details"><div className="system-backup-summary"><Database size={30} /><div><h2>{system?.backupCaughtUp ? "备份已追平" : "需要创建新备份"}</h2><p>{system?.lastBackupAt ? formatLocalDateTime(system.lastBackupAt) : "尚未创建备份"}</p></div></div><dl><div><dt>数据序号</dt><dd>{system?.dataSequence ?? "—"}</dd></div><div><dt>备份序号</dt><dd>{system?.backupSequence ?? "—"}</dd></div><div><dt>追平状态</dt><dd>{system?.backupCaughtUp ? "已追平" : "待备份"}</dd></div></dl><div className="system-backup-privacy"><LockSimple size={18} /><p>备份保留在服务器电脑；诊断导出不包含预约内容或凭据。</p></div><button className="primary-button system-backup-close" onClick={createBackup}>立即备份</button></div>;
    if (drawer.type === "token-create") return <form className="system-token-form" onSubmit={createIntegrationToken}><label><span>令牌名称</span><input data-initial-focus value={drawer.form.name} placeholder="例如 只读数据看板" onChange={(event) => setDrawer((current) => ({ ...current, form: { ...current.form, name: event.target.value } }))} /></label><fieldset><legend>只读权限</legend>{[["rooms:read", "笔录室"], ["availability:read", "可用时段"], ["health:read", "服务健康"]].map(([scope, label]) => <label key={scope}><input type="checkbox" checked={drawer.form.scopes.includes(scope)} onChange={(event) => setDrawer((current) => ({ ...current, form: { ...current.form, scopes: event.target.checked ? [...current.form.scopes, scope] : current.form.scopes.filter((item) => item !== scope) } }))} />{label}</label>)}</fieldset><label><span>到期时间（可选）</span><input type="datetime-local" value={drawer.form.expiresAt} onChange={(event) => setDrawer((current) => ({ ...current, form: { ...current.form, expiresAt: event.target.value } }))} /></label><p>接口令牌仅开放所选读取接口，不可写入预约数据。</p><div className="drawer-fixed-footer"><button className="primary-button" type="submit">创建令牌</button></div></form>;
    if (drawer.type === "token-created") return <div className="system-token-created"><CheckCircle size={32} /><h2>请立即保存令牌</h2><p>关闭此侧栏后，系统不会再次显示明文。</p><code>{drawer.token.token}</code><button className="primary-button" onClick={async () => { try { await copyText(drawer.token.token); setToast("令牌已复制", "success"); } catch { setToast("无法自动复制，请手动选择令牌", "error"); } }}>复制令牌</button><dl><div><dt>名称</dt><dd>{drawer.token.name}</dd></div><div><dt>权限</dt><dd>{drawer.token.scopes.join("、")}</dd></div><div><dt>到期</dt><dd>{drawer.token.expiresAt ? formatLocalDateTime(drawer.token.expiresAt) : "长期有效"}</dd></div></dl></div>;
    if (drawer.type === "token-revoke") return <div className="system-token-revoke"><WarningCircle size={32} /><h2>撤销 {drawer.token.name}？</h2><p>服务器确认撤销前，令牌仍会保留在列表中。撤销后依赖它的只读集成会立即失效。</p><button className="cancel-booking-button" disabled={tokenRevokingId === drawer.token.id} onClick={revokeIntegrationToken}>{tokenRevokingId === drawer.token.id ? "正在等待服务器确认…" : "确认撤销令牌"}</button><button className="secondary-button" disabled={tokenRevokingId === drawer.token.id} onClick={() => setDrawer(null)}>返回</button></div>;
    return null;
  }

  async function logout() {
    try {
      await api.logout();
      clearSessionBookingDraft(window.sessionStorage, currentUser?.id);
      onLoggedOut();
    } catch (error) {
      if (error?.code === "SYSTEM_RECOVERY_REQUIRED") onRecovery(error);
      else if (error?.status === 401 || error?.code === "SESSION_REQUIRED" || error?.code === "SESSION_EXPIRED") {
        clearSessionBookingDraft(window.sessionStorage, currentUser?.id);
        onLoggedOut();
      }
      else handleError(error, "退出失败，请确认网络后重试");
    }
  }

  return <SessionIsolationBoundary
    blocked={sessionExpired}
    reauthentication={<SessionExpired onRecovery={onRecovery} onRecovered={onAuthenticatedContext} />}
  ><div className={`app-shell ${drawer ? "drawer-open" : ""} ${drawer?.type?.startsWith("user") ? "user-drawer-open" : ""}`}>
    <div ref={mainRef} className="app-main-region">
      <aside className="icon-rail" aria-label="主导航">
        <button className="brand-mark tooltip-right" data-tooltip="回到我的预约" aria-label="回到我的预约" onClick={() => navigate("mine")}><Asterisk size={34} /></button>
        <nav className="rail-nav">{NAV_ITEMS.filter((item) => !item.permission || permissions[item.permission]).map(({ id, label, Icon }) => {
          const badgeCount = id === "mine" ? dueReminders.upcoming.length : id === "handovers" ? handoverBoard.incoming.length : 0;
          const badgeLabel = id === "mine" ? `${badgeCount} 场预约即将开始` : `${badgeCount} 条交接等待确认`;
          const hasBadge = badgeCount > 0;
          return <button className={"rail-button tooltip-right " + (activeView === id ? "active" : "") + (hasBadge ? " has-upcoming-reminder" : "")} data-tooltip={hasBadge ? `${label} · ${badgeLabel}` : label} aria-label={hasBadge ? `${label}，${badgeLabel}` : label} aria-current={activeView === id ? "page" : undefined} key={id} onClick={() => navigate(id)}><Icon size={25} />{hasBadge && <span className="rail-reminder-badge" aria-hidden="true">{badgeCount > 9 ? "9+" : badgeCount}</span>}</button>;
        })}</nav>
        <button className={"avatar-button tooltip-right " + (activeView === "settings" ? "active" : "")} data-tooltip={itemName(currentUser) + " · 个人中心"} aria-label={itemName(currentUser) + "，个人中心"} onClick={openPersonalCenter}><UserCircle size={42} weight="thin" /></button>
      </aside>
      {activeView === "mine" && renderMine()}{activeView === "calendar" && renderCalendar()}{activeView === "handovers" && renderHandovers()}{activeView === "history" && renderHistory()}{activeView === "data-center" && permissions.viewReports && renderDataCenter()}{activeView === "rooms" && permissions.manageRooms && renderRooms()}{activeView === "users" && permissions.manageUsers && renderUsers()}{activeView === "system" && permissions.manageSystem && renderSystem()}{activeView === "settings" && renderSettings()}{activeView === "unauthorized" && renderUnauthorized()}
      {toast && <div className={`toast visible ${toast.tone}`} role="status" aria-live="polite"><ToastIcon tone={toast.tone} /><span>{toast.message}</span><button aria-label="关闭提示" onClick={() => setToast("")}><X size={16} /></button></div>}
      {arrivalNotice && !drawer && <div className="toast visible reminder-toast arrival-toast" role="status" aria-live="polite"><Clock size={20} /><span>{arrivalNotice.message}</span><button onClick={() => { const booking = arrivalNotice.booking; setArrivalNotice(null); openDetails(booking); }}>查看</button><button onClick={() => setArrivalNotice(null)}>知道了</button></div>}
    </div>
    {drawer && (dueReminders.changes.length > 0 || dueReminders.handovers.length > 0) && <div className="notice-queue-chip" role="status" aria-live="polite"><span className="notice-queue-chip-icon" aria-hidden="true">{dueReminders.changes.length > 0 ? <ClockCounterClockwise size={16} /> : <ArrowsLeftRight size={16} />}</span><span className="notice-queue-chip-copy"><strong>{dueReminders.changes.length > 0 ? `${dueReminders.changes.length} 条预约变更待确认` : `${dueReminders.handovers.length} 条工作交接待处理`}</strong><small>关闭预约详情后自动打开</small></span></div>}
    {noticeModalOpen && <div className="notice-modal-layer"><section ref={noticeModalRef} className={`notice-modal ${noticeOnlyHandovers ? "handover-only" : ""}`} role="alertdialog" aria-modal="true" aria-labelledby="notice-modal-heading" aria-describedby="notice-modal-hint" aria-busy={noticeAckBusy}>
      <header className="notice-modal-head"><span className="notice-modal-icon" aria-hidden="true">{noticeOnlyHandovers ? <ArrowsLeftRight size={22} /> : <ClockCounterClockwise size={22} />}</span><div><h2 id="notice-modal-heading">{noticeOnlyHandovers ? "工作交接" : noticeHasHandovers ? "待处理事项" : "预约变更通知"}</h2><p>{noticeOnlyHandovers ? `${visibleHandoverReminders.length} 条交接请求等待你处理` : noticeHasHandovers ? `${dueReminders.changes.length} 条预约变更，${visibleHandoverReminders.length} 条工作交接` : dueReminders.changes.length > 1 ? `${dueReminders.changes.length} 条待确认变更` : `${dueReminders.changes[0].actorName || "其他用户"} · ${formatLocalDateTime(dueReminders.changes[0].occurredAt)}`}</p></div></header>
      {noticeHasHandovers && <ul className="notice-modal-list notice-handover-list">{visibleHandoverReminders.map((item, index) => <li className="notice-modal-item notice-handover-item" key={item.handoverRequestId}>
        {dueReminders.changes.length > 0 && <p className="notice-item-meta">{item.fromName} · 刚刚发起的交接</p>}
        <h3>{item.fromName} 希望将这场预约交接给你</h3>
        <dl className="notice-item-identity" aria-label="交接预约信息"><div><dt>当事人</dt><dd>{item.partyName}</dd></div><div><dt>事项</dt><dd>{item.purpose}</dd></div><div className="notice-identity-schedule"><dt>时间</dt><dd>{item.start}–{item.end} · {item.roomName}</dd></div></dl>
        <p className="notice-handover-note">接受后预约转入你名下；不接受则仍归 {item.fromName}。请求在预约开始前有效。</p>
        <div className="notice-item-actions"><button type="button" disabled={noticeAckBusy} onClick={() => void decideHandover(item.handoverRequestId, "decline")}>不接受</button><button type="button" className="notice-item-ack" data-initial-focus={index === 0 && dueReminders.changes.length === 0 || undefined} disabled={noticeAckBusy} onClick={() => void decideHandover(item.handoverRequestId, "accept")}>{noticeAckBusy ? "正在处理…" : "接受交接"}</button></div>
      </li>)}</ul>}
      {dueReminders.changes.length > 0 && <ul className="notice-modal-list">{dueReminders.changes.map((item, index) => <ChangeNoticeItem item={item} busy={noticeAckBusy} initialFocus={index === 0 && !noticeHasHandovers} showMeta={dueReminders.changes.length > 1} showActions={dueReminders.changes.length > 1} onView={(target) => void viewChangeNotice(target)} onAcknowledge={(targets) => void acknowledgeChangeNotices(targets)} key={item.eventId} />)}</ul>}
      {noticeOnlyHandovers
        ? <footer className="handover-modal-foot"><p id="notice-modal-hint" className="notice-modal-hint">暂不处理不会改变预约归属，可稍后在「工作交接」中继续。</p><button type="button" className="handover-defer-button" disabled={noticeAckBusy} onClick={deferVisibleHandovers}>稍后处理</button></footer>
        : dueReminders.changes.length === 1
        ? <footer className="notice-modal-single-foot"><p id="notice-modal-hint" className="notice-modal-hint">{noticeHasHandovers ? "交接请求需明确处理；预约变更仍需确认" : "按 Esc 可确认并关闭"}</p><div className="notice-item-actions"><button type="button" disabled={noticeAckBusy} onClick={() => void viewChangeNotice(dueReminders.changes[0])}>查看预约</button><button type="button" className="notice-item-ack" data-initial-focus disabled={noticeAckBusy} onClick={() => void acknowledgeChangeNotices(dueReminders.changes)}>{noticeAckBusy ? "正在确认…" : "我知道了"}</button></div></footer>
        : <><footer className="notice-modal-foot"><button type="button" className="notice-ack-all" disabled={noticeAckBusy} onClick={() => void acknowledgeChangeNotices(dueReminders.changes)}>{noticeAckBusy ? "正在确认…" : "全部知道了"}</button></footer><p id="notice-modal-hint" className="notice-modal-hint notice-modal-multi-hint">{noticeHasHandovers ? "按 Esc 会暂后交接请求，不会确认预约变更" : "按 Esc 可确认全部；“查看预约”会先确认该条通知"}</p></>}
    </section></div>}
    <Drawer open={Boolean(drawer) && !sessionExpired && isDrawerAllowed(drawer?.type, permissions)} heading={drawerHeading()} onBack={drawer?.returnTo ? () => setDrawer(drawer.returnTo) : null} onClose={() => setDrawer(null)} className={drawer?.type?.startsWith("user") ? "user-drawer" : ""} backgroundRef={mainRef}>{!sessionExpired && renderDrawer()}</Drawer>
  </div></SessionIsolationBoundary>;
}

export function App() {
  const publicRoute = window.location.pathname === "/display" || window.location.pathname.endsWith("/display/");
  const [phase, setPhase] = useState(publicRoute ? "public" : "loading");
  const [session, setSession] = useState(null);
  const [initialBootstrap, setInitialBootstrap] = useState(null);
  const [scopeVersion, setScopeVersion] = useState(0);
  const [fatal, setFatal] = useState("");
  const [recoveryError, setRecoveryError] = useState(null);
  const enterRecovery = useCallback((error) => {
    setRecoveryError(error);
    setPhase("recovery");
  }, []);
  const acceptAuthenticatedContext = useCallback((context) => {
    const verified = validateAuthenticatedContext(context?.session, context?.bootstrap);
    setSession(verified.session);
    setInitialBootstrap(verified.bootstrap);
    setScopeVersion((current) => current + 1);
    setPhase("app");
  }, []);
  const finishLogin = useCallback(async () => {
    try {
      acceptAuthenticatedContext(await readAuthenticatedContext(api));
    } catch (error) {
      if (error?.code === "SYSTEM_RECOVERY_REQUIRED") enterRecovery(error);
      else {
        setFatal(userFacingError(error, "无法完成登录后的身份校验"));
        setPhase("fatal");
      }
    }
  }, [acceptAuthenticatedContext, enterRecovery]);

  const start = useCallback(async () => {
    if (publicRoute) return;
    setPhase("loading");
    setFatal("");
    setRecoveryError(null);
    try {
      const value = await api.getSession();
      setSession(value);
      setInitialBootstrap(null);
      if (!value.setupComplete) setPhase("setup");
      else if (!value.authenticated) setPhase("login");
      else acceptAuthenticatedContext(validateAuthenticatedContext(value, await api.getBootstrap()));
    } catch (error) {
      if (error?.code === "SYSTEM_RECOVERY_REQUIRED") {
        setRecoveryError(error);
        setPhase("recovery");
      } else {
        setFatal(userFacingError(error, "无法连接系统服务"));
        setPhase("fatal");
      }
    }
  }, [acceptAuthenticatedContext, publicRoute]);

  useEffect(() => {
    const timer = window.setTimeout(start, 0);
    return () => window.clearTimeout(timer);
  }, [start]);
  if (phase === "public") return <PublicDisplay />;
  if (phase === "loading") return <LoadingScreen />;
  if (phase === "recovery") return <RecoveryScreen error={recoveryError} onRetry={start} />;
  if (phase === "fatal") return <FatalScreen error={fatal} onRetry={start} />;
  if (phase === "setup") return <Setup onComplete={start} onRecovery={enterRecovery} />;
  if (phase === "login") return <Login onAuthenticated={finishLogin} onRecovery={enterRecovery} />;
  return <MainApp
    key={scopedAppKey(session, scopeVersion)}
    session={session}
    initialBootstrap={initialBootstrap}
    onAuthenticatedContext={acceptAuthenticatedContext}
    onRecovery={enterRecovery}
    onLoggedOut={() => { setSession(null); setInitialBootstrap(null); setScopeVersion((current) => current + 1); setPhase("login"); }}
  />;
}
