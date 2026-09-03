// 3画面（フィードバック・計画ファイル・セッション）が共有するナビゲーション。
// ナビゲーションのリンクを傍受し、ページ全体を再読み込みせずに画面を入れ替える。
//
// 各画面スクリプトは`window.__atkScreens`へ、`<body data-screen>`が示す画面名をキーとして
// `{mount, unmount}`を登録する。`mount`は差し替え後のDOMへ初期化を適用し、`unmount`は
// SSE購読・監視・生成済みオブジェクトURL・`window`と`document`へ登録した購読を解放する。
// いずれも引数と戻り値を持たない。
// 画面ごとのDOMは同時に1つだけ存在するため、3画面が同じ`id`を持つ要素は衝突しない。
window.__atkScreens = window.__atkScreens || {};

(() => {
  // 読み込み済みの画面スクリプトとスタイルシートのURL。同じ資産を二重に読み込まないために持つ。
  const loadedAssets = new Set();
  let currentScreen = null;

  function screenNameOf(doc) {
    return doc.body.getAttribute("data-screen") || "";
  }

  function registerExistingAssets() {
    for (const node of document.querySelectorAll("script[src], link[rel=stylesheet]")) {
      loadedAssets.add(node.src || node.href);
    }
  }

  async function mountScreen(name) {
    const screen = window.__atkScreens[name];
    if (!screen) return;
    currentScreen = name;
    // `mount`が初期表示のためにサーバーへ問い合わせる画面があるため、完了まで待つ。
    await screen.mount();
  }

  function unmountScreen() {
    const screen = currentScreen ? window.__atkScreens[currentScreen] : null;
    currentScreen = null;
    if (screen) screen.unmount();
  }

  function loadAsset(node) {
    // `document.head`へ追加した要素の`load`を待ち、`mount`が参照する資産をそろえてから画面を起動する。
    return new Promise((resolve, reject) => {
      node.addEventListener("load", () => resolve());
      node.addEventListener("error", () => reject(new Error(`資産を読み込めません: ${node.src || node.href}`)));
      document.head.append(node);
    });
  }

  function loadStylesheets(doc) {
    const pending = [];
    for (const link of doc.querySelectorAll("link[rel=stylesheet]")) {
      const href = new URL(link.getAttribute("href"), location.href).href;
      if (loadedAssets.has(href)) continue;
      loadedAssets.add(href);
      const node = document.createElement("link");
      node.rel = "stylesheet";
      node.href = href;
      pending.push(loadAsset(node));
    }
    return Promise.all(pending);
  }

  function loadScreenScripts(doc) {
    const pending = [];
    for (const script of doc.querySelectorAll("script[src]")) {
      const src = new URL(script.getAttribute("src"), location.href).href;
      if (loadedAssets.has(src)) continue;
      loadedAssets.add(src);
      const node = document.createElement("script");
      node.src = src;
      pending.push(loadAsset(node));
    }
    return Promise.all(pending);
  }

  function replaceBootstrapData(doc) {
    // 画面の初期値はサーバーが要求ごとにJSONブロックへ埋め込むため、遷移先の内容へ入れ替える。
    for (const source of doc.querySelectorAll('script[type="application/json"]')) {
      const existing = document.getElementById(source.id);
      if (existing) {
        existing.textContent = source.textContent;
        continue;
      }
      const node = document.createElement("script");
      node.type = "application/json";
      node.id = source.id;
      node.textContent = source.textContent;
      document.body.append(node);
    }
  }

  function replaceScreenContent(doc) {
    document.title = doc.title;
    document.body.setAttribute("data-screen", screenNameOf(doc));
    const header = document.querySelector("header.app-header");
    const nextHeader = doc.querySelector("header.app-header");
    if (header && nextHeader) header.replaceChildren(...nextHeader.childNodes);
    const root = document.getElementById("screen-root");
    const nextRoot = doc.getElementById("screen-root");
    if (root && nextRoot) root.replaceChildren(...nextRoot.childNodes);
    replaceBootstrapData(doc);
  }

  async function swapScreen(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`画面を取得できません (${response.status})`);
    const doc = new DOMParser().parseFromString(await response.text(), "text/html");
    unmountScreen();
    replaceScreenContent(doc);
    await loadStylesheets(doc);
    await loadScreenScripts(doc);
    return doc;
  }

  async function navigate(url, {push}) {
    let doc = null;
    try {
      doc = await swapScreen(url);
    } catch (_) {
      // 取得と資産の読み込みに失敗した画面は中途半端な状態のため、通常のページ遷移でやり直す。
      location.assign(url);
      return;
    }
    // 画面の初期表示はサーバーへの問い合わせを伴うため、資産がそろった時点で履歴を進める。
    if (push) history.pushState({atkShell: true}, "", url);
    await mountScreen(screenNameOf(doc));
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
    const url = new URL(link.href, location.href).href;
    if (url === location.href) return;
    void navigate(url, {push: true});
  });

  window.addEventListener("popstate", () => {
    void navigate(location.href, {push: false});
  });

  // 画面スクリプトは本スクリプトより後に読み込まれ、登録は同期的に完了する。
  document.addEventListener("DOMContentLoaded", () => {
    registerExistingAssets();
    void mountScreen(screenNameOf(document));
  });
})();
