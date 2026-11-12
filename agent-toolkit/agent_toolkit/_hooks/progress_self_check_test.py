"""agent-toolkit/agent_toolkit/_hooks/progress_self_check.py のテスト。

通知の要否と状態の更新を判定関数で確かめ、PostToolUseとUserPromptSubmitの両フックが
判定の結果を`additionalContext`へ載せることをhook.pyのサブコマンド経由で確かめる。
"""

import json
import os
import pathlib
import re
import shlex
import subprocess
import uuid
from collections.abc import Mapping

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._common.periodic_recheck import PERIODIC_RECHECK_MARKER
from agent_toolkit._hooks import notice as notice_module
from agent_toolkit._hooks import posttooluse, progress_self_check
from agent_toolkit._testing import fork_runner as _fork_runner
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process

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
    # 通知が案内する読取コマンドは、実在する規範ファイルを指す。
    match = re.search(r"`(?:cat -- (?P<posix>[^`]+)|Get-Content -LiteralPath '(?P<nt>[^`]+?)' -Encoding utf8)`", notice)
    assert match is not None
    target = shlex.split(match["posix"])[0] if match["posix"] else match["nt"].replace("''", "'")
    assert pathlib.Path(target).is_file()
    # 通知が名指しする節は、そのファイルに見出しとして実在する。
    section = re.search(r"「(?P<name>[^」]+)」節", notice)
    assert section is not None
    assert f"\n## {section['name']}\n" in pathlib.Path(target).read_text(encoding="utf-8")
    # 生成しただけでは期日を消費せず、最終出力へ採用された時点で確定する。
    assert _entries(sid)["main"]["last_notified_at"] == 1000.0
    notice_module.record_delivery(notice)
    progress_self_check.release_reservations()

    # 通知した直後は次の間隔が経過するまで再び通知しない。
    assert progress_self_check.claim_notice(payload, now=1000.0 + _INTERVAL + 60) is None
    assert _entries(sid)["main"]["last_notified_at"] == 1000.0 + _INTERVAL
    assert progress_self_check.claim_notice(payload, now=1000.0 + 2 * _INTERVAL) is not None
    progress_self_check.release_reservations()


def test_undelivered_notice_keeps_the_due_time_for_the_next_call() -> None:
    """未採用の通知は期日を消費せず、予約の解除後の次の呼出しで通知する。"""
    sid = _session_id()
    _seed(sid, "main", started_at=1000.0, last_notified_at=1000.0)
    payload = {"session_id": sid}
    assert progress_self_check.claim_notice(payload, now=2800.0) is not None
    progress_self_check.release_reservations()
    assert _entries(sid)["main"]["last_notified_at"] == 1000.0
    retry = progress_self_check.claim_notice(payload, now=2801.0)
    assert retry is not None
    notice_module.record_delivery(retry)
    progress_self_check.release_reservations()
    assert progress_self_check.claim_notice(payload, now=2802.0) is None


def test_parallel_claim_of_the_same_subject_is_suppressed() -> None:
    """予約中の同じ主体は重ねて通知せず、別主体は独立して通知する。"""
    sid = _session_id()
    _seed(sid, "main", started_at=0.0, last_notified_at=0.0)
    _seed(sid, "sub-1", started_at=0.0, last_notified_at=0.0)
    first = progress_self_check.claim_notice({"session_id": sid}, now=float(_INTERVAL))
    assert first is not None
    assert progress_self_check.claim_notice({"session_id": sid}, now=float(_INTERVAL) + 1) is None
    assert progress_self_check.claim_notice({"session_id": sid, "agent_id": "sub-1"}, now=float(_INTERVAL) + 1) is not None
    # 予約は失効時間を過ぎれば外れ、配送されなかった期日を後続の呼出しが引き継ぐ。
    later = float(_INTERVAL) + progress_self_check.RESERVATION_SECONDS + 1
    assert progress_self_check.claim_notice({"session_id": sid}, now=later) is not None
    progress_self_check.release_reservations()


def test_older_delivery_receipt_does_not_rewind_the_confirmed_time() -> None:
    """古い試行の採用記録が後から届いても、新しい確定時刻を巻き戻さない。"""
    sid = _session_id()
    _seed(sid, "main", started_at=0.0, last_notified_at=0.0)
    old = progress_self_check.claim_notice({"session_id": sid}, now=float(_INTERVAL))
    progress_self_check.release_reservations()
    new = progress_self_check.claim_notice({"session_id": sid}, now=float(_INTERVAL) + 10)
    assert old is not None and new is not None
    notice_module.record_delivery(new)
    notice_module.restore_delivery_receipts(
        [
            {
                "text": old,
                "session_id": sid,
                "field": progress_self_check.DELIVERED_KEY,
                "key": "main",
                "value": repr(float(_INTERVAL)),
            }
        ]
    )
    notice_module.record_delivery(old)
    progress_self_check.release_reservations()
    assert progress_self_check.claim_notice({"session_id": sid}, now=float(_INTERVAL) + 20) is None
    assert _entries(sid)["main"]["last_notified_at"] == float(_INTERVAL) + 10


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

    third = _additional_context(_run_hook(subcommand, {**payload, "session_id": sid}))
    assert _OPENING not in third
    assert _entries(sid)["main"]["last_notified_at"] >= started_at


def _in_process(subcommand: str, payload: dict) -> str:
    result = _run_hook_in_process(subcommand, payload)
    assert result.returncode == 0, result.stderr
    if not result.stdout:
        return ""
    return json.loads(result.stdout)["hookSpecificOutput"].get("additionalContext", "")


def test_mixed_event_sequence_keeps_the_period_across_cycles(monkeypatch: pytest.MonkeyPatch) -> None:
    """実発話・機械注入・PostToolUseが30分未満の間隔で続いても、期日到達後の最初のイベントで周期どおり通知する。"""
    sid = _session_id()
    clock = {"now": 10_000.0}
    monkeypatch.setattr(progress_self_check.time, "time", lambda: clock["now"])
    monkeypatch.setattr(
        "agent_toolkit._hooks.user_prompt_submit._claim_user_confirmation_notice", lambda *_args, **_kwargs: False
    )
    events: list[tuple[str, Mapping[str, object]]] = [
        ("user_prompt_submit", {"hook_event_name": "UserPromptSubmit", "prompt": "進めて"}),
        ("posttooluse", _POSTTOOLUSE_PAYLOAD),
        (
            "user_prompt_submit",
            {"hook_event_name": "UserPromptSubmit", "prompt": f"{PERIODIC_RECHECK_MARKER}\n確認する。\n</atk-auto>"},
        ),
    ]
    notified: list[float] = []
    step = 0
    while clock["now"] <= 10_000.0 + 3 * _INTERVAL + 600:
        subcommand, payload = events[step % len(events)]
        if _OPENING in _in_process(subcommand, {**payload, "session_id": sid}):
            notified.append(clock["now"])
        step += 1
        clock["now"] += 10 * 60
    # 初回観測は通知せず、1800秒以上たった最初のイベントごとに1回だけ通知し、期日の延期は起きない。
    assert notified == [10_000.0 + 1800.0, 10_000.0 + 3600.0, 10_000.0 + 5400.0]


def test_format_failure_after_claim_does_not_consume_the_due_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """公開PostToolUseで通知の整形が失敗しても予約が残らず、直後の正常な呼出しで通知が採用される。"""
    sid = _session_id()
    _seed(sid, "main", started_at=1000.0, last_notified_at=1000.0)
    clock = {"now": 2800.0}
    monkeypatch.setattr(progress_self_check.time, "time", lambda: clock["now"])

    def broken(*_args, **_kwargs) -> str:
        raise ValueError("通知の整形に失敗した")

    with monkeypatch.context() as patch:
        patch.setattr(progress_self_check, "_llm_notice", broken)
        failed = _in_process("posttooluse", {**_POSTTOOLUSE_PAYLOAD, "session_id": sid})
    assert _OPENING not in failed
    assert "reserved_at" not in _entries(sid)["main"]
    clock["now"] = 2801.0
    assert _OPENING in _in_process("posttooluse", {**_POSTTOOLUSE_PAYLOAD, "session_id": sid})


def test_output_failure_after_claim_does_not_consume_the_due_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """公開PostToolUseで通知の生成後に出力が失敗しても、障害を外した直後の再試行で通知が採用される。"""
    sid = _session_id()
    _seed(sid, "main", started_at=1000.0, last_notified_at=1000.0)
    clock = {"now": 2800.0}
    monkeypatch.setattr(progress_self_check.time, "time", lambda: clock["now"])

    def broken(_response: dict) -> None:
        raise OSError("応答の書き出しに失敗した")

    with monkeypatch.context() as patch:
        patch.setattr(posttooluse, "emit_response", broken)
        failed = _in_process("posttooluse", {**_POSTTOOLUSE_PAYLOAD, "session_id": sid})
    assert _OPENING not in failed
    assert _entries(sid)["main"]["last_notified_at"] == 1000.0
    clock["now"] = 2801.0
    assert _OPENING in _in_process("posttooluse", {**_POSTTOOLUSE_PAYLOAD, "session_id": sid})
    clock["now"] = 2802.0
    assert _OPENING not in _in_process("posttooluse", {**_POSTTOOLUSE_PAYLOAD, "session_id": sid})
