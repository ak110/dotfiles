import {BASE_PATH, beginOperation, diagnostic, readFailure, recordUpdateSuccess, setDisplayedReadError, setUpdateError, timedFetch} from "./common.js";
import {decorateWiReferences} from "./wi_reference.js";

export function planPreviewScroller() {
  return document.querySelector("#preview .review-scroll") ?? document.querySelector("#screen-plans main");
}

export function createPlanPreview({
  filePageUrl, fileQuery, fileSelectionFromUrl, fileSource, getFiles, getRootDirs,
  isMobileViewport, refreshFiles, renderFiles, showPlanList,
}) {
  const selection = {host: null, source: "", path: null};
  // 図の描画まで終えたHTMLだけを記憶し、再同期で同じ内容なら開いた要素・図・読み位置を保つ。 pyfltr: allow-comment
  let appliedPreviewHtml = null;

  let mermaidLoadPromise = null;
  const MERMAID_CDN_URL = "https://cdn.jsdelivr.net/npm/mermaid@12.0.0/dist/mermaid.min.js";
  const MERMAID_CDN_INTEGRITY = "sha384-xzghz1GQ5u9HCpVskeDPqMsdogD1yvuMQbEK53+wi+G70+6J1AG0L2cfi9PHjDWI";
  // CDNが応答を返さないと読込は完了も失敗もせず、プレビューが描画中のまま戻らない。上限を過ぎたら図の位置へエラーを表示する。 pyfltr: allow-comment
  const MERMAID_LOAD_TIMEOUT_MS = 30000;
  const PREVIEW_LOADING_DELAY_MS = 500;
  const MISSING_SELECTION_MESSAGE = "選択した計画ファイルは移動または削除されたため表示できません。一覧を更新しました。";
  let previewGeneration = 0;
  let previewLoadingTimer = null;
  let copyStatusTimer = null;
  let previewObjectUrls = new Set();

  function rootEntries(host) {
    const value = getRootDirs()[host];
    if (!value) return {};
    if (value.root || value.portable_root || value.source_id) {
      return {[value.source_id || ""]: value};
    }
    return value;
  }

  function rootInfo(host, source) {
    return rootEntries(host)[source || ""] || null;
  }

  function planTarget(host = selection.host, source = selection.source, path = selection.path) {
    const info = rootInfo(host, source);
    const root = info?.portable_root || info?.root || "保存元未取得";
    return path ? `${root}/${path}` : root;
  }

  function planDiagnostic(reason, browser = false) {
    const target = browser ? `${selection.host} / ${planTarget()}` : planTarget();
    return diagnostic("計画", browser ? "ブラウザー" : selection.host, target, reason);
  }

  function updateCopyPathButton(host, source) {
    if (!source) {
      // 旧単一root形式との互換。source IDが無い要求では従来のホスト判定を使う。 pyfltr: allow-comment
      document.getElementById("copy-path-btn").disabled = !(host in getRootDirs());
      return;
    }
    document.getElementById("copy-path-btn").disabled = !rootInfo(host, source);
  }

  function updatePlanIdentity() {
    const block = document.getElementById("meta-mobile");
    const heading = document.getElementById("plan-title");
    const context = document.getElementById("plan-context");
    context.hidden = !selection.host || !selection.path;
    if (!selection.host || !selection.path) {
      heading.textContent = "計画を選ぶ";
      block.textContent = "";
      return;
    }
    const selected = getFiles().find(f => f.host === selection.host && fileSource(f) === selection.source && f.path === selection.path);
    block.replaceChildren();
    const hostSpan = document.createElement("span");
    hostSpan.className = "meta-host";
    hostSpan.textContent = selection.host;
    const ctimeSpan = document.createElement("span");
    ctimeSpan.className = "meta-ctime";
    ctimeSpan.textContent = selected ? selected.ctime : "";
    const pathSpan = document.createElement("span");
    pathSpan.className = "meta-path";
    heading.textContent = selection.path.split(/[\\/]/u).at(-1) || selection.path;
    pathSpan.textContent = selection.path;
    block.append(pathSpan, hostSpan, ctimeSpan);
  }

  async function applyPreviewHtml(html, scrollTop, generation) {
    if (generation !== previewGeneration) return;
    revokePreviewObjectUrls();
    const preview = document.getElementById("preview");
    preview.innerHTML = html;
    const review = preview.querySelector(":scope > .review-table");
    preview.classList.toggle("review-preview", Boolean(review));
    if (review) {
      const region = document.createElement("div");
      region.className = "review-scroll";
      region.tabIndex = 0;
      region.setAttribute("role", "region");
      region.setAttribute("aria-label", "レビュー指摘一覧");
      review.before(region);
      region.append(review);
    }
    decorateWiReferences(preview);
    appliedPreviewHtml = null;
    for (const link of preview.querySelectorAll("a[data-plan-path]")) {
      link.href = filePageUrl(selection.host, link.dataset.planPath, selection.source);
    }
    await (renderDiagrams(preview, generation));
    if (generation !== previewGeneration) return;
    appliedPreviewHtml = html;
    planPreviewScroller().scrollTop = scrollTop;
  }

  async function renderDiagrams(preview, generation) {
    renderSvgDiagrams(preview.querySelectorAll(".diagram-svg"));
    await (renderMermaidDiagrams(preview.querySelectorAll(".mermaid-output"), generation));
  }

  function loadMermaid() {
    if (mermaidLoadPromise) return mermaidLoadPromise;
    const loading = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      const timer = setTimeout(() => {
        // 上限を過ぎた要素の遅れた完了は、次の読込と重ねて初期化しないよう無視する。 pyfltr: allow-comment
        script.onload = null;
        script.onerror = null;
        script.remove();
        reject(new Error(`Mermaidの読み込みが${MERMAID_LOAD_TIMEOUT_MS / 1000}秒以内に終わりませんでした`));
      }, MERMAID_LOAD_TIMEOUT_MS);
      script.src = MERMAID_CDN_URL;
      script.integrity = MERMAID_CDN_INTEGRITY;
      script.crossOrigin = "anonymous";
      script.onload = () => {
        clearTimeout(timer);
        window.mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
        });
        resolve(window.mermaid);
      };
      script.onerror = () => {
        clearTimeout(timer);
        reject(new Error("Mermaidの読み込みに失敗しました"));
      };
      document.head.appendChild(script);
    });
    // 失敗した読込は保持せず、次の描画で読み込み直す。一時的な失敗の後も、再表示で図を描画できる。 pyfltr: allow-comment
    mermaidLoadPromise = loading.catch((error) => {
      mermaidLoadPromise = null;
      throw error;
    });
    return mermaidLoadPromise;
  }

  async function renderMermaidDiagrams(nodes, generation) {
    if (nodes.length === 0) return;
    let api;
    try {
      api = await (loadMermaid());
    } catch (error) {
      if (generation !== previewGeneration) return;
      for (const node of nodes) showDiagramError(node.closest("figure"), error.message);
      return;
    }
    if (generation !== previewGeneration) return;
    for (const node of nodes) {
      const source = node.textContent;
      try {
        await (api.run({nodes: [node]}));
        disableMermaidNavigation(node);
        if (generation !== previewGeneration) return;
      } catch (_) {
        if (generation !== previewGeneration) return;
        node.textContent = source;
        showDiagramError(node.closest("figure"), "Mermaid図を描画できませんでした");
      }
    }
  }

  function disableMermaidNavigation(node) {
    for (const anchor of node.querySelectorAll("a")) {
      anchor.removeAttribute("href");
      anchor.removeAttribute("xlink:href");
      anchor.removeAttribute("target");
    }
  }

  function renderSvgDiagrams(figures) {
    const target = fileQuery(selection.host, selection.path, selection.source);
    for (const figure of figures) {
      const source = figure.querySelector(".diagram-source pre").textContent;
      const image = figure.querySelector(".svg-output");
      const url = URL.createObjectURL(new Blob([source], {type: "image/svg+xml"}));
      previewObjectUrls.add(url);
      image.onerror = () => {
        // 同じHTMLの再同期では画像DOMとURLを再利用し、要求の世代だけが進む。 pyfltr: allow-comment
        if (figure.isConnected && target === fileQuery(selection.host, selection.path, selection.source)) {
          showDiagramError(figure, "SVG図を描画できませんでした");
        }
      };
      image.src = url;
    }
  }

  function revokePreviewObjectUrls() {
    for (const url of previewObjectUrls) URL.revokeObjectURL(url);
    previewObjectUrls.clear();
  }

  function showDiagramError(figure, message) {
    let error = figure.querySelector(".diagram-error");
    if (!error) {
      error = document.createElement("p");
      error.className = "diagram-error";
      figure.appendChild(error);
    }
    error.textContent = planDiagnostic(message, true);
    error.hidden = false;
  }

  function beginPreviewLoading(generation) {
    if (previewLoadingTimer !== null) clearTimeout(previewLoadingTimer);
    const indicator = document.getElementById("plans-loading-indicator");
    const preview = document.getElementById("preview");
    indicator.hidden = true;
    preview.setAttribute("aria-busy", "true");
    previewLoadingTimer = setTimeout(() => {
      if (generation !== previewGeneration) return;
      previewLoadingTimer = null;
      indicator.hidden = false;
    }, PREVIEW_LOADING_DELAY_MS);
  }

  function endPreviewLoading(generation) {
    if (generation !== previewGeneration) return;
    if (previewLoadingTimer !== null) clearTimeout(previewLoadingTimer);
    previewLoadingTimer = null;
    document.getElementById("plans-loading-indicator").hidden = true;
    document.getElementById("preview").setAttribute("aria-busy", "false");
  }

  function showPreviewError(retry, code = 503) {
    if (readFailure(code) === "temporary") {
      setDisplayedReadError("plans", "preview", "計画の本文を取得できません。", retry);
      return;
    }
    setUpdateError("plans", "preview");
    document.getElementById("preview").replaceChildren();
    appliedPreviewHtml = null;
    const status = document.getElementById("plans-selection-status");
    status.textContent = code === 403 ? "計画の本文を読む権限がありません。" : "計画の本文を取得できません。";
    status.hidden = false;
    const alert = document.createElement("p");
    alert.setAttribute("role", "status");
    alert.textContent = status.textContent;
    document.getElementById("preview").append(alert);
  }

  function showCopyError(message) {
    const preview = document.getElementById("preview");
    let alert = preview.querySelector(".copy-error");
    if (!alert) {
      alert = document.createElement("p");
      alert.className = "copy-error";
      alert.setAttribute("role", "alert");
      preview.prepend(alert);
    }
    alert.textContent = message;
  }

  function announceCopy(message = "") {
    const status = document.getElementById("plan-copy-status");
    document.querySelector("#preview .copy-error")?.remove();
    clearTimeout(copyStatusTimer);
    status.textContent = message;
    if (message) copyStatusTimer = setTimeout(() => { status.textContent = ""; }, 2500);
  }

  // 一覧の項目は外部操作（計画の保存による別rootへの移動や削除）で消え得る。同じ対象へは再試行しない。 pyfltr: allow-comment
  function showMissingSelection() {
    setUpdateError("plans", "preview");
    const message = planDiagnostic(MISSING_SELECTION_MESSAGE);
    selection.host = null;
    selection.source = "";
    selection.path = null;
    announceCopy();
    document.getElementById("copy-btn").disabled = true;
    document.getElementById("copy-path-btn").disabled = true;
    if (fileSelectionFromUrl()) history.replaceState({atkPlan: true}, "", `${BASE_PATH}/plans`);
    const preview = document.getElementById("preview");
    preview.replaceChildren();
    appliedPreviewHtml = null;
    const status = document.createElement("div");
    status.setAttribute("role", "status");
    status.textContent = message;
    preview.append(status);
    const sidebarStatus = document.getElementById("plans-selection-status");
    sidebarStatus.textContent = message;
    sidebarStatus.hidden = false;
    if (isMobileViewport()) showPlanList({focus: document.querySelector("#plans-app main").contains(document.activeElement)});
    void refreshFiles();
  }

  async function updatePreview(timing = {}) {
    if (!selection.path || !selection.host) return;
    const scrollTop = planPreviewScroller().scrollTop;
    const generation = ++previewGeneration;
    beginPreviewLoading(generation);
    try {
      const res = await timedFetch(
        "plans", BASE_PATH + "/api/plans/file?" + fileQuery(selection.host, selection.path, selection.source), {fresh: true}, timing
      );
      if (generation !== previewGeneration) return;
      if (res.status === 404) {
        showMissingSelection();
        return;
      }
      if (!res.ok) {
        showPreviewError(updatePreview, res.status);
        return;
      }
      const html = await (res.text());
      if (generation !== previewGeneration) return;
      recordUpdateSuccess("plans", "preview");
      document.getElementById("plans-selection-status").hidden = true;
      if (html === appliedPreviewHtml) return;
      await (applyPreviewHtml(html, scrollTop, generation));
    } catch (error) {
      if (generation === previewGeneration) showPreviewError(updatePreview);
    } finally {
      endPreviewLoading(generation);
    }
  }

  async function openFile(host, path, source, focusDetail = true) {
    // 一覧はSSEで同期する。選択のたびに取り直さず、プレビューまでの往復を減らす。 pyfltr: allow-comment
    if (host !== selection.host || path !== selection.path || (source || "") !== selection.source) {
      setUpdateError("plans", "preview");
      announceCopy();
      document.getElementById("plan-context").open = false;
      document.getElementById("preview").replaceChildren();
      document.getElementById("preview").classList.remove("review-preview");
      appliedPreviewHtml = null;
    }
    selection.host = host;
    selection.source = source || "";
    selection.path = path;
    const sidebarStatus = document.getElementById("plans-selection-status");
    sidebarStatus.hidden = true;
    sidebarStatus.textContent = "";
    const selected = source
      ? getFiles().find(f => f.host === host && fileSource(f) === selection.source && f.path === path)
      : getFiles().find(f => f.host === host && f.path === path);
    if (selected) selection.source = fileSource(selected);
    if (fileSelectionFromUrl()) {
      history.replaceState({atkPlan: true}, "", filePageUrl(host, path, selection.source));
    }
    document.getElementById("copy-btn").disabled = false;
    // 選択したrootの情報が未取得の間（リモート接続の確立前）はdisabledのまま保つ。 pyfltr: allow-comment
    updateCopyPathButton(host, selection.source);
    renderFiles();
    if (focusDetail) document.getElementById("plans-app").dataset.view = "detail";
    const main = document.querySelector("#plans-app main");
    main.scrollTop = 0;
    if (focusDetail && isMobileViewport()) document.getElementById("plan-title").focus();
    const generation = ++previewGeneration;
    beginPreviewLoading(generation);
    // 所要は選択からプレビューの表示（Mermaidなどの非同期の図の描画を含む）までを1件の操作として残す。 pyfltr: allow-comment
    const operation = beginOperation("plans", "open-file", {trigger: "click"});
    let outcome = "ok";
    try {
      const res = await timedFetch("plans", BASE_PATH + "/api/plans/file?" + fileQuery(host, path, selection.source), {}, operation.timing);
      if (generation !== previewGeneration) {
        outcome = "superseded";
        return;
      }
      if (res.status === 404) {
        showMissingSelection();
        if (main) main.scrollTop = 0;
        return;
      }
      if (!res.ok) {
        showPreviewError(updatePreview, res.status);
        if (main) main.scrollTop = 0;
        return;
      }
      const html = await (res.text());
      if (generation !== previewGeneration) {
        outcome = "superseded";
        return;
      }
      recordUpdateSuccess("plans", "preview");
      await (applyPreviewHtml(html, 0, generation));
      if (generation !== previewGeneration) outcome = "superseded";
    } catch (error) {
      outcome = "error";
      if (generation === previewGeneration) showPreviewError(updatePreview);
    } finally {
      endPreviewLoading(generation);
      operation.end(outcome);
    }
  }

  async function copySelectedRaw() {
    if (!selection.path || !selection.host) return;
    const target = planTarget();
    let copying = false;
    const {host, path, source} = selection;
    const stillSelected = () => host === selection.host && path === selection.path && source === selection.source;
    announceCopy();
    try {
      const res = await timedFetch("plans", BASE_PATH + "/api/plans/raw?" + fileQuery(host, path, source));
      if (!res.ok) throw new Error("status " + res.status);
      const text = await (res.text());
      copying = true;
      await (navigator.clipboard.writeText(text));
      if (stillSelected()) announceCopy("本文をコピーしました");
    } catch (e) {
      if (stillSelected()) showCopyError(diagnostic("計画", copying ? "ブラウザー" : host, copying ? `${host} / ${target}` : target, `コピーに失敗しました: ${e.message}`));
    }
  }

  async function copySelectedPath() {
    if (!selection.path || !selection.host) return;
    const info = rootInfo(selection.host, selection.source);
    if (!info) return;
    const target = `${selection.host} / ${planTarget()}`;
    const {host, path, source} = selection;
    const stillSelected = () => host === selection.host && path === selection.path && source === selection.source;
    announceCopy();
    // 計画ディレクトリはホームと一致しないため、~ と%USERPROFILE%の置換基準はホームにする。 pyfltr: allow-comment
    let absolutePath;
    if (info.portable_root) {
      absolutePath = info.portable_root + "/" + selection.path;
    } else if (info.os_type === "nt") {
      const winRoot = info.root.split("/").join("\\\\");
      const winHome = info.home.split("/").join("\\\\");
      const winRelative = selection.path.split("/").join("\\\\");
      absolutePath = (winRoot + "\\\\" + winRelative).replace(winHome, "%USERPROFILE%");
    } else {
      absolutePath = (info.root + "/" + selection.path).replace(info.home, "~");
    }
    try {
      await (navigator.clipboard.writeText(absolutePath));
      if (stillSelected()) announceCopy("パスをコピーしました");
    } catch (e) {
      if (stillSelected()) showCopyError(diagnostic("計画", "ブラウザー", target, `コピーに失敗しました: ${e.message}`));
    }
  }

  return {
    selection,
    get generation() { return previewGeneration; },
    beginPreviewLoading, copySelectedPath, copySelectedRaw, openFile,
    planTarget, updateCopyPathButton, updatePlanIdentity, updatePreview,
  };
}
