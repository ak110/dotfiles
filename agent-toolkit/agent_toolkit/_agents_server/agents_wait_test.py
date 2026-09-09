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
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "root-session")
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(_atk_config, "state_dir", lambda: tmp_path)
    return status_file.results_directory("root-session", tmp_path)


def test_agents_wait_outputs_matching_result(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """終端結果を1行JSONで返す。"""
    wait_environment.mkdir(parents=True)
    payload = {"session_id": "session-1", "status": "completed", "turn_seq": 2}
    (wait_environment / "session-1.json").write_text(json.dumps(payload), encoding="utf-8")
    other_result = wait_environment / "session-2.json"
    other_result.write_text(json.dumps({"session_id": "session-2", "status": "failed"}), encoding="utf-8")

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents-wait", "session-1", "--timeout=0"])

    captured = capsys.readouterr()
    assert json.loads(captured.out) == payload
    assert captured.out.count("\n") == 1
    assert not captured.err
    assert not (wait_environment / "session-1.json").exists()
    assert other_result.exists()


def test_agents_wait_any_removes_only_selected_result(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """複数待機は昇順の最初の結果だけを回収する。"""
    wait_environment.mkdir(parents=True)
    first = wait_environment / "session-1.json"
    second = wait_environment / "session-2.json"
    first.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    second.write_text(json.dumps({"status": "failed"}), encoding="utf-8")

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents-wait-any", "session-2", "session-1", "--timeout=0"])

    assert json.loads(capsys.readouterr().out)["session_id"] == "session-1"
    assert not first.exists()
    assert second.exists()


def test_agents_wait_any_rejects_overlapping_owner(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """同じsessionの待機所有権を保持する後発は理由と対象sessionを添えて終了コード8で拒否する。"""
    lock_path = status_file.status_directory("root-session", tmp_path) / "wait-any-locks" / "session-1.lock"
    lock_path.parent.mkdir(parents=True)
    with lock_path.open("a+b") as lock_file:
        acquire_lock(lock_file, blocking=False)
        try:
            assert (
                agents_wait.wait_for_any_result(
                    ["session-1"],
                    0,
                    environment={"AGENT_TOOLKIT_OWNER_SESSION": "root-session"},
                    state_root=tmp_path,
                )
                == 8
            )
        finally:
            release_lock(lock_file)
    assert lock_path.exists()
    assert "session-1" in capsys.readouterr().err


def test_agents_wait_any_allows_disjoint_sets(tmp_path: pathlib.Path) -> None:
    """別sessionの待機所有権は互いに競合しない。"""
    lock_path = status_file.status_directory("root-session", tmp_path) / "wait-any-locks" / "session-1.lock"
    lock_path.parent.mkdir(parents=True)
    with lock_path.open("a+b") as lock_file:
        acquire_lock(lock_file, blocking=False)
        try:
            assert (
                agents_wait.wait_for_any_result(
                    ["session-2"],
                    0,
                    environment={"AGENT_TOOLKIT_OWNER_SESSION": "root-session"},
                    state_root=tmp_path,
                )
                == 3
            )
        finally:
            release_lock(lock_file)


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
        atk.main(["agents-wait", "session-1", "--timeout=0"])

    assert json.loads(capsys.readouterr().out) == payload
    assert not (results / "session-1.json").exists()


@pytest.mark.parametrize("result_body", [None])
def test_agents_wait_times_out_without_result(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    result_body: str | None,
) -> None:
    """結果が無い場合は終了コード3を返す。"""
    if result_body is not None:
        wait_environment.mkdir(parents=True)
        (wait_environment / "session-1.json").write_text(result_body, encoding="utf-8")

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents-wait", "session-1", "--timeout=0"])

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"session_id": "session-1", "status": "running"}
    assert not captured.err


def test_agents_wait_returns_expired_when_session_is_absent_from_root(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """終端結果と通知が無いsessionの消失を待機上限より前に返す。"""
    root_status = wait_environment.parent / "root.json"
    root_status.parent.mkdir(parents=True)
    root_status.write_text(json.dumps({"version": 1, "sessions": []}), encoding="utf-8")

    with pytest.raises(SystemExit, match="7"):
        atk.main(["agents-wait", "session-1", "--timeout=3600"])

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"session_id": "session-1", "status": "expired"}
    assert captured.out.count("\n") == 1
    assert not captured.err


def test_agents_wait_keeps_waiting_for_retained_session(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """状態ファイルが保持するsessionを消失として返さない。"""
    root_status = wait_environment.parent / "root.json"
    root_status.parent.mkdir(parents=True)
    root_status.write_text(
        json.dumps({"version": 1, "sessions": [{"session_id": "session-1"}]}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents-wait", "session-1", "--timeout=0"])

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"session_id": "session-1", "status": "running"}
    assert not captured.err


def test_agents_wait_keeps_waiting_for_session_retained_by_nested_server(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """委譲先のMCPサーバーが保持するsessionを消失として返さない。"""
    nested_status = wait_environment.parent / "child-session.json"
    nested_status.parent.mkdir(parents=True)
    nested_status.write_text(
        json.dumps({"version": 1, "sessions": [{"session_id": "session-1"}]}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents-wait", "session-1", "--timeout=0"])

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"session_id": "session-1", "status": "running"}
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
        atk.main(["agents-wait", "session-1", "--timeout=0"])

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"session_id": "session-1", "status": "running"}
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
        atk.main(["agents-wait", "session-1", "--timeout=0"])

    captured = capsys.readouterr()
    assert not captured.out
    assert str(wait_environment / "session-1.json") in captured.err


@pytest.mark.parametrize("session_id", ["../outside", ""])
def test_agents_wait_rejects_invalid_session_before_path_resolution(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    session_id: str,
) -> None:
    """形式外のsession識別子を状態ディレクトリの解決前に拒否する。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)

    with pytest.raises(SystemExit, match="5"):
        atk.main(["agents-wait", session_id, "--timeout=0"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "session_idの形式が不正" in captured.err


def test_agents_wait_rejects_turn(capsys: pytest.CaptureFixture[str]) -> None:
    """撤去したturn指定はargparseの終了コード2で拒否する。"""
    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents-wait", "session-1", "--" + "turn=1"])
    assert not capsys.readouterr().out


def test_missing_state_dir_reports_alternative(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ルートsessionを解決できない場合は終了コード4を返す。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)

    with pytest.raises(SystemExit, match="4"):
        atk.main(["agents-wait", "session-1", "--timeout=0"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "状態ディレクトリを解決できません" in captured.err
    assert "`agents_server`の`list`と`wait`" in captured.err


def test_agents_wait_returns_notices_while_running(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """終端前の通知を回収し、running状態とともに1行で返す。"""
    notices = wait_environment.parent / "notices"
    notices.mkdir(parents=True)
    for sequence, sent_at, body in (
        (2, "2026-09-07T00:00:02+00:00", "後の通知"),
        (1, "2026-09-07T00:00:01+00:00", "先の通知"),
    ):
        payload = {"version": 1, "session_id": "session-1", "sent_at": sent_at, "body": body}
        (notices / f"session-1.{sequence}.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents-wait", "session-1", "--timeout=0"])

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
    root_status = wait_environment.parent / "root.json"
    root_status.parent.mkdir(parents=True)
    updated_at = "2026-09-09T00:00:00+00:00"
    root_status.write_text(
        json.dumps({"version": 1, "sessions": [{"session_id": "session-1", "updated_at": updated_at}]}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents-wait", "session-1", "--timeout=0"])

    response = json.loads(capsys.readouterr().out)
    assert response["updated_at"] == updated_at
    assert isinstance(response["seconds_since_update"], float)


@pytest.mark.parametrize(
    "sessions",
    [
        [None],
        [{"session_id": "session-1", "updated_at": "2026-09-09T00:00:00"}],
    ],
    ids=["non-dict-session", "naive-updated-at"],
)
def test_agents_wait_omits_unreadable_session_updated_at_on_timeout(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    sessions: list[object],
) -> None:
    """解釈不能な共有状態では更新時刻を省略してrunning応答を返す。"""
    root_status = wait_environment.parent / "root.json"
    root_status.parent.mkdir(parents=True)
    root_status.write_text(json.dumps({"version": 1, "sessions": sessions}), encoding="utf-8")

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents-wait", "session-1", "--timeout=0"])

    assert json.loads(capsys.readouterr().out) == {"session_id": "session-1", "status": "running"}


def test_agents_wait_returns_stall_notice_after_threshold(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """長時間更新されないsessionは待機上限応答へ停滞候補を付ける。"""
    root_status = wait_environment.parent / "root.json"
    root_status.parent.mkdir(parents=True)
    root_status.write_text(
        json.dumps({"version": 1, "sessions": [{"session_id": "session-1", "updated_at": "2000-01-01T00:00:00+00:00"}]}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents-wait", "session-1", "--timeout=0"])

    assert json.loads(capsys.readouterr().out)["stalled"] is True


def test_agents_wait_notices_response_reports_seconds_since_update(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """通知だけを回収する非終端応答にも最終活動時刻を付ける。"""
    root_status = wait_environment.parent / "root.json"
    root_status.parent.mkdir(parents=True)
    updated_at = "2026-09-09T00:00:00+00:00"
    root_status.write_text(
        json.dumps({"version": 1, "sessions": [{"session_id": "session-1", "updated_at": updated_at}]}),
        encoding="utf-8",
    )
    notices = wait_environment.parent / "notices"
    notices.mkdir()
    (notices / "session-1.1.json").write_text(
        json.dumps({"version": 1, "session_id": "session-1", "sent_at": updated_at, "body": "通知"}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents-wait", "session-1", "--timeout=0"])

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
        atk.main(["agents-wait", "session-1", "--timeout=0"])

    payload["notices"] = [{"sent_at": notice["sent_at"], "body": notice["body"]}]
    assert json.loads(capsys.readouterr().out) == payload
    assert not (wait_environment / "session-1.json").exists()
    assert not any(notices.iterdir())
