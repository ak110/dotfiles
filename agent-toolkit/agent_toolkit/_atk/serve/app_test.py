"""serveのappに関するui_detailの試験。"""

# pylint: disable=protected-access

import re

import pytest

from agent_toolkit._atk.serve import assets
from agent_toolkit._testing.serve_support import _run_node_ui


def test_assets_size_dialogs_with_small_viewport_height_unit() -> None:
    """高さを決めるビューポート単位を`svh`で書く。

    無印`vh`は大ビューポート基準のため、モバイルのブラウザーUI展開時に
    ダイアログの外枠が可視領域を超え、ヘッダーとフッターが隠れる。
    `dvh`はスクロールに伴うブラウザーUIの伸縮で枠の高さを再レイアウトするため、
    常に可視な下限側へ収める`svh`を採用する。
    """
    assert not re.findall(r"\dvh\b", assets.CSS)
    assert "100svh" in assets.CSS


def test_assets_execute_error_paths_across_binding_and_initialization_code() -> None:
    """外部変更の再読込と初期化のどちらで失敗しても、失敗メッセージを操作通知へ表示する。"""
    result = _run_node_ui(
        """
fetchHandler = async url => {
  if (url.includes('/api/repos?')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos: []})};
  }
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})};
};
refreshKnownEntries = async () => { throw new Error('外部更新失敗'); };
await reloadFromExternalChange();
await Promise.resolve();
const reloadError = elements['operation-notice-message'].textContent;
setGlobalError('');
refreshKnownEntries = async () => { throw new Error('初期化失敗'); };
initializeApp();
await initialization;
process.stdout.write(JSON.stringify({reloadError, initializationError: elements['operation-notice-message'].textContent}));
"""
    )
    assert result == {
        "reloadError": "［WI / node-test / /notes / 操作］外部更新失敗",
        "initializationError": "［WI / node-test / /notes / 操作］初期化失敗",
    }


def test_assets_global_error_does_not_restore_refresh_after_later_error() -> None:
    """同期中の消去後に後続エラーが再表示された場合は閉じる操作のフォーカスを維持する。"""
    result = _run_node_ui(
        """
bindEvents();
let releaseSync;
const syncBlocked = new Promise(resolve => { releaseSync = resolve; });
fetchHandler = async url => {
  if (url.endsWith('/api/sync')) await syncBlocked;
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: [], repos: []})};
};
const synchronization = elements['refresh-button'].listeners.click();
await Promise.resolve();
setGlobalError('同期中の最初のエラー');
elements['operation-notice-close-button'].focus();
elements['operation-notice-close-button'].listeners.click();
setGlobalError('同期中の後続エラー');
elements['operation-notice-close-button'].focus();
releaseSync();
await synchronization;
process.stdout.write(JSON.stringify({
  focused,
  hidden: elements['operation-notice'].hidden,
  message: elements['operation-notice-message'].textContent
}));
"""
    )
    assert result == {
        "focused": "operation-notice-close-button",
        "hidden": False,
        "message": "［WI / node-test / /notes / 操作］同期中の後続エラー",
    }


def test_assets_prefill_answer_change_and_keep_question_visible() -> None:
    """回答できるUWIは開いた時点で既存回答入りの回答欄と他の操作を同時に表示し、候補を入力補助にする。"""
    result = _run_node_ui(
        """
const origin = new Element('origin', 'BUTTON');
const entry = {
  kind: 'uwi', state: 'inbox', filename: 'question.md', answered: true, answer: '既存回答',
  summary: '質問', target_repo: 'example/repo', content: 'raw',
  body_html: '<h2>質問</h2><p>本文</p>', question_type: 'choice', choices: ['A', 'B'],
  frontmatter_entries: []
};
displayEntry(entry);
openDialog(elements['detail-dialog'], origin, elements['detail-dialog-body']);
const answerVisible = !elements['answer-panel'].hidden;
const saveVisible = !elements['save-answer-button'].hidden;
const otherActionsVisible = ['adopt-button', 'hold-button', 'edit-button', 'delete-button']
  .every(id => !elements[id].hidden);
const prefilled = elements['answer-input'].value;
const candidates = elements['answer-choices'].children.map(button => button.textContent);
elements['answer-choices'].children[0].listeners.click();
const afterCandidate = elements['answer-input'].value;
elements['answer-input'].value += 'を補足';
displayEntry({...entry, content: 'raw2'});
const keptAfterRefresh = elements['answer-input'].value;
closeDetailDialog();
process.stdout.write(JSON.stringify({
  detailVisible: !elements['detail-view'].hidden,
  answerVisible,
  saveVisible,
  otherActionsVisible,
  prefilled,
  candidates,
  afterCandidate,
  keptAfterRefresh,
  focused
}));
"""
    )
    assert result == {
        "detailVisible": True,
        "answerVisible": True,
        "saveVisible": True,
        "otherActionsVisible": True,
        "prefilled": "既存回答",
        "candidates": ["A", "B"],
        "afterCandidate": "A",
        "keptAfterRefresh": "Aを補足",
        "focused": "origin",
    }


def test_assets_keep_unsaved_answer_protected_after_switching_mode() -> None:
    """回答欄を隠す編集モードへ切り替えても、入力途中の回答は保持され、閉じる前に破棄を確認する。"""
    result = _run_node_ui(
        """
const entry = {
  kind: 'uwi', state: 'inbox', filename: 'question.md', answered: false, answer: '',
  content: 'question', choices: [], frontmatter_entries: []
};
displayEntry(entry);
openDialog(elements['detail-dialog'], new Element('origin', 'BUTTON'), elements['detail-dialog-body']);
elements['answer-input'].value = '保持する未保存回答';
enterEdit();
const answerHidden = elements['answer-panel'].hidden;
let confirmations = 0;
window.confirm = () => { confirmations += 1; return false; };
closeDetailDialog();
process.stdout.write(JSON.stringify({
  answerHidden,
  confirmations,
  open: elements['detail-dialog'].open,
  kept: elements['answer-input'].value
}));
"""
    )
    assert result == {"answerHidden": True, "confirmations": 1, "open": True, "kept": "保持する未保存回答"}


def test_open_detail_dialog_resets_scroll_position() -> None:
    """WI詳細は再表示のたびに本文の先頭から表示する。"""
    result = _run_node_ui(
        """
const origin = new Element('origin', 'BUTTON');
const body = elements['detail-dialog-body'];
body.scrollTop = 240;
openDetailDialog(origin);
process.stdout.write(JSON.stringify({scrollTop: body.scrollTop, focused}));
"""
    )
    assert result == {"scrollTop": 0, "focused": "detail-dialog-body"}


def test_assets_restore_detail_focus_after_origin_row_disappears() -> None:
    """詳細の起点行が消えた場合も、残存行または空状態の操作へフォーカスを戻す。"""
    result = _run_node_ui(
        """
const first = {
  kind: 'awi', state: 'inbox', filename: 'first.md', answered: null,
  summary: '先頭', content: '本文', body_html: '<p>本文</p>', frontmatter_entries: []
};
const second = {
  kind: 'awi', state: 'inbox', filename: 'second.md', answered: null,
  summary: '次行', content: '本文', body_html: '<p>本文</p>', frontmatter_entries: []
};
entries = [first, second];
renderEntries();
const firstButton = elements['entry-list'].children[0].children[0];
displayEntry(first);
detailOriginKey = entryKey(first);
openDialog(elements['detail-dialog'], firstButton, elements['detail-dialog-body']);
entries = [second];
renderEntries();
closeDetailDialog();
const remainingRow = focused;

entries = [second];
renderEntries();
const secondButton = elements['entry-list'].children[0].children[0];
displayEntry(second);
detailOriginKey = entryKey(second);
openDialog(elements['detail-dialog'], secondButton, elements['detail-dialog-body']);
elements['search-input'].value = '一致しない条件';
setDisplayMode('search');
entries = [];
renderEntries();
closeDetailDialog();
process.stdout.write(JSON.stringify({remainingRow, emptyState: focused}));
"""
    )
    assert result == {
        "remainingRow": "second.md",
        "emptyState": "empty-clear-button",
    }


def test_assets_sse_keeps_edit_target_when_state_changes() -> None:
    """同じ項目のstate更新をSSEで受け取っても、編集中のファイル名を保存先として維持する。"""
    result = _run_node_ui(
        """
const processing = {
  kind: 'awi', state: 'processing', filename: 'same.md', answered: null,
  summary: '処理中', content: '処理中本文', body_html: '<p>処理中本文</p>', frontmatter_entries: []
};
const inbox = {
  ...processing, state: 'inbox', summary: '未処理', content: '未処理本文', body_html: '<p>未処理本文</p>'
};
displayEntry(processing);
detailOriginKey = entryKey(processing);
elements['detail-dialog'].open = true;
entries = [inbox];
enterEdit();
elements['edit-content'].value = 'ユーザーの保存本文';
const putUrls = [];
fetchHandler = async (url, options) => {
  if (options.method === 'PUT') {
    putUrls.push(url);
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({ok: true})};
  }
  if (url.includes('/api/entries?')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [inbox], warnings: []})};
  }
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entry: processing})};
};
await reloadOpenDetailFromExternalChange();
const savedState = currentEntry.state;
await saveEntry();
process.stdout.write(JSON.stringify({putUrls, state: savedState, open: elements['detail-dialog'].open}));
"""
    )
    assert result == {
        "putUrls": ["/atk/api/entries/same.md"],
        "state": "processing",
        "open": False,
    }


def test_assets_clear_self_write_sse_alert_after_save_and_answer_success() -> None:
    """保存・回答の応答前にSSEが届いても、送信中は警告せず、成功後は詳細を閉じる。

    送信中の更新通知は操作自身の書込みを含むため、外部更新として警告すると成功した操作を失敗と誤認させる。
    """
    result = _run_node_ui(
        """
async function runSave() {
  let serverEntry = {
    kind: 'awi', state: 'inbox', filename: 'entry.md', answered: null,
    summary: '保存対象', content: '更新前', body_html: '<p>更新前</p>', frontmatter_entries: []
  };
  displayEntry(serverEntry);
  detailOriginKey = entryKey(serverEntry);
  openDialog(elements['detail-dialog'], new Element('save-origin', 'BUTTON'), elements['detail-dialog-body']);
  enterEdit();
  elements['edit-content'].value = '保存後';
  let resolveMutation;
  let markStarted;
  const started = new Promise(resolve => { markStarted = resolve; });
  fetchHandler = async (url, options) => {
    if (options.method === 'PUT') return new Promise(resolve => {
      resolveMutation = resolve;
      markStarted();
    });
    if (url.includes('/api/entries?')) {
      return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [serverEntry], warnings: []})};
    }
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entry: serverEntry})};
  };
  const saving = saveEntry();
  await started;
  serverEntry = {...serverEntry, content: '保存後', body_html: '<p>保存後</p>'};
  await reloadOpenDetailFromExternalChange();
  const during = elements['detail-alert'].textContent;
  resolveMutation({ok: true, status: 200, statusText: 'OK', json: async () => ({ok: true})});
  await saving;
  return {
    during,
    after: elements['detail-alert'].textContent,
    status: elements['detail-status'].textContent,
    toast: elements['toast'].textContent,
    open: elements['detail-dialog'].open,
    mode: currentDetailMode()
  };
}

async function runAnswer() {
  let serverEntry = {
    kind: 'uwi', state: 'inbox', filename: 'question.md', answered: false,
    summary: '回答対象', content: '質問', body_html: '<p>質問</p>',
    question_type: 'free-form', choices: [], frontmatter_entries: []
  };
  displayEntry(serverEntry);
  detailOriginKey = entryKey(serverEntry);
  openDialog(elements['detail-dialog'], new Element('answer-origin', 'BUTTON'), elements['detail-dialog-body']);
  elements['answer-input'].value = '回答';
  let resolveMutation;
  let markStarted;
  const started = new Promise(resolve => { markStarted = resolve; });
  fetchHandler = async (url, options) => {
    if (url.endsWith('/api/entries/answer') && options.method === 'POST') return new Promise(resolve => {
      resolveMutation = resolve;
      markStarted();
    });
    if (url.includes('/api/entries?')) {
      return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [serverEntry], warnings: []})};
    }
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entry: serverEntry})};
  };
  const answering = saveAnswer();
  await started;
  serverEntry = {...serverEntry, answered: true, content: '質問と回答', body_html: '<p>質問と回答</p>'};
  await reloadOpenDetailFromExternalChange();
  const during = elements['detail-alert'].textContent;
  resolveMutation({ok: true, status: 200, statusText: 'OK', json: async () => ({ok: true})});
  await answering;
  return {
    during,
    after: elements['detail-alert'].textContent,
    status: elements['detail-status'].textContent,
    toast: elements['toast'].textContent,
    open: elements['detail-dialog'].open,
    mode: currentDetailMode()
  };
}

const saved = await runSave();
const answered = await runAnswer();
process.stdout.write(JSON.stringify({saved, answered}));
"""
    )
    assert result == {
        "saved": {
            "during": "",
            "after": "",
            "status": "",
            "toast": "entry.mdを保存しました。",
            "open": False,
            "mode": "view",
        },
        "answered": {
            "during": "",
            "after": "",
            "status": "",
            "toast": "question.mdへ回答しました。",
            "open": False,
            "mode": "view",
        },
    }


def test_node_dom_matches_html_structure() -> None:
    """本番HTMLのIDと、通知・フォーム・ヘッダーの構造が擬似DOMでも一致する。"""
    result = _run_node_ui("""
process.stdout.write(JSON.stringify({
  ids: Object.keys(elements).filter(id => id !== 'toast').sort(),
  header: controlGroups['app-header'].map(node => node.id),
  notification: elements['operation-notice'].children.map(node => node.id),
  closeClass: elements['detail-close-button'].className,
  aria: elements['operation-notice-close-button'].getAttribute('aria-label'),
  selected: ['kind-filter', 'state-filter', 'period-filter', 'answer-filter', 'create-kind', 'create-question-type']
    .map(id => elements[id].value)
}));
""")
    assert result["ids"] == sorted(re.findall(r'\bid="([^"]+)"', assets.HTML))
    assert "notification-button" in result["header"]
    assert result["notification"] == ["operation-notice-message", "operation-notice-close-button"]
    assert result["closeClass"] == "dialog-close"
    assert result["aria"] == "操作通知を閉じる"
    assert result["selected"] == ["all", "all", "all", "all", "awi", "yes-no"]


def test_node_dom_uses_new_html_element(monkeypatch: pytest.MonkeyPatch) -> None:
    """新しい入れ子のフォーム要素と属性を加えても、helperへの追記なしで取得できる。"""
    added = (
        '<form id="probe-form"><span><input id="probe-input" class="probe" aria-label="追随" /></span>'
        '<select id="probe-select"><option value="first">先頭</option><option value="chosen" selected>選択</option></select>'
        '<button id="probe-button" hidden disabled>実行</button></form>'
    )
    monkeypatch.setattr(assets, "HTML", assets.HTML + added)
    result = _run_node_ui("""
process.stdout.write(JSON.stringify({
  controls: elements['probe-form'].querySelectorAll('input, select, textarea, button').map(node => node.id),
  value: elements['probe-select'].value, className: elements['probe-input'].className,
  aria: elements['probe-input'].getAttribute('aria-label'), hidden: elements['probe-button'].hidden,
  disabled: elements['probe-button'].disabled
}));
""")
    assert result == {
        "controls": ["probe-input", "probe-select", "probe-button"],
        "value": "chosen",
        "className": "probe",
        "aria": "追随",
        "hidden": True,
        "disabled": True,
    }
