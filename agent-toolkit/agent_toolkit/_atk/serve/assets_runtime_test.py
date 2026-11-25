# pylint: disable=protected-access

import asyncio
import io
import logging
import pathlib
import threading

import pytest

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import assets, config, state
from agent_toolkit._atk.serve import cli as serve
from agent_toolkit._atk.serve import runtime as serve_runtime
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._testing.serve_support import _three_screen_app


@pytest.mark.asyncio
async def test_navigation_serves_all_screens(tmp_path: pathlib.Path) -> None:
    client = _three_screen_app(tmp_path).test_client()
    for path, screen in (
        ("/", "wi"),
        ("/plans", "plans"),
        ("/agents", "agents"),
        ("/sessions", "sessions"),
        ("/settings", "settings"),
        ("/usage", "usage"),
    ):
        response = await client.get(path)
        assert response.status_code == 200
        assert response.mimetype == "text/html"
        assert f'id="screen-{screen}"' in await response.get_data(as_text=True)


@pytest.mark.asyncio
async def test_plan_and_session_apis_classify_input_errors(tmp_path: pathlib.Path) -> None:
    app = _three_screen_app(tmp_path)
    client = app.test_client()
    assert (await client.get("/api/plans/file")).status_code == 400
    assert (await client.get("/api/plans/file?path=a.md&host=unknown-host")).status_code == 400
    assert (await client.get("/api/sessions/detail")).status_code == 400
    assert (await client.get("/api/sessions/detail?engine=claude&path=../etc/passwd.jsonl")).status_code == 404
    assert (await client.get("/api/sessions/detail?engine=unknown&path=a.jsonl")).status_code == 404


@pytest.mark.asyncio
async def test_session_cost_api_errors_tell_the_next_step(tmp_path: pathlib.Path) -> None:
    app = _three_screen_app(tmp_path)
    client = app.test_client()
    unknown = await client.get("/api/sessions/cost?engine=claude&path=a.jsonl&extra=1")
    assert unknown.status_code == 400
    assert "host・engine・path" in (await unknown.get_json())["error"]
    missing = await client.get("/api/sessions/cost?engine=claude")
    assert missing.status_code == 400
    assert "/api/sessions/list" in (await missing.get_json())["error"]
    remote = await client.get("/api/sessions/cost?host=other-host&engine=claude&path=a.jsonl")
    assert remote.status_code == 404
    assert "/api/sessions/detail" in (await remote.get_json())["error"]


@pytest.mark.asyncio
async def test_cancelled_request_keeps_worker_slot_until_completion() -> None:
    """要求キャンセル後も同期処理が終わるまでSemaphore枠を解放しない。"""
    workers = serve_runtime.BoundedWorkers(1)
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


@pytest.mark.asyncio
async def test_index_and_js_without_prefix_use_empty_base(tmp_path: pathlib.Path) -> None:
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), state.ServeState(tmp_path))
    client = app.test_client()

    index_body = await (await client.get("/")).get_data(as_text=True)
    assert 'href="/static/app.css"' in index_body
    assert 'type="module" src="/static/wi.js"' in index_body
    assert '"base_path": ""' in index_body

    js_response = await client.get("/static/wi.js")
    assert js_response.status_code == 200
    assert (await js_response.get_data(as_text=True)) == assets.SCRIPTS["wi.js"]


@pytest.mark.asyncio
async def test_protocol_relative_prefix_logs_rejection_via_proxy_fix(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), state.ServeState(tmp_path))
    client = app.test_client()
    with caplog.at_level("WARNING"):
        response = await client.get("//evil.example/", headers={"X-Forwarded-Prefix": "//evil.example"})
    assert response.status_code == 404
    assert "X-Forwarded-Prefixに不正な値が含まれています" in caplog.text


def test_run_initializes_logging_and_logs_startup(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = logging.getLogger()
    error_logger = logging.getLogger("hypercorn.error")
    with io.StringIO() as previous_output, monkeypatch.context() as patch:
        previous_handler = logging.StreamHandler(previous_output)
        server_handler = logging.StreamHandler()
        # force付きの実初期化がpytest自身のハンドラーを閉じないよう、試験用の初期状態へ隔離する。
        patch.setattr(root, "handlers", [previous_handler])
        patch.setattr(root, "level", logging.WARNING)
        for logger in (serve.logger, error_logger):
            patch.setattr(logger, "parent", root)
            patch.setattr(logger, "level", logging.NOTSET)
            patch.setattr(logger, "disabled", False)
            patch.setattr(logger, "propagate", True)

            patch.setattr(logger, "_cache", {})
        patch.setattr(serve.logger, "handlers", [])
        patch.setattr(error_logger, "handlers", [server_handler])
        patch.setattr(_wi_sync, "ensure_environment", lambda home: tmp_path)
        patch.setattr(serve.asyncio, "run", lambda coro: coro.close())
        try:
            serve.run(host="127.0.0.1", port=28766, home=tmp_path)
            error_logger.warning("試験用のサーバー診断")
            output = capsys.readouterr().err
            assert output.count("http://127.0.0.1:28766/") == 1
            assert output.count("試験用のサーバー診断") == 1
            assert not previous_output.getvalue()
        finally:
            for handler in (*root.handlers, previous_handler, server_handler):
                handler.close()
            patch.undo()
            root.setLevel(root.level)
