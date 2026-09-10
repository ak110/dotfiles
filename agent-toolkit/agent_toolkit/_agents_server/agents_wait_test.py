"""atk agents-waitの公開CLI契約を検証する。"""

import json
import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import agents_wait, status_file
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common.file_lock import acquire_lock, release_lock


@pytest.fixture(name="wait_environment")
def _wait_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    """状態ファイルの解決先をテスト用ディレクトリへ隔離する。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "root-session")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_STATUS_HOST_SESSION", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(_atk_config, "state_dir", lambda: tmp_path)
    return status_file.results_directory("root-session", tmp_path)


def _write_own_status(results_directory: pathlib.Path, sessions: list[object]) -> pathlib.Path:
    """自身の書込主体の状態ファイルを作成する。"""
    root_status = results_directory.parent / "root.json"
    root_status.parent.mkdir(parents=True, exist_ok=True)
    root_status.write_text(json.dumps({"version": 1, "sessions": sessions}), encoding="utf-8")
    return root_status


def test_agents_wait_outputs_matching_result(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """識別子順の最初の終端結果だけを1行JSONで返し、残る結果を保持する。"""
    wait_environment.mkdir(parents=True)
    payload = {"session_id": "session-1", "status": "completed", "turn_seq": 2}
    (wait_environment / "session-1.json").write_text(json.dumps(payload), encoding="utf-8")
    other_result = wait_environment / "session-2.json"
    other_result.write_text(json.dumps({"session_id": "session-2", "status": "failed"}), encoding="utf-8")

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents-wait", "--timeout=0"])

    captured = capsys.readouterr()
    assert json.loads(captured.out) == payload
    assert captured.out.count("\n") == 1
    assert not captured.err
    assert not (wait_environment / "session-1.json").exists()
    assert other_result.exists()


def _wait_lock_path(tmp_path: pathlib.Path) -> pathlib.Path:
    """`session-1`の待機所有権を表すロックの経路を返す。"""
    return status_file.status_directory("root-session", tmp_path) / "wait-locks" / "session-1.lock"


def _wait_while_session_1_is_locked(tmp_path: pathlib.Path, own_session_id: str) -> int:
    """別主体が`session-1`の待機所有権を保持する状態で待機を発行し終了コードを返す。"""
    _write_own_status(status_file.results_directory("root-session", tmp_path), [{"session_id": own_session_id}])
    lock_path = _wait_lock_path(tmp_path)
    lock_path.parent.mkdir(parents=True)
    with lock_path.open("a+b") as lock_file:
        acquire_lock(lock_file, blocking=False)
        try:
            return agents_wait.wait_for_result(
                0,
                environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
                state_root=tmp_path,
            )
        finally:
            release_lock(lock_file)


def test_agents_wait_rejects_overlapping_owner(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """同じsessionの待機所有権を保持する後発は理由と対象sessionを添えて終了コード8で拒否する。"""
    assert _wait_while_session_1_is_locked(tmp_path, "session-1") == 8

    assert _wait_lock_path(tmp_path).exists()
    assert "session-1" in capsys.readouterr().err


def test_agents_wait_allows_disjoint_sets(tmp_path: pathlib.Path) -> None:
    """別sessionの待機所有権は互いに競合しない。"""
    assert _wait_while_session_1_is_locked(tmp_path, "session-2") == 3


def test_agents_wait_resolves_changed_conversation_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """会話のsession識別子が変わった後も索引先の終端結果を回収する。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "current-session")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(_atk_config, "state_dir", lambda: tmp_path)
    results = status_file.results_directory("root-session", tmp_path)
    results.mkdir(parents=True)
    payload = {"session_id": "session-1", "status": "completed"}
    (results / "session-1.json").write_text(json.dumps(payload), encoding="utf-8")
    status_file.write_root_alias("current-session", "root-session", tmp_path)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents-wait", "--timeout=0"])

    assert json.loads(capsys.readouterr().out) == payload
    assert not (results / "session-1.json").exists()


def test_agents_wait_times_out_without_result(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """結果が無い場合は終了コード3を返す。"""
    assert not wait_environment.exists()

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents-wait", "--timeout=0"])

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"status": "running"}
    assert not captured.err


def test_agents_wait_returns_expired_when_session_is_absent_from_root(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """終端結果と通知が無いまま対象が消失した場合を待機上限より前に返す。"""
    _write_own_status(wait_environment, [])

    with pytest.raises(SystemExit, match="7"):
        atk.main(["agents-wait", "--timeout=3600"])

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"status": "expired"}
    assert captured.out.count("\n") == 1
    assert not captured.err


def test_agents_wait_keeps_waiting_for_retained_session(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """状態ファイルが保持するsessionを消失として返さない。"""
    _write_own_status(wait_environment, [{"session_id": "session-1"}])

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents-wait", "--timeout=0"])

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"session_id": "session-1", "status": "running"}
    assert not captured.err


def test_agents_wait_does_not_expire_without_own_status_file(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """自身の書込主体の状態ファイルが無い場合を消失として返さない。"""
    nested_status = wait_environment.parent / "child-session.json"
    nested_status.parent.mkdir(parents=True)
    nested_status.write_text(
        json.dumps({"version": 1, "sessions": [{"session_id": "session-1"}]}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents-wait", "--timeout=0"])

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"status": "running"}
    assert not captured.err


@pytest.mark.parametrize("root_body", ["{", "[]"])
def test_agents_wait_ignores_unreadable_root_status(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    root_body: str,
) -> None:
    """状態ファイルを解釈できない場合は従来の待機を継続する。"""
    root_status = wait_environment.parent / "root.json"
    root_status.parent.mkdir(parents=True)
    root_status.write_text(root_body, encoding="utf-8")

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents-wait", "--timeout=0"])

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"status": "running"}
    assert not captured.err


@pytest.mark.parametrize("result_body", ["[]", "{"])
def test_agents_wait_rejects_corrupted_result(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    result_body: str,
) -> None:
    """破損した終端結果は待機上限と別の終了コードで停止する。"""
    wait_environment.mkdir(parents=True)
    (wait_environment / "session-1.json").write_text(result_body, encoding="utf-8")

    with pytest.raises(SystemExit, match="6"):
        atk.main(["agents-wait", "--timeout=0"])

    captured = capsys.readouterr()
    assert not captured.out
    assert str(wait_environment / "session-1.json") in captured.err


@pytest.mark.parametrize("session_id", ["../outside", ""])
def test_agents_wait_rejects_invalid_session_in_status_file(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    session_id: str,
) -> None:
    """形式外のsession識別子を結果ファイルの解決前に拒否する。"""
    _write_own_status(wait_environment, [{"session_id": session_id}])

    with pytest.raises(SystemExit, match="5"):
        atk.main(["agents-wait", "--timeout=0"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "session_idの形式が不正" in captured.err


def test_agents_wait_rejects_turn(capsys: pytest.CaptureFixture[str]) -> None:
    """撤去したturn指定はargparseの終了コード2で拒否する。"""
    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents-wait", "--" + "turn=1"])
    assert not capsys.readouterr().out


def test_agents_wait_rejects_positional_session(capsys: pytest.CaptureFixture[str]) -> None:
    """撤去した待機対象の位置引数はargparseの終了コード2で拒否する。"""
    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents-wait", "session-1"])
    assert not capsys.readouterr().out


@pytest.mark.parametrize(
    "environment",
    [{}, {"AGENT_TOOLKIT_OWNER_SESSION": "root-session"}],
    ids=["no-root-session", "owner-without-host-session"],
)
def test_missing_state_dir_reports_alternative(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    environment: dict[str, str],
) -> None:
    """状態ファイルの書込主体を解決できない場合は終了コード4を返す。"""
    assert agents_wait.wait_for_result(0, environment=environment, state_root=tmp_path) == 4

    captured = capsys.readouterr()
    assert not captured.out
    assert "状態ディレクトリを解決できません" in captured.err
    assert "`agents_server`の`list`と`wait`" in captured.err


def test_agents_wait_returns_notices_while_running(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """終端前の通知を回収し、running状態とともに1行で返す。"""
    _write_own_status(wait_environment, [{"session_id": "session-1"}])
    notices = wait_environment.parent / "notices"
    notices.mkdir(parents=True)
    for sequence, sent_at, body in (
        (2, "2026-09-07T00:00:02+00:00", "後の通知"),
        (1, "2026-09-07T00:00:01+00:00", "先の通知"),
    ):
        payload = {"version": 1, "session_id": "session-1", "sent_at": sent_at, "body": body}
        (notices / f"session-1.{sequence}.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents-wait", "--timeout=0"])

    response = json.loads(capsys.readouterr().out)
    assert response == {
        "session_id": "session-1",
        "status": "running",
        "notices": [
            {"sent_at": "2026-09-07T00:00:01+00:00", "body": "先の通知"},
            {"sent_at": "2026-09-07T00:00:02+00:00", "body": "後の通知"},
        ],
    }
    assert not any(notices.iterdir())


def test_agents_wait_reports_seconds_since_update_on_timeout(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """待機上限の応答はsessionの最終活動時刻を返す。"""
    updated_at = "2026-09-09T00:00:00+00:00"
    _write_own_status(wait_environment, [{"session_id": "session-1", "updated_at": updated_at}])

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents-wait", "--timeout=0"])

    response = json.loads(capsys.readouterr().out)
    assert response["updated_at"] == updated_at
    assert isinstance(response["seconds_since_update"], float)


@pytest.mark.parametrize(
    ("sessions", "expected"),
    [
        ([None], {"status": "running"}),
        (
            [{"session_id": "session-1", "updated_at": "2026-09-09T00:00:00"}],
            {"session_id": "session-1", "status": "running"},
        ),
    ],
    ids=["non-dict-session", "naive-updated-at"],
)
def test_agents_wait_omits_unreadable_session_updated_at_on_timeout(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    sessions: list[object],
    expected: dict[str, str],
) -> None:
    """解釈不能な共有状態では更新時刻を省略してrunning応答を返す。"""
    _write_own_status(wait_environment, sessions)

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents-wait", "--timeout=0"])

    assert json.loads(capsys.readouterr().out) == expected


def test_agents_wait_returns_stall_notice_after_threshold(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """長時間更新されないsessionは待機上限応答へ停滞候補を付ける。"""
    _write_own_status(wait_environment, [{"session_id": "session-1", "updated_at": "2000-01-01T00:00:00+00:00"}])

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents-wait", "--timeout=0"])

    assert json.loads(capsys.readouterr().out)["stalled"] is True


def test_agents_wait_notices_response_reports_seconds_since_update(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """通知だけを回収する非終端応答にも最終活動時刻を付ける。"""
    updated_at = "2026-09-09T00:00:00+00:00"
    _write_own_status(wait_environment, [{"session_id": "session-1", "updated_at": updated_at}])
    notices = wait_environment.parent / "notices"
    notices.mkdir()
    (notices / "session-1.1.json").write_text(
        json.dumps({"version": 1, "session_id": "session-1", "sent_at": updated_at, "body": "通知"}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents-wait", "--timeout=0"])

    response = json.loads(capsys.readouterr().out)
    assert response["updated_at"] == updated_at
    assert isinstance(response["seconds_since_update"], float)


def test_agents_wait_adds_notices_to_terminal_result(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """同じ周回の通知を終端結果へ加える。"""
    wait_environment.mkdir(parents=True)
    payload = {"session_id": "session-1", "status": "completed", "turn_seq": 2}
    (wait_environment / "session-1.json").write_text(json.dumps(payload), encoding="utf-8")
    notices = wait_environment.parent / "notices"
    notices.mkdir()
    notice = {
        "version": 1,
        "session_id": "session-1",
        "sent_at": "2026-09-07T00:00:01+00:00",
        "body": "終端時の通知",
    }
    (notices / "session-1.1.json").write_text(json.dumps(notice), encoding="utf-8")

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents-wait", "--timeout=0"])

    payload["notices"] = [{"sent_at": notice["sent_at"], "body": notice["body"]}]
    assert json.loads(capsys.readouterr().out) == payload
    assert not (wait_environment / "session-1.json").exists()
    assert not any(notices.iterdir())
