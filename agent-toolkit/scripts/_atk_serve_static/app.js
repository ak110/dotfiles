// 3画面は同じドキュメントへ順に読み込まれるため、トップレベルの宣言を即時実行関数で囲んで
// 画面ごとにスコープを閉じる。`window.__atkScreens`への登録だけを外部へ公開する。
// 内側の字下げは、囲む前後の差分を比較できるよう元のままとする。
(() => {
const BASE_PATH=__BASE_PATH_JS__;
// エラー表示は既存のError契約に合わせ、error.messageを直接参照する。
const KIND_LABELS = {awi: 'AWI', uwi: 'UWI', unknown: '種別不明'};
const STATE_LABELS = {
  inbox: '未処理', processing: '処理中', hold: '保留',
  adopted: '採用済み', rejected: '不採用'
};
const PROCESSABLE_STATES = new Set(['inbox', 'processing']);
const MUTABLE_STATES = new Set(['inbox', 'processing', 'hold']);
const DELETABLE_STATES = new Set(['inbox', 'processing', 'hold', 'adopted', 'rejected']);
const SEARCH_FALLBACK_MAX_RESULTS = 5;
const SEARCH_FALLBACK_NOTICE =
  '状態などの条件では一致しなかったため、検索欄の条件だけで見つかった項目を表示しています。' +
  'フィルターの選択値は変更していません。';
const ENTRY_PAGE_SIZE = 100;
const TARGET_REPO_DISPLAY_LENGTH = 20;
const METADATA_FIELDS = [
  ['kind', '種別'],
  ['state', '状態'],
  ['answered', '回答状況'],
  ['target_repo', '対象リポジトリ'],
  ['source', '投入元'],
  ['updated_at', '更新日時']
];
const FRONTMATTER_LABELS = {target_repo: '対象リポジトリ', source: '投入元'};
const FRONTMATTER_EXCLUDED_KEYS = new Set(['type']);

let entries = [];
let currentEntry = null;
let detailOrigin = null;
let detailOriginKey = '';
let detailRequestGeneration = 0;
let detailSessionGeneration = 0;
let listRequestGeneration = 0;
let targetRepoRequestGeneration = 0;
let pendingListRequests = 0;
let pendingListAnnouncement = false;
let detailRefreshRequired = false;
let deleteDialogEntrySnapshot = '';
let searchTimer = null;
let currentPage = 1;
let pagination = {page: 1, page_size: ENTRY_PAGE_SIZE, page_count: 1, total_count: 0};
let knownUwiBaselineReady = false;
const knownUwiFilenames = new Set();
const pendingOperations = new Set();
const dialogOrigins = new Map();
const dialogStack = [];
let refreshFocusRequested = false;

const byId = id => document.getElementById(id);
const entryKey = entry => entry ? `${entry.state}/${entry.filename}` : '';
const deleteEntrySnapshot = entry => entry ? JSON.stringify([
  entryKey(entry), entry.content, entry.target_repo || '', entry.summary || ''
]) : '';

async function api(path, options = {}) {
  const request = {
    ...options,
    headers: {'Content-Type': 'application/json', ...(options.headers || {})}
  };
  const response = await fetch(BASE_PATH + path, request);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error || response.statusText || '通信に失敗しました。');
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function setTextMessage(id, message) {
  const element = byId(id);
  element.textContent = message;
  element.hidden = !message;
}

function setGlobalError(message) {
  refreshFocusRequested = false;
  byId('global-error-message').textContent = message;
  byId('global-error').hidden = !message;
}

function focusRefreshButton() {
  const refreshButton = byId('refresh-button');
  if (refreshButton.disabled) {
    refreshFocusRequested = true;
    return;
  }
  refreshFocusRequested = false;
  refreshButton.focus();
}

function restoreRefreshFocus() {
  if (!refreshFocusRequested) return;
  const refreshButton = byId('refresh-button');
  if (refreshButton.disabled) return;
  refreshFocusRequested = false;
  refreshButton.focus();
}

function clearDialogMessages(dialogName) {
  setTextMessage(`${dialogName}-alert`, '');
  setTextMessage(`${dialogName}-status`, '');
}

function showToast(message, isError = false) {
  const notice = byId('operation-notice');
  byId('operation-notice-message').textContent = message;
  notice.dataset.error = String(isError);
  notice.setAttribute('role', isError ? 'alert' : 'status');
  notice.setAttribute('aria-live', isError ? 'assertive' : 'polite');
  notice.hidden = false;
}

function closeOperationNotice() {
  byId('operation-notice').hidden = true;
  focusRefreshButton();
}

function topmostDialog() {
  for (let index = dialogStack.length - 1; index >= 0; index -= 1) {
    const dialog = byId(dialogStack[index]);
    if (dialog && dialog.open) return dialog;
  }
  return null;
}

function deliverOperationMessage(message, isError = false) {
  const dialog = topmostDialog();
  if (!dialog) {
    showToast(message, isError);
    return;
  }
  // モーダルはtop layerへ描画され、ページ固定の通知は`::backdrop`の下へ入り、暗転して操作も受け付けない。
  // 開いているダイアログがあるときは、そのダイアログ内の結果表示領域へ配送する。
  const name = dialog.id.replace(/-dialog$/, '');
  setTextMessage(`${name}-${isError ? 'alert' : 'status'}`, message);
  setTextMessage(`${name}-${isError ? 'status' : 'alert'}`, '');
}

function openDialog(dialog, origin, focusTarget) {
  dialogOrigins.set(dialog.id, origin || document.activeElement);
  const previous = dialogStack.indexOf(dialog.id);
  if (previous >= 0) dialogStack.splice(previous, 1);
  dialogStack.push(dialog.id);
  dialog.showModal();
  if (focusTarget) focusTarget.focus();
}

function closeDialog(dialog, {restoreFocus = true} = {}) {
  const wasOpen = dialog.open;
  if (wasOpen) dialog.close();
  const index = dialogStack.lastIndexOf(dialog.id);
  if (index >= 0) dialogStack.splice(index, 1);
  const origin = dialogOrigins.get(dialog.id);
  dialogOrigins.delete(dialog.id);
  if (wasOpen && restoreFocus && origin && typeof origin.focus === 'function') origin.focus();
}

function setFieldError(input, errorElement, message) {
  input.setAttribute('aria-invalid', message ? 'true' : 'false');
  errorElement.textContent = message;
  errorElement.hidden = !message;
}

function firstInvalid(inputs) {
  const invalid = inputs.find(input => input.getAttribute('aria-invalid') === 'true');
  if (invalid) invalid.focus();
  return invalid;
}

async function runPending(key, {container, button, busyLabel}, operation) {
  if (pendingOperations.has(key)) return undefined;
  pendingOperations.add(key);
  const controls = Array.from(container.querySelectorAll('input, select, textarea, button'))
    .filter(control => !control.classList.contains('dialog-close'));
  const previous = controls.map(control => control.disabled);
  const originalLabel = button.textContent;
  controls.forEach(control => { control.disabled = true; });
  button.disabled = true;
  button.textContent = busyLabel;
  button.classList.add('is-pending');
  button.setAttribute('aria-busy', 'true');
  container.setAttribute('aria-busy', 'true');
  try {
    return await operation();
  } finally {
    controls.forEach((control, index) => { control.disabled = previous[index]; });
    button.textContent = originalLabel;
    button.classList.remove('is-pending');
    button.setAttribute('aria-busy', 'false');
    container.setAttribute('aria-busy', 'false');
    pendingOperations.delete(key);
    syncFilterDependencies();
    syncDetailMutationAvailability();
    restoreRefreshFocus();
  }
}

function formatDateParts(value) {
  if (!value) return {date: '—', time: ''};
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return {date: String(value), time: ''};
  const parts = new Intl.DateTimeFormat('ja-JP', {
    timeZone: 'Asia/Tokyo', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false
  }).formatToParts(date);
  const part = type => parts.find(item => item.type === type)?.value || '';
  return {date: `${part('year')}/${part('month')}/${part('day')}`, time: `${part('hour')}:${part('minute')}`};
}

function kindLabel(kind) { return KIND_LABELS[kind] || '種別不明'; }
function stateLabel(state) { return STATE_LABELS[state] || state || '不明'; }

function appendCell(row, label, className) {
  const cell = document.createElement('span');
  cell.className = `entry-cell ${className}`;
  cell.dataset.label = label;
  row.append(cell);
  return cell;
}

function appendTextCell(row, label, className, value) {
  const cell = appendCell(row, label, className);
  cell.textContent = value || '—';
  return cell;
}

function targetRepoDisplay(value) {
  if (!value || value.length <= TARGET_REPO_DISPLAY_LENGTH) return value || '—';
  const prefixLength = Math.floor((TARGET_REPO_DISPLAY_LENGTH - 1) / 2);
  const suffixLength = TARGET_REPO_DISPLAY_LENGTH - prefixLength - 1;
  return `${value.slice(0, prefixLength)}…${value.slice(-suffixLength)}`;
}

function renderEntry(entry) {
  const item = document.createElement('li');
  item.className = 'entry-row';
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'entry-select';
  button.dataset.key = entryKey(entry);
  button.dataset.kind = entry.kind || 'unknown';
  const unanswered = entry.kind === 'uwi' && entry.answered === false;
  button.dataset.unansweredUwi = String(unanswered);
  button.setAttribute('aria-current', String(entryKey(currentEntry) === entryKey(entry)));

  appendTextCell(button, 'ファイル名', 'filename-cell', entry.filename);
  const targetRepo = appendTextCell(button, '対象リポジトリ', 'target-repo-cell', targetRepoDisplay(entry.target_repo));
  if (entry.target_repo) {
    targetRepo.title = entry.target_repo;
    targetRepo.setAttribute('aria-label', `対象リポジトリ: ${entry.target_repo}`);
  }
  const status = appendCell(button, '種別・状態', 'status-cell');
  const kind = document.createElement('span');
  kind.className = 'entry-kind';
  kind.textContent = entry.kind || 'unknown';
  status.append(kind);
  const badge = document.createElement('span');
  badge.className = 'state-badge';
  badge.dataset.state = entry.state;
  badge.textContent = entry.state || 'unknown';
  status.append(badge);
  if (entry.plan) {
    const plan = document.createElement('span');
    plan.className = 'plan-badge';
    plan.textContent = 'plan';
    status.append(plan);
  }
  if (unanswered) {
    const attention = document.createElement('span');
    attention.className = 'attention-badge';
    attention.textContent = '未回答';
    status.append(attention);
  }
  appendTextCell(button, '要約', 'summary-cell', entry.summary);
  button.setAttribute(
    'aria-label',
    [entry.filename, entry.target_repo || '対象なし', entry.kind || 'unknown', entry.state || 'unknown',
      entry.plan ? 'plan' : '',
      unanswered ? '未回答' : '', entry.summary || '要約なし'].filter(Boolean).join('、')
  );
  button.addEventListener('click', () => selectEntry(entry, button));
  item.append(button);
  return item;
}

function hasNonStateFilters() {
  return byId('search-input').value !== '' ||
    byId('kind-filter').value !== 'all' ||
    byId('answer-filter').value !== 'all' ||
    byId('target-filter').value !== '' ||
    byId('source-filter').value !== '';
}

function renderEmptyState() {
  const empty = byId('empty-state');
  const message = byId('empty-state-message');
  const clear = byId('empty-clear-button');
  const allStates = byId('empty-all-states-button');
  const create = byId('empty-create-button');
  empty.hidden = entries.length !== 0;
  clear.hidden = true;
  allStates.hidden = true;
  create.hidden = true;
  if (entries.length) return;
  if (hasNonStateFilters() || !['active', 'all'].includes(byId('state-filter').value)) {
    message.textContent = '条件に一致する項目はありません。';
    clear.hidden = false;
  } else if (byId('state-filter').value === 'active') {
    message.textContent = '対応中の項目はありません。';
    allStates.hidden = false;
  } else {
    message.textContent = '項目はまだありません。';
    create.hidden = false;
  }
}

function renderWarnings(warnings) {
  const warning = byId('list-warning');
  if (!warnings.length) {
    warning.hidden = true;
    warning.textContent = '';
    return;
  }
  warning.textContent = `一覧から除外したファイル: ${warnings.map(item => `${item.filename}（${item.reason}）`).join('、')}`;
  warning.hidden = false;
}

function renderList(warnings = [], announce = false, searchFallback = false) {
  const list = byId('entry-list');
  list.replaceChildren(...entries.map(renderEntry));
  const unanswered = entries.filter(entry => entry.kind === 'uwi' && entry.answered === false).length;
  byId('entry-count').textContent = `${entries.length}件（未回答UWI ${unanswered}件）`;
  renderPagination();
  setTextMessage('list-fallback-notice', searchFallback ? SEARCH_FALLBACK_NOTICE : '');
  renderWarnings(warnings);
  renderEmptyState();
  if (announce) {
    byId('result-status').textContent = entries.length ? `${entries.length}件を表示` : '一致する項目はありません';
  }
}

function buildQuery(page = currentPage) {
  const parameters = new URLSearchParams();
  parameters.set('type', byId('kind-filter').value);
  parameters.set('status', byId('state-filter').value);
  parameters.set('answered', byId('answer-filter').value);
  const values = {
    target_repo: byId('target-filter').value,
    source_kind: byId('source-filter').value,
    q: byId('search-input').value.trim()
  };
  Object.entries(values).forEach(([name, value]) => { if (value) parameters.set(name, value); });
  parameters.set('page', String(page));
  return parameters;
}

function hasSearchFallbackFilters(query) {
  return query.get('type') !== 'all' || query.get('status') !== 'all' ||
    query.get('answered') !== 'all' || query.has('target_repo') || query.has('source_kind');
}

function setListLoading(value) {
  pendingListRequests += value ? 1 : -1;
  pendingListRequests = Math.max(0, pendingListRequests);
  const loading = pendingListRequests > 0;
  byId('loading-indicator').hidden = !loading;
  byId('entry-list').setAttribute('aria-busy', String(loading));
  renderPagination();
}

function renderPagination() {
  const previous = byId('previous-page-button');
  const next = byId('next-page-button');
  const status = byId('pagination-status');
  if (!previous || !next || !status) return;
  const page = pagination.page || currentPage;
  const pageCount = pagination.page_count || 1;
  status.textContent = `ページ ${page} / ${pageCount}（全${pagination.total_count}件）`;
  previous.disabled = pendingListRequests > 0 || page <= 1;
  next.disabled = pendingListRequests > 0 || page >= pageCount;
  previous.setAttribute('aria-label', `前のページ（現在${page}ページ）`);
  next.setAttribute('aria-label', `次のページ（現在${page}ページ）`);
}

async function movePage(offset) {
  const targetPage = Math.min(
    Math.max(1, currentPage + offset),
    pagination.page_count || 1
  );
  if (targetPage === currentPage) return;
  currentPage = targetPage;
  await loadEntries({announce: true});
}

function applyPagination(payload) {
  const metadata = payload.pagination;
  if (metadata && Number.isInteger(metadata.page) && metadata.page > 0 &&
      Number.isInteger(metadata.page_count) && metadata.page_count > 0 &&
      Number.isInteger(metadata.total_count) && metadata.total_count >= 0) {
    pagination = {
      page: metadata.page,
      page_size: Number.isInteger(metadata.page_size) && metadata.page_size > 0
        ? metadata.page_size : ENTRY_PAGE_SIZE,
      page_count: metadata.page_count,
      total_count: metadata.total_count
    };
    currentPage = metadata.page;
    return;
  }
  const totalCount = Array.isArray(payload.entries) ? payload.entries.length : 0;
  pagination = {
    page: currentPage,
    page_size: ENTRY_PAGE_SIZE,
    page_count: Math.max(1, Math.ceil(totalCount / ENTRY_PAGE_SIZE)),
    total_count: totalCount
  };
}

async function loadEntries({announce = false} = {}) {
  pendingListAnnouncement = pendingListAnnouncement || announce;
  const query = buildQuery(currentPage);
  const searchTerm = query.get('q') || '';
  const canSearchFallback = searchTerm !== '' && hasSearchFallbackFilters(query);
  const generation = ++listRequestGeneration;
  setListLoading(true);
  try {
    const payload = await api(`/api/entries?${query.toString()}`);
    if (generation !== listRequestGeneration) return entries;
    const initialEntries = Array.isArray(payload.entries) ? payload.entries : [];
    let selectedPayload = payload;
    let searchFallback = false;
    let fallbackError = null;
    if (canSearchFallback && initialEntries.length === 0) {
      try {
        const fallbackQuery = new URLSearchParams({q: searchTerm, page: String(currentPage)});
        const fallbackPayload = await api(`/api/entries?${fallbackQuery.toString()}`);
        if (generation !== listRequestGeneration) return entries;
        const fallbackEntries = Array.isArray(fallbackPayload.entries) ? fallbackPayload.entries : [];
        if (fallbackEntries.length > 0 && fallbackEntries.length <= SEARCH_FALLBACK_MAX_RESULTS) {
          selectedPayload = fallbackPayload;
          searchFallback = true;
        }
      } catch (error) {
        fallbackError = error;
      }
    }
    if (generation !== listRequestGeneration) return entries;
    entries = Array.isArray(selectedPayload.entries) ? selectedPayload.entries : [];
    applyPagination(selectedPayload);
    const selected = entries.find(item => entryKey(item) === entryKey(currentEntry));
    if (selected) currentEntry = {...currentEntry, ...selected};
    const shouldAnnounce = pendingListAnnouncement;
    pendingListAnnouncement = false;
    renderList(Array.isArray(selectedPayload.warnings) ? selectedPayload.warnings : [], shouldAnnounce, searchFallback);
    if (fallbackError) setGlobalError(fallbackError.message);
    return entries;
  } catch (error) {
    if (generation === listRequestGeneration) {
      pendingListAnnouncement = false;
      setGlobalError(error.message);
    }
    return entries;
  } finally {
    setListLoading(false);
  }
}

function syncNotificationButton() {
  const button = byId('notification-button');
  button.hidden = typeof Notification === 'undefined' || Notification.permission !== 'default';
}

async function refreshKnownUwis({notify = false} = {}) {
  const payload = await api('/api/entries?type=uwi&status=all&answered=all');
  const allUwis = Array.isArray(payload.entries) ? payload.entries : [];
  const newUnanswered = knownUwiBaselineReady && notify ? allUwis.filter(entry =>
    !knownUwiFilenames.has(entry.filename) && PROCESSABLE_STATES.has(entry.state) && entry.answered === false
  ) : [];
  allUwis.forEach(entry => knownUwiFilenames.add(entry.filename));
  knownUwiBaselineReady = true;
  if (newUnanswered.length && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
    const filenames = newUnanswered.map(entry => entry.filename);
    new Notification('新規未回答UWI', {
      body: filenames.length === 1 ? filenames[0] : `${filenames.length}件: ${filenames.join('、')}`
    });
  }
}

async function enableNotifications() {
  if (typeof Notification === 'undefined') return;
  await Notification.requestPermission();
  syncNotificationButton();
}

function replaceOptions(select, values, firstLabel) {
  const selected = select.value;
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

async function loadTargetRepos() {
  const generation = ++targetRepoRequestGeneration;
  const requestedState = byId('state-filter').value;
  try {
    const status = encodeURIComponent(requestedState);
    const payload = await api(`/api/repos?status=${status}`);
    if (generation !== targetRepoRequestGeneration || byId('state-filter').value !== requestedState) return false;
    const repos = Array.isArray(payload.repos) ? payload.repos : [];
    replaceOptions(byId('target-filter'), repos, 'すべて');
    const datalist = byId('repo-options');
    datalist.replaceChildren(...repos.map(value => {
      const option = document.createElement('option');
      option.value = value;
      return option;
    }));
    return true;
  } catch (error) {
    const isCurrent = generation === targetRepoRequestGeneration && byId('state-filter').value === requestedState;
    if (isCurrent) setGlobalError(error.message);
    return isCurrent;
  }
}

function syncFilterDependencies() {
  const awiOnly = byId('kind-filter').value === 'awi';
  if (awiOnly) byId('answer-filter').value = 'all';
  byId('answer-filter').disabled = awiOnly;
}

async function clearFilters({load = true} = {}) {
  byId('search-input').value = '';
  byId('kind-filter').value = 'all';
  byId('state-filter').value = 'active';
  byId('answer-filter').value = 'all';
  byId('target-filter').value = '';
  byId('source-filter').value = '';
  currentPage = 1;
  pagination.page = 1;
  syncFilterDependencies();
  if (load) {
    await loadTargetRepos();
    await loadEntries({announce: true});
  }
}

function updateCurrentRowSelection() {
  document.querySelectorAll('.entry-select').forEach(button => {
    button.setAttribute('aria-current', String(button.dataset.key === entryKey(currentEntry)));
  });
}

function entryButtonForKey(key) {
  return Array.from(document.querySelectorAll('.entry-select')).find(button => button.dataset.key === key) || null;
}

function detailReturnTarget() {
  const currentOrigin = entryButtonForKey(detailOriginKey);
  if (currentOrigin) return currentOrigin;
  const original = dialogOrigins.get('detail-dialog');
  if (original?.isConnected) return original;
  const firstRow = document.querySelectorAll('.entry-select')[0];
  if (firstRow) return firstRow;
  const emptyAction = [byId('empty-clear-button'), byId('empty-all-states-button'), byId('empty-create-button')]
    .find(button => !button.hidden);
  return emptyAction || byId('search-input');
}

function formatMetadataValue(value) {
  if (value !== null && typeof value === 'object') return JSON.stringify(value, null, 2);
  return value === null ? 'null' : String(value);
}

function formatMetadataKey(key) {
  if (key && typeof key === 'object' && !Array.isArray(key) &&
      typeof key.type === 'string' && Object.prototype.hasOwnProperty.call(key, 'value')) {
    if (key.type === 'str') return String(key.value);
    return `${key.type}: ${formatMetadataValue(key.value)}`;
  }
  return String(key);
}

function metadataEntries(entry) {
  const result = entry.frontmatter_entries.map(item => ({key: item.key, value: item.value}));
  for (const key of ['target_repo', 'source']) {
    if (!result.some(item => item.key?.type === 'str' && item.key.value === key) &&
        entry[key] !== undefined && entry[key] !== null) {
      result.push({key: {type: 'str', value: key}, value: entry[key]});
    }
  }
  return result;
}

function appendMetadataItem(metadata, label, value) {
  if (value === undefined) return;
  const item = document.createElement('div');
  item.className = 'metadata-item';
  const term = document.createElement('dt');
  term.textContent = label;
  const definition = document.createElement('dd');
  definition.textContent = formatMetadataValue(value);
  item.append(term, definition);
  metadata.append(item);
}

function renderMetadata(entry) {
  const metadata = byId('detail-metadata');
  metadata.replaceChildren();
  if (entry.answered === true || entry.answered === false) {
    appendMetadataItem(metadata, '回答状況', entry.answered ? '回答済み' : '未回答');
  }
  for (const item of metadataEntries(entry)) {
    const key = item.key;
    if (key?.type === 'str' && FRONTMATTER_EXCLUDED_KEYS.has(key.value)) continue;
    const keyValue = key?.type === 'str' ? key.value : undefined;
    const label = keyValue !== undefined && Object.prototype.hasOwnProperty.call(FRONTMATTER_LABELS, keyValue)
      ? FRONTMATTER_LABELS[keyValue] : formatMetadataKey(key);
    appendMetadataItem(metadata, label, item.value);
  }
  const parts = formatDateParts(entry.updated_at);
  appendMetadataItem(metadata, '更新日時', parts.time ? `${parts.date} ${parts.time}` : parts.date);
}

function setDetailMode(mode) {
  const editing = mode === 'edit';
  const answering = mode === 'answer';
  const commenting = mode === 'user-comment';
  const mutating = editing || answering || commenting;
  const unansweredUwi = currentEntry?.kind === 'uwi' && currentEntry.answered === false;
  const processable = currentEntry && PROCESSABLE_STATES.has(currentEntry.state);
  const mutable = currentEntry && MUTABLE_STATES.has(currentEntry.state);
  const deletable = currentEntry && DELETABLE_STATES.has(currentEntry.state);
  const held = currentEntry?.state === 'hold';
  const rejected = currentEntry?.state === 'rejected';
  byId('edit-panel').hidden = !editing;
  byId('answer-panel').hidden = !answering;
  byId('user-comment-panel').hidden = !commenting;
  byId('decision-panel').hidden = mutating || !mutable;
  byId('edit-button').hidden = mutating || !mutable;
  byId('answer-button').hidden = mutating || !currentEntry ||
    currentEntry.kind !== 'uwi' || !mutable;
  byId('user-comment-button').hidden = mutating || currentEntry?.user_comment_editable !== true;
  byId('answer-button').textContent = currentEntry?.answered === true ? '回答を変更' : '回答';
  byId('adopt-button').hidden = mutating || !mutable;
  byId('reject-button').hidden = mutating || !mutable || currentEntry.kind !== 'awi';
  byId('hold-button').hidden = mutating || !processable;
  byId('unhold-button').hidden = mutating || !held;
  byId('return-to-inbox-button').hidden = mutating || !rejected;
  byId('delete-button').hidden = mutating || !deletable;
  byId('save-entry-button').hidden = !editing;
  byId('save-answer-button').hidden = !answering;
  byId('save-user-comment-button').hidden = !commenting;
  syncDetailMutationAvailability();
  byId('edit-button').className = unansweredUwi ? 'button-secondary' : 'button-primary';
  if (!editing) setFieldError(byId('edit-content'), byId('edit-content-error'), '');
  if (!answering) setFieldError(byId('answer-input'), byId('answer-input-error'), '');
  if (!commenting) setFieldError(byId('user-comment-input'), byId('user-comment-input-error'), '');
}

function syncDetailMutationAvailability() {
  for (const id of [
    'edit-button', 'answer-button', 'user-comment-button', 'delete-button',
    'save-entry-button', 'save-answer-button', 'save-user-comment-button',
    'adopt-button', 'reject-button', 'hold-button', 'unhold-button', 'return-to-inbox-button'
  ]) {
    const button = byId(id);
    const userCommentUnavailable = id === 'save-user-comment-button' && currentEntry?.user_comment_editable !== true;
    button.disabled = !button.hidden && (detailRefreshRequired || userCommentUnavailable);
  }
}

function renderAnswerChoices(entry) {
  const container = byId('answer-choices');
  const choices = entry.question_type === 'yes-no' ? ['はい', 'いいえ'] :
    entry.question_type === 'choice' && Array.isArray(entry.choices) ? entry.choices : [];
  container.replaceChildren(...choices.map(value => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'button-secondary';
    button.textContent = value;
    button.addEventListener('click', () => {
      byId('answer-input').value = value;
      byId('answer-input').focus();
    });
    return button;
  }));
  container.hidden = choices.length === 0;
}

function displayEntry(entry) {
  currentEntry = entry;
  detailRefreshRequired = false;
  byId('decision-note').value = '';
  setTextMessage('detail-alert', '');
  byId('detail-view').hidden = false;
  byId('detail-filename').textContent = entry.filename;
  byId('detail-state').textContent = `${entry.kind || 'unknown'} / ${entry.state || 'unknown'}`;
  byId('detail-state').dataset.state = entry.state;
  byId('detail-content').innerHTML = entry.body_html ?? entry.content_html ?? '';
  renderMetadata(entry);
  renderAnswerChoices(entry);
  byId('readonly-notice').hidden = MUTABLE_STATES.has(entry.state) || entry.state === 'rejected';
  setDetailMode('view');
  updateCurrentRowSelection();
}

async function selectEntry(entry, origin = null) {
  const requestGeneration = ++detailRequestGeneration;
  const sessionGeneration = ++detailSessionGeneration;
  detailOrigin = origin || document.activeElement;
  detailOriginKey = entryKey(entry);
  clearDialogMessages('detail');
  try {
    const payload = await api(`/api/entries/${encodeURIComponent(entry.state)}/${encodeURIComponent(entry.filename)}`);
    if (requestGeneration !== detailRequestGeneration || sessionGeneration !== detailSessionGeneration) return;
    displayEntry(payload.entry);
    openDialog(byId('detail-dialog'), detailOrigin, byId('detail-dialog-body'));
  } catch (error) {
    if (requestGeneration === detailRequestGeneration) setGlobalError(error.message);
  }
}

function closeDeleteDialog({restoreFocus = true} = {}) {
  closeDialog(byId('delete-dialog'), {restoreFocus});
  deleteDialogEntrySnapshot = '';
}

function invalidateDeleteConfirmation() {
  if (!byId('delete-dialog').open) return false;
  closeDeleteDialog({restoreFocus: false});
  byId('detail-dialog-body').focus();
  return true;
}

const DELETE_RECONFIRM_MESSAGE =
  '外部更新により削除確認を閉じました。詳細を確認し、削除操作をやり直してください。';
const DELETE_CONFLICT_MESSAGE =
  '外部更新により削除確認を閉じました。詳細を閉じて開き直してから削除してください。';

function reportExternalDetailFailure(error, deleteConfirmationInvalidated) {
  const invalidated = invalidateDeleteConfirmation() || deleteConfirmationInvalidated;
  const recovery = invalidated ? ` ${DELETE_RECONFIRM_MESSAGE}` : '';
  setTextMessage('detail-alert', `${error.message}${recovery}`);
}

function closeDetailDialog() {
  const detailDialog = byId('detail-dialog');
  const deleteDialog = byId('delete-dialog');
  const hadOpenDialog = detailDialog.open || deleteDialog.open;
  if (!hadOpenDialog && !currentEntry) return;
  const returnTarget = detailReturnTarget();
  detailRequestGeneration += 1;
  detailSessionGeneration += 1;
  closeDeleteDialog({restoreFocus: false});
  closeDialog(detailDialog, {restoreFocus: false});
  currentEntry = null;
  detailOriginKey = '';
  detailRefreshRequired = false;
  setTextMessage('detail-alert', '');
  setDetailMode('view');
  updateCurrentRowSelection();
  if (hadOpenDialog && returnTarget && typeof returnTarget.focus === 'function') returnTarget.focus();
}

function currentDetailMode() {
  if (!byId('edit-panel').hidden) return 'edit';
  if (!byId('answer-panel').hidden) return 'answer';
  if (!byId('user-comment-panel').hidden) return 'user-comment';
  return 'view';
}

function refreshUserCommentMode(entry, message) {
  const input = byId('user-comment-input');
  const value = input.value;
  detailOriginKey = entryKey(entry);
  displayEntry(entry);
  input.value = value;
  setDetailMode('user-comment');
  setTextMessage('detail-alert', message);
  input.focus();
}

async function reloadOpenDetailFromExternalChange() {
  if (!byId('detail-dialog').open || !currentEntry) return;
  if (detailOriginKey !== entryKey(currentEntry)) return;
  const sessionGeneration = detailSessionGeneration;
  const originalState = currentEntry.state;
  const filename = currentEntry.filename;
  const requestGeneration = ++detailRequestGeneration;
  const requestIsCurrent = () => requestGeneration === detailRequestGeneration &&
    sessionGeneration === detailSessionGeneration && byId('detail-dialog').open &&
    currentEntry?.filename === filename;
  let deleteConfirmationInvalidated = false;
  if (byId('delete-dialog').open && deleteEntrySnapshot(currentEntry) !== deleteDialogEntrySnapshot) {
    deleteConfirmationInvalidated = invalidateDeleteConfirmation();
  }
  let resolvedEntry = null;
  try {
    const payload = await api(`/api/entries/${encodeURIComponent(originalState)}/${encodeURIComponent(filename)}`);
    if (!requestIsCurrent()) return;
    resolvedEntry = payload.entry;
  } catch (error) {
    if (!requestIsCurrent()) return;
    if (error.status !== 404) {
      reportExternalDetailFailure(error, deleteConfirmationInvalidated);
      return;
    }
    deleteConfirmationInvalidated = invalidateDeleteConfirmation() || deleteConfirmationInvalidated;
  }
  if (!resolvedEntry) {
    const candidates = [];
    for (const state of Object.keys(STATE_LABELS).filter(state => state !== originalState)) {
      try {
        const payload = await api(`/api/entries/${encodeURIComponent(state)}/${encodeURIComponent(filename)}`);
        if (!requestIsCurrent()) return;
        candidates.push(payload.entry);
      } catch (error) {
        if (!requestIsCurrent()) return;
        if (error.status !== 404) {
          reportExternalDetailFailure(error, deleteConfirmationInvalidated);
          return;
        }
      }
    }
    if (candidates.length > 1) {
      closeDetailDialog();
      setGlobalError(`${filename}の移動先を一意に特定できません。詳細を開き直してください。`);
      return;
    }
    resolvedEntry = candidates[0] || null;
  }
  if (!resolvedEntry) {
    closeDetailDialog();
    return;
  }
  const detailChanged = entryKey(resolvedEntry) !== entryKey(currentEntry) ||
    resolvedEntry.content !== currentEntry.content;
  if (byId('delete-dialog').open && deleteEntrySnapshot(resolvedEntry) !== deleteDialogEntrySnapshot) {
    deleteConfirmationInvalidated = invalidateDeleteConfirmation() || deleteConfirmationInvalidated;
  }
  const mode = currentDetailMode();
  if (mode === 'user-comment' && detailChanged) {
    const message = resolvedEntry.user_comment_editable === true
      ? '外部で項目が更新されました。入力を保持して最新内容を再取得しました。'
      : `${stateLabel(resolvedEntry.state)}へ移動したためユーザーコメントを保存できません。入力は保持しています。`;
    refreshUserCommentMode(resolvedEntry, message);
    updateCurrentRowSelection();
    return;
  }
  if (mode !== 'view') {
    currentEntry = {...currentEntry, state: resolvedEntry.state};
    detailOriginKey = entryKey(currentEntry);
    if (detailChanged) {
      detailRefreshRequired = true;
      setTextMessage(
        'detail-alert',
        '外部で項目が更新されました。入力を保持しています。詳細を閉じて開き直してから保存してください。'
      );
      setDetailMode(mode);
    }
    updateCurrentRowSelection();
    return;
  }
  detailOriginKey = entryKey(resolvedEntry);
  displayEntry(resolvedEntry);
  if (deleteConfirmationInvalidated) setTextMessage('detail-alert', DELETE_RECONFIRM_MESSAGE);
}

function enterEdit() {
  if (!currentEntry) return;
  byId('edit-content').value = currentEntry.content;
  setDetailMode('edit');
  byId('edit-content').focus();
}

function enterAnswer() {
  if (!currentEntry) return;
  byId('answer-input').value = currentEntry.answer || '';
  setDetailMode('answer');
  byId('answer-input').focus();
}

function enterUserComment() {
  if (!currentEntry || currentEntry.user_comment_editable !== true) return;
  byId('user-comment-input').value = currentEntry.user_comment || '';
  setDetailMode('user-comment');
  byId('user-comment-input').focus();
}

async function reloadUserCommentAfterConflict(key, sessionGeneration) {
  const state = currentEntry?.state;
  const filename = currentEntry?.filename;
  if (!state || !filename) return false;
  try {
    const refreshed = await api(`/api/entries/${encodeURIComponent(state)}/${encodeURIComponent(filename)}`);
    if (!byId('detail-dialog').open || entryKey(currentEntry) !== key ||
        sessionGeneration !== detailSessionGeneration || refreshed.entry.user_comment_editable !== true) return false;
    refreshUserCommentMode(
      refreshed.entry,
      `${key}は外部で更新されました。入力を保持して最新内容を再取得しました。内容を確認して再度保存してください。`
    );
    return true;
  } catch (_error) {
    return false;
  }
}

function mutationFailureMessage(key, failure, error) {
  const recoveryRequired = error.payload?.code === 'edit_conflict' || detailRefreshRequired;
  if (!recoveryRequired) return failure;
  detailRefreshRequired = true;
  syncDetailMutationAvailability();
  const recovery = `${key}は外部で更新されました。入力を保持しています。` +
    '詳細を閉じて開き直してから保存してください。';
  return `${failure} ${recovery}`;
}

async function saveEntry() {
  if (!currentEntry || detailRefreshRequired) return;
  const content = byId('edit-content').value;
  setFieldError(byId('edit-content'), byId('edit-content-error'), content.trim() ? '' : 'ファイル全体を入力してください。');
  if (firstInvalid([byId('edit-content')])) return;
  const key = entryKey(currentEntry);
  const sessionGeneration = detailSessionGeneration;
  const payload = {content, expected_content: currentEntry.content};
  clearDialogMessages('detail');
  try {
    await runPending('save', {
      container: byId('detail-shell'), button: byId('save-entry-button'), busyLabel: '保存中'
    }, () => api(`/api/entries/${encodeURIComponent(currentEntry.state)}/${encodeURIComponent(currentEntry.filename)}`, {
      method: 'PUT', body: JSON.stringify(payload)
    }));
    await loadEntries();
    // 本文編集の保存確定後は詳細を閉じて一覧へ戻す。保存中に別項目へ切り替えた場合は閉じない。
    if (byId('detail-dialog').open && entryKey(currentEntry) === key &&
        sessionGeneration === detailSessionGeneration) {
      closeDetailDialog();
    }
    deliverOperationMessage(`${key}を保存しました。`);
  } catch (error) {
    const failure = `${key}を保存できませんでした。 ${error.message}`;
    deliverOperationMessage(mutationFailureMessage(key, failure, error), true);
    if (byId('detail-dialog').open && entryKey(currentEntry) === key) byId('edit-content').focus();
  }
}

async function saveAnswer() {
  if (!currentEntry || detailRefreshRequired) return;
  const answer = byId('answer-input').value;
  setFieldError(byId('answer-input'), byId('answer-input-error'), answer.trim() ? '' : '回答を入力してください。');
  if (firstInvalid([byId('answer-input')])) return;
  const key = entryKey(currentEntry);
  const sessionGeneration = detailSessionGeneration;
  const payload = {
    filename: currentEntry.filename,
    state: currentEntry.state,
    answer,
    expected_content: currentEntry.content
  };
  clearDialogMessages('detail');
  try {
    await runPending('answer', {
      container: byId('detail-shell'), button: byId('save-answer-button'), busyLabel: '保存中'
    }, () => api('/api/entries/answer', {method: 'POST', body: JSON.stringify(payload)}));
    await loadEntries();
    // 回答の確定は次の項目へ移る操作単位のため、保存後は詳細を閉じて一覧へ戻す。
    // 本文編集の保存は同じ対象を続けて編集する操作単位のため閉じない（saveEntry参照）。
    // 保存中に別項目へ切り替わった場合は、切り替え先の詳細を閉じない。
    if (byId('detail-dialog').open && entryKey(currentEntry) === key &&
        sessionGeneration === detailSessionGeneration) {
      closeDetailDialog();
    }
    // 詳細を閉じた後に配送し、成功メッセージを一覧側の共通通知へ表示する。
    deliverOperationMessage(`${key}へ回答しました。`);
  } catch (error) {
    const failure = `${key}へ回答できませんでした。 ${error.message}`;
    deliverOperationMessage(mutationFailureMessage(key, failure, error), true);
    if (byId('detail-dialog').open && entryKey(currentEntry) === key) byId('answer-input').focus();
  }
}

async function saveUserComment() {
  if (!currentEntry || detailRefreshRequired || currentEntry.user_comment_editable !== true) return;
  const input = byId('user-comment-input');
  const comment = input.value;
  setFieldError(input, byId('user-comment-input-error'), comment.trim() ? '' : 'コメントを入力してください。');
  if (firstInvalid([input])) return;
  const key = entryKey(currentEntry);
  const sessionGeneration = detailSessionGeneration;
  const payload = {
    state: currentEntry.state,
    filename: currentEntry.filename,
    comment,
    expected_content: currentEntry.content
  };
  clearDialogMessages('detail');
  try {
    await runPending('user-comment', {
      container: byId('detail-shell'), button: byId('save-user-comment-button'), busyLabel: '保存中'
    }, () => api('/api/entries/user-comment', {method: 'POST', body: JSON.stringify(payload)}));
    await loadEntries();
    if (byId('detail-dialog').open && entryKey(currentEntry) === key &&
        sessionGeneration === detailSessionGeneration) {
      closeDetailDialog();
    }
    deliverOperationMessage(`${key}のユーザーコメントを保存しました。`);
  } catch (error) {
    if (error.payload?.code === 'edit_conflict' && await reloadUserCommentAfterConflict(key, sessionGeneration)) return;
    deliverOperationMessage(`${key}のユーザーコメントを保存できませんでした。 ${error.message}`, true);
    if (byId('detail-dialog').open && entryKey(currentEntry) === key) input.focus();
  }
}

async function transitionDetail(action) {
  if (!currentEntry || detailRefreshRequired) return;
  const allowed = action === 'unhold' ? currentEntry.state === 'hold' :
    action === 'return-to-inbox' ? currentEntry.state === 'rejected' : MUTABLE_STATES.has(currentEntry.state);
  if (!allowed || (action === 'reject' && currentEntry.kind !== 'awi')) return;
  const key = entryKey(currentEntry);
  const payload = {filenames: [currentEntry.filename]};
  if (action === 'return-to-inbox') payload.state = 'rejected';
  if ((action === 'adopt' || action === 'reject') && currentEntry.state === 'hold') payload.state = 'hold';
  const note = byId('decision-note').value.trim();
  if (note && (action === 'adopt' || action === 'reject')) payload.note = note;
  try {
    await runPending(`transition-${action}`, {
      container: byId('detail-shell'), button: byId(`${action}-button`), busyLabel: '処理中'
    }, () => api(`/api/entries/${action}`, {method: 'POST', body: JSON.stringify(payload)}));
    await loadEntries();
    if (byId('detail-dialog').open && entryKey(currentEntry) === key) closeDetailDialog();
    const label = {
      adopt: '採用', reject: '却下', hold: '保留', unhold: '保留解除', 'return-to-inbox': 'inboxへ戻す'
    }[action];
    deliverOperationMessage(`${key}を${label}しました。`);
  } catch (error) {
    deliverOperationMessage(`${key}を処理できませんでした。 ${error.message}`, true);
  }
}

function resetCreateForm() {
  byId('create-kind').value = 'awi';
  byId('create-content').value = '';
  byId('create-target').value = '';
  byId('create-source').value = '';
  byId('create-scope').value = '';
  byId('create-question-type').value = 'free-form';
  byId('create-choices').value = '';
  setFieldError(byId('create-content'), byId('create-content-error'), '');
  setFieldError(byId('create-target'), byId('create-target-error'), '');
  setFieldError(byId('create-choices'), byId('create-choices-error'), '');
  updateCreateFields();
}

function updateCreateFields() {
  const kind = byId('create-kind').value;
  const isBatch = kind === 'batch';
  const isUwi = kind === 'uwi';
  const isChoice = isUwi && byId('create-question-type').value === 'choice';
  byId('uwi-fields').hidden = !isUwi;
  byId('choice-fields').hidden = !isChoice;
  // 一括登録は各エントリのfrontmatterの値だけを用いるため、対象リポジトリ欄と投入元欄を隠す。
  byId('create-repo-fields').hidden = isBatch;
  byId('create-content-label').textContent = isBatch ? 'show形式テキスト（必須）' : '本文（必須）';
}

function openCreateDialog(origin = null) {
  resetCreateForm();
  clearDialogMessages('create');
  openDialog(byId('create-dialog'), origin || document.activeElement, byId('create-content'));
}

function createResultMessage(isBatch, result) {
  const filenames = Array.isArray(result.filenames) ? result.filenames : [];
  const warnings = Array.isArray(result.warnings) ? result.warnings : [];
  const renamed = Object.entries(result.mapping || {}).filter(([original, saved]) => original !== saved);
  const summary = isBatch
    ? `${filenames.length}件を取り込みました。` +
      (renamed.length ? `改名: ${renamed.map(([original, saved]) => `${original} -> ${saved}`).join('、')}` : '')
    : (filenames[0] ? `${filenames[0]}を追加しました。` : '項目を追加しました。');
  return warnings.length ? `${summary} 警告: ${warnings.join('、')}` : summary;
}

async function createEntry(event) {
  event.preventDefault();
  const type = byId('create-kind').value;
  const isBatch = type === 'batch';
  // 一括登録は原文保持のため、送信値へtrimを適用せず入力の生テキストをそのまま送る。
  const rawContent = byId('create-content').value;
  const message = rawContent.trim();
  const targetRepo = byId('create-target').value.trim();
  const choiceValues = byId('create-choices').value.split(/\r?\n/).map(item => item.trim()).filter(Boolean);
  setFieldError(
    byId('create-content'), byId('create-content-error'),
    message ? '' : (isBatch ? 'show形式テキストを入力してください。' : '本文を入力してください。')
  );
  const choiceInvalid = type === 'uwi' && byId('create-question-type').value === 'choice' && choiceValues.length < 2;
  setFieldError(byId('create-choices'), byId('create-choices-error'), choiceInvalid ? '選択肢を2件以上入力してください。' : '');
  if (firstInvalid([byId('create-content'), byId('create-choices')])) return;
  const payload = isBatch ? {text: rawContent} : {type, messages: [message]};
  if (!isBatch) {
    if (targetRepo) payload.target_repo = targetRepo;
    const source = byId('create-source').value.trim();
    if (source) payload.source = source;
    if (type === 'uwi') {
      const scope = byId('create-scope').value.trim();
      if (scope) payload.scope = scope;
      payload.question_type = byId('create-question-type').value;
      if (payload.question_type === 'choice') payload.choices = choiceValues;
    }
  }
  clearDialogMessages('create');
  try {
    const result = await runPending('create', {
      container: byId('create-form'), button: byId('create-submit-button'), busyLabel: '追加中'
    }, () => api(isBatch ? '/api/entries/batch' : '/api/entries', {method: 'POST', body: JSON.stringify(payload)}));
    closeDialog(byId('create-dialog'));
    await clearFilters({load: false});
    await loadTargetRepos();
    await loadEntries({announce: true});
    deliverOperationMessage(createResultMessage(isBatch, result));
  } catch (error) {
    deliverOperationMessage(`項目を追加できませんでした。 ${error.message}`, true);
    if (byId('create-dialog').open) byId('create-content').focus();
  }
}

function openDeleteDialog() {
  if (!currentEntry) return;
  deleteDialogEntrySnapshot = deleteEntrySnapshot(currentEntry);
  clearDialogMessages('delete');
  byId('delete-target').textContent = currentEntry.filename;
  byId('delete-state').textContent = `${currentEntry.kind || 'unknown'} / ${currentEntry.state || 'unknown'}`;
  byId('delete-state').dataset.state = currentEntry.state;
  byId('delete-target-repo').textContent = currentEntry.target_repo || '—';
  byId('delete-summary').textContent = currentEntry.summary || '—';
  byId('force-delete-row').hidden = currentEntry.state !== 'processing';
  byId('force-delete-confirmation').checked = false;
  setFieldError(byId('force-delete-confirmation'), byId('delete-error'), '');
  openDialog(byId('delete-dialog'), byId('delete-button'), byId('delete-close-button'));
}

async function deleteEntry(event) {
  event.preventDefault();
  if (!currentEntry) return;
  const force = byId('force-delete-confirmation').checked;
  if (currentEntry.state === 'processing' && !force) {
    setFieldError(byId('force-delete-confirmation'), byId('delete-error'), '処理中の項目を削除するには確認が必要です。');
    byId('force-delete-confirmation').focus();
    return;
  }
  const key = entryKey(currentEntry);
  const payload = {
    filenames: [currentEntry.filename],
    state: currentEntry.state,
    expected_content: currentEntry.content,
    force
  };
  clearDialogMessages('delete');
  try {
    await runPending('delete', {
      container: byId('delete-form'), button: byId('delete-submit-button'), busyLabel: '削除中'
    }, () => api('/api/entries/remove', {method: 'POST', body: JSON.stringify(payload)}));
    if (byId('delete-dialog').open) closeDeleteDialog();
    await loadEntries();
    if (!entries.some(entry => entryKey(entry) === key)) {
      if (byId('detail-dialog').open || currentEntry) closeDetailDialog();
      else detailReturnTarget().focus();
    } else {
      byId('edit-button').hidden = true;
      byId('answer-button').hidden = true;
      byId('delete-button').hidden = true;
    }
    deliverOperationMessage(`${key}を削除しました。`);
  } catch (error) {
    const failure = `${key}を削除できませんでした。 ${error.message}`;
    if (error.payload?.code === 'edit_conflict' && byId('detail-dialog').open) {
      invalidateDeleteConfirmation();
      detailRefreshRequired = true;
      syncDetailMutationAvailability();
      setTextMessage('detail-alert', `${failure} ${DELETE_CONFLICT_MESSAGE}`);
      byId('detail-dialog-body').focus();
    } else {
      deliverOperationMessage(failure, true);
      if (byId('delete-dialog').open) byId('delete-close-button').focus();
    }
  }
}

async function synchronizeAndLoad() {
  const payload = {};
  setTextMessage('sync-result', '');
  try {
    await runPending('sync', {
      container: document.querySelector('.app-header'), button: byId('refresh-button'), busyLabel: '同期中'
    }, () => api('/api/sync', {method: 'POST', body: JSON.stringify(payload)}));
    setTextMessage('sync-result', 'Git同期が完了しました。');
  } catch (error) {
    setTextMessage('sync-result', `Git同期に失敗しました。ローカル内容を表示中です。 ${error.message}`);
  }
  await loadTargetRepos();
  await loadEntries({announce: true});
}

async function handleFilterChange({reloadRepos = false} = {}) {
  currentPage = 1;
  pagination.page = 1;
  syncFilterDependencies();
  const requestedState = byId('state-filter').value;
  if (reloadRepos && !await loadTargetRepos() && byId('state-filter').value !== requestedState) return;
  await loadEntries({announce: true});
}

async function reloadFromExternalChange() {
  void refreshKnownUwis({notify: true}).catch((error) => {
    setGlobalError(error.message);
  });
  await loadTargetRepos();
  await loadEntries({announce: false});
  await reloadOpenDetailFromExternalChange();
}

function attachDialogCloseHandlers(dialogId, closeButtonId, closeHandler = null) {
  const dialog = byId(dialogId);
  const close = closeHandler || (() => closeDialog(dialog));
  byId(closeButtonId).addEventListener('click', close);
  dialog.addEventListener('cancel', event => {
    event.preventDefault();
    close();
  });
}

function handleFocusIn() {
  refreshFocusRequested = false;
}

function bindEvents() {
  document.addEventListener('focusin', handleFocusIn);
  byId('global-error-close-button').addEventListener('click', () => {
    setGlobalError('');
    focusRefreshButton();
  });
  byId('operation-notice-close-button').addEventListener('click', closeOperationNotice);
  byId('previous-page-button').addEventListener('click', () => { void movePage(-1); });
  byId('next-page-button').addEventListener('click', () => { void movePage(1); });
  byId('refresh-button').addEventListener('click', synchronizeAndLoad);
  byId('notification-button').addEventListener('click', () => { void enableNotifications(); });
  byId('create-button').addEventListener('click', event => openCreateDialog(event.currentTarget));
  byId('empty-create-button').addEventListener('click', event => openCreateDialog(event.currentTarget));
  byId('clear-filters-button').addEventListener('click', () => clearFilters());
  byId('empty-clear-button').addEventListener('click', () => clearFilters());
  byId('empty-all-states-button').addEventListener('click', () => {
    byId('state-filter').value = 'all';
    handleFilterChange({reloadRepos: true});
  });
  byId('kind-filter').addEventListener('change', () => { void handleFilterChange(); });
  byId('state-filter').addEventListener('change', () => { void handleFilterChange({reloadRepos: true}); });
  byId('answer-filter').addEventListener('change', () => { void handleFilterChange(); });
  byId('target-filter').addEventListener('change', () => { void handleFilterChange(); });
  byId('source-filter').addEventListener('change', () => { void handleFilterChange(); });
  byId('search-input').addEventListener('input', () => {
    if (searchTimer !== null) clearTimeout(searchTimer);
    currentPage = 1;
    pagination.page = 1;
    searchTimer = setTimeout(() => loadEntries({announce: true}), 250);
  });
  byId('edit-button').addEventListener('click', enterEdit);
  byId('answer-button').addEventListener('click', enterAnswer);
  byId('user-comment-button').addEventListener('click', enterUserComment);
  byId('save-entry-button').addEventListener('click', saveEntry);
  byId('save-answer-button').addEventListener('click', saveAnswer);
  byId('save-user-comment-button').addEventListener('click', saveUserComment);
  byId('adopt-button').addEventListener('click', () => { void transitionDetail('adopt'); });
  byId('reject-button').addEventListener('click', () => { void transitionDetail('reject'); });
  byId('hold-button').addEventListener('click', () => { void transitionDetail('hold'); });
  byId('unhold-button').addEventListener('click', () => { void transitionDetail('unhold'); });
  byId('return-to-inbox-button').addEventListener('click', () => { void transitionDetail('return-to-inbox'); });
  byId('delete-button').addEventListener('click', openDeleteDialog);
  byId('create-kind').addEventListener('change', updateCreateFields);
  byId('create-question-type').addEventListener('change', updateCreateFields);
  byId('create-form').addEventListener('submit', createEntry);
  byId('delete-form').addEventListener('submit', deleteEntry);
  attachDialogCloseHandlers('detail-dialog', 'detail-close-button', closeDetailDialog);
  attachDialogCloseHandlers('create-dialog', 'create-close-button', () => closeDialog(byId('create-dialog')));
  attachDialogCloseHandlers('delete-dialog', 'delete-close-button', closeDeleteDialog);
}

let initialization = Promise.resolve();
// SSE購読。`unmount`で閉じるため保持する。
let eventSource = null;

function initializeApp() {
  eventSource = new EventSource(BASE_PATH + '/api/events');
  eventSource.addEventListener('open', () => { byId('connection-status').textContent = '自動更新に接続済み'; });
  eventSource.addEventListener('error', () => { byId('connection-status').textContent = '自動更新を再接続中'; });
  eventSource.addEventListener('changed', () => { void initialization.then(reloadFromExternalChange); });
  syncFilterDependencies();
  syncNotificationButton();
  initialization = synchronizeAndLoad()
    .then(() => refreshKnownUwis({notify: false}))
    .catch((error) => {
      setGlobalError(error.message);
    });
}

function mount() {
  bindEvents();
  initializeApp();
}

function unmount() {
  document.removeEventListener('focusin', handleFocusIn);
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  if (searchTimer !== null) {
    clearTimeout(searchTimer);
    searchTimer = null;
  }
  initialization = Promise.resolve();
  entries = [];
  currentEntry = null;
  detailOrigin = null;
  detailOriginKey = '';
  currentPage = 1;
  pagination = {page: 1, page_size: ENTRY_PAGE_SIZE, page_count: 1, total_count: 0};
  knownUwiBaselineReady = false;
  knownUwiFilenames.clear();
  pendingOperations.clear();
  dialogOrigins.clear();
  dialogStack.length = 0;
  refreshFocusRequested = false;
}

window.__atkScreens = window.__atkScreens || {};
window.__atkScreens.wi = {mount, unmount};
})();
