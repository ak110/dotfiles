# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""待機CLIの共有期限と保持期間による上限を検証する。"""

import asyncio
import json
import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import agents_wait, shared_roots
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._common.file_lock import release_lock
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX
from agent_toolkit._testing.agents_wait_support import (
    _TTL_CASES,
    _observe_cli_wait_limit,
    _running_body,
    _set_ttl_environment,
    _write_own_status,
)
from agent_toolkit._testing.agents_wait_support import _short_wait as _short_wait
from agent_toolkit._testing.agents_wait_support_contention import (
    _leave_result_original,
    _stash_notice,
    _stash_result,
    _wait_lock_path,
    _write_current_wait_run,
)


@pytest.mark.parametrize("host_alias", [False, True], ids=["root", "codex-alias"])
def test_agents_wait_contention_uses_derived_deadline_and_preserves_results(
    monkeypatch: pytest.MonkeyPatch,
    wait_environment: pathlib.Path,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    host_alias: bool,
) -> None:
    """長時間の競合は導出期限で診断を返し、固定runと原本・退避物を保持する。"""
    _set_ttl_environment(monkeypatch, {})
    if host_alias:
        shared_roots.write_host_alias("root-session", "root", "codex-thread", tmp_path)
        monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "root-session")
        monkeypatch.setenv("CODEX_THREAD_ID", "codex-thread")
        monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    _write_own_status(wait_environment, [{"session_id": "session-1"}])
    directory, run_path = _write_current_wait_run(tmp_path, target="session-1", status="running")
    _stash_result(tmp_path, run_path)
    _leave_result_original(tmp_path, run_path)
    _stash_notice(tmp_path, run_path)
    preserved = {path: path.read_bytes() for path in tmp_path.rglob("*.json") if path.is_file()}
    # 委譲先の印（`AGENT_TOOLKIT_OWNER_SESSION`）があってもCLIの待機上限は縮めない。
    expected_limit = 270.0
    clock = [0.0]
    sleeps: list[float] = []
    original_acquire = agents_wait.acquire_lock

    def nonblocking_only(lock_file, *, blocking: bool) -> None:
        assert not blocking, "待機全体の期限をOSのblocking取得へ委ねている"
        original_acquire(lock_file, blocking=False)

    def advance(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] = expected_limit - 0.5 if len(sleeps) == 1 else clock[0] + seconds
        # 競合開始後にcurrentが動いても、合流先は最初のrunへ固定する。
        agents_wait._write_json(directory / "current.json", {"run_id": "later-run"})  # pylint: disable=protected-access

    monkeypatch.setattr(agents_wait, "acquire_lock", nonblocking_only)
    monkeypatch.setattr(agents_wait.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(agents_wait.time, "sleep", advance)
    lock_path = _wait_lock_path(tmp_path)
    lock_path.parent.mkdir(parents=True)
    with lock_path.open("a+b") as lock_file:
        original_acquire(lock_file, blocking=False)
        try:
            with pytest.raises(SystemExit, match="8"):
                atk.main(["agents", "wait"])
        finally:
            release_lock(lock_file)

    captured = capsys.readouterr()
    assert not captured.out
    for fragment in ("owner=root.json", "run_id=run-1", "targets=session-1", f"run_path={run_path.resolve()}"):
        assert fragment in captured.err
    assert "Traceback" not in captured.err
    assert NEXT_ACTION_PREFIX in captured.err
    assert "保存出力" in captured.err
    assert "jq -r .output" in captured.err
    assert sleeps == [1.0, 0.5]
    assert clock[0] == expected_limit
    for path, content in preserved.items():
        if path != directory / "current.json":
            assert path.read_bytes() == content
    assert run_path.exists()


@pytest.mark.parametrize("contended", [False, True], ids=["uncontended", "contended"])
def test_agents_wait_shares_deadline_with_result_wait_and_added_target(
    monkeypatch: pytest.MonkeyPatch,
    wait_environment: pathlib.Path,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    contended: bool,
) -> None:
    """取得途中の解放と動的対象追加があっても、結果待ちまで一回の期限で終了する。"""
    _set_ttl_environment(monkeypatch, {})
    _write_own_status(wait_environment, [{"session_id": "session-1"}])
    directory, run_path = _write_current_wait_run(tmp_path, target="session-1", status="running")
    lock_path = _wait_lock_path(tmp_path)
    lock_path.parent.mkdir(parents=True)
    clock = [0.0]
    sleeps: list[float] = []
    original_acquire = agents_wait.acquire_lock

    def nonblocking_only(lock_file, *, blocking: bool) -> None:
        assert not blocking, "期限を持つ合流は非blocking取得を使う"
        original_acquire(lock_file, blocking=False)

    with lock_path.open("a+b") as lock_file:
        if contended:
            original_acquire(lock_file, blocking=False)

        def advance(seconds: float) -> None:
            sleeps.append(seconds)
            if len(sleeps) == 1:
                clock[0] = 120.0
                _write_own_status(wait_environment, [{"session_id": "session-1"}, {"session_id": "session-2"}])
                run = json.loads(run_path.read_text(encoding="utf-8"))
                run["targets"] = ["session-1", "session-2"]
                agents_wait._write_json(run_path, run)  # pylint: disable=protected-access
                if contended:
                    release_lock(lock_file)
            elif len(sleeps) == 2:
                clock[0] = 269.5
            else:
                clock[0] += seconds
                assert len(sleeps) <= 3, "結果待ちが期限を開始し直している"

        monkeypatch.setattr(agents_wait, "acquire_lock", nonblocking_only)
        monkeypatch.setattr(agents_wait.time, "monotonic", lambda: clock[0])
        monkeypatch.setattr(agents_wait.time, "sleep", advance)
        try:
            with pytest.raises(SystemExit, match="3"):
                atk.main(["agents", "wait"])
        finally:
            if contended:
                release_lock(lock_file)

    captured = capsys.readouterr()
    assert not captured.err
    assert _running_body(json.loads(captured.out)) == {"session_id": "session-1", "status": "running"}
    assert sleeps == [1.0, 1.0, 0.5]
    assert clock[0] == 270.0
    assert json.loads((directory / "current.json").read_text(encoding="utf-8"))["run_id"] == "run-1"
    assert json.loads(run_path.read_text(encoding="utf-8"))["targets"] == ["session-1", "session-2"]


@pytest.mark.parametrize(("environment", "expected_limit", "_mcp_limit"), _TTL_CASES)
def test_wait_limit_follows_prompt_cache_ttl(
    monkeypatch: pytest.MonkeyPatch,
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    environment: dict[str, str],
    expected_limit: float,
    _mcp_limit: float,
) -> None:
    """CLIの待機上限は保持期間とホストの条件から導出され、到達時に終了コード3で戻る。

    上限の定数を置き換えず、環境条件だけを切り替える。固定値へ戻ると、条件ごとの秒数で上限へ到達しない。
    委譲先の印で240秒へ縮めると、長時間の背景待機が約4分ごとの終了と再発行を反復する。
    """
    _set_ttl_environment(monkeypatch, environment)

    assert _observe_cli_wait_limit(monkeypatch, wait_environment, capsys, expected_limit) == expected_limit


@pytest.mark.parametrize(("environment", "cli_limit", "mcp_limit"), _TTL_CASES)
def test_cli_and_mcp_wait_limits_differ_only_by_delegation(
    monkeypatch: pytest.MonkeyPatch,
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    environment: dict[str, str],
    cli_limit: float,
    mcp_limit: float,
) -> None:
    """CLIとMCPの待機上限は同じ保持期間・ホストの判定を共有し、委譲先の短縮はMCPの待機だけに適用する。"""
    _set_ttl_environment(monkeypatch, environment)
    # 導出値の保持だけを持つ最小のインスタンスで、MCPの`wait`が使う解決処理を呼ぶ。
    manager = server_manager.AgentsServerManager.__new__(server_manager.AgentsServerManager)
    manager._wait_timeouts = {}  # pylint: disable=protected-access

    assert asyncio.run(manager._resolve_wait_timeout("main")) == mcp_limit  # pylint: disable=protected-access
    assert _observe_cli_wait_limit(monkeypatch, wait_environment, capsys, cli_limit) == cli_limit
