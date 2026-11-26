import {decorateWiReferences} from "./wi_reference.js";
import {BASE_PATH, attachScrollEdges, beginOperation, isSelected, setDrawerOpen, timedFetch, readFailure, recordUpdateSuccess, setUpdateError, setDisplayedReadError} from "./common.js";

const KIND_LABELS = {
  user: "ユーザー",
  developer: "開発者",
  injected: "自動挿入",
  assistant: "アシスタント",
  thinking: "思考・要約",
  tool_call: "ツール呼び出し",
  tool_result: "ツール結果",
  compact_boundary: "コンテキスト圧縮",
};

export function createSessionDetail({ENGINE_LABELS, sessionDiagnostic, formatTime, getSessions, onSelect}) {
  // サブエージェントの記録は左ペインの一覧に現れないため、委譲元の記録を古い順に保持して戻れるようにする。  // pyfltr: allow-comment
  let parentTrail = [];
  // 詳細の取得の世代。選択の切り替えや再取得の後に届いた古い応答を反映しないために使う。  // pyfltr: allow-comment
  let detailGeneration = 0;
  let renderLimit = 0;
  const DETAIL_PAGE_SIZE = 100;
  let currentDetail = null;
  const FOLLOW_TAIL_PX = 48;
  // 描画済みの末尾からこの距離以内まで読み進めたら、続きのイベントを描画する。左ペインの番兵の`rootMargin`と同じ値とする。  // pyfltr: allow-comment
  const RENDER_AHEAD_PX = 400;

  let selected = null;
  let detailEl, detailTitleEl, detailUsageEl;
  function usageText(usage) {
    if (!usage) return "";
    const parts = [];
    for (const [key, label] of [["input_tokens", "入力"], ["output_tokens", "出力"]]) {
      const value = usage[key];
      if (Number.isFinite(value)) parts.push(`${label} ${value.toLocaleString()}`);
    }
    return parts.join(" · ");
  }

  function detailCost(detail) {
    const family = detail.cost;
    if (!family) return null;
    const listed = getSessions().find(item => isSelected({host: detail.host, engine: detail.engine, path: detail.path}, item));
    const own = parentTrail.length > 0 || Boolean(listed?.parent_path);
    const amount = own ? family.breakdown?.self : family;
    return amount && Number.isFinite(amount.known_usd) ? {amount, own, pricing: family.pricing} : null;
  }

  function costText(cost, pending = false) {
    if (!cost) return pending ? "推定費用: 算出中" : "";
    const amount = cost.amount;
    const unknown = amount.unknown_responses || 0;
    const models = Object.entries(amount.unknown_models || {})
      .map(([model, count]) => `${model || "モデル名なし"} ${count.toLocaleString()}件`).join("、");
    const unknownText = `単価不明 ${unknown.toLocaleString()}件${models ? `（${models}）` : ""}`;
    if (!amount.known_usd && (unknown || amount.incomplete)) {
      return unknown ? `推定費用: ${unknownText}` : "推定費用: 一部未算出";
    }
    const value = amount.known_usd > 0 && amount.known_usd < 0.0001 ? "<$0.0001"
      : new Intl.NumberFormat("ja-JP", {style: "currency", currency: "USD", maximumFractionDigits: 4}).format(amount.known_usd);
    const parts = [`推定${unknown || amount.incomplete ? "（既知分）" : ""} ${value} USD`];
    if (unknown) parts.push(unknownText);
    if (amount.incomplete) parts.push("一部未算出");
    return parts.join(" · ");
  }

  function appendUnavailable(parent, label) {
    const span = document.createElement("span");
    span.className = "unavailable";
    span.textContent = label;
    parent.append(span);
  }

  function toolInputSummary(event) {
    let input = event.detail?.input;
    if (typeof input === "string") {
      try {
        input = JSON.parse(input);
      } catch (_) {
        return "";
      }
    }
    if (!input || typeof input !== "object" || Array.isArray(input)) return "";
    if (typeof input.command === "string") return input.command.split(/\r?\n/, 1)[0];
    if (typeof input.file_path === "string") return input.file_path;
    return Object.values(input).find((value) => typeof value === "string") || "";
  }

  function renderEvent(event, index, linkTarget, nested = false) {
    const block = document.createElement("details");
    block.className = `event kind-${event.kind}`;
    if (matchesLinkTarget(event, linkTarget)) block.classList.add("event-linked");
    block.dataset.eventIndex = String(index);
    if (event.line != null) {
      block.dataset.recordLine = String(event.line);
      block.dataset.recordOrdinal = String(event.ordinal);
    }
    // 発言と本文のある思考・要約は読む対象として開き、ツールの入出力などは要求された時だけ1項目ずつ展開する。  // pyfltr: allow-comment
    const hasThinkingText =
      event.kind === "thinking" && (typeof event.html === "string" || (typeof event.text === "string" && event.text.trim() !== ""));
    const hasDeveloperText = event.kind === "developer" && typeof event.text === "string" && event.text.trim() !== "" && !event.text.trimStart().startsWith("<atk-auto");
    block.open = event.kind === "user_report" || event.initial_work === true || event.kind === "user" || event.kind === "assistant" || hasDeveloperText || hasThinkingText;
    if (!block.open) {
      block.dataset.exclusiveEvent = "true";
    }

    const summary = document.createElement("summary");
    const kind = document.createElement("span");
    kind.className = "event-kind status-chip";
    kind.dataset.state = ({user: "inbox", assistant: "success", tool_call: "warning"})[event.kind] || "";
    const delivery = {delivered: "成功", unavailable: "非対応", rejected: "拒否", unknown: "不明"};
    kind.textContent = event.kind === "user_report" ? `ユーザーへの報告（${delivery[event.delivery_status]}）` :
      event.conversationLabel || KIND_LABELS[event.kind] || event.kind;
    const time = document.createElement("span");
    time.className = "event-time";
    time.textContent = event.timestamp ? formatTime(event.timestamp) : "時刻なし";
    summary.append(kind, time);
    if (block.dataset.exclusiveEvent) {
      summary.addEventListener("click", () => {
        if (block.open) return;
        for (const other of detailEl.querySelectorAll('details[data-exclusive-event="true"]')) {
          if (other !== block) other.open = false;
        }
      });
    }
    if (event.name && event.kind !== "user_report") {
      const name = document.createElement("span");
      name.textContent = event.name;
      summary.append(name);
    }
    const preview = event.kind === "tool_call" ? toolInputSummary(event) :
      !block.open && !nested && (event.kind === "tool_result" || event.kind === "injected") ?
        (event.text?.trimStart().startsWith("<atk-auto") ? "自動で追加された情報" :
          event.text?.trim().split(/\r?\n/, 1)[0] || (event.detail || event.html ? "本文あり" : "本文なし")) : "";
    if (preview) {
      const excerpt = document.createElement("span");
      excerpt.className = "event-input-summary text-clip";
      excerpt.textContent = preview;
      excerpt.title = preview;
      summary.append(excerpt);
    }
    if (event.line != null && selected) {
      const link = document.createElement("a");
      link.className = "event-link";
      link.href = eventUrl(event, conversationOnly && event.kind !== "tool_call");
      link.textContent = event.kind === "tool_call" && conversationOnly ? "全イベントで開く" : "発話リンク";
      if (link.textContent === "発話リンク") {
        link.title = "この発話へのリンク";
        link.setAttribute("aria-label", "この発話へのリンク");
      }
      summary.append(link);
    }
    block.append(summary);

    // アシスタントの発言はサーバーが生HTMLを無効にして整形したHTMLを持ち、本文の装飾で表示する。  // pyfltr: allow-comment
    // 他の種別はツールの入出力や挿入本文の記号をそのまま読めるよう、等幅の`<pre>`で表示する。  // pyfltr: allow-comment
    if (typeof event.html === "string") {
      const formatted = document.createElement("div");
      formatted.className = "markdown-body event-markdown";
      formatted.innerHTML = event.html;
      block.append(formatted);
      decorateWiReferences(formatted);
      if (!conversationOnly && !nested && Number.isInteger(event.result_index)) {
        block.append(renderEvent(currentDetail.events[event.result_index], event.result_index, linkTarget, true));
      }
      return block;
    }
    const body = document.createElement("pre");
    if (typeof event.text === "string" && event.text !== "") {
      const message = event.initial_work && event.kind === "injected" ?
        event.text.trim().match(/^<atk-auto\b[^>]*>([\s\S]*)<\/atk-auto>$/u) : null;
      body.textContent = message && !message[1].includes("<atk-auto") ? message[1].trim() : event.text;
      if (body.textContent !== event.text) {
        const payload = document.createElement("details");
        payload.className = "event-payload";
        const label = document.createElement("summary");
        label.textContent = "配送時の原文";
        const source = document.createElement("pre");
        source.textContent = event.text;
        payload.append(label, source);
        block.append(body, payload);
        decorateWiReferences(body);
        return block;
      }
    } else if (event.detail !== null && event.detail !== undefined) {
      body.textContent = JSON.stringify(event.detail, null, 2);
    } else {
      appendUnavailable(body, "本文は記録に含まれていません");
    }
    block.append(body);
    decorateWiReferences(body);
    return block;
  }

  function renderSubagents(detail) {
    const container = document.createElement("div");
    container.className = "subagents";
    const heading = document.createElement("h2");
    heading.textContent = "サブエージェント";
    container.append(heading);
    for (const subagent of detail.subagents) {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "subagent-item";
      if (subagent.path) item.dataset.focusKey = subagent.path;
      // 起動の深さを字下げで表す。`parent_agent_id`は深さ2以上の記録にしか現れないため使わない。  // pyfltr: allow-comment
      const depth = Number.isInteger(subagent.spawn_depth) ? subagent.spawn_depth : 0;
      item.style.paddingInlineStart = `calc(var(--space-3) + ${Math.max(0, depth - 1)} * var(--space-4))`;
      const type = document.createElement("span");
      type.className = "subagent-type";
      type.textContent = subagent.agent_type || "(種別不明)";
      const description = document.createElement("span");
      description.className = "subagent-description";
      description.textContent = subagent.description || "";
      item.append(type, description);
      if (subagent.path) {
        item.addEventListener("click", () => openSession(detail.host, "claude", subagent.path, [...parentTrail, selected]));
      } else {
        item.disabled = true;
        const status = document.createElement("span");
        status.className = "secondary-text";
        status.textContent = "記録なし";
        item.append(status);
      }
      container.append(item);
    }
    return container;
  }

  function renderBack() {
    const trail = parentTrail;
    const parent = trail[trail.length - 1];
    const button = document.createElement("button");
    button.type = "button";
    button.className = "detail-back button-secondary";
    button.dataset.focusKey = JSON.stringify([parent.host, parent.engine, parent.path]);
    button.textContent = "委譲元の記録へ戻る";
    button.addEventListener("click", () => openSession(parent.host, parent.engine, parent.path, trail.slice(0, -1)));
    return button;
  }

  // イベントは記録の先頭からの順で並び、追記で既存の位置は変わらないため、位置を識別に使う。  // pyfltr: allow-comment
  function captureDetailState() {
    const openStates = new Map();
    for (const block of detailEl.querySelectorAll("details[data-event-index]")) {
      openStates.set(block.dataset.eventIndex, block.open);
    }
    return {openStates, focus: captureDetailFocus(), renderLimit, scrollTop: detailEl.parentElement.scrollTop};
  }

  function focusMatches(scope, target) {
    const {tag, key} = target;
    return [...scope.querySelectorAll(tag)].filter(node =>
      (node.dataset.focusKey ?? node.getAttribute("href")) === key &&
      (node.closest("[data-event-index]") ?? detailEl) === scope);
  }

  function captureDetailFocus() {
    const active = document.activeElement;
    if (!detailEl.contains(active)) return null;
    const scope = active.closest("[data-event-index]") ?? detailEl;
    const target = {
      event: scope.dataset.eventIndex,
      tag: active.localName,
      key: active.dataset.focusKey ?? active.getAttribute("href"),
    };
    return {...target, ordinal: focusMatches(scope, target).indexOf(active)};
  }

  function restoreDetailFocus(target) {
    if (!target) return;
    const scope = target.event === undefined ? detailEl :
      detailEl.querySelector(`[data-event-index="${CSS.escape(target.event)}"]`);
    if (!scope) return;
    focusMatches(scope, target)[target.ordinal]?.focus({preventScroll: true});
  }

  // ローカルの記録は本文の後に費用を別に取得するため、取得中は前回の値か「算出中」を表示する。  // pyfltr: allow-comment
  function renderCost(detail) {
    const cost = detailCost(detail);
    detailUsageEl.textContent = costText(cost, Boolean(detail.cost_pending));
    const pricing = detailEl.querySelector(".detail-pricing");
    if (!pricing) return;
    pricing.hidden = !cost;
    pricing.textContent = cost
      ? `推定費用（API料金換算）: ${cost.own ? "この記録" : "子孫を含む"} / 料金表基準日: ${cost.pricing?.as_of || "取得不能"}`
      : "";
  }

  // 推定費用は子孫の全記録を読むため、本文の表示を待たせないよう別の要求で取得する。  // pyfltr: allow-comment
  // 子孫の多い記録では算出に十数秒かかり、追記のたびに詳細を取り直す記録では、取り直しごとに結果を捨てると  // pyfltr: allow-comment
  // 金額が表示されないまま算出だけが重なる。そのため算出中の要求は記録ごとに1件へまとめ、届いた結果は  // pyfltr: allow-comment
  // その時点で表示している同じ記録の詳細へ反映する。算出中に記録が追記された場合は、届いた後にもう1回だけ取り直す。  // pyfltr: allow-comment
  // 別の記録を開いた時点で前の記録の要求は中止する。ブラウザーの同じホストへの同時接続数は限られ（HTTP/1.1で6本、  // pyfltr: allow-comment
  // うち3本はSSEが使う）、算出の長い要求が残ると次の詳細の取得が接続の空きを待つためである。  // pyfltr: allow-comment
  let costRequest = null;

  function costKey(detail) {
    return `${detail.host}\n${detail.engine}\n${detail.path}`;
  }

  function cancelOtherCost(host, engine, path) {
    if (!costRequest || costRequest.key === costKey({host, engine, path})) return;
    costRequest.controller.abort();
    costRequest = null;
  }

  async function loadDeferredCost(detail) {
    if (!detail.cost_pending) return;
    const key = costKey(detail);
    if (costRequest?.key === key) {
      costRequest.stale = true;
      return;
    }
    costRequest?.controller.abort();
    const request = {key, stale: false, controller: new AbortController()};
    costRequest = request;
    const {host, engine, path} = detail;
    let cost = null;
    let failed = false;
    try {
      const response = await timedFetch(
        "sessions", `${BASE_PATH}/api/sessions/cost?${new URLSearchParams({host, engine, path}).toString()}`,
        {signal: request.controller.signal}
      );
      failed = readFailure(response.status) === "temporary";
      if (response.ok) cost = (await response.json()).cost;
    } catch {
      failed = true;
    }
    if (costRequest !== request) return;
    costRequest = null;
    const shown = currentDetail;
    if (!shown || costKey(shown) !== key) return;
    if (!failed) {
      if (cost) shown.cost = cost;
      setUpdateError("sessions", "cost");
    } else if (!request.stale) {
      setDisplayedReadError("sessions", "cost", "費用を取得できません。", () => {
        if (currentDetail && costKey(currentDetail) === key) {
          currentDetail.cost_pending = true;
          void loadDeferredCost(currentDetail);
        }
      });
    }
    shown.cost_pending = request.stale;
    renderCost(shown);
    if (request.stale) void loadDeferredCost(shown);
  }

  let conversationOnly = false;
  let visibleEvents = [];
  let searchMatches = [];
  let searchPosition = -1;
  let updateScrollEdges = () => {};
  function eventUrl(event, inConversation = conversationOnly) {
    const url = new URL(BASE_PATH + "/sessions", location.href);
    url.search = new URLSearchParams({
      ...selected, ...(inConversation ? {view: "conversation"} : {}),
      line: String(event.line), ordinal: String(event.ordinal),
    });
    return url.href;
  }
  function linkTargetFromUrl() {
    if (!location.pathname.endsWith("/sessions")) return null;
    const params = new URLSearchParams(location.search);
    const [host, engine, path] = ["host", "engine", "path"].map(key => params.get(key));
    if (!params.has("line") || !isSelected(selected, {host, engine, path})) return null;
    return {line: Number(params.get("line")), ordinal: params.has("ordinal") ? Number(params.get("ordinal")) : null};
  }
  function matchesLinkTarget(event, target) {
    return target && event.line === target.line && (target.ordinal === null || event.ordinal === target.ordinal);
  }
  function detailEvents(detail) {
    if (conversationOnly) return (detail.conversation ?? []).flatMap(row => {
      const source = detail.events[row.index];
      if (!source) return [];
      const event = {...source, text: row.text};
      if (row.rank === 1) {
        event.conversationLabel = source.kind === "user" || source.kind === "injected" ? "委譲元からの依頼" :
          row.text ? "委譲先の最終発話" : "委譲先の発話未観測";
        if (!row.text) {
          event.kind = "injected";
          delete event.detail;
          delete event.html;
        }
      } else if (row.rank === 3) {
        event.conversationLabel = row.label.replace(/^- /u, "").replace(/ record:\d+/u, "").replace(/: $/u, "");
      }
      return [{index: row.index, event}];
    });
    return detail.events.flatMap((event, index) => event.report_index == null ? [{event, index}] : []);
  }
  function appendEvents(end, preserved) {
    const linkTarget = linkTargetFromUrl();
    for (const {event, index} of visibleEvents.slice(renderLimit, end)) {
      const block = renderEvent(event, index, linkTarget);
      const open = preserved?.openStates.get(String(index));
      if (open !== undefined) block.open = open;
      const nested = block.querySelector("details[data-event-index]");
      if (nested) nested.open = preserved?.openStates.get(nested.dataset.eventIndex) ?? nested.open;
      detailEl.append(block);
    }
    // 描画を要求した範囲を保ち、次の追記もこの範囲内ならスクロールせず表示する。  // pyfltr: allow-comment
    renderLimit = Math.max(renderLimit, end);
    updateScrollEdges();
  }
  function currentPosition() {
    const top = detailScroller().getBoundingClientRect().top + document.getElementById("session-reading").offsetHeight;
    return [...detailEl.querySelectorAll(":scope > [data-event-index]")]
      .find(block => block.getBoundingClientRect().bottom > top)?.dataset.eventIndex ?? 0;
  }
  function moveToEvent(index) {
    const position = visibleEvents.findIndex(item => item.index === index ||
      (!conversationOnly && item.event.result_index === index));
    if (position < 0) return null;
    if (position >= renderLimit) appendEvents(position + 1);
    const target = detailEl.querySelector(`[data-event-index="${index}"]`);
    if (!target) return null;
    for (let node = target; node && node !== detailEl; node = node.parentElement) {
      if (node.tagName === "DETAILS") node.open = true;
    }
    target.scrollIntoView({block: "start"});
    return target;
  }
  function moveToUrlEvent() {
    const linkTarget = linkTargetFromUrl();
    if (!linkTarget) return;
    const matches = currentDetail.events.flatMap((event, index) => matchesLinkTarget(event, linkTarget) ? [index] : []);
    const target = matches.map(moveToEvent).filter(Boolean);
    target[0]?.scrollIntoView({block: "start"});
  }
  function currentSearch() { return document.getElementById("session-search").value.trim(); }
  function refreshSearch(move = false) {
    const needle = currentSearch().toLowerCase();
    const inputs = conversationOnly ? visibleEvents : (currentDetail?.events ?? []).map((event, index) => ({event, index}));
    searchMatches = needle ? inputs.flatMap(({event, index}) => {
      const value = [event.text, event.name, JSON.stringify(event.detail ?? "")].filter(Boolean).join("\n").toLowerCase();
      return value.includes(needle) ? [index] : [];
    }) : [];
    searchPosition = searchMatches.length ? 0 : -1;
    showSearch(move);
  }
  function showSearch(move) {
    const count = document.getElementById("session-search-count");
    count.textContent = currentSearch() ? `${searchPosition + 1} / ${searchMatches.length}件` : "";
    for (const id of ["session-search-prev", "session-search-next"]) document.getElementById(id).disabled = !searchMatches.length;
    CSS.highlights?.delete("session-search");
    if (!move || searchPosition < 0) return;
    const target = moveToEvent(searchMatches[searchPosition]);
    if (!target) return;
    detailEl.querySelector(".event-search-match")?.classList.remove("event-search-match");
    target.classList.add("event-search-match");
    if (!CSS.highlights || typeof Highlight === "undefined") return;
    const query = currentSearch().toLowerCase();
    const walker = document.createTreeWalker(target, NodeFilter.SHOW_TEXT);
    const ranges = [];
    while (walker.nextNode()) {
      const node = walker.currentNode;
      const value = node.textContent.toLowerCase();
      for (let start = value.indexOf(query); start >= 0; start = value.indexOf(query, start + query.length)) {
        if (start + query.length > node.length) continue;
        const range = new Range();
        range.setStart(node, start);
        range.setEnd(node, start + query.length);
        ranges.push(range);
      }
    }
    CSS.highlights.set("session-search", new Highlight(...ranges));
  }
  function moveUser(delta) {
    const current = Number(currentPosition());
    const candidates = visibleEvents.filter(({event, index}) => event.user_navigation && (index - current) * delta > 0);
    const target = delta === 1 ? candidates[0] : candidates.at(-1);
    if (target) moveToEvent(target.index);
  }
  function setConversation(checked) {
    if (!currentDetail) return;
    const current = Number(currentPosition());
    conversationOnly = checked;
    const url = new URL(location.href);
    if (checked) url.searchParams.set("view", "conversation");
    else url.searchParams.delete("view");
    url.searchParams.delete("line");
    url.searchParams.delete("ordinal");
    history.replaceState(history.state, "", url);
    renderDetail(currentDetail);
    const anchor = visibleEvents.findLast(({index}) => index <= current);
    if (anchor) moveToEvent(anchor.index);
  }

  function renderDetail(detail, preserved = null) {
    currentDetail = detail;
    // ホスト名と作業ディレクトリは左ペインの一覧項目が示すため、見出しはエンジン名と取得できたセッション名に限る。  // pyfltr: allow-comment
    const engineLabel = ENGINE_LABELS[detail.engine] || detail.engine;
    detailTitleEl.textContent = detail.session_name ? `${engineLabel} / ${detail.session_name}` : engineLabel;
    detailEl.replaceChildren();

    if (parentTrail.length > 0) {
      detailEl.append(renderBack());
    }

    const meta = document.createElement("div");
    meta.className = "secondary-text";
    meta.classList.add("session-detail-meta");
    for (const value of [
      detail.started_at ? `開始: ${formatTime(detail.started_at)}` : "",
      usageText(detail.usage),
    ].filter(Boolean)) {
      const item = document.createElement("span");
      item.textContent = value;
      meta.append(item);
    }
    const id = document.createElement("details");
    const title = document.createElement("summary");
    title.textContent = "記録の識別子";
    id.append(title, detail.session_id);
    meta.append(id);
    detailEl.append(meta);
    const pricing = document.createElement("div");
    pricing.className = "secondary-text detail-pricing";
    detailEl.append(pricing);
    renderCost(detail);

    if (Array.isArray(detail.subagents) && detail.subagents.length > 0) {
      detailEl.append(renderSubagents(detail));
    } else if (detail.subagents_unavailable) {
      // サブエージェントが無い場合は何も表示しないため、有無を判定できなかったことは明示して区別する。  // pyfltr: allow-comment
      const note = document.createElement("div");
      note.className = "secondary-text";
      appendUnavailable(note, sessionDiagnostic(detail, "サブエージェントの一覧を取得できません（リモートホストのdotfilesを更新すると表示されます）"));
      detailEl.append(note);
    }

    if (detail.broken_lines > 0) {
      const broken = document.createElement("div");
      broken.className = "session-warning";
      broken.textContent = sessionDiagnostic(detail, `解析できない行が${detail.broken_lines}件あります`);
      detailEl.append(broken);
    }

    visibleEvents = detailEvents(detail);
    renderLimit = 0;
    document.getElementById("session-reading").hidden = false;
    appendEvents(preserved ? preserved.renderLimit : DETAIL_PAGE_SIZE, preserved);
    restoreDetailFocus(preserved?.focus);
    refreshSearch();
    detailScroller().scrollTop = preserved ? preserved.scrollTop : 0;
    renderEventsNearEnd();
  }

  // 短い記録でスクロールが生じない場合も、末尾が近い限り続けて記録の最後まで描画する。  // pyfltr: allow-comment
  // 追加の描画ではスクロール位置を動かさず、エンドユーザーが読んでいる位置を保つ。  // pyfltr: allow-comment
  function renderEventsNearEnd() {
    if (!currentDetail) return;
    const scroller = detailScroller();
    while (
      renderLimit < visibleEvents.length
      && scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <= RENDER_AHEAD_PX
    ) {
      appendEvents(renderLimit + DETAIL_PAGE_SIZE);
    }
  }
  function detailScroller() {
    return detailEl.parentElement;
  }

  function isReadingTail() {
    if (!currentDetail || renderLimit < visibleEvents.length) return false;
    const scroller = detailScroller();
    return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <= FOLLOW_TAIL_PX;
  }

  function showDetailFailure(status) {
    setUpdateError("sessions", "detail");
    detailTitleEl.textContent = "セッションの詳細";
    document.getElementById("session-reading").hidden = true;
    detailEl.replaceChildren();
    const alert = document.createElement("p");
    alert.setAttribute("role", "status");
    alert.textContent = sessionDiagnostic(selected, readFailure(status) === "missing" ?
      "記録が見つかりません。" : "記録を取得できません。");
    detailEl.append(alert);
  }

  // 末尾を読んでいた場合は追記の末尾へ追従し、それ以外は位置を保つ。末尾より上を読んでいる間に届いた追記は、  // pyfltr: allow-comment
  // エンドユーザーが末尾まで読み進めた時点で`renderEventsNearEnd`が描画する。  // pyfltr: allow-comment
  async function refreshSelectedDetail(timing = {}) {
    if (!selected) return;
    const {host, engine, path} = selected;
    const generation = ++detailGeneration;
    try {
      const response = await timedFetch(
        "sessions", `${BASE_PATH}/api/sessions/detail?${new URLSearchParams({host, engine, path}).toString()}`, {fresh: true}, timing
      );
      if (!response.ok) {
        if (readFailure(response.status) === "temporary") throw new Error("取得失敗");
        if (generation === detailGeneration) showDetailFailure(response.status);
        return;
      }
      const detail = await response.json();
      if (generation !== detailGeneration || !isSelected(selected, {host, engine, path})) return;
      recordUpdateSuccess("sessions", "detail");
      const previousCount = currentDetail ? currentDetail.events.length : detail.events.length;
      const readingTail = isReadingTail();
      // 追記のたびに見出しが「算出中」へ戻らないよう、新しい費用が届くまで前回の値を表示する。  // pyfltr: allow-comment
      if (detail.cost_pending && currentDetail?.cost && currentDetail.path === detail.path && currentDetail.host === detail.host) {
        detail.cost = currentDetail.cost;
      }
      if (currentDetail) {
        renderDetail(detail, {...captureDetailState(), renderLimit: readingTail ? detail.events.length : renderLimit});
      } else {
        renderDetail(detail);
        moveToUrlEvent();
      }
      void loadDeferredCost(detail);
      if (readingTail && detail.events.length > previousCount) {
        const scroller = detailScroller();
        scroller.scrollTop = scroller.scrollHeight;
      }
    } catch {
      if (generation === detailGeneration) {
        setDisplayedReadError("sessions", "detail", "セッションの詳細を取得できません。", refreshSelectedDetail);
      }
    }
  }

  // `trail`は開こうとする記録の委譲元を古い順に並べる。左ペインから選んだ記録には委譲元が無いため、省略した場合は空とする。  // pyfltr: allow-comment
  async function openSession(host, engine, path, trail = [], updateUrl = true, closeDrawer = true) {
    if (closeDrawer) setDrawerOpen("sessions", false);
    cancelOtherCost(host, engine, path);
    selected = { host, engine, path };
    if (updateUrl && location.pathname.endsWith("/sessions")) {
      const url = new URL(location.href);
      url.search = new URLSearchParams({host, engine, path}).toString();
      history.pushState({atkSession: true}, "", url);
    }
    parentTrail = trail;
    currentDetail = null;
    setUpdateError("sessions", "detail");
    setUpdateError("sessions", "cost");
    conversationOnly = new URLSearchParams(location.search).get("view") === "conversation";
    document.getElementById("session-conversation").checked = conversationOnly;
    document.getElementById("session-reading").hidden = true;
    onSelect(selected);
    detailEl.replaceChildren();
    detailTitleEl.textContent = detailUsageEl.textContent = "";
    const pendingStatus = document.createElement("p");
    pendingStatus.className = "inline-loading";
    pendingStatus.setAttribute("role", "status");
    pendingStatus.textContent = "読み込み中";
    detailEl.append(pendingStatus);
    const query = new URLSearchParams({ host, engine, path });
    const generation = ++detailGeneration;
    const operation = beginOperation("sessions", "open-session", {trigger: "click"});
    let outcome = "ok";
    try {
      const response = await (timedFetch("sessions", `${BASE_PATH}/api/sessions/detail?${query.toString()}`, {}, operation.timing));
      if (!response.ok) {
        const error = new Error(`記録を取得できません (${response.status})`);
        error.status = response.status;
        throw error;
      }
      const detail = await (response.json());
      if (generation !== detailGeneration) {
        outcome = "superseded";
        return;
      }
      renderDetail(detail);
      moveToUrlEvent();
      void loadDeferredCost(detail);
    } catch (error) {
      outcome = "error";
      if (generation !== detailGeneration) {
        outcome = "superseded";
        return;
      }
      if (readFailure(error.status) === "temporary") {
        detailTitleEl.textContent = "";
        setDisplayedReadError("sessions", "detail", "セッションの詳細を取得できません。", refreshSelectedDetail);
        return;
      }
      showDetailFailure(error.status);
    } finally {
      operation.end(outcome);
    }
  }
  function init() {
    detailEl = document.getElementById("detail");
    detailTitleEl = document.getElementById("detail-title");
    detailUsageEl = document.getElementById("detail-usage");
    const controls = document.getElementById("session-reading");
    new ResizeObserver(() => detailEl.style.setProperty("--reading-height", `${controls.offsetHeight}px`)).observe(controls);
    const disclosure = controls.querySelector(".reading-controls");
    const compact = matchMedia("(max-width: 48rem)");
    disclosure.open = !compact.matches;
    compact.addEventListener("change", event => { disclosure.open = !event.matches; });
    updateScrollEdges = attachScrollEdges(detailScroller(), controls.querySelector(".reading-navigation"), detailEl, {
      beforeEnd: () => appendEvents(visibleEvents.length),
      hasMore: () => renderLimit < visibleEvents.length,
    });
    document.getElementById("session-conversation").addEventListener("change", event => setConversation(event.target.checked));
    document.getElementById("session-search").addEventListener("input", () => refreshSearch(true));
    for (const [name, delta] of [["prev", -1], ["next", 1]]) {
      document.getElementById(`session-user-${name}`).addEventListener("click", () => moveUser(delta));
      document.getElementById(`session-search-${name}`).addEventListener("click", () => {
        if (!searchMatches.length) return;
        searchPosition = (searchPosition + delta + searchMatches.length) % searchMatches.length;
        showSearch(true);
      });
    }
    detailScroller().addEventListener("scroll", renderEventsNearEnd, {passive: true});
  }
  function clear(message) {
    setUpdateError("sessions", "detail");
    setUpdateError("sessions", "cost");
    selected = currentDetail = null;
    parentTrail = [];
    detailGeneration++;
    detailTitleEl.textContent = detailUsageEl.textContent = "";
    detailEl.textContent = message;
    document.getElementById("session-reading").hidden = true;
  }
  return {init, clear, openSession, refreshSelectedDetail, isChild: () => parentTrail.length > 0};
}
