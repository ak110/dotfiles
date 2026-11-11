"""serveのappに関するruntimeの試験。"""

# pylint: disable=protected-access

import asyncio
import datetime
import json
import pathlib
import subprocess
import sys
import threading
import typing

import pytest

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import config, sessions_remote, state, timings, wi_operations
from agent_toolkit._atk.serve import plans as serve_plans
from agent_toolkit._atk.serve import sessions as serve_sessions
from agent_toolkit._atk.serve.plans import remote as serve_plan_remote
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._testing.serve_app_support import _REBASE_AWI, _rebasing_notes


@pytest.mark.asyncio
async def test_worker_exit_is_request_failure(tmp_path: pathlib.Path) -> None:
    """管理情報と無関係な操作のSystemExitも共通ワーカーで変換する。"""

    class ExitingOperations(wi_operations.Operations):
        def target_repos(self, status: str = "active", *, now: datetime.datetime | None = None) -> list[str]:
            raise SystemExit(2)

    current = state.ServeState(tmp_path)
    app = serve_app.create_app(
        tmp_path, config.ServeConfig("127.0.0.1", 28766), current, operations=ExitingOperations(tmp_path)
    )
    client = app.test_client()
    for _attempt in range(2):
        response = await client.get("/api/repos")
        assert 400 <= response.status_code < 600
        assert "標準エラー" in (await response.get_json())["error"]
        assert (await client.get("/")).status_code == 200


@pytest.mark.asyncio
async def test_shutdown_request_stops_remote_connections_without_reconnecting(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """停止要求の時点で常駐接続を止め、`after_serving`までの間に常駐SSHを起動し直さない。

    systemdがcgroup全体へSIGTERMを送ると常駐SSHが停止処理と同時に終わり、常駐接続はバックオフの後に
    新しい常駐SSHを起動する。停止要求で止めないと、`after_serving`まで再接続を繰り返す。
    """
    started: list[str] = []
    real_exec = asyncio.create_subprocess_exec

    async def fake_exec(*cmd: typing.Any, **kwargs: typing.Any) -> asyncio.subprocess.Process:
        started.append(" ".join(str(part) for part in cmd))
        # 標準出力を閉じて接続断を起こし、標準入力の終端を受けて終了する子プロセスで常駐SSHを代替する。
        script = "import os, sys; os.close(1); sys.stdin.read()"
        return await real_exec(sys.executable, "-c", script, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(sessions_remote, "BACKOFF_INITIAL_SEC", 0.01)
    monkeypatch.setattr(serve_plan_remote, "REMOTE_BACKOFF_INITIAL_SEC", 0.01)
    current_state = state.ServeState(tmp_path)
    sessions_context = serve_sessions.create_context(
        hostname="local-host",
        claude_home=tmp_path / "claude",
        codex_home=tmp_path / "codex",
        remote_hosts=["remote-host"],
    )
    plans_context = serve_plans.create_context(root=tmp_path / "plans", hostname="local-host", remote_hosts=["remote-host"])
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        current_state,
        plans_context=plans_context,
        sessions_context=sessions_context,
    )
    async with app.test_app():  # ty: ignore[invalid-context-manager]
        for _ in range(500):
            if len(started) >= 4:
                break
            await asyncio.sleep(0.01)
        assert len(started) >= 4

        tasks = [*sessions_context.state.tasks, *plans_context.state.remote_tasks]
        current_state.request_shutdown()
        for _ in range(500):
            if not sessions_context.state.tasks and not plans_context.state.remote_tasks:
                break
            await asyncio.sleep(0.01)
        assert not sessions_context.state.tasks
        assert not plans_context.state.remote_tasks
        count = len(started)
        assert all(task.done() for task in tasks)

        assert len(started) == count


@pytest.mark.asyncio
async def test_reads_respond_while_updates_wait_for_lock(
    tmp_path: pathlib.Path, make_clean_repo: typing.Callable[..., pathlib.Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """更新が共通ロックを待って更新用の実行枠を全て占める間も、一覧・詳細・検索・候補はローカル内容で応答する。

    参照が更新と同じ枠を共有すると、ロックの解放（最大30秒）まで参照が待つ。所要記録には更新のロック待機と
    参照の各区間が要求IDで残り、本文と検索語は含まれない。
    """
    notes = _rebasing_notes(tmp_path, make_clean_repo)
    rebase_dir = subprocess.run(
        ["git", "-C", str(notes), "rev-parse", "--git-path", "rebase-merge"], check=True, capture_output=True, text=True
    ).stdout.strip()
    (notes / rebase_dir).rmdir()
    timing_log = tmp_path / "timings.jsonl"
    app = serve_app.create_app(
        notes, config.ServeConfig("127.0.0.1", 28766), state.ServeState(notes), worker_limit=2, timing_log_path=timing_log
    )
    client = app.test_client()
    locked = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with _wi_sync.repo_lock(notes):
            locked.set()
            release.wait(timeout=10)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert await asyncio.to_thread(locked.wait, 10)
    attempting: asyncio.Queue[None] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    repo_lock = _wi_sync.repo_lock

    def observed_lock(*args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        loop.call_soon_threadsafe(attempting.put_nowait, None)
        return repo_lock(*args, **kwargs)

    monkeypatch.setattr(_wi_sync, "repo_lock", observed_lock)
    updates = [
        asyncio.ensure_future(
            client.put(
                "/api/entries/inbox/entry.md",
                json={"content": _REBASE_AWI.replace("本文", f"変更{index}"), "expected_content": _REBASE_AWI},
                headers={"X-Atk-Request-Id": f"update-{index}"},
            )
        )
        for index in range(3)
    ]
    for _ in range(2):
        await asyncio.wait_for(attempting.get(), timeout=5)
    try:
        assert not any(update.done() for update in updates)
        started = asyncio.get_running_loop().time()
        listed, detail, searched, repos = await asyncio.wait_for(
            asyncio.gather(
                client.get("/api/entries?status=active"),
                client.get("/api/entries/inbox/entry.md", headers={"X-Atk-Request-Id": "read-detail"}),
                client.get("/api/entries?status=all&q=%E7%A7%98%E5%8C%BF%E8%AA%9E"),
                client.get("/api/repos"),
            ),
            timeout=5,
        )
        elapsed = asyncio.get_running_loop().time() - started
    finally:
        release.set()
    responses = await asyncio.gather(*updates)
    await asyncio.to_thread(holder.join, 10)

    assert [response.status_code for response in (listed, detail, searched, repos)] == [200, 200, 200, 200]
    assert elapsed < 1.0
    assert (await detail.get_json())["entry"]["content"] == _REBASE_AWI
    assert sorted(response.status_code for response in responses)[0] == 200
    records = [json.loads(line) for line in timing_log.read_text(encoding="utf-8").splitlines()]
    detail_record = next(record for record in records if record.get("request_id") == "read-detail")
    assert detail_record["route"] == "/api/entries/<state_name>/<filename>"
    assert detail_record["filename"] == "entry.md"
    assert detail_record["lock_wait_ms"] == 0
    update_records = [record for record in records if str(record.get("request_id", "")).startswith("update-")]
    assert len(update_records) == 3
    assert max(record["lock_wait_ms"] for record in update_records) > 0
    assert all({"total_ms", "queue_ms", "work_ms", "lock_hold_ms", "status"} <= record.keys() for record in update_records)
    raw = timing_log.read_text(encoding="utf-8")
    assert "変更0" not in raw
    assert "秘匿語" not in raw


@pytest.mark.asyncio
async def test_timing_api_records_only_allowed_browser_fields(tmp_path: pathlib.Path) -> None:
    """画面の所要記録は許可した項目と型だけを残し、本文などの項目を捨てる。"""
    timing_log = tmp_path / "timings.jsonl"
    app = serve_app.create_app(
        tmp_path, config.ServeConfig("127.0.0.1", 28766), state.ServeState(tmp_path), timing_log_path=timing_log
    )
    client = app.test_client()
    response = await client.post(
        "/api/timings",
        json={
            "records": [
                {"screen": "wi", "operation": "GET /api/entries", "fetch_ms": 12.34, "render_ms": 5, "content": "本文"},
                {
                    "screen": "wi",
                    "operation": "save",
                    "kind": "operation",
                    "operation_id": "abc123",
                    "queued_ms": 40,
                    "total_ms": 812.04,
                    "query": "検索語",
                },
                {"screen": "wi", "fetch_ms": 1},
                "不正",
            ]
        },
    )
    invalid = await client.post("/api/timings", json={"records": "x"})

    assert (response.status_code, invalid.status_code) == (204, 400)
    records = [json.loads(line) for line in timing_log.read_text(encoding="utf-8").splitlines()]
    assert [{key: value for key, value in record.items() if key != "at"} for record in records] == [
        {
            "source": "browser",
            "screen": "wi",
            "operation": "GET /api/entries",
            "fetch_ms": 12.3,
            "render_ms": 5.0,
        },
        {
            "source": "browser",
            "screen": "wi",
            "operation": "save",
            "kind": "operation",
            "operation_id": "abc123",
            "queued_ms": 40.0,
            "total_ms": 812.0,
        },
    ]


def test_timing_log_rotates_to_one_generation(tmp_path: pathlib.Path) -> None:
    """上限を超える追記は1世代へ回し、記録の合計量を2世代に保つ。"""
    path = tmp_path / "serve" / "timings.jsonl"
    log = timings.TimingLog(path, max_bytes=200)
    for index in range(20):
        log.write({"index": index, "padding": "x" * 30})

    rotated = path.with_name("timings.jsonl.1")
    assert path.stat().st_size <= 200
    assert rotated.stat().st_size <= 200
    assert sorted(path.parent.iterdir()) == sorted([path, rotated])
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[-1])["index"] == 19
