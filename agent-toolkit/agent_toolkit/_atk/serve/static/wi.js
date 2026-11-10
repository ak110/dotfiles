// ワークアイテム画面。キューの一覧・詳細・登録・編集・状態遷移を扱う。
import {
  BASE_PATH, SERVE_HOST, WI_ROOT, diagnostic, connectEvents, renderList, renderWarnings, resyncWhenVisible,
  timedFetch, beginOperation, notifyDelayMs
} from "./common.js";
import {registerScreen} from "./shell.js";

// エラー表示は既存のError契約に合わせ、error.messageを直接参照する。
const KIND_LABELS = {awi: 'awi', uwi: 'uwi', unknown: 'unknown'};
// 確認状態の値ごとの表示名。キーを持たない項目は表示しない。
const STATE_LABELS = {
  inbox: 'inbox', processing: 'processing', hold: 'hold',
  adopted: 'adopted', rejected: 'rejected'
};
const PROCESSABLE_STATES = new Set(['inbox', 'processing']);
const MUTABLE_STATES = new Set(['inbox', 'processing', 'hold']);
const EDITABLE_STATES = new Set(['inbox', 'processing', 'hold', 'adopted', 'rejected']);
const TERMINAL_STATES = new Set(['adopted', 'rejected']);
const DELETABLE_STATES = new Set(['inbox', 'processing', 'hold', 'adopted', 'rejected']);
const ENTRY_PAGE_SIZE = 100;
const TARGET_REPO_DISPLAY_LENGTH = 20;
const FRONTMATTER_EXCLUDED_KEYS = new Set(['type', 'origin_session', 'origin_locator']);

let entries = [];
let currentEntry = null;
let detailOrigin = null;
let detailOriginKey = '';
let detailRequestGeneration = 0;
let detailSessionGeneration = 0;
let listRequestGeneration = 0;
let targetRepoRequestGeneration = 0;
let knownEntryRequestGeneration = 0;
let listLoadingVisible = false;
let listRequestPending = false;
let detailRequestPending = false;
let userListOperationGeneration = 0;
let userListOperationPending = false;
let externalListReloadPending = false;
let pendingListAnnouncement = false;
let detailRefreshRequired = false;
// 確認状態の同期が完了しなかった案内。失敗した操作自身のローカル書込みが外部変更として通知され、
// 詳細の自動再取得が警告欄を消すため、同じ詳細を開いている間だけ再取得後に同じ案内を表示し直す。
// 詳細を閉じるか別の項目を開くと`detailSessionGeneration`が変わり、案内は表示されなくなる。
let syncIncompleteNotice = null;
// 詳細ダイアログから送信した変更操作の応答を待つ間か、その間に詳細の再読込を保留したか。
let detailMutationPending = false;
let detailReloadDeferred = false;
let deleteDialogEntrySnapshot = '';
let searchTimer = null;
let searchComposing = false;
let displayMode = 'normal';
let waitingAdvancePending = false;
let waitingSavedFilename = '';
let cooldownTimer = null;
const entryStateDisplays = new Map();
let currentPage = 1;
let pagination = {page: 1, page_size: ENTRY_PAGE_SIZE, page_count: 1, total_count: 0};
let knownEntryBaselineReady = false;
const knownEntryFilenames = new Set();
const pendingOperations = new Set();
const dialogOrigins = new Map();
const dialogStack = [];
let refreshFocusRequested = false;
let noticeTimer = null;
let noticeOrigin = null;
let lastFocusedElement = null;

const byId = id => document.getElementById(id);
const entryKey = entry => entry ? entry.filename : '';
const isPlainPrimaryClick = event =>
  event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
const deleteEntrySnapshot = entry => entry ? JSON.stringify([
  entryKey(entry), entry.state, entry.content, entry.target_repo || '', entry.summary || ''
]) : '';

// 理由の後へ次の操作を続ける。APIの応答と一覧の警告は理由と次の操作を別項目で持つため、表示の直前に合わせる。
function withNextAction(reason, nextAction) {
  if (typeof nextAction !== 'string' || !nextAction.trim()) return reason;
  return `${reason.replace(/[。.]$/, '')}。次の操作: ${nextAction}`;
}

async function api(path, {timing = {}, ...options} = {}) {
  const route = path.split('?')[0];
  const entryMatch = route.match(/^\/api\/entries\/([^/]+\.md)$/);
  const operation = {
    '/api/entries': options.method ? '項目登録' : '一覧取得',
    '/api/repos': '対象リポジトリ一覧取得',
    '/api/sync': 'Git同期',
    '/api/entries/batch': '一括登録',
    '/api/entries/answer': '回答保存',
    '/api/entries/user-comment': 'ユーザーコメント保存',
    '/api/entries/important': '重要設定・解除',
    '/api/entries/remove': '削除',
    '/api/entries/adopt': '採用',
    '/api/entries/reject': '却下',
    '/api/entries/hold': '保留',
    '/api/entries/unhold': '保留解除',
    '/api/entries/return-to-inbox': 'inboxへ戻す'
  }[route] || '詳細取得・保存';
  const target = entryMatch ? entryMatch.slice(1).map(decodeURIComponent).join('/') : operation;
  const request = {
    ...options,
    headers: {'Content-Type': 'application/json', ...(options.headers || {})}
  };
  try {
    const response = await timedFetch('wi', BASE_PATH + path, request, timing);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      // 次の操作をmessageへ含め、同期・一括登録など各操作の失敗通知がそのまま表示できるようにする。
      const error = new Error(
        withNextAction(payload.error || response.statusText || '通信に失敗しました。', payload.next_action)
      );
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  } catch (error) {
    error.message = diagnostic('WI', SERVE_HOST, `${WI_ROOT} / ${target}`, error.message);
    throw error;
  }
}

function setTextMessage(id, message) {
  const element = byId(id);
  element.textContent = message;
  element.hidden = !message;
}

function setGlobalError(message) {
  refreshFocusRequested = false;
  if (message) showToast(message, true);
  else closeOperationNotice();
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
  // 詳細で次の操作を始めるときは、前の操作の同期未完了の案内を再表示の対象から外す。
  if (dialogName === 'detail') syncIncompleteNotice = null;
  setTextMessage(`${dialogName}-alert`, '');
  setTextMessage(`${dialogName}-status`, '');
}

function showToast(message, isError = false) {
  if (isError && !message.includes('［WI / ')) {
    message = diagnostic('WI', SERVE_HOST, `${WI_ROOT} / 操作`, message);
  }
  const notice = byId('operation-notice');
  clearTimeout(noticeTimer);
  noticeOrigin = isError && pendingOperations.has('sync') ? byId('refresh-button') :
    (document.activeElement === document.body ? lastFocusedElement : document.activeElement);
  byId('operation-notice-message').textContent = message;
  notice.dataset.error = String(isError);
  notice.setAttribute('role', isError ? 'alert' : 'status');
  notice.setAttribute('aria-live', isError ? 'assertive' : 'polite');
  notice.hidden = false;
  if (!isError) noticeTimer = setTimeout(() => { notice.hidden = true; }, 6000);
}

function closeOperationNotice() {
  clearTimeout(noticeTimer);
  byId('operation-notice').hidden = true;
  const returnTarget = noticeOrigin?.isConnected && !noticeOrigin.disabled ? noticeOrigin : byId('refresh-button');
  if (returnTarget === byId('refresh-button')) focusRefreshButton();
  else returnTarget.focus();
  noticeOrigin = null;
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
  // モーダルはtop layerへ描画されるため、ページに固定した通知は`::backdrop`の下に隠れ、暗く表示されて操作も受け付けない。
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

function openDetailDialog(origin) {
  const body = byId('detail-dialog-body');
  openDialog(byId('detail-dialog'), origin, body);
  body.scrollTop = 0;
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
  if (invalid) {
    invalid.focus();
    const error = (invalid.getAttribute('aria-describedby') || '').split(/\s+/)
      .map(byId).find(element => element && !element.hidden);
    // 入力欄へfocusするだけでは直後のエラー表示が本文領域の下に隠れるため、対応するエラーを表示領域の中央までスクロールする。
    if (error) error.scrollIntoView({block: 'center', behavior: 'instant'});
  }
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
  const restorePendingState = () => {
    controls.forEach((control, index) => { control.disabled = previous[index]; });
    button.textContent = originalLabel;
    button.classList.remove('is-pending');
    button.setAttribute('aria-busy', 'false');
    container.setAttribute('aria-busy', 'false');
    pendingOperations.delete(key);
  };
  try {
    return await Promise.resolve().then(operation);
  } finally {
    restorePendingState();
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
// 反映後の観測だけが残るinboxのawiは、保存状態をinboxのまま、表示だけをneeds-verifyにする。
// inboxの回答済みuwiは表示だけをansweredにし、色は保存状態inboxのバッジと同じにする。
// 状態フィルター・件数・操作の可否は保存状態で判定する。
function displayStateLabel(entry) {
  if (entry.state === 'inbox' && Date.parse(entry.cooldown_until) > Date.now()) return 'cooldown';
  if (entry.state === 'inbox' && entry.kind === 'uwi' && entry.answered === true) return 'answered';
  return entry.needs_verify === true ? 'needs-verify' : STATE_LABELS[entry.state] || 'unknown';
}
function entryStateText(entry) {
  return `${KIND_LABELS[entry.kind] || KIND_LABELS.unknown} / ${displayStateLabel(entry)}`;
}

function updateEntryStateDisplay(entry, button, badge) {
  const label = displayStateLabel(entry);
  badge.textContent = label;
  badge.dataset.cooldown = String(label === 'cooldown');
  if (label === 'cooldown') {
    const parts = formatDateParts(entry.cooldown_until);
    badge.title = `解除日時: ${parts.date} ${parts.time} JST`;
  } else badge.removeAttribute('title');
  button.setAttribute(
    'aria-label',
    [entry.filename, entry.target_repo || '対象なし', KIND_LABELS[entry.kind] || KIND_LABELS.unknown,
      label, badge.title, entry.plan ? 'plan' : '',
      entry.important === true ? '重要' : '',
      entry.kind === 'uwi' && entry.answered === false ? '未回答' : '', entry.summary || '要約なし']
      .filter(Boolean).join('、')
  );
}

// 最も近い期限で状態表示だけを更新し、一覧警告・詳細の入力・フォーカスを保持する。
function refreshCooldownDisplays() {
  clearTimeout(cooldownTimer);
  for (const {entry, button, badge} of entryStateDisplays.values()) updateEntryStateDisplay(entry, button, badge);
  if (currentEntry) {
    byId('detail-state').textContent = entryStateText(currentEntry);
    if (byId('delete-dialog').open) byId('delete-state').textContent = entryStateText(currentEntry);
  }
  const remaining = [...entries, ...(currentEntry ? [currentEntry] : [])]
    .filter(entry => entry.state === 'inbox')
    .map(entry => Date.parse(entry.cooldown_until) - Date.now()).filter(value => value > 0);
  if (remaining.length) {
    // setTimeoutが扱える遅延の範囲で予約し、長い期限は次の予約で再判定する。
    cooldownTimer = setTimeout(refreshCooldownDisplays, Math.min(...remaining, 2147483647));
  }
}

function appendCell(row, className) {
  const cell = document.createElement('span');
  cell.className = `entry-cell ${className}`;
  row.append(cell);
  return cell;
}

function appendTextCell(row, className, value) {
  const cell = appendCell(row, className);
  cell.textContent = value || '—';
  return cell;
}

function targetRepoDisplay(value) {
  if (!value || value.length <= TARGET_REPO_DISPLAY_LENGTH) return value || '—';
  const prefixLength = Math.floor((TARGET_REPO_DISPLAY_LENGTH - 1) / 2);
  const suffixLength = TARGET_REPO_DISPLAY_LENGTH - prefixLength - 1;
  return `${value.slice(0, prefixLength)}…${value.slice(-suffixLength)}`;
}

function entryPageUrl(entry) {
  const query = new URLSearchParams({filename: entry.filename});
  return `${BASE_PATH}/?${query.toString()}`;
}

function entrySelectionFromUrl() {
  const currentLocation = globalThis.location;
  if (!currentLocation || currentLocation.pathname !== `${BASE_PATH}/`) return null;
  const query = new URLSearchParams(currentLocation.search);
  const filename = query.get('filename');
  return filename ? {filename} : null;
}

// 一覧は再読込のたびに全行を再生成するため、コピー直後の表示は項目の識別子に対応付けて保持する。
const COPIED_LABEL_MS = 2000;
const copiedLabelDeadlines = new Map();

function renderEntry(entry) {
  const item = document.createElement('li');
  item.className = 'entry-row';
  const button = document.createElement('a');
  button.href = entryPageUrl(entry);
  button.className = 'entry-select';
  button.dataset.key = entryKey(entry);
  button.dataset.kind = entry.kind || 'unknown';
  const unanswered = entry.kind === 'uwi' && entry.answered === false;
  button.dataset.unansweredUwi = String(unanswered);
  button.setAttribute('aria-current', String(entryKey(currentEntry) === entryKey(entry)));

  appendTextCell(button, 'filename-cell', entry.filename);
  const targetRepo = appendTextCell(button, 'target-repo-cell', targetRepoDisplay(entry.target_repo));
  if (entry.target_repo) {
    targetRepo.title = entry.target_repo;
    targetRepo.setAttribute('aria-label', `target-repo: ${entry.target_repo}`);
  }
  const status = appendCell(button, 'status-cell');
  const kind = document.createElement('span');
  kind.className = 'entry-kind';
  kind.textContent = KIND_LABELS[entry.kind] || KIND_LABELS.unknown;
  status.append(kind);
  const badge = document.createElement('span');
  badge.className = 'state-badge';
  badge.dataset.state = entry.state;
  badge.dataset.needsVerify = String(entry.needs_verify === true);
  badge.textContent = displayStateLabel(entry);
  status.append(badge);
  if (entry.important === true) {
    const important = document.createElement('span');
    important.className = 'attention-badge important-badge';
    important.textContent = '重要';
    status.append(important);
  }

  if (entry.blocked === true) {
    // 未回答UWIの回答待ちは保存状態ではなく依存先から導いた値であり、編集中のholdと区別して示す。
    const blocked = document.createElement('span');
    blocked.className = 'blocked-badge';
    blocked.textContent = 'blocked';
    blocked.title = '未回答のUWIの回答待ち';
    status.append(blocked);
  }
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
  appendTextCell(button, 'summary-cell', entry.summary || entry.filename);
  updateEntryStateDisplay(entry, button, badge);
  entryStateDisplays.set(entryKey(entry), {entry, button, badge});
  button.addEventListener('click', event => {
    if (!isPlainPrimaryClick(event) || button.target) return;
    event.preventDefault();
    history.pushState({atkEntry: true}, '', button.href);
    void selectEntry(entry, button);
  });
  const copy = document.createElement('button');
  copy.type = 'button';
  copy.className = 'entry-copy button-secondary';
  const key = entryKey(entry);
  copy.dataset.key = key;
  copy.textContent = (copiedLabelDeadlines.get(key) ?? 0) > Date.now() ? 'コピーしました' : 'コピー';
  copy.setAttribute('aria-label', `${entry.filename}の要約をコピー`);
  copy.addEventListener('click', async () => {
    try {
      await (navigator.clipboard.writeText(`${entry.filename} ${entry.summary || ''}`));
      const deadline = Date.now() + COPIED_LABEL_MS;
      copiedLabelDeadlines.set(key, deadline);
      copy.textContent = 'コピーしました';
      byId('result-status').textContent = `${entry.filename}をコピーしました。`;
      setTimeout(() => {
        // 同じ項目を再度コピーした場合は、後のクリックの期限で戻す。
        if (copiedLabelDeadlines.get(key) !== deadline) return;
        copiedLabelDeadlines.delete(key);
        const current = document.querySelector(`#entry-list .entry-copy[data-key="${CSS.escape(key)}"]`);
        if (current) current.textContent = 'コピー';
      }, COPIED_LABEL_MS);
    } catch (error) {
      setGlobalError(diagnostic('WI', 'ブラウザー', `${WI_ROOT} / ${entryKey(entry)}`, `コピーに失敗しました。 ${error.message}`));
    }
  });
  item.append(button, copy);
  return item;
}

// 日常確認の期間だけを制限し、条件検索の入力値はモード往復の間もDOMへ保持する。
const DEFAULT_PERIOD = '2w';
const PERIOD_LABELS = {'2w': '直近2週間', '4w': '直近4週間', '8w': '直近8週間'};

function hasNonStateFilters() {
  const query = buildQuery();
  return query.has('q') || query.get('type') !== 'all' || query.get('answered') !== 'all' ||
    query.has('important') || query.has('target_repo') || query.has('source_kind');
}

function renderEmptyState() {
  const empty = byId('empty-state');
  const message = byId('empty-state-message');
  const clear = byId('empty-clear-button');
  const allStates = byId('empty-all-states-button');
  const allPeriods = byId('empty-all-periods-button');
  const create = byId('empty-create-button');
  empty.hidden = entries.length !== 0;
  clear.hidden = true;
  allStates.hidden = true;
  allPeriods.hidden = true;
  create.hidden = true;
  if (entries.length) return;
  if (displayMode === 'waiting') {
    message.textContent = '判断待ちの未回答UWIはありません。';
    return;
  }
  const query = buildQuery();
  const periodLabel = PERIOD_LABELS[query.get('period')];
  allPeriods.hidden = !periodLabel;
  if (hasNonStateFilters() || !['active', 'all'].includes(query.get('status'))) {
    message.textContent = '条件に一致する項目はありません。';
    clear.hidden = false;
  } else if (query.get('status') === 'active') {
    message.textContent = periodLabel ? `${periodLabel}に作成された対応中の項目はありません。` : '対応中の項目はありません。';
    allStates.hidden = false;
  } else if (periodLabel) {
    message.textContent = `${periodLabel}に作成された項目はありません。`;
  } else {
    message.textContent = '項目はまだありません。';
    create.hidden = false;
  }
}

// 一覧から除外したファイルの警告を表示する。実際の保存先と次の操作を含む全文は長く、狭い幅では一覧を画面の外へ
// 押し下げるため、警告があることと件数だけを常に示し、全文は折り畳む。
// detailsは作り直さずに中の行だけを差し替え、更新通知による再描画でも開閉状態を保つ。
function renderListWarnings(lines) {
  renderWarnings(byId('list-warning'), lines);
  const summary = byId('list-warning-summary');
  const text = `一覧から除外したファイルの警告: ${lines.length}件（開くと理由と次の操作を表示）`;
  // 外枠は通知領域（role="alert"）のため、同じ件数での再描画で読み上げを繰り返さないよう、変わった時だけ書き換える。
  if (summary.textContent !== text) summary.textContent = text;
  byId('list-warning-box').hidden = lines.length === 0;
}

// 行は項目のクロージャーを持つため、再利用せずに毎回作り直す。
function renderEntries(warnings = [], announce = false) {
  const list = byId('entry-list');
  const focusedKey = document.activeElement?.classList?.contains('entry-select')
    ? document.activeElement.dataset.key : null;
  entryStateDisplays.clear();
  renderList(list, entries, entryKey, entry => renderEntry(entry));
  refreshCooldownDisplays();
  if (focusedKey) {
    (entryButtonForKey(focusedKey) || list.querySelector('.entry-select') || byId('empty-clear-button')).focus();
  }
  const unanswered = entries.filter(entry => entry.kind === 'uwi' && entry.answered === false).length;
  byId('entry-count').textContent = `${entries.length}件（未回答UWI ${unanswered}件）`;
  // フィルター欄を折りたたむ狭い幅でも、一覧が期間で限られていることを見出しの近くで示す。
  const periodLabel = PERIOD_LABELS[buildQuery().get('period')];
  byId('entry-period').textContent = periodLabel ? `${periodLabel}に作成` : '';
  byId('entry-period').hidden = !periodLabel;
  renderPagination();
  renderEffectiveConditions();
  // 除外したファイルは状態を読めず、状態とファイル名では年月の保存先を特定できないため、応答が持つ実際のパスを対象に示す。
  renderListWarnings(warnings.map(item => diagnostic(
    'WI', SERVE_HOST, item.path || `${WI_ROOT} / ${item.state}/${item.filename}`,
    withNextAction(`一覧から除外したファイル: ${item.reason}`, item.next_action)
  )));
  renderEmptyState();
  if (announce) {
    byId('result-status').textContent = entries.length ? `${entries.length}件を表示` : '一致する項目はありません';
  }
}

function buildQuery(page = currentPage) {
  const parameters = new URLSearchParams();
  if (displayMode === 'waiting') {
    parameters.set('view', 'waiting');
    parameters.set('page', String(page));
    if (byId('waiting-target-filter').value) parameters.set('target_repo', byId('waiting-target-filter').value);
    return parameters;
  }
  if (displayMode === 'normal') {
    return new URLSearchParams({type: 'all', status: 'active', answered: 'all', period: DEFAULT_PERIOD, page: String(page)});
  }
  parameters.set('type', byId('kind-filter').value);
  parameters.set('status', byId('state-filter').value);
  parameters.set('answered', byId('answer-filter').value);
  parameters.set('period', byId('period-filter').value);
  if (byId('important-filter').value !== 'all') parameters.set('important', byId('important-filter').value);
  const values = {
    target_repo: byId('target-filter').value,
    source_kind: byId('source-filter').value,
    q: byId('search-input').value.trim()
  };
  Object.entries(values).forEach(([name, value]) => { if (value) parameters.set(name, value); });
  parameters.set('page', String(page));
  return parameters;
}

async function copyAllEntries() {
  if (pendingOperations.has('copy-all')) return;
  const query = buildQuery();
  query.delete('page');
  setTextMessage('copy-all-status', '現在のフィルターに一致する全ページを取得中');
  try {
    await runPending('copy-all', {
      container: byId('copy-all-button').parentElement, button: byId('copy-all-button'), busyLabel: '取得中'
    }, async () => {
      const payload = await api(`/api/entries?${query.toString()}`);
      const selected = payload.entries;
      if (!selected.length) {
        setTextMessage('copy-all-status', '現在のフィルターに一致する項目は0件です。条件を変更してください。');
        return;
      }
      await navigator.clipboard.writeText(selected.map(entry => `${entry.filename} ${entry.summary || ''}`).join('\n'));
      setTextMessage('copy-all-status', `${selected.length}件をコピーしました。`);
    });
  } catch (error) {
    setTextMessage('copy-all-status', '全てコピーに失敗しました。');
    setGlobalError(`全てコピーに失敗しました。 ${error.message} ブラウザーのクリップボード権限と接続を確認し、再度実行してください。`);
  }
}

function renderEffectiveConditions() {
  const query = buildQuery();
  if (displayMode === 'waiting') {
    byId('wi-effective-conditions').textContent = '判断待ち: 未回答UWI、active、全期間、重要順・作成日時の古い順' +
      (query.has('target_repo') ? `、target-repo=${query.get('target_repo')}` : '、全リポジトリ');
    return;
  }
  const labels = [`state=${query.get('status')}`, PERIOD_LABELS[query.get('period')] || '全期間'];
  for (const [key, label] of [['q', '検索'], ['type', 'type'], ['answered', 'answered'],
    ['important', '重要'], ['target_repo', 'target-repo'], ['source_kind', 'source kind']]) {
    const value = query.get(key);
    if (value && !(value === 'all' && ['type', 'answered'].includes(key))) labels.push(`${label}=${value}`);
  }
  byId('wi-effective-conditions').textContent = `${displayMode === 'normal' ? '通常表示' : '条件検索'}: ${labels.join('、')}`;
}

function setDisplayMode(mode) {
  displayMode = mode;
  byId('wi-mode-normal').checked = mode === 'normal';
  byId('wi-mode-search').checked = mode === 'search';
  byId('wi-mode-waiting').checked = mode === 'waiting';
  byId('waiting-filters').hidden = mode !== 'waiting';
  renderEffectiveConditions();
}

function captureListLoadingView() {
  return {
    indicator: byId('loading-indicator'),
    list: byId('entry-list'),
    previous: byId('previous-page-button'),
    next: byId('next-page-button'),
    status: byId('pagination-status')
  };
}

function renderListLoading(view = captureListLoadingView()) {
  view.indicator.hidden = !(listLoadingVisible || userListOperationPending || detailRequestPending);
  view.list.setAttribute('aria-busy', String(listRequestPending || userListOperationPending));
  renderPagination(view);
}

function beginUserListOperation() {
  // 一覧条件を変える操作が始まった時点で、それより前に始めた一覧取得の応答を無効にする。
  // 検索欄のデバウンス中も旧取得の一覧反映を防ぐため。
  // 無効にした取得のendListRequestは世代不一致で何もしないので、その処理中表示もここで解く。
  listRequestGeneration += 1;
  listRequestPending = false;
  listLoadingVisible = false;
  userListOperationPending = true;
  renderListLoading();
  return ++userListOperationGeneration;
}

function endUserListOperation(generation) {
  if (generation !== userListOperationGeneration) return;
  userListOperationPending = false;
  renderListLoading();
  if (externalListReloadPending) {
    externalListReloadPending = false;
    void reloadFromExternalChange();
  }
}

function isCurrentUserListOperation(generation) {
  return generation === userListOperationGeneration;
}

function cancelSearchTimer() {
  if (searchTimer !== null) clearTimeout(searchTimer);
  searchTimer = null;
}

function beginListRequest(view, showLoading = true) {
  listRequestPending = true;
  listLoadingVisible = listLoadingVisible || showLoading;
  renderListLoading(view);
}

function endListRequest(view, generation) {
  if (generation !== listRequestGeneration) return;
  listLoadingVisible = false;
  listRequestPending = false;
  renderListLoading(view);
}

function renderPagination(view = captureListLoadingView()) {
  const {previous, next, status} = view;
  if (!previous || !next || !status) return;
  const page = pagination.page || currentPage;
  const pageCount = pagination.page_count || 1;
  status.textContent = `ページ ${page} / ${pageCount}（全${pagination.total_count}件）`;
  previous.disabled = listRequestPending || userListOperationPending || page <= 1;
  next.disabled = listRequestPending || userListOperationPending || page >= pageCount;
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
  await (loadEntries({announce: true}));
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

async function loadEntries({announce = false, showLoading = true, timing = {}} = {}) {
  pendingListAnnouncement = pendingListAnnouncement || announce;
  const query = buildQuery(currentPage);
  const generation = ++listRequestGeneration;
  const loadingView = captureListLoadingView();
  beginListRequest(loadingView, showLoading);
  const pending = (async () => {
    try {
      const payload = await api(`/api/entries?${query.toString()}`, {timing});
      if (generation !== listRequestGeneration) return entries;
      entries = Array.isArray(payload.entries) ? payload.entries : [];
      applyPagination(payload);
      const selected = entries.find(item => entryKey(item) === entryKey(currentEntry));
      if (selected) currentEntry = {...currentEntry, ...selected};
      const shouldAnnounce = pendingListAnnouncement;
      pendingListAnnouncement = false;
      renderEntries(
        Array.isArray(payload.warnings) ? payload.warnings : [],
        shouldAnnounce
      );
      renderWaitingProgress();
      return entries;
    } catch (error) {
      if (generation === listRequestGeneration) {
        pendingListAnnouncement = false;
        setGlobalError(error.message);
      }
      return entries;
    }
  })();
  return pending.finally(() => endListRequest(loadingView, generation));
}

function syncNotificationButton() {
  const button = byId('notification-button');
  button.hidden = typeof Notification === 'undefined' || Notification.permission !== 'default';
}

// 新しく登録されたユーザー対応の項目（未回答UWIと要確認AWI）だけを通知する。
// 既知集合はファイル名で持ち、初回取得では通知せずに基準だけを作る。
// 既知の項目の回答・確認状態・対象repoの変化と、削除後の同名の再登録は通知しない。
async function refreshKnownEntries({notify = false, timing = {}} = {}) {
  const generation = ++knownEntryRequestGeneration;
  let payload;
  try {
    // 通知の対象は新規の処理待ち項目だけのため、未終了の項目に限って取得する。全状態の一覧は数MBに及び、
    // 変更通知のたびに取得すると参照の実行枠を占めて画面の操作を待たせる。
    payload = await (api('/api/entries?type=all&status=active&answered=all', {timing}));
  } catch (error) {
    if (generation !== knownEntryRequestGeneration) return;
    throw error;
  }
  if (generation !== knownEntryRequestGeneration) return;
  const allEntries = Array.isArray(payload.entries) ? payload.entries : [];
  const added = knownEntryBaselineReady && notify
    ? allEntries.filter(entry => !knownEntryFilenames.has(entry.filename) && PROCESSABLE_STATES.has(entry.state))
    : [];
  allEntries.forEach(entry => knownEntryFilenames.add(entry.filename));
  knownEntryBaselineReady = true;
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
  const notifications = [
    ['新規未回答UWI', added.filter(entry => entry.kind === 'uwi' && entry.answered === false)],
  ];
  for (const [title, entries] of notifications) {
    if (!entries.length) continue;
    const filenames = entries.map(entry => entry.filename);
    new Notification(title, {
      body: filenames.length === 1 ? filenames[0] : `${filenames.length}件: ${filenames.join('、')}`
    });
  }
}

async function enableNotifications() {
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

async function loadTargetRepos({timing = {}} = {}) {
  const generation = ++targetRepoRequestGeneration;
  const requestedState = byId('state-filter').value;
  try {
    const status = encodeURIComponent(requestedState);
    const payload = await (api(`/api/repos?status=${status}`, {timing}));
    if (generation !== targetRepoRequestGeneration ||
        byId('state-filter').value !== requestedState) return false;
    const repos = Array.isArray(payload.repos) ? payload.repos : [];
    replaceOptions(byId('target-filter'), repos, 'all');
    // 判断待ちは検索条件のstateに依存せず、activeのrepo候補を別途取得する。
    if (displayMode === 'waiting') {
      const waiting = await api('/api/repos?status=active', {timing});
      if (generation !== targetRepoRequestGeneration || displayMode !== 'waiting') return false;
      replaceOptions(byId('waiting-target-filter'), waiting.repos, 'all');
    }
    const datalist = byId('repo-options');
    datalist.replaceChildren(...repos.map(value => {
      const option = document.createElement('option');
      option.value = value;
      return option;
    }));
    return true;
  } catch (error) {
    const isCurrent = generation === targetRepoRequestGeneration &&
      byId('state-filter').value === requestedState;
    if (isCurrent) setGlobalError(error.message);
    return isCurrent;
  }
}

async function clearFilters({timing = {}} = {}) {
  cancelSearchTimer();
  const operation = beginUserListOperation();
  byId('search-input').value = '';
  byId('kind-filter').value = 'all';
  byId('state-filter').value = 'all';
  byId('period-filter').value = 'all';
  byId('answer-filter').value = 'all';
  byId('important-filter').value = 'all';
  byId('target-filter').value = '';
  byId('source-filter').value = '';
  setDisplayMode('normal');
  currentPage = 1;
  pagination.page = 1;
  try {
    await (loadTargetRepos({timing}));
    if (!isCurrentUserListOperation(operation)) return;
    await (loadEntries({announce: true, timing}));
  } finally {
    endUserListOperation(operation);
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
  const emptyAction = [
    byId('empty-clear-button'), byId('empty-all-states-button'), byId('empty-all-periods-button'), byId('empty-create-button')
  ]
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
    appendMetadataItem(metadata, 'answered', entry.answered ? 'yes' : 'no');
  }
  for (const item of metadataEntries(entry)) {
    const key = item.key;
    if (key?.type === 'str' && FRONTMATTER_EXCLUDED_KEYS.has(key.value)) continue;
    if (key?.type === 'str' && key.value === 'cooldown_until' && entry.cooldown_until) {
      const deadline = formatDateParts(entry.cooldown_until);
      appendMetadataItem(metadata, key.value, `${deadline.date} ${deadline.time} JST`);
    } else appendMetadataItem(metadata, formatMetadataKey(key), item.value);
  }
  const parts = formatDateParts(entry.updated_at);
  appendMetadataItem(metadata, 'updated_at', parts.time ? `${parts.date} ${parts.time}` : parts.date);
}

function setDetailMode(mode) {
  const editing = mode === 'edit';
  const commenting = mode === 'user-comment';
  const decisionAction = mode === 'decide-adopt' ? 'adopt' : mode === 'decide-reject' ? 'reject' : '';
  const mutating = editing || commenting || decisionAction !== '';
  const unansweredUwi = currentEntry?.kind === 'uwi' && currentEntry.answered === false;
  const processable = currentEntry && PROCESSABLE_STATES.has(currentEntry.state);
  const mutable = currentEntry && MUTABLE_STATES.has(currentEntry.state);
  const deletable = currentEntry && DELETABLE_STATES.has(currentEntry.state);
  const held = currentEntry?.state === 'hold';
  const terminal = currentEntry && TERMINAL_STATES.has(currentEntry.state);
  byId('edit-panel').hidden = !editing;
  // 回答できるUWIは閲覧中から回答欄を表示し、回答と他の操作を切り替えなしで行えるようにする。
  const answering = !mutating && currentEntry?.kind === 'uwi' && (Boolean(mutable) || displayMode === 'waiting');
  byId('answer-panel').hidden = !answering;
  const waiting = displayMode === 'waiting' && answering;
  byId('save-answer-button').hidden = waiting;
  byId('save-next-answer-button').hidden = !waiting;
  byId('skip-answer-button').hidden = !waiting;
  // 連続回答では本文の読取領域を確保する。編集・状態操作は詳細を閉じて通常/検索表示から行える。
  for (const id of ['delete-button', 'adopt-button', 'edit-button']) byId(id).parentElement.hidden = waiting;
  renderWaitingProgress();
  byId('user-comment-panel').hidden = !commenting;
  // 採否のメモは採用・却下の操作モードでだけ表示し、閲覧中の本文とユーザーコメントの欄から分ける。
  byId('decision-panel').hidden = decisionAction === '';
  byId('decision-panel').dataset.action = decisionAction;
  if (decisionAction) byId('decision-heading').textContent = decisionAction === 'adopt' ? '採用' : '却下';
  byId('edit-button').hidden = mutating || !currentEntry || !EDITABLE_STATES.has(currentEntry.state);
  byId('important-button').hidden = mutating || !currentEntry;
  // 確認状態は確認状態を持つ未終了のAWIだけが切り替えられ、ボタンの文言で次の操作を示す。
  byId('important-button').textContent = currentEntry?.important === true ? '重要解除' : '重要設定';
  byId('user-comment-button').hidden = mutating || currentEntry?.user_comment_editable !== true;
  byId('adopt-button').hidden = mutating || !mutable;
  byId('reject-button').hidden = mutating || !mutable || currentEntry.kind !== 'awi';
  byId('hold-button').hidden = mutating || !(processable || terminal);
  byId('unhold-button').hidden = mutating || !held;
  byId('return-to-inbox-button').hidden = mutating || !terminal;
  byId('delete-button').hidden = mutating || !deletable;
  byId('save-entry-button').hidden = !editing;
  byId('save-user-comment-button').hidden = !commenting;
  byId('confirm-adopt-button').hidden = decisionAction !== 'adopt';
  byId('confirm-reject-button').hidden = decisionAction !== 'reject';
  syncDetailMutationAvailability();
  byId('edit-button').className = unansweredUwi ? 'button-secondary' : 'button-primary';
  byId('adopt-button').className = unansweredUwi ? 'button-secondary' : 'button-primary';
  if (!editing) setFieldError(byId('edit-content'), byId('edit-content-error'), '');
  if (!answering) setFieldError(byId('answer-input'), byId('answer-input-error'), '');
  if (!commenting) setFieldError(byId('user-comment-input'), byId('user-comment-input-error'), '');
}

function syncDetailMutationAvailability() {
  for (const id of [
    'edit-button', 'user-comment-button', 'delete-button', 'important-button',
    'save-entry-button', 'save-answer-button', 'save-next-answer-button', 'skip-answer-button', 'save-user-comment-button',
    'adopt-button', 'reject-button', 'hold-button', 'unhold-button', 'return-to-inbox-button',
    'confirm-adopt-button', 'confirm-reject-button'
  ]) {
    const button = byId(id);
    const userCommentUnavailable = id === 'save-user-comment-button' && currentEntry?.user_comment_editable !== true;
    button.disabled = !button.hidden && (waitingAdvancePending || detailRefreshRequired || userCommentUnavailable ||
      id === 'save-next-answer-button' && (waitingSavedFilename === currentEntry?.filename ||
        currentEntry?.answered !== false || !MUTABLE_STATES.has(currentEntry?.state)));
  }
  // 見送りは競合した入力を明示的に破棄して次へ進むため、保存を止めた後にも使える。
  byId('skip-answer-button').disabled = waitingAdvancePending;
}

function renderWaitingProgress() {
  const progress = byId('waiting-progress');
  progress.hidden = displayMode !== 'waiting' || !currentEntry;
  if (progress.hidden) return;
  const index = entries.findIndex(entry => entry.filename === currentEntry.filename);
  const position = index < 0 ? null : (currentPage - 1) * pagination.page_size + index + 1;
  progress.textContent = position === null
    ? `未回答 残り${pagination.total_count}件（現在の項目は一覧の対象外）`
    : `現在 ${position} / ${pagination.total_count}件・未回答 残り${pagination.total_count}件`;
}

async function advanceWaiting(after, sessionGeneration, savedMessage = '') {
  const listOperation = beginUserListOperation();
  const stillCurrent = () => displayMode === 'waiting' && byId('detail-dialog').open &&
    sessionGeneration === detailSessionGeneration && currentEntry?.filename === after.filename;
  try {
    // 順番の基準だけを保持し、候補集合はページごとに再取得する。保存で前の行が消えても飛ばさない。
    for (let page = 1; ; page += 1) {
      const payload = await api(`/api/entries?${buildQuery(page).toString()}`);
      if (!stillCurrent()) return;
      const candidates = payload.entries;
      for (let index = 0; index < candidates.length; index += 1) {
        const candidate = candidates[index];
        const follows = candidate.important === true && after.important !== true ? false :
          candidate.important !== true && after.important === true || candidate.filename > after.filename;
        if (candidate.filename === after.filename || !follows) continue;
        let detail;
        try {
          detail = (await api(`/api/entries/${encodeURIComponent(candidate.filename)}`)).entry;
        } catch (error) {
          if (error.status === 404) continue;
          throw error;
        }
        if (!stillCurrent()) return;
        if (detail.kind !== 'uwi' || detail.answered !== false || !MUTABLE_STATES.has(detail.state)) continue;
        entries = candidates;
        applyPagination(payload);
        renderEntries(payload.warnings || []);
        detailSessionGeneration += 1;
        detailRequestGeneration += 1;
        detailOriginKey = detail.filename;
        waitingSavedFilename = '';
        displayEntry(detail);
        byId('detail-dialog-body').scrollTop = 0;
        byId('detail-dialog-body').focus();
        if (savedMessage) deliverOperationMessage(savedMessage);
        return;
      }
      if (page >= (payload.pagination?.page_count || 1)) {
        entries = candidates;
        applyPagination(payload);
        closeDetailDialog({force: true});
        renderEntries(payload.warnings || []);
        deliverOperationMessage(`${savedMessage}この順番での連続確認は終了しました。未回答は残り${pagination.total_count}件です。`);
        return;
      }
    }
  } catch (error) {
    if (stillCurrent()) deliverOperationMessage(
      `${savedMessage}次の項目を取得できませんでした。入力を保持しています。「保存せず次へ」で再試行してください。 ${error.message}`, true
    );
  } finally {
    endUserListOperation(listOperation);
  }
}

async function skipWaitingAnswer() {
  if (!currentEntry || displayMode !== 'waiting' || waitingAdvancePending || detailMutationPending) return;
  if (answerInputChanged() && !window.confirm('入力した回答を保存せずに次へ進みますか？')) return;
  waitingAdvancePending = true;
  detailMutationPending = true;
  syncDetailMutationAvailability();
  try {
    await advanceWaiting({...currentEntry}, detailSessionGeneration);
  } finally {
    waitingAdvancePending = false;
    detailMutationPending = false;
    detailReloadDeferred = false;
    syncDetailMutationAvailability();
  }
}

function renderAnswerChoices(entry) {
  const container = byId('answer-choices');
  const choices = entry.question_type === 'yes-no' ? ['はい', 'いいえ'] :
    ['choice', 'post-approval'].includes(entry.question_type) && Array.isArray(entry.choices) ? entry.choices : [];
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
  // 同じ項目の再表示（外部変更の反映など）では、入力途中の回答を保持する。
  const keepTypedAnswer = entryKey(currentEntry) === entryKey(entry) && answerInputChanged();
  currentEntry = entry;
  if (waitingSavedFilename !== entry.filename) waitingSavedFilename = '';
  if (!keepTypedAnswer) byId('answer-input').value = entry.answer || '';
  refreshCooldownDisplays();
  detailRefreshRequired = false;
  byId('decision-note').value = '';
  setTextMessage('detail-alert', '');
  byId('detail-view').hidden = false;
  byId('detail-filename').textContent = entry.filename;
  byId('detail-state').textContent = entryStateText(entry);
  byId('detail-state').dataset.state = entry.state;
  byId('detail-content').innerHTML = entry.body_html ?? entry.content_html ?? '';
  renderMetadata(entry);
  renderAnswerChoices(entry);
  byId('readonly-notice').hidden = EDITABLE_STATES.has(entry.state);
  setDetailMode('view');
  updateCurrentRowSelection();
  if (entrySelectionFromUrl()) history.replaceState({atkEntry: true}, '', entryPageUrl(entry));
}

// 一覧の行と開いている詳細の対象は、外部操作で移動・削除され得る。移動はサーバーが全状態から最新の状態を
// 探して返すため、詳細APIの404は削除済みを意味する。エンドユーザーへ再操作を求めず、削除済みの通知と一覧の更新を行う。
function reportDeletedEntry(filename, {reloadList = true} = {}) {
  deliverOperationMessage(`${filename}は削除されたため表示できません。一覧を更新しました。`);
  if (reloadList) void reloadFromExternalChange();
}

async function selectEntry(entry, origin = null, {ignoreNotFound = false} = {}) {
  const requestGeneration = ++detailRequestGeneration;
  const sessionGeneration = ++detailSessionGeneration;
  const requestIsCurrent = () => requestGeneration === detailRequestGeneration &&
    sessionGeneration === detailSessionGeneration;
  detailOrigin = origin || document.activeElement;
  detailOriginKey = entryKey(entry);
  clearDialogMessages('detail');
  detailRequestPending = true;
  renderListLoading();
  const pendingRow = origin?.closest('.entry-row');
  if (pendingRow) {
    pendingRow.setAttribute('aria-busy', 'true');
  }
  try {
    const payload = await api(`/api/entries/${encodeURIComponent(entry.filename)}`);
    if (!requestIsCurrent()) return;
    displayEntry(payload.entry);
    openDetailDialog(detailOrigin);
    if (payload.entry.state !== entry.state) void loadEntries({showLoading: false});
  } catch (error) {
    if (!requestIsCurrent()) return;
    if (error.status === 404) {
      if (!ignoreNotFound) reportDeletedEntry(entry.filename);
      return;
    }
    setGlobalError(error.message);
  } finally {
    if (pendingRow) {
      pendingRow.removeAttribute('aria-busy');
    }
    if (requestIsCurrent()) {
      detailRequestPending = false;
      renderListLoading();
    }
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

function closeDetailDialog({updateUrl = true, force = false} = {}) {
  const detailDialog = byId('detail-dialog');
  const deleteDialog = byId('delete-dialog');
  const hadOpenDialog = detailDialog.open || deleteDialog.open;
  if (!hadOpenDialog && !currentEntry) return;
  if (!force) {
    const mode = currentDetailMode();
    const changed = mode === 'edit' && byId('edit-content').value !== currentEntry?.content ||
      answerInputChanged() ||
      mode === 'user-comment' && byId('user-comment-input').value !== (currentEntry?.user_comment || '') ||
      mode.startsWith('decide-') && byId('decision-note').value !== '';
    if (changed && !window.confirm('変更した入力を破棄しますか？')) return;
  }
  const returnTarget = detailReturnTarget();
  detailRequestGeneration += 1;
  detailSessionGeneration += 1;
  detailRequestPending = false;
  renderListLoading();
  closeDeleteDialog({restoreFocus: false});
  closeDialog(detailDialog, {restoreFocus: false});
  currentEntry = null;
  detailOriginKey = '';
  detailRefreshRequired = false;
  setTextMessage('detail-alert', '');
  setDetailMode('view');
  updateCurrentRowSelection();
  if (updateUrl && globalThis.location?.pathname === `${BASE_PATH}/` && entrySelectionFromUrl()) {
    history.replaceState({atkEntry: false}, '', `${BASE_PATH}/`);
  }
  if (hadOpenDialog && returnTarget && typeof returnTarget.focus === 'function') returnTarget.focus();
}

async function restoreEntryFromUrl() {
  const selection = entrySelectionFromUrl();
  if (!selection) {
    if (byId('detail-dialog').open || currentEntry) closeDetailDialog({updateUrl: false});
    return;
  }
  const origin = document.querySelector(`.entry-select[data-key="${CSS.escape(entryKey(selection))}"]`);
  await selectEntry(selection, origin, {ignoreNotFound: true});
}

function currentDetailMode() {
  if (!byId('edit-panel').hidden) return 'edit';
  if (!byId('user-comment-panel').hidden) return 'user-comment';
  if (!byId('decision-panel').hidden) return `decide-${byId('decision-panel').dataset.action}`;
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

// 詳細ダイアログから変更操作を送信している間は、開いている詳細の再読込を保留し、保留したことだけを記録する。
// その間に届く更新通知には操作自身の書込みが含まれ、外部更新として比べると成功した操作に警告を表示する。
// 操作によっては項目の状態も変わり、詳細の表示が書き換わる。保留した再読込は失敗時にだけ実行する。
function deferDetailReloadWhileMutating() {
  if (!detailMutationPending) return false;
  detailReloadDeferred = true;
  return true;
}

async function reloadOpenDetailFromExternalChange({timing = {}} = {}) {
  if (!byId('detail-dialog').open || !currentEntry) return;
  if (detailOriginKey !== entryKey(currentEntry)) return;
  if (deferDetailReloadWhileMutating()) return;
  const sessionGeneration = detailSessionGeneration;
  const filename = currentEntry.filename;
  const requestGeneration = ++detailRequestGeneration;
  // 送信の開始前に始まった再読込も、応答を待つ間に結果を反映せず保留へ回す。
  const requestIsCurrent = () => !deferDetailReloadWhileMutating() &&
    requestGeneration === detailRequestGeneration &&
    sessionGeneration === detailSessionGeneration && byId('detail-dialog').open &&
    currentEntry?.filename === filename;
  let deleteConfirmationInvalidated = false;
  if (byId('delete-dialog').open && deleteEntrySnapshot(currentEntry) !== deleteDialogEntrySnapshot) {
    deleteConfirmationInvalidated = invalidateDeleteConfirmation();
  }
  let resolvedEntry = null;
  try {
    const payload = await api(`/api/entries/${encodeURIComponent(filename)}`, {timing});
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
    if (displayMode === 'waiting') {
      detailRefreshRequired = true;
      syncDetailMutationAvailability();
      setTextMessage('detail-alert', '項目が削除されました。入力を保持しています。「保存せず次へ」で次の項目を確認できます。');
      return;
    }
    closeDetailDialog({force: true});
    reportDeletedEntry(filename, {reloadList: false});
    return;
  }
  const detailChanged = resolvedEntry.state !== currentEntry.state ||
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
  // 編集・採否のモードと、閲覧中に入力途中の回答がある場合は、入力を保持して保存を止める。
  // 入力が最新内容の回答と同じなら保存済みである（commitだけ失敗した保存の再表示など）ため、保護せずに表示を更新する。
  const unsavedAnswer = answerInputChanged() && byId('answer-input').value !== (resolvedEntry.answer || '');
  if (mode !== 'view' || unsavedAnswer) {
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
  else if (syncIncompleteNotice?.sessionGeneration === detailSessionGeneration &&
    syncIncompleteNotice.filename === resolvedEntry.filename) {
    setTextMessage('detail-alert', syncIncompleteNotice.message);
  }
}

function enterEdit() {
  if (!currentEntry) return;
  byId('edit-content').value = currentEntry.content;
  setDetailMode('edit');
  byId('edit-content').focus();
}

// 編集・コメント・採否のモードでは回答欄を隠すが、入力途中の回答は保持しているため、表示状態ではなく回答できる項目かで判定する。
function answerInputChanged() {
  const answerable = currentEntry?.kind === 'uwi' && (MUTABLE_STATES.has(currentEntry.state) || displayMode === 'waiting');
  return answerable && byId('answer-input').value !== (currentEntry.answer || '');
}


function enterUserComment() {
  if (!currentEntry || currentEntry.user_comment_editable !== true) return;
  byId('user-comment-input').value = currentEntry.user_comment || '';
  setDetailMode('user-comment');
  byId('user-comment-input').focus();
}

function enterDecision(action) {
  if (!currentEntry || detailRefreshRequired || !MUTABLE_STATES.has(currentEntry.state)) return;
  if (action === 'reject' && currentEntry.kind !== 'awi') return;
  byId('decision-note').value = '';
  setDetailMode(`decide-${action}`);
  byId('decision-note').focus();
}

async function reloadUserCommentAfterConflict(key, sessionGeneration) {
  const filename = currentEntry?.filename;
  if (!filename) return false;
  try {
    const refreshed = await api(`/api/entries/${encodeURIComponent(filename)}`);
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

// 操作の対象と開いている詳細の同一性は、操作で変わり得る状態を除き、詳細を開いた単位とファイル名で判定する。
// 成功の表示と一覧の再取得を行い、一覧の再描画を終えるPromiseを返す（操作の所要の終点に使う）。
function finishDetailMutation(key, filename, sessionGeneration, message, timing = {}) {
  const sameDetail = byId('detail-dialog').open && currentEntry?.filename === filename &&
    sessionGeneration === detailSessionGeneration;
  if (sameDetail) closeDetailDialog({force: true});
  const reloaded = loadEntries({showLoading: false, timing}).then(() => {
    if (!sameDetail || document.activeElement !== document.body) return;
    (entryButtonForKey(key) || document.querySelector('.entry-select') ||
      (!byId('empty-clear-button').hidden && byId('empty-clear-button')) || byId('search-input')).focus();
  });
  if (sameDetail) deliverOperationMessage(message);
  else showToast(message);
  return reloaded;
}

// 詳細ダイアログからの全変更操作を実行する共通の処理。送信中は詳細の再読込を保留し、成功時は保留を破棄して
// 詳細を閉じ成功を通知する。失敗時は保留した再読込を実行してから例外を呼び出し元へ返し、
// 呼び出し元が表示する失敗の文を再読込の警告で上書きしない。
// 所要はボタン操作から結果の表示（詳細を閉じた後の一覧の再描画、失敗の表示）までを1件の操作として残す。
async function runDetailMutation(pendingKey, pendingOptions, request, successMessage, onSuccess = null) {
  if (pendingOperations.has(pendingKey)) return;
  const operation = beginOperation('wi', `mutation:${pendingKey}`, {trigger: 'click'});
  const key = entryKey(currentEntry);
  const filename = currentEntry.filename;
  const sessionGeneration = detailSessionGeneration;
  detailMutationPending = true;
  detailReloadDeferred = false;
  let result;
  try {
    result = await (runPending(pendingKey, pendingOptions, () => request(operation.timing)));
  } catch (error) {
    detailMutationPending = false;
    if (pendingKey === 'answer' && waitingAdvancePending) {
      detailReloadDeferred = false;
      if (error.payload?.code === 'sync_incomplete') {
        waitingSavedFilename = filename;
        detailRefreshRequired = true;
      }
      operation.end('error');
      throw error;
    }
    if (error.payload?.code === 'sync_incomplete' &&
        sessionGeneration === detailSessionGeneration && currentEntry?.filename === filename) {
      const message = error.message;
      detailReloadDeferred = false;
      if (pendingKey === 'delete') {
        closeDetailDialog({force: true});
        await loadEntries({showLoading: false, timing: operation.timing});
        deliverOperationMessage(message, true);
      } else {
        syncIncompleteNotice = {sessionGeneration, filename, message};
        // 応答はローカル保存済みを保証する。保存した値を表示へ切り替え、後続の編集では通常の入力保持へ戻す。
        setDetailMode('view');
        await reloadOpenDetailFromExternalChange({timing: operation.timing});
        await loadEntries({showLoading: false, timing: operation.timing});
        deliverOperationMessage(message, true);
      }
      operation.end('sync-incomplete');
      return;
    }
    if (detailReloadDeferred) {
      detailReloadDeferred = false;
      await (reloadOpenDetailFromExternalChange({timing: operation.timing}));
    }
    // 失敗の文は呼出し元が例外を受けて同期的に表示するため、その描画後の時刻で記録される。
    operation.end('error');
    throw error;
  } finally {
    detailMutationPending = false;
  }
  detailReloadDeferred = false;
  const message = typeof successMessage === 'function' ? successMessage(result) : successMessage;
  if (onSuccess) {
    await onSuccess(message);
    operation.end('ok');
    return;
  }
  void finishDetailMutation(key, filename, sessionGeneration, message, operation.timing)
    .finally(() => operation.end('ok'));
}

async function saveEntry() {
  if (!currentEntry || detailRefreshRequired) return;
  const content = byId('edit-content').value;
  setFieldError(byId('edit-content'), byId('edit-content-error'), content.trim() ? '' : 'ファイル全体を入力してください。');
  if (firstInvalid([byId('edit-content')])) return;
  const key = entryKey(currentEntry);
  const path = `/api/entries/${encodeURIComponent(currentEntry.filename)}`;
  const payload = {content, expected_content: currentEntry.content};
  clearDialogMessages('detail');
  try {
    await (runDetailMutation('save', {
      container: byId('detail-shell'), button: byId('save-entry-button'), busyLabel: '保存中'
    }, timing => api(path, {method: 'PUT', body: JSON.stringify(payload), timing}), `${key}を保存しました。`));
  } catch (error) {
    const failure = `${key}を保存できませんでした。 ${error.message}`;
    deliverOperationMessage(mutationFailureMessage(key, failure, error), true);
    if (byId('detail-dialog').open && entryKey(currentEntry) === key) byId('edit-content').focus();
  }
}

async function toggleImportant() {
  if (!currentEntry || detailRefreshRequired) return;
  const key = entryKey(currentEntry);
  const important = currentEntry.important !== true;
  const label = important ? '重要設定' : '重要解除';
  const payload = {filename: currentEntry.filename, important, expected_content: currentEntry.content};
  clearDialogMessages('detail');
  try {
    await runDetailMutation('important', {
      container: byId('detail-shell'), button: byId('important-button'), busyLabel: '保存中'
    }, timing => api('/api/entries/important', {method: 'POST', body: JSON.stringify(payload), timing}), `${key}を${label}しました。`);
  } catch (error) {
    deliverOperationMessage(mutationFailureMessage(key, `${key}を${label}できませんでした。 ${error.message}`, error), true);
  }
}

async function saveAnswer({next = false} = {}) {
  if (!currentEntry || detailRefreshRequired || waitingAdvancePending || detailMutationPending) return;
  const advance = next && displayMode === 'waiting';
  if (advance && (waitingSavedFilename === currentEntry.filename || currentEntry.answered !== false ||
      !MUTABLE_STATES.has(currentEntry.state))) return;
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
  const answeredEntry = {...currentEntry};
  if (advance) waitingAdvancePending = true;
  try {
    await (runDetailMutation('answer', {
      container: byId('detail-shell'), button: byId(advance ? 'save-next-answer-button' : 'save-answer-button'), busyLabel: '保存中'
    }, timing => api('/api/entries/answer', {method: 'POST', body: JSON.stringify(payload), timing}), result =>
      result.state === 'adopted' ? `${key}の回答を保存し、採用済み（adopted）へ移しました。` : `${key}へ回答しました。`,
    advance ? async message => {
      waitingSavedFilename = answeredEntry.filename;
      detailMutationPending = true;
      try {
        await advanceWaiting(answeredEntry, sessionGeneration, message);
      } finally {
        detailMutationPending = false;
        detailReloadDeferred = false;
      }
    } : null));
  } catch (error) {
    const failure = `${key}へ回答できませんでした。 ${error.message}`;
    if (sessionGeneration === detailSessionGeneration) {
      deliverOperationMessage(mutationFailureMessage(key, failure, error), true);
    } else {
      showToast(mutationFailureMessage(key, failure, error), true);
    }
    if (byId('detail-dialog').open && entryKey(currentEntry) === key) byId('answer-input').focus();
  } finally {
    if (advance) waitingAdvancePending = false;
    syncDetailMutationAvailability();
  }
}

async function saveUserComment() {
  if (!currentEntry || detailRefreshRequired || currentEntry.user_comment_editable !== true) return;
  const input = byId('user-comment-input');
  const comment = input.value;
  setFieldError(input, byId('user-comment-input-error'), '');
  const key = entryKey(currentEntry);
  // 空の保存はユーザーコメント節の削除を表す。節が無い項目では本文を変えずに終える。
  const savedMessage = comment.trim() ? `${key}のユーザーコメントを保存しました。` :
    currentEntry.user_comment == null ? `${key}にはユーザーコメントが無いため、変更していません。` :
      `${key}のユーザーコメントを削除しました。`;
  const sessionGeneration = detailSessionGeneration;
  const payload = {
    state: currentEntry.state,
    filename: currentEntry.filename,
    comment,
    expected_content: currentEntry.content
  };
  clearDialogMessages('detail');
  try {
    await (runDetailMutation('user-comment', {
      container: byId('detail-shell'), button: byId('save-user-comment-button'), busyLabel: '保存中'
    }, timing => api('/api/entries/user-comment', {method: 'POST', body: JSON.stringify(payload), timing}), savedMessage));
  } catch (error) {
    if (error.payload?.code === 'edit_conflict' &&
        await (reloadUserCommentAfterConflict(key, sessionGeneration))) return;
    deliverOperationMessage(`${key}のユーザーコメントを保存できませんでした。 ${error.message}`, true);
    if (byId('detail-dialog').open && entryKey(currentEntry) === key) input.focus();
  }
}

async function transitionDetail(action) {
  if (!currentEntry || detailRefreshRequired) return;
  const terminal = TERMINAL_STATES.has(currentEntry.state);
  const allowed = action === 'unhold' ? currentEntry.state === 'hold' :
    action === 'return-to-inbox' ? terminal :
      action === 'hold' ? PROCESSABLE_STATES.has(currentEntry.state) || terminal : MUTABLE_STATES.has(currentEntry.state);
  if (!allowed || (action === 'reject' && currentEntry.kind !== 'awi')) return;
  const key = entryKey(currentEntry);
  const payload = {filenames: [currentEntry.filename]};
  if (terminal && (action === 'return-to-inbox' || action === 'hold')) payload.state = currentEntry.state;
  if ((action === 'adopt' || action === 'reject') && currentEntry.state === 'hold') payload.state = 'hold';
  const note = byId('decision-note').value.trim();
  if (note && (action === 'adopt' || action === 'reject')) payload.note = note;
  const completedAction = {
    adopt: '採用しました', reject: '却下しました', hold: '保留しました', unhold: '保留解除しました',
    'return-to-inbox': 'inboxへ戻しました'
  }[action];
  try {
    await (runDetailMutation(`transition-${action}`, {
      container: byId('detail-shell'),
      button: byId(action === 'adopt' || action === 'reject' ? `confirm-${action}-button` : `${action}-button`),
      busyLabel: '処理中'
    }, timing => api(`/api/entries/${action}`, {method: 'POST', body: JSON.stringify(payload), timing}), `${key}を${completedAction}。`));
  } catch (error) {
    deliverOperationMessage(`${key}を処理できませんでした。 ${error.message}`, true);
  }
}

function resetCreateForm() {
  byId('create-kind').value = 'awi';
  byId('create-content').value = '';
  byId('create-target').value = '';
  byId('create-scope').value = '';
  byId('create-question-type').value = 'yes-no';
  byId('create-choices').value = '';
  setFieldError(byId('create-content'), byId('create-content-error'), '');
  setFieldError(byId('create-target'), byId('create-target-error'), '');
  setFieldError(byId('create-choices'), byId('create-choices-error'), '');
  updateCreateFields();
}

function updateCreateFields() {
  const kind = byId('create-kind').value;
  const isBatch = kind === 'batch';
  const showFormatHint = !isBatch && isShowFormatCandidate(byId('create-content').value);
  const isUwi = kind === 'uwi';
  const isChoice = isUwi && !showFormatHint && byId('create-question-type').value === 'choice';
  byId('create-mode-hint').hidden = !showFormatHint;
  byId('uwi-fields').hidden = !isUwi || showFormatHint;
  byId('choice-fields').hidden = !isChoice;
  // 一括登録は各エントリのfrontmatterの値だけを用いるため、対象リポジトリ欄を隠す。
  byId('create-repo-fields').hidden = isBatch || showFormatHint;
  byId('create-content-label').textContent = isBatch || showFormatHint ? 'show形式テキスト（必須）' : '本文（必須）';
  if (!isChoice || choiceValues().length >= 2) {
    setFieldError(byId('create-choices'), byId('create-choices-error'), '');
  }
}

// サーバーの`is_show_batch_format`と同じく、先頭エントリより前に構造行（空行・種別見出し・target_repo見出し）だけを許す。
function isShowFormatCandidate(content) {
  const lines = content.replace(/\r\n?/g, '\n').split('\n');
  const first = lines.findIndex((line, index) => /^### \S+\.md(?: \[[^\]]*\])?$/.test(line) && lines[index + 1] === '---');
  return first >= 0 && lines.slice(0, first).every(line => !line || /^# (?:awi|uwi)$/.test(line) || /^## target_repo: .*$/.test(line));
}

function choiceValues() {
  return byId('create-choices').value.split(/\r?\n/).map(item => item.trim()).filter(Boolean);
}

function updateCreateChoices() {
  if (choiceValues().length >= 2) setFieldError(byId('create-choices'), byId('create-choices-error'), '');
}

function openCreateDialog(origin = null) {
  resetCreateForm();
  clearDialogMessages('create');
  openDialog(byId('create-dialog'), origin || document.activeElement, byId('create-content'));
}

const IGNORED_SINGLE_FIELD_LABELS = {
  target_repo: 'target-repo',
  scope: '確認範囲',
  question_type: '回答形式',
  choices: '選択肢',
};

function createResultMessage(isBatch, result) {
  const filenames = Array.isArray(result.filenames) ? result.filenames : [];
  const warnings = Array.isArray(result.warnings) ? result.warnings : [];
  const renamed = Object.entries(result.mapping || {}).filter(([original, saved]) => original !== saved);
  // 種別awi・uwiのまま送ったshow形式の本文をサーバーが一括登録として取り込んだ場合、応答の`batch`が真になる。
  const autoBatch = !isBatch && result.batch === true;
  const ignored = Array.isArray(result.ignored_fields) ? result.ignored_fields : [];
  const summary = isBatch || autoBatch
    ? (autoBatch ? 'show形式の本文のため一括登録として取り込みました。' : '') +
      `${filenames.length}件を取り込みました。` +
      (renamed.length ? `改名: ${renamed.map(([original, saved]) => `${original} -> ${saved}`).join('、')}` : '') +
      (ignored.length
        ? ` 使わなかった入力欄: ${ignored.map(name => IGNORED_SINGLE_FIELD_LABELS[name] || name).join('、')}`
        : '')
    : (filenames[0] ? `${filenames[0]}を追加しました。` : '項目を追加しました。');
  return warnings.length ? diagnostic('WI', SERVE_HOST, `${WI_ROOT} / 項目登録`, `${summary} 警告: ${warnings.join('、')}`) : summary;
}

async function createEntry(event) {
  event.preventDefault();
  const type = byId('create-kind').value;
  const isBatch = type === 'batch';
  // 一括登録は原文保持のため、送信値へtrimを適用せず入力の生テキストをそのまま送る。
  const rawContent = byId('create-content').value;
  const message = rawContent.trim();
  const targetRepo = byId('create-target').value.trim();
  const choices = choiceValues();
  // show形式の候補は単件用の選択肢検証を保留し、保存形式の確定と本文検証をサーバーへ渡す。
  const showFormatHint = isShowFormatCandidate(rawContent);
  setFieldError(
    byId('create-content'), byId('create-content-error'),
    message ? '' : (isBatch ? 'show形式テキストを入力してください。' : '本文を入力してください。')
  );
  const choiceInvalid = !showFormatHint && type === 'uwi' && byId('create-question-type').value === 'choice' && choices.length < 2;
  setFieldError(byId('create-choices'), byId('create-choices-error'), choiceInvalid ? '選択肢を2件以上入力してください。' : '');
  if (firstInvalid([byId('create-content'), byId('create-choices')])) return;
  // 単件でも未trimの入力を添え、show形式の本文ならサーバーが原文のまま一括登録へ切り替える。
  const payload = isBatch ? {text: rawContent} : {type, messages: [message], raw_text: rawContent};
  if (!isBatch) {
    if (targetRepo) payload.target_repo = targetRepo;
    if (type === 'uwi') {
      const scope = byId('create-scope').value.trim();
      if (scope) payload.scope = scope;
      payload.question_type = byId('create-question-type').value;
      if (payload.question_type === 'choice') payload.choices = choices;
    }
  }
  clearDialogMessages('create');
  // 所要は追加ボタンから、ダイアログを閉じた後の一覧の再描画と結果の表示までを1件の操作として残す。
  const operation = beginOperation('wi', 'mutation:create', {trigger: 'click'});
  let outcome = 'ok';
  try {
    const result = await (runPending('create', {
      container: byId('create-form'), button: byId('create-submit-button'), busyLabel: '追加中'
    }, () => api(isBatch ? '/api/entries/batch' : '/api/entries', {
      method: 'POST', body: JSON.stringify(payload), timing: operation.timing
    })));
    closeDialog(byId('create-dialog'));
    await (clearFilters({timing: operation.timing}));
    deliverOperationMessage(createResultMessage(isBatch, result));
  } catch (error) {
    if (error.payload?.code === 'sync_incomplete') {
      outcome = 'sync-incomplete';
      await clearFilters({timing: operation.timing});
      deliverOperationMessage(error.message, true);
      return;
    }
    outcome = 'error';
    deliverOperationMessage(`項目を追加できませんでした。 ${error.message}`, true);
    if (byId('create-dialog').open) byId('create-content').focus();
  } finally {
    operation.end(outcome);
  }
}

function openDeleteDialog() {
  if (!currentEntry) return;
  deleteDialogEntrySnapshot = deleteEntrySnapshot(currentEntry);
  clearDialogMessages('delete');
  byId('delete-target').textContent = currentEntry.filename;
  byId('delete-state').textContent = entryStateText(currentEntry);
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
    await (runDetailMutation('delete', {
      container: byId('delete-form'), button: byId('delete-submit-button'), busyLabel: '削除中'
    }, timing => api('/api/entries/remove', {method: 'POST', body: JSON.stringify(payload), timing}), `${key}を削除しました。`));
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
  cancelSearchTimer();
  const operation = beginUserListOperation();
  // 所要は「今すぐ同期」の押下から、同期後の一覧の再描画までを1件の操作として残す。
  const measured = beginOperation('wi', 'sync', {trigger: 'click'});
  const timing = measured.timing;
  let syncFailed = false;
  try {
    await (runPending('sync', {
      container: document.querySelector('.app-header'), button: byId('refresh-button'), busyLabel: '同期中'
    }, async () => {
      let syncFailure = null;
      try {
        await api('/api/sync', {method: 'POST', body: JSON.stringify(payload), timing});
      } catch (error) {
        syncFailure = error;
        syncFailed = true;
      }
      await loadTargetRepos({timing});
      if (!isCurrentUserListOperation(operation)) return;
      await loadEntries({announce: true, timing});
      if (!isCurrentUserListOperation(operation)) return;
      if (syncFailure) setGlobalError(`Git同期に失敗しました。ローカル内容を表示中です。 ${syncFailure.message}`);
    }));
  } finally {
    endUserListOperation(operation);
    measured.end(syncFailed ? 'error' : 'ok');
  }
}

async function handleFilterChange({reloadRepos = false, mode = 'search'} = {}) {
  cancelSearchTimer();
  const operation = beginUserListOperation();
  setDisplayMode(mode);
  currentPage = 1;
  pagination.page = 1;
  try {
    if (reloadRepos) {
      await Promise.all([loadEntries({announce: true}), loadTargetRepos()]);
      return;
    }
    await (loadEntries({announce: true}));
  } finally {
    endUserListOperation(operation);
  }
}

let externalReloadRunning = null;
let externalReloadQueued = null;

// 変更通知・再接続・表示復帰による再読込を1件ずつ実行し、実行中に届いた要求は1回にまとめて後で実行する。
// 通知が続く間に同じ再取得を重ねて参照の実行枠を占め、ユーザーの操作を待たせないためである。
// 所要は契機の受信から再読込の表示完了までを1件の操作として残し、まとめた要求は最初の受信時刻から測る。
function reloadFromExternalChange(trigger = 'resync', event = null) {
  const request = {trigger, notify_delay_ms: event ? notifyDelayMs(event) : undefined, receivedAt: performance.now()};
  if (externalReloadRunning) {
    if (externalReloadQueued) externalReloadQueued.trigger = request.trigger;
    else externalReloadQueued = request;
    return externalReloadRunning;
  }
  externalReloadRunning = (async () => {
    let next = request;
    while (next) {
      externalReloadQueued = null;
      const operation = beginOperation('wi', 'external-reload', {
        startedAt: next.receivedAt, trigger: next.trigger,
        ...(Number.isFinite(next.notify_delay_ms) ? {notify_delay_ms: next.notify_delay_ms} : {})
      });
      let outcome = 'ok';
      try {
        await reloadFromExternalChangeOnce(operation.timing);
      } catch (error) {
        outcome = 'error';
        throw error;
      } finally {
        operation.end(outcome);
      }
      next = externalReloadQueued;
    }
  })().finally(() => {
    externalReloadRunning = null;
  });
  return externalReloadRunning;
}

async function reloadFromExternalChangeOnce(timing = {}) {
  // 通知用の既知集合の更新は表示に関わらないため待たず、取得の記録だけを同じ操作へ対応付ける。
  void refreshKnownEntries({notify: true, timing}).catch((error) => {
    setGlobalError(error.message);
  });
  if (userListOperationPending) {
    externalListReloadPending = true;
    return;
  }
  await (loadTargetRepos({timing}));
  if (userListOperationPending) {
    externalListReloadPending = true;
    return;
  }
  await (loadEntries({announce: false, showLoading: false, timing}));
  await (reloadOpenDetailFromExternalChange({timing}));
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

function handleFocusIn(event) {
  if (!byId('operation-notice').contains(event.target)) lastFocusedElement = event.target;
  refreshFocusRequested = false;
}

function handleSearchInput(event) {
  cancelSearchTimer();
  const operation = beginUserListOperation();
  if (searchComposing || event?.isComposing) return;
  setDisplayMode('search');
  currentPage = 1;
  pagination.page = 1;
  searchTimer = setTimeout(() => {
    searchTimer = null;
    void loadEntries({announce: true}).finally(() => endUserListOperation(operation));
  }, 250);
}

function bindEvents() {
  document.addEventListener('focusin', handleFocusIn);
  globalThis.addEventListener?.('popstate', () => { void restoreEntryFromUrl(); });
  byId('operation-notice-close-button').addEventListener('click', closeOperationNotice);
  byId('previous-page-button').addEventListener('click', () => { void movePage(-1); });
  byId('copy-all-button').addEventListener('click', () => { void copyAllEntries(); });
  byId('next-page-button').addEventListener('click', () => { void movePage(1); });
  byId('refresh-button').addEventListener('click', synchronizeAndLoad);
  byId('notification-button').addEventListener('click', () => { void enableNotifications(); });
  byId('create-button').addEventListener('click', event => openCreateDialog(event.currentTarget));
  byId('empty-create-button').addEventListener('click', event => openCreateDialog(event.currentTarget));
  byId('clear-filters-button').addEventListener('click', () => clearFilters());
  byId('empty-clear-button').addEventListener('click', () => clearFilters());
  byId('wi-mode-normal').addEventListener('change', () => {
    void handleFilterChange({mode: 'normal'});
  });
  byId('wi-mode-search').addEventListener('change', () => { void handleFilterChange(); });
  byId('wi-mode-waiting').addEventListener('change', () => {
    void handleFilterChange({mode: 'waiting', reloadRepos: true});
  });
  byId('waiting-target-filter').addEventListener('change', () => { void handleFilterChange({mode: 'waiting'}); });
  byId('empty-all-states-button').addEventListener('click', () => {
    byId('state-filter').value = 'all';
    handleFilterChange({reloadRepos: true});
  });
  byId('empty-all-periods-button').addEventListener('click', () => {
    byId('period-filter').value = 'all';
    handleFilterChange();
  });
  byId('kind-filter').addEventListener('change', () => { void handleFilterChange(); });
  byId('state-filter').addEventListener('change', () => { void handleFilterChange({reloadRepos: true}); });
  byId('period-filter').addEventListener('change', () => { void handleFilterChange(); });
  byId('answer-filter').addEventListener('change', () => { void handleFilterChange(); });
  byId('important-filter').addEventListener('change', () => { void handleFilterChange(); });
  byId('target-filter').addEventListener('change', () => { void handleFilterChange(); });
  byId('source-filter').addEventListener('change', () => { void handleFilterChange(); });
  byId('search-input').addEventListener('compositionstart', () => {
    searchComposing = true;
    cancelSearchTimer();
    beginUserListOperation();
  });
  byId('search-input').addEventListener('compositionend', () => {
    searchComposing = false;
    handleSearchInput();
  });
  byId('search-input').addEventListener('input', handleSearchInput);
  byId('edit-button').addEventListener('click', enterEdit);
  byId('important-button').addEventListener('click', () => { void toggleImportant(); });
  byId('user-comment-button').addEventListener('click', enterUserComment);
  byId('save-entry-button').addEventListener('click', saveEntry);
  byId('save-answer-button').addEventListener('click', saveAnswer);
  byId('save-next-answer-button').addEventListener('click', () => { void saveAnswer({next: true}); });
  byId('skip-answer-button').addEventListener('click', () => { void skipWaitingAnswer(); });
  byId('save-user-comment-button').addEventListener('click', saveUserComment);
  byId('adopt-button').addEventListener('click', () => enterDecision('adopt'));
  byId('reject-button').addEventListener('click', () => enterDecision('reject'));
  byId('confirm-adopt-button').addEventListener('click', () => { void transitionDetail('adopt'); });
  byId('confirm-reject-button').addEventListener('click', () => { void transitionDetail('reject'); });
  byId('hold-button').addEventListener('click', () => { void transitionDetail('hold'); });
  byId('unhold-button').addEventListener('click', () => { void transitionDetail('unhold'); });
  byId('return-to-inbox-button').addEventListener('click', () => { void transitionDetail('return-to-inbox'); });
  byId('delete-button').addEventListener('click', openDeleteDialog);
  byId('create-kind').addEventListener('change', updateCreateFields);
  byId('create-question-type').addEventListener('change', updateCreateFields);
  byId('create-content').addEventListener('input', updateCreateFields);
  byId('create-choices').addEventListener('input', updateCreateChoices);
  byId('create-form').addEventListener('submit', createEntry);
  byId('delete-form').addEventListener('submit', deleteEntry);
  attachDialogCloseHandlers('detail-dialog', 'detail-close-button', closeDetailDialog);
  attachDialogCloseHandlers('create-dialog', 'create-close-button', () => closeDialog(byId('create-dialog')));
  attachDialogCloseHandlers('delete-dialog', 'delete-close-button', closeDeleteDialog);
}

let initialization = Promise.resolve();
// 初期取得はサーバーが購読を登録した合図（`subscribed`）の後に始める。登録後の変更は通知で届くため、
// 最初の接続では全件を読み直さない。初期取得を始めた後に登録された接続と再接続では、その間の変更を
// 取りこぼさないよう読み直す。
const INITIAL_SUBSCRIBE_WAIT_MS = 300;
let initialLoadStarted = false;
let resolveFirstSubscription = () => {};
const firstSubscription = new Promise(resolve => { resolveFirstSubscription = resolve; });

// 無通信の検知、再接続とbfcacheの前後の接続の開閉は共通モジュールが担う。
function subscribeEvents() {
  return connectEvents('/api/events', {
    open: () => {
      if (byId('connection-status').dataset.syncFailed !== 'true') {
        byId('connection-status').hidden = true;
        byId('connection-status').textContent = '';
      }
    },
    subscribed: () => {
      if (!initialLoadStarted) {
        resolveFirstSubscription();
        void firstSubscription.then(() => initialization).then(() => {
          byId('connection-status').dataset.connected = 'true';
        });
        return;
      }
      void initialization.then(async () => {
        await reloadFromExternalChange('reconnect');
        byId('connection-status').dataset.connected = 'true';
      });
    },
    error: () => {
      byId('connection-status').dataset.connected = 'false';
      if (byId('connection-status').dataset.syncFailed === 'true') return;
      byId('connection-status').textContent = diagnostic('WI', SERVE_HOST, `${WI_ROOT} / 自動更新`, '自動更新を再接続中');
      byId('connection-status').hidden = false;
    },
    changed: (event) => {
      void initialization.then(() => reloadFromExternalChange('sse', event));
    },
    'sync-error': () => {
      const status = byId('connection-status');
      status.dataset.syncFailed = 'true';
      status.textContent = diagnostic('WI', SERVE_HOST, `${WI_ROOT} / Git同期`, 'Git同期に失敗しました。自動で再試行します。今すぐ同期も利用できます。');
      status.hidden = false;
    },
    'sync-ok': () => {
      const status = byId('connection-status');
      status.dataset.syncFailed = 'false';
      status.textContent = '';
      status.hidden = true;
    },
  });
}

function initializeApp() {
  subscribeEvents();
  syncNotificationButton();
  // 購読を確立できない場合も初期表示を遅らせすぎないよう、待つのは短い上限までとする。
  const subscribed = Promise.race([firstSubscription, new Promise(resolve => setTimeout(resolve, INITIAL_SUBSCRIBE_WAIT_MS))]);
  initialization = subscribed
    .then(() => {
      initialLoadStarted = true;
      return Promise.all([loadEntries(), loadTargetRepos()]);
    })
    .then(() => refreshKnownEntries({notify: false}))
    .catch((error) => {
      setGlobalError(error.message);
    });
}

async function init() {
  bindEvents();
  resyncWhenVisible(() => {
    refreshCooldownDisplays();
    return initialization.then(() => reloadFromExternalChange());
  });
  if (window.matchMedia('(max-width: 700px)').matches) {
    document.querySelector('#screen-wi .filters details').open = false;
  }
  document.addEventListener('focusin', handleFocusIn);
  initializeApp();
  await initialization;
  // 初期取得中のユーザー操作で変わったURLを復元すると、回答保存中の詳細を再選択してしまう。
  if (detailSessionGeneration === 0) await restoreEntryFromUrl();
}

registerScreen('wi', {init});
