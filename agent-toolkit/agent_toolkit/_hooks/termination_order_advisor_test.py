"""agent-toolkit/agent_toolkit/_hooks/termination_order_advisor.py のテスト。

多段終了手順の不足・順序違反の検出と、非対象・委譲先・記録読取不能・非同期作業継続中の
各非遮断条件を検証する。
"""

import contextlib
import io
import json
import pathlib

import pytest

from agent_toolkit._hooks import stop_gate as _stop_gate
from agent_toolkit._hooks import termination_evidence, termination_order_advisor, user_prompt_submit
from agent_toolkit._testing.helpers import _write_transcript


def _set_state_directory(monkeypatch: pytest.MonkeyPatch, directory: pathlib.Path) -> None:
    """常時ログの出力先を固定する。"""
    monkeypatch.setenv("TMPDIR", str(directory))
    monkeypatch.setenv("TEMP", str(directory))
    monkeypatch.setenv("TMP", str(directory))


def _skill_entry(skill: str, *, tool_use_id: str = "toolu_skill") -> dict:
    """`Skill`の呼び出しを含むアシスタントエントリを生成する。"""
    return {
        "type": "assistant",
        "message": {
            "id": "msg_skill",
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tool_use_id, "name": "Skill", "input": {"skill": skill}}],
            "stop_reason": "end_turn",
        },
    }


def _tool_result_entry(tool_use_id: str, *, is_error: bool = False) -> dict:
    """ツール結果を含むユーザーエントリを生成する。"""
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": "result",
                    "is_error": is_error,
                }
            ],
        },
    }


def _exit_entry(*, tool_use_id: str = "toolu_exit", command: str = "atk agents-exit-session") -> dict:
    """終了CLIのBash起動を含むアシスタントエントリを生成する。"""
    return {
        "type": "assistant",
        "message": {
            "id": "msg_exit",
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_use_id,
                    "name": "Bash",
                    "input": {"command": command},
                }
            ],
            "stop_reason": "end_turn",
        },
    }


def _async_wait_entry(*, tool_use_id: str = "toolu_async") -> dict:
    """非同期待機系ツール（Agent）の起動を含むアシスタントエントリを生成する。"""
    return {
        "type": "assistant",
        "message": {
            "id": "msg_async",
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tool_use_id, "name": "Agent", "input": {}}],
            "stop_reason": "end_turn",
        },
    }


def _payload(session_id: str, transcript_path: str, *, stop_hook_active: bool = True, agent_id: str | None = None) -> str:
    payload = {
        "session_id": session_id,
        "transcript_path": transcript_path,
        "stop_hook_active": stop_hook_active,
        "background_tasks": [],
    }
    if agent_id is not None:
        payload["agent_id"] = agent_id
    return json.dumps(payload)


def _clear_caches() -> None:
    _stop_gate._PENDING_ASYNC_WORK_CACHE.clear()  # pylint: disable=protected-access
    _stop_gate._TRANSCRIPT_ENTRIES_CACHE.clear()  # pylint: disable=protected-access


def test_approves_when_not_reentrant(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """`stop_hook_active`が偽の場合は、終了手順が不足していても遮断しない。"""
    _set_state_directory(monkeypatch, tmp_path)
    _clear_caches()
    transcript = _write_transcript(
        tmp_path,
        [_skill_entry("agent-toolkit:process-wi"), _tool_result_entry("toolu_skill")],
    )

    decision, body = termination_order_advisor.evaluate(
        _payload("sess-not-reentrant", str(transcript), stop_hook_active=False),
    )

    assert (decision, body) == ("approve", "")


def test_blocks_when_termination_skill_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """対象スキル起動後に終了工程が1つも起動されていない場合は遮断する。"""
    _set_state_directory(monkeypatch, tmp_path)
    _clear_caches()
    transcript = _write_transcript(
        tmp_path,
        [_skill_entry("agent-toolkit:process-wi"), _tool_result_entry("toolu_skill")],
    )

    decision, body = termination_order_advisor.evaluate(_payload("sess-missing", str(transcript)))

    assert decision == "block"
    assert "agent-toolkit:process-wi" in body
    assert "agent-toolkit:completion-report" in body
    assert "atk agents-exit-session" in body


def test_blocks_when_termination_order_reversed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """終了工程の起動順が逆の場合も未充足として遮断する。"""
    _set_state_directory(monkeypatch, tmp_path)
    _clear_caches()
    transcript = _write_transcript(
        tmp_path,
        [
            _skill_entry("agent-toolkit:process-wi", tool_use_id="toolu_1"),
            _tool_result_entry("toolu_1"),
            _exit_entry(tool_use_id="toolu_2"),
            _skill_entry("agent-toolkit:completion-report", tool_use_id="toolu_3"),
            _tool_result_entry("toolu_3"),
        ],
    )

    decision, body = termination_order_advisor.evaluate(_payload("sess-reversed", str(transcript)))

    assert decision == "block"
    assert "atk agents-exit-session" in body


@pytest.mark.parametrize(
    ("command", "expected_decision"),
    [
        ("atk agents-exit-session", "approve"),
        ("atk agents-exit-session 2>&1", "approve"),
        ("atk agents-exit-session --x", "block"),
    ],
)
def test_termination_order_satisfied_by_exit_invocation_without_arguments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    command: str,
    expected_decision: str,
) -> None:
    """対象スキルの最新起動以後に終了工程が要求順で起動済みなら許可する。

    終了CLIはリダイレクトだけを伴う起動も終了工程として数え、引数を伴う起動は数えない。
    """
    _set_state_directory(monkeypatch, tmp_path)
    _clear_caches()
    transcript = _write_transcript(
        tmp_path,
        [
            _skill_entry("agent-toolkit:process-wi", tool_use_id="toolu_1"),
            _tool_result_entry("toolu_1"),
            _skill_entry("agent-toolkit:completion-report", tool_use_id="toolu_2"),
            _tool_result_entry("toolu_2"),
            _exit_entry(tool_use_id="toolu_3", command=command),
        ],
    )

    decision, body = termination_order_advisor.evaluate(_payload("sess-satisfied", str(transcript)))

    assert decision == expected_decision
    # 遮断時だけ、残りの工程として終了CLIを本文に示す。
    assert ("atk agents-exit-session" in body) is (expected_decision == "block")


def test_blocks_when_second_invocation_lacks_new_termination(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """1回目の充足を2回目の対象スキル起動の充足へ流用しない。"""
    _set_state_directory(monkeypatch, tmp_path)
    _clear_caches()
    transcript = _write_transcript(
        tmp_path,
        [
            _skill_entry("agent-toolkit:process-wi", tool_use_id="toolu_1"),
            _tool_result_entry("toolu_1"),
            _skill_entry("agent-toolkit:completion-report", tool_use_id="toolu_2"),
            _tool_result_entry("toolu_2"),
            _exit_entry(tool_use_id="toolu_3"),
            _skill_entry("agent-toolkit:process-wi", tool_use_id="toolu_4"),
            _tool_result_entry("toolu_4"),
        ],
    )

    decision, body = termination_order_advisor.evaluate(_payload("sess-second", str(transcript)))

    assert decision == "block"
    assert "agent-toolkit:completion-report" in body


def test_approves_when_target_skill_never_invoked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """対象スキルを一度も起動していないセッションでは起動順を判定しない。"""
    _set_state_directory(monkeypatch, tmp_path)
    _clear_caches()
    transcript = _write_transcript(
        tmp_path,
        [_skill_entry("agent-toolkit:writing-standards"), _tool_result_entry("toolu_skill")],
    )

    decision, body = termination_order_advisor.evaluate(_payload("sess-unrelated", str(transcript)))

    assert (decision, body) == ("approve", "")


def test_approves_delegated_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """`AGENT_TOOLKIT_DELEGATED_SESSION`が`1`の委譲先では終了手順の起動順を確かめない。"""
    _set_state_directory(monkeypatch, tmp_path)
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    _clear_caches()
    transcript = _write_transcript(tmp_path, [_skill_entry("agent-toolkit:process-wi")])

    decision, body = termination_order_advisor.evaluate(_payload("sess-delegated", str(transcript)))

    assert (decision, body) == ("approve", "")


def test_approves_native_subagent_even_when_parent_termination_is_pending(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """親のprocess-wi起動記録を共有する委譲先へ終了工程を要求しない。"""
    _set_state_directory(monkeypatch, tmp_path)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    _clear_caches()
    transcript = _write_transcript(
        tmp_path,
        [_skill_entry("agent-toolkit:process-wi"), _tool_result_entry("toolu_skill")],
    )

    decision, body = termination_order_advisor.evaluate(
        _payload("sess-native", str(transcript), agent_id="agent-review"),
    )

    assert (decision, body) == ("approve", "")


def test_approves_and_logs_when_transcript_unreadable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """transcriptが存在しない場合は遮断せず、Stop判定ログへ起動順を確かめられないことを記録する。"""
    _set_state_directory(monkeypatch, tmp_path)
    _clear_caches()
    session_id = "sess-unreadable"
    missing_path = str(tmp_path / "does-not-exist.jsonl")

    decision, body = termination_order_advisor.evaluate(_payload(session_id, missing_path))

    assert (decision, body) == ("approve", "")
    log_text = (tmp_path / f"claude-agent-toolkit-stop-{session_id}.log").read_text(encoding="utf-8")
    assert "起動順を確認できない" in log_text


def test_approves_when_pending_async_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """継続中の非同期作業がある場合は既存の判定を維持し遮断しない。"""
    _set_state_directory(monkeypatch, tmp_path)
    _clear_caches()
    transcript = _write_transcript(
        tmp_path,
        [
            _skill_entry("agent-toolkit:process-wi", tool_use_id="toolu_1"),
            _tool_result_entry("toolu_1"),
            _async_wait_entry(),
        ],
    )

    decision, body = termination_order_advisor.evaluate(_payload("sess-pending-async", str(transcript)))

    assert (decision, body) == ("approve", "")


def test_add_awi_requires_only_completion_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """`agent-toolkit:add-awi-by-user`は`agent-toolkit:completion-report`だけを要求する。"""
    _set_state_directory(monkeypatch, tmp_path)
    _clear_caches()
    transcript = _write_transcript(
        tmp_path,
        [
            _skill_entry("agent-toolkit:add-awi-by-user", tool_use_id="toolu_1"),
            _tool_result_entry("toolu_1"),
            _skill_entry("agent-toolkit:completion-report", tool_use_id="toolu_2"),
            _tool_result_entry("toolu_2"),
        ],
    )

    decision, body = termination_order_advisor.evaluate(_payload("sess-add-awi", str(transcript)))

    assert (decision, body) == ("approve", "")


def test_add_awi_blocks_without_completion_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """`agent-toolkit:add-awi-by-user`起動後に終了工程が無ければ遮断する。"""
    _set_state_directory(monkeypatch, tmp_path)
    _clear_caches()
    transcript = _write_transcript(
        tmp_path,
        [_skill_entry("agent-toolkit:add-awi-by-user"), _tool_result_entry("toolu_skill")],
    )

    decision, body = termination_order_advisor.evaluate(_payload("sess-add-awi-missing", str(transcript)))

    assert decision == "block"
    assert "agent-toolkit:add-awi-by-user" in body
    assert "agent-toolkit:completion-report" in body


@pytest.mark.parametrize("is_error", [True, False])
def test_target_skill_requires_successful_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    is_error: bool,
) -> None:
    """失敗結果または結果未到着の対象Skillは終了手順の対象にしない。"""
    _set_state_directory(monkeypatch, tmp_path)
    _clear_caches()
    entries = [_skill_entry("agent-toolkit:process-wi")]
    if is_error:
        entries.append(_tool_result_entry("toolu_skill", is_error=True))
    transcript = _write_transcript(tmp_path, entries)

    decision, body = termination_order_advisor.evaluate(_payload(f"sess-target-{is_error}", str(transcript)))

    assert (decision, body) == ("approve", "")


def test_failed_completion_report_does_not_satisfy_termination(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """失敗したcompletion-reportは終了工程を充足しない。"""
    _set_state_directory(monkeypatch, tmp_path)
    _clear_caches()
    transcript = _write_transcript(
        tmp_path,
        [
            _skill_entry("agent-toolkit:add-awi-by-user", tool_use_id="toolu_1"),
            _tool_result_entry("toolu_1"),
            _skill_entry("agent-toolkit:completion-report", tool_use_id="toolu_2"),
            _tool_result_entry("toolu_2", is_error=True),
        ],
    )

    decision, body = termination_order_advisor.evaluate(_payload("sess-failed-report", str(transcript)))

    assert decision == "block"
    assert "agent-toolkit:completion-report" in body


def _background_bash_entries(tool_use_id: str, task_id: str, command: str = "atk agents wait") -> list[dict]:
    """背景Bashの起動と、`backgroundTaskId`を持つ起動結果のエントリを返す。"""
    return [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_use_id,
                        "name": "Bash",
                        "input": {"command": command, "run_in_background": True},
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": "started"}]},
            "toolUseResult": {"backgroundTaskId": task_id},
        },
    ]


def _report_entry(text: str) -> dict:
    """報告を可視発話として持つアシスタントエントリを返す。"""
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _append_entries(trace: pathlib.Path, entries: list[dict]) -> None:
    with trace.open("a", encoding="utf-8") as stream:
        for entry in entries:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _human_input(session_id: str, trace: pathlib.Path, turn_id: str, prompt: str) -> None:
    """UserPromptSubmitへ人間の入力を渡し、その時点のtranscriptの位置を作業の開始位置として記録させる。"""
    with contextlib.redirect_stdout(io.StringIO()):
        user_prompt_submit.main(
            json.dumps({"session_id": session_id, "turn_id": turn_id, "prompt": prompt, "transcript_path": str(trace)})
        )


@pytest.mark.parametrize("reentrant", [False, True])
def test_unrelated_background_task_does_not_exempt_missing_reports(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, reentrant: bool
) -> None:
    """作業の開始より前から動く常駐タスクだけでは、振り返り結果報告の不足を免除しない。

    セッション全体のバックグラウンドタスクの有無で許可すると、無関係な常駐Bashがある間は報告が欠けたまま終了する。
    """
    _set_state_directory(monkeypatch, tmp_path)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    _clear_caches()
    trace = tmp_path / "trace.jsonl"
    _append_entries(trace, _background_bash_entries("toolu_server", "resident"))
    _human_input("background-unrelated", trace, "request", "作業を進める")
    _append_entries(trace, [_report_entry("## 作業完了報告\n成果。")])
    payload = {
        "session_id": "background-unrelated",
        "transcript_path": str(trace),
        "stop_hook_active": reentrant,
        "background_tasks": [{"id": "resident", "type": "shell"}],
    }
    decision, reason = termination_order_advisor.evaluate(json.dumps(payload))
    assert decision == "block" and "review-result" in reason


@pytest.mark.parametrize("reentrant", [False, True])
def test_background_wait_of_work_allows_turn_end_until_collected(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, reentrant: bool
) -> None:
    """作業が起動した背景CLIの待機中は終了を許し、回収後は残る報告段階を示す。"""
    _set_state_directory(monkeypatch, tmp_path)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    _clear_caches()
    trace = tmp_path / "trace.jsonl"
    _human_input("background-owned", trace, "request", "委譲して結果を待つ")
    _append_entries(trace, [_report_entry("## 作業完了報告\n成果。"), *_background_bash_entries("toolu_wait", "wait-task")])
    payload = {
        "session_id": "background-owned",
        "transcript_path": str(trace),
        "stop_hook_active": reentrant,
        "background_tasks": [{"id": "wait-task", "type": "shell"}],
    }
    assert termination_order_advisor.evaluate(json.dumps(payload)) == ("approve", "")
    notification = (
        "<task-notification><tool-use-id>toolu_wait</tool-use-id><task-id>wait-task</task-id>"
        "<status>completed</status></task-notification>"
    )
    _append_entries(
        trace,
        [
            {"type": "user", "message": {"content": [{"type": "text", "text": notification}]}},
            _report_entry("委譲先の結果を受け取った。"),
        ],
    )
    payload["background_tasks"] = []
    _clear_caches()
    decision, reason = termination_order_advisor.evaluate(json.dumps(payload))
    assert decision == "block" and "review-result" in reason


@pytest.mark.parametrize("command", ["atk serve --port 8000", "pnpm run dev", "cd /repo && make watch"])
def test_resident_background_command_of_work_does_not_exempt_missing_reports(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    """作業内で背景起動した常駐コマンドは待機対象ではなく、報告不足を免除しない。

    常駐コマンドは終了せず完了通知による再開も来ないため、免除すると報告が欠けたまま終了する。
    """
    _set_state_directory(monkeypatch, tmp_path)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    _clear_caches()
    trace = tmp_path / "trace.jsonl"
    _human_input("background-resident", trace, "request", "開発サーバーを起動して確かめる")
    _append_entries(
        trace, [_report_entry("## 作業完了報告\n成果。"), *_background_bash_entries("toolu_server", "server", command)]
    )
    payload = {
        "session_id": "background-resident",
        "transcript_path": str(trace),
        "stop_hook_active": False,
        "background_tasks": [{"id": "server", "type": "shell"}],
    }
    decision, reason = termination_order_advisor.evaluate(json.dumps(payload))
    assert decision == "block" and "review-result" in reason


@pytest.mark.parametrize(
    "command",
    [
        "/plugin/agent-toolkit/agent_toolkit/atk.py agents wait",
        "uv run --project /plugin --locked --no-default-groups /plugin/agent_toolkit/wait_ci.py --baseline /tmp/b.json",
    ],
)
def test_background_wait_command_variants_allow_turn_end(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    """パスで起動した`atk agents wait`と`wait_ci.py`の背景待機は、作業の待機として終了を許す。"""
    _set_state_directory(monkeypatch, tmp_path)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    _clear_caches()
    trace = tmp_path / "trace.jsonl"
    _human_input("background-wait-variant", trace, "request", "結果を待つ")
    _append_entries(
        trace, [_report_entry("## 作業完了報告\n成果。"), *_background_bash_entries("toolu_wait", "wait-task", command)]
    )
    payload = {
        "session_id": "background-wait-variant",
        "transcript_path": str(trace),
        "stop_hook_active": False,
        "background_tasks": [{"id": "wait-task", "type": "shell"}],
    }
    assert termination_order_advisor.evaluate(json.dumps(payload)) == ("approve", "")


def test_background_wait_exempts_only_work_that_started_it(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """後の作業が起動した待機は後の作業だけを免除し、先の作業の不足は遮断の理由に残す。"""
    _set_state_directory(monkeypatch, tmp_path)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    _clear_caches()
    session_id = "background-two-works"
    trace = tmp_path / "trace.jsonl"
    _human_input(session_id, trace, "first", "最初の作業")
    _append_entries(trace, [_report_entry("## 作業完了報告\n最初の成果。")])
    payload = {"session_id": session_id, "transcript_path": str(trace), "stop_hook_active": False, "background_tasks": []}
    assert termination_order_advisor.evaluate(json.dumps(payload))[0] == "block"
    prompt = "次の作業を始める"
    _human_input(session_id, trace, "second", prompt)
    second = termination_evidence.record_decision(
        {"session_id": session_id, "action": "start", "input_id": "second", "quote": prompt, "reason": "次の作業"}
    )
    _append_entries(trace, [_report_entry("## 作業完了報告\n次の成果。"), *_background_bash_entries("toolu_wait", "wait-task")])
    payload["background_tasks"] = [{"id": "wait-task", "type": "shell"}]
    _clear_caches()
    decision, reason = termination_order_advisor.evaluate(json.dumps(payload))
    assert decision == "block"
    assert "作業 work-1: review-result" in reason
    assert f"作業 {second}" not in reason
