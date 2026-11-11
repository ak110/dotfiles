// WI詳細からの保存・回答・状態遷移と削除。
import {
  MUTABLE_STATES, PROCESSABLE_STATES, TERMINAL_STATES, byId, deleteEntrySnapshot, entryKey, wiState
} from "./wi_state.js";
import {
  api, clearDialogMessages, deliverOperationMessage, firstInvalid, openDialog, runPending, setFieldError,
  setTextMessage, showToast
} from "./wi_feedback.js";
import {
  DELETE_CONFLICT_MESSAGE, closeDetailDialog, entryButtonForKey, invalidateDeleteConfirmation,
  refreshUserCommentMode, reloadOpenDetailFromExternalChange, setDetailMode, syncDetailMutationAvailability
} from "./wi_detail.js";
import {
  loadEntries
} from "./wi_list.js";
import {
  beginOperation
} from "./common.js";
import {
  advanceWaiting
} from "./wi_waiting.js";
import {
  entryStateText
} from "./wi_list_render.js";

async function reloadUserCommentAfterConflict(key, sessionGeneration) {
  const filename = wiState.currentEntry?.filename;
  if (!filename) return false;
  try {
    const refreshed = await api(`/api/entries/${encodeURIComponent(filename)}`);
    if (!byId('detail-dialog').open || entryKey(wiState.currentEntry) !== key ||
        sessionGeneration !== wiState.detailSessionGeneration || refreshed.entry.user_comment_editable !== true) return false;
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
  const recoveryRequired = error.payload?.code === 'edit_conflict' || wiState.detailRefreshRequired;
  if (!recoveryRequired) return failure;
  wiState.detailRefreshRequired = true;
  syncDetailMutationAvailability();
  const recovery = `${key}は外部で更新されました。入力を保持しています。` +
    '詳細を閉じて開き直してから保存してください。';
  return `${failure} ${recovery}`;
}

// 操作の対象と開いている詳細の同一性は、操作で変わり得る状態を除き、詳細を開いた単位とファイル名で判定する。
// 成功の表示と一覧の再取得を行い、一覧の再描画を終えるPromiseを返す（操作の所要の終点に使う）。
function finishDetailMutation(key, filename, sessionGeneration, message, timing = {}) {
  const sameDetail = byId('detail-dialog').open && wiState.currentEntry?.filename === filename &&
    sessionGeneration === wiState.detailSessionGeneration;
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
  if (wiState.pendingOperations.has(pendingKey)) return;
  const operation = beginOperation('wi', `mutation:${pendingKey}`, {trigger: 'click'});
  const key = entryKey(wiState.currentEntry);
  const filename = wiState.currentEntry.filename;
  const sessionGeneration = wiState.detailSessionGeneration;
  wiState.detailMutationPending = true;
  wiState.detailReloadDeferred = false;
  let result;
  try {
    result = await (runPending(pendingKey, pendingOptions, () => request(operation.timing)));
  } catch (error) {
    wiState.detailMutationPending = false;
    if (pendingKey === 'answer' && wiState.waitingAdvancePending) {
      wiState.detailReloadDeferred = false;
      if (error.payload?.code === 'sync_incomplete') {
        wiState.waitingSavedFilename = filename;
        wiState.detailRefreshRequired = true;
      }
      operation.end('error');
      throw error;
    }
    if (error.payload?.code === 'sync_incomplete' &&
        sessionGeneration === wiState.detailSessionGeneration && wiState.currentEntry?.filename === filename) {
      const message = error.message;
      wiState.detailReloadDeferred = false;
      if (pendingKey === 'delete') {
        closeDetailDialog({force: true});
        await loadEntries({showLoading: false, timing: operation.timing});
        deliverOperationMessage(message, true);
      } else {
        wiState.syncIncompleteNotice = {sessionGeneration, filename, message};
        // 応答はローカル保存済みを保証する。保存した値を表示へ切り替え、後続の編集では通常の入力保持へ戻す。
        setDetailMode('view');
        await reloadOpenDetailFromExternalChange({timing: operation.timing});
        await loadEntries({showLoading: false, timing: operation.timing});
        deliverOperationMessage(message, true);
      }
      operation.end('sync-incomplete');
      return;
    }
    if (wiState.detailReloadDeferred) {
      wiState.detailReloadDeferred = false;
      await (reloadOpenDetailFromExternalChange({timing: operation.timing}));
    }
    // 失敗の文は呼出し元が例外を受けて同期的に表示するため、その描画後の時刻で記録される。
    operation.end('error');
    throw error;
  } finally {
    wiState.detailMutationPending = false;
  }
  wiState.detailReloadDeferred = false;
  const message = typeof successMessage === 'function' ? successMessage(result) : successMessage;
  if (onSuccess) {
    await onSuccess(message);
    operation.end('ok');
    return;
  }
  void finishDetailMutation(key, filename, sessionGeneration, message, operation.timing)
    .finally(() => operation.end('ok'));
}

export async function saveEntry() {
  if (!wiState.currentEntry || wiState.detailRefreshRequired) return;
  const content = byId('edit-content').value;
  setFieldError(byId('edit-content'), byId('edit-content-error'), content.trim() ? '' : 'ファイル全体を入力してください。');
  if (firstInvalid([byId('edit-content')])) return;
  const key = entryKey(wiState.currentEntry);
  const path = `/api/entries/${encodeURIComponent(wiState.currentEntry.filename)}`;
  const payload = {content, expected_content: wiState.currentEntry.content};
  clearDialogMessages('detail');
  try {
    await (runDetailMutation('save', {
      container: byId('detail-shell'), button: byId('save-entry-button'), busyLabel: '保存中'
    }, timing => api(path, {method: 'PUT', body: JSON.stringify(payload), timing}), `${key}を保存しました。`));
  } catch (error) {
    const failure = `${key}を保存できませんでした。 ${error.message}`;
    deliverOperationMessage(mutationFailureMessage(key, failure, error), true);
    if (byId('detail-dialog').open && entryKey(wiState.currentEntry) === key) byId('edit-content').focus();
  }
}

export async function toggleImportant() {
  if (!wiState.currentEntry || wiState.detailRefreshRequired) return;
  const key = entryKey(wiState.currentEntry);
  const important = wiState.currentEntry.important !== true;
  const label = important ? '重要設定' : '重要解除';
  const payload = {filename: wiState.currentEntry.filename, important, expected_content: wiState.currentEntry.content};
  clearDialogMessages('detail');
  try {
    await runDetailMutation('important', {
      container: byId('detail-shell'), button: byId('important-button'), busyLabel: '保存中'
    }, timing => api('/api/entries/important', {method: 'POST', body: JSON.stringify(payload), timing}), `${key}を${label}しました。`);
  } catch (error) {
    deliverOperationMessage(mutationFailureMessage(key, `${key}を${label}できませんでした。 ${error.message}`, error), true);
  }
}

export async function saveAnswer({next = false} = {}) {
  if (!wiState.currentEntry || wiState.detailRefreshRequired || wiState.waitingAdvancePending || wiState.detailMutationPending) return;
  const advance = next && wiState.displayMode === 'waiting';
  if (advance && (wiState.waitingSavedFilename === wiState.currentEntry.filename || wiState.currentEntry.answered !== false ||
      !MUTABLE_STATES.has(wiState.currentEntry.state))) return;
  const answer = byId('answer-input').value;
  setFieldError(byId('answer-input'), byId('answer-input-error'), answer.trim() ? '' : '回答を入力してください。');
  if (firstInvalid([byId('answer-input')])) return;
  const key = entryKey(wiState.currentEntry);
  const sessionGeneration = wiState.detailSessionGeneration;
  const payload = {
    filename: wiState.currentEntry.filename,
    state: wiState.currentEntry.state,
    answer,
    expected_content: wiState.currentEntry.content
  };
  clearDialogMessages('detail');
  const answeredEntry = {...wiState.currentEntry};
  if (advance) wiState.waitingAdvancePending = true;
  try {
    await (runDetailMutation('answer', {
      container: byId('detail-shell'), button: byId(advance ? 'save-next-answer-button' : 'save-answer-button'), busyLabel: '保存中'
    }, timing => api('/api/entries/answer', {method: 'POST', body: JSON.stringify(payload), timing}), result =>
      result.state === 'adopted' ? `${key}の回答を保存し、採用済み（adopted）へ移しました。` : `${key}へ回答しました。`,
    advance ? async message => {
      wiState.waitingSavedFilename = answeredEntry.filename;
      wiState.detailMutationPending = true;
      try {
        await advanceWaiting(answeredEntry, sessionGeneration, message);
      } finally {
        wiState.detailMutationPending = false;
        wiState.detailReloadDeferred = false;
      }
    } : null));
  } catch (error) {
    const failure = `${key}へ回答できませんでした。 ${error.message}`;
    if (sessionGeneration === wiState.detailSessionGeneration) {
      deliverOperationMessage(mutationFailureMessage(key, failure, error), true);
    } else {
      showToast(mutationFailureMessage(key, failure, error), true);
    }
    if (byId('detail-dialog').open && entryKey(wiState.currentEntry) === key) byId('answer-input').focus();
  } finally {
    if (advance) wiState.waitingAdvancePending = false;
    syncDetailMutationAvailability();
  }
}

export async function saveUserComment() {
  if (!wiState.currentEntry || wiState.detailRefreshRequired || wiState.currentEntry.user_comment_editable !== true) return;
  const input = byId('user-comment-input');
  const comment = input.value;
  setFieldError(input, byId('user-comment-input-error'), '');
  const key = entryKey(wiState.currentEntry);
  // 空の保存はユーザーコメント節の削除を表す。節が無い項目では本文を変えずに終える。
  const savedMessage = comment.trim() ? `${key}のユーザーコメントを保存しました。` :
    wiState.currentEntry.user_comment == null ? `${key}にはユーザーコメントが無いため、変更していません。` :
      `${key}のユーザーコメントを削除しました。`;
  const sessionGeneration = wiState.detailSessionGeneration;
  const payload = {
    state: wiState.currentEntry.state,
    filename: wiState.currentEntry.filename,
    comment,
    expected_content: wiState.currentEntry.content
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
    if (byId('detail-dialog').open && entryKey(wiState.currentEntry) === key) input.focus();
  }
}

export async function transitionDetail(action) {
  if (!wiState.currentEntry || wiState.detailRefreshRequired) return;
  const terminal = TERMINAL_STATES.has(wiState.currentEntry.state);
  const allowed = action === 'unhold' ? wiState.currentEntry.state === 'hold' :
    action === 'return-to-inbox' ? terminal :
      action === 'hold' ? PROCESSABLE_STATES.has(wiState.currentEntry.state) || terminal : MUTABLE_STATES.has(wiState.currentEntry.state);
  if (!allowed || (action === 'reject' && wiState.currentEntry.kind !== 'awi')) return;
  const key = entryKey(wiState.currentEntry);
  const payload = {filenames: [wiState.currentEntry.filename]};
  if (terminal && (action === 'return-to-inbox' || action === 'hold')) payload.state = wiState.currentEntry.state;
  if ((action === 'adopt' || action === 'reject') && wiState.currentEntry.state === 'hold') payload.state = 'hold';
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

export function openDeleteDialog() {
  if (!wiState.currentEntry) return;
  wiState.deleteDialogEntrySnapshot = deleteEntrySnapshot(wiState.currentEntry);
  clearDialogMessages('delete');
  byId('delete-target').textContent = wiState.currentEntry.filename;
  byId('delete-state').textContent = entryStateText(wiState.currentEntry);
  byId('delete-state').dataset.state = wiState.currentEntry.state;
  byId('delete-target-repo').textContent = wiState.currentEntry.target_repo || '—';
  byId('delete-summary').textContent = wiState.currentEntry.summary || '—';
  byId('force-delete-row').hidden = wiState.currentEntry.state !== 'processing';
  byId('force-delete-confirmation').checked = false;
  setFieldError(byId('force-delete-confirmation'), byId('delete-error'), '');
  openDialog(byId('delete-dialog'), byId('delete-button'), byId('delete-close-button'));
}

export async function deleteEntry(event) {
  event.preventDefault();
  if (!wiState.currentEntry) return;
  const force = byId('force-delete-confirmation').checked;
  if (wiState.currentEntry.state === 'processing' && !force) {
    setFieldError(byId('force-delete-confirmation'), byId('delete-error'), '処理中の項目を削除するには確認が必要です。');
    byId('force-delete-confirmation').focus();
    return;
  }
  const key = entryKey(wiState.currentEntry);
  const payload = {
    filenames: [wiState.currentEntry.filename],
    state: wiState.currentEntry.state,
    expected_content: wiState.currentEntry.content,
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
      wiState.detailRefreshRequired = true;
      syncDetailMutationAvailability();
      setTextMessage('detail-alert', `${failure} ${DELETE_CONFLICT_MESSAGE}`);
      byId('detail-dialog-body').focus();
    } else {
      deliverOperationMessage(failure, true);
      if (byId('delete-dialog').open) byId('delete-close-button').focus();
    }
  }
}
