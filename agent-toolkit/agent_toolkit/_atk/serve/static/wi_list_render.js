// WI一覧の行・状態・条件の描画と検索パラメーター。
import {
  KIND_LABELS, STATE_LABELS, TARGET_REPO_DISPLAY_LENGTH, byId, entryKey, isPlainPrimaryClick, wiState
} from "./wi_state.js";
import {
  BASE_PATH, SERVE_HOST, WI_ROOT, diagnostic, renderList, renderWarnings
} from "./common.js";
import {
  entryButtonForKey, selectEntry
} from "./wi_detail.js";
import {
  api, runPending, setGlobalError, setTextMessage, withNextAction
} from "./wi_feedback.js";
import {
  renderPagination
} from "./wi_list.js";

export function formatDateParts(value) {
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
export function stateLabel(state) { return STATE_LABELS[state] || state || '不明'; }
// 反映後の観測だけが残るinboxのawiは、保存状態をinboxのまま、表示だけをneeds-verifyにする。
// inboxの回答済みuwiは表示だけをansweredにし、色は保存状態inboxのバッジと同じにする。
// 状態フィルター・件数・操作の可否は保存状態で判定する。
function displayStateLabel(entry) {
  if (entry.state === 'inbox' && Date.parse(entry.cooldown_until) > Date.now()) return 'cooldown';
  if (entry.state === 'inbox' && entry.kind === 'uwi' && entry.answered === true) return 'answered';
  return entry.needs_verify === true ? 'needs-verify' : STATE_LABELS[entry.state] || 'unknown';
}
export function entryStateText(entry) {
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
export function refreshCooldownDisplays() {
  clearTimeout(wiState.cooldownTimer);
  for (const {entry, button, badge} of wiState.entryStateDisplays.values()) updateEntryStateDisplay(entry, button, badge);
  if (wiState.currentEntry) {
    byId('detail-state').textContent = entryStateText(wiState.currentEntry);
    if (byId('delete-dialog').open) byId('delete-state').textContent = entryStateText(wiState.currentEntry);
  }
  const remaining = [...wiState.entries, ...(wiState.currentEntry ? [wiState.currentEntry] : [])]
    .filter(entry => entry.state === 'inbox')
    .map(entry => Date.parse(entry.cooldown_until) - Date.now()).filter(value => value > 0);
  if (remaining.length) {
    // setTimeoutが扱える遅延の範囲で予約し、長い期限は次の予約で再判定する。
    wiState.cooldownTimer = setTimeout(refreshCooldownDisplays, Math.min(...remaining, 2147483647));
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

export function entryPageUrl(entry) {
  const query = new URLSearchParams({filename: entry.filename});
  return `${BASE_PATH}/?${query.toString()}`;
}

export function entrySelectionFromUrl() {
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
  button.setAttribute('aria-current', String(entryKey(wiState.currentEntry) === entryKey(entry)));

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
  wiState.entryStateDisplays.set(entryKey(entry), {entry, button, badge});
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
  empty.hidden = wiState.entries.length !== 0;
  clear.hidden = true;
  allStates.hidden = true;
  allPeriods.hidden = true;
  create.hidden = true;
  if (wiState.entries.length) return;
  if (wiState.displayMode === 'waiting') {
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
export function renderEntries(warnings = [], announce = false) {
  const list = byId('entry-list');
  const focusedKey = document.activeElement?.classList?.contains('entry-select')
    ? document.activeElement.dataset.key : null;
  wiState.entryStateDisplays.clear();
  renderList(list, wiState.entries, entryKey, entry => renderEntry(entry));
  refreshCooldownDisplays();
  if (focusedKey) {
    (entryButtonForKey(focusedKey) || list.querySelector('.entry-select') || byId('empty-clear-button')).focus();
  }
  const unanswered = wiState.entries.filter(entry => entry.kind === 'uwi' && entry.answered === false).length;
  byId('entry-count').textContent = `${wiState.entries.length}件（未回答UWI ${unanswered}件）`;
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
    byId('result-status').textContent = wiState.entries.length ? `${wiState.entries.length}件を表示` : '一致する項目はありません';
  }
}

export function buildQuery(page = wiState.currentPage) {
  const parameters = new URLSearchParams();
  if (wiState.displayMode === 'waiting') {
    parameters.set('view', 'waiting');
    parameters.set('page', String(page));
    if (byId('waiting-target-filter').value) parameters.set('target_repo', byId('waiting-target-filter').value);
    return parameters;
  }
  if (wiState.displayMode === 'normal') {
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

export async function copyAllEntries() {
  if (wiState.pendingOperations.has('copy-all')) return;
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
  if (wiState.displayMode === 'waiting') {
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
  byId('wi-effective-conditions').textContent = `${wiState.displayMode === 'normal' ? '通常表示' : '条件検索'}: ${labels.join('、')}`;
}

export function setDisplayMode(mode) {
  wiState.displayMode = mode;
  byId('wi-mode-normal').checked = mode === 'normal';
  byId('wi-mode-search').checked = mode === 'search';
  byId('wi-mode-waiting').checked = mode === 'waiting';
  byId('waiting-filters').hidden = mode !== 'waiting';
  renderEffectiveConditions();
}
