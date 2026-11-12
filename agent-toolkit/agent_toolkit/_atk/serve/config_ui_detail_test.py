"""serveのconfigに関するui_detailの試験。"""

# pylint: disable=protected-access

import re

from agent_toolkit._atk.serve import assets
from agent_toolkit._atk.wi import constants as _wi_constants
from agent_toolkit._testing.serve_support import _run_node_ui


def test_assets_are_self_contained() -> None:
    # test-copy-contract: WI資産内への絶対HTTP/HTTPS URLの混入を検出し、外部配信元の追加を防ぐ。
    """WI資産は外部配信元から取得しない。本文の安全表示は実ブラウザー試験で検証する。"""
    combined = (
        assets.HTML
        + assets.CSS
        + "".join(content for name, content in assets.SCRIPTS.items() if name == "wi.js" or name.startswith("wi_"))
    )
    assert "https://" not in combined
    assert "http://" not in combined


def test_assets_global_error_does_not_restore_refresh_after_user_focus_move() -> None:
    """同期中の消去後にエンドユーザーが別の入力へ移動した場合は、そのフォーカスを維持する。"""
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
setGlobalError('同期中の外部更新失敗');
elements['operation-notice-close-button'].focus();
elements['operation-notice-close-button'].listeners.click();
elements['search-input'].focus();
releaseSync();
await synchronization;
process.stdout.write(JSON.stringify({focused, hidden: elements['operation-notice'].hidden}));
"""
    )
    assert result == {"focused": "search-input", "hidden": True}


def test_assets_render_three_contextual_empty_states() -> None:
    """条件適用中、対応中0件、期間内0件、全件0件を別の回復操作へ案内する。"""
    result = _run_node_ui(
        """
entries = [];
renderEmptyState();
const active = {
  message: elements['empty-state-message'].textContent,
  allStates: !elements['empty-all-states-button'].hidden,
  allPeriods: !elements['empty-all-periods-button'].hidden
};
elements['search-input'].value = 'none';
setDisplayMode('search');
renderEmptyState();
const filtered = {
  message: elements['empty-state-message'].textContent,
  clear: !elements['empty-clear-button'].hidden
};
elements['search-input'].value = '';
elements['state-filter'].value = 'all';
elements['period-filter'].value = '2w';
renderEmptyState();
const recent = {
  message: elements['empty-state-message'].textContent,
  create: !elements['empty-create-button'].hidden,
  allPeriods: !elements['empty-all-periods-button'].hidden
};
elements['period-filter'].value = 'all';
renderEmptyState();
const all = {
  message: elements['empty-state-message'].textContent,
  create: !elements['empty-create-button'].hidden,
  allPeriods: !elements['empty-all-periods-button'].hidden
};
process.stdout.write(JSON.stringify({active, filtered, recent, all}));
"""
    )
    assert result == {
        "active": {"message": "直近2週間に作成された対応中の項目はありません。", "allStates": True, "allPeriods": True},
        "filtered": {"message": "条件に一致する項目はありません。", "clear": True},
        "recent": {"message": "直近2週間に作成された項目はありません。", "create": False, "allPeriods": True},
        "all": {"message": "項目はまだありません。", "create": True, "allPeriods": False},
    }


def test_assets_offer_hold_and_rejected_actions_and_preserve_implicit_resolution() -> None:
    """保留・不採用の操作の表示と、採否APIへ明示した状態を送る条件を検証する。"""
    result = _run_node_ui(
        """
const base = {
  kind: 'awi', filename: 'entry.md', answered: null, summary: '対象', target_repo: 'example/repo',
  content: '本文', body_html: '<p>本文</p>', frontmatter_entries: []
};
const visibility = {};
for (const state of ['hold', 'rejected']) {
  displayEntry({...base, state});
  visibility[state] = {
    readonly: !elements['readonly-notice'].hidden,
    edit: !elements['edit-button'].hidden,
    adopt: !elements['adopt-button'].hidden,
    reject: !elements['reject-button'].hidden,
    remove: !elements['delete-button'].hidden,
    unhold: !elements['unhold-button'].hidden,
    returnToInbox: !elements['return-to-inbox-button'].hidden
  };
}
const sent = {};
fetchHandler = async () => ({
  ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})
});
for (const [action, state] of [
  ['adopt', 'inbox'], ['adopt', 'processing'], ['adopt', 'hold'],
  ['reject', 'inbox'], ['reject', 'processing'], ['reject', 'hold']
]) {
  displayEntry({...base, state});
  fetchCalls.length = 0;
  await transitionDetail(action);
  const call = fetchCalls.find(item => item.url.endsWith(`/api/entries/${action}`));
  sent[`${action}-${state}`] = JSON.parse(call.options.body);
}
process.stdout.write(JSON.stringify({visibility, sent}));
"""
    )
    assert result == {
        "visibility": {
            "hold": {
                "readonly": False,
                "edit": True,
                "adopt": True,
                "reject": True,
                "remove": True,
                "unhold": True,
                "returnToInbox": False,
            },
            "rejected": {
                "readonly": False,
                "edit": True,
                "adopt": False,
                "reject": False,
                "remove": True,
                "unhold": False,
                "returnToInbox": True,
            },
        },
        "sent": {
            "adopt-inbox": {"filenames": ["entry.md"]},
            "adopt-processing": {"filenames": ["entry.md"]},
            "adopt-hold": {"filenames": ["entry.md"], "state": "hold"},
            "reject-inbox": {"filenames": ["entry.md"]},
            "reject-processing": {"filenames": ["entry.md"]},
            "reject-hold": {"filenames": ["entry.md"], "state": "hold"},
        },
    }


def test_assets_decision_note_is_shown_only_in_adopt_and_reject_modes() -> None:
    """採否のメモ欄は閲覧中に隠し、採用・却下の操作モードでだけ表示して確定ボタンで送信する。"""
    result = _run_node_ui(
        """
const entry = {
  kind: 'awi', state: 'inbox', filename: 'entry.md', answered: null, summary: '対象', target_repo: 'example/repo',
  content: '本文', body_html: '<p>本文</p>', frontmatter_entries: []
};
fetchHandler = async () => ({
  ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})
});
displayEntry(entry);
const view = {
  panel: !elements['decision-panel'].hidden,
  adopt: !elements['adopt-button'].hidden,
  confirmAdopt: !elements['confirm-adopt-button'].hidden
};
const modes = {};
const sent = {};
for (const action of ['adopt', 'reject']) {
  displayEntry(entry);
  enterDecision(action);
  modes[action] = {
    panel: !elements['decision-panel'].hidden,
    heading: elements['decision-heading'].textContent,
    focused: globalThis.focused,
    confirm: !elements[`confirm-${action}-button`].hidden,
    otherButtons: ['adopt-button', 'reject-button', 'hold-button', 'edit-button', 'delete-button',
      'user-comment-button'].filter(id => !elements[id].hidden)
  };
  elements['decision-note'].value = `  ${action}のメモ  `;
  fetchCalls.length = 0;
  await transitionDetail(action);
  const call = fetchCalls.find(item => item.url.endsWith(`/api/entries/${action}`));
  sent[action] = JSON.parse(call.options.body);
}
process.stdout.write(JSON.stringify({view, modes, sent}));
"""
    )
    assert result == {
        "view": {"panel": False, "adopt": True, "confirmAdopt": False},
        "modes": {
            "adopt": {"panel": True, "heading": "採用", "focused": "decision-note", "confirm": True, "otherButtons": []},
            "reject": {"panel": True, "heading": "却下", "focused": "decision-note", "confirm": True, "otherButtons": []},
        },
        "sent": {
            "adopt": {"filenames": ["entry.md"], "note": "adoptのメモ"},
            "reject": {"filenames": ["entry.md"], "note": "rejectのメモ"},
        },
    }


def test_assets_sse_detail_tracks_state_changes_by_filename_and_closes_deleted_entry() -> None:
    """SSEの詳細再取得は同じファイル名で状態変更を追跡し、404で削除済みとして閉じる。"""
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
entries = [processing];
const exactRequests = [];
fetchHandler = async url => {
  exactRequests.push(url);
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entry: processing})};
};
await reloadOpenDetailFromExternalChange();
const exact = {
  state: currentEntry.state,
  content: elements['detail-content'].innerHTML,
  requests: exactRequests
};

const changedRequests = [];
entries = [inbox];
fetchHandler = async url => {
  changedRequests.push(url);
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entry: inbox})};
};
await reloadOpenDetailFromExternalChange();
const stateChange = {
  state: currentEntry.state, key: detailOriginKey, open: elements['detail-dialog'].open, requests: changedRequests
};

const deletedRequests = [];
entries = [];
fetchHandler = async url => {
  deletedRequests.push(url);
  return {ok: false, status: 404, statusText: 'Not Found', json: async () => ({error: 'not found'})};
};
await reloadOpenDetailFromExternalChange();
process.stdout.write(JSON.stringify({
  exact,
  stateChange,
  deleted: {
    detailOpen: elements['detail-dialog'].open,
    currentEntryIsNull: currentEntry === null,
    error: elements['operation-notice-message'].textContent,
    requests: deletedRequests
  }
}));
"""
    )
    assert result == {
        "exact": {
            "state": "processing",
            "content": "<p>処理中本文</p>",
            "requests": ["/atk/api/entries/same.md"],
        },
        "stateChange": {
            "state": "inbox",
            "key": "same.md",
            "open": True,
            "requests": ["/atk/api/entries/same.md"],
        },
        "deleted": {
            "detailOpen": False,
            "currentEntryIsNull": True,
            "error": "same.mdは削除されたため表示できません。一覧を更新しました。",
            "requests": ["/atk/api/entries/same.md"],
        },
    }


def test_pending_helper_locks_inputs_rejects_reentry_and_keeps_close_enabled() -> None:
    """要求中は対象操作だけを固定し、二重実行を拒み、finallyで状態を復元する。"""
    result = _run_node_ui(
        """
let release;
let calls = 0;
const waiting = new Promise(resolve => { release = resolve; });
const first = runPending('save', {
  container: elements['detail-shell'],
  button: elements['save-entry-button'],
  busyLabel: '保存中'
}, async () => { calls += 1; return waiting; });
await Promise.resolve();
const during = {
  inputDisabled: elements['edit-content'].disabled,
  submitDisabled: elements['save-entry-button'].disabled,
  closeDisabled: elements['detail-close-button'].disabled,
  label: elements['save-entry-button'].textContent,
  busy: elements['detail-shell'].attributes['aria-busy']
};
const second = await runPending('save', {
  container: elements['detail-shell'],
  button: elements['save-entry-button'],
  busyLabel: '保存中'
}, async () => { calls += 1; });
release('done');
await first;
let failureCaught = false;
try {
  await runPending('create', {
    container: elements['create-form'],
    button: elements['create-submit-button'],
    busyLabel: '追加中'
  }, async () => { throw new Error('失敗'); });
} catch (error) {
  failureCaught = error.message === '失敗';
}
process.stdout.write(JSON.stringify({
  during,
  secondIsUndefined: second === undefined,
  calls,
  failureCaught,
  failureRestored: !elements['create-content'].disabled &&
    elements['create-form'].attributes['aria-busy'] === 'false',
  after: {
    inputDisabled: elements['edit-content'].disabled,
    submitDisabled: elements['save-entry-button'].disabled,
    label: elements['save-entry-button'].textContent,
    busy: elements['detail-shell'].attributes['aria-busy']
  }
}));
"""
    )
    assert result == {
        "during": {
            "inputDisabled": True,
            "submitDisabled": True,
            "closeDisabled": False,
            "label": "保存中",
            "busy": "true",
        },
        "secondIsUndefined": True,
        "calls": 1,
        "failureCaught": True,
        "failureRestored": True,
        "after": {
            "inputDisabled": False,
            "submitDisabled": False,
            "label": "",
            "busy": "false",
        },
    }


def test_assets_preserve_user_announcement_across_user_and_sse_request_orders() -> None:
    """エンドユーザーの要求とSSE要求の開始・完了順にかかわらず、件数を一度通知する。"""
    result = _run_node_ui(
        """
async function runCase(startOrder, completionOrder) {
  const resolvers = {};
  let currentKind = '';
  fetchHandler = async url => new Promise(resolve => {
    resolvers[currentKind] = {resolve, url};
  });
  elements['result-status'].textContent = '変更前の通知';
  const requests = {};
  for (const kind of startOrder) {
    currentKind = kind;
    requests[kind] = loadEntries({announce: kind === 'user'});
    await Promise.resolve();
  }
  for (const kind of completionOrder) {
    resolvers[kind].resolve({
      ok: true, status: 200, statusText: 'OK',
      json: async () => ({
        entries: [{kind: 'awi', state: 'inbox', filename: `${kind}.md`, summary: kind}],
        warnings: []
      })
    });
    await requests[kind];
  }
  return {
    status: elements['result-status'].textContent,
    rows: entries.map(entry => entry.filename),
    urls: Object.fromEntries(Object.entries(resolvers).map(([kind, item]) => [kind, item.url]))
  };
}

const userThenSseUserFirst = await runCase(['user', 'sse'], ['user', 'sse']);
const userThenSseSseFirst = await runCase(['user', 'sse'], ['sse', 'user']);
const sseThenUserSseFirst = await runCase(['sse', 'user'], ['sse', 'user']);
const sseThenUserUserFirst = await runCase(['sse', 'user'], ['user', 'sse']);

elements['result-status'].textContent = 'SSE前の通知';
fetchHandler = async () => ({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({entries: [], warnings: []})
});
await loadEntries({announce: false});
process.stdout.write(JSON.stringify({
  cases: [userThenSseUserFirst, userThenSseSseFirst, sseThenUserSseFirst, sseThenUserUserFirst],
  sseOnlyStatus: elements['result-status'].textContent
}));
"""
    )
    assert [case["status"] for case in result["cases"]] == ["1件を表示"] * 4
    assert result["cases"][0]["rows"] == ["sse.md"]
    assert result["cases"][1]["rows"] == ["sse.md"]
    assert result["cases"][2]["rows"] == ["user.md"]
    assert result["cases"][3]["rows"] == ["user.md"]
    assert result["sseOnlyStatus"] == "SSE前の通知"


def test_assets_state_sets_match_python_states() -> None:
    """フロントエンドが持つ状態集合をPython側の保存状態と一致させる。"""
    labels = re.search(r"const STATE_LABELS = \{(.*?)\n\};", assets.SCRIPTS["wi_state.js"], re.DOTALL)
    assert labels is not None
    assert set(re.findall(r"(\w+):", labels.group(1))) == set(_wi_constants.WI_STATES)

    deletable = re.search(r"const DELETABLE_STATES = new Set\(\[(.*?)\]\);", assets.SCRIPTS["wi_state.js"])
    assert deletable is not None
    assert set(re.findall(r"'(\w+)'", deletable.group(1))) == set(_wi_constants.WI_STATES)

    processable = re.search(r"const PROCESSABLE_STATES = new Set\(\[(.*?)\]\);", assets.SCRIPTS["wi_state.js"])
    assert processable is not None
    assert set(re.findall(r"'(\w+)'", processable.group(1))) == set(_wi_constants.WI_PROCESSABLE_STATES)
