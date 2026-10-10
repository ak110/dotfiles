"""agent-toolkit/agent_toolkit/_hooks/termination_order_advisor.py のテスト。

多段終了手順の不足・順序違反の検出と、非対象・委譲先・記録読取不能・非同期作業継続中の
各非遮断条件を検証する。
"""

import contextlib
import io
import json
import pathlib

import pytest

from agent_toolkit._hooks import background_tasks as _background_tasks
from agent_toolkit._hooks import termination_evidence, termination_order_advisor, user_prompt_submit
from agent_toolkit._hooks import transcript_scan as _transcript_scan
from agent_toolkit._testing import git_repository
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
    _background_tasks._PENDING_ASYNC_WORK_CACHE.clear()  # pylint: disable=protected-access
    _transcript_scan._TRANSCRIPT_ENTRIES_CACHE.clear()  # pylint: disable=protected-access


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


def _self_edit_setup(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[dict, pathlib.Path]:
    """Claudeの協調作業と実Gitの基準版を、ホストの設定と分離して用意する。"""
    _set_state_directory(monkeypatch, tmp_path)
    for key in (
        "AGENT_TOOLKIT_DELEGATED_SESSION",
        "AGENT_TOOLKIT_OWNER_SESSION",
        "AGENT_TOOLKIT_PROCESS_LOOP_SESSION",
        "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    _clear_caches()
    repository = git_repository.init_repository(tmp_path / "repo", files={"README.md": "元の本文\n"}, commit_message="基準")
    trace = _write_transcript(tmp_path, [])
    payload = {
        "session_id": "self-edit",
        "transcript_path": str(trace),
        "cwd": str(repository),
        "last_assistant_message": "",
        "background_tasks": [],
        "stop_hook_active": False,
    }
    termination_evidence.observe_user(json.dumps({**payload, "prompt_id": "request", "prompt": "READMEを編集する"}))
    return payload, repository


def _record_self_edit(payload: dict, target: pathlib.Path, *, tool: str = "Edit", failed: bool = False) -> None:
    if tool == "Bash":
        tool_input = {"command": f"sed -i 's/元/新/' {target}"}
    else:
        tool_input = {"file_path": str(target), "old_string": "元", "new_string": "新", "content": "新しい本文\n"}
    invocation = {**payload, "tool_name": tool, "tool_input": tool_input, "tool_use_id": "edit-call"}
    termination_evidence.observe_tool(json.dumps(invocation), after=False)
    target.write_text("新しい本文\n", encoding="utf-8")
    termination_evidence.observe_tool(
        json.dumps(
            {
                **invocation,
                "hook_event_name": "PostToolUseFailure" if failed else "PostToolUse",
                "tool_response": {"exit_code": 1 if failed else 0},
            }
        ),
        after=True,
    )


@pytest.mark.parametrize("tool", ["Edit", "Write", "Bash"])
def test_unresolved_self_edits_warn_at_first_stop(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, tool: str) -> None:
    """成功した自編集だけから初回Stopを案内し、同じ状態の再入で反復・昇格しない。"""
    payload, repository = _self_edit_setup(tmp_path, monkeypatch)
    _record_self_edit(payload, repository / "README.md", tool=tool)
    decision, body = termination_order_advisor.evaluate(json.dumps(payload))
    assert decision == "notify"
    assert 'kind="warn"' in body
    assert "公開範囲" in body and "completion-report" in body
    assert "commitを" not in body
    payload["stop_hook_active"] = True
    assert termination_order_advisor.evaluate(json.dumps(payload)) == ("approve", "")


@pytest.mark.parametrize("origin", ["instruction", "answer", "standing-authorization"])
@pytest.mark.parametrize("scope", ["既存の判断基準どおり", "push・CIまで", "commitまで", "commitしない"])
def test_public_scope_evidence_exempts_self_edits(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, origin: str, scope: str
) -> None:
    """原入力または適用済み認可から確定した範囲を、JSON記録用の公開コマンドで同じ作業へ接続する。"""
    payload, repository = _self_edit_setup(tmp_path, monkeypatch)
    _record_self_edit(payload, repository / "README.md")
    quote = f"公開範囲は{scope}とする"
    termination_evidence.observe_user(json.dumps({**payload, "prompt_id": "scope-answer", "prompt": quote}))
    work_id = termination_evidence.session_works(payload)[-1][0]
    document = {
        "session_id": "self-edit",
        "work_id": work_id,
        "action": "publish-scope",
        "input_id": "scope-answer",
        "quote": quote,
        "reason": "原入力の範囲を適用",
        "origin": origin,
        "scope": scope,
    }
    if origin == "standing-authorization":
        policy = tmp_path / "policy.md"
        policy.write_text(quote, encoding="utf-8")
        document["policy_file"] = str(policy)
    decision_file = tmp_path / "decision.json"
    decision_file.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    assert termination_evidence.main(["--decision-file", str(decision_file)]) == 0
    assert termination_order_advisor.evaluate(json.dumps(payload)) == ("approve", "")


@pytest.mark.parametrize(
    "case",
    [
        "other-diff",
        "outside",
        "script",
        "failed",
        "committed",
        "restored",
        "completion",
        "wait",
        "codex",
        "delegated",
        "autonomous",
    ],
)
def test_self_edit_warning_excludes_resolved_and_unowned_changes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """無関係な差分・解消済みの編集・正常待機と対象外ホストを未完了にしない。"""
    payload, repository = _self_edit_setup(tmp_path, monkeypatch)
    target = tmp_path / "outside.md" if case == "outside" else repository / "README.md"
    if case == "script":
        termination_evidence.observe_tool(
            json.dumps(
                {
                    **payload,
                    "tool_name": "Bash",
                    "tool_use_id": "script-call",
                    "tool_input": {"command": "python arbitrary.py"},
                    "tool_response": {"exit_code": 0},
                }
            ),
            after=True,
        )
        target.write_text("変化\n", encoding="utf-8")
    else:
        _record_self_edit(payload, target, failed=case == "failed")
    if case == "other-diff":
        target.write_text("元の本文\n", encoding="utf-8")
        (repository / "other.md").write_text("他主体の差分\n", encoding="utf-8")
    elif case == "committed":
        git_repository.commit_all(repository, "編集")
    elif case == "restored":
        target.write_text("元の本文\n", encoding="utf-8")
    elif case == "completion":
        termination_evidence.observe_tool(
            json.dumps(
                {
                    **payload,
                    "tool_name": "Skill",
                    "tool_input": {"skill": "agent-toolkit:completion-report"},
                    "tool_use_id": "completion-call",
                    "tool_response": "起動",
                }
            ),
            after=True,
        )
    elif case == "wait":
        _append_entries(pathlib.Path(payload["transcript_path"]), _background_bash_entries("toolu_wait", "wait-task"))
        payload["background_tasks"] = [{"id": "wait-task", "type": "shell"}]
        _clear_caches()
    elif case == "codex":
        payload["turn_id"] = "codex-turn"
    elif case == "delegated":
        payload["agent_id"] = "child"
    elif case == "autonomous":
        monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    assert termination_order_advisor.evaluate(json.dumps(payload)) == ("approve", "")


def test_scope_does_not_carry_into_next_editing_work(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """前の作業の公開範囲を、次の依頼による自編集へ流用しない。"""
    payload, repository = _self_edit_setup(tmp_path, monkeypatch)
    _record_self_edit(payload, repository / "README.md")
    work_id = termination_evidence.session_works(payload)[-1][0]
    termination_evidence.record_decision(
        {
            "session_id": "self-edit",
            "work_id": work_id,
            "action": "publish-scope",
            "input_id": "request",
            "quote": "READMEを編集する",
            "reason": "依頼へ範囲を適用",
            "origin": "instruction",
            "scope": "commitしない",
        }
    )
    termination_evidence.observe_user(json.dumps({**payload, "prompt_id": "next", "prompt": "次の編集をする"}))
    termination_evidence.record_decision(
        {
            "session_id": "self-edit",
            "action": "start",
            "input_id": "next",
            "quote": "次の編集をする",
            "reason": "独立した次の作業",
        }
    )
    next_call = {
        **payload,
        "tool_name": "Write",
        "tool_input": {"file_path": str(repository / "next.md"), "content": "次の本文"},
        "tool_use_id": "next-edit",
        "tool_response": {"exit_code": 0},
    }
    (repository / "next.md").write_text("次の本文", encoding="utf-8")
    termination_evidence.observe_tool(json.dumps(next_call), after=True)
    assert termination_order_advisor.evaluate(json.dumps(payload))[0] == "notify"


def test_self_edit_warning_preserves_existing_termination_block(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """自編集のwarnが、再入時の別の終了契約のblockを上書きしない。"""
    payload, repository = _self_edit_setup(tmp_path, monkeypatch)
    _record_self_edit(payload, repository / "README.md")
    _append_entries(
        pathlib.Path(payload["transcript_path"]),
        [_skill_entry("agent-toolkit:add-awi-by-user"), _tool_result_entry("toolu_skill")],
    )
    payload["stop_hook_active"] = True
    _clear_caches()
    decision, body = termination_order_advisor.evaluate(json.dumps(payload))
    assert decision == "block"
    assert "agent-toolkit:completion-report" in body


def test_generated_notification_keeps_human_editing_request(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """人間の依頼後の自動通知で再開しても、その依頼に属する自編集を判定する。"""
    payload, repository = _self_edit_setup(tmp_path, monkeypatch)
    termination_evidence.observe_user(
        json.dumps(
            {
                **payload,
                "prompt_id": "notification",
                "source": "plugin",
                "prompt": '<atk-auto source="host" kind="notice">完了通知</atk-auto>',
            }
        )
    )
    _record_self_edit(payload, repository / "README.md")
    assert termination_order_advisor.evaluate(json.dumps(payload))[0] == "notify"


def test_scope_of_separate_work_keeps_prior_unresolved_edit(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """独立した作業の開始を記録した場合、次の公開範囲が前の未確定編集を覆わない。"""
    payload, repository = _self_edit_setup(tmp_path, monkeypatch)
    _record_self_edit(payload, repository / "README.md")
    prompt = "次の独立した作業"
    termination_evidence.observe_user(json.dumps({**payload, "prompt_id": "next", "prompt": prompt}))
    next_work = termination_evidence.record_decision(
        {"session_id": "self-edit", "action": "start", "input_id": "next", "quote": prompt, "reason": "独立した作業"}
    )
    target = repository / "next.md"
    target.write_text("次の本文", encoding="utf-8")
    termination_evidence.observe_tool(
        json.dumps(
            {
                **payload,
                "tool_name": "Write",
                "tool_input": {"file_path": str(target), "content": "次の本文"},
                "tool_use_id": "next-call",
                "tool_response": {"exit_code": 0},
            }
        ),
        after=True,
    )
    termination_evidence.record_decision(
        {
            "session_id": "self-edit",
            "action": "publish-scope",
            "work_id": next_work,
            "input_id": "next",
            "quote": prompt,
            "reason": "次の作業だけを扱う",
            "origin": "instruction",
            "scope": "commitしない",
        }
    )
    decision, body = termination_order_advisor.evaluate(json.dumps(payload))
    assert decision == "notify"
    assert str(repository / "README.md") in body
    assert str(target) not in body


def test_public_context_supplies_exact_input_for_scope_recording(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """変更前でも原入力を公開コマンドで取得し、同じ入力だけを公開範囲の記録へ渡せる。"""
    payload, repository = _self_edit_setup(tmp_path, monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "self-edit")
    assert termination_evidence.main(["--context"]) == 0
    context = json.loads(capsys.readouterr().out)
    assert context == {"session_id": "self-edit", "input_id": "request", "quote": "READMEを編集する", "work_id": None}
    document = {
        **context,
        "action": "publish-scope",
        "scope": "commitしない",
        "origin": "instruction",
        "reason": "依頼へ既存の範囲を適用",
    }
    source = tmp_path / "scope.json"
    source.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    assert termination_evidence.main(["--decision-file", str(source)]) == 0
    _record_self_edit(payload, repository / "README.md")
    assert termination_order_advisor.evaluate(json.dumps(payload)) == ("approve", "")


def test_scope_metadata_keeps_cancelled_work_cancelled(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """公開範囲の証拠は、既存の中止判断を取り下げる操作ではない。"""
    payload, repository = _self_edit_setup(tmp_path, monkeypatch)
    _record_self_edit(payload, repository / "README.md")
    work_id = termination_evidence.session_works(payload)[-1][0]
    termination_evidence.observe_user(json.dumps({**payload, "prompt_id": "cancel", "prompt": "作業を中止する"}))
    termination_evidence.record_decision(
        {
            "session_id": "self-edit",
            "work_id": work_id,
            "action": "cancel",
            "input_id": "cancel",
            "quote": "作業を中止する",
            "reason": "中止指示",
        }
    )
    termination_evidence.observe_user(json.dumps({**payload, "prompt_id": "scope", "prompt": "公開せずcommitしない"}))
    termination_evidence.record_decision(
        {
            "session_id": "self-edit",
            "work_id": work_id,
            "action": "publish-scope",
            "input_id": "scope",
            "quote": "公開せずcommitしない",
            "reason": "中止した作業の範囲",
            "origin": "answer",
            "scope": "commitしない",
        }
    )
    assert not termination_evidence.pending_work(payload)


def test_failed_edit_keeps_existing_failure_evidence(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """編集の失敗を自編集へ計上せず、技術的不成立の原証拠としては保持する。"""
    payload, repository = _self_edit_setup(tmp_path, monkeypatch)
    work_id = termination_evidence.record_decision(
        {
            "session_id": "self-edit",
            "action": "start",
            "input_id": "request",
            "quote": "READMEを編集する",
            "reason": "編集する作業",
        }
    )
    _record_self_edit(payload, repository / "README.md", failed=True)
    assert (
        termination_evidence.record_decision(
            {
                "session_id": "self-edit",
                "work_id": work_id,
                "action": "blocked",
                "call_id": "edit-call",
                "quote": json.dumps({"exit_code": 1}, ensure_ascii=False, sort_keys=True),
                "reason": "編集が失敗した",
            }
        )
        == work_id
    )
