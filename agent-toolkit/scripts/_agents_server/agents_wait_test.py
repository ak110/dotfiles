"""atk agents-waitの公開CLI契約を検証する。"""

import json
import pathlib

import atk
import pytest
from _atk import config as _atk_config

from _agents_server import status_file


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

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents-wait", "session-1", "--timeout=0"])

    captured = capsys.readouterr()
    assert json.loads(captured.out) == payload
    assert captured.out.count("\n") == 1
    assert not captured.err
    assert not (wait_environment / "session-1.json").exists()


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
    assert not captured.out
    assert "待機が上限へ到達" in captured.err


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
