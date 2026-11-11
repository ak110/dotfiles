// WI画面のAPI呼出し・通知・ダイアログと送信中表示。
import {
  BASE_PATH, SERVE_HOST, WI_ROOT, diagnostic, timedFetch
} from "./common.js";
import {
  byId, wiState
} from "./wi_state.js";
import {
  syncDetailMutationAvailability
} from "./wi_detail.js";

// 理由の後へ次の操作を続ける。APIの応答と一覧の警告は理由と次の操作を別項目で持つため、表示の直前に合わせる。
export function withNextAction(reason, nextAction) {
  if (typeof nextAction !== 'string' || !nextAction.trim()) return reason;
  return `${reason.replace(/[。.]$/, '')}。次の操作: ${nextAction}`;
}

export async function api(path, {timing = {}, ...options} = {}) {
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

export function setTextMessage(id, message) {
  const element = byId(id);
  element.textContent = message;
  element.hidden = !message;
}

export function setGlobalError(message) {
  wiState.refreshFocusRequested = false;
  if (message) showToast(message, true);
  else closeOperationNotice();
}

function focusRefreshButton() {
  const refreshButton = byId('refresh-button');
  if (refreshButton.disabled) {
    wiState.refreshFocusRequested = true;
    return;
  }
  wiState.refreshFocusRequested = false;
  refreshButton.focus();
}

function restoreRefreshFocus() {
  if (!wiState.refreshFocusRequested) return;
  const refreshButton = byId('refresh-button');
  if (refreshButton.disabled) return;
  wiState.refreshFocusRequested = false;
  refreshButton.focus();
}

export function clearDialogMessages(dialogName) {
  // 詳細で次の操作を始めるときは、前の操作の同期未完了の案内を再表示の対象から外す。
  if (dialogName === 'detail') wiState.syncIncompleteNotice = null;
  setTextMessage(`${dialogName}-alert`, '');
  setTextMessage(`${dialogName}-status`, '');
}

export function showToast(message, isError = false) {
  if (isError && !message.includes('［WI / ')) {
    message = diagnostic('WI', SERVE_HOST, `${WI_ROOT} / 操作`, message);
  }
  const notice = byId('operation-notice');
  clearTimeout(wiState.noticeTimer);
  wiState.noticeOrigin = isError && wiState.pendingOperations.has('sync') ? byId('refresh-button') :
    (document.activeElement === document.body ? wiState.lastFocusedElement : document.activeElement);
  byId('operation-notice-message').textContent = message;
  notice.dataset.error = String(isError);
  notice.setAttribute('role', isError ? 'alert' : 'status');
  notice.setAttribute('aria-live', isError ? 'assertive' : 'polite');
  notice.hidden = false;
  if (!isError) wiState.noticeTimer = setTimeout(() => { notice.hidden = true; }, 6000);
}

export function closeOperationNotice() {
  clearTimeout(wiState.noticeTimer);
  byId('operation-notice').hidden = true;
  const returnTarget = wiState.noticeOrigin?.isConnected && !wiState.noticeOrigin.disabled ? wiState.noticeOrigin : byId('refresh-button');
  if (returnTarget === byId('refresh-button')) focusRefreshButton();
  else returnTarget.focus();
  wiState.noticeOrigin = null;
}

function topmostDialog() {
  for (let index = wiState.dialogStack.length - 1; index >= 0; index -= 1) {
    const dialog = byId(wiState.dialogStack[index]);
    if (dialog && dialog.open) return dialog;
  }
  return null;
}

export function deliverOperationMessage(message, isError = false) {
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

export function openDialog(dialog, origin, focusTarget) {
  wiState.dialogOrigins.set(dialog.id, origin || document.activeElement);
  const previous = wiState.dialogStack.indexOf(dialog.id);
  if (previous >= 0) wiState.dialogStack.splice(previous, 1);
  wiState.dialogStack.push(dialog.id);
  dialog.showModal();
  if (focusTarget) focusTarget.focus();
}

export function openDetailDialog(origin) {
  const body = byId('detail-dialog-body');
  openDialog(byId('detail-dialog'), origin, body);
  body.scrollTop = 0;
}

export function closeDialog(dialog, {restoreFocus = true} = {}) {
  const wasOpen = dialog.open;
  if (wasOpen) dialog.close();
  const index = wiState.dialogStack.lastIndexOf(dialog.id);
  if (index >= 0) wiState.dialogStack.splice(index, 1);
  const origin = wiState.dialogOrigins.get(dialog.id);
  wiState.dialogOrigins.delete(dialog.id);
  if (wasOpen && restoreFocus && origin && typeof origin.focus === 'function') origin.focus();
}

export function setFieldError(input, errorElement, message) {
  input.setAttribute('aria-invalid', message ? 'true' : 'false');
  errorElement.textContent = message;
  errorElement.hidden = !message;
}

export function firstInvalid(inputs) {
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

export async function runPending(key, {container, button, busyLabel}, operation) {
  if (wiState.pendingOperations.has(key)) return undefined;
  wiState.pendingOperations.add(key);
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
    wiState.pendingOperations.delete(key);
  };
  try {
    return await Promise.resolve().then(operation);
  } finally {
    restorePendingState();
    syncDetailMutationAvailability();
    restoreRefreshFocus();
  }
}
