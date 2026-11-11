"""serveのcliに関するui_detailの試験。"""

# pylint: disable=protected-access

import pathlib

from agent_toolkit._atk.serve import assets
from agent_toolkit._testing.serve_support import _run_node_ui


def test_text_assets_are_bundled_as_plugin_files() -> None:
    """配布対象のscripts配下に実ファイルを同梱し、Python側がその内容を読む。"""
    static_dir = pathlib.Path(assets.__file__).with_name("static")
    expected = {
        "index.html": assets.HTML.removesuffix("\n"),
        **{name: content.removesuffix("\n") for name, content in assets.STYLES.items()},
        **assets.SCRIPTS,
    }
    assert {path.name for path in static_dir.iterdir()} == set(expected)
    for filename, content in expected.items():
        bundled = (static_dir / filename).read_text(encoding="utf-8")
        assert (bundled if filename.endswith(".js") else bundled.removesuffix("\n")) == content


def test_assets_global_error_focuses_refresh_after_synchronization() -> None:
    """同期中の共通エラー消去後も、再び操作可能になった同期ボタンへフォーカスを移す。"""
    result = _run_node_ui(
        """
bindEvents();
let releaseSync;
let syncCalls = 0;
const syncBlocked = new Promise(resolve => { releaseSync = resolve; });
fetchHandler = async url => {
  if (url.endsWith('/api/sync')) {
    syncCalls += 1;
    await syncBlocked;
  }
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: [], repos: []})};
};
elements['refresh-button'].focus();
const synchronization = elements['refresh-button'].listeners.click();
await Promise.resolve();
const disabled = elements['refresh-button'].disabled;
const duplicateSynchronization = elements['refresh-button'].listeners.click();
setGlobalError('同期中の外部更新失敗');
elements['operation-notice-close-button'].focus();
elements['operation-notice-close-button'].listeners.click();
const during = {
  hidden: elements['operation-notice'].hidden,
  focused
};
releaseSync();
await Promise.all([synchronization, duplicateSynchronization]);
process.stdout.write(JSON.stringify({
  disabled,
  syncCalls,
  during,
  after: {
    disabled: elements['refresh-button'].disabled,
    focused,
    hidden: elements['operation-notice'].hidden
  }
}));
"""
    )
    assert result == {
        "disabled": True,
        "syncCalls": 1,
        "during": {"hidden": True, "focused": "operation-notice-close-button"},
        "after": {"disabled": False, "focused": "refresh-button", "hidden": True},
    }


def test_assets_allow_terminal_entry_editing_and_keep_decision_closed() -> None:
    """終了済みの本文編集と削除を許可し、採否を閉じたままkind/state識別子を表示する。"""
    result = _run_node_ui(
        """
displayEntry({
  kind: 'uwi', state: 'adopted', filename: 'done.md', answered: true, answer: '回答',
  summary: '完了', target_repo: 'example/repo', content: 'raw', body_html: '<p>本文</p>',
  question_type: 'free-form', choices: [], frontmatter_entries: []
});
enterDecision('adopt');
process.stdout.write(JSON.stringify({
  heading: elements['detail-state'].textContent,
  metadata: elements['detail-metadata'].children.map(child => child.children.map(item => item.textContent).join(':')),
  readonly: !elements['readonly-notice'].hidden,
  editHidden: elements['edit-button'].hidden,
  answerHidden: elements['answer-panel'].hidden,
  deleteHidden: elements['delete-button'].hidden,
  decisionHidden: elements['decision-panel'].hidden
}));
"""
    )
    assert result == {
        "heading": "uwi / adopted",
        "metadata": [
            "answered:yes",
            "target_repo:example/repo",
            "updated_at:—",
        ],
        "readonly": False,
        "editHidden": False,
        "answerHidden": True,
        "deleteHidden": False,
        "decisionHidden": True,
    }


def test_assets_notify_only_new_active_unanswered_uwi_after_permission() -> None:
    """初期集合と既知項目の属性変化を通知せず、新規の未回答UWIと要確認AWIだけを種別ごとの題で通知する。

    確認済みへ切り替えた既知のAWIと、既知のAWIを要確認へ戻した変化は通知しない。
    """
    result = _run_node_ui(
        """
const notifications = [];
globalThis.Notification = class {
  static permission = 'default';
  static async requestPermission() { this.permission = 'granted'; return this.permission; }
  constructor(title, options) { notifications.push({title, body: options.body}); }
};
const snapshots = [
  [
    {kind: 'uwi', state: 'inbox', filename: 'base.md', answered: false, target_repo: 'old/repo'},
    {kind: 'uwi', state: 'inbox', filename: 'answered.md', answered: true},
    {kind: 'uwi', state: 'inbox', filename: 'moving.md', answered: false},
    {kind: 'uwi', state: 'adopted', filename: 'reappear.md', answered: true},
    {kind: 'awi', state: 'inbox', filename: 'base-awi.md', confirmation: 'required'},
    {kind: 'awi', state: 'inbox', filename: 'confirmed-awi.md', confirmation: 'confirmed'}
  ],
  [
    {kind: 'uwi', state: 'inbox', filename: 'base.md', answered: false, target_repo: 'new/repo'},
    {kind: 'uwi', state: 'inbox', filename: 'answered.md', answered: false},
    {kind: 'uwi', state: 'processing', filename: 'moving.md', answered: false},
    {kind: 'uwi', state: 'inbox', filename: 'new.md', answered: false},
    {kind: 'awi', state: 'inbox', filename: 'base-awi.md', confirmation: 'confirmed'},
    {kind: 'awi', state: 'inbox', filename: 'confirmed-awi.md', confirmation: 'required'},
    {kind: 'awi', state: 'inbox', filename: 'new-awi.md', confirmation: 'required'},
    {kind: 'awi', state: 'inbox', filename: 'plain-awi.md', confirmation: null}
  ],
  [
    {kind: 'uwi', state: 'inbox', filename: 'base.md', answered: false},
    {kind: 'uwi', state: 'inbox', filename: 'answered.md', answered: true},
    {kind: 'uwi', state: 'inbox', filename: 'moving.md', answered: false},
    {kind: 'uwi', state: 'inbox', filename: 'new.md', answered: true},
    {kind: 'uwi', state: 'inbox', filename: 'reappear.md', answered: false}
  ]
];
fetchHandler = async () => ({
  ok: true, status: 200, statusText: 'OK', json: async () => ({entries: snapshots.shift()})
});
syncNotificationButton();
const buttonVisibleBefore = !elements['notification-button'].hidden;
await enableNotifications();
const buttonHiddenAfter = elements['notification-button'].hidden;
await refreshKnownEntries({notify: false});
const afterBaseline = notifications.length;
await refreshKnownEntries({notify: true});
await refreshKnownEntries({notify: true});
process.stdout.write(JSON.stringify({
  buttonVisibleBefore, buttonHiddenAfter, afterBaseline, notifications,
  known: Array.from(knownEntryFilenames).sort()
}));
"""
    )
    assert result == {
        "buttonVisibleBefore": True,
        "buttonHiddenAfter": True,
        "afterBaseline": 0,
        "notifications": [
            {"title": "新規未回答UWI", "body": "new.md"},
        ],
        "known": [
            "answered.md",
            "base-awi.md",
            "base.md",
            "confirmed-awi.md",
            "moving.md",
            "new-awi.md",
            "new.md",
            "plain-awi.md",
            "reappear.md",
        ],
    }


def test_assets_sse_reconciles_owned_delete_dialog() -> None:
    """SSEで対象の移動を受けた場合は削除確認を閉じ、消失を受けた場合は詳細と削除確認を閉じて一覧へ戻す。"""
    result = _run_node_ui(
        """
const inbox = {
  kind: 'awi', state: 'inbox', filename: 'entry.md', answered: null,
  summary: '移動前', target_repo: 'example/repo', content: '本文', body_html: '<p>本文</p>',
  frontmatter_entries: []
};
const processing = {...inbox, state: 'processing', summary: '移動後'};
const remaining = {...inbox, filename: 'remaining.md', summary: '残存'};
entries = [inbox, remaining];
renderEntries();
const origin = elements['entry-list'].children[0].children[0];
displayEntry(inbox);
detailOriginKey = entryKey(inbox);
openDialog(elements['detail-dialog'], origin, elements['detail-dialog-body']);
openDeleteDialog();
let phase = 'moved';
fetchHandler = async url => {
  if (phase === 'moved' && url === '/atk/api/entries/entry.md') {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entry: processing})};
  }
  return {ok: false, status: 404, statusText: 'Not Found', json: async () => ({error: 'not found'})};
};
await reloadOpenDetailFromExternalChange();
const moved = {
  detailOpen: elements['detail-dialog'].open,
  deleteOpen: elements['delete-dialog'].open,
  state: currentEntry.state,
  focused
};
openDeleteDialog();
const reopened = {
  state: elements['delete-state'].textContent,
  forceVisible: !elements['force-delete-row'].hidden
};
entries = [remaining];
renderEntries();
phase = 'missing';
await reloadOpenDetailFromExternalChange();
process.stdout.write(JSON.stringify({
  moved,
  reopened,
  missing: {
    detailOpen: elements['detail-dialog'].open,
    deleteOpen: elements['delete-dialog'].open,
    focused
  }
}));
"""
    )
    assert result == {
        "moved": {
            "detailOpen": True,
            "deleteOpen": False,
            "state": "processing",
            "focused": "detail-dialog-body",
        },
        "reopened": {"state": "awi / processing", "forceVisible": True},
        "missing": {
            "detailOpen": False,
            "deleteOpen": False,
            "focused": "remaining.md",
        },
    }


def test_assets_close_delete_confirmation_before_detail_refresh_failure() -> None:
    """一覧の反映後に詳細の取得が失敗しても、古い削除確認を閉じて詳細のダイアログへ復旧手順を示す。"""
    result = _run_node_ui(
        """
async function exercise(updatedEntries, warnings) {
  const original = {
    kind: 'awi', state: 'inbox', filename: 'entry.md', answered: null,
    summary: '変更前の要約', target_repo: 'example/old', content: '変更前本文', body_html: '<p>変更前本文</p>',
    frontmatter_entries: []
  };
  entries = [original];
  renderEntries();
  displayEntry(original);
  detailOriginKey = entryKey(original);
  openDialog(elements['detail-dialog'], elements['entry-list'].children[0].children[0], elements['detail-dialog-body']);
  openDeleteDialog();
  fetchHandler = async url => {
    if (url.includes('/api/repos')) {
      return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos: []})};
    }
    if (url.includes('/api/entries?')) {
      return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: updatedEntries, warnings})};
    }
    return {ok: false, status: 500, statusText: 'Error', json: async () => ({error: '詳細取得に失敗'})};
  };
  await reloadFromExternalChange();
  const outcome = {
    deleteOpen: elements['delete-dialog'].open,
    detailOpen: elements['detail-dialog'].open,
    alert: elements['detail-alert'].textContent,
    topmost: topmostDialog()?.id || '',
    focused
  };
  closeDetailDialog();
  return outcome;
}

const updated = {
  kind: 'awi', state: 'inbox', filename: 'entry.md', answered: null,
  summary: '変更後の要約', target_repo: 'example/new', content: '変更後本文', body_html: '<p>変更後本文</p>'
};
const changed = await exercise([updated], []);
const unreadable = await exercise([], [{filename: 'entry.md', reason: 'UTF-8として読み取れません'}]);
process.stdout.write(JSON.stringify({changed, unreadable}));
"""
    )
    for outcome in result.values():
        assert outcome["deleteOpen"] is False
        assert outcome["detailOpen"] is True
        assert "詳細取得に失敗" in outcome["alert"]
        assert "削除確認を閉じました" in outcome["alert"]
        assert "削除操作をやり直してください" in outcome["alert"]
        assert outcome["topmost"] == "detail-dialog"
        assert outcome["focused"] == "detail-dialog-body"


def test_failed_dialog_updates_restore_actionable_focus() -> None:
    """更新失敗後は開いたダイアログ内の再操作可能な要素へフォーカスを戻す。"""
    result = _run_node_ui(
        """
fetchHandler = async () => ({
  ok: false, status: 500, statusText: 'Error', json: async () => ({error: '失敗'})
});
const awi = {
  kind: 'awi', state: 'inbox', filename: 'entry.md', answered: null,
  summary: '要約', target_repo: 'example/repo', content: 'raw', body_html: '<p>本文</p>',
  question_type: 'free-form', choices: [], frontmatter_entries: []
};
elements['detail-dialog'].open = true;
dialogStack.push('detail-dialog');
displayEntry(awi);
enterEdit();
elements['edit-content'].value = '更新後';
await saveEntry();
const editFailure = focused;

displayEntry({...awi, kind: 'uwi', filename: 'question.md', answered: false});
elements['answer-input'].value = '回答';
await saveAnswer();
const answerFailure = focused;

elements['create-dialog'].open = true;
dialogStack.push('create-dialog');
elements['create-content'].value = '新規本文';
elements['create-target'].value = 'example/repo';
await createEntry({preventDefault() {}});
const createFailure = focused;

displayEntry(awi);
openDeleteDialog();
await deleteEntry({preventDefault() {}});
const deleteFailure = focused;

process.stdout.write(JSON.stringify({editFailure, answerFailure, createFailure, deleteFailure}));
"""
    )
    assert result == {
        "editFailure": "edit-content",
        "answerFailure": "answer-input",
        "createFailure": "create-content",
        "deleteFailure": "delete-close-button",
    }
