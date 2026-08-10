import { createElement } from "react";

import { waitForSetupRestart } from "./domain.js";

export async function runSetupRestartTransition({
  probe,
  pause,
  attempts,
  intervalMs,
  stableChecks,
  onState = () => {},
  onReady = () => {},
} = {}) {
  onState("waiting");
  try {
    const health = await waitForSetupRestart({
      probe,
      pause,
      attempts,
      intervalMs,
      stableChecks,
    });
    onState("ready");
    await onReady(health);
    return health;
  } catch (error) {
    onState("failed");
    throw error;
  }
}

export function SetupRestartStatus({
  state,
  onRetry,
  waitingIndicator = null,
  failureIndicator = null,
} = {}) {
  const waiting = state === "waiting";
  const ready = state === "ready";
  const description = ready
    ? "服务已经以局域网模式重新上线，正在进入登录页。"
    : waiting
      ? "正在等待服务以局域网模式重新上线，确认可用后会自动进入登录页。"
      : "数据已经保存，但服务未能确认重新上线。请在服务器电脑运行“① 启动系统”，然后重新检查；仍失败请把 _程序文件\\logs 交给维护人员。";
  return createElement(
    "div",
    { className: "setup-complete", role: waiting || ready ? "status" : "alert", "data-restart-state": state },
    waiting || ready ? waitingIndicator : failureIndicator,
    createElement("h2", null, "首次配置已完成"),
    createElement("p", null, description),
    state === "failed"
      ? createElement("button", { className: "setup-primary-button", type: "button", onClick: onRetry }, "重新检查服务")
      : null,
  );
}
