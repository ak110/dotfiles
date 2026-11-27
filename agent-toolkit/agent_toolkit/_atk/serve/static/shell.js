// 全画面は同じ文書に常駐し、各画面が登録する処理は初期化処理`init`だけとする。
const screens = new Map();

// 画面の初期化処理を登録する。画面のモジュールは文書の解析後、`DOMContentLoaded`より前に評価されるため、
// 登録した全画面の`init`を`DOMContentLoaded`で実行する。
export function registerScreen(name, screen) {
  screens.set(name, screen);
}

function screenNameFromUrl(url) {
  const path = new URL(url, location.href).pathname.replace(/\/$/, "");
  if (path.endsWith("/plans")) return "plans";
  if (path.endsWith("/sessions")) return "sessions";
  if (path.endsWith("/agents")) return "agents";
  if (path.endsWith("/usage")) return "usage";
  if (path.endsWith("/settings")) return "settings";
  return "wi";
}

const lastScreenUrls = new Map();
let currentScreenUrl = location.href;

function showScreen(name, focusHeading = false) {
  currentScreenUrl = location.href;
  document.body.dataset.screen = name;
  document.title = `${{wi: "ワークアイテム", plans: "計画ファイル", sessions: "セッション", agents: "エージェント", settings: "設定", usage: "利用枠"}[name]} - atk serve`;
  for (const screen of document.querySelectorAll(".screen")) {
    screen.hidden = screen.id !== `screen-${name}`;
  }
  for (const picker of document.querySelectorAll(".app-nav-picker")) picker.value = name;
  for (const link of document.querySelectorAll("nav.app-nav a[href]")) {
    const current = screenNameFromUrl(link.href) === name;
    if (current) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  if (focusHeading) document.querySelector(`#screen-${name} h1`).focus();
  document.dispatchEvent(new CustomEvent("atk-screen-change", {detail: name}));
}

function isSameOriginNavigation(link, event) {
  if (event.defaultPrevented) return false;
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return false;
  if (link.target && link.target !== "_self") return false;
  if (link.hasAttribute("download")) return false;
  return new URL(link.href, location.href).origin === location.origin;
}

document.addEventListener("click", (event) => {
  const skip = event.target.closest("a.skip-content");
  if (skip && isSameOriginNavigation(skip, event)) {
    event.preventDefault();
    document.getElementById(skip.hash.slice(1))?.focus();
    return;
  }
  const link = event.target.closest("nav.app-nav a[href]");
  if (!link || !isSameOriginNavigation(link, event)) return;
  event.preventDefault();
  navigateTo(link.href);
});

function restoreTooltip(event) {
  event.target.closest?.("[data-tooltip-dismissed]")?.removeAttribute("data-tooltip-dismissed");
}
document.addEventListener("focusout", restoreTooltip);
document.addEventListener("pointerover", restoreTooltip);
document.addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  const tooltip = document.querySelector("[data-tooltip]:focus-visible, [data-tooltip]:hover");
  if (!tooltip || tooltip.hasAttribute("data-tooltip-dismissed")) return;
  tooltip.setAttribute("data-tooltip-dismissed", "");
  event.preventDefault();
});

function navigateTo(href) {
  const url = new URL(href, location.href);
  const name = screenNameFromUrl(url);
  if (name === document.body.dataset.screen || !canSwitchScreen(name)) return;
  lastScreenUrls.set(document.body.dataset.screen, location.href);
  history.pushState({atkShell: true}, "", lastScreenUrls.get(name) ?? url);
  showScreen(name, true);
}

function canSwitchScreen(name) {
  return document.dispatchEvent(new CustomEvent("atk-screen-will-change", {detail: name, cancelable: true}));
}

window.addEventListener("popstate", () => {
  const name = screenNameFromUrl(location.href);
  if (name !== document.body.dataset.screen && !canSwitchScreen(name)) {
    history.pushState({atkShell: true}, "", currentScreenUrl);
    return;
  }
  showScreen(name, true);
});

document.addEventListener("DOMContentLoaded", () => {
  const initialName = document.body.dataset.screen;
  for (const nav of document.querySelectorAll(".app-nav")) {
    const picker = document.createElement("select");
    picker.className = "app-nav-picker";
    picker.setAttribute("aria-label", "画面を切り替える");
    const links = new Map();
    const groups = new Map();
    for (const link of nav.querySelectorAll("a[href]")) {
      const name = screenNameFromUrl(link.href);
      links.set(name, link.href);
      const label = link.dataset.navGroup;
      if (!groups.has(label)) {
        const group = document.createElement("optgroup");
        group.label = label;
        groups.set(label, group);
        picker.append(group);
      }
      groups.get(label).append(new Option(link.textContent, name));
    }
    picker.addEventListener("change", () => {
      navigateTo(links.get(picker.value));
      picker.value = document.body.dataset.screen;
    });
    nav.append(picker);
  }
  showScreen(initialName);
  void screens.get(initialName)?.init();
  for (const [name, screen] of screens) {
    if (name !== initialName) void screen.init();
  }
});
