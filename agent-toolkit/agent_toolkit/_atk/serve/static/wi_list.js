import {readFailure} from "./common.js";
import {recordUpdateSuccess, setUpdateError, setDisplayedReadError} from "./common.js";
import {pushHandlesNotifications} from "./wi_push.js";
import {
  ENTRY_PAGE_SIZE, PROCESSABLE_STATES, byId, entryKey, wiState
} from "./wi_state.js";
import {
  reloadFromExternalChange
} from "./wi.js";
import {
  buildQuery, renderEntries, setDisplayMode
} from "./wi_list_render.js";
import {
  api, setGlobalError
} from "./wi_feedback.js";
import {
  renderWaitingProgress
} from "./wi_waiting.js";
import {
  updateCreateBranchChoices
} from "./wi_create.js";

function captureListLoadingView() {
  return {
    indicator: byId('loading-indicator'),
    list: byId('entry-list'),
    previous: byId('previous-page-button'),
    next: byId('next-page-button'),
    status: byId('pagination-status')
  };
}

export function renderListLoading(view = captureListLoadingView()) {
  view.indicator.hidden = !(wiState.listLoadingVisible || wiState.userListOperationPending || wiState.detailRequestPending);
  view.list.setAttribute('aria-busy', String(wiState.listRequestPending || wiState.userListOperationPending));
  renderPagination(view);
}

export function beginUserListOperation() {
  // 一覧条件を変える操作が始まった時点で、それより前に始めた一覧取得の応答を無効にする。
  // 検索欄のデバウンス中も旧取得の一覧反映を防ぐため。
  // 無効にした取得のendListRequestは世代不一致で何もしないので、その処理中表示もここで解く。
  wiState.listRequestGeneration += 1;
  wiState.listRequestPending = false;
  wiState.listLoadingVisible = false;
  wiState.userListOperationPending = true;
  renderListLoading();
  return ++wiState.userListOperationGeneration;
}

export function endUserListOperation(generation) {
  if (generation !== wiState.userListOperationGeneration) return;
  wiState.userListOperationPending = false;
  renderListLoading();
  if (wiState.externalListReloadPending) {
    wiState.externalListReloadPending = false;
    void reloadFromExternalChange();
  }
}

export function isCurrentUserListOperation(generation) {
  return generation === wiState.userListOperationGeneration;
}

export function cancelSearchTimer() {
  if (wiState.searchTimer !== null) clearTimeout(wiState.searchTimer);
  wiState.searchTimer = null;
}

function beginListRequest(view, showLoading = true) {
  wiState.listRequestPending = true;
  wiState.listLoadingVisible = wiState.listLoadingVisible || showLoading;
  renderListLoading(view);
}

function endListRequest(view, generation) {
  if (generation !== wiState.listRequestGeneration) return;
  wiState.listLoadingVisible = false;
  wiState.listRequestPending = false;
  renderListLoading(view);
}

export function renderPagination(view = captureListLoadingView()) {
  const {previous, next, status} = view;
  if (!previous || !next || !status) return;
  const page = wiState.pagination.page || wiState.currentPage;
  const pageCount = wiState.pagination.page_count || 1;
  status.textContent = `ページ ${page} / ${pageCount}（全${wiState.pagination.total_count}件）`;
  previous.disabled = wiState.listRequestPending || wiState.userListOperationPending || page <= 1;
  next.disabled = wiState.listRequestPending || wiState.userListOperationPending || page >= pageCount;
  previous.setAttribute('aria-label', `前のページ（現在${page}ページ）`);
  next.setAttribute('aria-label', `次のページ（現在${page}ページ）`);
}

export async function movePage(offset) {
  const targetPage = Math.min(
    Math.max(1, wiState.currentPage + offset),
    wiState.pagination.page_count || 1
  );
  if (targetPage === wiState.currentPage) return;
  wiState.currentPage = targetPage;
  await (loadEntries({announce: true}));
}

export function applyPagination(payload) {
  const metadata = payload.pagination;
  if (metadata && Number.isInteger(metadata.page) && metadata.page > 0 &&
      Number.isInteger(metadata.page_count) && metadata.page_count > 0 &&
      Number.isInteger(metadata.total_count) && metadata.total_count >= 0) {
    wiState.pagination = {
      page: metadata.page,
      page_size: Number.isInteger(metadata.page_size) && metadata.page_size > 0
        ? metadata.page_size : ENTRY_PAGE_SIZE,
      page_count: metadata.page_count,
      total_count: metadata.total_count
    };
    wiState.currentPage = metadata.page;
    return;
  }
  const totalCount = Array.isArray(payload.entries) ? payload.entries.length : 0;
  wiState.pagination = {
    page: wiState.currentPage,
    page_size: ENTRY_PAGE_SIZE,
    page_count: Math.max(1, Math.ceil(totalCount / ENTRY_PAGE_SIZE)),
    total_count: totalCount
  };
}

let listScope;
let repoScope;

export async function loadEntries({announce = false, showLoading = true, timing = {}} = {}) {
  wiState.pendingListAnnouncement = wiState.pendingListAnnouncement || announce;
  const query = buildQuery(wiState.currentPage);
  if (listScope !== query.toString()) setUpdateError("wi", "list");
  listScope = query.toString();
  const generation = ++wiState.listRequestGeneration;
  const loadingView = captureListLoadingView();
  beginListRequest(loadingView, showLoading);
  const pending = (async () => {
    try {
      const payload = await api(`/api/entries?${query.toString()}`, {timing});
      if (generation !== wiState.listRequestGeneration) return wiState.entries;
      recordUpdateSuccess("wi", "list");
      wiState.entries = Array.isArray(payload.entries) ? payload.entries : [];
      applyPagination(payload);
      const selected = wiState.entries.find(item => entryKey(item) === entryKey(wiState.currentEntry));
      if (selected) wiState.currentEntry = {...wiState.currentEntry, ...selected};
      const shouldAnnounce = wiState.pendingListAnnouncement;
      wiState.pendingListAnnouncement = false;
      renderEntries(
        Array.isArray(payload.warnings) ? payload.warnings : [],
        shouldAnnounce
      );
      renderWaitingProgress();
      return wiState.entries;
    } catch (error) {
      if (generation === wiState.listRequestGeneration) {
        wiState.pendingListAnnouncement = false;
        if (readFailure(error.status) !== "temporary") setGlobalError(error.message);
        else setDisplayedReadError("wi", "list", "WI一覧を取得できません。", () => loadEntries({announce: true}));
      }
      return wiState.entries;
    }
  })();
  return pending.finally(() => endListRequest(loadingView, generation));
}

export function syncNotificationButton() {
  const button = byId('notification-button');
  button.hidden = typeof Notification === 'undefined' || Notification.permission !== 'default';
}

// 新しく登録されたユーザー対応の項目（未回答UWIと要確認AWI）だけを通知する。
// 既知集合はファイル名で持ち、初回取得では通知せずに基準だけを作る。
// 既知の項目の回答・確認状態・対象repoの変化と、削除後の同名の再登録は通知しない。
export async function refreshKnownEntries({notify = false, timing = {}} = {}) {
  const generation = ++wiState.knownEntryRequestGeneration;
  let payload;
  try {
    // 通知の対象は新規の処理待ち項目だけのため、未終了の項目に限って取得する。全状態の一覧は数MBに及び、
    // 変更通知のたびに取得すると参照の実行枠を占めて画面の操作を待たせる。
    payload = await (api('/api/entries?type=all&status=active&answered=all', {timing}));
  } catch (error) {
    if (generation !== wiState.knownEntryRequestGeneration) return;
    throw error;
  }
  if (generation !== wiState.knownEntryRequestGeneration) return;
  const allEntries = Array.isArray(payload.entries) ? payload.entries : [];
  const added = wiState.knownEntryBaselineReady && notify
    ? allEntries.filter(entry => !wiState.knownEntryFilenames.has(entry.filename) && PROCESSABLE_STATES.has(entry.state))
    : [];
  allEntries.forEach(entry => wiState.knownEntryFilenames.add(entry.filename));
  wiState.knownEntryBaselineReady = true;
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
  if (!added.length || await pushHandlesNotifications()) return;
  const notifications = [
    ['新規未回答UWI', added.filter(entry => entry.kind === 'uwi' && entry.answered === false)],
    ['新規要確認AWI', added.filter(entry => entry.kind === 'awi' && entry.needs_verify === true)],
  ];
  for (const [title, entries] of notifications) {
    if (!entries.length) continue;
    const filenames = entries.map(entry => entry.filename);
    new Notification(title, {
      body: filenames.length === 1 ? filenames[0] : `${filenames.length}件: ${filenames.join('、')}`
    });
  }
}

export async function enableNotifications() {
  if (typeof Notification === 'undefined') return;
  await (Notification.requestPermission());
  syncNotificationButton();
}

function replaceOptions(select, values, firstLabel) {
  const selected = select.value;
  if (selected && !values.includes(selected)) values = [...values, selected];
  select.replaceChildren();
  const first = document.createElement('option');
  first.value = '';
  first.textContent = firstLabel;
  select.append(first);
  values.forEach(value => {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = value;
    select.append(option);
  });
  select.value = values.includes(selected) ? selected : '';
}

// 系統を登録したrepositoryは、まだ項目が無くても新規登録の候補へ出す。
function renderRepoOptions() {
  const values = [...new Set([...wiState.repoCandidates, ...Object.keys(wiState.branchRegistry)])];
  byId('repo-options').replaceChildren(...values.map(value => {
    const option = document.createElement('option');
    option.value = value;
    return option;
  }));
}

// 系統を登録したrepositoryがある場合だけ、系統で限定する欄を出す。選択肢は登録した系統の和集合とする。
function renderBranchFilter() {
  const select = byId('target-branch-filter');
  const branches = [...new Set(Object.values(wiState.branchRegistry).flatMap(item => item.branches))];
  const registered = branches.length > 0;
  byId('target-branch-filter-label').hidden = !registered;
  select.hidden = !registered;
  replaceOptions(select, registered ? branches : [], 'すべて');
}

export async function loadBranchRegistry({timing = {}} = {}) {
  try {
    const payload = await api('/api/branches', {timing});
    setUpdateError("wi", "branches");
    wiState.branchRegistry = payload.repositories ?? {};
    renderBranchFilter();
    renderRepoOptions();
    updateCreateBranchChoices();
    return true;
  } catch (error) {
    setDisplayedReadError("wi", "branches", "ブランチ候補を取得できません。", loadBranchRegistry);
    return false;
  }
}

export async function loadTargetRepos({timing = {}} = {}) {
  const generation = ++wiState.targetRepoRequestGeneration;
  const requestedState = byId('state-filter').value;
  const scope = JSON.stringify([requestedState, wiState.displayMode]);
  if (repoScope !== scope) setUpdateError("wi", "repos");
  repoScope = scope;
  try {
    const status = encodeURIComponent(requestedState);
    const payload = await (api(`/api/repos?status=${status}`, {timing}));
    if (generation !== wiState.targetRepoRequestGeneration ||
        byId('state-filter').value !== requestedState) return false;
    const repos = Array.isArray(payload.repos) ? payload.repos : [];
    replaceOptions(byId('target-filter'), repos, 'すべて');
    // 判断待ちは検索条件のstateに依存せず、activeのrepo候補を別途取得する。
    if (wiState.displayMode === 'waiting') {
      const waiting = await api('/api/repos?status=active', {timing});
      if (generation !== wiState.targetRepoRequestGeneration || wiState.displayMode !== 'waiting') return false;
      replaceOptions(byId('waiting-target-filter'), waiting.repos, 'すべて');
    }
    setUpdateError("wi", "repos");
    wiState.repoCandidates = repos;
    renderRepoOptions();
    return true;
  } catch (error) {
    const isCurrent = generation === wiState.targetRepoRequestGeneration &&
      byId('state-filter').value === requestedState;
    if (isCurrent) setDisplayedReadError("wi", "repos", "リポジトリ候補を取得できません。", loadTargetRepos);
    return isCurrent;
  }
}

export async function clearFilters({timing = {}} = {}) {
  cancelSearchTimer();
  const operation = beginUserListOperation();
  byId('search-input').value = '';
  byId('kind-filter').value = 'all';
  byId('state-filter').value = 'all';
  byId('period-filter').value = 'all';
  byId('answer-filter').value = 'all';
  byId('important-filter').value = 'all';
  byId('target-filter').value = '';
  byId('target-branch-filter').value = '';
  byId('source-filter').value = '';
  setDisplayMode('normal');
  wiState.currentPage = 1;
  wiState.pagination.page = 1;
  try {
    await (loadTargetRepos({timing}));
    if (!isCurrentUserListOperation(operation)) return;
    await (loadEntries({announce: true, timing}));
  } finally {
    endUserListOperation(operation);
  }
}
