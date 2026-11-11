// WIの単件・一括登録フォーム。
import {
  byId
} from "./wi_state.js";
import {
  api, clearDialogMessages, closeDialog, deliverOperationMessage, firstInvalid, openDialog, runPending, setFieldError
} from "./wi_feedback.js";
import {
  SERVE_HOST, WI_ROOT, beginOperation, diagnostic
} from "./common.js";
import {
  clearFilters
} from "./wi_list.js";

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

export function updateCreateFields() {
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

export function updateCreateChoices() {
  if (choiceValues().length >= 2) setFieldError(byId('create-choices'), byId('create-choices-error'), '');
}

export function openCreateDialog(origin = null) {
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

export async function createEntry(event) {
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
