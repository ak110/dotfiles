const BASE_PATH=__BASE_PATH_JS__;
// エラー表示は既存のError契約に合わせ、error.messageを直接参照する。
const KIND_LABELS = {feedback: 'フィードバック', tbd: 'TBD', unknown: '種別不明'};
const STATE_LABELS = {inbox: '未処理', processing: '処理中', adopted: '採用済み', rejected: '不採用'};
const ACTIVE_STATES = new Set(['inbox', 'processing']);
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
let toastTimer = null;
let knownTbdBaselineReady = false;
const knownTbdFilenames = new Set();
const pendingOperations = new Set();
const dialogOrigins = new Map();
const dialogStack = [];

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

function clearDialogMessages(dialogName) {
  setTextMessage(`${dialogName}-alert`, '');
  setTextMessage(`${dialogName}-status`, '');
}

function showToast(message) {
  const toast = byId('toast');
  toast.textContent = message;
  toast.hidden = false;
  if (toastTimer !== null) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, 4000);
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
  if (dialog) {
    const name = dialog.id.replace('-dialog', '');
    setTextMessage(`${name}-${isError ? 'alert' : 'status'}`, message);
    return;
  }
  if (isError) setTextMessage('global-error', message);
  else showToast(message);
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

function renderEntry(entry) {
  const item = document.createElement('li');
  item.className = 'entry-row';
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'entry-select';
  button.dataset.key = entryKey(entry);
  button.dataset.kind = entry.kind || 'unknown';
  const unanswered = entry.kind === 'tbd' && entry.answered === false;
  button.dataset.unansweredTbd = String(unanswered);
  button.setAttribute('aria-current', String(entryKey(currentEntry) === entryKey(entry)));

  appendTextCell(button, 'ファイル名', 'filename-cell', entry.filename);
  appendTextCell(button, '対象リポジトリ', 'target-repo-cell', entry.target_repo);
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
    byId('source-filter').value !== '' ||
    byId('source-empty-filter').checked;
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

function renderList(warnings = [], announce = false) {
  const list = byId('entry-list');
  list.replaceChildren(...entries.map(renderEntry));
  const unanswered = entries.filter(entry => entry.kind === 'tbd' && entry.answered === false).length;
  byId('entry-count').textContent = `${entries.length}件（未回答TBD ${unanswered}件）`;
  renderWarnings(warnings);
  renderEmptyState();
  if (announce) {
    byId('result-status').textContent = entries.length ? `${entries.length}件を表示` : '一致する項目はありません';
  }
}

function buildQuery() {
  const parameters = new URLSearchParams();
  parameters.set('type', byId('kind-filter').value);
  parameters.set('status', byId('state-filter').value);
  parameters.set('answered', byId('answer-filter').value);
  const values = {
    target_repo: byId('target-filter').value,
    source: byId('source-filter').value.trim(),
    q: byId('search-input').value.trim()
  };
  Object.entries(values).forEach(([name, value]) => { if (value) parameters.set(name, value); });
  if (byId('source-empty-filter').checked) parameters.set('source_empty', 'true');
  return parameters;
}

function setListLoading(value) {
  pendingListRequests += value ? 1 : -1;
  pendingListRequests = Math.max(0, pendingListRequests);
  const loading = pendingListRequests > 0;
  byId('loading-indicator').hidden = !loading;
  byId('entry-list').setAttribute('aria-busy', String(loading));
}

async function loadEntries({announce = false} = {}) {
  pendingListAnnouncement = pendingListAnnouncement || announce;
  const generation = ++listRequestGeneration;
  setListLoading(true);
  try {
    const payload = await api(`/api/entries?${buildQuery().toString()}`);
    if (generation !== listRequestGeneration) return entries;
    entries = Array.isArray(payload.entries) ? payload.entries : [];
    const selected = entries.find(item => entryKey(item) === entryKey(currentEntry));
    if (selected) currentEntry = {...currentEntry, ...selected};
    const shouldAnnounce = pendingListAnnouncement;
    pendingListAnnouncement = false;
    renderList(Array.isArray(payload.warnings) ? payload.warnings : [], shouldAnnounce);
    return entries;
  } catch (error) {
    if (generation === listRequestGeneration) {
      pendingListAnnouncement = false;
      setTextMessage('global-error', error.message);
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

async function refreshKnownTbds({notify = false} = {}) {
  const payload = await api('/api/entries?type=tbd&status=all&answered=all');
  const allTbds = Array.isArray(payload.entries) ? payload.entries : [];
  const newUnanswered = knownTbdBaselineReady && notify ? allTbds.filter(entry =>
    !knownTbdFilenames.has(entry.filename) && ACTIVE_STATES.has(entry.state) && entry.answered === false
  ) : [];
  allTbds.forEach(entry => knownTbdFilenames.add(entry.filename));
  knownTbdBaselineReady = true;
  if (newUnanswered.length && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
    const filenames = newUnanswered.map(entry => entry.filename);
    new Notification('新規未回答TBD', {
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
    if (isCurrent) setTextMessage('global-error', error.message);
    return isCurrent;
  }
}

function syncFilterDependencies() {
  const feedbackOnly = byId('kind-filter').value === 'feedback';
  if (feedbackOnly) byId('answer-filter').value = 'all';
  byId('answer-filter').disabled = feedbackOnly;
  const sourceEmpty = byId('source-empty-filter').checked;
  if (sourceEmpty) byId('source-filter').value = '';
  byId('source-filter').disabled = sourceEmpty;
}

async function clearFilters({load = true} = {}) {
  byId('search-input').value = '';
  byId('kind-filter').value = 'all';
  byId('state-filter').value = 'active';
  byId('answer-filter').value = 'all';
  byId('target-filter').value = '';
  byId('source-filter').value = '';
  byId('source-empty-filter').checked = false;
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
  const frontmatter = entry.frontmatter && typeof entry.frontmatter === 'object' && !Array.isArray(entry.frontmatter)
    ? {...entry.frontmatter} : {};
  for (const key of ['target_repo', 'source']) {
    if (!(key in frontmatter) && entry[key] !== undefined && entry[key] !== null) frontmatter[key] = entry[key];
  }
  for (const [key, value] of Object.entries(frontmatter)) {
    if (FRONTMATTER_EXCLUDED_KEYS.has(key)) continue;
    const label = Object.prototype.hasOwnProperty.call(FRONTMATTER_LABELS, key) ? FRONTMATTER_LABELS[key] : key;
    appendMetadataItem(metadata, label, value);
  }
  const parts = formatDateParts(entry.updated_at);
  appendMetadataItem(metadata, '更新日時', parts.time ? `${parts.date} ${parts.time}` : parts.date);
}

function setDetailMode(mode) {
  const editing = mode === 'edit';
  const answering = mode === 'answer';
  const unansweredTbd = currentEntry?.kind === 'tbd' && currentEntry.answered === false;
  byId('edit-panel').hidden = !editing;
  byId('answer-panel').hidden = !answering;
  byId('edit-button').hidden = editing || answering || !currentEntry || !ACTIVE_STATES.has(currentEntry.state);
  byId('answer-button').hidden = editing || answering || !currentEntry ||
    currentEntry.kind !== 'tbd' || !ACTIVE_STATES.has(currentEntry.state);
  byId('answer-button').textContent = currentEntry?.answered === true ? '回答を変更' : '回答';
  byId('delete-button').hidden = editing || answering || !currentEntry || !ACTIVE_STATES.has(currentEntry.state);
  byId('save-entry-button').hidden = !editing;
  byId('save-answer-button').hidden = !answering;
  syncDetailMutationAvailability();
  byId('edit-button').className = unansweredTbd ? 'button-secondary' : 'button-primary';
  if (!editing) setFieldError(byId('edit-content'), byId('edit-content-error'), '');
  if (!answering) setFieldError(byId('answer-input'), byId('answer-input-error'), '');
}

function syncDetailMutationAvailability() {
  for (const id of ['edit-button', 'answer-button', 'delete-button', 'save-entry-button', 'save-answer-button']) {
    const button = byId(id);
    button.disabled = !button.hidden && detailRefreshRequired;
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
  setTextMessage('detail-alert', '');
  byId('detail-view').hidden = false;
  byId('detail-filename').textContent = entry.filename;
  byId('detail-state').textContent = `${entry.kind || 'unknown'} / ${entry.state || 'unknown'}`;
  byId('detail-state').dataset.state = entry.state;
  byId('detail-content').innerHTML = entry.body_html ?? entry.content_html ?? '';
  renderMetadata(entry);
  renderAnswerChoices(entry);
  byId('readonly-notice').hidden = ACTIVE_STATES.has(entry.state);
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
    if (requestGeneration === detailRequestGeneration) setTextMessage('global-error', error.message);
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
  return 'view';
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
      setTextMessage('global-error', `${filename}の移動先を一意に特定できません。詳細を開き直してください。`);
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
  if (currentDetailMode() !== 'view') {
    currentEntry = {...currentEntry, state: resolvedEntry.state};
    detailOriginKey = entryKey(currentEntry);
    if (detailChanged) {
      detailRefreshRequired = true;
      setTextMessage(
        'detail-alert',
        '外部で項目が更新されました。入力を保持しています。詳細を閉じて開き直してから保存してください。'
      );
      setDetailMode(currentDetailMode());
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
    if (byId('detail-dialog').open && entryKey(currentEntry) === key &&
        sessionGeneration === detailSessionGeneration) {
      const refreshed = await api(`/api/entries/${encodeURIComponent(currentEntry.state)}/${encodeURIComponent(currentEntry.filename)}`);
      if (byId('detail-dialog').open && entryKey(currentEntry) === key &&
          sessionGeneration === detailSessionGeneration) {
        displayEntry(refreshed.entry);
        byId('detail-dialog-body').focus();
      }
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

function resetCreateForm() {
  byId('create-kind').value = 'feedback';
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
  const isTbd = kind === 'tbd';
  const isChoice = isTbd && byId('create-question-type').value === 'choice';
  byId('tbd-fields').hidden = !isTbd;
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
  const choiceInvalid = type === 'tbd' && byId('create-question-type').value === 'choice' && choiceValues.length < 2;
  setFieldError(byId('create-choices'), byId('create-choices-error'), choiceInvalid ? '選択肢を2件以上入力してください。' : '');
  if (firstInvalid([byId('create-content'), byId('create-choices')])) return;
  const payload = isBatch ? {text: rawContent} : {type, messages: [message]};
  if (!isBatch) {
    if (targetRepo) payload.target_repo = targetRepo;
    const source = byId('create-source').value.trim();
    if (source) payload.source = source;
    if (type === 'tbd') {
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
    if (!isBatch) {
      const created = entries.find(entry => entry.filename === result.filenames?.[0]);
      if (created) await selectEntry(created, byId('create-button'));
    }
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
  syncFilterDependencies();
  const requestedState = byId('state-filter').value;
  if (reloadRepos && !await loadTargetRepos() && byId('state-filter').value !== requestedState) return;
  await loadEntries({announce: true});
}

async function reloadFromExternalChange() {
  void refreshKnownTbds({notify: true}).catch((error) => {
    setTextMessage('global-error', error.message);
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

function bindEvents() {
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
  byId('source-empty-filter').addEventListener('change', () => { void handleFilterChange(); });
  byId('search-input').addEventListener('input', () => {
    if (searchTimer !== null) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadEntries({announce: true}), 250);
  });
  byId('edit-button').addEventListener('click', enterEdit);
  byId('answer-button').addEventListener('click', enterAnswer);
  byId('save-entry-button').addEventListener('click', saveEntry);
  byId('save-answer-button').addEventListener('click', saveAnswer);
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

function initializeApp() {
  const eventSource = new EventSource(BASE_PATH + '/api/events');
  eventSource.addEventListener('open', () => { byId('connection-status').textContent = '自動更新に接続済み'; });
  eventSource.addEventListener('error', () => { byId('connection-status').textContent = '自動更新を再接続中'; });
  eventSource.addEventListener('changed', () => { void initialization.then(reloadFromExternalChange); });
  syncFilterDependencies();
  syncNotificationButton();
  initialization = synchronizeAndLoad()
    .then(() => refreshKnownTbds({notify: false}))
    .catch((error) => {
      setTextMessage('global-error', error.message);
    });
}

bindEvents();
initializeApp();
