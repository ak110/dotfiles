# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""待機CLIの活動時刻・通知・動的対象を検証する。"""

import json
import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import agents_wait, state
from agent_toolkit._testing.agents_wait_support import (
    _publish_result_on_sleep,
    _running_body,
    _wait_for_session_1_result,
    _write_own_status,
)
from agent_toolkit._testing.agents_wait_support import _short_wait as _short_wait


def test_agents_wait_reports_seconds_since_output_on_timeout(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """待機上限の応答はsessionの活動時刻と最新テキスト出力時刻を別項目で返し、再待機の操作を示す。"""
    output_updated_at = "2026-09-09T00:00:00+00:00"
    updated_at = "2099-01-01T00:00:00+00:00"
    _write_own_status(
        wait_environment,
        [{"session_id": "session-1", "output_updated_at": output_updated_at, "updated_at": updated_at}],
    )

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents", "wait"])

    response = json.loads(capsys.readouterr().out)
    assert {"output_updated_at", "updated_at", "seconds_since_output"}.isdisjoint(response)
    assert response["seconds_since_activity"] == 0
    assert "atk agents wait" in response["next_action"]


@pytest.mark.parametrize(
    ("sessions", "expected"),
    [
        ([None], {"status": "running"}),
        (
            [{"session_id": "session-1", "started_at": "2026-09-09T00:00:00"}],
            {"session_id": "session-1", "status": "running"},
        ),
    ],
    ids=["non-dict-session", "naive-started-at"],
)
def test_agents_wait_omits_unreadable_output_activity_on_timeout(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    sessions: list[object],
    expected: dict[str, str],
) -> None:
    """解釈不能な共有状態では出力活動を省略してrunning応答を返す。"""
    _write_own_status(wait_environment, sessions)

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents", "wait"])

    assert _running_body(json.loads(capsys.readouterr().out)) == expected


def test_agents_wait_returns_stall_notice_after_threshold(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """長時間出力されないsessionは待機上限応答へ停滞候補を付ける。"""
    _write_own_status(
        wait_environment,
        [{"session_id": "session-1", "started_at": "2000-01-01T00:00:00+00:00", "output_updated_at": None}],
    )

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents", "wait"])

    response = json.loads(capsys.readouterr().out)
    assert "output_updated_at" not in response
    assert response["seconds_since_activity"] >= state.STALL_NOTICE_SECONDS


def test_agents_wait_keeps_waiting_after_stall_threshold(
    monkeypatch: pytest.MonkeyPatch,
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """停滞候補の診断後も待機を続け、次の周回で終了時の結果を回収する。"""
    _write_own_status(wait_environment, [{"session_id": "session-1", "updated_at": "2000-01-01T00:00:00+00:00"}])
    monotonic_values = iter((0.0, 1.0, state.STALL_NOTICE_SECONDS + 1.0))
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: state.STALL_NOTICE_SECONDS + 100.0)
    monkeypatch.setattr(agents_wait.time, "monotonic", lambda: next(monotonic_values))

    monkeypatch.setattr(agents_wait.time, "sleep", _publish_result_on_sleep(wait_environment))

    assert _wait_for_session_1_result(wait_environment) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "completed", "session_id": "session-1"}


def test_agents_wait_notices_response_reports_seconds_since_output(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """通知だけを回収する未終了応答にも活動時刻と最新テキスト出力時刻を付ける。"""
    output_updated_at = "2026-09-09T00:00:00+00:00"
    updated_at = "2099-01-01T00:00:00+00:00"
    _write_own_status(
        wait_environment,
        [{"session_id": "session-1", "output_updated_at": output_updated_at, "updated_at": updated_at}],
    )
    notices = wait_environment.parent / "notices"
    notices.mkdir()
    (notices / "session-1.1.json").write_text(
        json.dumps({"version": 1, "session_id": "session-1", "sent_at": output_updated_at, "body": "通知"}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])

    response = json.loads(capsys.readouterr().out)
    assert {"output_updated_at", "updated_at", "seconds_since_output"}.isdisjoint(response)
    assert response["seconds_since_activity"] == 0


def test_agents_wait_stall_uses_activity_not_text_output(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """テキスト出力が閾値を超えて止まっていても、活動が続く間は停滞の印を付けない。"""
    _write_own_status(
        wait_environment,
        [
            {
                "session_id": "session-1",
                "started_at": "2000-01-01T00:00:00+00:00",
                "output_updated_at": "2000-01-01T00:00:00+00:00",
                "updated_at": "2099-01-01T00:00:00+00:00",
            }
        ],
    )

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents", "wait"])

    response = json.loads(capsys.readouterr().out)
    assert "seconds_since_output" not in response
    assert response["seconds_since_activity"] == 0


def test_agents_wait_adds_notices_to_terminal_result(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """同じ周回の通知を終了時の結果へ加える。"""
    wait_environment.mkdir(parents=True)
    _write_own_status(wait_environment, [{"session_id": "session-1", "status": "completed"}])
    payload = {"session_id": "session-1", "status": "completed", "turn_seq": 2}
    (wait_environment / "session-1.json").write_text(json.dumps(payload), encoding="utf-8")
    notices = wait_environment.parent / "notices"
    notices.mkdir()
    notice = {
        "version": 1,
        "session_id": "session-1",
        "sent_at": "2026-09-07T00:00:01+00:00",
        "body": "終了時の通知",
    }
    (notices / "session-1.1.json").write_text(json.dumps(notice), encoding="utf-8")

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])

    payload.pop("turn_seq")
    payload["notices"] = [{"body": notice["body"]}]
    assert json.loads(capsys.readouterr().out) == payload
    assert not (wait_environment / "session-1.json").exists()
    assert not any(notices.iterdir())


def test_agents_wait_logs_targets_and_collection_without_result_body(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """待機対象の由来と回収主体を記録し、結果本文をログへ含めない。"""
    wait_environment.mkdir(parents=True)
    payload = {"status": "completed", "agent_message": "秘密の結果本文"}
    (wait_environment / "session-1.json").write_text(json.dumps(payload), encoding="utf-8")

    assert _wait_for_session_1_result(wait_environment) == 0

    assert json.loads(capsys.readouterr().out)["agent_message"] == "秘密の結果本文"
    log_text = (wait_environment.parents[2] / "agents-server.log").read_text(encoding="utf-8")
    assert "wait_start targets=session-1 origins=session-1:result" in log_text
    assert "collector=atk-agents-wait" in log_text
    assert "reason=collected count=1 session_ids=session-1" in log_text
    assert "秘密の結果本文" not in log_text


def test_agents_wait_collects_dynamic_target_under_owner_lock(
    monkeypatch: pytest.MonkeyPatch,
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """書込主体の単一ロックを保持したまま追加対象を待機集合へ加える。"""
    _write_own_status(wait_environment, [{"session_id": "session-1"}])
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: 10.0)
    monotonic_values = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(agents_wait.time, "monotonic", lambda: next(monotonic_values))

    def change_targets(_seconds: float) -> None:
        _write_own_status(wait_environment, [{"session_id": "session-1"}, {"session_id": "session-2"}])
        wait_environment.mkdir(parents=True, exist_ok=True)
        (wait_environment / "session-2.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")

    monkeypatch.setattr(agents_wait.time, "sleep", change_targets)
    assert _wait_for_session_1_result(wait_environment) == 0

    assert json.loads(capsys.readouterr().out) == {"status": "completed", "session_id": "session-2"}
    log_text = (wait_environment.parents[2] / "agents-server.log").read_text(encoding="utf-8")
    assert "wait_target_added session_id=session-2" in log_text
