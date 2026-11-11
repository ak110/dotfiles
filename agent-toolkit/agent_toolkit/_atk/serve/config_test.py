"""serveのconfigに関するruntimeの試験。"""

# pylint: disable=protected-access

import asyncio
import pathlib
import signal
import threading
import typing

import filelock
import pytest

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import cli as serve
from agent_toolkit._atk.serve import config, state, wi_operations
from agent_toolkit._atk.serve import plans as serve_plans
from agent_toolkit._atk.wi import constants as _wi_constants
from agent_toolkit._testing.serve_support import _stub_state, _three_screen_app
from agent_toolkit._testing.wi_entry_files import entry_path


@pytest.mark.parametrize("host", ["", "  ", 1])
def test_invalid_host(host: object) -> None:
    """空または非文字列hostを拒否する。"""
    with pytest.raises(ValueError):
        config.resolve_config(host=host)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]


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
async def test_read_routes_remain_available_during_entry_removal(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ファイル削除と競合した一覧はスナップショット、詳細は404を返す。"""
    entry = entry_path(tmp_path, "entry.md")
    original = "---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\n---\n\n本文\n"
    entry.write_text(original, encoding="utf-8")
    original_normalized_wi_type = _wi_constants.normalized_wi_type

    async def race_request(path: str) -> typing.Any:
        started = threading.Event()
        release = threading.Event()

        def normalized_wi_type(value: object) -> str | None:
            started.set()
            assert release.wait(timeout=5), "ファイル削除後の解放がありません"
            return original_normalized_wi_type(value)

        monkeypatch.setattr(_wi_constants, "normalized_wi_type", normalized_wi_type)
        request = asyncio.create_task(app.test_client().get(path))
        try:
            assert await asyncio.to_thread(started.wait, 5), "種別判定へ到達しません"
            entry.unlink()
        finally:
            release.set()
            response = await asyncio.wait_for(request, timeout=5)
        return response

    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    entries_response = await race_request("/api/entries?status=inbox")
    assert entries_response.status_code == 200
    assert await entries_response.get_json() == {"entries": [], "warnings": []}

    entry.write_text(original, encoding="utf-8")
    detail_response = await race_request("/api/entries/entry.md")
    assert detail_response.status_code == 404


@pytest.mark.asyncio
async def test_unrelated_runtime_error_is_not_classified_as_edit_conflict(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """競合以外のRuntimeErrorを409へ誤分類しない。"""
    operations = wi_operations.Operations(tmp_path)
    entry_path(tmp_path, "entry.md").write_text("---\ntype: awi\nstate: inbox\n---\n\n本文\n", encoding="utf-8")

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
        "/api/entries/entry.md",
        json={"content": "本文"},
    )
    assert response.status_code == 500


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
    """変更操作が上限まで待ってもロックを取得できない競合を、JSON 409応答へ変換する。"""

    def edit(
        _state: str,
        _filename: str,
        _content: str,
        _expected_content: str | None = None,
    ) -> bool:
        raise filelock.Timeout("locked")

    operations = wi_operations.Operations(tmp_path)
    entry_path(tmp_path, "entry.md").write_text("---\ntype: awi\nstate: inbox\n---\n\n本文\n", encoding="utf-8")
    monkeypatch.setattr(operations, "edit", edit)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
        operations=operations,
    )
    response = await app.test_client().put(
        "/api/entries/entry.md",
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

    assert '"base_path": ""' in index_body
    js_response = await client.get("/atk:1/static/wi.js", headers=headers)
    assert js_response.status_code == 200


@pytest.mark.parametrize("received_signal", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
@pytest.mark.asyncio
async def test_serve_shuts_down_on_signal_and_stops_state(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    received_signal: signal.Signals,
) -> None:
    """シグナル集約でshutdown_triggerが解除され、停止時にstateを止める。"""
    stopped: list[str] = []
    _stub_state(monkeypatch, tmp_path, stopped)
    handlers: dict[int, tuple[typing.Callable[[str], None], str]] = {}
    loop = asyncio.get_running_loop()

    def add_signal_handler(sig: int, callback: typing.Callable[[str], None], signal_name: str, **kwargs: object) -> None:
        del kwargs
        handlers[sig] = (callback, signal_name)

    monkeypatch.setattr(loop, "add_signal_handler", add_signal_handler)
    observed: dict[str, object] = {}

    async def fake_serve(app: object, hypercorn_config: typing.Any, *, shutdown_trigger: typing.Any) -> None:
        del app
        observed["bind"] = hypercorn_config.bind[0]
        observed["graceful_timeout"] = hypercorn_config.graceful_timeout
        observed["accesslog"] = hypercorn_config.accesslog
        # シグナル受信を模擬してshutdown_triggerを解除する。
        callback, signal_name = handlers[received_signal]
        callback(signal_name)
        await shutdown_trigger()

    monkeypatch.setattr(serve.hypercorn.asyncio, "serve", fake_serve)
    await serve._serve(tmp_path, config.ServeConfig("127.0.0.1", 28766))

    assert observed["bind"] == "127.0.0.1:28766"
    assert observed["graceful_timeout"] == 1.0
    assert observed["accesslog"] is None
    assert set(handlers) == {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}
    assert stopped == ["stop"]
