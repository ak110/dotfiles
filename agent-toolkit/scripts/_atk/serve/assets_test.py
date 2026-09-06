# pylint: disable=duplicate-code,function-redefined,pointless-string-statement,undefined-variable,duplicate-code,function-redefined,pointless-string-statement,undefined-variable,ungrouped-imports,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
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

from _atk.serve import app as serve_app
from _atk.serve import assets, config, state
from _atk.serve import cli as serve
from _atk.serve import plans as serve_plans
from _atk.serve import sessions as serve_sessions
from _atk.wi import common, user_comment
from _atk.wi import repo as awi_repo

# UI検証で起動する`node`は、CIの実行環境ではmiseのshimとして提供され、版と信頼設定の解決に
# 実行環境のホーム・設定ディレクトリを参照する。conftestの`_isolated_home`が差し替えた環境を
# そのまま渡すとこの解決が失敗するため、取り込み時点の環境変数を控えてNodeへ渡す。
# 同じ目的のconftestの`host_environ` fixtureは使わない。`node`を起動する`_run_node_ui`は
# module levelのヘルパーであり、fixtureを受け取るには全呼び出し元のテストへ引数を追加する必要がある。
_HOST_ENVIRON = dict(os.environ)


from _atk.serve.test_support_test import *  # noqa: F403


def test_web_transition_warns_and_records_unverified_commit(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Web採否はcloneを探索せず、警告後に指定revisionを記録する。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "awi.md").write_text(
        "---\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n本文\n",
        encoding="utf-8",
    )
    mutations = serve_app.awi_mutations
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)

    result = serve_app.Operations(tmp_path).transition("adopt", ["awi.md"], commit="abcdef1")

    assert result == ["awi.md"]
    assert "- 対応commit: abcdef1" in (tmp_path / "adopted/awi.md").read_text(encoding="utf-8")
    assert "github.com/example/foo" in capsys.readouterr().err


def test_assets_use_single_cli_ordered_list_and_current_terms() -> None:
    """単一一覧のCLI準拠列順、件数、識別子表示を固定する。"""
    assert assets.HTML.count('<ul id="entry-list"') == 1
    assert "other-entry-list" not in assets.HTML
    columns = re.search(r'<div class="entry-columns"[^>]*>(.*?)</div>', assets.HTML, re.DOTALL)
    assert columns is not None
    assert re.findall(r"<span>(.*?)</span>", columns.group(1)) == [
        "ファイル名",
        "対象リポジトリ",
        "種別・状態",
        "要約",
    ]
    assert "未回答UWI 0件" in assets.HTML
    assert "種別・状態・回答状況" not in assets.HTML
    assert ">確認事項<" not in assets.HTML
    assert assets.HTML.count(">uwi<") == 2
    assert ">今すぐ同期<" in assets.HTML
    assert 'placeholder="本文・ファイル名・対象・投入元を検索"' in assets.HTML
    source_filter = re.search(r'<select id="source-filter">(.*?)</select>', assets.HTML, re.DOTALL)
    assert source_filter is not None
    assert re.findall(r'<option value="([^"]*)">(.*?)</option>', source_filter.group(1)) == [
        ("", "すべて"),
        ("human", "human"),
        ("agent", "agent"),
    ]
    assert "source-empty-filter" not in assets.HTML
    assert "dataset.unansweredUwi" in assets.JS
    assert "種別不明" in assets.JS

    grid = re.search(r"\.entry-columns, \.entry-row \{(.*?)\n\}", assets.CSS, re.DOTALL)
    assert grid is not None
    template = re.search(r"grid-template-columns:(.*?);", grid.group(1), re.DOTALL)
    assert template is not None
    widths = re.findall(r"minmax\([^)]+\)|\d+rem|auto", template.group(1))
    assert widths == [
        "15rem",
        "14rem",
        "15rem",
        "minmax(0, 1fr)",
        "auto",
    ]
    assert "grid-column: 1 / 5;" in assets.CSS
    assert "grid-template-columns: subgrid;" in assets.CSS
    assert ".entry-copy { grid-column: 5;" in assets.CSS


def test_assets_render_single_list_warnings_and_filter_dependencies() -> None:
    """一覧警告、種別不明、件数通知、成立しないフィルター組合せの解除を検証する。"""
    result = _run_node_ui(
        """
entries = [
  {kind: 'uwi', state: 'inbox', filename: 'u.md', answered: false, summary: '未回答', target_repo: 'x/u'},
  {kind: 'unknown', state: 'inbox', filename: 'x.md', answered: null, summary: '不明', target_repo: 'x/u'},
  {kind: 'awi', state: 'inbox', filename: 'f.md', answered: null, plan: true, summary: '本文',
   target_repo: 'github.com/example/a-very-long-repository-name',
   updated_at: '2026-08-07T10:11:00+00:00'}
];
renderList([{filename: 'bad.md', reason: 'UTF-8として読み取れません'}], true);
const announced = elements['result-status'].textContent;
const warning = elements['list-warning'].textContent;
  const awiCells = elements['entry-list'].children[2].children[0].children;
  const kindState = awiCells[2].children.map(child => child.textContent);
const targetCell = awiCells[1];
  const summary = awiCells[3].textContent;
elements['kind-filter'].value = 'awi';
elements['answer-filter'].value = 'no';
elements['source-filter'].value = 'agent';
syncFilterDependencies();
elements['result-status'].textContent = '変更しない';
renderList([], false);
process.stdout.write(JSON.stringify({
  keys: elements['entry-list'].children.map(item => item.children[0].dataset.key),
  unanswered: elements['entry-list'].children[0].children[0].dataset.unansweredUwi,
  unknownKind: elements['entry-list'].children[1].children[0].dataset.kind,
  count: elements['entry-count'].textContent,
  warning,
  announced,
  kindState,
  targetLabel: targetCell.textContent,
  targetAria: targetCell.attributes['aria-label'],
  rowAria: elements['entry-list'].children[2].children[0].attributes['aria-label'],
  summary,
  sseStatus: elements['result-status'].textContent,
  answerValue: elements['answer-filter'].value,
  answerDisabled: elements['answer-filter'].disabled,
  sourceValue: elements['source-filter'].value,
  sourceDisabled: elements['source-filter'].disabled
}));
"""
    )
    assert result == {
        "keys": ["inbox/u.md", "inbox/x.md", "inbox/f.md"],
        "unanswered": "true",
        "unknownKind": "unknown",
        "count": "3件（未回答UWI 1件）",
        "warning": "一覧から除外したファイル: bad.md（UTF-8として読み取れません）",
        "announced": "3件を表示",
        "kindState": ["awi", "inbox", "plan"],
        "targetLabel": "github.co…itory-name",
        "targetAria": "対象リポジトリ: github.com/example/a-very-long-repository-name",
        "rowAria": "f.md、github.com/example/a-very-long-repository-name、awi、inbox、plan、本文",
        "summary": "本文",
        "sseStatus": "変更しない",
        "answerValue": "all",
        "answerDisabled": True,
        "sourceValue": "agent",
        "sourceDisabled": False,
    }


def test_assets_render_all_frontmatter_without_repeating_detail_badges() -> None:
    """詳細メタデータは種別・状態を除き、任意の入れ子値を構造付きで表示する。"""
    result = _run_node_ui(
        """
displayEntry({
  kind: 'awi', state: 'inbox', filename: 'entry.md', answered: null,
  target_repo: 'legacy/repo', source: 'legacy',
  frontmatter_entries: [
    {key: {type: 'str', value: 'type'}, value: 'awi'},
    {key: {type: 'str', value: 'target_repo'}, value: 'example/repo'},
    {key: {type: 'str', value: 'source'}, value: 'web'},
    {key: {type: 'str', value: 'priority'}, value: 'high'},
    {key: {type: 'int', value: 1}, value: 'numeric'},
    {key: {type: 'str', value: '1'}, value: 'textual'},
    {key: {type: 'str', value: 'nested'}, value: {branch: 'main', flags: ['x']}},
    {key: {type: 'str', value: 'values'}, value: ['one', {enabled: true}]},
  ], body_html: '<p>本文</p>'
});
const items = elements['detail-metadata'].children.map(item => ({
  label: item.children[0].textContent,
  value: item.children[1].textContent,
  className: item.className
}));
process.stdout.write(JSON.stringify({items, heading: elements['detail-state'].textContent}));
"""
    )
    assert result["heading"] == "awi / inbox"
    assert [item["label"] for item in result["items"]] == [
        "対象リポジトリ",
        "投入元",
        "priority",
        "int: 1",
        "1",
        "nested",
        "values",
        "更新日時",
    ]
    assert [item["className"] for item in result["items"]] == ["metadata-item"] * 8
    assert result["items"][0]["value"] == "example/repo"
    assert result["items"][1]["value"] == "web"
    assert result["items"][2]["value"] == "high"
    assert result["items"][3]["value"] == "numeric"
    assert result["items"][4]["value"] == "textual"
    assert result["items"][5]["value"] == '{\n  "branch": "main",\n  "flags": [\n    "x"\n  ]\n}'
    assert result["items"][6]["value"] == '[\n  "one",\n  {\n    "enabled": true\n  }\n]'
    assert all("[object Object]" not in item["value"] for item in result["items"])


def test_assets_sse_detail_tracks_state_and_discards_stale_response() -> None:
    """SSE詳細は状態移動を追跡し、別項目選択後の古い応答を破棄する。"""
    result = _run_node_ui(
        """
const inbox = {
  kind: 'awi', state: 'inbox', filename: 'entry.md', answered: null,
  content: '移動前本文', body_html: '<p>移動前本文</p>', frontmatter_entries: []
};
const processing = {
  ...inbox, state: 'processing', content: '移動後本文', body_html: '<p>移動後本文</p>'
};
const second = {
  kind: 'awi', state: 'inbox', filename: 'second.md', answered: null,
  content: '別項目本文', body_html: '<p>別項目本文</p>', frontmatter_entries: []
};
displayEntry(inbox);
detailOriginKey = entryKey(inbox);
elements['detail-dialog'].open = true;
entries = [processing];
fetchHandler = async url => ({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({entry: url.endsWith('/entry.md') ? processing : second})
});
await reloadOpenDetailFromExternalChange();
const moved = {
  state: currentEntry.state,
  content: elements['detail-content'].innerHTML,
  key: detailOriginKey
};

let resolveStale;
let markStaleStarted;
const staleStarted = new Promise(resolve => { markStaleStarted = resolve; });
fetchHandler = async url => {
  if (url.endsWith('/entry.md')) return new Promise(resolve => {
    resolveStale = resolve;
    markStaleStarted();
  });
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entry: second})};
};
const stale = reloadOpenDetailFromExternalChange();
await staleStarted;
await selectEntry(second, new Element('second-origin', 'BUTTON'));
resolveStale({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({entry: {...processing, content: '遅延本文', body_html: '<p>遅延本文</p>'}})
});
await stale;
process.stdout.write(JSON.stringify({
  moved,
  final: {filename: currentEntry.filename, content: elements['detail-content'].innerHTML, key: detailOriginKey}
}));
"""
    )
    assert result == {
        "moved": {
            "state": "processing",
            "content": "<p>移動後本文</p>",
            "key": "processing/entry.md",
        },
        "final": {
            "filename": "second.md",
            "content": "<p>別項目本文</p>",
            "key": "inbox/second.md",
        },
    }


def test_assets_preserve_external_update_recovery_across_operation_failures() -> None:
    """外部更新後の一般失敗と競合は復旧手順を保ち、単独の権限拒否は再試行できる。"""
    result = _run_node_ui(
        """
async function exercise(kind, status, code, withExternalUpdate) {
  const answering = kind === 'answer';
  let serverEntry = {
    kind: answering ? 'uwi' : 'awi', state: 'inbox',
    filename: answering ? 'question.md' : 'entry.md', answered: answering ? false : null,
    summary: '操作対象', content: '更新前', body_html: '<p>更新前</p>',
    question_type: 'free-form', choices: [], frontmatter_entries: []
  };
  displayEntry(serverEntry);
  detailOriginKey = entryKey(serverEntry);
  openDialog(elements['detail-dialog'], new Element(`${kind}-origin`, 'BUTTON'), elements['detail-dialog-body']);
  if (answering) {
    enterAnswer();
    elements['answer-input'].value = '利用者の回答';
  } else {
    enterEdit();
    elements['edit-content'].value = '利用者の保存本文';
  }
  let resolveMutation;
  let markStarted;
  const started = new Promise(resolve => { markStarted = resolve; });
  fetchHandler = async (url, options) => {
    const isMutation = answering
      ? url.endsWith('/api/entries/answer') && options.method === 'POST'
      : options.method === 'PUT';
    if (isMutation) return new Promise(resolve => {
      resolveMutation = resolve;
      markStarted();
    });
    if (url.includes('/api/entries?')) {
      return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [serverEntry], warnings: []})};
    }
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entry: serverEntry})};
  };
  const pending = answering ? saveAnswer() : saveEntry();
  await started;
  if (withExternalUpdate) {
    serverEntry = {...serverEntry, content: '外部更新後', body_html: '<p>外部更新後</p>'};
    await reloadOpenDetailFromExternalChange();
  }
  const payload = {error: status === 403 ? '権限がありません' : '一般失敗'};
  if (code) payload.code = code;
  resolveMutation({ok: false, status, statusText: 'Error', json: async () => payload});
  await pending;
  const button = elements[answering ? 'save-answer-button' : 'save-entry-button'];
  const input = elements[answering ? 'answer-input' : 'edit-content'];
  const outcome = {
    alert: elements['detail-alert'].textContent,
    disabled: button.disabled,
    input: input.value,
    mode: currentDetailMode()
  };
  closeDetailDialog();
  return outcome;
}

const saveGeneral = await exercise('save', 500, null, true);
const answerGeneral = await exercise('answer', 500, null, true);
const saveConflict = await exercise('save', 409, 'edit_conflict', false);
const answerConflict = await exercise('answer', 409, 'edit_conflict', false);
const savePermission = await exercise('save', 403, null, false);
const answerPermission = await exercise('answer', 403, null, false);
process.stdout.write(JSON.stringify({
  saveGeneral, answerGeneral, saveConflict, answerConflict, savePermission, answerPermission
}));
"""
    )
    for name in ("saveGeneral", "answerGeneral", "saveConflict", "answerConflict"):
        assert "詳細を閉じて開き直してから保存してください" in result[name]["alert"]
        assert result[name]["disabled"] is True
        assert result[name]["mode"] == ("answer" if name.startswith("answer") else "edit")
    assert "保存できませんでした。 一般失敗" in result["saveGeneral"]["alert"]
    assert "回答できませんでした。 一般失敗" in result["answerGeneral"]["alert"]
    assert result["saveGeneral"]["input"] == "利用者の保存本文"
    assert result["answerGeneral"]["input"] == "利用者の回答"
    assert result["savePermission"] == {
        "alert": "inbox/entry.mdを保存できませんでした。 権限がありません",
        "disabled": False,
        "input": "利用者の保存本文",
        "mode": "edit",
    }
    assert result["answerPermission"] == {
        "alert": "inbox/question.mdへ回答できませんでした。 権限がありません",
        "disabled": False,
        "input": "利用者の回答",
        "mode": "answer",
    }


def test_state_keeps_latest_event(tmp_path: pathlib.Path) -> None:
    """状態管理を構築できることを検証する。"""
    current = state.ServeState(tmp_path)
    assert current.root == tmp_path


@pytest.mark.asyncio
async def test_state_publishes_once_after_last_change(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """閾値内の連続変更をまとめ、最後の変更後に1回だけ通知する。"""
    current = state.ServeState(tmp_path, debounce_seconds=0.03)
    current._loop = asyncio.get_running_loop()
    published: list[str] = []
    monkeypatch.setattr(current, "publish", lambda: published.append("changed"))
    event = watchdog.events.FileModifiedEvent(str(tmp_path / "entry.md"))

    current.on_modified(event)
    await asyncio.sleep(0.01)
    current.on_modified(event)
    await asyncio.sleep(0.01)
    current.on_modified(event)

    await asyncio.sleep(0.02)
    assert not published
    await asyncio.sleep(0.03)
    assert published == ["changed"]


@pytest.mark.asyncio
async def test_state_discards_pending_notification_when_stopped(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """監視停止時は保留中の通知を破棄する。"""
    current = state.ServeState(tmp_path, debounce_seconds=0.02)
    current._loop = asyncio.get_running_loop()
    published: list[str] = []
    monkeypatch.setattr(current, "publish", lambda: published.append("changed"))
    monkeypatch.setattr(current.observer, "stop", lambda: None)
    monkeypatch.setattr(current.observer, "join", lambda: None)

    current.on_modified(watchdog.events.FileModifiedEvent(str(tmp_path / "entry.md")))
    current.stop()
    await asyncio.sleep(0.04)

    assert not published


def test_navigation_offers_three_screens_in_declared_order(tmp_path: pathlib.Path) -> None:
    """3画面のページ経路とナビゲーションの表示順・表記を固定する。"""
    app = _three_screen_app(tmp_path)
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert {"/", "/plans", "/sessions"} <= rules
    for document in (assets.HTML, assets.PLANS_HTML, assets.SESSIONS_HTML):
        navigation = re.search(r'<nav class="app-nav"[^>]*>(.*?)</nav>', document, re.DOTALL)
        assert navigation is not None
        assert re.findall(r">([^<>]+)</a>", navigation.group(1)) == ["ワークアイテム", "計画ファイル", "セッション"]
        assert re.findall(r'href="__BASE_PATH_HTML__(/[a-z]*)"', navigation.group(1)) == ["/", "/plans", "/sessions"]
    # 現在の画面だけが`aria-current`を持つ。
    assert assets.HTML.count('aria-current="page"') == 1
    assert assets.PLANS_HTML.count('aria-current="page"') == 1
    assert assets.SESSIONS_HTML.count('aria-current="page"') == 1


@pytest.mark.asyncio
async def test_plan_and_session_apis_classify_input_errors(tmp_path: pathlib.Path) -> None:
    """追加した2画面のAPIが既存と同じ応答分類（入力不正400・未検出404）を返す。"""
    app = _three_screen_app(tmp_path)
    client = app.test_client()
    assert (await client.get("/api/plans/file")).status_code == 400
    assert (await client.get("/api/plans/file?path=a.md&host=unknown-host")).status_code == 400
    assert (await client.get("/api/sessions/detail")).status_code == 400
    assert (await client.get("/api/sessions/detail?engine=claude&path=../etc/passwd.jsonl")).status_code == 404
    assert (await client.get("/api/sessions/detail?engine=unknown&path=a.jsonl")).status_code == 404


def test_config_defaults_when_sections_are_absent(tmp_path: pathlib.Path) -> None:
    """節を持たない設定では両画面の参照元を既定へ委ねる。"""
    path = tmp_path / "serve.toml"
    path.write_text("port = 3000\n", encoding="utf-8")
    resolved = config.resolve_config(environ={"AGENT_TOOLKIT_SERVE_CONFIG": str(path)}, platform="linux")
    assert resolved.plans == config.PlansConfig()
    assert resolved.sessions == config.SessionsConfig()


def test_config_warns_unknown_keys_in_screen_sections(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """画面別の節の未知キーを警告して無視し、既知キーの解決は継続する。"""
    path = tmp_path / "serve.toml"
    path.write_text('[plans]\nroot = "/srv/plans"\nunknown_key = 1\n', encoding="utf-8")
    with caplog.at_level("WARNING"):
        resolved = config.resolve_config(environ={"AGENT_TOOLKIT_SERVE_CONFIG": str(path)}, platform="linux")
    assert resolved.plans.root == "/srv/plans"
    assert "unknown_key" in caplog.text


@pytest.mark.asyncio
async def test_cancelled_request_keeps_worker_slot_until_completion() -> None:
    """要求キャンセル後も同期処理が終わるまでSemaphore枠を解放しない。"""
    workers = serve_app.BoundedWorkers(1)
    first_started = threading.Event()
    first_release = threading.Event()
    second_started = threading.Event()

    def first() -> None:
        first_started.set()
        first_release.wait()

    def second() -> None:
        second_started.set()

    first_task = asyncio.create_task(workers.run(first))
    await asyncio.to_thread(first_started.wait)
    first_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_task
    second_task = asyncio.create_task(workers.run(second))
    await asyncio.sleep(0.01)
    assert not second_started.is_set()
    first_release.set()
    await second_task
    assert second_started.is_set()


def test_operations_reads_local_entries_and_detail_without_pull(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一覧と詳細はGit同期を開始せずローカル内容を返す。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    entry = inbox / "entry.md"
    entry.write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: test\n---\n\n要約本文\n",
        encoding="utf-8",
    )

    def unexpected(*_args: object, **_kwargs: object) -> typing.NoReturn:
        raise AssertionError("読取り処理がGit同期を開始しました")

    monkeypatch.setattr(common, "repo_lock", unexpected)
    monkeypatch.setattr(common, "pull", unexpected)
    operations = serve_app.Operations(tmp_path)
    result, warnings = operations.entries_with_warnings({})
    assert not warnings
    assert result[0] | {
        "updated_at": result[0]["updated_at"],
    } == {
        "kind": "awi",
        "state": "inbox",
        "filename": "entry.md",
        "answered": None,
        "plan": False,
        "target_repo": "example/repo",
        "source": "test",
        "summary": "要約本文",
        "updated_at": result[0]["updated_at"],
    }
    detail = operations.detail("inbox", "entry.md")
    content = detail["content"]
    assert isinstance(content, str)
    assert content.endswith("要約本文\n")


def test_detail_renders_frontmatter_as_table(tmp_path: pathlib.Path) -> None:
    """frontmatterは入れ子の長い値も表として描画し、区切り行由来の見出しを生成しない。"""
    _write_detail_entry(
        tmp_path,
        "---\ntarget_repo: github.com/ak110/dotfiles\ntype: awi\n"
        "plan_file: /tmp/plan.md\ndepends_on:\n  - predecessor.md\n---\n\n本文です。\n",
    )
    rendered = typing.cast(str, serve_app.Operations(tmp_path).detail("inbox", "entry.md")["content_html"])
    assert "<table" in rendered
    assert "target_repo" in rendered
    assert "plan_file" in rendered
    assert "predecessor.md" in rendered
    assert "<hr" not in rendered
    assert "<h2" not in rendered
    assert "<p>本文です。</p>" in rendered


def test_detail_escapes_frontmatter_values(tmp_path: pathlib.Path) -> None:
    """frontmatterの値に含まれるHTML特殊文字をエスケープする。"""
    _write_detail_entry(tmp_path, '---\ntype: awi\nnote: "<script>alert(1)</script>"\n---\n\n本文\n')
    rendered = typing.cast(str, serve_app.Operations(tmp_path).detail("inbox", "entry.md")["content_html"])
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_detail_without_frontmatter_is_unchanged(tmp_path: pathlib.Path) -> None:
    """frontmatterを持たない本文は従来のMarkdownとして整形する。"""
    _write_detail_entry(tmp_path, "# 見出し\n\n本文\n")
    rendered = typing.cast(str, serve_app.Operations(tmp_path).detail("inbox", "entry.md")["content_html"])
    assert "<h1>見出し</h1>" in rendered
    assert "<p>本文</p>" in rendered


def test_detail_with_empty_frontmatter_renders_body_only(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """空のfrontmatterは空表を生成せず、分離後の本文だけを整形する。"""
    _write_detail_entry(tmp_path, "---\n---\n\n本文\n")
    monkeypatch.setattr(common, "entry_type_from_metadata", lambda *_args: "awi")
    rendered = typing.cast(str, serve_app.Operations(tmp_path).detail("inbox", "entry.md")["content_html"])
    assert '<table class="frontmatter">' not in rendered
    # 分離自体は成立するため、開始区切りが水平線として残らない。
    assert "<hr" not in rendered
    assert "<p>本文</p>" in rendered


def test_operations_sort_entries_by_filename_across_states_and_render_markdown(tmp_path: pathlib.Path) -> None:
    """一覧を状態横断のファイル名順で返し、詳細本文を安全なHTMLへ整形する。"""
    inbox = tmp_path / "inbox"
    processing = tmp_path / "processing"
    inbox.mkdir()
    processing.mkdir()
    (inbox / "z-last.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n",
        encoding="utf-8",
    )
    (processing / "a-first.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n"
        "# 見出し\n\n- 項目\n\n```python\nprint('x')\n```\n\n<script>alert(1)</script>\n",
        encoding="utf-8",
    )

    operations = serve_app.Operations(tmp_path)
    entries, warnings = operations.entries_with_warnings({"status": "active"})
    assert not warnings
    assert [item["filename"] for item in entries] == ["z-last.md", "a-first.md"]
    detail = operations.detail("processing", "a-first.md")
    rendered = typing.cast(str, detail["content_html"])
    assert "<h1>見出し</h1>" in rendered
    assert "<li>項目</li>" in rendered
    assert '<code class="language-python">' in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered


def test_operations_parses_each_scanned_entry_once_before_query_filter(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本文検索で除外するエントリも含め、走査したMarkdownを1件1回だけ解析する。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    for filename, body in (("match.md", "検索対象"), ("other.md", "別の本文")):
        (inbox / filename).write_text(
            f"---\ntype: awi\ntarget_repo: example/repo\n---\n\n{body}\n",
            encoding="utf-8",
        )
    original_parse = serve_app.frontmatter.parse_frontmatter
    parse_calls = 0

    def counting_parse(text: str) -> tuple[dict[str, typing.Any], str] | None:
        nonlocal parse_calls
        parse_calls += 1
        return original_parse(text)

    monkeypatch.setattr(serve_app.frontmatter, "parse_frontmatter", counting_parse)

    result, warnings = serve_app.Operations(tmp_path).entries_with_warnings({"q": "検索対象"})

    assert not warnings
    assert [item["filename"] for item in result] == ["match.md"]
    assert parse_calls == 2


@pytest.mark.asyncio
async def test_answer_api_rejects_awi_entry(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AWIエントリへの回答送信は拒否する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    monkeypatch.setattr(common, "_repo_lock", lock)
    monkeypatch.setattr(common, "_pull", lambda _path: None)
    monkeypatch.setattr(serve_app.uwi_mutations, "_repo_lock", lock)
    monkeypatch.setattr(serve_app.uwi_mutations, "_pull", lambda _path: None)
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "entry.md").write_text(
        "---\ntype: awi\ntarget_repo: github.com/example/foo\n---\n\n本文\n",
        encoding="utf-8",
    )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(
        "/api/entries/answer",
        json={"filename": "entry.md", "answer": "回答"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_answer_api_returns_edit_conflict_for_unreadable_expected_content(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回答対象の非UTF-8化を409競合へ正規化し、元のバイト列を保つ。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    for module in (common, serve_app.uwi_mutations):
        monkeypatch.setattr(module, "_repo_lock", lock, raising=False)
        monkeypatch.setattr(module, "_pull", lambda _path: None, raising=False)
        monkeypatch.setattr(module, "_commit_and_push", lambda *_args, **_kwargs: None, raising=False)
        monkeypatch.setattr(module, "_push_pending_commits", lambda _path: None, raising=False)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    target = inbox / "question.md"
    target.write_bytes(b"\xff")
    original = (
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n"
    )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().post(
        "/api/entries/answer",
        json={
            "filename": target.name,
            "state": "inbox",
            "answer": "回答",
            "expected_content": original,
        },
    )

    assert response.status_code == 409
    assert await response.get_json() == {
        "code": "edit_conflict",
        "error": "編集中に他プロセスが対象を変更しました",
    }
    assert target.read_bytes() == b"\xff"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload", "filename", "initial_content"),
    [
        (
            "put",
            "/api/entries/inbox/awi.md",
            {"content": "更新本文", "expected_content": None},
            "awi.md",
            "変更前の本文\n",
        ),
        (
            "put",
            "/api/entries/inbox/awi.md",
            {"content": "更新本文", "expected_content": ""},
            "awi.md",
            "変更前の本文\n",
        ),
        (
            "put",
            "/api/entries/inbox/awi.md",
            {"content": "更新本文", "expected_content": "  "},
            "awi.md",
            "変更前の本文\n",
        ),
        (
            "post",
            "/api/entries/answer",
            {"filename": "question.md", "answer": "回答", "expected_content": None},
            "question.md",
            "## 質問\n\n質問？\n\n## 回答\n\n",
        ),
        (
            "post",
            "/api/entries/answer",
            {"filename": "question.md", "answer": "回答", "expected_content": ""},
            "question.md",
            "## 質問\n\n質問？\n\n## 回答\n\n",
        ),
        (
            "post",
            "/api/entries/answer",
            {"filename": "question.md", "answer": "回答", "expected_content": "\t"},
            "question.md",
            "## 質問\n\n質問？\n\n## 回答\n\n",
        ),
    ],
)
async def test_edit_and_answer_apis_reject_invalid_specified_expected_content(
    tmp_path: pathlib.Path,
    method: str,
    path: str,
    payload: dict[str, object],
    filename: str,
    initial_content: str,
) -> None:
    """明示した`expected_content`のnull・空値を400で拒否し、対象を変更しない。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    entry = inbox / filename
    entry.write_text(initial_content, encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await getattr(app.test_client(), method)(path, json=payload)
    assert response.status_code == 400
    assert entry.read_text(encoding="utf-8") == initial_content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/api/entries?status=unknown", None),
        ("get", "/api/entries?page=0", None),
        ("get", "/api/entries?page=abc", None),
        ("get", "/api/entries?target_repo=", None),
        ("get", "/api/entries?q=", None),
        ("get", "/api/entries?source_empty=false", None),
        ("get", "/api/entries?source=web&source_empty=true", None),
        ("get", "/api/entries?source_kind=unknown", None),
        ("get", "/api/entries?source_kind=agent&source=web", None),
        ("get", "/api/entries?source_kind=human&source_empty=true", None),
        (
            "post",
            "/api/entries",
            {"type": "awi", "messages": ["x"], "target_repo": "example/repo", "source": "web"},
        ),
        (
            "post",
            "/api/entries",
            {
                "type": "uwi",
                "messages": ["x"],
                "target_repo": "example/repo",
                "scope": "s",
                "question_type": "free-form",
                "choices": ["a"],
            },
        ),
        ("post", "/api/entries/adopt", {"filenames": ["x.md"], "note": 1}),
        ("post", "/api/entries/adopt", {"filenames": ["../x.md"]}),
    ],
)
async def test_api_rejects_invalid_inputs(
    tmp_path: pathlib.Path,
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    """Web入力境界が列挙・型・空文字・basename違反・競合パラメーターを400で拒否する。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()
    response = await getattr(client, method)(path, json=payload)
    assert response.status_code == 400


def test_add_omits_frontmatter_when_source_is_the_only_metadata(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """投入元だけのfrontmatterを除いた本文へ空の区切りを残さない。"""
    captured: dict[str, object] = {}

    def add_entries(_private_notes: pathlib.Path, **kwargs: typing.Any) -> list[str]:
        captured.update(kwargs)
        return ["entry.md"]

    monkeypatch.setattr(serve_app.awi_add, "add_entries", add_entries)

    result = serve_app.Operations(tmp_path).add(
        ["---\nsource: add-awi\n---\n\n本文"],
        entry_type="awi",
        target_repo="github.com/example/repo",
    )

    assert result == ["entry.md"]
    assert captured["messages"] == ["\n本文"]


@pytest.mark.asyncio
async def test_index_and_js_without_prefix_use_empty_base(tmp_path: pathlib.Path) -> None:
    """ヘッダー無しでは空文字列扱いとなり、直接アクセス時も従来どおり応答する。"""
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), state.ServeState(tmp_path))
    client = app.test_client()

    index_body = await (await client.get("/")).get_data(as_text=True)
    assert 'href="/static/app.css"' in index_body
    assert 'src="/static/app.js"' in index_body

    js_body = await (await client.get("/static/app.js")).get_data(as_text=True)
    assert 'const BASE_PATH="";' in js_body


@pytest.mark.asyncio
async def test_protocol_relative_prefix_logs_rejection_via_proxy_fix(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """スキーム相対形式のプレフィクスは`pytilpack.quart.ProxyFix`層が拒否しWARNINGを記録する。

    404単独では既存のルート未マッチ応答と区別できないため、`pytilpack.web.validate_forwarded_prefix`が
    記録する`X-Forwarded-Prefixに不正な値が含まれています`という警告ログの有無でProxyFix層の
    拒否経路が実際に実行されたことを検証する。
    """
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), state.ServeState(tmp_path))
    client = app.test_client()
    with caplog.at_level("WARNING"):
        response = await client.get("//evil.example/", headers={"X-Forwarded-Prefix": "//evil.example"})
    assert response.status_code == 404
    assert "X-Forwarded-Prefixに不正な値が含まれています" in caplog.text


def test_run_initializes_logging_and_logs_startup(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """logging初期化と起動ログ出力を実施し、hypercorn.errorの伝搬を止める。"""
    basic_config_calls: list[dict[str, object]] = []
    monkeypatch.setattr(serve.logging, "basicConfig", lambda **kwargs: basic_config_calls.append(kwargs))
    monkeypatch.setattr(serve.common, "ensure_environment", lambda home: tmp_path)
    monkeypatch.setattr(serve.asyncio, "run", lambda coro: coro.close())
    logging.getLogger("hypercorn.error").propagate = True

    with caplog.at_level("INFO", logger=serve.logger.name):
        serve.run(host="127.0.0.1", port=28766, home=tmp_path)

    assert basic_config_calls[0]["force"] is True
    assert basic_config_calls[0]["level"] == logging.INFO
    assert "http://127.0.0.1:28766/" in caplog.text
    assert logging.getLogger("hypercorn.error").propagate is False


@pytest.mark.asyncio
async def test_api_repos_returns_target_repos(tmp_path: pathlib.Path) -> None:
    """`GET /api/repos`は一覧と同じ状態指定で対象リポジトリの一覧を返す。"""
    _write_repo_entry(tmp_path, "inbox", "a.md", "github.com/x/alpha")
    _write_repo_entry(tmp_path, "adopted", "b.md", "github.com/x/adopted")
    current_state = state.ServeState(tmp_path)
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), current_state)
    response = await app.test_client().get("/api/repos")
    assert response.status_code == 200
    assert await response.get_json() == {"repos": ["github.com/x/alpha"]}
    adopted_response = await app.test_client().get("/api/repos?status=adopted")
    assert adopted_response.status_code == 200
    assert await adopted_response.get_json() == {"repos": ["github.com/x/adopted"]}
    invalid_response = await app.test_client().get("/api/repos?status=unknown")
    assert invalid_response.status_code == 400


def test_assets_populate_target_repo_choices() -> None:
    """対象リポジトリの候補をフィルターと新規登録欄へ反映する。"""
    assert '<select id="target-filter">' in assets.HTML
    assert '<datalist id="repo-options"></datalist>' in assets.HTML
    result = _run_node_ui(
        """
fetchHandler = async (url) => {
  if (url.endsWith('/api/repos?status=active')) {
    const repos = ['github.com/x/alpha', 'github.com/x/beta'];
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos})};
  }
  throw new Error('想定外のURL: ' + url);
};
await loadTargetRepos();
process.stdout.write(JSON.stringify({
  filterValues: byId('target-filter').children.map(option => option.value),
  filterLabels: byId('target-filter').children.map(option => option.textContent),
  datalistValues: byId('repo-options').children.map(option => option.value),
}));
"""
    )
    assert result["filterValues"] == ["", "github.com/x/alpha", "github.com/x/beta"]
    assert result["filterLabels"][0] == "すべて"
    assert result["datalistValues"] == ["github.com/x/alpha", "github.com/x/beta"]


def test_assets_load_entries_when_current_repo_candidate_request_fails() -> None:
    """最新の候補取得が失敗しても、現在の状態による一覧更新は継続する。"""
    result = _run_node_ui(
        """
const listUrls = [];
fetchHandler = async url => {
  if (url.includes('/api/repos?')) throw new Error('候補取得失敗');
  if (url.includes('/api/entries?')) {
    listUrls.push(url);
    return {
      ok: true, status: 200, statusText: 'OK',
      json: async () => ({
        entries: [{kind: 'awi', state: 'adopted', filename: 'entry.md', summary: '本文'}],
        warnings: []
      })
    };
  }
  throw new Error('想定外のURL: ' + url);
};
elements['state-filter'].value = 'adopted';
await handleFilterChange({reloadRepos: true});
process.stdout.write(JSON.stringify({
  listUrls,
  rows: entries.map(entry => entry.filename),
  error: elements['global-error-message'].textContent
}));
"""
    )
    assert result == {
        "listUrls": ["/atk/api/entries?type=all&status=adopted&answered=all&page=1"],
        "rows": ["entry.md"],
        "error": "候補取得失敗",
    }


def test_assets_discard_stale_search_fallback_response() -> None:
    """後発の一覧要求が完了した後に補助応答が到着しても表示を上書きしない。"""
    result = _run_node_ui(
        """
let resolveFallback;
let fallbackStarted;
const fallbackReady = new Promise(resolve => { fallbackStarted = resolve; });
elements['search-input'].value = 'old';
fetchHandler = async url => {
  if (url === '/atk/api/entries?q=old&page=1') {
    fallbackStarted();
    return new Promise(resolve => { resolveFallback = resolve; });
  }
  if (url.includes('q=old')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})};
  }
  if (url.includes('q=new')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({
      entries: [{kind: 'awi', state: 'inbox', filename: 'new.md', summary: 'new'}], warnings: []
    })};
  }
  throw new Error('想定外のURL: ' + url);
};
const oldRequest = loadEntries({announce: true});
await fallbackReady;
elements['search-input'].value = 'new';
const newRequest = loadEntries({announce: true});
await newRequest;
resolveFallback({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({entries: [{kind: 'awi', state: 'adopted', filename: 'old.md', summary: 'old'}], warnings: []})
});
await oldRequest;
process.stdout.write(JSON.stringify({
  rows: entries.map(entry => entry.filename),
  state: entries[0]?.state,
  notice: elements['list-fallback-notice'].textContent,
  status: elements['result-status'].textContent
}));
"""
    )
    assert result == {
        "rows": ["new.md"],
        "state": "inbox",
        "notice": "",
        "status": "1件を表示",
    }


def test_assets_keep_latest_repo_selection_when_stale_sse_candidates_finish() -> None:
    """SSEの旧候補応答後も最新の対象リポジトリと一覧条件を維持する。"""
    result = _run_node_ui(
        """
let resolveActive;
const listUrls = [];
fetchHandler = async url => {
  if (url.endsWith('/api/repos?status=active')) {
    return new Promise(resolve => { resolveActive = resolve; });
  }
  if (url.endsWith('/api/repos?status=adopted')) {
    return {
      ok: true, status: 200, statusText: 'OK',
      json: async () => ({repos: ['adopted/repo']})
    };
  }
  if (url.includes('/api/entries?')) {
    listUrls.push(url);
    const selected = url.includes('target_repo=adopted%2Frepo');
    const matching = {kind: 'awi', state: 'adopted', filename: 'selected.md', summary: 'selected'};
    const other = {kind: 'awi', state: 'adopted', filename: 'other.md', summary: 'other'};
    return {
      ok: true, status: 200, statusText: 'OK',
      json: async () => ({entries: selected ? [matching] : [matching, other], warnings: []})
    };
  }
  throw new Error('想定外のURL: ' + url);
};
const staleSse = reloadFromExternalChange();
await Promise.resolve();
elements['state-filter'].value = 'adopted';
await handleFilterChange({reloadRepos: true});
elements['target-filter'].value = 'adopted/repo';
await handleFilterChange();
resolveActive({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({repos: ['active/old']})
});
await staleSse;
process.stdout.write(JSON.stringify({
  state: elements['state-filter'].value,
  candidates: elements['target-filter'].children.map(option => option.value),
  selected: elements['target-filter'].value,
  lastListUrl: listUrls.at(-1),
  rows: entries.map(entry => entry.filename)
}));
"""
    )
    assert result == {
        "state": "adopted",
        "candidates": ["", "adopted/repo"],
        "selected": "adopted/repo",
        "lastListUrl": ("/atk/api/entries?type=all&status=adopted&answered=all&target_repo=adopted%2Frepo&page=1"),
        "rows": ["selected.md"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [{}, {"text": ""}, {"text": "  "}, {"text": 1}, {"text": "ただの本文\n"}, {"text": _BATCH_TEXT, "type": "awi"}],
)
async def test_batch_api_rejects_invalid_inputs(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    """必須キー・型・空文字・未知キー・show形式外の入力を400で拒否する。"""
    _patch_batch_repo_operations(monkeypatch)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().post("/api/entries/batch", json=payload)

    assert response.status_code == 400
    assert not (tmp_path / "inbox").exists() or not list((tmp_path / "inbox").iterdir())


def test_user_comment_pure_update_ignores_fenced_heading_and_appends_once() -> None:
    """コードフェンス内の同名文字列を見出しと誤認せず、予約節を1件だけ追記する。"""
    original = _session_review_awi("```markdown\n## ユーザーコメント\n```\n\n通常本文\n")

    updated = user_comment.update_user_comment(original, "コメント")

    assert updated.count("## ユーザーコメント") == 2
    assert updated.endswith("## ユーザーコメント\n\nコメント\n")
    assert user_comment.extract_user_comment(updated) == "コメント"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "editable"),
    [(None, False), ("human", True), ("alert-monitor", True), ("plan", True)],
)
async def test_user_comment_api_matches_agent_source_classification(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str | None,
    editable: bool,
) -> None:
    """詳細表示と保存APIが同じエージェント由来判定を使う。"""
    _patch_comment_edit_dependencies(monkeypatch)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    original = _session_review_awi("本文\n", source=source)
    path = inbox / "awi.md"
    path.write_text(original, encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    detail = await (await client.get("/api/entries/inbox/awi.md")).get_json()
    assert detail["entry"]["user_comment_editable"] is editable
    response = await client.post(
        "/api/entries/user-comment",
        json={
            "state": "inbox",
            "filename": "awi.md",
            "comment": "追加コメント",
            "expected_content": original,
        },
    )
    assert response.status_code == (200 if editable else 400)
    if editable:
        assert user_comment.extract_user_comment(path.read_text(encoding="utf-8")) == "追加コメント"
    else:
        assert path.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_user_comment_api_requires_expected_content_and_rejects_comment_h2(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """expected_contentの省略・空値とコメント内H2を保存前に拒否する。"""
    _patch_comment_edit_dependencies(monkeypatch)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    original = _session_review_awi("本文\n")
    path = inbox / "awi.md"
    path.write_text(original, encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    for payload in (
        {"state": "inbox", "filename": "awi.md", "comment": "コメント"},
        {
            "state": "inbox",
            "filename": "awi.md",
            "comment": "コメント",
            "expected_content": "",
        },
        {
            "state": "inbox",
            "filename": "awi.md",
            "comment": "## 禁止見出し",
            "expected_content": original,
        },
    ):
        response = await client.post("/api/entries/user-comment", json=payload)
        assert response.status_code == 400
        assert path.read_text(encoding="utf-8") == original
