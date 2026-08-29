import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

// App 初始渲染读取 window.location；模块图本身不触碰其它浏览器 API。
// SSR 渲染不执行 effect，登录页初始状态足以覆盖密码可见性图标的渲染路径。
globalThis.window = globalThis.window ?? {
  location: { pathname: "/" },
  addEventListener() {},
  removeEventListener() {},
};

test("login page renders through the real module graph without missing icon imports", async () => {
  const server = await createServer({
    root: frontendRoot,
    logLevel: "error",
    server: { middlewareMode: true, hmr: false },
    appType: "custom",
    plugins: [{
      name: "expose-login-for-render-test",
      enforce: "pre",
      transform(code, id) {
        if (!id.endsWith("/src/App.jsx")) return null;
        return `${code}\nexport { Login as __LoginForRenderTest };\n`;
      },
    }],
  });
  try {
    const { App, __LoginForRenderTest: Login } = await server.ssrLoadModule("/src/App.jsx");
    assert.equal(typeof App, "function");
    assert.equal(typeof Login, "function");
    // Eye/EyeSlash 缺导入时，这里在渲染阶段抛 ReferenceError 而不是源码字符串不匹配。
    const markup = renderToStaticMarkup(
      React.createElement(Login, { onAuthenticated: () => {}, onRecovery: () => {} }),
    );
    assert.match(markup, /id="login-username"/);
    assert.match(markup, /id="login-password"/);
    assert.match(markup, /aria-label="显示密码"/);
    assert.match(markup, /帮助中心/);
  } finally {
    await server.close();
  }
});
