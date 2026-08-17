import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowClockwise,
  ChartBar,
  CircleNotch,
  DownloadSimple,
  FunnelSimple,
  Info,
} from "@phosphor-icons/react";

import { api } from "../../api.js";
import {
  defaultReportFilters,
  filenameFromDisposition,
  formatCancellationRate,
  formatReportDuration,
  reportComposition,
  reportExportCount,
  reportScope,
  reportTagOptions,
  reportTrendModel,
  tagTone,
  weekdaySlotDistribution,
} from "./reporting.js";


function shortDate(value) {
  const parts = String(value || "").split("-");
  return parts.length === 3 ? `${Number(parts[1])}月${Number(parts[2])}日` : value;
}

function MetricCard({ label, value, unit, note, tone = "default" }) {
  return <article className={`report-metric-card ${tone === "guardrail" ? "guardrail" : ""}`}>
    <span>{label}</span>
    <p><strong>{value}</strong>{unit && <small>{unit}</small>}</p>
    {note && <small className="report-metric-note">{note}</small>}
  </article>;
}

function EmptyChart({ message = "当前条件下还没有可展示的数据" }) {
  return <div className="report-chart-empty"><ChartBar size={30} weight="thin" /><p>{message}</p></div>;
}

function SegmentedControl({ label, onChange, options, value }) {
  return <div className="report-segmented" role="group" aria-label={label}>
    {options.map((option) => <button
      className={option.value === value ? "active" : ""}
      type="button"
      aria-pressed={option.value === value}
      onClick={() => onChange(option.value)}
      key={option.value}
    >{option.label}</button>)}
  </div>;
}

function TrendChart({ granularity, items = [], metric, onMetricChange }) {
  const [hoveredKey, setHoveredKey] = useState("");
  const [pinnedKey, setPinnedKey] = useState("");
  const grainLabel = granularity === "month" ? "月" : "周";
  const hasValues = items.some((item) => Number(item.activeCount) > 0);
  if (!items.length || !hasValues) return <EmptyChart />;
  const width = 1080;
  const height = 330;
  const padding = { left: 48, right: 24, top: 54, bottom: 54 };
  const innerWidth = width - padding.left - padding.right;
  const innerHeight = height - padding.top - padding.bottom;
  const values = items.map((item) => metric === "duration"
    ? Number(item.activeDurationMinutes || 0) / 60
    : Number(item.activeCount || 0));
  const maximum = Math.max(1, ...values);
  const displayMaximum = metric === "duration"
    ? Math.max(5, Math.ceil(maximum / 5) * 5)
    : Math.max(20, Math.ceil(maximum / 20) * 20);
  const step = innerWidth / Math.max(1, items.length);
  const barWidth = Math.max(8, Math.min(72, step * 0.42));
  const activeKey = hoveredKey || pinnedKey;
  const activeItem = items.find((item) => item.key === activeKey);
  const activeDuration = formatReportDuration(activeItem?.activeDurationMinutes);
  const togglePin = (key) => setPinnedKey((current) => current === key ? "" : key);
  return <figure className="report-chart report-weekly-chart" onKeyDown={(event) => event.key === "Escape" && setPinnedKey("")}>
    <div className="report-chart-toolbar">
      <div className="report-chart-detail" aria-live="polite">
        {activeItem
          ? <><strong>{activeItem.intervalLabel}</strong><span>{activeItem.activeCount}场 · {activeDuration.value}{activeDuration.unit}</span></>
          : <span>悬停柱形查看{grainLabel}区间与概要</span>}
      </div>
      <SegmentedControl label="趋势指标" value={metric} onChange={onMetricChange} options={[{ value: "count", label: "场次" }, { value: "duration", label: "时长" }]} />
    </div>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`按${grainLabel}${metric === "duration" ? "预约时长" : "有效预约数量"}趋势`}>
      {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
        const y = padding.top + innerHeight * (1 - ratio);
        const tick = displayMaximum * ratio;
        return <g key={ratio}>
          <line className="report-chart-gridline" x1={padding.left} x2={width - padding.right} y1={y} y2={y} />
          <text className="report-chart-tick" x={padding.left - 10} y={y + 4} textAnchor="end">{metric === "duration" ? `${Math.round(tick * 10) / 10}` : Math.round(tick)}</text>
        </g>;
      })}
      {items.map((item, index) => {
        const value = values[index];
        const duration = formatReportDuration(item.activeDurationMinutes);
        const barHeight = value / displayMaximum * innerHeight;
        const x = padding.left + index * step + (step - barWidth) / 2;
        const y = padding.top + innerHeight - barHeight;
        const isSelected = item.key === activeKey;
        const valueLabel = metric === "duration" ? duration.value : item.activeCount;
        return <g
          className={`report-chart-bar${isSelected ? " selected" : ""}`}
          role="button"
          tabIndex="0"
          aria-label={`${item.intervalLabel}，${item.activeCount}场，${duration.value}${duration.unit}`}
          aria-pressed={item.key === pinnedKey}
          onMouseEnter={() => setHoveredKey(item.key)}
          onMouseLeave={() => setHoveredKey("")}
          onFocus={() => setHoveredKey(item.key)}
          onBlur={() => setHoveredKey("")}
          onClick={() => togglePin(item.key)}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              togglePin(item.key);
            }
          }}
          key={item.key}
        >
          <rect x={x} y={y} width={barWidth} height={Math.max(value ? 3 : 0, barHeight)} rx="5" />
          {value > 0 && <text className="report-chart-value" x={x + barWidth / 2} y={Math.max(24, y - 9)} textAnchor="middle">{valueLabel}</text>}
          <text className="report-chart-label" x={x + barWidth / 2} y={height - 18} textAnchor="middle">{item.axisLabel}</text>
        </g>;
      })}
    </svg>
    <table className="sr-only"><caption>按{grainLabel}工作量明细</caption><thead><tr><th>开始日期</th><th>结束日期</th><th>有效预约</th><th>预约时长（分钟）</th></tr></thead><tbody>{items.map((item) => <tr key={item.key}><td>{item.periodStart}</td><td>{item.periodEnd}</td><td>{item.activeCount}</td><td>{item.activeDurationMinutes}</td></tr>)}</tbody></table>
  </figure>;
}

function slotFromEnd(slot, slotMinutes) {
  const [hours, minutes] = slot.split(":").map(Number);
  const value = hours * 60 + minutes + slotMinutes;
  return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
}

function TimeDistribution({ items = [], settings = {} }) {
  const { maximum, peak, rows, slotMinutes, slots } = weekdaySlotDistribution(items, settings);
  const [hoveredCell, setHoveredCell] = useState(null);
  const [pinnedCell, setPinnedCell] = useState(null);
  if (!maximum) return <EmptyChart />;
  const gridStyle = {
    "--report-time-columns": slots.length,
    minWidth: `${72 + slots.length * 48}px`,
  };
  const summary = hoveredCell || pinnedCell || {
    label: "最高峰",
    weekdayLabel: peak.weekdayLabel,
    slot: peak.slot,
    end: peak.end,
    count: peak.count,
  };
  const activeKey = hoveredCell?.key || pinnedCell?.key || "";
  const togglePin = (cellSummary) => setPinnedCell((current) => current?.key === cellSummary.key ? null : cellSummary);
  return <figure className="report-time-distribution" onKeyDown={(event) => event.key === "Escape" && setPinnedCell(null)}>
    <figcaption className="report-time-summary" aria-live="polite"><span>{summary.label}</span><strong>{summary.weekdayLabel} {summary.slot}–{summary.end}</strong><em>· {summary.count}</em></figcaption>
    <div className="report-time-scroll">
      <div className="report-time-axis" style={gridStyle} aria-hidden="true">
        <span />
        {slots.map((slot) => <span key={slot.slot}>{slot.axisLabel}</span>)}
      </div>
      <div className="report-time-plot" role="group" aria-label={`星期与半小时时段分布；最高峰是${peak.weekdayLabel}${peak.slot}至${peak.end}，${peak.count}个预约占用槽`}>
        {rows.map((row) => <div className="report-time-row" style={gridStyle} key={row.weekday}>
          <strong>{row.label}</strong>
          {row.cells.map((cell) => {
            const ratio = cell.count / maximum;
            const key = `${row.weekday}:${cell.slot}`;
            const end = slotFromEnd(cell.slot, slotMinutes);
            const label = `${row.label} ${cell.slot}–${end} · ${cell.count} 个预约占用槽`;
            const cellSummary = cell.count ? {
              key,
              label: "当前时段",
              weekdayLabel: row.label,
              slot: cell.slot,
              end,
              count: cell.count,
            } : null;
            const dotStyle = {
              "--report-dot-size": `${cell.count ? Math.round(8 + Math.sqrt(ratio) * 22) : 4}px`,
              "--report-dot-opacity": cell.count ? 0.28 + ratio * 0.66 : 0.12,
            };
            return <button
              className={`report-time-cell${cell.count ? " has-value" : ""}${key === activeKey ? " active" : ""}`}
              type="button"
              disabled={!cell.count}
              aria-label={cell.count ? label : undefined}
              aria-pressed={cell.count ? pinnedCell?.key === key : undefined}
              onMouseEnter={() => cellSummary && setHoveredCell(cellSummary)}
              onMouseLeave={() => setHoveredCell(null)}
              onFocus={() => cellSummary && setHoveredCell(cellSummary)}
              onBlur={() => setHoveredCell(null)}
              onClick={() => cellSummary && togglePin(cellSummary)}
              key={cell.slot}
            >
              <i style={dotStyle} aria-hidden="true">{cell.count >= 2 ? cell.count : ""}</i>
            </button>;
          })}
        </div>)}
      </div>
    </div>
    <div className="report-time-legend" aria-hidden="true"><span><i className="small" />少</span><span><i className="medium" />中</span><span><i className="large" />多</span></div>
    <p className="report-interaction-hint">悬停圆点查看场次 · 点击固定查看 · Esc 清除</p>
    <table className="sr-only"><caption>星期与半小时时段分布明细</caption><thead><tr><th>星期</th><th>开始时间</th><th>结束时间</th><th>预约占用槽</th></tr></thead><tbody>{rows.flatMap((row) => row.cells.filter((cell) => cell.count).map((cell) => <tr key={`${row.weekday}:${cell.slot}`}><td>{row.label}</td><td>{cell.slot}</td><td>{slotFromEnd(cell.slot, slotMinutes)}</td><td>{cell.count}</td></tr>))}</tbody></table>
  </figure>;
}

function RoomDistribution({ items = [] }) {
  const [metric, setMetric] = useState("count");
  const [hoveredKey, setHoveredKey] = useState("");
  const [pinnedKey, setPinnedKey] = useState("");
  if (!items.length || !items.some((item) => Number(item.activeCount) > 0)) return <EmptyChart />;
  const maximum = Math.max(1, ...items.map((item) => metric === "duration" ? Number(item.activeDurationMinutes || 0) : Number(item.activeCount || 0)));
  const totalCount = items.reduce((sum, item) => sum + Number(item.activeCount || 0), 0);
  const activeKey = hoveredKey || pinnedKey;
  const activeItem = items.find((item) => item.roomId === activeKey);
  const activeDuration = formatReportDuration(activeItem?.activeDurationMinutes);
  return <figure className="report-room-distribution" onKeyDown={(event) => event.key === "Escape" && setPinnedKey("")}>
    <div className="report-visual-toolbar">
      <div className="report-chart-detail" aria-live="polite">
        {activeItem ? <><strong>{activeItem.label}</strong><span>{activeItem.activeCount}场 · {activeDuration.value}{activeDuration.unit} · 占全部{totalCount ? formatCancellationRate(activeItem.activeCount / totalCount) : "—"}</span></> : <span>悬停笔录室查看场次、时长与构成</span>}
      </div>
      <SegmentedControl label="笔录室指标" value={metric} onChange={setMetric} options={[{ value: "count", label: "场次" }, { value: "duration", label: "时长" }]} />
    </div>
    <div className="report-lollipop-list">
      {items.map((item) => {
        const duration = formatReportDuration(item.activeDurationMinutes);
        const value = metric === "duration" ? Number(item.activeDurationMinutes || 0) : Number(item.activeCount || 0);
        const width = Math.max(value ? 3 : 0, value / maximum * 100);
        const selected = item.roomId === activeKey;
        return <button
          className={`report-lollipop-row${selected ? " active" : ""}`}
          type="button"
          aria-pressed={item.roomId === pinnedKey}
          aria-label={`${item.label}，${item.activeCount}场，${duration.value}${duration.unit}`}
          onMouseEnter={() => setHoveredKey(item.roomId)}
          onMouseLeave={() => setHoveredKey("")}
          onFocus={() => setHoveredKey(item.roomId)}
          onBlur={() => setHoveredKey("")}
          onClick={() => setPinnedKey((current) => current === item.roomId ? "" : item.roomId)}
          key={item.roomId}
        >
          <strong>{item.label}</strong>
          <span className="report-lollipop-track" aria-hidden="true"><i style={{ width: `${width}%` }}><b /></i></span>
          <span className="report-lollipop-value"><strong>{item.activeCount}场</strong><small>{duration.value}{duration.unit}</small></span>
        </button>;
      })}
    </div>
    <p className="report-interaction-hint">悬停查看详情 · 点击固定查看 · Esc 清除</p>
  </figure>;
}

function TagComposition({ items = [] }) {
  const [hoveredKey, setHoveredKey] = useState("");
  const [pinnedKey, setPinnedKey] = useState("");
  const categories = useMemo(() => reportComposition(items).map((item, index) => ({ ...item, tone: tagTone(item, index) })), [items]);
  if (!categories.length) return <EmptyChart />;
  const activeKey = hoveredKey || pinnedKey;
  const activeItem = categories.find((item) => item.key === activeKey);
  const dots = categories.flatMap((item) => Array.from({ length: item.dotCount }, (_, index) => ({ ...item, dotKey: `${item.key}:${index}` })));
  const togglePin = (key) => setPinnedKey((current) => current === key ? "" : key);
  return <figure className="report-tag-composition" onKeyDown={(event) => event.key === "Escape" && setPinnedKey("")}>
    <div className="report-composition-visual">
      <div
        className={`report-dot-composition${activeKey ? " has-active" : ""}`}
        role="img"
        aria-label={categories.map((item) => `${item.label}${item.activeCount}场，占${item.shareLabel}`).join("；")}
        onMouseLeave={() => setHoveredKey("")}
      >
        {dots.map((dot) => <span
          className={`report-composition-dot tone-${dot.tone}${dot.key === activeKey ? " active" : ""}`}
          aria-hidden="true"
          onMouseEnter={() => setHoveredKey(dot.key)}
          onClick={() => togglePin(dot.key)}
          key={dot.dotKey}
        />)}
      </div>
      <small>每个点约代表 1%</small>
    </div>
    <div className="report-composition-copy">
      <div className="report-chart-detail" aria-live="polite">
        {activeItem ? <><strong>{activeItem.label}</strong><span>{activeItem.activeCount}场 · 占{activeItem.shareLabel}</span></> : <span>悬停类别查看构成</span>}
      </div>
      <div className="report-composition-legend">
        {categories.map((item) => <button
          className={`${item.key === activeKey ? "active" : ""} tone-${item.tone}`}
          type="button"
          aria-pressed={item.key === pinnedKey}
          onMouseEnter={() => setHoveredKey(item.key)}
          onMouseLeave={() => setHoveredKey("")}
          onFocus={() => setHoveredKey(item.key)}
          onBlur={() => setHoveredKey("")}
          onClick={() => togglePin(item.key)}
          key={item.key}
        ><i aria-hidden="true" /><strong>{item.label}</strong><span>{item.activeCount}场</span><span>{item.shareLabel}</span></button>)}
      </div>
      {!categories.every((item) => item.tagId) && <p className="report-composition-insight">未使用单位标签仅表示缺少单位分类，不作为质量判断。</p>}
    </div>
    <p className="report-interaction-hint">悬停突出类别 · 点击固定查看 · Esc 清除</p>
  </figure>;
}

function ReportFilters({ draft, onChange, onApply, onReset, rooms, tags, busy }) {
  return <form className="report-filters" onSubmit={onApply}>
    <label><span>开始日期</span><input type="date" value={draft.dateFrom} onChange={(event) => onChange("dateFrom", event.target.value)} /></label>
    <label><span>结束日期</span><input type="date" value={draft.dateTo} onChange={(event) => onChange("dateTo", event.target.value)} /></label>
    <label><span>笔录室</span><select value={draft.roomId} onChange={(event) => onChange("roomId", event.target.value)}><option value="">全部笔录室</option>{rooms.map((room) => <option value={room.id} key={room.id}>{room.name}{room.isActive === false ? "（已停用）" : ""}</option>)}</select></label>
    <label><span>标签</span><select value={draft.tagId} onChange={(event) => onChange("tagId", event.target.value)}><option value="">全部标签</option>{tags.map((tag) => <option value={tag.id} key={`${tag.id}:${tag.label}`}>{tag.label}</option>)}</select></label>
    <label className="report-query-field"><span>关键词</span><input value={draft.query} maxLength={120} placeholder="当事人、案号、事项或备注" onChange={(event) => onChange("query", event.target.value)} /></label>
    <div className="report-filter-actions"><button className="report-filter-apply" type="submit" disabled={busy}><FunnelSimple size={17} />应用筛选</button><button type="button" disabled={busy} onClick={onReset}><ArrowClockwise size={17} />重置</button></div>
  </form>;
}

export function DataCenter({
  businessDate,
  currentUser,
  globalTags = [],
  onError,
  personalTags = [],
  permissions = {},
  role,
  rooms = [],
  settings = {},
  users = [],
}) {
  const initialFilters = useMemo(() => defaultReportFilters(businessDate), [businessDate]);
  const [view, setView] = useState(role === "admin" ? "overall" : currentUser.id);
  const [draft, setDraft] = useState(initialFilters);
  const [applied, setApplied] = useState(initialFilters);
  const [report, setReport] = useState(null);
  const [state, setState] = useState("loading");
  const [message, setMessage] = useState("");
  const [exportStatus, setExportStatus] = useState("");
  const [exporting, setExporting] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [reportPage, setReportPage] = useState("overview");
  const [trendMetric, setTrendMetric] = useState("count");
  const requestRef = useRef(0);

  const scope = useMemo(() => reportScope(role, currentUser.id, view), [currentUser.id, role, view]);
  const tags = useMemo(() => reportTagOptions({ role, view, currentUser, users, globalTags, personalTags }), [currentUser, globalTags, personalTags, role, users, view]);
  const loadReport = useCallback(async () => {
    const requestNumber = requestRef.current + 1;
    requestRef.current = requestNumber;
    setState("loading");
    setMessage("");
    try {
      const result = await api.getReportOverview({ ...scope, ...applied });
      if (requestRef.current !== requestNumber) return;
      setReport(result);
      setState("ready");
    } catch (error) {
      if (requestRef.current !== requestNumber) return;
      setState("failed");
      setMessage(error?.message || "数据中心暂时无法读取");
      onError?.(error, "数据中心暂时无法读取");
    }
  }, [applied, onError, scope]);

  useEffect(() => {
    loadReport();
  }, [loadReport]);

  const changeView = (next) => {
    setView(next);
    setDraft((current) => ({ ...current, tagId: "" }));
    setApplied((current) => ({ ...current, tagId: "" }));
    setExportStatus("");
    setReportPage("overview");
    setReport(null);
  };

  const applyFilters = (event) => {
    event.preventDefault();
    setApplied({ ...draft, query: draft.query.trim() });
    setFiltersOpen(false);
  };

  const resetFilters = () => {
    setDraft(initialFilters);
    setApplied(initialFilters);
    setExportStatus("");
  };

  const downloadCsv = async () => {
    setExporting(true);
    try {
      const result = await api.downloadReportCsv({ ...scope, ...applied, status: exportStatus });
      const url = URL.createObjectURL(result.blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filenameFromDisposition(result.contentDisposition);
      document.body.appendChild(anchor);
      try {
        anchor.click();
      } finally {
        anchor.remove();
        URL.revokeObjectURL(url);
      }
      setMessage(`已生成 ${result.rowCount} 条办件明细`);
    } catch (error) {
      setMessage(error?.message || "CSV 导出失败");
      onError?.(error, "CSV 导出失败");
    } finally {
      setExporting(false);
    }
  };

  const duration = formatReportDuration(report?.summary?.activeDurationMinutes);
  const trend = useMemo(
    () => reportTrendModel(report || {}, report?.filters || applied),
    [applied, report],
  );
  const isOverall = report
    ? report.resolvedScope?.kind === "overall"
    : scope.scope === "overall";
  const scopeLabel = report?.resolvedScope?.owner?.name || currentUser.name || "本人";
  const exportCount = reportExportCount(report, exportStatus);
  const pages = useMemo(() => isOverall
    ? [{ id: "overview", label: "概览" }, { id: "time", label: "时段分布" }, { id: "rooms", label: "笔录室" }, { id: "tags", label: "标签" }]
    : [{ id: "overview", label: "概览" }, { id: "time", label: "时段分布" }, { id: "tags", label: "标签" }], [isOverall]);

  useEffect(() => {
    if (!pages.some((page) => page.id === reportPage)) setReportPage("overview");
  }, [pages, reportPage]);

  const pageHeading = {
    time: ["时段分布", "查看一周中预约集中出现的半小时时段"],
    rooms: ["笔录室", "比较各笔录室承接的预约场次与服务时长"],
    tags: ["标签", "查看服务标签在全部有效预约中的构成"],
  }[reportPage];

  return <main className="main-canvas data-center-canvas" tabIndex={0} onKeyDown={(event) => {
    if (event.key === "Escape") {
      setFiltersOpen(false);
      setExportOpen(false);
    }
  }}>
    <header className="page-header data-center-header">
      <h1>数据中心</h1>
      <div className="report-header-controls">
        {role === "admin" && permissions.viewOverallReports
          ? <label className="report-view-control"><span className="sr-only">选择数据视角</span><select value={view} onChange={(event) => changeView(event.target.value)}><option value="overall">全单位概览</option>{users.map((user) => <option value={user.id} key={user.id}>{user.name} · {user.department}{user.enabled === false ? "（已停用）" : ""}</option>)}</select></label>
          : <span className="report-view-label">我的数据</span>}
        <button className="report-range-button" type="button" onClick={() => { setFiltersOpen(true); setExportOpen(false); }}><span>{shortDate(applied.dateFrom)}—{shortDate(applied.dateTo)}</span></button>
        <span className="report-generated-at">{report?.generatedAtUtc ? `更新于 ${new Date(report.generatedAtUtc).toLocaleString("zh-CN", { hour12: false })}` : "正在读取最新数据"}</span>
        <div className="report-header-actions">
          <button type="button" aria-expanded={filtersOpen} onClick={() => { setFiltersOpen((current) => !current); setExportOpen(false); }}><FunnelSimple size={17} />筛选</button>
          <button type="button" aria-expanded={exportOpen} onClick={() => { setExportOpen((current) => !current); setFiltersOpen(false); }}><DownloadSimple size={17} />导出 CSV</button>
        </div>
      </div>
    </header>

    <nav className="report-page-nav" role="tablist" aria-label="数据中心页面">
      {pages.map((page) => <button className={reportPage === page.id ? "active" : ""} type="button" role="tab" aria-selected={reportPage === page.id} onClick={() => setReportPage(page.id)} key={page.id}>{page.label}</button>)}
    </nav>

    {filtersOpen && <ReportFilters draft={draft} onChange={(field, value) => setDraft((current) => ({ ...current, [field]: value }))} onApply={applyFilters} onReset={resetFilters} rooms={rooms} tags={tags} busy={state === "loading"} />}
    {exportOpen && <section className="report-export-panel" aria-label="CSV 导出设置"><div><strong>办件明细</strong><span>当前范围预计 {exportCount} 条，CSV 含单位内部办件信息</span></div><label><span className="sr-only">记录状态</span><select value={exportStatus} onChange={(event) => setExportStatus(event.target.value)}><option value="">全部状态</option><option value="active">仅有效</option><option value="cancelled">仅已取消</option></select></label><button type="button" disabled={exporting || state !== "ready" || exportCount > 20000} onClick={downloadCsv}>{exporting ? <><CircleNotch className="spin" size={17} />正在生成</> : <><DownloadSimple size={17} />导出{isOverall ? "全单位" : scopeLabel}的服务记录</>}</button></section>}

    {state === "failed" && !report ? <section className="report-load-state" role="alert"><Info size={32} /><h2>数据暂时无法读取</h2><p>{message}</p><button type="button" onClick={loadReport}><ArrowClockwise size={17} />重新加载</button></section> : <>
      {reportPage === "overview" && <>
        <section className={`report-metrics ${state === "loading" ? "loading" : ""}`} aria-label="核心指标">
          <MetricCard label="有效预约" value={report?.summary?.activeCount ?? "—"} unit="场" />
          <MetricCard label="已结束" value={report?.summary?.endedCount ?? "—"} unit="场" />
          <MetricCard label="总时长" value={report ? duration.value : "—"} unit={report ? duration.unit : ""} />
          <MetricCard label="取消" value={report?.summary?.cancelledCount ?? "—"} unit="场" note={isOverall ? `取消率 ${formatCancellationRate(report?.summary?.cancellationRate)}` : undefined} tone="guardrail" />
        </section>
        <section className="report-section report-trend-section" role="tabpanel"><header><div><h2>预约{trend.granularity === "month" ? "月" : "周"}趋势</h2></div></header>{state === "loading" && !report ? <div className="report-chart-loading"><CircleNotch className="spin" size={24} />正在汇总</div> : <TrendChart granularity={trend.granularity} items={trend.items} metric={trendMetric} onMetricChange={setTrendMetric} />}</section>
      </>}

      {reportPage !== "overview" && <section className="report-focus-page" role="tabpanel">
        <header className="report-focus-heading"><div><h2>{pageHeading?.[0]}</h2><p>{pageHeading?.[1]}</p></div>{reportPage === "tags" && <span>共 {report?.summary?.activeCount ?? "—"} 场</span>}</header>
        {state === "loading" && !report ? <div className="report-chart-loading"><CircleNotch className="spin" size={24} />正在汇总</div> : <>
          {reportPage === "time" && <TimeDistribution items={report?.weekdayTimeDistribution} settings={settings} />}
          {reportPage === "rooms" && <RoomDistribution items={report?.roomWorkload} />}
          {reportPage === "tags" && <TagComposition items={isOverall ? report?.globalTagDistribution : report?.tagDistribution} />}
        </>}
      </section>}
      {message && <p className="report-message" role="status">{message}</p>}
    </>}
  </main>;
}
