// WI画面の状態・状態集合と要素の識別。
// エラー表示は既存のError契約に合わせ、error.messageを直接参照する。
export const KIND_LABELS = {awi: 'awi', uwi: 'uwi', unknown: 'unknown'};
// 確認状態の値ごとの表示名。キーを持たない項目は表示しない。
export const STATE_LABELS = {
  inbox: 'inbox', processing: 'processing', hold: 'hold',
  adopted: 'adopted', rejected: 'rejected'
};
export const PROCESSABLE_STATES = new Set(['inbox', 'processing']);
export const MUTABLE_STATES = new Set(['inbox', 'processing', 'hold']);
export const EDITABLE_STATES = new Set(['inbox', 'processing', 'hold', 'adopted', 'rejected']);
export const TERMINAL_STATES = new Set(['adopted', 'rejected']);
export const DELETABLE_STATES = new Set(['inbox', 'processing', 'hold', 'adopted', 'rejected']);
export const ENTRY_PAGE_SIZE = 100;
export const TARGET_REPO_DISPLAY_LENGTH = 20;
export const FRONTMATTER_EXCLUDED_KEYS = new Set(['type', 'origin_session', 'origin_locator']);

export const wiState = {
  entries: [],
  currentEntry: null,
  detailOrigin: null,
  detailOriginKey: '',
  detailRequestGeneration: 0,
  detailSessionGeneration: 0,
  listRequestGeneration: 0,
  targetRepoRequestGeneration: 0,
  knownEntryRequestGeneration: 0,
  listLoadingVisible: false,
  listRequestPending: false,
  detailRequestPending: false,
  userListOperationGeneration: 0,
  userListOperationPending: false,
  externalListReloadPending: false,
  pendingListAnnouncement: false,
  detailRefreshRequired: false,
  // 確認状態の同期が完了しなかった案内。失敗した操作自身のローカル書込みが外部変更として通知され、
  // 詳細の自動再取得が警告欄を消すため、同じ詳細を開いている間だけ再取得後に同じ案内を表示し直す。
  // 詳細を閉じるか別の項目を開くと`detailSessionGeneration`が変わり、案内は表示されなくなる。
  syncIncompleteNotice: null,
  // 詳細ダイアログから送信した変更操作の応答を待つ間か、その間に詳細の再読込を保留したか。
  detailMutationPending: false,
  detailReloadDeferred: false,
  deleteDialogEntrySnapshot: '',
  searchTimer: null,
  searchComposing: false,
  displayMode: 'normal',
  waitingAdvancePending: false,
  waitingSavedFilename: '',
  cooldownTimer: null,
  entryStateDisplays: new Map(),
  currentPage: 1,
  pagination: {page: 1, page_size: ENTRY_PAGE_SIZE, page_count: 1, total_count: 0},
  knownEntryBaselineReady: false,
  knownEntryFilenames: new Set(),
  pendingOperations: new Set(),
  dialogOrigins: new Map(),
  dialogStack: [],
  refreshFocusRequested: false,
  noticeTimer: null,
  noticeOrigin: null,
  lastFocusedElement: null
};

export const byId = id => document.getElementById(id);
export const entryKey = entry => entry ? entry.filename : '';
export const isPlainPrimaryClick = event =>
  event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
export const deleteEntrySnapshot = entry => entry ? JSON.stringify([
  entryKey(entry), entry.state, entry.content, entry.target_repo || '', entry.summary || ''
]) : '';
