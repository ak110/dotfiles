"""agent-toolkit/agent_toolkit/_hooks/progress_self_check.py のテスト。

通知の要否と状態の更新を判定関数で確かめ、PostToolUseとUserPromptSubmitの両フックが
判定の結果を`additionalContext`へ載せることをhook.pyのサブコマンド経由で確かめる。
"""

import json
import os
import pathlib
import subprocess
import time
import uuid
from collections.abc import Mapping

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._common.periodic_recheck import PERIODIC_RECHECK_MARKER
from agent_toolkit._hooks import notice as notice_module
from agent_toolkit._hooks import posttooluse, progress_self_check
from agent_toolkit._hooks.pretooluse import operation_skills
from agent_toolkit._testing import fork_runner as _fork_runner
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "hook.py"
_INTERVAL = progress_self_check.SELF_CHECK_INTERVAL_SECONDS
_OPENING = f'<atk-auto source="{progress_self_check.SOURCE}" kind="notice">'


@pytest.fixture(autouse=True)
def _without_unrelated_document_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    """通知の採用・予約・再試行を通し、対象外の本文取得の観測を隔離する。"""
    monkeypatch.setattr(operation_skills, "notices", lambda *_args, **_kwargs: [])


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


def test_periodic_recheck_absorbs_one_minute_of_clock_jitter() -> None:
    """同じ経過時間で通常の発話は待ち、既存の定期発火だけが期日へ達する。"""
    sid = _session_id()
    payload = {"session_id": sid}
    assert progress_self_check.claim_notice(payload, now=1000.0) is None
    early = 1000.0 + _INTERVAL - 1
    assert progress_self_check.claim_notice(payload, now=early) is None
    assert progress_self_check.claim_notice(payload, now=early, periodic_recheck=True) is not None
    progress_self_check.release_reservations()


def test_notice_waits_for_interval_and_fires_once() -> None:
    sid = _session_id()
    payload = {"session_id": sid}
    assert progress_self_check.claim_notice(payload, now=1000.0) is None
    assert progress_self_check.claim_notice(payload, now=1000.0 + _INTERVAL - 1) is None
    assert _entries(sid)["main"]["last_notified_at"] == 1000.0

    notice = progress_self_check.claim_notice(payload, now=1000.0 + _INTERVAL)
    assert notice is not None and notice.startswith(_OPENING)
    # 通知だけで報告の1行目を埋められる。
    assert "1行目" in notice
    assert "進捗 N%・完了見込み HH:MM（根拠: …）" in notice
    assert "git -C" not in notice
    assert ".md" not in notice
    assert "cat --" not in notice and "Get-Content" not in notice
    # 生成しただけでは期日を消費せず、最終出力へ採用された時点で確定する。
    assert _entries(sid)["main"]["last_notified_at"] == 1000.0
    notice_module.record_delivery(notice)
    progress_self_check.release_reservations()

    # 通知した直後は次の間隔が経過するまで再び通知しない。
    assert progress_self_check.claim_notice(payload, now=1000.0 + _INTERVAL + 60) is None
    assert _entries(sid)["main"]["last_notified_at"] == 1000.0 + _INTERVAL
    assert progress_self_check.claim_notice(payload, now=1000.0 + 2 * _INTERVAL) is not None
    progress_self_check.release_reservations()


_VALID_REPORT = "進捗 50%・完了見込み 18:30（根拠: 検証があと30分）\n関連試験は完了した。"
_SEND_TOOL = "mcp__plugin_agent-toolkit_agents_server__send_to_user"


def _due_notice(sid: str, *, agent: str | None = None) -> str:
    now = time.time()
    payload = {"session_id": sid, **({"agent_id": agent} if agent else {})}
    _seed(sid, agent or "main", started_at=now - 2 * _INTERVAL, last_notified_at=now - _INTERVAL - 1)
    generated = progress_self_check.claim_notice(payload, now=now)
    assert generated is not None
    return generated


def _report_hook(subcommand: str, payload: dict) -> dict:
    result = _run_hook_in_process(subcommand, payload)
    assert result.returncode in (0, 2), result.stderr
    return json.loads(result.stdout) if result.stdout else {}


@pytest.mark.parametrize("tool", [_SEND_TOOL, "mcp__agent-toolkit__send_to_user"])
def test_send_gate_waits_for_delivered_notice_and_confirmed_report(tool: str) -> None:
    sid = _session_id()
    generated = _due_notice(sid)
    common = {"session_id": sid, "tool_name": tool}
    wrong_message = "点検した。"
    wrong = {**common, "tool_input": {"message": wrong_message}}
    correct = {**common, "tool_input": {"message": _VALID_REPORT}}

    assert progress_self_check.send_block(wrong, wrong_message) is None
    notice_module.record_delivery(generated)
    progress_self_check.release_reservations()

    blocked = _report_hook("pretooluse", {**wrong, "hook_event_name": "PreToolUse"})["hookSpecificOutput"]
    assert blocked["permissionDecision"] == "deny"
    assert "完了見込み" in blocked["permissionDecisionReason"]
    accepted = _report_hook("pretooluse", {**correct, "hook_event_name": "PreToolUse"})
    assert accepted.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"

    _report_hook("posttooluse", {**correct, "hook_event_name": "PostToolUse", "tool_response": {"ok": False}})
    assert progress_self_check.send_block(wrong, wrong_message) is not None
    other_tool = "mcp__other__send_to_user"
    allowed = _report_hook("pretooluse", {**wrong, "tool_name": other_tool, "hook_event_name": "PreToolUse"})
    assert allowed.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"
    _report_hook(
        "posttooluse",
        {**correct, "tool_name": other_tool, "hook_event_name": "PostToolUse", "tool_response": {"ok": True}},
    )
    assert progress_self_check.send_block(wrong, wrong_message) is not None
    _report_hook(
        "posttooluse", {**correct, "hook_event_name": "PostToolUse", "tool_response": {"structuredContent": {"ok": True}}}
    )
    assert progress_self_check.send_block(wrong, wrong_message) is None


@pytest.mark.parametrize("host_fields", [{}, {"turn_id": "test-turn"}], ids=["claude", "codex"])
def test_stop_does_not_check_the_progress_report_format(host_fields: dict) -> None:
    sid = _session_id()
    notice_module.record_delivery(_due_notice(sid))
    progress_self_check.release_reservations()
    payload = {"session_id": sid, "hook_event_name": "Stop", **host_fields}
    result = _report_hook("stop", {**payload, "last_assistant_message": "点検を終えた。"})
    assert result.get("decision") != "block"
    assert progress_self_check.send_block(payload, "次の進捗。") is not None


def test_in_process_subagent_notice_does_not_require_user_report() -> None:
    sid = _session_id()
    generated = _due_notice(sid, agent="subagent-1")
    assert "役割文書" in generated and "次のユーザー向け報告" not in generated
    notice_module.record_delivery(generated)
    progress_self_check.release_reservations()
    assert progress_self_check.send_block({"session_id": sid}, "通常の応答") is None


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
    assert notice is not None and f"この主体の初回観測から{expected}たった。" in notice
    assert "前回の通知から30分たった" in notice
    assert "+00:00" not in notice


def test_agents_in_same_session_have_independent_timers() -> None:
    """in-processのサブエージェントは同じsession_idでも、メインと別の時刻で判定する。"""
    sid = _session_id()
    _seed(sid, "main", started_at=0.0, last_notified_at=0.0)
    now = float(_INTERVAL * 2)
    assert progress_self_check.claim_notice({"session_id": sid, "agent_id": "sub-1"}, now=now) is None
    assert progress_self_check.claim_notice({"session_id": sid}, now=now) is not None
    assert _entries(sid)["sub-1"] == {"started_at": now, "last_notified_at": now}


@pytest.mark.parametrize("now", [86399.0, 86400.0])
def test_notice_reports_elapsed_time_across_midnight(now: float) -> None:
    """日付をまたいでも、初回通知は初回観測からの経過だけを示す。"""
    sid = _session_id()
    _seed(sid, "main", started_at=now - _INTERVAL, last_notified_at=now - _INTERVAL)
    notice = progress_self_check.claim_notice({"session_id": sid}, now=now)
    assert notice is not None and "初回観測から30分" in notice
    assert "前回の通知から" not in notice and "+00:00" not in notice
    progress_self_check.release_reservations()


def test_notice_does_not_report_negative_elapsed_after_clock_disagreement() -> None:
    """起点と時計が食い違っても、負の経過時間を通常の経過として示さない。"""
    sid = _session_id()
    _seed(sid, "main", started_at=100000.0, last_notified_at=1000.0)
    notice = progress_self_check.claim_notice({"session_id": sid}, now=2800.0)
    assert notice is not None and "経過時間は不明" in notice
    assert "-1620分" not in notice
    assert _entries(sid)["main"]["started_at"] == 100000.0
    progress_self_check.release_reservations()


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


@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "模擬時刻の11イベントを公開User/Postへ順に渡し、3周期の通知と周期保持を確認する（Linux限定再確認のCPU"
        "合計最大0.848秒）。"
    ),
)
def test_mixed_event_sequence_keeps_the_period_across_cycles(monkeypatch: pytest.MonkeyPatch) -> None:
    """実発話・機械注入・PostToolUseが30分未満の間隔で続いても、期日到達後の最初のイベントで周期どおり通知する。"""
    sid = _session_id()
    clock = {"now": 10_000.0}
    monkeypatch.setattr(progress_self_check.time, "time", lambda: clock["now"])
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


def test_two_periodic_prompts_notify_just_before_the_normal_due_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """標識のある入力だけが、続く2周期で通常の期日より前に通知する。"""
    sid = _session_id()
    clock = {"now": 10_000.0}
    monkeypatch.setattr(progress_self_check.time, "time", lambda: clock["now"])
    _seed(sid, "main", started_at=clock["now"], last_notified_at=clock["now"])
    periodic = {"session_id": sid, "prompt": f"{PERIODIC_RECHECK_MARKER}\n確認する。\n</atk-auto>"}
    for _ in range(2):
        clock["now"] += _INTERVAL - 1
        assert _OPENING not in _in_process("user_prompt_submit", {"session_id": sid, "prompt": "状況は？"})
        assert _OPENING not in _in_process("posttooluse", {**_POSTTOOLUSE_PAYLOAD, "session_id": sid})
        assert _in_process("user_prompt_submit", periodic).count(_OPENING) == 1
        assert _entries(sid)["main"]["last_notified_at"] == clock["now"]


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


def test_notice_reports_machine_concurrency_and_recent_hook_durations(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """点検の通知に、マシン全体の作業中の数と直近のhook所要が現れ、飽和を進め方と分けて判定できる。"""
    from agent_toolkit._agents_server import admission  # pylint: disable=import-outside-toplevel
    from agent_toolkit._atk import managed_temp  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(admission, "observe", lambda *_a, **_k: admission.Observation(working=13, admitted=0, waiting=2))
    monkeypatch.setattr(admission, "configured_limit", lambda: admission.Limit(15, "test"))
    (tmp_path / "hook-timings.jsonl").write_text(
        "\n".join(json.dumps({"stage": "total", "seconds": value}) for value in (2.0, 4.0, 12.0)) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(managed_temp, "find_session_temp", lambda _sid: tmp_path)
    sid = f"progress-saturation-{uuid.uuid4()}"
    assert progress_self_check.claim_notice({"session_id": sid}, now=1000.0) is None
    notice = progress_self_check.claim_notice({"session_id": sid}, now=1000.0 + _INTERVAL)
    assert notice is not None
    assert "13" in notice and "上限15" in notice and "起動待ち2" in notice
    assert "平均6.0秒" in notice and "最大12.0秒" in notice


@pytest.mark.parametrize("case", ["partial", "observe-error", "unreadable-hooks", "no-hooks"])
def test_notice_distinguishes_partial_failed_and_absent_observations(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """部分観測・取得失敗・記録不在を区別し、取得失敗には次に確認する操作を示す。"""
    from agent_toolkit._agents_server import admission  # pylint: disable=import-outside-toplevel
    from agent_toolkit._atk import managed_temp  # pylint: disable=import-outside-toplevel

    def observe(*_a: object, **_k: object) -> admission.Observation:
        if case == "observe-error":
            raise PermissionError("denied")
        return admission.Observation(
            working=3, admitted=0, waiting=0, failures=("root/w.json: ValueError",) if case == "partial" else ()
        )

    monkeypatch.setattr(admission, "observe", observe)
    monkeypatch.setattr(admission, "configured_limit", lambda: admission.Limit(15, "test"))
    if case == "unreadable-hooks":
        (tmp_path / "hook-timings.jsonl").write_text("{broken\n", encoding="utf-8")
    monkeypatch.setattr(managed_temp, "find_session_temp", lambda _sid: tmp_path)
    sid = f"progress-observation-{case}-{uuid.uuid4()}"
    assert progress_self_check.claim_notice({"session_id": sid}, now=1000.0) is None
    notice = progress_self_check.claim_notice({"session_id": sid}, now=1000.0 + _INTERVAL)
    assert notice is not None
    expected = {
        "partial": "部分観測",
        "observe-error": "取得できない（PermissionError: denied）",
        "unreadable-hooks": "hook所要の記録を読めない",
        "no-hooks": "直近のhook所要の記録は無い",
    }[case]
    assert expected in notice
    if case == "partial":
        # 一覧コマンドでは見えない失敗対象の所在と診断を、通知から直接たどれる。
        assert "root/w.json: ValueError" in notice and "agents-server" in notice
    if case == "observe-error":
        assert "denied" in notice and "`atk config get state_dir`" in notice


def test_notice_keeps_all_unreadable_records_reachable(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """読めない状態ファイルが4件以上でも、通知が示す保存先から全件の所在と診断を取得できる。"""
    from agent_toolkit._agents_server import admission  # pylint: disable=import-outside-toplevel
    from agent_toolkit._atk import managed_temp  # pylint: disable=import-outside-toplevel

    failures = tuple(f"root{index}/w.json: ValueError" for index in range(4))
    monkeypatch.setattr(
        admission, "observe", lambda *_a, **_k: admission.Observation(working=1, admitted=0, waiting=0, failures=failures)
    )
    monkeypatch.setattr(admission, "configured_limit", lambda: admission.Limit(15, "test"))
    monkeypatch.setattr(managed_temp, "find_session_temp", lambda _sid: tmp_path)
    sid = f"progress-many-failures-{uuid.uuid4()}"
    assert progress_self_check.claim_notice({"session_id": sid}, now=1000.0) is None
    notice = progress_self_check.claim_notice({"session_id": sid}, now=1000.0 + _INTERVAL)
    assert notice is not None and "他1件" in notice
    report = tmp_path / "agents-concurrency-failures.txt"
    assert str(report) in notice
    assert all(failure in report.read_text(encoding="utf-8") for failure in failures)
