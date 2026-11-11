// WI画面のイベント接続・同期と初期化。
import {
  beginUserListOperation, cancelSearchTimer, clearFilters, enableNotifications, endUserListOperation,
  isCurrentUserListOperation, loadEntries, loadTargetRepos, movePage, refreshKnownEntries, syncNotificationButton
} from "./wi_list.js";
import {
  SERVE_HOST, WI_ROOT, beginOperation, connectEvents, diagnostic, notifyDelayMs, resyncWhenVisible
} from "./common.js";
import {
  api, closeDialog, closeOperationNotice, runPending, setGlobalError
} from "./wi_feedback.js";
import {
  byId, wiState
} from "./wi_state.js";
import {
  copyAllEntries, refreshCooldownDisplays, setDisplayMode
} from "./wi_list_render.js";
import {
  closeDeleteDialog, closeDetailDialog, enterDecision, enterEdit, enterUserComment,
  reloadOpenDetailFromExternalChange, restoreEntryFromUrl
} from "./wi_detail.js";
import {
  createEntry, openCreateDialog, updateCreateChoices, updateCreateFields
} from "./wi_create.js";
import {
  deleteEntry, openDeleteDialog, saveAnswer, saveEntry, saveUserComment, toggleImportant, transitionDetail
} from "./wi_mutations.js";
import {
  skipWaitingAnswer
} from "./wi_waiting.js";
import {
  registerScreen
} from "./shell.js";

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
  wiState.currentPage = 1;
  wiState.pagination.page = 1;
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
export function reloadFromExternalChange(trigger = 'resync', event = null) {
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
  if (wiState.userListOperationPending) {
    wiState.externalListReloadPending = true;
    return;
  }
  await (loadTargetRepos({timing}));
  if (wiState.userListOperationPending) {
    wiState.externalListReloadPending = true;
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
  if (!byId('operation-notice').contains(event.target)) wiState.lastFocusedElement = event.target;
  wiState.refreshFocusRequested = false;
}

function handleSearchInput(event) {
  cancelSearchTimer();
  const operation = beginUserListOperation();
  if (wiState.searchComposing || event?.isComposing) return;
  setDisplayMode('search');
  wiState.currentPage = 1;
  wiState.pagination.page = 1;
  wiState.searchTimer = setTimeout(() => {
    wiState.searchTimer = null;
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
    wiState.searchComposing = true;
    cancelSearchTimer();
    beginUserListOperation();
  });
  byId('search-input').addEventListener('compositionend', () => {
    wiState.searchComposing = false;
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
  if (wiState.detailSessionGeneration === 0) await restoreEntryFromUrl();
}

registerScreen('wi', {init});
