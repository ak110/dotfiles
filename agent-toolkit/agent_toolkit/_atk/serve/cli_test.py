# pylint: disable=function-redefined,pointless-string-statement,undefined-variable,function-redefined,pointless-string-statement,undefined-variable,ungrouped-imports,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# ruff: noqa: E402,F401,F403,F405,I001
# pylint: disable=unused-import,unused-wildcard-import,wildcard-import,wrong-import-order
"""`atk serve`のテスト。"""

# pylint: disable=protected-access

import asyncio
import binascii
import contextlib
import json
import logging
import math
import os
import pathlib
import re
import signal
import struct
import subprocess
import threading
import types
import typing
import zlib

import filelock
import pytest
import watchdog.events

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import assets, config, state
from agent_toolkit._atk.serve import cli as serve
from agent_toolkit._atk.serve import plans as serve_plans
from agent_toolkit._atk.serve import sessions as serve_sessions
from agent_toolkit._atk.wi import common, user_comment
from agent_toolkit._atk.wi import repo as awi_repo

# UI検証で起動する`node`は、CIの実行環境ではmiseのshimとして提供され、版と信頼設定の解決に
# 実行環境のホーム・設定ディレクトリを参照する。conftestの`_isolated_home`が差し替えた環境を
# そのまま渡すとこの解決が失敗するため、取り込み時点の環境変数を控えてNodeへ渡す。
# 同じ目的のconftestの`host_environ` fixtureは使わない。`node`を起動する`_run_node_ui`は
# module levelのヘルパーであり、fixtureを受け取るには全呼び出し元のテストへ引数を追加する必要がある。
_HOST_ENVIRON = dict(os.environ)


from agent_toolkit._atk.serve.test_support_test import *  # noqa: F403


def test_config_precedence_and_platform_ports(tmp_path: pathlib.Path) -> None:
    """設定優先順位とOS別ポートを検証する。"""
    path = tmp_path / "serve.toml"
    path.write_text('host = "toml-host"\nport = 3000\n', encoding="utf-8")
    env = {
        "AGENT_TOOLKIT_SERVE_CONFIG": str(path),
        "AGENT_TOOLKIT_SERVE_HOST": "0.0.0.0",
        "AGENT_TOOLKIT_SERVE_PORT": "4000",
    }
    assert config.resolve_config(environ=env) == config.ServeConfig("0.0.0.0", 4000)
    assert config.resolve_config(host="cli-host", port=5000, environ=env) == config.ServeConfig("cli-host", 5000)
    assert config.default_port("linux") == 28766
    assert config.default_port("win32") == 28876


def test_text_assets_are_bundled_as_plugin_files() -> None:
    """配布対象のscripts配下に実ファイルを同梱し、Python側がその内容を読む。"""
    static_dir = pathlib.Path(assets.__file__).with_name("static")
    expected = {
        "index.html": assets.HTML,
        "app.css": assets.CSS,
        "app.js": assets.JS,
        "shell.js": assets.SHELL_JS,
        "plans.js": assets.PLANS_JS,
        "sessions.js": assets.SESSIONS_JS,
    }
    # Mermaidは容量が大きく要求時に読むため、内容の一致検査ではなく実在だけを確認する。
    assert {path.name for path in static_dir.iterdir()} == {*expected, "vendor"}
    assert (static_dir / "vendor" / "mermaid.min.js").is_file()
    for filename, content in expected.items():
        bundled = (static_dir / filename).read_text(encoding="utf-8")
        assert (bundled if filename == "app.js" else bundled.removesuffix("\n")) == content


def test_assets_define_all_operation_lifecycles_and_message_regions() -> None:
    """6更新操作を共通pending処理へ接続し、結果領域を操作場所ごとに持つ。"""
    assert "async function runPending(" in assets.JS
    for key in ("'sync'", "'save'", "'answer'", "'user-comment'", "'create'", "'delete'"):
        assert f"runPending({key}" in assets.JS
    assert "payload" in assets.JS.partition("async function runPending")[0] or "payload" in assets.JS
    assert ".filter(control => !control.classList.contains('dialog-close'))" in assets.JS
    assert "pendingOperations.has(key)" in assets.JS
    assert "finally {" in assets.JS
    for dialog_name in ("detail", "create", "delete"):
        assert f'id="{dialog_name}-alert"' in assets.HTML
        assert f'id="{dialog_name}-status"' in assets.HTML
    assert 'id="result-status"' in assets.HTML
    assert 'id="list-warning"' in assets.HTML
    assert "showError('')" not in assets.JS


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
const synchronization = elements['refresh-button'].listeners.click();
await Promise.resolve();
const disabled = elements['refresh-button'].disabled;
const duplicateSynchronization = elements['refresh-button'].listeners.click();
setGlobalError('同期中の外部更新失敗');
elements['global-error-close-button'].focus();
elements['global-error-close-button'].listeners.click();
const during = {
  hidden: elements['global-error'].hidden,
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
    hidden: elements['global-error'].hidden
  }
}));
"""
    )
    assert result == {
        "disabled": True,
        "syncCalls": 1,
        "during": {"hidden": True, "focused": "global-error-close-button"},
        "after": {"disabled": False, "focused": "refresh-button", "hidden": True},
    }


def test_assets_keep_terminal_entry_editing_read_only_and_show_identifiers() -> None:
    """終端状態では編集を隠し、削除とkind/state識別子を表示する。"""
    result = _run_node_ui(
        """
displayEntry({
  kind: 'uwi', state: 'adopted', filename: 'done.md', answered: true, answer: '回答',
  summary: '完了', target_repo: 'example/repo', content: 'raw', body_html: '<p>本文</p>',
  question_type: 'free-form', choices: [], frontmatter_entries: []
});
process.stdout.write(JSON.stringify({
  heading: elements['detail-state'].textContent,
  metadata: elements['detail-metadata'].children.map(child => child.children.map(item => item.textContent).join(':')),
  readonly: !elements['readonly-notice'].hidden,
  editHidden: elements['edit-button'].hidden,
  answerHidden: elements['answer-button'].hidden,
  deleteHidden: elements['delete-button'].hidden
}));
"""
    )
    assert result == {
        "heading": "uwi / adopted",
        "metadata": [
            "回答状況:回答済み",
            "対象リポジトリ:example/repo",
            "更新日時:—",
        ],
        "readonly": True,
        "editHidden": True,
        "answerHidden": True,
        "deleteHidden": False,
    }


def test_assets_notify_only_new_active_unanswered_uwi_after_permission() -> None:
    """初期集合と既知項目の属性変化を通知せず、新規未回答UWIだけを通知する。"""
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
    {kind: 'uwi', state: 'adopted', filename: 'reappear.md', answered: true}
  ],
  [
    {kind: 'uwi', state: 'inbox', filename: 'base.md', answered: false, target_repo: 'new/repo'},
    {kind: 'uwi', state: 'inbox', filename: 'answered.md', answered: false},
    {kind: 'uwi', state: 'processing', filename: 'moving.md', answered: false},
    {kind: 'uwi', state: 'inbox', filename: 'new.md', answered: false}
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
await refreshKnownUwis({notify: false});
const afterBaseline = notifications.length;
await refreshKnownUwis({notify: true});
await refreshKnownUwis({notify: true});
process.stdout.write(JSON.stringify({
  buttonVisibleBefore, buttonHiddenAfter, afterBaseline, notifications,
  known: Array.from(knownUwiFilenames).sort()
}));
"""
    )
    assert result == {
        "buttonVisibleBefore": True,
        "buttonHiddenAfter": True,
        "afterBaseline": 0,
        "notifications": [{"title": "新規未回答UWI", "body": "new.md"}],
        "known": ["answered.md", "base.md", "moving.md", "new.md", "reappear.md"],
    }


def test_assets_sse_reconciles_owned_delete_dialog() -> None:
    """SSE移動時は削除確認を閉じ、消失時は親子を閉じて一覧へ戻す。"""
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
renderList();
const origin = elements['entry-list'].children[0].children[0];
displayEntry(inbox);
detailOriginKey = entryKey(inbox);
openDialog(elements['detail-dialog'], origin, elements['detail-dialog-body']);
openDeleteDialog();
let phase = 'moved';
fetchHandler = async url => {
  if (url.includes('/inbox/entry.md')) {
    return {ok: false, status: 404, statusText: 'Not Found', json: async () => ({error: 'not found'})};
  }
  if (phase === 'moved' && url.includes('/processing/entry.md')) {
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
renderList();
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
            "focused": "inbox/remaining.md",
        },
    }


def test_assets_close_delete_confirmation_before_detail_refresh_failure() -> None:
    """一覧反映後に詳細取得が失敗しても、古い削除確認を閉じて親へ復旧手順を示す。"""
    result = _run_node_ui(
        """
async function exercise(updatedEntries, warnings) {
  const original = {
    kind: 'awi', state: 'inbox', filename: 'entry.md', answered: null,
    summary: '変更前の要約', target_repo: 'example/old', content: '変更前本文', body_html: '<p>変更前本文</p>',
    frontmatter_entries: []
  };
  entries = [original];
  renderList();
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
enterAnswer();
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


@pytest.mark.asyncio
async def test_state_ignores_pending_timer_cancelled_by_deadline(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取り消し済みタイマーが発火しても、再設定後の保留通知を重複発行しない。"""
    current = state.ServeState(
        tmp_path,
        debounce_seconds=10.0,
        timer_factory=_FakeTimer,
    )
    current._loop = asyncio.get_running_loop()
    published: list[str] = []
    monkeypatch.setattr(current, "publish", lambda: published.append("changed"))
    event = watchdog.events.FileModifiedEvent(str(tmp_path / "entry.md"))

    current.on_modified(event)
    stale = current._pending_notification
    assert stale is not None
    current.on_modified(event)
    # 取り消しと同時に起動した旧タイマーが発火した状況を再現する。
    stale.function(*stale.args)
    await asyncio.sleep(0)

    assert not published


def test_plan_and_session_api_routes_are_registered(tmp_path: pathlib.Path) -> None:
    """計画ファイル画面とセッション画面のAPI・SSE経路を登録する。"""
    app = _three_screen_app(tmp_path)
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    expected = {
        "/api/plans/files",
        "/api/plans/file",
        "/api/plans/raw",
        "/api/plans/search",
        "/api/plans/host-status",
        "/api/plans/host-info",
        "/api/plans/root-info",
        "/api/plans/root-status",
        "/api/plans/events",
        "/api/sessions/list",
        "/api/sessions/detail",
        "/api/sessions/host-status",
        "/api/sessions/events",
        "/static/plans.js",
        "/static/sessions.js",
        "/static/vendor/mermaid.min.js",
    }
    assert expected <= rules
    # SSEは画面ごとに分ける。単一経路へまとめると別画面の更新でも再読込することになる。
    assert "/api/events" in rules


@pytest.mark.asyncio
async def test_transition_routes_forward_only_supported_explicit_states(tmp_path: pathlib.Path) -> None:
    """各遷移ルートが共通契約の明示状態だけを処理層へ渡す。"""

    class CaptureOperations(serve_app.Operations):
        def __init__(self, private_notes: pathlib.Path) -> None:
            super().__init__(private_notes)
            self.calls: list[tuple[str, list[str], dict[str, object]]] = []

        def transition(self, action: str, filenames: list[str], **kwargs: object) -> list[str]:
            self.calls.append((action, filenames, kwargs))
            return filenames

    operations = CaptureOperations(tmp_path)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
        operations=operations,
    )
    client = app.test_client()
    for action, state_name in (
        ("start-processing", "hold"),
        ("return-to-inbox", "rejected"),
        ("adopt", "hold"),
        ("reject", "hold"),
        ("remove", "hold"),
    ):
        response = await client.post(
            f"/api/entries/{action}",
            json={"filenames": ["entry.md"], "state": state_name},
        )
        assert response.status_code == 200
        assert operations.calls[-1] == (
            action,
            ["entry.md"],
            {
                "note": None,
                "commit": None,
                "target_repo": None,
                "force": False,
                "state": state_name,
                "expected_content": None,
            },
        )

    implicit = await client.post("/api/entries/adopt", json={"filenames": ["entry.md"]})
    assert implicit.status_code == 200
    assert operations.calls[-1] == (
        "adopt",
        ["entry.md"],
        {"note": None, "commit": None, "target_repo": None, "force": False},
    )

    invalid = await client.post(
        "/api/entries/adopt",
        json={"filenames": ["entry.md"], "state": "inbox"},
    )
    assert invalid.status_code == 400


def test_detail_preserves_frontmatter_as_strict_json_values(tmp_path: pathlib.Path) -> None:
    """詳細APIはfrontmatterの入れ子値とYAML固有型を厳格JSON互換値へ変換する。"""
    _write_detail_entry(
        tmp_path,
        "---\n"
        "type: awi\n"
        "target_repo: example/repo\n"
        "source: test\n"
        "binary_value: !!binary |\n"
        "  SGVsbG8=\n"
        "set_value: !!set\n"
        "  alpha: null\n"
        "  beta: null\n"
        "ordered: !!omap\n"
        "  - first: 1\n"
        "  - second: 2\n"
        "nested:\n"
        "  label: value\n"
        "  values:\n"
        "    - one\n"
        "    - two\n"
        "queue_schedule:\n"
        "  timestamp: 2026-08-20T12:34:56Z\n"
        "  day: 2026-08-20\n"
        "  nan: .nan\n"
        "  positive: .inf\n"
        "  negative: -.inf\n"
        "---\n\n本文\n",
    )

    detail = serve_app.Operations(tmp_path).detail("inbox", "entry.md")
    entries = typing.cast(list[dict[str, typing.Any]], detail["frontmatter_entries"])
    frontmatter = {item["key"]["value"]: item["value"] for item in entries}

    json.dumps(detail, ensure_ascii=False, allow_nan=False)
    assert frontmatter["binary_value"] == "SGVsbG8="
    assert frontmatter["set_value"] == ["alpha", "beta"]
    assert frontmatter["ordered"] == [["first", "1"], ["second", "2"]]
    assert frontmatter["nested"] == {"label": "value", "values": ["one", "two"]}
    assert frontmatter["queue_schedule"] == {
        "timestamp": "2026-08-20T12:34:56+00:00",
        "day": "2026-08-20",
        "nan": "NaN",
        "positive": "Infinity",
        "negative": "-Infinity",
    }


def test_detail_with_frontmatter_only_returns_empty_body_html(tmp_path: pathlib.Path) -> None:
    """frontmatterだけの詳細は本文用HTMLを空文字列として返す。"""
    _write_detail_entry(tmp_path, "---\ntype: awi\ntarget_repo: example/repo\n---\n")

    detail = serve_app.Operations(tmp_path).detail("inbox", "entry.md")

    assert detail["body_html"] == ""
    assert '<table class="frontmatter">' in typing.cast(str, detail["content_html"])


@pytest.mark.parametrize(
    ("question_source", "expected_type", "expected_choices"),
    [
        ("question_type: choice\nchoices: 最初, 2番目\n", "choice", ["最初", "2番目"]),
        ("question_type: choice\nchoices:\n  - 最初\n  - 2番目\n", "choice", ["最初", "2番目"]),
        ("question_type: yes-no\nchoices: 無視する\n", "yes-no", []),
        ("question_type: broken\nchoices: [最初, '']\n", "free-form", []),
        ("question_type:\n  - choice\nchoices: [最初, 2番目]\n", "free-form", []),
        ("question_type:\n  nested: choice\nchoices: [最初, 2番目]\n", "free-form", []),
    ],
)
def test_detail_returns_body_only_and_normalized_question_metadata(
    tmp_path: pathlib.Path,
    question_source: str,
    expected_type: str,
    expected_choices: list[str],
) -> None:
    """詳細API用データはfrontmatterを除いた本文と正規化済みの回答形式を返す。"""
    _write_detail_entry(
        tmp_path,
        f"---\ntype: uwi\ntarget_repo: example/repo\n{question_source}---\n\n## 質問\n\n質問本文\n",
    )

    detail = serve_app.Operations(tmp_path).detail("inbox", "entry.md")

    assert detail["question_type"] == expected_type
    assert detail["choices"] == expected_choices
    body_html = typing.cast(str, detail["body_html"])
    assert '<table class="frontmatter">' not in body_html
    assert "target_repo" not in body_html
    assert "質問本文" in body_html


def test_operations_source_empty_filter_returns_items_with_missing_or_empty_source(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`source_empty=true`は空の投入元だけを返し、値あり・非文字列の項目を除外する。"""
    monkeypatch.setattr(common, "repo_lock", lambda *_a, **_k: contextlib.nullcontext())
    monkeypatch.setattr(common, "pull", lambda _path: None)
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "no-source.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n投入元なし\n",
        encoding="utf-8",
    )
    (inbox / "empty-source.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: \n---\n\n空投入元\n",
        encoding="utf-8",
    )
    (inbox / "whitespace-source.md").write_text(
        '---\ntype: awi\ntarget_repo: example/repo\nsource: "   "\n---\n\n空白投入元\n',
        encoding="utf-8",
    )
    (inbox / "with-source.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: web\n---\n\n投入元あり\n",
        encoding="utf-8",
    )
    (inbox / "list-source.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: []\n---\n\nリスト形式の投入元\n",
        encoding="utf-8",
    )
    result, warnings = serve_app.Operations(tmp_path).entries_with_warnings({"source_empty": "true"})
    assert not warnings
    filenames = [typing.cast(str, item["filename"]) for item in result]
    filenames.sort()
    assert filenames == ["empty-source.md", "no-source.md", "whitespace-source.md"]


@pytest.mark.asyncio
async def test_answer_and_remove_apis_target_state_and_keep_legacy_resolution(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """状態指定時は表示対象へ作用し、省略時はprocessing優先を維持する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    for module in (common, serve_app.awi_mutations, serve_app.uwi_mutations):
        monkeypatch.setattr(module, "_repo_lock", lock, raising=False)
        monkeypatch.setattr(module, "_pull", lambda _path: None, raising=False)
        monkeypatch.setattr(module, "_commit_and_push", lambda *_args, **_kwargs: None, raising=False)
        monkeypatch.setattr(module, "_push_pending_commits", lambda _path: None, raising=False)
    inbox = tmp_path / "inbox"
    processing = tmp_path / "processing"
    inbox.mkdir()
    processing.mkdir()
    uwi_content = (
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n"
    )
    awi_content = "---\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n"
    for filename in ("answer-state.md", "answer-legacy.md"):
        (inbox / filename).write_text(uwi_content, encoding="utf-8")
        (processing / filename).write_text(uwi_content, encoding="utf-8")
    for filename in ("remove-state.md", "remove-legacy.md", "remove-protected.md"):
        (inbox / filename).write_text(awi_content, encoding="utf-8")
        (processing / filename).write_text(awi_content, encoding="utf-8")

    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    state_answer = await client.post(
        "/api/entries/answer",
        json={
            "filename": "answer-state.md",
            "state": "inbox",
            "answer": "未処理側への回答",
            "expected_content": uwi_content,
        },
    )
    assert state_answer.status_code == 200
    assert (inbox / "answer-state.md").read_text(encoding="utf-8").endswith("未処理側への回答\n")
    assert (processing / "answer-state.md").read_text(encoding="utf-8") == uwi_content

    legacy_answer = await client.post(
        "/api/entries/answer",
        json={"filename": "answer-legacy.md", "answer": "従来経路の回答"},
    )
    assert legacy_answer.status_code == 200
    assert (inbox / "answer-legacy.md").read_text(encoding="utf-8") == uwi_content
    assert (processing / "answer-legacy.md").read_text(encoding="utf-8").endswith("従来経路の回答\n")

    state_remove = await client.post(
        "/api/entries/remove",
        json={
            "filenames": ["remove-state.md"],
            "state": "inbox",
            "expected_content": awi_content,
            "force": False,
        },
    )
    assert state_remove.status_code == 200
    assert not (inbox / "remove-state.md").exists()
    assert (processing / "remove-state.md").is_file()

    protected = await client.post(
        "/api/entries/remove",
        json={"filenames": ["remove-protected.md"], "force": False},
    )
    assert protected.status_code == 400
    assert await protected.get_json() == {"error": "指定したエントリを操作できません"}

    legacy_remove = await client.post(
        "/api/entries/remove",
        json={"filenames": ["remove-legacy.md"], "force": True},
    )
    assert legacy_remove.status_code == 200
    assert (inbox / "remove-legacy.md").is_file()
    assert not (processing / "remove-legacy.md").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 100, 101])
async def test_entries_api_paginates_at_one_hundred_entries(tmp_path: pathlib.Path, count: int) -> None:
    """一覧APIは明示ページだけを100件単位で返し、総数境界と空一覧を正規化する。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for index in range(count):
        (inbox / f"entry-{index:03d}.md").write_text(
            "---\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n",
            encoding="utf-8",
        )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().get("/api/entries?status=inbox&page=1")

    assert response.status_code == 200
    payload = await response.get_json()
    expected_page_count = max(1, math.ceil(count / 100))
    assert len(payload["entries"]) == min(count, 100)
    assert payload["pagination"] == {
        "page": 1,
        "page_size": 100,
        "page_count": expected_page_count,
        "total_count": count,
    }
    if count == 101:
        second = await app.test_client().get("/api/entries?status=inbox&page=2")
        second_payload = await second.get_json()
        assert len(second_payload["entries"]) == 1
        assert second_payload["pagination"]["page"] == 2


@pytest.mark.asyncio
async def test_entries_api_filters_awi_plan_type_and_keeps_warnings_full_scan(tmp_path: pathlib.Path) -> None:
    """awiの通常型・計画型を独立条件で限定し、ページ警告は全走査分を返す。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "normal.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n通常\n",
        encoding="utf-8",
    )
    (inbox / "plan.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nplan_file: /tmp/plan.md\n---\n\n計画\n",
        encoding="utf-8",
    )
    (inbox / "question.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n質問\n",
        encoding="utf-8",
    )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    normal = await client.get("/api/entries?status=inbox&type=awi&plan=normal&page=1")
    planned = await client.get("/api/entries?status=inbox&type=all&plan=plan&page=1")

    assert [item["filename"] for item in (await normal.get_json())["entries"]] == ["normal.md"]
    assert [item["filename"] for item in (await planned.get_json())["entries"]] == ["plan.md"]
    assert (await normal.get_json())["entries"][0]["plan"] is False
    assert (await planned.get_json())["entries"][0]["plan"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/entries", {"type": "awi", "messages": ["awi"], "target_repo": None}),
        (
            "/api/entries",
            {
                "type": "uwi",
                "messages": ["UWIですか？"],
                "scope": "test",
                "question_type": "free-form",
                "target_repo": None,
            },
        ),
    ],
)
async def test_web_add_mutations_reject_null_target_repo(
    tmp_path: pathlib.Path,
    path: str,
    payload: dict[str, object],
) -> None:
    """`target_repo`キーが存在しても値が`null`の場合は必須検証を回避できず400になる。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(path, json=payload)
    assert response.status_code == 400
    assert "target_repo" in (await response.get_json())["error"]


def test_create_app_keeps_resolved_config(tmp_path: pathlib.Path) -> None:
    """解決済み設定と状態をapp.configへ保持する。"""
    resolved = config.ServeConfig("127.0.0.1", 28766)
    current_state = state.ServeState(tmp_path)
    app = serve_app.create_app(tmp_path, resolved, current_state)
    assert app.config["SERVE_CONFIG"] == resolved
    assert app.config["SERVE_STATE"] is current_state


@pytest.mark.asyncio
async def test_manifest_declares_svg_icon(tmp_path: pathlib.Path) -> None:
    """manifestのiconsがSVG1件を宣言し、従来のPNGも引き続き配信する。"""
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), state.ServeState(tmp_path))
    client = app.test_client()
    manifest_response = await client.get("/manifest.webmanifest")
    assert manifest_response.content_type == "application/manifest+json"
    assert manifest_response.headers["Cache-Control"] == "no-cache"
    manifest = await manifest_response.get_json()
    assert manifest == {
        "name": "ワークアイテム",
        "short_name": "atk serve",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "theme_color": assets.THEME_COLOR,
        "background_color": assets.THEME_COLOR,
        "icons": [
            {
                "src": "/favicon.svg",
                "sizes": "192x192 512x512 any",
                "type": "image/svg+xml",
                "purpose": "any",
            }
        ],
    }

    for size in (192, 512):
        response = await client.get(f"/static/icon-{size}.png")
        body = await response.get_data()
        assert isinstance(body, bytes)
        assert response.content_type == "image/png"
        assert body.startswith(b"\x89PNG\r\n\x1a\n")
        assert response.headers["Cache-Control"] == "public, max-age=31536000, immutable"

        offset = 8
        chunks: list[tuple[bytes, bytes]] = []
        while offset < len(body):
            length = struct.unpack_from(">I", body, offset)[0]
            chunk_type = body[offset + 4 : offset + 8]
            chunk_data = body[offset + 8 : offset + 8 + length]
            stored_crc = struct.unpack_from(">I", body, offset + 8 + length)[0]
            assert binascii.crc32(chunk_type + chunk_data) & 0xFFFFFFFF == stored_crc
            chunks.append((chunk_type, chunk_data))
            offset += 12 + length
        assert offset == len(body)
        assert [chunk_type for chunk_type, _ in chunks] == [b"IHDR", b"IDAT", b"IEND"]

        width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
            ">IIBBBBB",
            chunks[0][1],
        )
        assert (width, height) == (size, size)
        assert (bit_depth, color_type, compression, filtering, interlace) == (8, 6, 0, 0, 0)
        raw = zlib.decompress(b"".join(data for chunk_type, data in chunks if chunk_type == b"IDAT"))
        row_size = 1 + size * 4
        assert len(raw) == size * row_size
        expected_pixel = bytes.fromhex(assets.THEME_COLOR.removeprefix("#")) + b"\xff"
        for row_start in range(0, len(raw), row_size):
            assert raw[row_start] == 0
            assert raw[row_start + 1 : row_start + row_size] == expected_pixel * size


def test_repository_readers_canonicalize_legacy_paths_once_per_operation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一覧フィルターと候補収集は旧パス形を統合し、解決不能値の原値を保持する。"""
    local_repo = tmp_path / "target-repo"
    local_repo.mkdir()
    _write_repo_entry(tmp_path, "inbox", "current.md", "github.com/example/repo")
    _write_repo_entry(tmp_path, "inbox", "legacy-a.md", str(local_repo))
    _write_repo_entry(tmp_path, "inbox", "legacy-b.md", str(local_repo))
    _write_repo_entry(tmp_path, "inbox", "other.md", "github.com/example/other")
    _write_repo_entry(tmp_path, "inbox", "missing.md", str(tmp_path / "missing"))
    _write_repo_entry(tmp_path, "inbox", "unresolved.md", "example/repo")
    resolved_paths: list[str] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        resolved_paths.append(cmd[2])
        return subprocess.CompletedProcess(cmd, 0, stdout="https://github.com/example/repo.git\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    operations = serve_app.Operations(tmp_path)

    url_entries, warnings = operations.entries_with_warnings({"target_repo": "github.com/example/repo"})
    assert [item["filename"] for item in url_entries] == ["legacy-b.md", "legacy-a.md", "current.md"]
    assert not warnings
    assert resolved_paths == [str(local_repo)]

    resolved_paths.clear()
    path_entries, warnings = operations.entries_with_warnings({"target_repo": str(local_repo)})
    assert [item["filename"] for item in path_entries] == ["legacy-b.md", "legacy-a.md", "current.md"]
    assert not warnings
    assert resolved_paths == [str(local_repo)]

    resolved_paths.clear()
    assert operations.target_repos() == [
        str(tmp_path / "missing"),
        "example/repo",
        "github.com/example/other",
        "github.com/example/repo",
    ]
    assert resolved_paths == [str(local_repo)]

    unresolved_entries, warnings = operations.entries_with_warnings({"target_repo": "example/repo"})
    assert [item["filename"] for item in unresolved_entries] == ["unresolved.md"]
    assert not warnings

    unfiltered_entries, warnings = operations.entries_with_warnings({})
    assert not warnings
    assert next(item for item in unfiltered_entries if item["filename"] == "missing.md")["target_repo"] == str(
        tmp_path / "missing"
    )


def test_assets_search_fallback_obeys_boundaries_and_keeps_filters() -> None:
    """検索結果0件時の補助検索を境界値、通常一致、検索語なしで検証する。"""
    result = _run_node_ui(
        """
const fallbackNotice =
  '状態などの条件では一致しなかったため、検索欄の条件だけで見つかった項目を表示しています。' +
  'フィルターの選択値は変更していません。';
const initialWarnings = [{filename: 'initial.md', reason: '初回警告'}];
const runCase = async (token, count) => {
  fetchCalls.length = 0;
  elements['search-input'].value = token;
  elements['kind-filter'].value = 'all';
  elements['state-filter'].value = 'active';
  elements['answer-filter'].value = 'all';
  elements['target-filter'].value = '';
  elements['source-filter'].value = '';
  const fallbackEntries = Array.from({length: count}, (_, index) => ({
    kind: 'awi', state: 'adopted', filename: `${token}-${index}.md`, summary: token
  }));
  fetchHandler = async url => {
    if (url.includes('status=active')) {
      return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: initialWarnings})};
    }
    if (url === `/atk/api/entries?q=${token}&page=1`) {
      return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: fallbackEntries, warnings: []})};
    }
    throw new Error('想定外のURL: ' + url);
  };
  await loadEntries({announce: true});
  return {
    count,
    rows: entries.map(entry => entry.filename),
    notice: elements['list-fallback-notice'].textContent,
    noticeHidden: elements['list-fallback-notice'].hidden,
    status: elements['result-status'].textContent,
    warning: elements['list-warning'].textContent,
    filters: {
      kind: elements['kind-filter'].value,
      state: elements['state-filter'].value,
      answer: elements['answer-filter'].value,
      target: elements['target-filter'].value,
      source: elements['source-filter'].value
    },
    urls: fetchCalls.map(call => call.url)
  };
};
const one = await runCase('one', 1);
const five = await runCase('five', 5);
const none = await runCase('none', 0);
const six = await runCase('six', 6);

fetchCalls.length = 0;
elements['search-input'].value = 'normal';
fetchHandler = async url => {
  if (!url.includes('/api/entries?')) throw new Error('想定外のURL: ' + url);
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({
    entries: [{kind: 'awi', state: 'inbox', filename: 'normal.md', summary: 'normal'}], warnings: []
  })};
};
await loadEntries({announce: true});
const normal = {
  rows: entries.map(entry => entry.filename),
  noticeHidden: elements['list-fallback-notice'].hidden,
  urls: fetchCalls.map(call => call.url)
};

fetchCalls.length = 0;
elements['search-input'].value = '';
fetchHandler = async url => {
  if (!url.includes('/api/entries?')) throw new Error('想定外のURL: ' + url);
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})};
};
await loadEntries({announce: true});
const emptySearch = {
  rows: entries.map(entry => entry.filename),
  noticeHidden: elements['list-fallback-notice'].hidden,
  urls: fetchCalls.map(call => call.url)
};

fetchCalls.length = 0;
elements['search-input'].value = 'all-filters-only';
elements['kind-filter'].value = 'all';
elements['state-filter'].value = 'all';
elements['answer-filter'].value = 'all';
elements['target-filter'].value = '';
elements['source-filter'].value = '';
fetchHandler = async url => {
  if (url !== '/atk/api/entries?type=all&status=all&answered=all&q=all-filters-only&page=1') {
    throw new Error('想定外のURL: ' + url);
  }
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})};
};
await loadEntries({announce: true});
const allFilters = {
  rows: entries.map(entry => entry.filename),
  noticeHidden: elements['list-fallback-notice'].hidden,
  urls: fetchCalls.map(call => call.url)
};
process.stdout.write(JSON.stringify({one, five, none, six, normal, emptySearch, allFilters, fallbackNotice}));
"""
    )
    expected_notice = (
        "状態などの条件では一致しなかったため、検索欄の条件だけで見つかった項目を表示しています。"
        "フィルターの選択値は変更していません。"
    )
    assert result["one"]["rows"] == ["one-0.md"]
    assert result["five"]["rows"] == [f"five-{index}.md" for index in range(5)]
    for name in ("one", "five"):
        assert result[name]["notice"] == expected_notice
        assert result[name]["noticeHidden"] is False
        assert result[name]["warning"] == ""
        assert result[name]["urls"] == [
            f"/atk/api/entries?type=all&status=active&answered=all&q={name}&page=1",
            f"/atk/api/entries?q={name}&page=1",
        ]
        assert result[name]["filters"] == {
            "kind": "all",
            "state": "active",
            "answer": "all",
            "target": "",
            "source": "",
        }
    for name in ("none", "six"):
        assert result[name]["rows"] == []
        assert result[name]["notice"] == ""
        assert result[name]["noticeHidden"] is True
        assert result[name]["warning"] == "一覧から除外したファイル: initial.md（初回警告）"
        assert result[name]["status"] == "一致する項目はありません"
        assert result[name]["urls"] == [
            f"/atk/api/entries?type=all&status=active&answered=all&q={name}&page=1",
            f"/atk/api/entries?q={name}&page=1",
        ]
    assert result["normal"] == {
        "rows": ["normal.md"],
        "noticeHidden": True,
        "urls": ["/atk/api/entries?type=all&status=active&answered=all&q=normal&page=1"],
    }
    assert result["emptySearch"] == {
        "rows": [],
        "noticeHidden": True,
        "urls": ["/atk/api/entries?type=all&status=active&answered=all&page=1"],
    }
    assert result["allFilters"] == {
        "rows": [],
        "noticeHidden": True,
        "urls": ["/atk/api/entries?type=all&status=all&answered=all&q=all-filters-only&page=1"],
    }
    assert result["fallbackNotice"] == expected_notice


def test_user_comment_pure_update_preserves_frontmatter_and_previous_body() -> None:
    """予約節の置換でfrontmatterと通常本文を保持し、再抽出できる。"""
    original = _session_review_awi("通常本文\n\n## 実装メモ\n\n既存の見出し本文\n\n## ユーザーコメント\n\n旧コメント\n")

    updated = user_comment.update_user_comment(original, "\n\n新しいコメント\n\n2行目\n\n")

    assert updated == _session_review_awi(
        "通常本文\n\n## 実装メモ\n\n既存の見出し本文\n\n## ユーザーコメント\n\n新しいコメント\n\n2行目\n"
    )
    assert user_comment.extract_user_comment(updated) == "新しいコメント\n\n2行目"


@pytest.mark.asyncio
async def test_user_comment_api_appends_and_replaces_inbox_and_hold_session_review_awi(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """専用APIがinboxとholdの対象本文へ追記し、同じ節だけを置換する。"""
    _patch_comment_edit_dependencies(monkeypatch)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    original = _session_review_awi("通常本文\n")
    path = inbox / "awi.md"
    path.write_text(original, encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    detail_response = await client.get("/api/entries/inbox/awi.md")
    detail = await detail_response.get_json()
    assert detail_response.status_code == 200
    assert detail["entry"]["user_comment"] is None
    assert detail["entry"]["user_comment_editable"] is True

    first = await client.post(
        "/api/entries/user-comment",
        json={
            "state": "inbox",
            "filename": "awi.md",
            "comment": "最初のコメント",
            "expected_content": original,
        },
    )
    assert first.status_code == 200
    assert await first.get_json() == {"changed": True}
    after_first = path.read_text(encoding="utf-8")
    assert after_first == _session_review_awi("通常本文\n\n## ユーザーコメント\n\n最初のコメント\n")

    second = await client.post(
        "/api/entries/user-comment",
        json={
            "state": "inbox",
            "filename": "awi.md",
            "comment": "置換後のコメント",
            "expected_content": after_first,
        },
    )
    assert second.status_code == 200
    assert await second.get_json() == {"changed": True}
    after_second = path.read_text(encoding="utf-8")
    assert after_second == _session_review_awi("通常本文\n\n## ユーザーコメント\n\n置換後のコメント\n")

    final_detail = await (await client.get("/api/entries/inbox/awi.md")).get_json()
    assert final_detail["entry"]["user_comment"] == "置換後のコメント"

    hold = tmp_path / "hold"
    hold.mkdir()
    held_path = hold / "held.md"
    held_path.write_text(original, encoding="utf-8")
    held_detail = await (await client.get("/api/entries/hold/held.md")).get_json()
    assert held_detail["entry"]["user_comment_editable"] is True
    held = await client.post(
        "/api/entries/user-comment",
        json={
            "state": "hold",
            "filename": "held.md",
            "comment": "保留中のコメント",
            "expected_content": original,
        },
    )
    assert held.status_code == 200
    assert user_comment.extract_user_comment(held_path.read_text(encoding="utf-8")) == "保留中のコメント"
