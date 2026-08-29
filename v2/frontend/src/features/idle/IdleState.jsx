/**
 * A quiet, recoverable zero-data state for the existing product canvases.
 * Page shells own their navigation and context; this component owns only the
 * icon, explanation, and one optional next action.
 */
export function IdleState({
  className = "",
  Icon,
  iconSize = 44,
  title,
  description,
  action = null,
  announce = false,
  tone = "neutral",
}) {
  const liveProps = announce ? { role: "status", "aria-live": "polite" } : {};
  return <section className={`idle-state idle-state--${tone} ${className}`.trim()} {...liveProps}>
    <Icon className="idle-state__icon" size={iconSize} weight="thin" aria-hidden="true" />
    <div className="idle-state__copy">
      <h2>{title}</h2>
      <p>{description}</p>
      {action && <button className={`idle-state__action idle-state__action--${action.variant || "primary"}`} type="button" onClick={action.onClick}>{action.label}</button>}
    </div>
  </section>;
}
