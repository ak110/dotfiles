// WI詳細の表示・選択と外部更新時の入力保護。
import {
  DELETABLE_STATES, EDITABLE_STATES, FRONTMATTER_EXCLUDED_KEYS, MUTABLE_STATES, PROCESSABLE_STATES,
  TERMINAL_STATES, byId, deleteEntrySnapshot, entryKey, wiState
} from "./wi_state.js";
import {
  entryPageUrl, entrySelectionFromUrl, entryStateText, formatDateParts, refreshCooldownDisplays, stateLabel
} from "./wi_list_render.js";
import {
  renderWaitingProgress
} from "./wi_waiting.js";
import {
  api, clearDialogMessages, closeDialog, deliverOperationMessage, openDetailDialog, setFieldError, setGlobalError,
  setTextMessage
} from "./wi_feedback.js";
import {
  reloadFromExternalChange
} from "./wi.js";
import {
  loadEntries, renderListLoading
} from "./wi_list.js";
import {
  BASE_PATH
} from "./common.js";

function updateCurrentRowSelection() {
  document.querySelectorAll('.entry-select').forEach(button => {
    button.setAttribute('aria-current', String(button.dataset.key === entryKey(wiState.currentEntry)));
  });
}

export function entryButtonForKey(key) {
  return Array.from(document.querySelectorAll('.entry-select')).find(button => button.dataset.key === key) || null;
}

function detailReturnTarget() {
  const currentOrigin = entryButtonForKey(wiState.detailOriginKey);
  if (currentOrigin) return currentOrigin;
  const original = wiState.dialogOrigins.get('detail-dialog');
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

export function setDetailMode(mode) {
  const editing = mode === 'edit';
  const commenting = mode === 'user-comment';
  const decisionAction = mode === 'decide-adopt' ? 'adopt' : mode === 'decide-reject' ? 'reject' : '';
  const mutating = editing || commenting || decisionAction !== '';
  const unansweredUwi = wiState.currentEntry?.kind === 'uwi' && wiState.currentEntry.answered === false;
  const processable = wiState.currentEntry && PROCESSABLE_STATES.has(wiState.currentEntry.state);
  const mutable = wiState.currentEntry && MUTABLE_STATES.has(wiState.currentEntry.state);
  const deletable = wiState.currentEntry && DELETABLE_STATES.has(wiState.currentEntry.state);
  const held = wiState.currentEntry?.state === 'hold';
  const terminal = wiState.currentEntry && TERMINAL_STATES.has(wiState.currentEntry.state);
  byId('edit-panel').hidden = !editing;
  // 回答できるUWIは閲覧中から回答欄を表示し、回答と他の操作を切り替えなしで行えるようにする。
  const answering = !mutating && wiState.currentEntry?.kind === 'uwi' && (Boolean(mutable) || wiState.displayMode === 'waiting');
  byId('answer-panel').hidden = !answering;
  const waiting = wiState.displayMode === 'waiting' && answering;
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
  byId('edit-button').hidden = mutating || !wiState.currentEntry || !EDITABLE_STATES.has(wiState.currentEntry.state);
  byId('important-button').hidden = mutating || !wiState.currentEntry;
  // 確認状態は確認状態を持つ未終了のAWIだけが切り替えられ、ボタンの文言で次の操作を示す。
  byId('important-button').textContent = wiState.currentEntry?.important === true ? '重要解除' : '重要設定';
  byId('user-comment-button').hidden = mutating || wiState.currentEntry?.user_comment_editable !== true;
  byId('adopt-button').hidden = mutating || !mutable;
  byId('reject-button').hidden = mutating || !mutable || wiState.currentEntry.kind !== 'awi';
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

export function syncDetailMutationAvailability() {
  for (const id of [
    'edit-button', 'user-comment-button', 'delete-button', 'important-button',
    'save-entry-button', 'save-answer-button', 'save-next-answer-button', 'skip-answer-button', 'save-user-comment-button',
    'adopt-button', 'reject-button', 'hold-button', 'unhold-button', 'return-to-inbox-button',
    'confirm-adopt-button', 'confirm-reject-button'
  ]) {
    const button = byId(id);
    const userCommentUnavailable = id === 'save-user-comment-button' && wiState.currentEntry?.user_comment_editable !== true;
    button.disabled = !button.hidden && (wiState.waitingAdvancePending || wiState.detailRefreshRequired || userCommentUnavailable ||
      id === 'save-next-answer-button' && (wiState.waitingSavedFilename === wiState.currentEntry?.filename ||
        wiState.currentEntry?.answered !== false || !MUTABLE_STATES.has(wiState.currentEntry?.state)));
  }
  // 見送りは競合した入力を明示的に破棄して次へ進むため、保存を止めた後にも使える。
  byId('skip-answer-button').disabled = wiState.waitingAdvancePending;
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

export function displayEntry(entry) {
  // 同じ項目の再表示（外部変更の反映など）では、入力途中の回答を保持する。
  const keepTypedAnswer = entryKey(wiState.currentEntry) === entryKey(entry) && answerInputChanged();
  wiState.currentEntry = entry;
  if (wiState.waitingSavedFilename !== entry.filename) wiState.waitingSavedFilename = '';
  if (!keepTypedAnswer) byId('answer-input').value = entry.answer || '';
  refreshCooldownDisplays();
  wiState.detailRefreshRequired = false;
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

export async function selectEntry(entry, origin = null, {ignoreNotFound = false} = {}) {
  const requestGeneration = ++wiState.detailRequestGeneration;
  const sessionGeneration = ++wiState.detailSessionGeneration;
  const requestIsCurrent = () => requestGeneration === wiState.detailRequestGeneration &&
    sessionGeneration === wiState.detailSessionGeneration;
  wiState.detailOrigin = origin || document.activeElement;
  wiState.detailOriginKey = entryKey(entry);
  clearDialogMessages('detail');
  wiState.detailRequestPending = true;
  renderListLoading();
  const pendingRow = origin?.closest('.entry-row');
  if (pendingRow) {
    pendingRow.setAttribute('aria-busy', 'true');
  }
  try {
    const payload = await api(`/api/entries/${encodeURIComponent(entry.filename)}`);
    if (!requestIsCurrent()) return;
    displayEntry(payload.entry);
    openDetailDialog(wiState.detailOrigin);
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
      wiState.detailRequestPending = false;
      renderListLoading();
    }
  }
}

export function closeDeleteDialog({restoreFocus = true} = {}) {
  closeDialog(byId('delete-dialog'), {restoreFocus});
  wiState.deleteDialogEntrySnapshot = '';
}

export function invalidateDeleteConfirmation() {
  if (!byId('delete-dialog').open) return false;
  closeDeleteDialog({restoreFocus: false});
  byId('detail-dialog-body').focus();
  return true;
}

const DELETE_RECONFIRM_MESSAGE =
  '外部更新により削除確認を閉じました。詳細を確認し、削除操作をやり直してください。';
export const DELETE_CONFLICT_MESSAGE =
  '外部更新により削除確認を閉じました。詳細を閉じて開き直してから削除してください。';

function reportExternalDetailFailure(error, deleteConfirmationInvalidated) {
  const invalidated = invalidateDeleteConfirmation() || deleteConfirmationInvalidated;
  const recovery = invalidated ? ` ${DELETE_RECONFIRM_MESSAGE}` : '';
  setTextMessage('detail-alert', `${error.message}${recovery}`);
}

export function closeDetailDialog({updateUrl = true, force = false} = {}) {
  const detailDialog = byId('detail-dialog');
  const deleteDialog = byId('delete-dialog');
  const hadOpenDialog = detailDialog.open || deleteDialog.open;
  if (!hadOpenDialog && !wiState.currentEntry) return;
  if (!force) {
    const mode = currentDetailMode();
    const changed = mode === 'edit' && byId('edit-content').value !== wiState.currentEntry?.content ||
      answerInputChanged() ||
      mode === 'user-comment' && byId('user-comment-input').value !== (wiState.currentEntry?.user_comment || '') ||
      mode.startsWith('decide-') && byId('decision-note').value !== '';
    if (changed && !window.confirm('変更した入力を破棄しますか？')) return;
  }
  const returnTarget = detailReturnTarget();
  wiState.detailRequestGeneration += 1;
  wiState.detailSessionGeneration += 1;
  wiState.detailRequestPending = false;
  renderListLoading();
  closeDeleteDialog({restoreFocus: false});
  closeDialog(detailDialog, {restoreFocus: false});
  wiState.currentEntry = null;
  wiState.detailOriginKey = '';
  wiState.detailRefreshRequired = false;
  setTextMessage('detail-alert', '');
  setDetailMode('view');
  updateCurrentRowSelection();
  if (updateUrl && globalThis.location?.pathname === `${BASE_PATH}/` && entrySelectionFromUrl()) {
    history.replaceState({atkEntry: false}, '', `${BASE_PATH}/`);
  }
  if (hadOpenDialog && returnTarget && typeof returnTarget.focus === 'function') returnTarget.focus();
}

export async function restoreEntryFromUrl() {
  const selection = entrySelectionFromUrl();
  if (!selection) {
    if (byId('detail-dialog').open || wiState.currentEntry) closeDetailDialog({updateUrl: false});
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

export function refreshUserCommentMode(entry, message) {
  const input = byId('user-comment-input');
  const value = input.value;
  wiState.detailOriginKey = entryKey(entry);
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
  if (!wiState.detailMutationPending) return false;
  wiState.detailReloadDeferred = true;
  return true;
}

export async function reloadOpenDetailFromExternalChange({timing = {}} = {}) {
  if (!byId('detail-dialog').open || !wiState.currentEntry) return;
  if (wiState.detailOriginKey !== entryKey(wiState.currentEntry)) return;
  if (deferDetailReloadWhileMutating()) return;
  const sessionGeneration = wiState.detailSessionGeneration;
  const filename = wiState.currentEntry.filename;
  const requestGeneration = ++wiState.detailRequestGeneration;
  // 送信の開始前に始まった再読込も、応答を待つ間に結果を反映せず保留へ回す。
  const requestIsCurrent = () => !deferDetailReloadWhileMutating() &&
    requestGeneration === wiState.detailRequestGeneration &&
    sessionGeneration === wiState.detailSessionGeneration && byId('detail-dialog').open &&
    wiState.currentEntry?.filename === filename;
  let deleteConfirmationInvalidated = false;
  if (byId('delete-dialog').open && deleteEntrySnapshot(wiState.currentEntry) !== wiState.deleteDialogEntrySnapshot) {
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
    if (wiState.displayMode === 'waiting') {
      wiState.detailRefreshRequired = true;
      syncDetailMutationAvailability();
      setTextMessage('detail-alert', '項目が削除されました。入力を保持しています。「保存せず次へ」で次の項目を確認できます。');
      return;
    }
    closeDetailDialog({force: true});
    reportDeletedEntry(filename, {reloadList: false});
    return;
  }
  const detailChanged = resolvedEntry.state !== wiState.currentEntry.state ||
    resolvedEntry.content !== wiState.currentEntry.content;
  if (byId('delete-dialog').open && deleteEntrySnapshot(resolvedEntry) !== wiState.deleteDialogEntrySnapshot) {
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
    wiState.currentEntry = {...wiState.currentEntry, state: resolvedEntry.state};
    wiState.detailOriginKey = entryKey(wiState.currentEntry);
    if (detailChanged) {
      wiState.detailRefreshRequired = true;
      setTextMessage(
        'detail-alert',
        '外部で項目が更新されました。入力を保持しています。詳細を閉じて開き直してから保存してください。'
      );
      setDetailMode(mode);
    }
    updateCurrentRowSelection();
    return;
  }
  wiState.detailOriginKey = entryKey(resolvedEntry);
  displayEntry(resolvedEntry);
  if (deleteConfirmationInvalidated) setTextMessage('detail-alert', DELETE_RECONFIRM_MESSAGE);
  else if (wiState.syncIncompleteNotice?.sessionGeneration === wiState.detailSessionGeneration &&
    wiState.syncIncompleteNotice.filename === resolvedEntry.filename) {
    setTextMessage('detail-alert', wiState.syncIncompleteNotice.message);
  }
}

export function enterEdit() {
  if (!wiState.currentEntry) return;
  byId('edit-content').value = wiState.currentEntry.content;
  setDetailMode('edit');
  byId('edit-content').focus();
}

// 編集・コメント・採否のモードでは回答欄を隠すが、入力途中の回答は保持しているため、表示状態ではなく回答できる項目かで判定する。
export function answerInputChanged() {
  const answerable = wiState.currentEntry?.kind === 'uwi' && (MUTABLE_STATES.has(wiState.currentEntry.state) || wiState.displayMode === 'waiting');
  return answerable && byId('answer-input').value !== (wiState.currentEntry.answer || '');
}


export function enterUserComment() {
  if (!wiState.currentEntry || wiState.currentEntry.user_comment_editable !== true) return;
  byId('user-comment-input').value = wiState.currentEntry.user_comment || '';
  setDetailMode('user-comment');
  byId('user-comment-input').focus();
}

export function enterDecision(action) {
  if (!wiState.currentEntry || wiState.detailRefreshRequired || !MUTABLE_STATES.has(wiState.currentEntry.state)) return;
  if (action === 'reject' && wiState.currentEntry.kind !== 'awi') return;
  byId('decision-note').value = '';
  setDetailMode(`decide-${action}`);
  byId('decision-note').focus();
}
