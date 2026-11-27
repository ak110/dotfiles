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

from agent_toolkit._atk import lane_cleanup
from agent_toolkit._atk.wi.formatters import NewWorkItem
from agent_toolkit._common import session_state
from agent_toolkit._common.delegated_session import OWNER_SESSION_ENV
from agent_toolkit._common.periodic_recheck import PERIODIC_RECHECK_MARKER
from agent_toolkit._hooks import notice as notice_module
from agent_toolkit._hooks import progress_self_check
from agent_toolkit._testing import fork_runner as _fork_runner
from agent_toolkit._testing.output_policy_support import assert_output_policy
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


def test_periodic_recheck_absorbs_one_minute_of_clock_jitter() -> None:
    sid = _session_id()
    payload = {"session_id": sid}
    assert progress_self_check.claim_notice(payload, now=1000.0) is None
    early = 1000.0 + _INTERVAL - 1
    assert progress_self_check.claim_notice(payload, now=early) is None
    assert progress_self_check.claim_notice(payload, now=early, periodic_recheck=True) is not None


@pytest.mark.parametrize("mode", ["mixed", "empty", "failure", "delegated"])
def test_orphan_candidates_are_read_only_when_main_notice_is_due(
    mode: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(OWNER_SESSION_ENV, raising=False)
    if mode == "delegated":
        monkeypatch.setenv(OWNER_SESSION_ENV, "test-owner")
    monkeypatch.setattr(lane_cleanup, "repository_at", lambda _path: tmp_path)
    scanned = []

    def scan(_repo, _registered, **_kwargs):
        scanned.append(mode)
        if mode == "failure":
            raise ValueError("Gitの一覧エラー")
        if mode == "mixed":
            return [
                {"worktree": "統合済み", "removable": True, "reason": None, "age_seconds": 30},
                {"worktree": "古い変更", "removable": False, "reason": "未追跡あり", "age_seconds": 86401},
                {"worktree": "最近の変更", "removable": False, "reason": "未追跡あり", "age_seconds": 86400},
            ]
        return []

    monkeypatch.setattr(lane_cleanup, "classify_orphans", scan)
    payload = {"session_id": _session_id(), "cwd": str(tmp_path)}
    assert progress_self_check.claim_notice(payload, now=1000.0) is None
    assert not scanned
    notice = progress_self_check.claim_notice(payload, now=1000.0 + _INTERVAL)
    assert notice and "完成条件の根拠と残工程" in notice
    assert scanned == ([] if mode == "delegated" else [mode])
    if mode == "mixed":
        assert "回収可能な登録外worktree 1件: 統合済み" in notice
        assert "保持候補 1件: 古い変更（未追跡あり）" in notice
        assert "最近の変更" not in notice
    elif mode == "failure":
        assert "登録外worktreeの一覧を取得できない" in notice and "Gitの一覧エラー" in notice
        assert "atk lane delete --list --repo <統合先worktree>" in notice
    else:
        assert "登録外worktree" not in notice


def test_notice_waits_for_interval_and_fires_once() -> None:
    sid = _session_id()
    payload = {"session_id": sid}
    assert progress_self_check.claim_notice(payload, now=1000.0) is None
    assert progress_self_check.claim_notice(payload, now=1000.0 + _INTERVAL - 1) is None
    assert _entries(sid)["main"]["last_notified_at"] == 1000.0

    notice = progress_self_check.claim_notice(payload, now=1000.0 + _INTERVAL)
    assert notice is not None and notice.startswith(_OPENING)

    assert "完成条件の根拠と残工程" in notice
    assert "完了見込み" not in notice
    assert "git -C" not in notice
    assert "`main-reporting.md`「進捗点検」" in notice
    assert "cat --" not in notice and "Get-Content" not in notice

    assert _entries(sid)["main"]["last_notified_at"] == 1000.0 + _INTERVAL

    assert progress_self_check.claim_notice(payload, now=1000.0 + _INTERVAL + 60) is None
    assert _entries(sid)["main"]["last_notified_at"] == 1000.0 + _INTERVAL
    assert progress_self_check.claim_notice(payload, now=1000.0 + 2 * _INTERVAL) is not None


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
def test_received_check_notice_does_not_block_a_report_without_time_estimate(tool: str) -> None:
    sid = _session_id()
    notice_module.record_delivery(_due_notice(sid))
    payload = {
        "session_id": sid,
        "tool_name": tool,
        "hook_event_name": "PreToolUse",
        "tool_input": {"message": "対象試験が終了0。公開後のCIは未確認。"},
    }

    response = _report_hook("pretooluse", payload)
    assert response.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


def test_in_process_subagent_notice_does_not_require_user_report() -> None:
    generated = _due_notice(_session_id(), agent="subagent-1")
    assert "既存の引き継ぎ記録" in generated
    assert "完了見込み" not in generated


def test_parallel_claim_of_the_same_subject_is_suppressed() -> None:
    sid = _session_id()
    _seed(sid, "main", started_at=0.0, last_notified_at=0.0)
    _seed(sid, "sub-1", started_at=0.0, last_notified_at=0.0)
    first = progress_self_check.claim_notice({"session_id": sid}, now=float(_INTERVAL))
    assert first is not None
    assert progress_self_check.claim_notice({"session_id": sid}, now=float(_INTERVAL) + 1) is None
    assert progress_self_check.claim_notice({"session_id": sid, "agent_id": "sub-1"}, now=float(_INTERVAL) + 1) is not None

    later = float(2 * _INTERVAL)
    assert progress_self_check.claim_notice({"session_id": sid}, now=later) is not None


@pytest.mark.parametrize(
    ("elapsed_seconds", "expected"),
    [(45 * 60, "45分"), (2 * 3600, "2時間"), (13 * 3600 + 5 * 60, "13時間5分")],
)
def test_notice_reports_elapsed_time_since_session_start(elapsed_seconds: int, expected: str) -> None:
    sid = _session_id()
    now = 100000.0
    _seed(sid, "main", started_at=now - elapsed_seconds, last_notified_at=now - _INTERVAL)
    notice = progress_self_check.claim_notice({"session_id": sid}, now=now)
    assert notice is not None and f"この主体の初回観測から{expected}たった。" in notice
    assert "前回の通知から30分たった" in notice
    assert "+00:00" not in notice


def test_agents_in_same_session_have_independent_timers() -> None:
    sid = _session_id()
    _seed(sid, "main", started_at=0.0, last_notified_at=0.0)
    now = float(_INTERVAL * 2)
    assert progress_self_check.claim_notice({"session_id": sid, "agent_id": "sub-1"}, now=now) is None
    assert progress_self_check.claim_notice({"session_id": sid}, now=now) is not None
    assert _entries(sid)["sub-1"] == {"started_at": now, "last_notified_at": now}


@pytest.mark.parametrize("now", [86399.0, 86400.0])
def test_notice_reports_elapsed_time_across_midnight(now: float) -> None:
    sid = _session_id()
    _seed(sid, "main", started_at=now - _INTERVAL, last_notified_at=now - _INTERVAL)
    notice = progress_self_check.claim_notice({"session_id": sid}, now=now)
    assert notice is not None and "初回観測から30分" in notice
    assert "前回の通知から" not in notice and "+00:00" not in notice


def test_notice_does_not_report_negative_elapsed_after_clock_disagreement() -> None:
    sid = _session_id()
    _seed(sid, "main", started_at=100000.0, last_notified_at=1000.0)
    notice = progress_self_check.claim_notice({"session_id": sid}, now=2800.0)
    assert notice is not None and "経過時間は不明" in notice
    assert "-1620分" not in notice
    assert _entries(sid)["main"]["started_at"] == 100000.0


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

    assert notified == [10_000.0 + 1800.0, 10_000.0 + 3600.0, 10_000.0 + 5400.0]


def test_two_periodic_prompts_notify_just_before_the_normal_due_time(monkeypatch: pytest.MonkeyPatch) -> None:
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


@pytest.mark.parametrize("failed", [None, OSError, SystemExit])
def test_process_loop_new_wis_reports_unconfirmed(monkeypatch, failed):
    from agent_toolkit._atk.wi import listing  # pylint: disable=import-outside-toplevel

    sid = _session_id()
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", sid)
    if failed:

        def unable(_sid):
            raise failed("取得不能")

        monkeypatch.setattr(listing, "progress_new_wis", unable)
    notice = _due_notice(sid)
    assert "新着WIは未確認" in notice
    assert "0件" not in notice


def test_process_loop_point_lists_merge_candidate(monkeypatch):
    from agent_toolkit._atk.wi import listing  # pylint: disable=import-outside-toplevel

    sid = _session_id()
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", sid)
    name = "20261123-145959-001.md"
    monkeypatch.setattr(listing, "progress_new_wis", lambda _sid, **_kwargs: (NewWorkItem(name, "implement", True),))

    payload = {"session_id": sid}
    assert progress_self_check.claim_notice(payload, now=1.0) is None
    assert progress_self_check.claim_notice(payload, now=1800.0) is None
    notice = progress_self_check.claim_notice(payload, now=1801.0)
    assert notice and f"{name} (source: implement、merge_branch: あり)" in notice


def test_new_wi_preview_counts_all_but_repeats_only_undelivered_ids() -> None:
    sid = _session_id()
    old = "20261123-145959-001.md"
    new = "20261123-150000-001.md"
    before = (NewWorkItem(old, "implement", False),)
    text, snapshot = notice_module.new_wi_preview(sid, "main", before)
    notice_module.stage_new_wi_preview(text, sid, "main", snapshot)
    assert old in text and "merge_branch" not in text
    assert old in notice_module.new_wi_preview(sid, "main", before)[0]
    assert_output_policy("wi_progress_first", text)
    notice_module.record_delivery(text)
    assert_output_policy("wi_progress_repeat", notice_module.new_wi_preview(sid, "main", before)[0])

    after = (*before, NewWorkItem(new, "implement", True))
    text, _ = notice_module.new_wi_preview(sid, "main", after)
    assert old not in text and new in text and "計2件" in text and "merge_branch: あり" in text
    assert old in notice_module.new_wi_preview(sid, "other", after)[0]


def test_scoped_notices_merge_at_delivery_even_if_built_before_either_was_sent() -> None:
    sid = _session_id()
    left = NewWorkItem("20261123-152201-001.md", "implement", False)
    right = NewWorkItem("20261123-152201-002.md", "implement", False)
    extra = NewWorkItem("20261123-152201-003.md", "implement", False)

    def stage(row: NewWorkItem) -> str:
        text, snapshot = notice_module.new_wi_preview(sid, "main", [row])
        assert row.filename in text
        notice_module.stage_new_wi_preview(text, sid, "main", snapshot, scoped=True)
        return text

    left_text = stage(left)
    right_text = stage(right)
    stage(extra)
    notice_module.record_delivery(left_text)
    notice_module.record_delivery(right_text)
    text, _ = notice_module.new_wi_preview(sid, "main", [left, right, extra])
    assert left.filename not in text and right.filename not in text and extra.filename in text
    text, snapshot = notice_module.new_wi_preview(sid, "main", [right])
    notice_module.stage_new_wi_preview(text, sid, "main", snapshot)
    notice_module.record_delivery(text)
    assert left.filename in notice_module.new_wi_preview(sid, "main", [left])[0]
