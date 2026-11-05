"""公開待機の診断から同じ所有者の保存結果へ戻る契約を検証する。"""

import json
import pathlib
from typing import Any

import pytest

from agent_toolkit._agents_server import agents_wait, shared_layout, wait_run


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
    assert "jq -r .output" in captured.err
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
    assert "agent_message_paths" in response["next_action"]


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


@pytest.mark.parametrize("status", ("published", "foreground-delivered", "consumed"))
@pytest.mark.parametrize("field", ("output", "exit_code"))
def test_incomplete_run_is_not_overwritten(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], status: str, field: str
) -> None:
    """公開以後の必須項目欠落を読取異常とし、元runとcurrent参照を保持する。"""
    path, _body_path = _saved_run(tmp_path, status)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop(field)
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = path.read_bytes()
    current = path.with_name("current.json")
    before_current = current.read_bytes()
    root = shared_layout.status_directory("root-session", tmp_path)
    (root / "root.json").write_text(
        json.dumps({"version": 1, "sessions": [{"session_id": "session-a", "status": "running"}]}), encoding="utf-8"
    )
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: 0)

    assert (
        agents_wait.wait_for_result(
            environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path, root_session_id="root-session"
        )
        == 6
    )

    captured = capsys.readouterr()
    assert not captured.out
    assert field in captured.err and str(path) in captured.err
    assert path.read_bytes() == before
    assert current.read_bytes() == before_current


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("status", "unknown"),
        ("run_id", "../outside"),
        ("targets", "session-a"),
        ("targets", ["../outside"]),
        ("output", None),
        ("exit_code", True),
        ("exit_code", "0"),
        ("stream", "other"),
        ("continuable", "false"),
    ),
)
def test_run_boundary_rejects_invalid_saved_fields(tmp_path: pathlib.Path, field: str, value: Any) -> None:
    """保存境界で状態と各項目の型を検証し、型変換やパスの逸脱を許さない。"""
    path, _body_path = _saved_run(tmp_path, "published")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        wait_run.read_run(path)


def test_run_transitions_preserve_saved_fields(tmp_path: pathlib.Path) -> None:
    """対象追加と公開・配送の遷移で同じrunのデータとJSONの実生成項目を保つ。"""
    run = wait_run.RunningRun(run_id="run-1", status="running", targets=["session-b"], started_at="2026-09-21T00:00:00+00:00")
    updated = run.add_targets(["session-a", "session-b"])
    assert run.targets == ["session-b"]
    assert updated.targets == ["session-a", "session-b"]
    running = updated.model_dump(exclude_unset=True)
    assert set(running) == {"run_id", "status", "targets", "started_at"}
    published = updated.publish("保存した出力", 3, "stderr")
    for result in (published.deliver(continuable=True), published.consume()):
        path = tmp_path / "run-1.json"
        path.write_text(json.dumps(result.model_dump(exclude_unset=True)), encoding="utf-8")
        restored = wait_run.read_run(path)
        assert restored == result
        assert isinstance(restored, (wait_run.ForegroundDeliveredRun, wait_run.ConsumedRun))
        assert restored.run_id == updated.run_id and restored.targets == updated.targets
        assert restored.output == "保存した出力" and restored.exit_code == 3 and restored.stream == "stderr"
        assert set(restored.model_dump(exclude_unset=True)) == set(running) | {"output", "exit_code", "stream", "continuable"}
    assert published.status == "published" and not published.continuable
    assert published.deliver(continuable=True).status == "foreground-delivered"
    assert published.consume().status == "consumed"


@pytest.mark.parametrize("value", ({}, {"run_id": "../outside"}, {"run_id": 2}, {"run_id": "run-1", "status": "running"}))
def test_current_reference_is_validated_separately(tmp_path: pathlib.Path, value: dict[str, Any]) -> None:
    """current参照にはrun本体や不正な識別子を受理しない。"""
    path = tmp_path / "current.json"
    assert wait_run.read_current(path) is None
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        wait_run.read_current(path)
