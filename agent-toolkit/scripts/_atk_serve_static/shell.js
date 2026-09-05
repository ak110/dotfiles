// 3画面（AWI・計画ファイル・セッション）が共有するナビゲーション。
// ナビゲーションのリンクを傍受し、ページ全体を再読み込みせずに画面を入れ替える。
//
// 各画面スクリプトは`window.__atkScreens`へ、`<body data-screen>`が示す画面名をキーとして
// `{mount, unmount}`を登録する。`mount`は現在のマウントかを返す関数を受け取り、
// 保持DOMを表示するたびに再開処理を適用する。要素リスナーとbootstrap読取は各画面の初回mountだけ、
// サーバーからの再読込と購読の再確立は毎回行う。`unmount`は
// SSE購読・監視・生成済みオブジェクトURL・`window`と`document`へ登録した購読を解放する。
// `unmount`は引数と戻り値を持たない。
// 保持中の画面DOMは切り離し、表示中の1画面だけを接続するため、同じ`id`を持つ要素は衝突しない。
window.__atkScreens = window.__atkScreens || {};

(() => {
  // 読み込み済み資産と画面ごとの保持内容。同じURLの取得と要素の生成を1回に限定する。
  const loadedAssets = new Set();
  const screenLoads = new Map();
  const stylesheetsByScreen = new Map();
  const sharedStylesheets = new Set();
  let currentScreen = null;
  let currentState = null;
  let mountGeneration = 0;
  let navigationGeneration = 0;

  function createContinuation(isCurrent) {
    const continuation = () => isCurrent();
    continuation.wait = async (pending) => {
      try {
        const value = await pending;
        if (isCurrent()) return value;
      } catch (error) {
        if (isCurrent()) throw error;
      }
      // 失効した処理の成功・失敗を呼び出し元へ返すと、catchやfinallyを含む継続が
      // 現行画面へ触れ得る。未完了のまま切り離し、継続をこの入口だけで遮断する。
      return new Promise(() => {});
    };
    return continuation;
  }

  function screenNameOf(doc) {
    return doc.body.getAttribute("data-screen") || "";
  }

  function initialScreenState() {
    const name = screenNameOf(document);
    const header = document.querySelector("header.app-header");
    const root = document.getElementById("screen-root");
    const state = {
      name,
      title: document.title,
      bodyClass: document.body.className,
      headerNodes: header ? Array.from(header.childNodes) : [],
      rootNodes: root ? Array.from(root.childNodes) : [],
      bootstrapNodes: Array.from(document.querySelectorAll('script[type="application/json"]')),
    };
    const stylesheets = new Set();
    for (const node of document.querySelectorAll("link[rel=stylesheet]")) {
      loadedAssets.add(node.href);
      if (node.href.endsWith("/static/shell.css")) sharedStylesheets.add(node);
      else stylesheets.add(node);
    }
    stylesheetsByScreen.set(name, stylesheets);
    for (const node of document.querySelectorAll("script[src]")) loadedAssets.add(node.src);
    screenLoads.set(location.href, Promise.resolve(state));
    return state;
  }

  function rememberDisplayedScreen(state) {
    const header = document.querySelector("header.app-header");
    const root = document.getElementById("screen-root");
    state.title = document.title;
    state.bodyClass = document.body.className;
    if (header) state.headerNodes = Array.from(header.childNodes);
    if (root) state.rootNodes = Array.from(root.childNodes);
  }

  function setActiveStylesheets(name) {
    for (const [screenName, nodes] of stylesheetsByScreen) {
      for (const node of nodes) node.disabled = screenName !== name;
    }
    for (const node of sharedStylesheets) node.disabled = false;
  }

  async function mountScreen(name) {
    const screen = window.__atkScreens[name];
    if (!screen) return;
    currentScreen = name;
    const generation = ++mountGeneration;
    const continuation = createContinuation(
      () => generation === mountGeneration && currentScreen === name,
    );
    // `mount`が初期表示のためにサーバーへ問い合わせる画面があるため、完了まで待つ。
    await continuation.wait(screen.mount(continuation));
  }

  function unmountScreen() {
    const screen = currentScreen ? window.__atkScreens[currentScreen] : null;
    currentScreen = null;
    mountGeneration++;
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

  function loadStylesheets(doc, name) {
    const pending = [];
    const stylesheets = new Set();
    for (const link of doc.querySelectorAll("link[rel=stylesheet]")) {
      const href = new URL(link.getAttribute("href"), location.href).href;
      if (href.endsWith("/static/shell.css")) continue;
      const existing = Array.from(document.querySelectorAll("link[rel=stylesheet]"))
        .find((node) => node.href === href);
      if (existing) {
        stylesheets.add(existing);
        continue;
      }
      if (loadedAssets.has(href)) continue;
      loadedAssets.add(href);
      const node = document.createElement("link");
      node.rel = "stylesheet";
      node.href = href;
      stylesheets.add(node);
      pending.push(loadAsset(node).then(() => { node.disabled = name !== currentScreen; }));
    }
    stylesheetsByScreen.set(name, stylesheets);
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

  function installBootstrapData(state) {
    for (const source of state.bootstrapNodes) {
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

  function showScreen(state) {
    document.title = state.title;
    document.body.className = state.bodyClass;
    document.body.setAttribute("data-screen", state.name);
    const header = document.querySelector("header.app-header");
    const root = document.getElementById("screen-root");
    if (header) header.replaceChildren(...state.headerNodes);
    if (root) root.replaceChildren(...state.rootNodes);
    installBootstrapData(state);
    setActiveStylesheets(state.name);
  }

  async function fetchScreen(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`画面を取得できません (${response.status})`);
    const text = await response.text();
    const doc = new DOMParser().parseFromString(text, "text/html");
    const name = screenNameOf(doc);
    const header = doc.querySelector("header.app-header");
    const root = doc.getElementById("screen-root");
    if (!name || !header || !root) throw new Error("画面の構造が不正です");
    const state = {
      name,
      title: doc.title,
      bodyClass: doc.body.className,
      headerNodes: Array.from(header.childNodes),
      rootNodes: Array.from(root.childNodes),
      bootstrapNodes: Array.from(doc.querySelectorAll('script[type="application/json"]')),
    };
    await loadStylesheets(doc, name);
    await loadScreenScripts(doc);
    return state;
  }

  function loadScreen(url) {
    const absolute = new URL(url, location.href).href;
    if (!screenLoads.has(absolute)) screenLoads.set(absolute, fetchScreen(absolute));
    return screenLoads.get(absolute);
  }

  async function navigate(url, {push}) {
    const generation = ++navigationGeneration;
    const continuation = createContinuation(() => generation === navigationGeneration);
    let state = null;
    try {
      state = await continuation.wait(loadScreen(url));
    } catch (_) {
      // 取得と資産の読み込みに失敗した画面は中途半端な状態のため、通常のページ遷移でやり直す。
      location.assign(url);
      return;
    }
    if (currentState) rememberDisplayedScreen(currentState);
    unmountScreen();
    showScreen(state);
    currentState = state;
    // 画面の初期表示はサーバーへの問い合わせを伴うため、資産がそろった時点で履歴を進める。
    if (push) history.pushState({atkShell: true}, "", url);
    await continuation.wait(mountScreen(state.name));
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
  document.addEventListener("DOMContentLoaded", async () => {
    const initial = initialScreenState();
    currentScreen = initial.name;
    currentState = initial;
    setActiveStylesheets(initial.name);
    await mountScreen(initial.name);
    const navigation = document.querySelector("nav.app-nav");
    if (!navigation) return;
    for (const link of navigation.querySelectorAll("a[href]")) {
      const url = new URL(link.href, location.href).href;
      if (url !== location.href) void loadScreen(url).catch(() => {});
    }
  });
})();
