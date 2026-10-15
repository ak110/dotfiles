"""公開待機の診断から同じ所有者の保存結果へ戻る契約を検証する。"""

import json
import pathlib
from typing import Any

import pytest

from agent_toolkit._agents_server import agents_wait, shared_layout


def _saved_run(tmp_path: pathlib.Path, status: str, *, owner: str = "root.json") -> tuple[pathlib.Path, pathlib.Path]:
    root = shared_layout.status_directory("root-session", tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    (root / "root.json").write_text(json.dumps({"version": 1, "sessions": []}), encoding="utf-8")
    directory = root / "wait-results" / owner
    directory.mkdir(parents=True)
    path = directory / "run-1.json"
    (directory / "current.json").write_text(json.dumps({"run_id": "run-1"}), encoding="utf-8")
    body_path = tmp_path / "body.md"
    body_path.write_text("元の報告全文\n2行目", encoding="utf-8")
    output = "\n".join(
        json.dumps(row, ensure_ascii=False)
        for row in [
            {
                "session_id": "session-a",
                "status": "completed",
                "agent_message": "元の報告全文\n2行目",
                "agent_message_path": str(body_path),
            },
            {"session_id": "session-b", "status": "failed", "agent_message": "失敗の報告"},
        ]
    )
    path.write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "status": status,
                "targets": ["session-a", "session-b"],
                "output": output,
                "exit_code": 0,
                "stream": "stdout",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path, body_path


@pytest.mark.parametrize("status", ("foreground-delivered", "consumed"))
@pytest.mark.parametrize("body_exists", (False, True))
def test_empty_wait_points_to_delivered_run(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], status: str, body_exists: bool
) -> None:
    """正常回収後の再waitは終了10を保ち、保存runから複数sessionの本文を全文復元できる。"""
    path, body_path = _saved_run(tmp_path, status)
    if not body_exists:
        body_path.unlink()
    code = agents_wait.wait_for_result(
        environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
        state_root=tmp_path,
        root_session_id="root-session",
    )
    captured = capsys.readouterr()
    assert code == 10
    assert str(path) in captured.err
    assert "session-a" in captured.err and "session-b" in captured.err
    assert "atk read-file" in captured.err
    assert "未読" not in captured.err
    saved = json.loads(path.read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in saved["output"].splitlines()]
    assert rows[0]["agent_message"] == "元の報告全文\n2行目"
    assert saved["status"] == status


def test_consumed_wait_points_to_saved_result(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """同時待機へ合流した後続は終了8の診断から当該runの保存本文へ戻れる。"""
    path, _body_path = _saved_run(tmp_path, "consumed")
    root = shared_layout.status_directory("root-session", tmp_path)
    (root / "root.json").write_text(
        json.dumps({"version": 1, "sessions": [{"session_id": "still-running", "status": "running"}]}),
        encoding="utf-8",
    )

    lock_contended = True

    def lock(_handle: Any, **_options: Any) -> None:
        nonlocal lock_contended
        # 初回の競合後に先行waitが解放した状態を表す。
        if lock_contended:
            lock_contended = False
            raise BlockingIOError("先行waitが所有")

    monkeypatch.setattr(agents_wait, "acquire_lock", lock)
    code = agents_wait.wait_for_result(
        environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
        state_root=tmp_path,
        root_session_id="root-session",
    )
    response = json.loads(capsys.readouterr().out)
    assert code == 8
    assert response["recovery"]["run_path"] == str(path)
    assert response["recovery"]["session_ids"] == ["session-a", "session-b"]
    assert "atk read-file" in response["next_action"]


@pytest.mark.parametrize("problem", ("missing", "broken", "empty", "other-owner"))
def test_saved_wait_recovery_boundaries(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], problem: str) -> None:
    """記録の不在・破損・結果なしを区別し、別所有者の記録を復旧先へ使わない。"""
    path, _body_path = _saved_run(
        tmp_path, "foreground-delivered", owner="other.json" if problem == "other-owner" else "root.json"
    )
    if problem == "missing":
        path.unlink()
    elif problem == "broken":
        path.write_text("{", encoding="utf-8")
    elif problem == "empty":
        value = json.loads(path.read_text(encoding="utf-8"))
        value["output"] = ""
        path.write_text(json.dumps(value), encoding="utf-8")
    code = agents_wait.wait_for_result(
        environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
        state_root=tmp_path,
        root_session_id="root-session",
    )
    captured = capsys.readouterr()
    assert code == 10
    if problem == "other-owner":
        assert str(path) not in captured.err
        assert "session-a" not in captured.err
    else:
        expected = {"missing": "missing", "broken": "unreadable", "empty": "no-results"}[problem]
        assert expected in captured.err
        assert str(path) in captured.err
