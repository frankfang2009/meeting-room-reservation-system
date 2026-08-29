export function HelpCenter() {
  return <main className="main-canvas help-reader-canvas" tabIndex={0}>
    <header className="help-reader-toolbar">
      <div>
        <h1>帮助中心</h1>
        <span>完整离线说明</span>
      </div>
    </header>
    <iframe className="help-reader-frame" src="/help/?embedded=1" title="帮助中心" />
  </main>;
}
