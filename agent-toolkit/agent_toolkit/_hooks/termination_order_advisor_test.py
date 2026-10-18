"""agent-toolkit/agent_toolkit/_hooks/termination_order_advisor.py のテスト。

成果作業でcompletion-reportの起動証拠を欠く終了の遮断と、委譲先・記録読取不能・作業が待つ非同期対象・
自編集の公開範囲の各条件を検証する。
"""

import argparse
import contextlib
import io
import json
import pathlib

import pytest

from agent_toolkit._atk import run_script
from agent_toolkit._common import automated_prompt
from agent_toolkit._hooks import (
    autonomous_exit,
    stop,
    termination_evidence,
    termination_order_advisor,
    user_prompt_submit,
)
from agent_toolkit._hooks import background_tasks as _background_tasks
from agent_toolkit._hooks import transcript_scan as _transcript_scan
from agent_toolkit._hooks.termination_evidence_test import isolated_session  # noqa: F401  # pylint: disable=unused-import
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.helpers import _write_transcript


@pytest.fixture(autouse=True)
def _fresh_caches() -> None:
    """前の試験のtranscript解析とバックグラウンドタスクの判定を流用しない。"""
    _clear_caches()


def _clear_caches() -> None:
    _background_tasks._PENDING_ASYNC_WORK_CACHE.clear()  # pylint: disable=protected-access
    _transcript_scan._TRANSCRIPT_ENTRIES_CACHE.clear()  # pylint: disable=protected-access


def _payload(session_id: str, transcript_path: str, *, agent_id: str | None = None, **extra: object) -> str:
    payload: dict[str, object] = {
        "session_id": session_id,
        "transcript_path": transcript_path,
        "stop_hook_active": False,
        "background_tasks": [],
        **extra,
    }
    if agent_id is not None:
        payload["agent_id"] = agent_id
    return json.dumps(payload)


def _observe(
    session_id: str, trace: pathlib.Path, tool_use_id: str, tool_name: str, tool_input: dict, response: object
) -> None:
    """同じ呼び出しの開始と成功した応答を証拠の所有モジュールへ渡す。"""
    payload = {
        "session_id": session_id,
        "transcript_path": str(trace),
        "tool_name": tool_name,
        "tool_use_id": tool_use_id,
        "tool_input": tool_input,
    }
    termination_evidence.observe_tool(json.dumps(payload), after=False)
    termination_evidence.observe_tool(json.dumps({**payload, "tool_response": response}), after=True)


def _start_skill(session_id: str, trace: pathlib.Path, skill: str, tool_use_id: str = "toolu_skill") -> None:
    _observe(session_id, trace, tool_use_id, "Skill", {"skill": skill}, "起動")


def _commit(session_id: str, trace: pathlib.Path, tool_use_id: str = "toolu_commit") -> None:
    _observe(session_id, trace, tool_use_id, "Bash", {"command": "git commit -m 変更"}, {"stdout": "", "exit_code": 0})


def test_approves_session_without_deliverable(tmp_path: pathlib.Path) -> None:
    """成果作業の無い回答・調査のセッションでは起動証拠を求めない。"""
    trace = _write_transcript(tmp_path, [])
    _start_skill("sess-unrelated", trace, "agent-toolkit:writing-standards")
    assert termination_order_advisor.evaluate(_payload("sess-unrelated", str(trace))) == ("approve", "")


@pytest.mark.parametrize("skill", ["agent-toolkit:process-wi", "agent-toolkit:add-awi-by-user"])
def test_blocks_target_skill_without_completion_report_from_first_stop(tmp_path: pathlib.Path, skill: str) -> None:
    """対象スキルの開始後にcompletion-reportの起動が無ければ、初回Stopから遮断して起動と残工程を案内する。"""
    trace = _write_transcript(tmp_path, [])
    _start_skill("sess-missing", trace, skill)
    decision, body = termination_order_advisor.evaluate(_payload("sess-missing", str(trace)))
    assert decision == "block"
    assert f"{skill}の開始" in body
    assert "Skillで`agent-toolkit:completion-report`を起動し" in body
    assert "終端工程" in body
    _start_skill("sess-missing", trace, "agent-toolkit:completion-report", "toolu_report")
    assert termination_order_advisor.evaluate(_payload("sess-missing", str(trace)))[0] == "block"
    assert termination_order_advisor.evaluate(_payload("sess-missing", str(trace))) == ("approve", "")


def test_completion_before_target_restart_is_not_reused(tmp_path: pathlib.Path) -> None:
    """前の作業の起動証拠を、新しい依頼で再開始した対象スキルの作業へ流用しない。"""
    trace = _write_transcript(tmp_path, [])
    _start_skill("sess-second", trace, "agent-toolkit:process-wi", "toolu_1")
    _start_skill("sess-second", trace, "agent-toolkit:completion-report", "toolu_2")
    with contextlib.redirect_stdout(io.StringIO()):
        user_prompt_submit.main(json.dumps({"session_id": "sess-second", "turn_id": "again", "prompt": "もう一度処理する"}))
    _start_skill("sess-second", trace, "agent-toolkit:process-wi", "toolu_3")
    assert termination_order_advisor.evaluate(_payload("sess-second", str(trace)))[0] == "block"


def test_approves_delegated_session(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`AGENT_TOOLKIT_DELEGATED_SESSION`が`1`の委譲先では起動証拠を確かめない。"""
    trace = _write_transcript(tmp_path, [])
    _commit("sess-delegated", trace)
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    assert termination_order_advisor.evaluate(_payload("sess-delegated", str(trace))) == ("approve", "")


def test_approves_native_subagent_even_when_parent_work_is_pending(tmp_path: pathlib.Path) -> None:
    """親の成果作業の記録を共有する委譲先へcompletion-reportを要求しない。"""
    trace = _write_transcript(tmp_path, [])
    _commit("sess-native", trace)
    assert termination_order_advisor.evaluate(_payload("sess-native", str(trace), agent_id="agent-review")) == ("approve", "")


def test_approves_and_logs_when_transcript_unreadable(tmp_path: pathlib.Path) -> None:
    """待機対象を確かめるtranscriptが存在しない場合は遮断せず、Stop判定ログへ確かめられないことを記録する。"""
    session_id = "sess-unreadable"
    missing_path = str(tmp_path / "does-not-exist.jsonl")
    _commit(session_id, pathlib.Path(missing_path))
    assert termination_order_advisor.evaluate(_payload(session_id, missing_path)) == ("approve", "")
    log_text = (tmp_path / f"claude-agent-toolkit-stop-{session_id}.log").read_text(encoding="utf-8")
    assert "作業の待機対象を確認できない" in log_text


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


def test_unrelated_background_task_does_not_exempt_missing_completion(tmp_path: pathlib.Path) -> None:
    """作業の開始より前から動く常駐タスクだけでは、起動不足を免除しない。"""
    trace = tmp_path / "trace.jsonl"
    _append_entries(trace, _background_bash_entries("toolu_server", "resident"))
    _human_input("background-unrelated", trace, "request", "作業を進める")
    _commit("background-unrelated", trace)
    payload = _payload("background-unrelated", str(trace), background_tasks=[{"id": "resident", "type": "shell"}])
    decision, reason = termination_order_advisor.evaluate(payload)
    assert decision == "block" and "commit" in reason


def test_background_wait_of_work_allows_turn_end_until_collected(tmp_path: pathlib.Path) -> None:
    """作業が起動した背景CLIの待機中は終了を許し、回収後は起動不足を示す。"""
    trace = tmp_path / "trace.jsonl"
    trace.touch()
    _human_input("background-owned", trace, "request", "委譲して結果を待つ")
    _commit("background-owned", trace)
    _append_entries(trace, _background_bash_entries("toolu_wait", "wait-task"))
    payload = json.loads(_payload("background-owned", str(trace), background_tasks=[{"id": "wait-task", "type": "shell"}]))
    assert termination_order_advisor.evaluate(json.dumps(payload)) == ("approve", "")
    notification = (
        "<task-notification><tool-use-id>toolu_wait</tool-use-id><task-id>wait-task</task-id>"
        "<status>completed</status></task-notification>"
    )
    _append_entries(
        trace,
        [
            {"type": "user", "message": {"content": [{"type": "text", "text": notification}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "委譲先の結果を受け取った。"}]}},
        ],
    )
    payload["background_tasks"] = []
    _clear_caches()
    decision, reason = termination_order_advisor.evaluate(json.dumps(payload))
    assert decision == "block" and "commit" in reason


@pytest.mark.parametrize("command", ["atk serve", "npm run watch", "python3 -m http.server 8080", "uv run uvicorn app:app"])
def test_resident_background_command_does_not_exempt_completion(tmp_path: pathlib.Path, command: str) -> None:
    """終了通知を待てない常駐コマンドは、成果作業の起動不足を免除しない。"""
    trace = tmp_path / "trace.jsonl"
    trace.touch()
    _human_input("resident-owned", trace, "request", "常駐サーバーを起動する")
    _commit("resident-owned", trace)
    _append_entries(trace, _background_bash_entries("toolu_server", "resident", command))
    payload = _payload("resident-owned", str(trace), background_tasks=[{"id": "resident", "type": "shell"}])
    assert termination_order_advisor.evaluate(payload)[0] == "block"


@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
@pytest.mark.parametrize("launcher", ["atk", "C:/tools/atk.cmd", r"'C:\tools\atk.cmd'", "atk.py"])
@pytest.mark.parametrize("arguments", ["serve", "serve --help"])
def test_atk_launcher_resident_detection_for_both_shell_tools(tmp_path, tool, launcher, arguments):
    """常駐起動は待機免除から除き、同じランチャーの有限のヘルプは待機できる。"""
    trace = tmp_path / "trace.jsonl"
    trace.touch()
    _human_input("launcher-resident", trace, "request", "操作を進める")
    _commit("launcher-resident", trace)
    prefix = "& " if tool == "PowerShell" else ""
    entries = _background_bash_entries("toolu_launcher", "launcher-task", prefix + launcher + " " + arguments)
    entries[0]["message"]["content"][0]["name"] = tool
    _append_entries(trace, entries)
    payload = _payload("launcher-resident", str(trace), background_tasks=[{"id": "launcher-task", "type": "shell"}])
    decision, _ = termination_order_advisor.evaluate(payload)
    assert decision == ("block" if arguments == "serve" else "approve")


@pytest.mark.parametrize(
    "command",
    [
        "make test",
        "atk run-script plan-verify -- --plan /p.md --worktree /repo --timeout 600",
        "timeout 10 atk serve",
        "echo serve",
        "echo --watch",
        "npm view watch",
        "atk serve --help",
    ],
)
def test_background_command_of_work_allows_turn_end_until_notified(tmp_path: pathlib.Path, command: str) -> None:
    """作業内で背景起動した有限のコマンドの実行中は終了を許し、完了通知の後は起動不足を示す。"""
    trace = tmp_path / "trace.jsonl"
    trace.touch()
    _human_input("background-command", trace, "request", "全体検証を背景で実行して結果を待つ")
    _commit("background-command", trace)
    _append_entries(trace, _background_bash_entries("toolu_check", "check-task", command))
    payload = json.loads(_payload("background-command", str(trace), background_tasks=[{"id": "check-task", "type": "shell"}]))
    assert termination_order_advisor.evaluate(json.dumps(payload)) == ("approve", "")
    notification = (
        "<task-notification><tool-use-id>toolu_check</tool-use-id><task-id>check-task</task-id>"
        "<status>completed</status></task-notification>"
    )
    _append_entries(
        trace,
        [
            {"type": "user", "message": {"content": [{"type": "text", "text": notification}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "検証の結果を受け取った。"}]}},
        ],
    )
    payload["background_tasks"] = []
    _clear_caches()
    decision, reason = termination_order_advisor.evaluate(json.dumps(payload))
    assert decision == "block" and "commit" in reason


@pytest.mark.parametrize(
    "command",
    [
        "/plugin/agent-toolkit/agent_toolkit/atk.py agents wait",
        "uv run --project /plugin --locked --no-default-groups /plugin/agent_toolkit/wait_ci.py --baseline /tmp/b.json",
    ],
)
def test_background_wait_command_variants_allow_turn_end(tmp_path: pathlib.Path, command: str) -> None:
    """パスで起動した`atk agents wait`と`wait_ci.py`の背景待機は、作業の待機として終了を許す。"""
    trace = tmp_path / "trace.jsonl"
    trace.touch()
    _human_input("background-wait-variant", trace, "request", "結果を待つ")
    _commit("background-wait-variant", trace)
    _append_entries(trace, _background_bash_entries("toolu_wait", "wait-task", command))
    payload = _payload("background-wait-variant", str(trace), background_tasks=[{"id": "wait-task", "type": "shell"}])
    assert termination_order_advisor.evaluate(payload) == ("approve", "")


def test_background_wait_exempts_only_work_that_started_it(tmp_path: pathlib.Path) -> None:
    """後の作業が起動した待機は後の作業だけを免除し、先の作業の不足は遮断の理由に残す。"""
    session_id = "background-two-works"
    trace = tmp_path / "trace.jsonl"
    trace.touch()
    _human_input(session_id, trace, "first", "最初の作業")
    _commit(session_id, trace, "toolu_first")
    assert termination_order_advisor.evaluate(_payload(session_id, str(trace)))[0] == "block"
    prompt = "次の作業を始める"
    _human_input(session_id, trace, "second", prompt)
    second = termination_evidence.record_decision(
        {"session_id": session_id, "action": "start", "input_id": "second", "quote": prompt, "reason": "次の作業"}
    )
    _commit(session_id, trace, "toolu_second")
    _append_entries(trace, _background_bash_entries("toolu_wait", "wait-task"))
    _clear_caches()
    decision, reason = termination_order_advisor.evaluate(
        _payload(session_id, str(trace), background_tasks=[{"id": "wait-task", "type": "shell"}])
    )
    assert decision == "block"
    assert "作業 work-1: commit" in reason
    assert f"作業 {second}" not in reason


def _self_edit_setup(tmp_path: pathlib.Path) -> tuple[dict, pathlib.Path]:
    """Claudeの協調作業と実Gitの基準版を用意する。"""
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
def test_self_edit_requires_completion_report_and_shows_unscoped_paths(tmp_path: pathlib.Path, tool: str) -> None:
    """成功した自編集を成果作業として遮断し、公開範囲が未確定の未コミットの編集を添える。"""
    payload, repository = _self_edit_setup(tmp_path)
    _record_self_edit(payload, repository / "README.md", tool=tool)
    decision, body = termination_order_advisor.evaluate(json.dumps(payload))
    assert decision == "block"
    assert "リポジトリの編集" in body and "completion-report" in body
    assert "公開範囲が未確定" in body and str(repository / "README.md") in body


@pytest.mark.parametrize("origin", ["instruction", "answer", "standing-authorization"])
@pytest.mark.parametrize("scope", ["既存の判断基準どおり", "commitしない"])
def test_public_scope_evidence_removes_unscoped_note(tmp_path: pathlib.Path, origin: str, scope: str) -> None:
    """原入力または適用済み認可から確定した範囲は未確定の編集の注記を除き、起動不足の遮断は残す。"""
    payload, repository = _self_edit_setup(tmp_path)
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
    with contextlib.redirect_stdout(io.StringIO()):
        assert termination_evidence.main(["--decision-file", str(decision_file)]) == 0
    decision, body = termination_order_advisor.evaluate(json.dumps(payload))
    assert decision == "block" and "公開範囲が未確定" not in body


@pytest.mark.parametrize("case", ["outside", "script", "failed", "completion", "delegated"])
def test_self_edit_excludes_unowned_failed_and_completed_edits(tmp_path: pathlib.Path, case: str) -> None:
    """担当外・失敗した編集と委譲先を除外し、起動済みの自編集は一度点検して終了できる。"""
    payload, repository = _self_edit_setup(tmp_path)
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
    if case == "completion":
        _start_skill(
            "self-edit", pathlib.Path(payload["transcript_path"]), "agent-toolkit:completion-report", "completion-call"
        )
    elif case == "delegated":
        payload["agent_id"] = "child"
    if case == "completion":
        assert termination_order_advisor.evaluate(json.dumps(payload))[0] == "block"
    assert termination_order_advisor.evaluate(json.dumps(payload)) == ("approve", "")


def test_codex_self_edit_is_deliverable(tmp_path: pathlib.Path) -> None:
    """Codexの自編集も成果作業として扱い、Codexの起動手順を案内する。"""
    payload, repository = _self_edit_setup(tmp_path)
    payload["turn_id"] = "codex-turn"
    _record_self_edit(payload, repository / "README.md")
    decision, body = termination_order_advisor.evaluate(json.dumps(payload))
    assert decision == "block" and "SKILL.md" in body and "公開範囲が未確定" not in body


def test_public_context_supplies_exact_input_for_scope_recording(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """変更前でも原入力を公開コマンドで取得し、同じ入力だけを公開範囲の記録へ渡せる。"""
    payload, repository = _self_edit_setup(tmp_path)
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
    decision, body = termination_order_advisor.evaluate(json.dumps(payload))
    assert decision == "block" and "公開範囲が未確定" not in body


def test_blocked_from_context_without_human_input(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """自動起動の会話で公開取得した識別子と実際の失敗証拠からblockedを記録し、Stopが技術的不成立として扱う。"""
    session_id = "auto-run"
    trace = _write_transcript(tmp_path, [])
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session_id)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    goal = automated_prompt.wrap("WIを処理する", source=automated_prompt.SOURCE_PROCESS_LOOP, kind=automated_prompt.KIND_GOAL)
    # 自動起動の生成入力も、人間入力と同じUserPromptSubmitのhookへ届く。
    _human_input(session_id, trace, "goal", goal)
    _commit(session_id, trace)
    failure_response = {"stdout": "", "stderr": "接続できない", "exit_code": 1}
    termination_evidence.observe_tool(
        json.dumps(
            {
                "session_id": session_id,
                "hook_event_name": "PostToolUseFailure",
                "tool_name": "Bash",
                "tool_use_id": "push",
                "tool_input": {"command": "git push"},
                "tool_response": failure_response,
            }
        ),
        after=True,
    )
    assert termination_order_advisor.evaluate(_payload(session_id, str(trace)))[0] == "block"
    _human_input(session_id, trace, "continuation", '<atk-auto source="stop" kind="continuation">判断を記録する</atk-auto>')

    capsys.readouterr()
    status = run_script.dispatch(argparse.Namespace(script_name="termination-evidence", script_args=["--", "--context"]))
    context = json.loads(capsys.readouterr().out)
    assert status == 0 and context["input_id"] is None and context["quote"] is None
    document = {
        "session_id": context["session_id"],
        "work_id": context["work_id"],
        "action": "blocked",
        "call_id": "push",
        "quote": json.dumps(failure_response, ensure_ascii=False, sort_keys=True),
        "reason": "pushの接続が失敗した",
    }
    source = tmp_path / "blocked.json"
    source.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    assert (
        run_script.dispatch(
            argparse.Namespace(script_name="termination-evidence", script_args=["--decision-file", str(source)])
        )
        == 0
    )
    assert termination_order_advisor.evaluate(_payload(session_id, str(trace))) == ("approve", "")


def test_scope_metadata_keeps_cancelled_work_cancelled(tmp_path: pathlib.Path) -> None:
    """公開範囲の証拠は、既存の中止判断を取り下げる操作ではない。"""
    payload, repository = _self_edit_setup(tmp_path)
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
    assert termination_order_advisor.evaluate(json.dumps(payload)) == ("approve", "")


def test_failed_edit_keeps_existing_failure_evidence(tmp_path: pathlib.Path) -> None:
    """編集の失敗を自編集へ計上せず、技術的不成立の原証拠としては保持する。"""
    payload, repository = _self_edit_setup(tmp_path)
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


def test_windows_edit_paths_match_git_status_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windowsの書込先も、Git状態が返すスラッシュ区切りの相対パスで対応付ける。"""
    repository = pathlib.PureWindowsPath("C:/repo")
    target = repository / "nested/note.md"
    with monkeypatch.context() as context:
        context.setattr(termination_evidence, "written_paths", lambda *_args: [str(target)])
        context.setattr(termination_evidence, "_git_root", lambda _cwd: repository)
        context.setattr(
            termination_evidence.uwi_scan, "private_notes_root", lambda: pathlib.PureWindowsPath("C:/private-notes")
        )
        context.setattr(termination_evidence._hook_tool_input, "parse_operations", lambda *_args: None)  # pylint: disable=protected-access
        context.setattr(pathlib, "Path", pathlib.PureWindowsPath)
        invocations = termination_evidence._invocations(  # pylint: disable=protected-access
            {"tool_name": "Write", "tool_input": {"file_path": str(target)}, "cwd": str(repository)}, after=True, failed=False
        )
    assert invocations == [
        {"kind": "deliverable", "labels": ["リポジトリの編集"], "repository": str(repository), "paths": ["nested/note.md"]}
    ]


def _omit_self_edit(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, *, codex: bool = False, deliverable: str = "edit"
) -> tuple[dict, pathlib.Path, str]:
    """成果作業とその規範を用意し、公開CLIから省略を記録する。"""
    payload, repository = _self_edit_setup(tmp_path)
    if codex:
        payload["turn_id"] = "codex-turn"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "self-edit")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    if codex:
        monkeypatch.setenv("CODEX_THREAD_ID", "self-edit")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent-conversation")
    policy = repository / "CLAUDE.md"
    quote = "ユーザーの指示まで作業完了報告と振り返りを実施しない。\n"
    policy.write_text(quote, encoding="utf-8")
    if deliverable == "edit":
        _record_self_edit(payload, repository / "README.md")
    else:
        termination_evidence.observe_tool(
            json.dumps(
                {
                    **payload,
                    "tool_name": "Bash",
                    "tool_use_id": "commit",
                    "tool_input": {"command": "git commit -m 変更"},
                    "tool_response": {"exit_code": 0},
                }
            ),
            after=True,
        )
    work_id = termination_evidence.session_works(payload)[-1][0]
    document = {
        "session_id": "self-edit",
        "action": "omit-completion",
        "work_id": work_id,
        "reason": "適用するプロジェクト規範を優先する",
        "policy_file": str(policy),
        "target_repo": str(repository),
    }
    source = tmp_path / "omit.json"
    source.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()):
        assert termination_evidence.main(["--decision-file", str(source)]) == 0
    return payload, policy, work_id


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize("deliverable", ["edit", "commit"])
def test_project_policy_omission_allows_completion_without_skill(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, deliverable: str, *, codex: bool
) -> None:
    """両ホストで規範に基づく省略はスキルを起動せず成果を届けて終了できる。"""
    payload, _policy, _work_id = _omit_self_edit(tmp_path, monkeypatch, codex=codex, deliverable=deliverable)
    assert termination_order_advisor.evaluate(json.dumps(payload)) == ("approve", "")


@pytest.mark.parametrize("changed", ["modified", "removed"])
def test_changed_omission_policy_restores_missing_completion(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    """保存後の規範全文の変更と消失は、終了時に省略を無効にする。"""
    payload, policy, work_id = _omit_self_edit(tmp_path, monkeypatch)
    if changed == "modified":
        policy.write_text("作業完了報告を実施する。\n", encoding="utf-8")
    else:
        policy.unlink()
    decision, body = termination_order_advisor.evaluate(json.dumps(payload))
    assert decision == "block" and f"作業 {work_id}:" in body


@pytest.mark.parametrize("switch", ["input", "start"])
def test_omission_does_not_exempt_later_work(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, switch: str) -> None:
    """新入力の後の成果作業と明示した開始では、以前の省略を流用しない。"""
    payload, _policy, work_id = _omit_self_edit(tmp_path, monkeypatch)
    termination_evidence.observe_user(json.dumps({**payload, "prompt_id": "next", "prompt": "次の成果を作成する"}))
    if switch == "start":
        termination_evidence.record_decision(
            {
                "session_id": "self-edit",
                "action": "start",
                "input_id": "next",
                "quote": "次の成果を作成する",
                "reason": "次の作業",
            }
        )
    _commit("self-edit", pathlib.Path(payload["transcript_path"]), "next-commit")
    decision, body = termination_order_advisor.evaluate(json.dumps(payload))
    works = termination_evidence.session_works(payload)
    assert len(works) == 2
    assert decision == "block" and f"作業 {works[-1][0]}:" in body and f"作業 {work_id}:" not in body


@pytest.mark.parametrize("action", ["resume", "cancel", "replace", "publish-scope"])
def test_next_decision_replaces_omission_without_changing_action_scope(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    """次の判断で省略を取り下げ、既存の中止・置換・再開・公開範囲の作用を維持する。"""
    payload, _policy, work_id = _omit_self_edit(tmp_path, monkeypatch)
    document = {
        "session_id": "self-edit",
        "action": action,
        "work_id": work_id,
        "input_id": "request",
        "quote": "READMEを編集する",
        "reason": "次の判断",
    }
    if action == "publish-scope":
        document.update(scope="commitしない", origin="instruction")
    termination_evidence.record_decision(document)
    decision, _body = termination_order_advisor.evaluate(json.dumps(payload))
    assert decision == ("approve" if action in {"cancel", "replace"} else "block")


def test_omission_exempts_only_selected_work(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """省略した作業と、別の起動不足が共存する場合は別作業だけを遮断する。"""
    payload, repository = _self_edit_setup(tmp_path)
    _record_self_edit(payload, repository / "README.md")
    first = termination_evidence.session_works(payload)[0][0]
    termination_evidence.record_decision(
        {
            "session_id": "self-edit",
            "action": "start",
            "input_id": "request",
            "quote": "READMEを編集する",
            "reason": "別作業を始める",
        }
    )
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "self-edit")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    policy = repository / "CLAUDE.md"
    policy.write_text("作業完了報告を省く。\n", encoding="utf-8")
    termination_evidence.observe_tool(
        json.dumps(
            {
                **payload,
                "tool_name": "Edit",
                "tool_use_id": "second-edit",
                "tool_input": {"file_path": str(repository / "README.md")},
                "tool_response": {"exit_code": 0},
            }
        ),
        after=True,
    )
    second = termination_evidence.session_works(payload)[-1][0]
    source = tmp_path / "omit-second.json"
    source.write_text(
        json.dumps(
            {
                "session_id": "self-edit",
                "action": "omit-completion",
                "work_id": second,
                "input_id": "request",
                "reason": "別作業の適用規範",
                "target_repo": str(repository),
                "policy_file": str(policy),
                "quote": policy.read_text(encoding="utf-8"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with contextlib.redirect_stdout(io.StringIO()):
        assert termination_evidence.main(["--decision-file", str(source)]) == 0
    decision, body = termination_order_advisor.evaluate(json.dumps(payload))
    assert decision == "block" and f"作業 {first}:" in body and f"作業 {second}:" not in body


@pytest.mark.parametrize("action", ["wait", "blocked"])
def test_omission_does_not_survive_wait_or_failure_resolution(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    """実在する待機・失敗を記録した後の続行は、古い省略を流用しない。"""
    payload, _policy, work_id = _omit_self_edit(tmp_path, monkeypatch)
    document = {"session_id": "self-edit", "work_id": work_id, "action": action, "reason": "実際の待機または失敗"}
    question: pathlib.Path | None = None
    if action == "wait":
        queue = tmp_path / "private-notes"
        inbox = queue / "inbox"
        inbox.mkdir(parents=True)
        monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(queue))
        question = inbox / "20261013-000000-001.md"
        text = "---\ntype: uwi\nsubmitter_session: self-edit\n---\n\n## 確認\n\n選択してください。\n\n## 回答\n"
        question.write_text(text, encoding="utf-8")
        document.update(uwi_file=str(question), quote=text)
    else:
        response = {"exit_code": 1, "stderr": "処理できない"}
        termination_evidence.observe_tool(
            json.dumps(
                {
                    **payload,
                    "tool_name": "Bash",
                    "tool_use_id": "failed-call",
                    "tool_input": {"command": "false"},
                    "tool_response": response,
                }
            ),
            after=True,
        )
        document.update(call_id="failed-call", quote=json.dumps(response, ensure_ascii=False, sort_keys=True))
    termination_evidence.record_decision(document)
    assert termination_order_advisor.evaluate(json.dumps(payload)) == ("approve", "")
    if action == "wait":
        assert question is not None
        question.write_text(question.read_text(encoding="utf-8") + "回答済み。\n", encoding="utf-8")
    else:
        termination_evidence.record_decision(
            {
                "session_id": "self-edit",
                "work_id": work_id,
                "action": "resume",
                "input_id": "request",
                "quote": "READMEを編集する",
                "reason": "失敗後に再開",
            }
        )
    assert termination_order_advisor.evaluate(json.dumps(payload))[0] == "block"


def test_omission_keeps_autonomous_exit_obligation(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """起動を省略した成果作業でも、process-loopの終了コマンドの義務は残る。"""
    payload, _policy, _work_id = _omit_self_edit(tmp_path, monkeypatch)
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", "self-edit")
    assert termination_order_advisor.evaluate(json.dumps(payload)) == ("approve", "")
    assert autonomous_exit.evaluate(json.dumps(payload))[0] == "block"


@pytest.mark.parametrize("codex", [False, True])
def test_public_stop_reviews_delivery_once_per_human_input(tmp_path, monkeypatch, codex):
    """両ホストで起動後の終了を一度止め、再入と回復では繰り返さず新入力で再び点検する。"""
    monkeypatch.setattr(stop, "CHECK_MODULE_NAMES", ("termination_order_advisor",))
    session_id = "review-codex" if codex else "review-claude"
    trace = _write_transcript(tmp_path, [])
    _human_input(session_id, trace, "request", "成果を仕上げる")
    _commit(session_id, trace)
    if codex:
        invocation = {
            "session_id": session_id,
            "turn_id": "request",
            "tool_name": "Bash",
            "tool_use_id": "report-read",
            "tool_input": {"command": "cat /plugin/skills/completion-report/SKILL.md"},
            "tool_response": {"stdout": "---\nname: completion-report\n---\n"},
        }
        termination_evidence.observe_tool(json.dumps(invocation), after=True)
    else:
        _start_skill(session_id, trace, "agent-toolkit:completion-report", "report-start")
    payload = json.loads(_payload(session_id, str(trace)))
    if codex:
        payload["turn_id"] = "request"
    first = stop.evaluate(json.dumps(payload))
    assert first["decision"] == "block"
    reason = first["reason"]
    assert isinstance(reason, str)
    assert "送達" in reason and "session-review" in reason and "bugfix" in reason
    assert "再出力せず終了" in reason
    payload["stop_hook_active"] = True
    assert "decision" not in stop.evaluate(json.dumps(payload))
    _human_input(session_id, trace, "generated", '<atk-auto source="stop" kind="continuation">続行する</atk-auto>')
    _commit(session_id, trace, "recovery-commit")
    assert "decision" not in stop.evaluate(json.dumps(payload))
    _human_input(session_id, trace, "correction", "報告漏れの是正も進めて")
    assert stop.evaluate(json.dumps(payload))["decision"] == "block"
    assert "decision" not in stop.evaluate(json.dumps(payload))


@pytest.mark.parametrize("case", ["delegated", "cancel", "replace", "blocked", "unreadable", "waiting"])
def test_started_completion_review_keeps_existing_exclusions(tmp_path, case):
    """起動済みでも委譲先・中止・置換・不成立・読取不能・正当な背景待機を再開しない。"""
    trace = _write_transcript(tmp_path, [])
    session_id = "review-excluded"
    _human_input(session_id, trace, "request", "成果作業を行う")
    _commit(session_id, trace)
    _start_skill(session_id, trace, "agent-toolkit:completion-report", "report-start")
    payload = json.loads(_payload(session_id, str(trace)))
    if case == "delegated":
        payload["agent_id"] = "child"
    elif case in {"cancel", "replace", "blocked"}:
        document = {
            "session_id": session_id,
            "work_id": termination_evidence.session_works(payload)[0][0],
            "action": case,
            "reason": "観測した除外理由",
            "input_id": "request",
            "quote": "成果作業を行う",
        }
        if case == "blocked":
            response = {"exit_code": 1, "stderr": "接続不能"}
            termination_evidence.observe_tool(
                json.dumps(
                    {
                        **payload,
                        "tool_name": "Bash",
                        "tool_use_id": "failure",
                        "tool_input": {"command": "false"},
                        "tool_response": response,
                    }
                ),
                after=True,
            )
            document.update(call_id="failure", quote=json.dumps(response, ensure_ascii=False, sort_keys=True))
        termination_evidence.record_decision(document)
    elif case == "unreadable":
        trace.unlink()
    else:
        _append_entries(trace, _background_bash_entries("wait-call", "wait-task"))
        payload["background_tasks"] = [{"id": "wait-task", "type": "shell"}]
    assert termination_order_advisor.evaluate(json.dumps(payload)) == ("approve", "")
