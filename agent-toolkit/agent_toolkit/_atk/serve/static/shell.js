// 3画面（ワークアイテム・計画ファイル・セッション）が共有するナビゲーション。
// 全画面は同じ文書に常駐し、画面登録契約は初期化処理だけを公開する。
window.__atkScreens = window.__atkScreens || {};

// 3画面のSSE購読の共通処理。接続の確立、heartbeat、通知のいずれも届かない時間が閾値を超えたら再接続する。
// サーバーのheartbeatはスクリプトから観測できる名前付きイベント`heartbeat`で届く。
// コメント行のheartbeatは`EventSource`がスクリプトへ渡さないため、接続が途中で止まっても検知できない。
// 閾値はサーバーがheartbeat間隔の3回分として`sse-bootstrap`へ埋め込む。
window.__atkSse = (() => {
  const DEFAULT_STALL_MS = 45000;

  function stallMs() {
    const bootstrap = document.getElementById("sse-bootstrap");
    const value = bootstrap ? JSON.parse(bootstrap.textContent).stall_ms : undefined;
    return Number.isFinite(value) && value > 0 ? value : DEFAULT_STALL_MS;
  }

  // `handlers`は`open`・`message`・`error`と、画面が受け取る名前付きイベント名から処理への対応を持つ。
  // 再接続した接続でも同じ処理を登録し、確立時の`open`で各画面が再同期する。
  function connect(url, handlers) {
    const limit = stallMs();
    let source = null;
    let lastSeen = 0;
    let timer = null;
    const touch = () => { lastSeen = Date.now(); };

    function open() {
      source = new EventSource(url);
      touch();
      source.addEventListener("heartbeat", touch);
      for (const [name, handler] of Object.entries(handlers)) {
        source.addEventListener(name, (event) => {
          if (name !== "error") touch();
          handler(event);
        });
      }
    }

    function check() {
      if (!source) return;
      if (source.readyState === EventSource.CLOSED || Date.now() - lastSeen > limit) {
        source.close();
        open();
      }
    }

    function close() {
      clearInterval(timer);
      timer = null;
      if (source) source.close();
      source = null;
    }

    function start() {
      if (source) return;
      open();
      timer = setInterval(check, Math.max(100, Math.min(5000, limit / 3)));
    }

    start();
    return {close, reopen: start};
  }

  return {connect};
})();

window.__atkDrawer = {
  set(screenName, open) {
    const screen = document.getElementById(`screen-${screenName}`);
    const button = document.getElementById(`${screenName}-menu-btn`);
    const sidebar = screen.querySelector("#" + screenName + "-app > aside");
    screen.classList.toggle("drawer-open", open);
    button.setAttribute("aria-expanded", String(open));
    sidebar.inert = window.matchMedia("(max-width: 768px)").matches && !open;
    if (open) document.getElementById(`${screenName}-filter`).focus();
    else if (screen.contains(document.activeElement) && sidebar.contains(document.activeElement)) button.focus();
  },
};

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  for (const name of ["plans", "sessions"]) {
    const screen = document.getElementById(`screen-${name}`);
    if (!screen.hidden && screen.classList.contains("drawer-open")) {
      window.__atkDrawer.set(name, false);
      document.getElementById(`${name}-menu-btn`).focus();
      event.preventDefault();
      break;
    }
  }
});

window.addEventListener("resize", () => {
  const narrow = window.matchMedia("(max-width: 768px)").matches;
  for (const name of ["plans", "sessions"]) {
    const screen = document.getElementById(`screen-${name}`);
    screen.querySelector("#" + name + "-app > aside").inert = narrow && !screen.classList.contains("drawer-open");
  }
});

(() => {
  function screenNameFromUrl(url) {
    const path = new URL(url, location.href).pathname.replace(/\/$/, "");
    if (path.endsWith("/plans")) return "plans";
    if (path.endsWith("/sessions")) return "sessions";
    return "wi";
  }

  function showScreen(name, focusHeading = false) {
    document.body.dataset.screen = name;
    document.title = `${{wi: "ワークアイテム", plans: "計画ファイル", sessions: "セッション"}[name]} - atk serve`;
    for (const screen of document.querySelectorAll(".screen")) {
      screen.hidden = screen.id !== `screen-${name}`;
    }
    for (const link of document.querySelectorAll("nav.app-nav a[href]")) {
      const current = screenNameFromUrl(link.href) === name;
      if (current) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
    if (focusHeading) document.querySelector(`#screen-${name} h1`).focus();
  }

  function isSameOriginNavigation(link, event) {
    if (event.defaultPrevented) return false;
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return false;
    if (link.target && link.target !== "_self") return false;
    if (link.hasAttribute("download")) return false;
    return new URL(link.href, location.href).origin === location.origin;
  }

  document.addEventListener("click", (event) => {
    const link = event.target.closest("nav.app-nav a[href]");
    if (!link || !isSameOriginNavigation(link, event)) return;
    event.preventDefault();
    const url = new URL(link.href, location.href);
    const name = screenNameFromUrl(url);
    if (name === document.body.dataset.screen) return;
    history.pushState({atkShell: true}, "", url);
    showScreen(name, true);
  });

  window.addEventListener("popstate", () => showScreen(screenNameFromUrl(location.href), true));

  document.addEventListener("DOMContentLoaded", () => {
    const initialName = document.body.dataset.screen;
    showScreen(initialName);
    for (const screen of Object.values(window.__atkScreens)) void screen.init();
  });
})();
