// 3画面（ワークアイテム・計画ファイル・セッション）が共有するナビゲーション。
// 全画面は同じ文書に常駐し、画面登録契約は初期化処理だけを公開する。
window.__atkScreens = window.__atkScreens || {};

(() => {
  const SCREEN_TITLES = {
    wi: "ワークアイテム",
    plans: "計画ファイル",
    sessions: "セッション",
  };

  function screenNameFromUrl(url) {
    const path = new URL(url, location.href).pathname.replace(/\/$/, "");
    if (path.endsWith("/plans")) return "plans";
    if (path.endsWith("/sessions")) return "sessions";
    return "wi";
  }

  function showScreen(name) {
    document.body.dataset.screen = name;
    document.title = SCREEN_TITLES[name];
    for (const screen of document.querySelectorAll(".screen")) {
      screen.hidden = screen.id !== `screen-${name}`;
    }
    for (const link of document.querySelectorAll("nav.app-nav a[href]")) {
      const current = screenNameFromUrl(link.href) === name;
      if (current) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
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
    showScreen(name);
  });

  window.addEventListener("popstate", () => showScreen(screenNameFromUrl(location.href)));

  document.addEventListener("DOMContentLoaded", async () => {
    const initialName = document.body.dataset.screen;
    showScreen(initialName);
    await window.__atkScreens[initialName].init();
    for (const [name, screen] of Object.entries(window.__atkScreens)) {
      if (name !== initialName) void screen.init();
    }
  });
})();
