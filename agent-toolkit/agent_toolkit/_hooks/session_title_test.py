"""UserPromptSubmitとPostToolUseを経由して自動名の保存・手動名の保持を確かめる。"""

import json
import pathlib

import pytest

from agent_toolkit._common import session_state, skill_modes
from agent_toolkit._hooks import posttooluse, rules_context, user_prompt_submit
from agent_toolkit._hooks.pretooluse import operation_skills


@pytest.fixture(autouse=True)
def _no_material_declarations(monkeypatch: pytest.MonkeyPatch) -> None:
    """会話名の遷移を検査し、独立した資料配送の全宣言を解析しない。"""
    monkeypatch.setattr(operation_skills, "notices", lambda *_args, **_kwargs: [])


@pytest.mark.parametrize("codex", [False, True])
def test_consultation_history_survives_other_skill(codex, capsys):
    """明示起動を両ホストで記録し、後の表示種別変更でも履歴を保持する。"""
    session = "consultation-history"
    payload = {"session_id": session, "prompt": "/agent-toolkit:atk-mode add-awi-by-user"}
    if codex:
        payload["turn_id"] = "turn"
        payload["prompt"] = "$agent-toolkit:atk-mode add-awi-by-user"
    user_prompt_submit.main(json.dumps(payload))
    capsys.readouterr()
    assert session_state.read_state(session)["add_awi_skill_invoked"] is True
    posttooluse.main(json.dumps({"session_id": session, "tool_name": "Skill", "tool_input": {"skill": skill_modes.PROCESS_WI}}))
    capsys.readouterr()
    assert session_state.read_state(session)["session_title_kind"] == "process-wi"
    assert session_state.read_state(session)["add_awi_skill_invoked"] is True


@pytest.mark.parametrize(
    "skill,arguments,kind",
    [
        ("process-wi", "", "process-wi"),
        ("process-wi", "single-lane 追記", "single-lane"),
        ("process-wi", "all-repos", "all-repos"),
        ("process-wi", "single-lanex", "process-wi"),
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
        # 子セッションの種別は起動入力から記録する（エージェントによる入口の起動からは記録しない）。
        launch = {"source": "run-skill", "skill": f"agent-toolkit:{skill}", "arguments": arguments}
        monkeypatch.setenv("AGENT_TOOLKIT_SESSION_START_INPUT", json.dumps(launch, ensure_ascii=False))
        payload = {"hook_event_name": "SessionStart", "source": "startup", "session_id": session}
        assert rules_context.main(json.dumps(payload), host="claude") == 0
        capsys.readouterr()
        monkeypatch.delenv("AGENT_TOOLKIT_SESSION_START_INPUT")
    # 一時ディレクトリが変わる再開でも種別を保持する。
    (tmp_path / "after-restart").mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path / "after-restart"))
    payload = {"session_id": session, "prompt": f"/atk-mode {skill} {arguments}" if manual else "続ける"}
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
    user_prompt_submit.main(json.dumps({"session_id": session, "prompt": "/atk-mode add-awi-by-user"}))
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
                "tool_input": {"skill": skill_modes.PROCESS_WI, "args": arguments},
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
                    "tool_input": {"skill": skill_modes.PROCESS_WI, "args": "single-lane"},
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
                "tool_input": {"skill": skill_modes.PROCESS_WI, "args": "single-lane"},
            }
        )
    )
    capsys.readouterr()
    _write_plan(session, tmp_path, "31-1002_pyfltr.md")
    capsys.readouterr()
    assert _hook_title(session, "", capsys) == "single-lane pyfltr"


@pytest.mark.parametrize(
    "prompt,codex",
    [
        ("/atk-mode add-awi-by-user https://example.com/a", False),
        ("/agent-toolkit:atk-mode add-awi-by-user", False),
        ("$atk-mode add-awi-by-user 入力", True),
    ],
)
def test_user_typed_entry_starts_submission(prompt, codex, capsys):
    """ユーザーが入口へAWI投入を名指しした入力は、移行前の名前の起動と同じ投入状態を立てる。"""
    session = f"entry-submission-{codex}-{len(prompt)}"
    payload = {"session_id": session, "prompt": prompt}
    if codex:
        payload["turn_id"] = "turn"
    user_prompt_submit.main(json.dumps(payload))
    capsys.readouterr()
    state = session_state.read_state(session)
    assert state["add_awi_skill_invoked"] is True
    assert codex or state["session_title_kind"] == "add-awi"


def test_user_typed_entry_starts_autonomous_work(capsys):
    """入口へ`process-wi`を名指しした入力は、モードを先頭語から読んで自律モードの状態を立てる。"""
    session = "entry-process-wi"
    user_prompt_submit.main(json.dumps({"session_id": session, "prompt": "/atk-mode process-wi single-lane 追記"}))
    capsys.readouterr()
    state = session_state.read_state(session)
    assert state["process_wi_skill_invoked"] is True
    assert state["session_title_kind"] == "single-lane"
    assert "add_awi_skill_invoked" not in state


@pytest.mark.parametrize("arguments", ["add-awi-by-user https://example.com/a", "process-wi all-repos"])
@pytest.mark.parametrize("skill", ["agent-toolkit:atk-mode", "atk-mode"])
def test_agent_invoked_entry_grants_no_state(skill, arguments, capsys):
    """エージェントが入口を起動しても、引数の作業名から投入状態と自律モードの状態は立たない。"""
    session = f"entry-agent-{skill}-{arguments.split()[0]}"
    posttooluse.main(
        json.dumps({"session_id": session, "tool_name": "Skill", "tool_input": {"skill": skill, "args": arguments}})
    )
    capsys.readouterr()
    state = session_state.read_state(session)
    assert not state.get("add_awi_skill_invoked")
    assert not state.get("process_wi_skill_invoked")
    assert "session_title_kind" not in state


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize("prefix", ["", "agent-toolkit:"])
def test_retired_skill_name_starts_nothing(prefix, codex, capsys):
    """スキルを廃止した作業は、移行前の名前の入力とSkill起動では始まらない。"""
    name = prefix + "add-awi-by-user"
    session = f"retired-{codex}-{len(name)}"
    payload = {"session_id": session, "prompt": ("$" if codex else "/") + name}
    if codex:
        payload["turn_id"] = "turn"
    user_prompt_submit.main(json.dumps(payload))
    posttooluse.main(json.dumps({"session_id": session, "tool_name": "Skill", "tool_input": {"skill": name}}))
    capsys.readouterr()
    state = session_state.read_state(session)
    assert not state.get("add_awi_skill_invoked")
    assert "session_title_kind" not in state


def test_delegated_first_input_grants_no_state(monkeypatch, capsys):
    """委譲先の最初の入力は委譲元のエージェントが書くため、入口の形でも状態を立てない。"""
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    session = "entry-delegated"
    user_prompt_submit.main(json.dumps({"session_id": session, "prompt": "/atk-mode add-awi-by-user"}))
    capsys.readouterr()
    state = session_state.read_state(session)
    assert not state.get("add_awi_skill_invoked")
    assert not state.get("process_wi_skill_invoked")


def test_entry_without_named_work_starts_nothing(capsys):
    """作業の指定が無い入口の起動は一般の作業であり、作業別の状態を立てない。"""
    session = "entry-general"
    user_prompt_submit.main(json.dumps({"session_id": session, "prompt": "/atk-mode この不具合を直して"}))
    capsys.readouterr()
    state = session_state.read_state(session)
    assert not state.get("add_awi_skill_invoked")
    assert not state.get("process_wi_skill_invoked")
