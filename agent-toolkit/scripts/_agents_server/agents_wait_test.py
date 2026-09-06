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
    """指定番号以上の終端結果を1行JSONで返す。"""
    wait_environment.mkdir(parents=True)
    payload = {"session_id": "session-1", "status": "completed", "turn_seq": 2}
    (wait_environment / "session-1.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents-wait", "session-1", "--turn=2", "--timeout=0"])

    captured = capsys.readouterr()
    assert json.loads(captured.out) == payload
    assert captured.out.count("\n") == 1
    assert not captured.err


@pytest.mark.parametrize("result_turn", [None, 1])
def test_agents_wait_times_out_without_matching_result(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    result_turn: int | None,
) -> None:
    """結果が無い場合と古いturnだけの場合は終了コード3を返す。"""
    if result_turn is not None:
        wait_environment.mkdir(parents=True)
        (wait_environment / "session-1.json").write_text(
            json.dumps({"session_id": "session-1", "turn_seq": result_turn}),
            encoding="utf-8",
        )

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents-wait", "session-1", "--turn=2", "--timeout=0"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "待機が上限へ到達" in captured.err


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
        atk.main(["agents-wait", session_id, "--turn=1", "--timeout=0"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "session_idの形式が不正" in captured.err


def test_agents_wait_requires_turn(capsys: pytest.CaptureFixture[str]) -> None:
    """turn指定の欠落はargparseの終了コード2で拒否する。"""
    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents-wait", "session-1"])
    assert not capsys.readouterr().out


def test_agents_wait_reports_unresolved_state_directory(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ルートsessionを解決できない場合は終了コード4を返す。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)

    with pytest.raises(SystemExit, match="4"):
        atk.main(["agents-wait", "session-1", "--turn=1", "--timeout=0"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "状態ディレクトリを解決できません" in captured.err
