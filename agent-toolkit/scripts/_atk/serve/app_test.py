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


def test_unknown_config_key_logs_warning(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """未知の設定キーを警告ログで通知し、既知キーの解決は継続する。"""
    path = tmp_path / "serve.toml"
    path.write_text('host = "toml-host"\nunknown = 1\n', encoding="utf-8")
    env = {"AGENT_TOOLKIT_SERVE_CONFIG": str(path)}
    with caplog.at_level("WARNING"):
        resolved = config.resolve_config(environ=env, platform="linux")
    assert resolved == config.ServeConfig("toml-host", 28766)
    assert "unknown" in caplog.text


def test_assets_define_pagination_and_dismissible_operation_notice() -> None:
    """一覧ページ移動と操作結果通知へ、キーボード操作可能な領域を持たせる。"""
    assert 'id="pagination"' in assets.HTML
    assert 'id="previous-page-button"' in assets.HTML
    assert 'id="next-page-button"' in assets.HTML
    assert 'id="pagination-status"' in assets.HTML
    assert 'id="operation-notice"' in assets.HTML
    assert 'id="operation-notice-message"' in assets.HTML
    assert (
        '<button id="operation-notice-close-button" class="operation-notice-close" type="button" '
        'aria-label="操作通知を閉じる">×</button>'
    ) in assets.HTML
    assert "parameters.set('page', String(page));" in assets.JS
    assert "new URLSearchParams({q: searchTerm, page: String(currentPage)})" in assets.JS
    assert 'operation-notice[data-error="true"]' in assets.CSS


def test_assets_size_dialogs_with_small_viewport_height_unit() -> None:
    """高さを決めるビューポート単位を無印`vh`ではなく`svh`で書く。

    無印`vh`は大ビューポート基準のため、モバイルのブラウザーUI展開時に
    ダイアログの外枠が可視領域を超え、ヘッダーとフッターが隠れる。
    `dvh`ではなく`svh`を採用するのは、スクロールに伴うブラウザーUIの伸縮で
    枠の高さが再レイアウトされることを避け、常に可視な下限側へ収めるためである。
    """
    assert not re.findall(r"\dvh\b", assets.CSS)
    assert "100svh" in assets.CSS


def test_assets_execute_error_paths_across_binding_and_initialization_code() -> None:
    """末尾を含むJS全体で外部更新と初期化の失敗メッセージを表示する。"""
    result = _run_node_ui(
        """
fetchHandler = async url => {
  if (url.includes('/api/repos?')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos: []})};
  }
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})};
};
refreshKnownUwis = async () => { throw new Error('外部更新失敗'); };
await reloadFromExternalChange();
await Promise.resolve();
const reloadError = elements['global-error-message'].textContent;
setGlobalError('');
refreshKnownUwis = async () => { throw new Error('初期化失敗'); };
initializeApp();
await initialization;
process.stdout.write(JSON.stringify({reloadError, initializationError: elements['global-error-message'].textContent}));
"""
    )
    assert result == {"reloadError": "外部更新失敗", "initializationError": "初期化失敗"}


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
elements['global-error-close-button'].focus();
elements['global-error-close-button'].listeners.click();
setGlobalError('同期中の後続エラー');
elements['global-error-close-button'].focus();
releaseSync();
await synchronization;
process.stdout.write(JSON.stringify({
  focused,
  hidden: elements['global-error'].hidden,
  message: elements['global-error-message'].textContent
}));
"""
    )
    assert result == {
        "focused": "global-error-close-button",
        "hidden": False,
        "message": "同期中の後続エラー",
    }


def test_assets_prefill_answer_change_and_keep_question_visible() -> None:
    """回答変更時は既存回答と質問本文を残し、候補を入力補助にする。"""
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
const answerLabel = elements['answer-button'].textContent;
openDialog(elements['detail-dialog'], origin, elements['detail-dialog-body']);
enterAnswer();
const prefilled = elements['answer-input'].value;
const candidates = elements['answer-choices'].children.map(button => button.textContent);
elements['answer-choices'].children[0].listeners.click();
const afterCandidate = elements['answer-input'].value;
elements['answer-input'].value += 'を補足';
const answerVisible = !elements['answer-panel'].hidden;
closeDetailDialog();
process.stdout.write(JSON.stringify({
  detailVisible: !elements['detail-view'].hidden,
  answerVisible,
  answerLabel,
  prefilled,
  candidates,
  afterCandidate,
  edited: elements['answer-input'].value,
  focused
}));
"""
    )
    assert result == {
        "detailVisible": True,
        "answerVisible": True,
        "answerLabel": "回答を変更",
        "prefilled": "既存回答",
        "candidates": ["A", "B"],
        "afterCandidate": "A",
        "edited": "Aを補足",
        "focused": "origin",
    }


def test_assets_restore_detail_focus_after_origin_row_disappears() -> None:
    """詳細の起点行が消えた場合も、残存行又は空状態の操作へフォーカスを戻す。"""
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
renderList();
const firstButton = elements['entry-list'].children[0].children[0];
displayEntry(first);
detailOriginKey = entryKey(first);
openDialog(elements['detail-dialog'], firstButton, elements['detail-dialog-body']);
entries = [second];
renderList();
closeDetailDialog();
const remainingRow = focused;

entries = [second];
renderList();
const secondButton = elements['entry-list'].children[0].children[0];
displayEntry(second);
detailOriginKey = entryKey(second);
openDialog(elements['detail-dialog'], secondButton, elements['detail-dialog-body']);
elements['search-input'].value = '一致しない条件';
entries = [];
renderList();
closeDetailDialog();
process.stdout.write(JSON.stringify({remainingRow, emptyState: focused}));
"""
    )
    assert result == {
        "remainingRow": "inbox/second.md",
        "emptyState": "empty-clear-button",
    }


def test_assets_sse_keeps_edit_target_for_duplicate_filename() -> None:
    """同名項目のSSE後も編集中の複合キーを保存先として維持する。"""
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
entries = [inbox, processing];
enterEdit();
elements['edit-content'].value = '利用者の保存本文';
const putUrls = [];
fetchHandler = async (url, options) => {
  if (options.method === 'PUT') {
    putUrls.push(url);
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({ok: true})};
  }
  if (url.includes('/api/entries?')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [inbox, processing], warnings: []})};
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
        "putUrls": ["/atk/api/entries/processing/same.md"],
        "state": "processing",
        "open": False,
    }


def test_assets_clear_self_write_sse_alert_after_save_and_answer_success() -> None:
    """保存・回答の応答前にSSEが届いても、成功後は競合警告を残さず、回答成功では詳細を閉じる。"""
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
  enterAnswer();
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
    warning = "外部で項目が更新されました。入力を保持しています。詳細を閉じて開き直してから保存してください。"
    assert result == {
        "saved": {
            "during": warning,
            "after": "",
            "status": "",
            "toast": "inbox/entry.mdを保存しました。",
            "open": False,
            "mode": "view",
        },
        "answered": {
            "during": warning,
            "after": "",
            "status": "",
            "toast": "inbox/question.mdへ回答しました。",
            "open": False,
            "mode": "view",
        },
    }


def test_create_success_resets_filters_and_keeps_list() -> None:
    """追加成功後は既定条件へ戻し、返却されたファイルを一覧へ残して詳細を開かない。"""
    result = _run_node_ui(
        """
elements['create-dialog'].open = true;
dialogStack.push('create-dialog');
elements['create-content'].value = '新しい本文';
elements['create-target'].value = 'example/repo';
elements['kind-filter'].value = 'awi';
elements['state-filter'].value = 'all';
elements['search-input'].value = '隠す条件';
const listed = {
  kind: 'awi', state: 'inbox', filename: 'new.md', answered: null,
  summary: '新しい本文', target_repo: 'example/repo', frontmatter_entries: []
};
fetchHandler = async (url, options) => {
  if (url.endsWith('/api/entries') && options.method === 'POST') {
    return {ok: true, status: 201, statusText: 'Created', json: async () => ({filenames: ['new.md']})};
  }
  if (url.endsWith('/api/repos?status=active')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos: ['example/repo']})};
  }
  if (url.includes('/api/entries?')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [listed], warnings: []})};
  }
  throw new Error('想定外のURL: ' + url);
};
await createEntry({preventDefault() {}});
process.stdout.write(JSON.stringify({
  kind: elements['kind-filter'].value,
  state: elements['state-filter'].value,
  search: elements['search-input'].value,
  detailOpen: elements['detail-dialog'].open,
  current: currentEntry ? currentEntry.filename : null,
  body: elements['detail-content'].innerHTML,
  listCount: elements['entry-list'].children.length,
  createCalls: fetchCalls.filter(call => call.url.endsWith('/api/entries') && call.options.method === 'POST').length
}));
"""
    )
    assert result == {
        "kind": "all",
        "state": "active",
        "search": "",
        "detailOpen": False,
        "current": None,
        "body": "",
        "listCount": 1,
        "createCalls": 1,
    }


def test_all_api_routes_are_registered(tmp_path: pathlib.Path) -> None:
    """計画で定義した全APIルートを登録する。"""
    current_state = state.ServeState(tmp_path)
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), current_state)
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    expected = {
        "/favicon.svg",
        "/manifest.webmanifest",
        "/static/icon-192.png",
        "/static/icon-512.png",
        "/api/sync",
        "/api/repos",
        "/api/entries",
        "/api/entries/batch",
        "/api/entries/<state_name>/<filename>",
        "/api/entries/start-processing",
        "/api/entries/return-to-inbox",
        "/api/entries/hold",
        "/api/entries/unhold",
        "/api/entries/adopt",
        "/api/entries/reject",
        "/api/entries/remove",
        "/api/entries/commit",
        "/api/entries/answer",
        "/api/entries/user-comment",
        "/api/events",
    }
    removed = {"/api/status", "/api/enable", "/api/disable"}
    assert expected <= rules
    assert not removed & rules


def test_config_ignores_legacy_plans_viewer_file(tmp_path: pathlib.Path) -> None:
    """旧`claude-plans-viewer.toml`は読み込まず、`serve.toml`だけを正本とする。"""
    legacy = tmp_path / "claude-plans-viewer.toml"
    legacy.write_text('root = "/legacy"\nremote-hosts = ["legacy-host"]\n', encoding="utf-8")
    path = tmp_path / "serve.toml"
    path.write_text("", encoding="utf-8")
    resolved = config.resolve_config(environ={"AGENT_TOOLKIT_SERVE_CONFIG": str(path)}, platform="linux")
    assert resolved.plans.root is None
    assert not resolved.plans.remote_hosts


@pytest.mark.asyncio
async def test_sse_heartbeat(tmp_path: pathlib.Path) -> None:
    """変更が無い期間はheartbeatを送信する。"""
    events = state.ServeState(tmp_path).events(heartbeat=0.001)
    assert await anext(events) == ": heartbeat\n\n"
    await events.aclose()


@pytest.mark.asyncio
async def test_concurrent_sync_requests_share_one_pull(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同時同期要求は1回のpull結果を共有する。"""
    operations = serve_app.Operations(tmp_path)
    started = threading.Event()
    release = threading.Event()
    calls = 0

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    def pull(_path: pathlib.Path) -> None:
        nonlocal calls
        calls += 1
        started.set()
        release.wait()

    monkeypatch.setattr(common, "repo_lock", lock)
    monkeypatch.setattr(common, "pull", pull)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
        operations=operations,
    )
    tasks = [asyncio.create_task(app.test_client().post("/api/sync")) for _ in range(4)]
    await asyncio.to_thread(started.wait)
    await asyncio.sleep(0)
    release.set()
    responses = await asyncio.gather(*tasks)
    assert calls == 1
    assert [await response.get_json() for response in responses] == [{"synced": True}] * 4


def test_operations_read_legacy_type_values_as_current_kinds(tmp_path: pathlib.Path) -> None:
    """`atk wi migrate`の実行前に保存された`type`値を現行の種別として一覧へ返す。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "entry.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n要約本文\n",
        encoding="utf-8",
    )
    (inbox / "question.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n質問本文\n",
        encoding="utf-8",
    )

    result, warnings = serve_app.Operations(tmp_path).entries_with_warnings({})

    assert not warnings
    assert {str(item["filename"]): item["kind"] for item in result} == {"entry.md": "awi", "question.md": "uwi"}
    assert [item["answered"] for item in result if item["filename"] == "question.md"] == [False]


@pytest.mark.asyncio
async def test_detail_api_preserves_frontmatter_order_cycles_and_mapping_keys(tmp_path: pathlib.Path) -> None:
    """詳細HTTP APIはfrontmatterの順序、循環参照、非文字列キーを保持して返す。"""
    _write_detail_entry(
        tmp_path,
        "---\n"
        "type: awi\n"
        "z_key: z\n"
        "a_key: a\n"
        "m_key: m\n"
        "queue_schedule:\n"
        "  1: numeric\n"
        '  "1": textual\n'
        "  cyclic: &cyclic\n"
        "    kept: value\n"
        "    self: *cyclic\n"
        "---\n\n本文\n",
    )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().get("/api/entries/inbox/entry.md")

    assert response.status_code == 200
    payload = json.loads(await response.get_data())
    frontmatter_entries = payload["entry"]["frontmatter_entries"]
    assert [item["key"]["value"] for item in frontmatter_entries] == [
        "type",
        "z_key",
        "a_key",
        "m_key",
        "queue_schedule",
    ]
    assert frontmatter_entries[:4] == [
        {"key": {"type": "str", "value": "type"}, "value": "awi"},
        {"key": {"type": "str", "value": "z_key"}, "value": "z"},
        {"key": {"type": "str", "value": "a_key"}, "value": "a"},
        {"key": {"type": "str", "value": "m_key"}, "value": "m"},
    ]
    assert frontmatter_entries[4] == {
        "key": {"type": "str", "value": "queue_schedule"},
        "value": {
            "__mapping__": [
                {"key": {"type": "int", "value": 1}, "value": "numeric"},
                {"key": {"type": "str", "value": "1"}, "value": "textual"},
                {
                    "key": {"type": "str", "value": "cyclic"},
                    "value": {"kept": "value", "self": "[Circular]"},
                },
            ]
        },
    }


@pytest.mark.parametrize(
    ("frontmatter_source", "expected_repo", "expected_source"),
    [
        (
            "type: awi\ntarget_repo: example/repo\nsource: test\nplan_file: /tmp/plan.md\ndepends_on:\n  - predecessor.md\n",
            "example/repo",
            "test",
        ),
        ("type: awi\ntarget_repo: [broken\n", None, None),
    ],
)
def test_operations_frontmatter_parser_handles_nested_dependencies_and_broken_yaml(
    tmp_path: pathlib.Path,
    frontmatter_source: str,
    expected_repo: str | None,
    expected_source: str | None,
) -> None:
    """一覧表示は入れ子メタデータを読み、YAML破損時も一覧全体を継続する。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "entry.md").write_text(f"---\n{frontmatter_source}---\n\n要約本文\n", encoding="utf-8")

    result, warnings = serve_app.Operations(tmp_path).entries_with_warnings({})
    assert not warnings

    assert result[0]["target_repo"] == expected_repo
    assert result[0]["source"] == expected_source


@pytest.mark.asyncio
async def test_add_api_rejects_missing_type(tmp_path: pathlib.Path) -> None:
    """`type`欠落は必須キー不足として400を返す。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(
        "/api/entries",
        json={"messages": ["x"], "target_repo": "example/repo"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_uwi_reject_transition_succeeds(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """UWIエントリも他種別と同様に不採用遷移が成功する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    monkeypatch.setattr(common, "_repo_lock", lock)
    monkeypatch.setattr(common, "_pull", lambda _path: None)
    monkeypatch.setattr(common, "_commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(common, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(serve_app.awi_mutations, "_repo_lock", lock)
    monkeypatch.setattr(serve_app.awi_mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(serve_app.awi_mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(serve_app.awi_mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "entry.md").write_text(
        "---\ntype: uwi\ntarget_repo: github.com/example/foo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post("/api/entries/reject", json={"filenames": ["entry.md"]})
    assert response.status_code == 200
    assert (tmp_path / "rejected" / "entry.md").is_file()
    assert not (inbox / "entry.md").exists()


@pytest.mark.asyncio
async def test_remove_api_returns_edit_conflict_before_target_repo_validation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """対象リポジトリ照合より先に非UTF-8化を削除競合へ正規化する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    for module in (common, serve_app.awi_mutations):
        monkeypatch.setattr(module, "_repo_lock", lock, raising=False)
        monkeypatch.setattr(module, "_pull", lambda _path: None, raising=False)
        monkeypatch.setattr(module, "_commit_and_push", lambda *_args, **_kwargs: None, raising=False)
        monkeypatch.setattr(module, "_push_pending_commits", lambda _path: None, raising=False)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    target = inbox / "unreadable.md"
    target.write_bytes(b"\xff")
    original = "---\ntype: awi\ntarget_repo: github.com/example/repo\n---\n\n取得時本文\n"
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().post(
        "/api/entries/remove",
        json={
            "filenames": [target.name],
            "state": "inbox",
            "target_repo": "github.com/example/repo",
            "expected_content": original,
            "force": False,
        },
    )

    assert response.status_code == 409
    assert await response.get_json() == {
        "code": "edit_conflict",
        "error": "編集中に他プロセスが対象を変更しました",
    }
    assert response.content_type == "application/json"
    assert target.read_bytes() == b"\xff"


@pytest.mark.asyncio
async def test_answer_and_remove_apis_return_edit_conflict_when_pull_moves_target(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pullで取得時の状態から移動した回答・削除対象を409競合として保全する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    for module in (common, serve_app.awi_mutations, serve_app.uwi_mutations):
        monkeypatch.setattr(module, "_repo_lock", lock, raising=False)
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
    answer_inbox = inbox / "answer.md"
    remove_inbox = inbox / "remove.md"
    answer_inbox.write_text(uwi_content, encoding="utf-8")
    remove_inbox.write_text(awi_content, encoding="utf-8")

    def move_during_pull(_path: pathlib.Path) -> None:
        if answer_inbox.exists():
            answer_inbox.rename(processing / answer_inbox.name)
        elif remove_inbox.exists():
            remove_inbox.rename(processing / remove_inbox.name)

    monkeypatch.setattr(serve_app.awi_mutations, "_pull", move_during_pull)
    monkeypatch.setattr(serve_app.uwi_mutations, "_pull", move_during_pull)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    answer_response = await client.post(
        "/api/entries/answer",
        json={
            "filename": answer_inbox.name,
            "state": "inbox",
            "answer": "回答",
            "expected_content": uwi_content,
        },
    )
    remove_response = await client.post(
        "/api/entries/remove",
        json={
            "filenames": [remove_inbox.name],
            "state": "inbox",
            "expected_content": awi_content,
            "force": False,
        },
    )

    for response in (answer_response, remove_response):
        assert response.status_code == 409
        assert await response.get_json() == {
            "code": "edit_conflict",
            "error": "編集中に他プロセスが対象を変更しました",
        }
    assert (processing / answer_inbox.name).read_text(encoding="utf-8") == uwi_content
    assert (processing / remove_inbox.name).read_text(encoding="utf-8") == awi_content


@pytest.mark.asyncio
async def test_entries_api_keeps_readable_entries_and_reports_unreadable_files(tmp_path: pathlib.Path) -> None:
    """一覧APIは読取り不能な1ファイルを警告へ分離し、残りの一覧を返す。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "readable.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n",
        encoding="utf-8",
    )
    (inbox / "invalid.md").write_bytes(b"\xff")
    current_state = state.ServeState(tmp_path)
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), current_state)

    response = await app.test_client().get("/api/entries?status=inbox")

    assert response.status_code == 200
    payload = await response.get_json()
    assert [item["filename"] for item in payload["entries"]] == ["readable.md"]
    assert payload["warnings"] == [{"filename": "invalid.md", "reason": "UTF-8として読み取れません"}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/entries", {"type": "awi", "messages": ["awi"]}),
        (
            "/api/entries",
            {"type": "uwi", "messages": ["UWIですか？"], "scope": "test", "question_type": "free-form"},
        ),
    ],
)
async def test_web_add_mutations_require_target_repo(
    tmp_path: pathlib.Path,
    path: str,
    payload: dict[str, object],
) -> None:
    """新規追加系APIは常駐プロセスのカレントディレクトリへ依存しないためtarget_repoを必須とする。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(path, json=payload)
    assert response.status_code == 400
    assert "target_repo" in (await response.get_json())["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload", "expected_target"),
    [
        (
            "/api/entries",
            {
                "type": "awi",
                "messages": ["---\nsource: add-awi\n---\n\nawi"],
                "target_repo": "https://github.com/Example/Specified.git",
            },
            "github.com/example/specified",
        ),
        (
            "/api/entries",
            {
                "type": "uwi",
                "messages": ["---\nsource: add-awi\n---\n\nUWIですか？"],
                "scope": "test",
                "question_type": "free-form",
                "target_repo": "https://github.com/Example/Specified.git",
            },
            "github.com/example/specified",
        ),
    ],
)
async def test_add_api_resolves_target_repo_into_frontmatter(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    payload: dict[str, object],
    expected_target: str,
) -> None:
    """追加APIがCLIと同じ解決契約のtarget_repoをfrontmatterへ保存する。"""

    def resolve(value: str | None, *, cwd: pathlib.Path | None = None) -> str:
        del cwd
        if value is None:
            return "github.com/example/current"
        if value == "https://github.com/Example/Specified.git":
            return "github.com/example/specified"
        raise AssertionError(value)

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    monkeypatch.setattr(awi_repo, "resolve_repo_id", resolve)
    monkeypatch.setattr(common, "_repo_lock", lock)
    monkeypatch.setattr(common, "_pull", lambda _path: None)
    monkeypatch.setattr(common, "_commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(common, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(serve_app.awi_add, "_repo_lock", lock)
    monkeypatch.setattr(serve_app.awi_add, "_pull", lambda _path: None)
    monkeypatch.setattr(serve_app.awi_add, "_commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_app.uwi_mutations, "_repo_lock", lock)
    monkeypatch.setattr(serve_app.uwi_mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(serve_app.uwi_mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(path, json=payload)
    assert response.status_code == 201
    body = await response.get_json()
    content = (tmp_path / "inbox" / body["filenames"][0]).read_text(encoding="utf-8")
    assert f"target_repo: {expected_target}" in content
    assert "target_repo: \n" not in content
    assert "source:" not in content


def test_target_repos_collects_distinct_values_from_active_states(tmp_path: pathlib.Path) -> None:
    """未処理・処理中のエントリから対象リポジトリを重複なく昇順で集める。

    処理済みにしか現れないリポジトリは、一覧の初期フィルター`active`で0件になるため含めない。
    """
    _write_repo_entry(tmp_path, "inbox", "a.md", "github.com/x/beta")
    _write_repo_entry(tmp_path, "processing", "b.md", "github.com/x/alpha")
    _write_repo_entry(tmp_path, "processing", "c.md", "github.com/x/beta")
    _write_repo_entry(tmp_path, "adopted", "d.md", "github.com/x/adopted-only")
    _write_repo_entry(tmp_path, "rejected", "e.md", "github.com/x/rejected-only")
    (tmp_path / "inbox" / "broken.md").write_text("frontmatterなし\n", encoding="utf-8")
    operations = serve_app.Operations(tmp_path)
    assert operations.target_repos() == ["github.com/x/alpha", "github.com/x/beta"]
    assert operations.target_repos("adopted") == ["github.com/x/adopted-only"]


def test_background_sync_respects_rate_limit(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """定期更新はレート制限に従い、ロック競合時は当該周期を見送る。"""
    calls: list[str] = []

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    monkeypatch.setattr(common, "repo_lock", lock)
    monkeypatch.setattr(common, "pull_if_stale", _recorder(calls, "pull_if_stale", result=False))
    operations = serve_app.Operations(tmp_path)
    assert operations.background_sync() is False
    assert calls == ["pull_if_stale"]

    def conflicting_lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        raise filelock.Timeout("lock")

    monkeypatch.setattr(common, "repo_lock", conflicting_lock)
    assert operations.background_sync() is False
    assert calls == ["pull_if_stale"]


def test_assets_keep_choices_when_repos_request_fails() -> None:
    """候補の取得に失敗しても既存の選択肢と選択値を壊さない。"""
    result = _run_node_ui(
        """
fetchHandler = async () => {
  throw new Error('通信失敗');
};
byId('target-filter').value = 'github.com/x/alpha';
await loadTargetRepos();
process.stdout.write(JSON.stringify({
  selected: byId('target-filter').value,
  values: byId('target-filter').children.map(option => option.value),
}));
"""
    )
    assert result["selected"] == "github.com/x/alpha"
    assert result["values"] == []


def test_assets_keep_latest_list_when_entry_requests_finish_out_of_order() -> None:
    """一覧要求が逆順に完了しても後発要求の行だけを維持する。"""
    result = _run_node_ui(
        """
const resolvers = [];
fetchHandler = async url => {
  if (!url.includes('/api/entries?')) throw new Error('想定外のURL: ' + url);
  return new Promise(resolve => { resolvers.push(resolve); });
};
const first = loadEntries();
const second = loadEntries();
resolvers[1]({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({
    entries: [{kind: 'awi', state: 'inbox', filename: 'new.md', summary: 'new'}],
    warnings: []
  })
});
await second;
resolvers[0]({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({
    entries: [{kind: 'awi', state: 'inbox', filename: 'old.md', summary: 'old'}],
    warnings: []
  })
});
await first;
process.stdout.write(JSON.stringify({
  rows: entries.map(entry => entry.filename),
  loading: elements['entry-list'].attributes['aria-busy']
}));
"""
    )
    assert result == {
        "rows": ["new.md"],
        "loading": "false",
    }


def test_assets_discard_stale_search_fallback_error_without_overwriting_global_error() -> None:
    """失効した補助検索のエラーが後発要求のglobal-errorを上書きしない。"""
    result = _run_node_ui(
        """
let rejectFallback;
let fallbackStarted;
const fallbackReady = new Promise(resolve => { fallbackStarted = resolve; });
elements['search-input'].value = 'old';
fetchHandler = async url => {
  if (url === '/atk/api/entries?q=old&page=1') {
    fallbackStarted();
    return new Promise((_resolve, reject) => { rejectFallback = reject; });
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
elements['global-error'].textContent = '後発要求のエラー';
rejectFallback(new Error('失効した補助検索エラー'));
await oldRequest;
process.stdout.write(JSON.stringify({
  rows: entries.map(entry => entry.filename),
  state: entries[0]?.state,
  notice: elements['list-fallback-notice'].textContent,
  status: elements['result-status'].textContent,
  error: elements['global-error'].textContent
}));
"""
    )
    assert result == {
        "rows": ["new.md"],
        "state": "inbox",
        "notice": "",
        "status": "1件を表示",
        "error": "後発要求のエラー",
    }


def test_assets_ignore_error_from_stale_repo_candidate_request() -> None:
    """失効した候補要求の失敗で最新候補とエラー領域を上書きしない。"""
    result = _run_node_ui(
        """
let rejectAdopted;
fetchHandler = async url => {
  if (url.endsWith('/api/repos?status=adopted')) {
    return new Promise((_resolve, reject) => { rejectAdopted = reject; });
  }
  return {
    ok: true, status: 200, statusText: 'OK',
    json: async () => ({repos: ['active/repo']})
  };
};
elements['state-filter'].value = 'adopted';
const stale = loadTargetRepos();
await Promise.resolve();
elements['state-filter'].value = 'active';
await loadTargetRepos();
rejectAdopted(new Error('旧要求の失敗'));
await stale;
process.stdout.write(JSON.stringify({
  candidates: elements['target-filter'].children.map(option => option.value),
  error: elements['global-error-message'].textContent
}));
"""
    )
    assert result == {
        "candidates": ["", "active/repo"],
        "error": "",
    }


@pytest.mark.asyncio
async def test_batch_api_imports_entries(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """一括登録APIが保存名・対応・警告を返し、原文保持で保存する。"""
    _patch_batch_repo_operations(monkeypatch)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().post("/api/entries/batch", json={"text": _BATCH_TEXT})

    assert response.status_code == 201
    assert await response.get_json() == {
        "filenames": ["keep.md"],
        "mapping": {"keep.md": "keep.md"},
        "warnings": [],
    }
    assert (tmp_path / "inbox" / "keep.md").read_text(encoding="utf-8") == (
        "---\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n取り込む本文\n"
    )


def test_assets_offer_batch_creation_without_required_target_repo() -> None:
    """新規追加ダイアログが一括登録種別を持ち、対象リポジトリの必須指定を外す。"""
    assert '<option value="batch">一括登録（show形式）</option>' in assets.HTML
    assert 'id="create-repo-fields"' in assets.HTML
    assert "対象リポジトリ（frontmatterに無い場合は必須）" in assets.HTML
    assert '<input id="create-target" name="target_repo" list="repo-options" aria-describedby="create-target-error">' in (
        assets.HTML
    )
    assert "'/api/entries/batch'" in assets.JS
    assert "対象リポジトリを入力してください" not in assets.JS


def test_normal_creation_omits_empty_target_repo_from_payload() -> None:
    """対象リポジトリ欄が空の通常追加では、target_repoを送らずサーバー検証へ委ねる。"""
    result = _run_node_ui(
        """
elements['create-dialog'].open = true;
dialogStack.push('create-dialog');
elements['create-content'].value = '---\\ntarget_repo: example/repo\\n---\\n\\n本文';
fetchHandler = async (url) => {
  if (url.endsWith('/api/entries')) {
    return {ok: true, status: 201, statusText: 'Created', json: async () => ({filenames: ['new.md']})};
  }
  if (url.endsWith('/api/repos?status=active')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos: []})};
  }
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})};
};
await createEntry({preventDefault() {}});
const call = fetchCalls.find(item => item.url.endsWith('/api/entries'));
process.stdout.write(JSON.stringify({body: JSON.parse(call.options.body)}));
"""
    )
    assert result == {"body": {"type": "awi", "messages": ["---\ntarget_repo: example/repo\n---\n\n本文"]}}


@pytest.mark.asyncio
async def test_user_comment_api_rejects_non_inbox_states_and_uwi(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """processing・終端状態及びUWIには操作を提供しない。"""
    _patch_comment_edit_dependencies(monkeypatch)
    contents = {
        "processing": _session_review_awi("processing本文\n"),
        "adopted": _session_review_awi("adopted本文\n"),
        "rejected": _session_review_awi("rejected本文\n"),
        "uwi": _session_review_awi("UWI本文\n", entry_type="uwi"),
    }
    for state_name, content in contents.items():
        directory = tmp_path / ("inbox" if state_name == "uwi" else state_name)
        directory.mkdir(exist_ok=True)
        path = directory / f"{state_name}.md"
        path.write_text(content, encoding="utf-8")

    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()
    for state_name, content in contents.items():
        filename = f"{state_name}.md"
        response = await client.post(
            "/api/entries/user-comment",
            json={
                "state": "inbox" if state_name == "uwi" else state_name,
                "filename": filename,
                "comment": "追加コメント",
                "expected_content": content,
            },
        )
        assert response.status_code == 400
        actual_path = tmp_path / ("inbox" if state_name == "uwi" else state_name) / filename
        assert actual_path.read_text(encoding="utf-8") == content
