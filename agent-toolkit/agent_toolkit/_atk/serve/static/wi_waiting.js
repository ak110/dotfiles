// 判断待ちUWIの連続確認と次の項目への移動。
import {
  MUTABLE_STATES, byId, wiState
} from "./wi_state.js";
import {
  applyPagination, beginUserListOperation, endUserListOperation
} from "./wi_list.js";
import {
  api, deliverOperationMessage
} from "./wi_feedback.js";
import {
  buildQuery, renderEntries
} from "./wi_list_render.js";
import {
  answerInputChanged, closeDetailDialog, displayEntry, syncDetailMutationAvailability
} from "./wi_detail.js";

export function renderWaitingProgress() {
  const progress = byId('waiting-progress');
  progress.hidden = wiState.displayMode !== 'waiting' || !wiState.currentEntry;
  if (progress.hidden) return;
  const index = wiState.entries.findIndex(entry => entry.filename === wiState.currentEntry.filename);
  const position = index < 0 ? null : (wiState.currentPage - 1) * wiState.pagination.page_size + index + 1;
  progress.textContent = position === null
    ? `未回答 残り${wiState.pagination.total_count}件（現在の項目は一覧の対象外）`
    : `現在 ${position} / ${wiState.pagination.total_count}件・未回答 残り${wiState.pagination.total_count}件`;
}

export async function advanceWaiting(after, sessionGeneration, savedMessage = '') {
  const listOperation = beginUserListOperation();
  const stillCurrent = () => wiState.displayMode === 'waiting' && byId('detail-dialog').open &&
    sessionGeneration === wiState.detailSessionGeneration && wiState.currentEntry?.filename === after.filename;
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
        wiState.entries = candidates;
        applyPagination(payload);
        renderEntries(payload.warnings || []);
        wiState.detailSessionGeneration += 1;
        wiState.detailRequestGeneration += 1;
        wiState.detailOriginKey = detail.filename;
        wiState.waitingSavedFilename = '';
        displayEntry(detail);
        byId('detail-dialog-body').scrollTop = 0;
        byId('detail-dialog-body').focus();
        if (savedMessage) deliverOperationMessage(savedMessage);
        return;
      }
      if (page >= (payload.pagination?.page_count || 1)) {
        wiState.entries = candidates;
        applyPagination(payload);
        closeDetailDialog({force: true});
        renderEntries(payload.warnings || []);
        deliverOperationMessage(`${savedMessage}この順番での連続確認は終了しました。未回答は残り${wiState.pagination.total_count}件です。`);
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

export async function skipWaitingAnswer() {
  if (!wiState.currentEntry || wiState.displayMode !== 'waiting' || wiState.waitingAdvancePending || wiState.detailMutationPending) return;
  if (answerInputChanged() && !window.confirm('入力した回答を保存せずに次へ進みますか？')) return;
  wiState.waitingAdvancePending = true;
  wiState.detailMutationPending = true;
  syncDetailMutationAvailability();
  try {
    await advanceWaiting({...wiState.currentEntry}, wiState.detailSessionGeneration);
  } finally {
    wiState.waitingAdvancePending = false;
    wiState.detailMutationPending = false;
    wiState.detailReloadDeferred = false;
    syncDetailMutationAvailability();
  }
}
