// 3画面（ワークアイテム・計画ファイル・セッション）が共有するナビゲーション。
// 全画面は同じ文書に常駐し、画面登録契約は初期化処理`init`だけとする。
import {setDrawerOpen} from "./common.js";

const DRAWER_SCREENS = ["plans", "sessions"];
const screens = new Map();

// 画面の初期化処理を登録する。画面のモジュールは文書の解析後、`DOMContentLoaded`より前に評価されるため、
// 登録した全画面の`init`を`DOMContentLoaded`で実行する。
export function registerScreen(name, screen) {
  screens.set(name, screen);
}

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  for (const name of DRAWER_SCREENS) {
    const screen = document.getElementById(`screen-${name}`);
    if (!screen.hidden && screen.classList.contains("drawer-open")) {
      setDrawerOpen(name, false);
      document.getElementById(`${name}-menu-btn`).focus();
      event.preventDefault();
      break;
    }
  }
});

window.addEventListener("resize", () => {
  const narrow = window.matchMedia("(max-width: 768px)").matches;
  for (const name of DRAWER_SCREENS) {
    const screen = document.getElementById(`screen-${name}`);
    screen.querySelector(`#${name}-app > aside`).inert = narrow && !screen.classList.contains("drawer-open");
  }
});

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
  for (const screen of screens.values()) void screen.init();
});
