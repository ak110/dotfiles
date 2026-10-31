"""agent-toolkit/agent_toolkit/_hooks/progress_self_check.py のテスト。

通知の要否と状態の更新を判定関数で確かめ、PostToolUseとUserPromptSubmitの両フックが
判定の結果を`additionalContext`へ載せることをhook.pyのサブコマンド経由で確かめる。
"""

import json
import os
import pathlib
import subprocess
import uuid

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._common.periodic_recheck import PERIODIC_RECHECK_MARKER
from agent_toolkit._hooks import progress_self_check
from agent_toolkit._testing import fork_runner as _fork_runner

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "hook.py"
_INTERVAL = progress_self_check.SELF_CHECK_INTERVAL_SECONDS
_OPENING = f'<atk-auto source="{progress_self_check.SOURCE}" kind="notice">'


def _entries(session_id: str) -> dict:
    return session_state.read_state(session_id).get(progress_self_check.STATE_KEY, {})


def _seed(session_id: str, agent_id: str, *, started_at: float, last_notified_at: float) -> None:
    def _mutate(state: dict) -> dict:
        entries = dict(state.get(progress_self_check.STATE_KEY, {}))
        entries[agent_id] = {"started_at": started_at, "last_notified_at": last_notified_at}
        state[progress_self_check.STATE_KEY] = entries
        return state

    assert session_state.update_state(session_id, _mutate)


def _session_id() -> str:
    return f"self-check-{uuid.uuid4().hex}"


def test_first_observation_records_start_without_notice() -> None:
    sid = _session_id()
    assert progress_self_check.claim_notice({"session_id": sid}, now=1000.0) is None
    assert _entries(sid) == {"main": {"started_at": 1000.0, "last_notified_at": 1000.0}}


def test_notice_waits_for_interval_and_fires_once() -> None:
    sid = _session_id()
    payload = {"session_id": sid}
    assert progress_self_check.claim_notice(payload, now=1000.0) is None
    assert progress_self_check.claim_notice(payload, now=1000.0 + _INTERVAL - 1) is None
    assert _entries(sid)["main"]["last_notified_at"] == 1000.0

    notice = progress_self_check.claim_notice(payload, now=1000.0 + _INTERVAL)
    assert notice is not None and notice.startswith(_OPENING)
    assert _entries(sid)["main"] == {"started_at": 1000.0, "last_notified_at": 1000.0 + _INTERVAL}

    # 通知した直後は次の間隔が経過するまで再び通知しない。
    assert progress_self_check.claim_notice(payload, now=1000.0 + _INTERVAL + 60) is None
    assert progress_self_check.claim_notice(payload, now=1000.0 + 2 * _INTERVAL) is not None


@pytest.mark.parametrize(
    ("elapsed_seconds", "expected"),
    [(45 * 60, "45分"), (2 * 3600, "2時間"), (13 * 3600 + 5 * 60, "13時間5分")],
)
def test_notice_reports_elapsed_time_since_session_start(elapsed_seconds: int, expected: str) -> None:
    """経過時間は最後の通知ではなく、セッション開始を起点に数える。"""
    sid = _session_id()
    now = 100000.0
    _seed(sid, "main", started_at=now - elapsed_seconds, last_notified_at=now - _INTERVAL)
    notice = progress_self_check.claim_notice({"session_id": sid}, now=now)
    assert notice is not None and f"セッション開始から{expected}たった。" in notice


def test_agents_in_same_session_have_independent_timers() -> None:
    """in-processのサブエージェントは同じsession_idでも、メインと別の時刻で判定する。"""
    sid = _session_id()
    _seed(sid, "main", started_at=0.0, last_notified_at=0.0)
    now = float(_INTERVAL * 2)
    assert progress_self_check.claim_notice({"session_id": sid, "agent_id": "sub-1"}, now=now) is None
    assert progress_self_check.claim_notice({"session_id": sid}, now=now) is not None
    assert _entries(sid)["sub-1"] == {"started_at": now, "last_notified_at": now}


def test_notice_requests_stage_comparison_and_missing_information() -> None:
    """通知を受けた担当が活動だけで判断せず、工程比較と不足情報の取得へ進める。"""
    sid = _session_id()
    _seed(sid, "main", started_at=0.0, last_notified_at=0.0)
    notice = progress_self_check.claim_notice({"session_id": sid}, now=float(_INTERVAL))
    assert notice is not None
    for instruction in (
        "現在の工程と開始時点を手元の記録で確かめ",
        "経過時間を目標や同種処理の実績と比較せよ",
        "工程開始時点や比較基準が不足するなら、記録や担当者への照会で取得せよ",
        "前回以降の工程の変化と再試行",
        "`HEAD`や最終活動時刻の変化だけで妥当と判断するな",
        "活動の有無と工程の所要・反復の妥当性を分けて点検せよ",
        "時間を理由に作業を止めたり品質の確認を省いたりするな",
        "問題の有無にかかわらず、何をどう点検したかと結果を既存の報告契約に従って報告せよ",
        "経過時間と目標に対する進み具合、余計な作業の有無、当初目的との一致を含めよ",
    ):
        assert instruction in notice


@pytest.mark.parametrize("payload", [{}, {"session_id": ""}, {"session_id": 1}])
def test_payload_without_session_is_ignored(payload: dict) -> None:
    assert progress_self_check.claim_notice(payload) is None


def test_broken_entry_is_reinitialized_without_notice() -> None:
    sid = _session_id()
    assert session_state.update_state(sid, lambda state: {**state, progress_self_check.STATE_KEY: {"main": "broken"}})
    assert progress_self_check.claim_notice({"session_id": sid}, now=5000.0) is None
    assert _entries(sid)["main"] == {"started_at": 5000.0, "last_notified_at": 5000.0}


def _run_hook(subcommand: str, payload: dict) -> subprocess.CompletedProcess[str]:
    return _fork_runner.run_script(
        _SCRIPT, argv=(subcommand,), input=json.dumps(payload, ensure_ascii=False), env=os.environ.copy()
    )


def _additional_context(result: subprocess.CompletedProcess[str]) -> str:
    assert result.returncode == 0, result.stderr
    # フックは例外でも終了コード0を返すため、例外の出力が無いことも確かめる。
    assert "Traceback" not in result.stderr, result.stderr
    if not result.stdout:
        return ""
    return json.loads(result.stdout)["hookSpecificOutput"].get("additionalContext", "")


_POSTTOOLUSE_PAYLOAD = {
    "hook_event_name": "PostToolUse",
    "tool_name": "Read",
    "tool_input": {"file_path": "/nonexistent/progress-self-check.txt"},
    "tool_response": {},
}


@pytest.mark.parametrize(
    ("subcommand", "payload"),
    [
        ("posttooluse", _POSTTOOLUSE_PAYLOAD),
        (
            "user_prompt_submit",
            {"hook_event_name": "UserPromptSubmit", "prompt": f"{PERIODIC_RECHECK_MARKER}\n確認する。\n</atk-auto>"},
        ),
    ],
    ids=["posttooluse", "periodic-recheck"],
)
def test_hooks_attach_notice_only_after_interval(subcommand: str, payload: dict) -> None:
    sid = _session_id()
    first = _additional_context(_run_hook(subcommand, {**payload, "session_id": sid}))
    assert _OPENING not in first
    assert set(_entries(sid)) == {"main"}

    started_at = _entries(sid)["main"]["started_at"]
    _seed(sid, "main", started_at=started_at - 2 * _INTERVAL, last_notified_at=started_at - _INTERVAL)
    second = _additional_context(_run_hook(subcommand, {**payload, "session_id": sid}))
    assert second.count(_OPENING) == 1
    assert _entries(sid)["main"]["last_notified_at"] >= started_at

    third = _additional_context(_run_hook(subcommand, {**payload, "session_id": sid}))
    assert _OPENING not in third
