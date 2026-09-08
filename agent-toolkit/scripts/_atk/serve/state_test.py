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


@pytest.mark.parametrize("port", [True, 0, 65536])
def test_invalid_port(port: object) -> None:
    """bool又は範囲外portを拒否する。"""
    with pytest.raises(ValueError):
        config.resolve_config(port=port)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]


def test_assets_define_dismissible_global_error_region() -> None:
    """共通エラーに本文、アクセシブルな消去操作及び十分な操作領域を持たせる。"""
    assert '<div id="global-error" class="global-error" hidden>' in assets.HTML
    assert '<div id="global-error-message" role="alert"></div>' in assets.HTML
    assert (
        '<button id="global-error-close-button" class="global-error-close" type="button" '
        'aria-label="エラーメッセージを閉じる">×</button>'
    ) in assets.HTML
    global_error = re.search(
        r"#screen-wi \.global-error \{(.*?)\n\}",
        assets.CSS,
        re.DOTALL,
    )
    assert global_error is not None
    assert "display: flex;" in global_error.group(1)
    message = re.search(
        r"#screen-wi \.global-error-message \{(.*?)\n\}",
        assets.CSS,
        re.DOTALL,
    )
    assert message is not None
    assert "overflow-wrap: anywhere;" in message.group(1)
    close = re.search(
        r"#screen-wi \.global-error-close \{(.*?)\n\}",
        assets.CSS,
        re.DOTALL,
    )
    assert close is not None
    assert "width: 2.75rem;" in close.group(1)
    assert "height: 2.75rem;" in close.group(1)
    assert "min-width: 2.75rem;" in close.group(1)
    assert "min-height: 2.75rem;" in close.group(1)


def test_assets_style_markdown_and_inputs_by_purpose() -> None:
    """本文、コード、用途別入力、モバイル操作の表示契約を固定する。"""
    assert "#screen-wi .markdown-body :not(pre) > code {" in assets.CSS
    pre_rule = re.search(
        r"#screen-wi \.markdown-body pre \{(.*?)\n\}",
        assets.CSS,
        re.DOTALL,
    )
    assert pre_rule is not None
    assert "white-space: pre-wrap;" in pre_rule.group(1)
    assert "overflow-wrap: anywhere;" in pre_rule.group(1)
    assert "#screen-wi .markdown-body pre code {" in assets.CSS
    assert "padding: 0;" in assets.CSS
    assert "background: transparent;" in assets.CSS
    for selector in (
        "#screen-wi #edit-content",
        "#screen-wi #answer-input",
        "#screen-wi #create-content",
        "#screen-wi #create-choices",
    ):
        rule = re.search(rf"{re.escape(selector)} \{{([^}}]+)\}}", assets.CSS)
        assert rule is not None
        assert "clamp(" in rule.group(1)
    mobile = assets.CSS.partition("@media (max-width: 700px) {")[2]
    # ヘッダーの1列化は共通規則が定め、画面固有のCSSは水平方向の余白だけを上書きする。
    assert "#screen-wi .app-header {" in mobile
    assert "padding-inline: var(--space-2);" in mobile
    assert "#screen-wi .dialog-footer button {" in mobile
    assert "width: auto;" in mobile
    assert "button,\n  input,\n  select,\n  textarea" not in mobile
    assert "grid-template-columns: minmax(0, 1fr);" in mobile


def test_assets_global_error_uses_shared_lifecycle_for_all_generators() -> None:
    """共通エラーの消去・再表示と、各生成元の同一表示経路を検証する。"""
    result = _run_node_ui(
        """
bindEvents();
setGlobalError('最初のエラー');
const shown = {
  message: elements['global-error-message'].textContent,
  hidden: elements['global-error'].hidden
};
elements['global-error-close-button'].listeners.click();
const cleared = {
  message: elements['global-error-message'].textContent,
  hidden: elements['global-error'].hidden,
  focused
};
setGlobalError('後続のエラー');
const redisplayed = {
  message: elements['global-error-message'].textContent,
  hidden: elements['global-error'].hidden
};
const failures = [];
fetchHandler = async () => ({
  ok: false, status: 500, statusText: 'Error', json: async () => ({error: '一覧取得失敗'})
});
await loadEntries();
failures.push(elements['global-error-message'].textContent);
fetchHandler = async () => ({
  ok: false, status: 500, statusText: 'Error', json: async () => ({error: '対象取得失敗'})
});
await loadTargetRepos();
failures.push(elements['global-error-message'].textContent);
fetchHandler = async () => ({
  ok: false, status: 500, statusText: 'Error', json: async () => ({error: '詳細取得失敗'})
});
await selectEntry({state: 'inbox', filename: 'detail.md'}, new Element('detail-origin', 'BUTTON'));
failures.push(elements['global-error-message'].textContent);
const ambiguous = {
  kind: 'awi', state: 'processing', filename: 'ambiguous.md', content: '本文', body_html: '<p>本文</p>',
  frontmatter_entries: []
};
displayEntry(ambiguous);
detailOriginKey = entryKey(ambiguous);
elements['detail-dialog'].open = true;
fetchHandler = async url => {
  if (url.includes('/processing/')) {
    return {ok: false, status: 404, statusText: 'Not Found', json: async () => ({error: 'not found'})};
  }
  if (url.includes('/inbox/') || url.includes('/adopted/')) {
    const state = url.includes('/inbox/') ? 'inbox' : 'adopted';
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entry: {...ambiguous, state}})};
  }
  return {ok: false, status: 404, statusText: 'Not Found', json: async () => ({error: 'not found'})};
};
await reloadOpenDetailFromExternalChange();
failures.push(elements['global-error-message'].textContent);
refreshKnownUwis = async () => { throw new Error('SSE更新失敗'); };
fetchHandler = async () => ({
  ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: [], repos: []})
});
await reloadFromExternalChange();
await Promise.resolve();
failures.push(elements['global-error-message'].textContent);
deliverOperationMessage('ダイアログ外失敗', true);
failures.push(elements['operation-notice-message'].textContent);
refreshKnownUwis = async () => { throw new Error('初期化失敗'); };
initializeApp();
await initialization;
failures.push(elements['global-error-message'].textContent);
process.stdout.write(JSON.stringify({shown, cleared, redisplayed, failures}));
"""
    )
    assert result == {
        "shown": {"message": "最初のエラー", "hidden": False},
        "cleared": {"message": "", "hidden": True, "focused": "refresh-button"},
        "redisplayed": {"message": "後続のエラー", "hidden": False},
        "failures": [
            "一覧取得失敗",
            "対象取得失敗",
            "詳細取得失敗",
            "ambiguous.mdの移動先を一意に特定できません。詳細を開き直してください。",
            "SSE更新失敗",
            "ダイアログ外失敗",
            "初期化失敗",
        ],
    }


def test_assets_restore_focus_on_escape_and_focus_delete_close_first() -> None:
    """Escapeで起点へ戻り、破壊確認では非破壊の閉じる操作へ初期フォーカスを置く。"""
    result = _run_node_ui(
        """
const origin = new Element('create-origin', 'BUTTON');
attachDialogCloseHandlers('create-dialog', 'create-close-button', () => closeDialog(elements['create-dialog']));
openDialog(elements['create-dialog'], origin, elements['create-content']);
elements['create-dialog'].listeners.cancel({preventDefault() {}});
const restored = focused;
displayEntry({
  kind: 'awi', state: 'processing', filename: 'entry.md', answered: null,
  summary: '要約', target_repo: 'example/repo', content: 'raw', body_html: '<p>本文</p>',
  question_type: 'free-form', choices: [], frontmatter_entries: []
});
elements['detail-dialog'].open = true;
dialogStack.push('detail-dialog');
openDeleteDialog();
process.stdout.write(JSON.stringify({
  restored,
  deleteInitial: focused,
  closeSizeClass: elements['delete-close-button'].className,
  detailStillOpen: elements['detail-dialog'].open
}));
"""
    )
    assert result == {
        "restored": "create-origin",
        "deleteInitial": "delete-close-button",
        "closeSizeClass": "dialog-close",
        "detailStillOpen": True,
    }


def test_assets_reload_open_detail_from_sse_and_preserve_editing_input() -> None:
    """SSE更新時に閲覧本文を再取得し、編集中は入力を保持して消失時に閉じる。"""
    result = _run_node_ui(
        """
const listed = {
  kind: 'awi', state: 'inbox', filename: 'entry.md', answered: null,
  summary: '一覧要約', target_repo: 'example/repo', frontmatter_entries: []
};
let detailContent = '外部更新後の本文';
let deleted = false;
fetchHandler = async url => {
  if (url.includes('/api/repos?')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos: ['example/repo']})};
  }
  if (url.includes('/api/entries?')) {
    return {
      ok: true, status: 200, statusText: 'OK',
      json: async () => ({entries: deleted ? [] : [listed], warnings: []})
    };
  }
  if (url.includes('/api/entries/')) {
    if (deleted) {
      return {ok: false, status: 404, statusText: 'Not Found', json: async () => ({error: 'not found'})};
    }
    return {
      ok: true, status: 200, statusText: 'OK',
      json: async () => ({entry: {...listed, content: detailContent, body_html: `<p>${detailContent}</p>`}})
    };
  }
  throw new Error('想定外のURL: ' + url);
};
const original = {...listed, content: '更新前の本文', body_html: '<p>更新前の本文</p>'};
entries = [original];
renderList();
const origin = elements['entry-list'].children[0].children[0];
displayEntry(original);
detailOriginKey = entryKey(original);
openDialog(elements['detail-dialog'], origin, elements['detail-dialog-body']);
await reloadFromExternalChange();
const viewed = elements['detail-content'].innerHTML;
enterEdit();
elements['edit-content'].value = '利用者の未保存本文';
detailContent = '編集中の外部更新';
await reloadFromExternalChange();
const editing = {
  input: elements['edit-content'].value,
  baseline: currentEntry.content,
  alert: elements['detail-alert'].textContent,
  saveDisabled: elements['save-entry-button'].disabled,
  open: elements['detail-dialog'].open
};
setDetailMode('answer');
elements['answer-input'].value = '利用者の未保存回答';
detailContent = '回答中の外部更新';
await reloadFromExternalChange();
const answering = {
  input: elements['answer-input'].value,
  alert: elements['detail-alert'].textContent,
  saveDisabled: elements['save-answer-button'].disabled,
  open: elements['detail-dialog'].open
};
deleted = true;
await reloadFromExternalChange();
process.stdout.write(JSON.stringify({
  viewed,
  detailRequests: fetchCalls.filter(call => call.url.includes('/api/entries/')).length,
  editing,
  answering,
  afterDelete: {open: elements['detail-dialog'].open, focused}
}));
"""
    )
    assert result["viewed"] == "<p>外部更新後の本文</p>"
    assert result["detailRequests"] >= 3
    assert result["editing"] == {
        "input": "利用者の未保存本文",
        "baseline": "外部更新後の本文",
        "alert": "外部で項目が更新されました。入力を保持しています。詳細を閉じて開き直してから保存してください。",
        "saveDisabled": True,
        "open": True,
    }
    assert result["answering"] == {
        "input": "利用者の未保存回答",
        "alert": "外部で項目が更新されました。入力を保持しています。詳細を閉じて開き直してから保存してください。",
        "saveDisabled": True,
        "open": True,
    }
    assert result["afterDelete"] == {"open": False, "focused": "empty-all-states-button"}


def test_assets_delete_conflict_requires_detail_reload() -> None:
    """削除競合後は古い詳細から再試行させず、開き直しを要求する。"""
    result = _run_node_ui(
        """
const entry = {
  kind: 'awi', state: 'inbox', filename: 'entry.md', answered: null,
  summary: '削除対象', target_repo: 'example/repo', content: '取得時本文', body_html: '<p>取得時本文</p>',
  frontmatter_entries: []
};
displayEntry(entry);
openDialog(elements['detail-dialog'], new Element('origin', 'BUTTON'), elements['detail-dialog-body']);
openDeleteDialog();
fetchHandler = async () => ({
  ok: false, status: 409, statusText: 'Conflict',
  json: async () => ({error: '編集中に他プロセスが対象を変更しました', code: 'edit_conflict'})
});
await deleteEntry({preventDefault() {}});
process.stdout.write(JSON.stringify({
  deleteOpen: elements['delete-dialog'].open,
  detailOpen: elements['detail-dialog'].open,
  deleteDisabled: elements['delete-button'].disabled,
  editDisabled: elements['edit-button'].disabled,
  alert: elements['detail-alert'].textContent
}));
"""
    )
    assert result["deleteOpen"] is False
    assert result["detailOpen"] is True
    assert result["deleteDisabled"] is True
    assert result["editDisabled"] is True
    assert "詳細を閉じて開き直してから削除してください" in result["alert"]


def test_operation_result_uses_active_dialog_or_page_notification() -> None:
    """操作結果は最上位ダイアログへ送り、ダイアログが無い場合だけページ通知へ送る。"""
    result = _run_node_ui(
        """
const dialogResults = {};
for (const name of ['detail', 'create', 'delete']) {
  elements['operation-notice'].hidden = true;
  elements[`${name}-dialog`].open = true;
  dialogStack.push(`${name}-dialog`);
  deliverOperationMessage(`${name}失敗`, true);
  dialogResults[name] = {
    alert: elements[`${name}-alert`].textContent,
    status: elements[`${name}-status`].textContent,
    pageHidden: elements['operation-notice'].hidden
  };
  closeDialog(elements[`${name}-dialog`]);
}
deliverOperationMessage('保存完了', true);
process.stdout.write(JSON.stringify({
  dialogResults,
  message: elements['operation-notice-message'].textContent,
  error: elements['operation-notice'].dataset.error,
  role: elements['operation-notice'].attributes.role,
  closeLabel: elements['operation-notice-close-button'].attributes['aria-label']
}));
"""
    )
    assert result == {
        "dialogResults": {
            "detail": {"alert": "detail失敗", "status": "", "pageHidden": True},
            "create": {"alert": "create失敗", "status": "", "pageHidden": True},
            "delete": {"alert": "delete失敗", "status": "", "pageHidden": True},
        },
        "message": "保存完了",
        "error": "true",
        "role": "alert",
        "closeLabel": "操作通知を閉じる",
    }


def test_field_errors_mark_and_focus_first_invalid_control() -> None:
    """入力エラーを関連付け、最初の不正入力へフォーカスする。"""
    result = _run_node_ui(
        """
setFieldError(elements['create-content'], elements['create-content-error'], '本文が必要です');
setFieldError(elements['create-target'], elements['create-target-error'], '対象が必要です');
const first = firstInvalid([elements['create-content'], elements['create-target']]);
process.stdout.write(JSON.stringify({
  first: first.id,
  focused,
  contentInvalid: elements['create-content'].attributes['aria-invalid'],
  targetInvalid: elements['create-target'].attributes['aria-invalid'],
  contentError: elements['create-content-error'].textContent
}));
"""
    )
    assert result == {
        "first": "create-content",
        "focused": "create-content",
        "contentInvalid": "true",
        "targetInvalid": "true",
        "contentError": "本文が必要です",
    }


@pytest.mark.asyncio
async def test_state_publishes_at_max_wait_deadline_and_restarts_debounce(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """閾値未満の間隔で変更が続いても最大待機時間で1回通知し、以降は新しい保留期間を開始する。

    実時間のスケジューリング遅延で結果が変わらないよう、単調時計とタイマーを注入して検証する。
    """
    debounce = 0.05
    max_wait = debounce * state.ServeState._MAX_DEBOUNCE_FACTOR
    clock = [1_000.0]
    timers: list[_FakeTimer] = []

    def _make_timer(interval: float, function: typing.Callable[..., None], args: tuple[typing.Any, ...] = ()) -> _FakeTimer:
        timer = _FakeTimer(interval, function, args)
        timers.append(timer)
        return timer

    current = state.ServeState(
        tmp_path,
        debounce_seconds=debounce,
        monotonic=lambda: clock[0],
        timer_factory=_make_timer,
    )
    current._loop = asyncio.get_running_loop()
    published: list[str] = []
    monkeypatch.setattr(current, "publish", lambda: published.append("changed"))
    event = watchdog.events.FileModifiedEvent(str(tmp_path / "entry.md"))

    # 閾値未満の間隔で変更を送り続け、期限に到達するまで発行されないことを確認する。
    deadline = clock[0] + max_wait
    while clock[0] < deadline:
        current.on_modified(event)
        # 直前に設定したタイマーの発火時刻が期限を超えないこと（期限が上界であること）。
        assert clock[0] + timers[-1].interval <= deadline
        await asyncio.sleep(0)
        assert not published
        clock[0] += 0.02

    # 期限に到達した変更はタイマーを介さず直ちに発行する。
    pending_before = len(timers)
    current.on_modified(event)
    await asyncio.sleep(0)
    assert published == ["changed"]
    assert len(timers) == pending_before

    # 以降は新しい保留期間となり、静穏期間の経過で再び1回発行する。
    clock[0] += 0.01
    current.on_modified(event)
    await asyncio.sleep(0)
    assert published == ["changed"]
    latest = timers[-1]
    assert latest.interval == debounce
    latest.function(*latest.args)
    await asyncio.sleep(0)
    assert published == ["changed", "changed"]


def test_config_resolves_plans_and_sessions_sources(tmp_path: pathlib.Path) -> None:
    """設定の正本を`serve.toml`へ統合し、両画面の参照元を同じファイルで解決する。"""
    path = tmp_path / "serve.toml"
    path.write_text(
        'host = "toml-host"\n'
        "\n"
        "[plans]\n"
        'root = "/srv/plans"\n'
        'remote_hosts = ["circe", "stheno"]\n'
        "\n"
        "[sessions]\n"
        'claude_home = "/srv/claude"\n'
        'codex_home = "/srv/codex"\n'
        'remote_hosts = ["circe"]\n',
        encoding="utf-8",
    )
    resolved = config.resolve_config(environ={"AGENT_TOOLKIT_SERVE_CONFIG": str(path)}, platform="linux")
    assert resolved.host == "toml-host"
    assert resolved.port == 28766
    assert resolved.plans == config.PlansConfig(root="/srv/plans", remote_hosts=("circe", "stheno"))
    assert resolved.sessions == config.SessionsConfig(
        claude_home="/srv/claude",
        codex_home="/srv/codex",
        remote_hosts=("circe",),
    )


@pytest.mark.asyncio
async def test_cancelled_sync_request_does_not_cancel_shared_sync(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """開始要求のキャンセル後も共有同期が継続し、別要求へ同じ結果を返す。"""
    operations = serve_app.Operations(tmp_path)
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def sync() -> bool:
        nonlocal calls
        calls += 1
        started.set()
        release.wait()
        return True

    monkeypatch.setattr(operations, "sync", sync)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
        operations=operations,
    )
    first = asyncio.create_task(app.test_client().post("/api/sync"))
    await asyncio.to_thread(started.wait)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(app.test_client().post("/api/sync"))
    await asyncio.sleep(0)
    release.set()
    response = await second
    assert calls == 1
    assert await response.get_json() == {"synced": True}


def test_detail_returns_existing_uwi_answer(tmp_path: pathlib.Path) -> None:
    """回答済みUWIの詳細は既存回答を編集用に返す。"""
    _write_detail_entry(
        tmp_path,
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問本文\n\n"
        "## 回答\n\n<!-- ユーザーはこの行以降に回答を追記する -->\n既存回答\n2行目\n",
    )

    detail = serve_app.Operations(tmp_path).detail("inbox", "entry.md")

    assert detail["answer"] == "既存回答\n2行目"


@pytest.mark.asyncio
async def test_detail_api_round_trips_frontmatter_as_strict_json(tmp_path: pathlib.Path) -> None:
    """詳細HTTP APIは非有限浮動小数点を含むfrontmatterも標準JSONとして返す。"""
    _write_detail_entry(
        tmp_path,
        "---\ntype: awi\nqueue_schedule:\n  nan: .nan\n  positive: .inf\n---\n\n本文\n",
    )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().get("/api/entries/inbox/entry.md")

    assert response.status_code == 200

    def reject_constant(_value: str) -> typing.NoReturn:
        raise ValueError("非標準JSON定数です")

    payload = json.loads(await response.get_data(), parse_constant=reject_constant)
    assert payload["entry"]["frontmatter_entries"] == [
        {"key": {"type": "str", "value": "type"}, "value": "awi"},
        {
            "key": {"type": "str", "value": "queue_schedule"},
            "value": {"nan": "NaN", "positive": "Infinity"},
        },
    ]


def test_detail_falls_back_on_broken_frontmatter(tmp_path: pathlib.Path) -> None:
    """frontmatterの解析に失敗した場合は本文全体の整形結果を返す。"""
    _write_detail_entry(tmp_path, "---\nkey: [unclosed\n---\n\n本文\n")
    rendered = typing.cast(str, serve_app.Operations(tmp_path).detail("inbox", "entry.md")["content_html"])
    assert "本文" in rendered
    # 表へ振り分けず本文全体をMarkdownとして整形するため、開始区切りが水平線として残る。
    assert '<table class="frontmatter">' not in rendered
    assert "<hr" in rendered


def test_detail_disables_only_bare_address_links(tmp_path: pathlib.Path) -> None:
    """詳細表示は裸アドレスをリンク化せず、明示リンクとGFM拡張を維持する。"""
    _write_detail_entry(
        tmp_path,
        "https://example.com www.example.com user@example.com\n\n"
        "[明示リンク](https://example.net) <https://example.org>\n\n"
        "~~取消~~\n\n| 列 |\n| --- |\n| 値 |\n",
    )

    rendered = typing.cast(str, serve_app.Operations(tmp_path).detail("inbox", "entry.md")["content_html"])

    assert '<a href="https://example.com">' not in rendered
    assert '<a href="http://www.example.com">' not in rendered
    assert '<a href="mailto:user@example.com">' not in rendered
    assert '<a href="https://example.net">明示リンク</a>' in rendered
    assert '<a href="https://example.org">https://example.org</a>' in rendered
    assert "<s>取消</s>" in rendered
    assert "<table>" in rendered


def test_operations_active_includes_hold_entries_of_both_types(tmp_path: pathlib.Path) -> None:
    """一覧APIのactive状態はhold配下のawiとUWIをいずれも含める。"""
    hold = tmp_path / "hold"
    hold.mkdir()
    (hold / "held.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n保留中\n",
        encoding="utf-8",
    )
    (hold / "held-uwi.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n保留中UWI\n",
        encoding="utf-8",
    )

    entries, warnings = serve_app.Operations(tmp_path).entries_with_warnings({"status": "active"})

    assert not warnings
    assert sorted(str(item["filename"]) for item in entries) == ["held-uwi.md", "held.md"]
    assert {item["state"] for item in entries} == {"hold"}


@pytest.mark.parametrize("query", ["本文途中の固有語", "日本語", "EXAMPLE/REPO", "entry.md"])
def test_operations_query_searches_full_markdown_and_metadata(tmp_path: pathlib.Path, query: str) -> None:
    """`q`は本文途中、Unicode、frontmatter値、ファイル名を大文字小文字を区別せず検索する。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "entry.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n先頭\n\n日本語と本文途中の固有語\n",
        encoding="utf-8",
    )
    (inbox / "other.md").write_text(
        "---\ntype: awi\ntarget_repo: other/repo\n---\n\n別本文\n",
        encoding="utf-8",
    )

    result, warnings = serve_app.Operations(tmp_path).entries_with_warnings({"q": query})
    assert not warnings

    assert [item["filename"] for item in result] == ["entry.md"]


@pytest.mark.asyncio
async def test_add_api_rejects_uwi_only_scope_on_awi(tmp_path: pathlib.Path) -> None:
    """UWI専用の`scope`をAWIへ指定すると拒否する。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(
        "/api/entries",
        json={"type": "awi", "messages": ["x"], "target_repo": "example/repo", "scope": "s"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_edit_and_answer_apis_detect_external_changes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取得後の外部更新を409で保護し、最新値と従来形式の更新を許可する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    for module in (common, awi_repo, serve_app.awi_mutations, serve_app.uwi_mutations):
        monkeypatch.setattr(module, "_repo_lock", lock, raising=False)
        monkeypatch.setattr(module, "_pull", lambda _path: None, raising=False)
        monkeypatch.setattr(module, "_commit_and_push", lambda *_args, **_kwargs: None, raising=False)
        monkeypatch.setattr(module, "_push_pending_commits", lambda _path: None, raising=False)
    monkeypatch.setattr(common, "repo_lock", lock)
    monkeypatch.setattr(common, "pull", lambda _path: None)

    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    awi_path = inbox / "awi.md"
    initial_awi = "---\ntype: awi\ntarget_repo: example/repo\n---\n\n取得時本文\n"
    awi_path.write_text(initial_awi, encoding="utf-8")
    uwi_path = inbox / "question.md"
    initial_uwi = (
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n"
    )
    uwi_path.write_text(initial_uwi, encoding="utf-8")

    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    awi_detail = await (await client.get("/api/entries/inbox/awi.md")).get_json()
    external_awi = initial_awi.replace("取得時本文", "外部更新後の本文")
    awi_path.write_text(external_awi, encoding="utf-8")
    conflict = await client.put(
        "/api/entries/inbox/awi.md",
        json={"content": "利用者の本文", "expected_content": awi_detail["entry"]["content"]},
    )
    assert conflict.status_code == 409
    assert (await conflict.get_json())["code"] == "edit_conflict"
    assert awi_path.read_text(encoding="utf-8") == external_awi

    latest_content = external_awi.replace("外部更新後の本文", "最新基準で更新")
    latest = await client.put(
        "/api/entries/inbox/awi.md",
        json={"content": latest_content, "expected_content": external_awi},
    )
    assert latest.status_code == 200
    assert awi_path.read_text(encoding="utf-8") == latest_content
    legacy_content = latest_content.replace("最新基準で更新", "従来形式で更新")
    legacy = await client.put(
        "/api/entries/inbox/awi.md",
        json={"content": legacy_content},
    )
    assert legacy.status_code == 200
    assert awi_path.read_text(encoding="utf-8") == legacy_content

    uwi_detail = await (await client.get("/api/entries/inbox/question.md")).get_json()
    external_uwi = initial_uwi.replace("質問？", "外部更新後の質問？")
    uwi_path.write_text(external_uwi, encoding="utf-8")
    answer_conflict = await client.post(
        "/api/entries/answer",
        json={
            "filename": "question.md",
            "answer": "古い基準からの回答",
            "expected_content": uwi_detail["entry"]["content"],
        },
    )
    assert answer_conflict.status_code == 409
    assert (await answer_conflict.get_json())["code"] == "edit_conflict"
    assert uwi_path.read_text(encoding="utf-8") == external_uwi

    latest_answer = await client.post(
        "/api/entries/answer",
        json={
            "filename": "question.md",
            "answer": "最新基準からの回答",
            "expected_content": external_uwi,
        },
    )
    assert latest_answer.status_code == 200
    assert uwi_path.read_text(encoding="utf-8").endswith("最新基準からの回答\n")
    legacy_answer = await client.post(
        "/api/entries/answer",
        json={"filename": "question.md", "answer": "従来形式からの回答"},
    )
    assert legacy_answer.status_code == 200
    assert uwi_path.read_text(encoding="utf-8").endswith("従来形式からの回答\n")


def test_operations_sort_entries_with_unanswered_uwi_then_mixed_remaining(tmp_path: pathlib.Path) -> None:
    """一覧は未回答UWIを先頭に置き、残りを種別混在のファイル名降順で返す。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    # ファイル名の昇順でファイルを作成
    (inbox / "a-awi.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n",
        encoding="utf-8",
    )
    (inbox / "z-answered-uwi.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問\n\n## 回答\n\n回答済み\n",
        encoding="utf-8",
    )
    (inbox / "m-answered-uwi.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問\n\n## 回答\n\n回答済み\n",
        encoding="utf-8",
    )
    (inbox / "a-unanswered-uwi.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    (inbox / "d-awi.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n",
        encoding="utf-8",
    )
    (inbox / "z-awi.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n",
        encoding="utf-8",
    )

    operations = serve_app.Operations(tmp_path)
    result, warnings = operations.entries_with_warnings({})
    assert not warnings
    filenames = [item["filename"] for item in result]

    assert filenames == [
        "a-unanswered-uwi.md",
        "z-awi.md",
        "z-answered-uwi.md",
        "m-answered-uwi.md",
        "d-awi.md",
        "a-awi.md",
    ]
    # 種別の確認
    assert result[0]["kind"] == "uwi"
    assert [item["kind"] for item in result[1:]] == ["awi", "uwi", "uwi", "awi", "awi"]


def test_serve_state_watches_all_queue_states(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """状態監視は7状態フォルダを対象とし、旧feedback/tbd階層を生成しない。"""
    current = state.ServeState(tmp_path)
    scheduled: list[str] = []
    monkeypatch.setattr(
        current.observer,
        "schedule",
        lambda _handler, path, recursive=False: scheduled.append(path),
    )
    monkeypatch.setattr(current.observer, "start", lambda: None)
    loop = asyncio.new_event_loop()
    try:
        current.start(loop)
        assert sorted(pathlib.Path(p).name for p in scheduled) == [
            "adopted",
            "hold",
            "inbox",
            "processing",
            "rejected",
        ]
        assert not (tmp_path / "awi").exists()
        assert not (tmp_path / "uwi").exists()
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_web_transition_mutations_allow_omitted_target_repo(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """状態遷移系APIはfilenameで対象を一意に特定できるためtarget_repo省略を許容する。"""

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
        "---\ntype: awi\ntarget_repo: github.com/example/foo\n---\n\n本文\n",
        encoding="utf-8",
    )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post("/api/entries/adopt", json={"filenames": ["entry.md"]})
    assert response.status_code == 200
    assert (tmp_path / "adopted" / "entry.md").is_file()
    assert not (inbox / "entry.md").exists()


@pytest.mark.asyncio
async def test_favicon_svg_is_served(tmp_path: pathlib.Path) -> None:
    """`/favicon.svg`がSVGとして配信される。"""
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), state.ServeState(tmp_path))
    response = await app.test_client().get("/favicon.svg")
    body = await response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.mimetype == "image/svg+xml"
    assert response.headers["Cache-Control"] == "public, max-age=3600"
    assert body == assets.FAVICON_SVG


@pytest.mark.asyncio
async def test_index_and_js_reflect_forwarded_prefix(tmp_path: pathlib.Path) -> None:
    """`X-Forwarded-Prefix`をHTML、JS、manifestの各URLへ1回だけ反映する。"""
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), state.ServeState(tmp_path))
    client = app.test_client()
    headers = {"X-Forwarded-Prefix": "/atk", "X-Forwarded-Proto": "https"}

    index_response = await client.get("/atk/", headers=headers)
    assert index_response.status_code == 200
    index_body = await index_response.get_data(as_text=True)
    assert 'href="/atk/static/app.css"' in index_body
    assert 'src="/atk/static/app.js"' in index_body
    assert 'href="/atk/favicon.svg"' in index_body
    assert 'href="/atk/manifest.webmanifest" crossorigin="use-credentials"' in index_body
    assert "/atk/atk/" not in index_body

    js_response = await client.get("/atk/static/app.js", headers=headers)
    assert js_response.status_code == 200
    js_body = await js_response.get_data(as_text=True)
    assert 'const BASE_PATH="/atk";' in js_body

    manifest_response = await client.get("/atk/manifest.webmanifest", headers=headers)
    manifest = await manifest_response.get_json()
    assert manifest["start_url"] == "/atk/"
    assert manifest["scope"] == "/atk/"
    assert [icon["src"] for icon in manifest["icons"]] == ["/atk/favicon.svg"]
    assert all("/atk/atk/" not in icon["src"] for icon in manifest["icons"])


@pytest.mark.asyncio
async def test_serve_tolerates_absent_and_unsupported_signals(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """シグナル不在とadd_signal_handler未実装の双方を吸収して起動する。"""
    stopped: list[str] = []
    _stub_state(monkeypatch, tmp_path, stopped)
    monkeypatch.delattr(serve.signal, "SIGHUP", raising=False)
    attempted: list[int] = []

    def add_signal_handler(sig: int, callback: typing.Callable[[], None]) -> None:
        del callback
        attempted.append(sig)
        raise NotImplementedError

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "add_signal_handler", add_signal_handler)

    async def fake_serve(app: object, hypercorn_config: object, *, shutdown_trigger: object) -> None:
        del app, hypercorn_config, shutdown_trigger

    monkeypatch.setattr(serve.hypercorn.asyncio, "serve", fake_serve)
    await serve._serve(tmp_path, config.ServeConfig("127.0.0.1", 28766))

    assert attempted == [signal.SIGINT, signal.SIGTERM]
    assert stopped == ["stop"]


def test_sync_ignores_rate_limit(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """利用者の明示的な同期はレート制限を経由せず毎回pullする。"""
    calls: list[str] = []

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    monkeypatch.setattr(common, "repo_lock", lock)
    monkeypatch.setattr(common, "pull", _recorder(calls, "pull", result=None))
    monkeypatch.setattr(common, "pull_if_stale", _recorder(calls, "pull_if_stale", result=True))
    operations = serve_app.Operations(tmp_path)
    assert operations.sync() is True
    assert operations.sync() is True
    assert calls == ["pull", "pull"]


def test_assets_clear_selected_target_repo_when_absent_from_choices() -> None:
    """候補から消えた対象リポジトリの選択状態を解除する。"""
    result = _run_node_ui(
        """
fetchHandler = async (url) => {
  if (url.endsWith('/api/repos?status=active')) {
    const repos = ['github.com/x/alpha'];
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos})};
  }
  throw new Error('想定外のURL: ' + url);
};
byId('target-filter').value = 'github.com/x/removed';
await loadTargetRepos();
process.stdout.write(JSON.stringify({
  values: byId('target-filter').children.map(option => option.value),
  selected: byId('target-filter').value,
}));
"""
    )
    assert result["values"] == ["", "github.com/x/alpha"]
    assert result["selected"] == ""


def test_assets_do_not_accumulate_target_repo_choices() -> None:
    """候補の再取得で選択肢が累積しない。"""
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
await loadTargetRepos();
process.stdout.write(JSON.stringify({
  filterCount: byId('target-filter').children.length,
  datalistCount: byId('repo-options').children.length,
}));
"""
    )
    assert result["filterCount"] == 3
    assert result["datalistCount"] == 2


def test_assets_search_fallback_failure_keeps_initial_empty_result() -> None:
    """補助検索が失敗しても初回の空一覧とエラー表示を維持する。"""
    result = _run_node_ui(
        """
bindEvents();
elements['search-input'].value = '失敗する検索';
fetchHandler = async url => {
  if (url.includes('status=active')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})};
  }
  return {ok: false, status: 503, statusText: 'Unavailable', json: async () => ({error: '補助検索に失敗'})};
};
await loadEntries({announce: true});
const shown = {
  message: elements['global-error-message'].textContent,
  hidden: elements['global-error'].hidden
};
elements['global-error-close-button'].focus();
elements['global-error-close-button'].listeners.click();
process.stdout.write(JSON.stringify({
  rows: entries.map(entry => entry.filename),
  notice: elements['list-fallback-notice'].textContent,
  status: elements['result-status'].textContent,
  shown,
  cleared: {
    message: elements['global-error-message'].textContent,
    hidden: elements['global-error'].hidden,
    focused
  }
}));
"""
    )
    assert result == {
        "rows": [],
        "notice": "",
        "status": "一致する項目はありません",
        "shown": {"message": "補助検索に失敗", "hidden": False},
        "cleared": {"message": "", "hidden": True, "focused": "refresh-button"},
    }


def test_assets_keep_user_filter_load_when_same_state_sse_supersedes_repo_request() -> None:
    """同一状態の後発SSEが候補要求を失効させても利用者の一覧通知を維持する。"""
    result = _run_node_ui(
        """
const repoResolvers = [];
const listUrls = [];
fetchHandler = async url => {
  if (url.includes('/api/repos?')) return new Promise(resolve => { repoResolvers.push(resolve); });
  if (url.includes('/api/entries?')) {
    listUrls.push(url);
    return {
      ok: true, status: 200, statusText: 'OK',
      json: async () => ({
        entries: [{kind: 'awi', state: 'inbox', filename: 'filtered.md', summary: 'filtered'}],
        warnings: []
      })
    };
  }
  throw new Error('想定外のURL: ' + url);
};
elements['kind-filter'].value = 'awi';
elements['result-status'].textContent = '変更前の通知';
const user = handleFilterChange({reloadRepos: true});
await Promise.resolve();
const sse = reloadFromExternalChange();
await Promise.resolve();
repoResolvers[1]({
  ok: true, status: 200, statusText: 'OK', json: async () => ({repos: ['example/repo']})
});
await Promise.resolve();
repoResolvers[0]({
  ok: true, status: 200, statusText: 'OK', json: async () => ({repos: ['example/repo']})
});
await Promise.all([user, sse]);
process.stdout.write(JSON.stringify({
  listUrls,
  status: elements['result-status'].textContent,
  rows: entries.map(entry => entry.filename)
}));
"""
    )
    assert result == {
        "listUrls": [
            "/atk/api/entries?type=uwi&status=all&answered=all",
            "/atk/api/entries?type=awi&status=active&answered=all&page=1",
            "/atk/api/entries?type=awi&status=active&answered=all&page=1",
        ],
        "status": "1件を表示",
        "rows": ["filtered.md"],
    }


@pytest.mark.asyncio
async def test_add_api_accepts_omitted_target_repo_with_frontmatter(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本文frontmatterにtarget_repoがあれば対象リポジトリ指定を省略できる。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    monkeypatch.setattr(common, "_repo_lock", lock)
    monkeypatch.setattr(common, "_pull", lambda _path: None)
    monkeypatch.setattr(common, "_commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(common, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(serve_app.awi_add, "_repo_lock", lock)
    monkeypatch.setattr(serve_app.awi_add, "_pull", lambda _path: None)
    monkeypatch.setattr(serve_app.awi_add, "_commit_and_push", lambda *_args, **_kwargs: None)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().post(
        "/api/entries",
        json={"type": "awi", "messages": ["---\ntarget_repo: github.com/Example/Repo\n---\n\n本文"]},
    )

    assert response.status_code == 201
    body = await response.get_json()
    content = (tmp_path / "inbox" / body["filenames"][0]).read_text(encoding="utf-8")
    assert "target_repo: github.com/example/repo" in content
    assert "source:" not in content


def test_assets_offer_every_queue_state_filter() -> None:
    """状態フィルターがキューの全状態を個別に選択できる。"""
    for state_name in common.WI_STATES:
        assert f'<option value="{state_name}">{state_name}</option>' in assets.HTML


def test_batch_creation_sends_raw_text_and_hides_frontmatter_driven_fields() -> None:
    """一括登録では生テキストを送り、対象リポジトリ欄と投入元欄を隠す。"""
    result = _run_node_ui(
        """
elements['create-dialog'].open = true;
dialogStack.push('create-dialog');
elements['create-kind'].value = 'batch';
updateCreateFields();
const hiddenRepoFields = elements['create-repo-fields'].hidden;
const contentLabel = elements['create-content-label'].textContent;
elements['create-content'].value = '  show形式テキスト  ';
fetchHandler = async (url) => {
  if (url.endsWith('/api/entries/batch')) {
    return {
      ok: true, status: 201, statusText: 'Created',
      json: async () => ({filenames: ['new.md'], mapping: {'old.md': 'new.md'}, warnings: ['依存先が不在']})
    };
  }
  if (url.endsWith('/api/repos?status=active')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos: []})};
  }
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})};
};
await createEntry({preventDefault() {}});
const call = fetchCalls.find(item => item.url.endsWith('/api/entries/batch'));
process.stdout.write(JSON.stringify({
  hiddenRepoFields,
  contentLabel,
  body: JSON.parse(call.options.body),
  toast: elements['toast'].textContent,
  detailOpen: elements['detail-dialog'].open
}));
"""
    )
    assert result == {
        "hiddenRepoFields": True,
        "contentLabel": "show形式テキスト（必須）",
        "body": {"text": "  show形式テキスト  "},
        "toast": "1件を取り込みました。改名: old.md -> new.md 警告: 依存先が不在",
        "detailOpen": False,
    }


@pytest.mark.asyncio
async def test_remove_api_accepts_terminal_states_and_preserves_protected_states(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """終端状態とholdは削除し、processingの保護を維持する。"""
    _patch_comment_edit_dependencies(monkeypatch)
    content = _session_review_awi("本文\n")
    for state_name in ("adopted", "rejected", "hold", "processing"):
        directory = tmp_path / state_name
        directory.mkdir()
        (directory / f"{state_name}.md").write_text(content, encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    for state_name in ("adopted", "rejected", "hold"):
        response = await client.post(
            "/api/entries/remove",
            json={
                "filenames": [f"{state_name}.md"],
                "state": state_name,
                "expected_content": content,
                "force": False,
            },
        )
        assert response.status_code == 200
        assert not (tmp_path / state_name / f"{state_name}.md").exists()

    for state_name in ("processing",):
        response = await client.post(
            "/api/entries/remove",
            json={
                "filenames": [f"{state_name}.md"],
                "state": state_name,
                "expected_content": content,
                "force": False,
            },
        )
        assert response.status_code == 400
        assert (tmp_path / state_name / f"{state_name}.md").is_file()
