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


@pytest.mark.parametrize("host", ["", "  ", 1])
def test_invalid_host(host: object) -> None:
    """空又は非文字列hostを拒否する。"""
    with pytest.raises(ValueError):
        config.resolve_config(host=host)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]


def test_assets_are_self_contained() -> None:
    """UI資産の自己完結性とサブパス置換点を検証する。"""
    combined = assets.HTML + assets.CSS + assets.JS
    assert "https://" not in combined
    assert "http://" not in combined
    assert combined.count("innerHTML") == 1
    assert "entry.body_html ?? entry.content_html ?? ''" in assets.JS
    assert "eventSource.addEventListener('changed'" in assets.JS
    assert "entries-changed" not in assets.JS
    assert "insertAdjacentHTML" not in combined
    assert "/api/entries" in combined
    assert "/api/events" in combined
    assert "__BASE_PATH_HTML__" in assets.HTML
    assert "__BASE_PATH_JS__" in assets.JS
    assert f'<meta name="theme-color" content="{assets.THEME_COLOR}">' in assets.HTML
    assert 'rel="icon" type="image/svg+xml" href="__BASE_PATH_HTML__/favicon.svg"' in assets.HTML
    assert 'rel="manifest" href="__BASE_PATH_HTML__/manifest.webmanifest" crossorigin="use-credentials"' in assets.HTML


def test_assets_use_shared_dialog_shell_without_cancel_ui() -> None:
    """3ダイアログへ共通シェルと唯一の終了操作を適用する。"""
    assert assets.HTML.count("<dialog ") == 3
    assert assets.HTML.count('class="dialog-shell') == 3
    assert 'class="dialog-shell detail-dialog"' in assets.HTML
    for dialog_id, heading_id, close_id in [
        ("detail-dialog", "detail-heading", "detail-close-button"),
        ("create-dialog", "create-dialog-heading", "create-close-button"),
        ("delete-dialog", "delete-dialog-heading", "delete-close-button"),
    ]:
        dialog = re.search(rf'<dialog id="{dialog_id}"([^>]*)>(.*?)</dialog>', assets.HTML, re.DOTALL)
        assert dialog is not None
        assert f'aria-labelledby="{heading_id}"' in dialog.group(1)
        assert 'class="dialog-header"' in dialog.group(2)
        assert 'class="dialog-body"' in dialog.group(2)
        assert 'class="dialog-footer' in dialog.group(2)
        assert f'id="{close_id}"' in dialog.group(2)
        assert 'aria-label="閉じる"' in dialog.group(2)
    assert "unsaved-dialog" not in assets.HTML
    assert "cancel-" not in assets.HTML
    assert "requestDiscard" not in assets.JS
    assert "pendingDiscardAction" not in assets.JS
    assert "中止</button>" not in assets.HTML
    assert "width: 2.75rem;" in assets.CSS
    assert "height: 2.75rem;" in assets.CSS
    assert "overflow-y: auto;" in assets.CSS
    assert "#screen-wi dialog.dialog-shell {" in assets.CSS
    dialog_rule = re.search(
        r"#screen-wi dialog\.dialog-shell \{(.*?)\n\}",
        assets.CSS,
        re.DOTALL,
    )
    assert dialog_rule is not None
    assert "overflow: hidden;" in dialog_rule.group(1)


def test_assets_global_error_does_not_restore_refresh_after_user_focus_move() -> None:
    """同期中の消去後に利用者が別の入力へ移動した場合は、そのフォーカスを維持する。"""
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
elements['global-error-close-button'].focus();
elements['global-error-close-button'].listeners.click();
elements['search-input'].focus();
releaseSync();
await synchronization;
process.stdout.write(JSON.stringify({focused, hidden: elements['global-error'].hidden}));
"""
    )
    assert result == {"focused": "search-input", "hidden": True}


def test_assets_render_three_contextual_empty_states() -> None:
    """条件適用中、対応中0件、全件0件を別の回復操作へ案内する。"""
    result = _run_node_ui(
        """
entries = [];
renderEmptyState();
const active = {
  message: elements['empty-state-message'].textContent,
  allStates: !elements['empty-all-states-button'].hidden
};
elements['search-input'].value = 'none';
renderEmptyState();
const filtered = {
  message: elements['empty-state-message'].textContent,
  clear: !elements['empty-clear-button'].hidden
};
elements['search-input'].value = '';
elements['state-filter'].value = 'all';
renderEmptyState();
const all = {
  message: elements['empty-state-message'].textContent,
  create: !elements['empty-create-button'].hidden
};
process.stdout.write(JSON.stringify({active, filtered, all}));
"""
    )
    assert result == {
        "active": {"message": "対応中の項目はありません。", "allStates": True},
        "filtered": {"message": "条件に一致する項目はありません。", "clear": True},
        "all": {"message": "項目はまだありません。", "create": True},
    }


def test_assets_offer_hold_and_rejected_actions_and_preserve_implicit_resolution() -> None:
    """保留・不採用の操作表示と、採否APIの明示状態送信境界を検証する。"""
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
                "edit": False,
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


def test_assets_sse_detail_prefers_exact_identity_and_only_tracks_unique_move() -> None:
    """SSE詳細は複合キーを優先し、404後の一意な移動だけを追跡する。"""
    result = _run_node_ui(
        """
const processing = {
  kind: 'awi', state: 'processing', filename: 'same.md', answered: null,
  summary: '処理中', content: '処理中本文', body_html: '<p>処理中本文</p>', frontmatter_entries: []
};
const inbox = {
  ...processing, state: 'inbox', summary: '未処理', content: '未処理本文', body_html: '<p>未処理本文</p>'
};
const adopted = {
  ...processing, state: 'adopted', summary: '採用済み', content: '採用済み本文', body_html: '<p>採用済み本文</p>'
};
displayEntry(processing);
detailOriginKey = entryKey(processing);
elements['detail-dialog'].open = true;
entries = [inbox, processing];
const exactRequests = [];
fetchHandler = async url => {
  exactRequests.push(url);
  const entry = url.includes('/processing/') ? processing : inbox;
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entry})};
};
await reloadOpenDetailFromExternalChange();
const exact = {
  state: currentEntry.state,
  content: elements['detail-content'].innerHTML,
  requests: exactRequests
};

fetchHandler = async url => {
  if (url.includes('/processing/')) {
    return {ok: false, status: 404, statusText: 'Not Found', json: async () => ({error: 'not found'})};
  }
  if (url.includes('/inbox/')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entry: inbox})};
  }
  return {ok: false, status: 404, statusText: 'Not Found', json: async () => ({error: 'not found'})};
};
await reloadOpenDetailFromExternalChange();
const uniqueMove = {state: currentEntry.state, key: detailOriginKey, open: elements['detail-dialog'].open};

displayEntry(processing);
detailOriginKey = entryKey(processing);
elements['detail-dialog'].open = true;
fetchHandler = async url => {
  if (url.includes('/processing/')) {
    return {ok: false, status: 404, statusText: 'Not Found', json: async () => ({error: 'not found'})};
  }
  const entry = url.includes('/inbox/') ? inbox : adopted;
  if (url.includes('/inbox/') || url.includes('/adopted/')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entry})};
  }
  return {ok: false, status: 404, statusText: 'Not Found', json: async () => ({error: 'not found'})};
};
await reloadOpenDetailFromExternalChange();
process.stdout.write(JSON.stringify({
  exact,
  uniqueMove,
  ambiguous: {
    detailOpen: elements['detail-dialog'].open,
    currentEntryIsNull: currentEntry === null,
    error: elements['global-error-message'].textContent
  }
}));
"""
    )
    assert result == {
        "exact": {
            "state": "processing",
            "content": "<p>処理中本文</p>",
            "requests": ["/atk/api/entries/processing/same.md"],
        },
        "uniqueMove": {
            "state": "inbox",
            "key": "inbox/same.md",
            "open": True,
        },
        "ambiguous": {
            "detailOpen": False,
            "currentEntryIsNull": True,
            "error": "same.mdの移動先を一意に特定できません。詳細を開き直してください。",
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


@pytest.mark.asyncio
async def test_state_publishes_only_markdown_change_events(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Markdownの変更イベントだけを購読者へ通知する。"""
    events = [
        ("on_opened", watchdog.events.FileOpenedEvent(str(tmp_path / "entry.md")), False),
        ("on_closed_no_write", watchdog.events.FileClosedNoWriteEvent(str(tmp_path / "entry.md")), False),
        ("on_created", watchdog.events.FileCreatedEvent(str(tmp_path / "entry.md")), True),
        ("on_modified", watchdog.events.FileModifiedEvent(str(tmp_path / "entry.md")), True),
        ("on_deleted", watchdog.events.FileDeletedEvent(str(tmp_path / "entry.md")), True),
        ("on_created", watchdog.events.FileCreatedEvent(str(tmp_path / "entry.lock")), False),
        ("on_modified", watchdog.events.FileModifiedEvent(str(tmp_path / "entry.lock")), False),
        ("on_deleted", watchdog.events.FileDeletedEvent(str(tmp_path / "entry.lock")), False),
        (
            "on_moved",
            watchdog.events.FileMovedEvent(
                str(tmp_path / "entry.tmp"),
                str(tmp_path / "entry.md"),
            ),
            True,
        ),
        (
            "on_moved",
            watchdog.events.FileMovedEvent(
                str(tmp_path / "entry.md"),
                str(tmp_path / "entry.tmp"),
            ),
            True,
        ),
        (
            "on_moved",
            watchdog.events.FileMovedEvent(
                str(tmp_path / "entry.lock"),
                str(tmp_path / "entry.lock.bak"),
            ),
            False,
        ),
    ]
    for handler_name, event, expected in events:
        current = state.ServeState(tmp_path, debounce_seconds=0)
        current._loop = asyncio.get_running_loop()
        published = threading.Event()
        monkeypatch.setattr(current, "publish", published.set)
        getattr(current, handler_name)(event)
        await asyncio.sleep(0)
        assert published.is_set() is expected


@pytest.mark.asyncio
async def test_plan_and_session_pages_apply_base_path(tmp_path: pathlib.Path) -> None:
    """追加した2画面のページがベースパスを反映する。"""
    app = _three_screen_app(tmp_path)
    client = app.test_client()
    for path in ("/atk/plans", "/atk/sessions"):
        response = await client.get(path, headers={"X-Forwarded-Prefix": "/atk"})
        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        assert 'href="/atk/"' in body
        assert '"base_path": "/atk"' in body
        assert "/atk/atk/" not in body


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["file", "raw"])
async def test_plan_file_apis_return_plan_file_error_status(tmp_path: pathlib.Path, route: str) -> None:
    """計画ファイル取得APIは取得層が分類した未検出と入力不正の状態コードを返す。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
        plans_context=serve_plans.create_context(
            root=tmp_path / "plans",
            hostname="local-host",
            remote_hosts=["remote-host"],
        ),
    )
    client = app.test_client()

    assert (await client.get(f"/api/plans/{route}?path=missing.md")).status_code == 404
    assert (await client.get(f"/api/plans/{route}?path=../secret.md&host=remote-host")).status_code == 400


@pytest.mark.asyncio
async def test_sse_multiple_subscribers_and_cleanup(tmp_path: pathlib.Path) -> None:
    """複数購読者へ変更通知を配信し、切断後に購読を除去する。"""
    current = state.ServeState(tmp_path)
    first = current.events(heartbeat=1)
    second = current.events(heartbeat=1)
    first_next = asyncio.create_task(anext(first))
    second_next = asyncio.create_task(anext(second))
    await asyncio.sleep(0)
    current.publish()
    assert "event: changed" in await first_next
    assert "event: changed" in await second_next
    await first.aclose()
    await second.aclose()


@pytest.mark.asyncio
async def test_read_routes_remain_available_during_entry_move(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """状態移動と競合した一覧はスナップショット、詳細は404を返し409にしない。"""
    inbox = tmp_path / "inbox"
    adopted = tmp_path / "adopted"
    inbox.mkdir()
    adopted.mkdir()
    entry = inbox / "entry.md"
    entry.write_text("---\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n", encoding="utf-8")
    original_entry_type_from_metadata = common.entry_type_from_metadata

    async def race_request(path: str) -> typing.Any:
        started = threading.Event()
        release = threading.Event()

        def entry_type_from_metadata(entry_path: pathlib.Path, metadata: typing.Mapping[str, object]) -> str:
            started.set()
            release.wait()
            return original_entry_type_from_metadata(entry_path, metadata)

        monkeypatch.setattr(common, "entry_type_from_metadata", entry_type_from_metadata)
        request = asyncio.create_task(app.test_client().get(path))
        await asyncio.to_thread(started.wait)
        entry.rename(adopted / entry.name)
        release.set()
        return await request

    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    entries_response = await race_request("/api/entries?status=inbox")
    assert entries_response.status_code == 200
    assert await entries_response.get_json() == {"entries": [], "warnings": []}

    entry = adopted / "entry.md"
    entry.rename(inbox / entry.name)
    entry = inbox / "entry.md"
    detail_response = await race_request("/api/entries/inbox/entry.md")
    assert detail_response.status_code == 404


@pytest.mark.parametrize(
    "text",
    ["本文のみ\n", "---\ninvalid: [unterminated\n---\n本文\n"],
)
def test_detail_returns_empty_frontmatter_when_unavailable(tmp_path: pathlib.Path, text: str) -> None:
    """frontmatterが無い又は解析できない詳細は空の表示用一覧を返す。"""
    _write_detail_entry(tmp_path, text)
    detail = serve_app.Operations(tmp_path).detail("inbox", "entry.md")
    assert not detail["frontmatter_entries"]


@pytest.mark.asyncio
async def test_detail_api_preserves_top_level_key_types_and_order(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """詳細HTTP APIは整数形式キーを含むトップレベル項目の型と挿入順を保持する。"""
    _write_detail_entry(
        tmp_path,
        "---\ntype: awi\nz_key: z\na_key: a\n---\n\n本文\n",
    )

    original_parse = serve_app.frontmatter.parse_frontmatter

    def parse_with_integer_key(text: str) -> tuple[dict[typing.Any, typing.Any], str] | None:
        parsed = original_parse(text)
        if parsed is None:
            return None
        metadata, body = parsed
        enriched: dict[typing.Any, typing.Any] = {}
        for key, value in metadata.items():
            enriched[key] = value
            if key == "z_key":
                enriched[1] = "numeric"
                enriched["1"] = "textual"
        return enriched, body

    monkeypatch.setattr(serve_app.frontmatter, "parse_frontmatter", parse_with_integer_key)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().get("/api/entries/inbox/entry.md")

    assert response.status_code == 200
    payload = json.loads(await response.get_data())
    assert payload["entry"]["frontmatter_entries"] == [
        {"key": {"type": "str", "value": "type"}, "value": "awi"},
        {"key": {"type": "str", "value": "z_key"}, "value": "z"},
        {"key": {"type": "int", "value": 1}, "value": "numeric"},
        {"key": {"type": "str", "value": "1"}, "value": "textual"},
        {"key": {"type": "str", "value": "a_key"}, "value": "a"},
    ]


def test_render_body_renders_footnote_with_document_anchor() -> None:
    """注記記法は本文と同一文書内の参照リンクとして描画する。"""
    rendered = serve_app._render_body("本文です[^1]。\n\n[^1]: 注記の本文\n")

    assert "注記の本文" in rendered
    assert 'href="#fn1"' in rendered
    assert 'href="%E6%B3%A8%E8%A8%98%E3%81%AE%E6%9C%AC%E6%96%87"' not in rendered


def test_operations_answered_filter_returns_only_answered_uwis(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`answered=yes`は回答済みUWIのみを返し、未回答UWI・AWIを除外する。"""
    monkeypatch.setattr(common, "repo_lock", lambda *_a, **_k: contextlib.nullcontext())
    monkeypatch.setattr(common, "pull", lambda _path: None)
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "answered.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n回答済み\n",
        encoding="utf-8",
    )
    (inbox / "unanswered.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    (inbox / "awi.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\nAWI本文\n",
        encoding="utf-8",
    )
    result, warnings = serve_app.Operations(tmp_path).entries_with_warnings({"answered": "yes"})
    assert not warnings
    assert [item["filename"] for item in result] == ["answered.md"]


@pytest.mark.asyncio
async def test_entries_api_filters_source_kind_and_preserves_raw_source_filters(tmp_path: pathlib.Path) -> None:
    """一覧APIは投入元分類を適用し、既存のraw投入元フィルターを維持する。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    for filename, source in [
        ("agent.md", "agent"),
        ("alert-monitor.md", "alert-monitor"),
        ("review.md", "session-review"),
        ("plan.md", "plan"),
        ("unknown.md", "web"),
        ("human.md", "human"),
        ("empty.md", None),
    ]:
        metadata = "type: awi\ntarget_repo: example/repo"
        if source is not None:
            metadata += f"\nsource: {source}"
        (inbox / filename).write_text(f"---\n{metadata}\n---\n\n本文\n", encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    agent = await client.get("/api/entries?status=inbox&type=awi&source_kind=agent")
    human = await client.get("/api/entries?status=inbox&type=awi&source_kind=human")
    raw = await client.get("/api/entries?status=inbox&type=awi&source=session-review")
    empty = await client.get("/api/entries?status=inbox&type=awi&source_empty=true")

    assert {item["filename"] for item in (await agent.get_json())["entries"]} == {
        "agent.md",
        "alert-monitor.md",
        "human.md",
        "plan.md",
        "review.md",
        "unknown.md",
    }
    assert {item["filename"] for item in (await human.get_json())["entries"]} == {"empty.md"}
    assert [item["filename"] for item in (await raw.get_json())["entries"]] == ["review.md"]
    assert {item["filename"] for item in (await empty.get_json())["entries"]} == {"empty.md"}


@pytest.mark.asyncio
async def test_remove_api_rejects_changed_and_unreadable_expected_content(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """内容変更と読取り不能を409で保護し、空の確認時本文も受理する。"""

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
    original = "---\ntype: awi\n---\n\n確認時本文\n"
    changed = inbox / "changed.md"
    changed.write_text(original.replace("確認時", "外部更新後"), encoding="utf-8")
    unreadable = inbox / "unreadable.md"
    unreadable.write_bytes(b"\xff")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    for filename in (changed.name, unreadable.name):
        response = await client.post(
            "/api/entries/remove",
            json={
                "filenames": [filename],
                "state": "inbox",
                "expected_content": original,
                "force": False,
            },
        )
        assert response.status_code == 409
        assert await response.get_json() == {
            "code": "edit_conflict",
            "error": "編集中に他プロセスが対象を変更しました",
        }
        assert (inbox / filename).exists()

    empty = inbox / "empty.md"
    empty.write_text("", encoding="utf-8")
    empty_response = await client.post(
        "/api/entries/remove",
        json={
            "filenames": [empty.name],
            "state": "inbox",
            "expected_content": "",
            "force": False,
        },
    )
    assert empty_response.status_code == 200
    assert not empty.exists()


@pytest.mark.asyncio
async def test_unrelated_runtime_error_is_not_classified_as_edit_conflict(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """競合以外のRuntimeErrorを409へ誤分類しない。"""
    operations = serve_app.Operations(tmp_path)

    def fail(
        _state: str,
        _filename: str,
        _content: str,
        _expected_content: str | None = None,
    ) -> bool:
        raise RuntimeError("別の実行時エラー")

    monkeypatch.setattr(operations, "edit", fail)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
        operations=operations,
    )
    response = await app.test_client().put(
        "/api/entries/inbox/entry.md",
        json={"content": "本文"},
    )
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_entries_api_omits_pagination_without_explicit_page_and_clamps_final_page(
    tmp_path: pathlib.Path,
) -> None:
    """ページ省略時の既存payloadを維持し、最終ページを超える指定を末尾へ正規化する。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for index in range(101):
        (inbox / f"entry-{index:03d}.md").write_text(
            "---\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n",
            encoding="utf-8",
        )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    complete = await client.get("/api/entries?status=inbox")
    clamped = await client.get("/api/entries?status=inbox&page=999")

    complete_payload = await complete.get_json()
    clamped_payload = await clamped.get_json()
    assert set(complete_payload) == {"entries", "warnings"}
    assert len(complete_payload["entries"]) == 101
    assert clamped_payload["pagination"] == {
        "page": 2,
        "page_size": 100,
        "page_count": 2,
        "total_count": 101,
    }
    assert len(clamped_payload["entries"]) == 1
    assert clamped_payload["entries"][0]["filename"] == complete_payload["entries"][-1]["filename"]


def test_entries_reports_os_error_without_treating_unknown_kind_as_warning(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OS読取り失敗だけを警告へ分離し、種別不明のMarkdownは一覧へ残す。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "unknown.md").write_text("種別を判定できない本文\n", encoding="utf-8")
    failed_path = inbox / "os-error.md"
    failed_path.touch()
    original_read_text = pathlib.Path.read_text

    def read_text(
        path: pathlib.Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if path == failed_path:
            raise OSError("読取り失敗")
        return original_read_text(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(pathlib.Path, "read_text", read_text)

    entries, warnings = serve_app.Operations(tmp_path).entries_with_warnings({"status": "inbox"})

    assert [(entry["filename"], entry["kind"]) for entry in entries] == [("unknown.md", "unknown")]
    assert warnings == [{"filename": "os-error.md", "reason": "ファイルを読み取れません"}]


@pytest.mark.asyncio
async def test_invalid_json_returns_json_error(tmp_path: pathlib.Path) -> None:
    """構文エラーのJSON本文を一貫したJSON 400応答へ変換する。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(
        "/api/entries",
        data='{"messages":',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert await response.get_json() == {"error": "JSON本文の構文が不正です"}


@pytest.mark.asyncio
async def test_lock_timeout_returns_conflict(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """mutationの有限待機ロック競合をJSON 409応答へ変換する。"""

    def edit(
        _state: str,
        _filename: str,
        _content: str,
        _expected_content: str | None = None,
    ) -> bool:
        raise filelock.Timeout("locked")

    operations = serve_app.Operations(tmp_path)
    monkeypatch.setattr(operations, "edit", edit)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
        operations=operations,
    )
    response = await app.test_client().put(
        "/api/entries/inbox/entry.md",
        json={"content": "本文"},
    )
    assert response.status_code == 409
    assert await response.get_json() == {
        "error": "別の操作が進行中です",
        "code": "lock_conflict",
    }


@pytest.mark.asyncio
async def test_safe_base_path_rejects_value_that_proxy_fix_accepts(tmp_path: pathlib.Path) -> None:
    """`ProxyFix`が受理する文字集合でも`_safe_base_path`固有の文字種制限に反する値は空文字列へ縮退する。

    `pytilpack.web.validate_forwarded_prefix`は`:`・`@`等RFC3986のpchar相当を広く許可するのに対し、
    `_safe_base_path`は英数字と`._~/-`のみへ限定しており両者の許容範囲は完全には一致しない。
    `:`を含む値は`ProxyFix`層を通過するため、この値で`_safe_base_path`固有の拒否分岐が
    実際に機能していることを検証する。
    """
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), state.ServeState(tmp_path))
    client = app.test_client()
    headers = {"X-Forwarded-Prefix": "/atk:1"}

    index_response = await client.get("/atk:1/", headers=headers)
    assert index_response.status_code == 200
    index_body = await index_response.get_data(as_text=True)
    assert 'href="/static/app.css"' in index_body
    assert "atk:1" not in index_body

    js_body = await (await client.get("/atk:1/static/app.js", headers=headers)).get_data(as_text=True)
    assert 'const BASE_PATH="";' in js_body


def test_console_title_builds_command_and_port() -> None:
    """ターミナルタイトルにコマンド名とポートを含める。"""
    assert serve.build_console_title(28766) == "atk serve :28766"


@pytest.mark.asyncio
async def test_serve_shuts_down_on_signal_and_stops_state(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """シグナル集約でshutdown_triggerが解除され、停止時にstateを止める。"""
    stopped: list[str] = []
    _stub_state(monkeypatch, tmp_path, stopped)
    handlers: dict[int, typing.Callable[[], None]] = {}
    loop = asyncio.get_running_loop()

    def add_signal_handler(sig: int, callback: typing.Callable[[], None]) -> None:
        handlers[sig] = callback

    monkeypatch.setattr(loop, "add_signal_handler", add_signal_handler)
    observed: dict[str, object] = {}

    async def fake_serve(app: object, hypercorn_config: typing.Any, *, shutdown_trigger: typing.Any) -> None:
        del app
        observed["bind"] = hypercorn_config.bind[0]
        observed["graceful_timeout"] = hypercorn_config.graceful_timeout
        observed["accesslog"] = hypercorn_config.accesslog
        # シグナル受信を模擬してshutdown_triggerを解除する。
        handlers[signal.SIGTERM]()
        await shutdown_trigger()

    monkeypatch.setattr(serve.hypercorn.asyncio, "serve", fake_serve)
    await serve._serve(tmp_path, config.ServeConfig("127.0.0.1", 28766))

    assert observed["bind"] == "127.0.0.1:28766"
    assert observed["graceful_timeout"] == 1.0
    assert observed["accesslog"] is None
    assert set(handlers) == {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}
    assert stopped == ["stop"]


@pytest.mark.asyncio
async def test_background_sync_task_starts_and_stops(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """定期更新タスクが起動時に開始し終了時に停止する。"""
    monkeypatch.setattr(serve_app, "_BACKGROUND_SYNC_INTERVAL_SECONDS", 0.001)
    calls: list[str] = []
    called = asyncio.Event()
    loop = asyncio.get_running_loop()

    class _Operations(serve_app.Operations):
        def background_sync(self) -> bool:
            calls.append("sync")
            loop.call_soon_threadsafe(called.set)
            return True

    current_state = state.ServeState(tmp_path)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        current_state,
        operations=_Operations(tmp_path),
    )
    async with app.test_app():  # ty: ignore[invalid-context-manager]
        # 実時間の経過ではなく初回呼び出しの成立を待ち、起動を確認する。
        await asyncio.wait_for(called.wait(), timeout=5)

    # 停止の成立は、`after_serving`の完了後に呼び出し回数が増えないことで確認する。
    # 実行中の周期が残りうるため、基準を取る前に処理待ちのタスクを消化させる。
    await asyncio.sleep(0)
    before = len(calls)
    called.clear()
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(called.wait(), timeout=0.1)
    assert len(calls) == before


def test_assets_ignore_out_of_order_repo_candidates_for_state_changes() -> None:
    """状態変更の旧候補応答を破棄し、旧処理から一覧要求を開始しない。"""
    result = _run_node_ui(
        """
let resolveAdopted;
const listUrls = [];
fetchHandler = async url => {
  if (url.endsWith('/api/repos?status=adopted')) {
    return new Promise(resolve => { resolveAdopted = resolve; });
  }
  if (url.endsWith('/api/repos?status=active')) {
    return {
      ok: true, status: 200, statusText: 'OK',
      json: async () => ({repos: ['active/repo']})
    };
  }
  if (url.includes('/api/entries?')) {
    listUrls.push(url);
    return {
      ok: true, status: 200, statusText: 'OK',
      json: async () => ({
        entries: [{kind: 'awi', state: 'inbox', filename: 'active.md', summary: 'active'}],
        warnings: []
      })
    };
  }
  throw new Error('想定外のURL: ' + url);
};
elements['state-filter'].value = 'adopted';
const stale = handleFilterChange({reloadRepos: true});
await Promise.resolve();
elements['state-filter'].value = 'active';
await handleFilterChange({reloadRepos: true});
resolveAdopted({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({repos: ['adopted/repo']})
});
await stale;
process.stdout.write(JSON.stringify({
  state: elements['state-filter'].value,
  candidates: elements['target-filter'].children.map(option => option.value),
  listUrls,
  rows: entries.map(entry => entry.filename)
}));
"""
    )
    assert result == {
        "state": "active",
        "candidates": ["", "active/repo"],
        "listUrls": ["/atk/api/entries?type=all&status=active&answered=all&page=1"],
        "rows": ["active.md"],
    }


def test_assets_preserve_user_announcement_across_user_and_sse_request_orders() -> None:
    """利用者要求とSSE要求の開始・完了順にかかわらず、件数を一度通知する。"""
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    ["---\ntarget_repo:\n- github.com/example/repo\n---\n\n本文", "本文だけ"],
)
async def test_add_api_rejects_omitted_target_repo_without_frontmatter_value(
    tmp_path: pathlib.Path,
    message: str,
) -> None:
    """frontmatterのtarget_repoが欠落・非文字列の場合は400で拒否する。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().post("/api/entries", json={"type": "awi", "messages": [message]})

    assert response.status_code == 400
    assert "target_repo" in (await response.get_json())["error"]


@pytest.mark.asyncio
async def test_batch_api_imports_crlf_text(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CRLF改行の一括登録テキストも取り込み、保存内容の改行をLFへ正規化する。"""
    _patch_batch_repo_operations(monkeypatch)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().post("/api/entries/batch", json={"text": _BATCH_TEXT.replace("\n", "\r\n")})

    assert response.status_code == 201
    assert (await response.get_json())["filenames"] == ["keep.md"]
    assert (tmp_path / "inbox" / "keep.md").read_text(encoding="utf-8") == (
        "---\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n取り込む本文\n"
    )


def test_assets_state_sets_match_python_states() -> None:
    """フロントエンドが持つ状態集合をPython側の保存状態と一致させる。"""
    labels = re.search(r"const STATE_LABELS = \{(.*?)\n\};", assets.JS, re.DOTALL)
    assert labels is not None
    assert set(re.findall(r"(\w+):", labels.group(1))) == set(common.WI_STATES)

    deletable = re.search(r"const DELETABLE_STATES = new Set\(\[(.*?)\]\);", assets.JS)
    assert deletable is not None
    assert set(re.findall(r"'(\w+)'", deletable.group(1))) == set(common.WI_STATES)

    processable = re.search(r"const PROCESSABLE_STATES = new Set\(\[(.*?)\]\);", assets.JS)
    assert processable is not None
    assert set(re.findall(r"'(\w+)'", processable.group(1))) == set(common.WI_PROCESSABLE_STATES)


@pytest.mark.parametrize(
    ("body", "comment", "message"),
    [
        (
            "本文\n\n## ユーザーコメント\n\nコメント\n\n## 後続見出し\n\n後続本文\n",
            "更新",
            "ユーザーコメント節の後ろに別のH2見出しがあります",
        ),
        (
            "本文\n\n## ユーザーコメント\n\n一つ目\n\n## ユーザーコメント\n\n二つ目\n",
            "更新",
            "ユーザーコメント節が複数あります",
        ),
        ("本文\n", "## コメント内見出し", "ユーザーコメントにコードフェンス外のH2見出しを含められません"),
        ("本文\n", "```markdown\n## コメント内見出し\n```", ""),
        ("本文\n", " \n\n", "ユーザーコメントは空にできません"),
    ],
)
def test_user_comment_pure_update_rejects_invalid_structure_without_mutation(
    body: str,
    comment: str,
    message: str,
) -> None:
    """不正な節・コメントは判別可能なエラーとなり、保存対象を変更しない。"""
    original = _session_review_awi(body)

    if message:
        with pytest.raises(user_comment.UserCommentError, match=re.escape(message)):
            user_comment.update_user_comment(original, comment)
        assert original == _session_review_awi(body)
    else:
        updated = user_comment.update_user_comment(original, comment)
        assert user_comment.extract_user_comment(updated) == comment


@pytest.mark.asyncio
async def test_user_comment_api_returns_edit_conflict_without_losing_latest_content(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """期待本文と現行本文が異なる場合は409として最新本文を保持する。"""
    _patch_comment_edit_dependencies(monkeypatch)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    original = _session_review_awi("取得時本文\n")
    latest = _session_review_awi("外部更新後の本文\n")
    path = inbox / "awi.md"
    path.write_text(original, encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    detail = await (await app.test_client().get("/api/entries/inbox/awi.md")).get_json()
    path.write_text(latest, encoding="utf-8")

    response = await app.test_client().post(
        "/api/entries/user-comment",
        json={
            "state": "inbox",
            "filename": "awi.md",
            "comment": "古い基準のコメント",
            "expected_content": detail["entry"]["content"],
        },
    )

    assert response.status_code == 409
    assert await response.get_json() == {
        "code": "edit_conflict",
        "error": "編集中に他プロセスが対象を変更しました",
    }
    assert path.read_text(encoding="utf-8") == latest
