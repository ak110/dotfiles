"""UserPromptSubmitとPostToolUseを経由して自動名の保存・手動名の保持を確かめる。"""

import json
import pathlib

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._hooks import posttooluse, user_prompt_submit


@pytest.mark.parametrize(
    "skill,arguments,kind",
    [
        ("process-wi", "", "process-wi"),
        ("process-wi", "single-lane 追記", "single-lane"),
        ("process-wi", "all-repos", "all-repos"),
        ("process-wi", "single-lanex", "process-wi"),
        ("add-awi-by-user", "", "add-awi"),
    ],
)
@pytest.mark.parametrize("manual", [False, True])
def test_command_hooks_name_session(
    skill: str,
    arguments: str,
    kind: str,
    manual: bool,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """機能を省くFunctionイベントを使わず、種別・計画名と手動名を引き継ぐ。"""
    monkeypatch.delenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", raising=False)
    session = "title-test"
    session_state.update_state(session, lambda state: {**state, "current_plan_file_path": "C:\\plans\\18-1921_画像整理.md"})
    if not manual:
        posttooluse.main(
            json.dumps(
                {
                    "session_id": session,
                    "hook_event_name": "PostToolUse",
                    "tool_name": "Skill",
                    "tool_input": {"skill": f"agent-toolkit:{skill}", "args": arguments},
                }
            )
        )
        capsys.readouterr()
    # 一時ディレクトリが変わる再開でも種別を保持する。
    (tmp_path / "after-restart").mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path / "after-restart"))
    payload = {"session_id": session, "prompt": f"/agent-toolkit:{skill} {arguments}" if manual else "続ける"}
    user_prompt_submit.main(json.dumps(payload))
    title = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["sessionTitle"]
    # 複数リポジトリの統括は個別計画を連結せず、全体の処理範囲を示す固定名にする。
    assert title == ("process-wi all-repos" if kind == "all-repos" else f"{kind} 画像整理")
    user_prompt_submit.main(json.dumps({**payload, "session_title": "手動の名前"}))
    output = capsys.readouterr().out
    assert not output or "sessionTitle" not in json.loads(output)["hookSpecificOutput"]
    user_prompt_submit.main(json.dumps({**payload, "session_title": title}))
    output = capsys.readouterr().out
    assert not output or "sessionTitle" not in json.loads(output)["hookSpecificOutput"]


@pytest.mark.parametrize("loop", [False, True])
def test_plan_writes_update_automatic_title(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], loop: bool
) -> None:
    """Skillと計画Writeの公開hookを順に通し、自動名の更新と手動名の保持を確かめる。"""
    session = "plan-title"
    monkeypatch.delenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", raising=False)
    if loop:
        monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
        monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", session)
    posttooluse.main(
        json.dumps({"session_id": session, "tool_name": "Skill", "tool_input": {"skill": "agent-toolkit:add-awi-by-user"}})
    )
    capsys.readouterr()
    plans = pathlib.Path.home() / ".claude/plans"
    plans.mkdir(parents=True, exist_ok=True)
    current = ""
    for name in ("21-1234_最初の計画.md", "21-1235_更新した計画.md"):
        plan = plans / name
        plan.write_text("# 計画\n", encoding="utf-8")
        posttooluse.main(
            json.dumps(
                {
                    "session_id": session,
                    "cwd": str(tmp_path),
                    "tool_name": "Write",
                    "tool_input": {"file_path": str(plan), "content": "# 計画\n"},
                }
            )
        )
        capsys.readouterr()
        user_prompt_submit.main(json.dumps({"session_id": session, "prompt": "続ける", "session_title": current}))
        output = capsys.readouterr().out
        if loop and current:
            # 常駐統括の名前は計画が切り替わっても変わらないため、hookは名前を出力しない。
            assert not output or "sessionTitle" not in json.loads(output)["hookSpecificOutput"]
            continue
        current = json.loads(output)["hookSpecificOutput"]["sessionTitle"]
        assert current == ("process-wi all-repos" if loop else "add-awi " + name.split("_", 1)[1].removesuffix(".md"))
    user_prompt_submit.main(json.dumps({"session_id": session, "prompt": "続ける", "session_title": "手動名"}))
    output = capsys.readouterr().out
    assert not output or "sessionTitle" not in json.loads(output)["hookSpecificOutput"]


def _write_plan(session: str, cwd: pathlib.Path, name: str) -> None:
    """計画ファイルを公開PostToolUseのWriteとして記録する。"""
    plan = pathlib.Path.home() / ".claude/plans" / name
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("# 計画\n", encoding="utf-8")
    posttooluse.main(
        json.dumps(
            {
                "session_id": session,
                "cwd": str(cwd),
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": "# 計画\n"},
            }
        )
    )


def _hook_title(session: str, current: str, capsys: pytest.CaptureFixture[str]) -> str | None:
    """UserPromptSubmitの公開出力から自動名を返す。名前を変えない場合は`None`を返す。"""
    user_prompt_submit.main(json.dumps({"session_id": session, "prompt": "続ける", "session_title": current}))
    output = capsys.readouterr().out
    if not output:
        return None
    return json.loads(output)["hookSpecificOutput"].get("sessionTitle")


@pytest.mark.parametrize("route", ["explicit", "process-loop"])
def test_all_repos_title_stays_across_plan_switches(
    route: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """統括の会話は計画なし・pyfltr・lcと計画が切り替わっても、単一処理の種別が記録されても全体名を保つ。"""
    session = "all-repos-title"
    monkeypatch.delenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", raising=False)
    if route == "process-loop":
        monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
        monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", session)
    arguments = "all-repos" if route == "explicit" else ""
    posttooluse.main(
        json.dumps(
            {
                "session_id": session,
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:process-wi", "args": arguments},
            }
        )
    )
    capsys.readouterr()
    current = _hook_title(session, "", capsys)
    assert current == "process-wi all-repos"
    for name in ("31-1000_pyfltr.md", "31-1001_lc.md"):
        _write_plan(session, tmp_path, name)
        capsys.readouterr()
        # 名前が変わらない場合、hookは`sessionTitle`を出力しない。
        assert _hook_title(session, current, capsys) is None
    if route == "process-loop":
        # 常駐統括の中で単一処理の種別が記録されても、統括名を保つ。
        posttooluse.main(
            json.dumps(
                {
                    "session_id": session,
                    "tool_name": "Skill",
                    "tool_input": {"skill": "agent-toolkit:process-wi", "args": "single-lane"},
                }
            )
        )
        capsys.readouterr()
        assert _hook_title(session, current, capsys) is None
    # 手動名は統括でも上書きしない。
    assert _hook_title(session, "手動名", capsys) is None


def test_process_loop_child_keeps_plan_title(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """常駐統括の子セッションは統括名にならず、単一処理の種別と計画名を使う。"""
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", "loop-owner")
    session = "loop-child"
    posttooluse.main(
        json.dumps(
            {
                "session_id": session,
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:process-wi", "args": "single-lane"},
            }
        )
    )
    capsys.readouterr()
    _write_plan(session, tmp_path, "31-1002_pyfltr.md")
    capsys.readouterr()
    assert _hook_title(session, "", capsys) == "single-lane pyfltr"
