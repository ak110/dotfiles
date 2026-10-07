"""agent-toolkit/agent_toolkit/_hooks/pretooluse/operation_skills.py のテスト。

PreToolUseとPostToolUseの統合フックをsubprocessで起動し、セッション状態を経由した警告の有無を検証する。
"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit._hooks.pretooluse import operation_skills
from agent_toolkit._hooks.pretooluse.test_support_test import (
    _additional_context,
    _plan_file_state_env,
    _run,
    _run_posttooluse,
)

_SEARCH_SKILL = operation_skills.OPERATION_SKILLS[0].skill_name
_SEARCH_SKILL_MD = pathlib.Path(__file__).resolve().parents[3] / "skills" / "search" / "SKILL.md"
_WARN_OPENING = '<atk-auto source="pretooluse" kind="warn">'


def _bash(command: str, session_id: str, **extra: object) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": session_id, **extra}


def _record_skill(env: dict[str, str], session_id: str, skill: str, **extra: object) -> None:
    result = _run_posttooluse(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Skill",
            "tool_input": {"skill": skill},
            "session_id": session_id,
            **extra,
        },
        env,
    )
    assert result.returncode == 0


def _search_warning(payload: dict, env: dict[str, str]) -> str:
    result = _run(payload, env)
    assert result.returncode == 0
    return _additional_context(result)


@pytest.mark.parametrize(
    "payload",
    [
        _bash("rg -l x .", "s"),
        _bash("git grep -n -F x", "s"),
        _bash("git -C sub grep x", "s"),
        _bash("grep -rn x .", "s"),
        _bash("egrep --recursive x .", "s"),
        _bash("grep -e x -R .", "s"),
        _bash("find . -name x", "s"),
        _bash("cd sub && rg x", "s"),
        _bash("timeout 10 rg x", "s"),
        _bash("bash -c 'rg x | head'", "s"),
        {"tool_name": "Grep", "tool_input": {"pattern": "x"}, "session_id": "s"},
        {"tool_name": "Glob", "tool_input": {"pattern": "**/*.py"}, "session_id": "s"},
    ],
    ids=lambda payload: payload["tool_input"].get("command") or payload["tool_name"],
)
def test_search_without_skill_warns_once_per_context(tmp_path: pathlib.Path, payload: dict) -> None:
    env = _plan_file_state_env(tmp_path)
    first = _search_warning(payload, env)
    assert first.startswith(_WARN_OPENING)
    assert f"`{_SEARCH_SKILL}`" in first
    assert _search_warning(payload, env) == ""


@pytest.mark.parametrize("skill", [_SEARCH_SKILL, operation_skills.OPERATION_SKILLS[0].short_name])
def test_search_after_skill_use_does_not_warn(tmp_path: pathlib.Path, skill: str) -> None:
    env = _plan_file_state_env(tmp_path)
    _record_skill(env, "s", skill)
    assert _search_warning(_bash("rg x", "s"), env) == ""


@pytest.mark.parametrize(
    "command",
    [
        "cat f | rg x",
        "git log --oneline | grep -r x",
        "grep x file",
        "grep -er file",
        "grep -e -r file",
        "grep -- -r file",
        "echo rg x",
        "git log -S rg",
        "ls -la",
    ],
)
def test_non_search_commands_do_not_warn(tmp_path: pathlib.Path, command: str) -> None:
    assert _search_warning(_bash(command, "s"), _plan_file_state_env(tmp_path)) == ""


def test_subagent_context_is_separate_from_main(tmp_path: pathlib.Path) -> None:
    env = _plan_file_state_env(tmp_path)
    _record_skill(env, "s", _SEARCH_SKILL)
    assert _search_warning(_bash("rg x", "s", agent_id="sub-1"), env).startswith(_WARN_OPENING)
    assert _search_warning(_bash("rg x", "s"), env) == ""

    _record_skill(env, "t", _SEARCH_SKILL, agent_id="sub-2")
    assert _search_warning(_bash("rg x", "t", agent_id="sub-2"), env) == ""
    assert _search_warning(_bash("rg x", "t"), env).startswith(_WARN_OPENING)


def test_codex_search_warning_points_to_skill_md(tmp_path: pathlib.Path) -> None:
    env = _plan_file_state_env(tmp_path)
    payload = _bash("rg x", "codex-search", turn_id="turn-1")
    first = _search_warning(payload, env)
    assert first.startswith(_WARN_OPENING)
    assert str(_SEARCH_SKILL_MD) in first
    assert _search_warning(payload, env) == ""


def test_search_warning_joins_other_warnings(tmp_path: pathlib.Path) -> None:
    result = _run(_bash("git rev-parse --short A B; rg x", "s"), _plan_file_state_env(tmp_path))
    assert result.returncode == 0
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert context.count(_WARN_OPENING) == 2
    assert f"`{_SEARCH_SKILL}`" in context
    assert "`git rev-parse --short`" in context


_BUGFIX_SKILL = "agent-toolkit:bugfix"
_ROOT_CAUSE_BODY = "# 表題\n\n## 原因分析\n\n| 要因系統 | L1 現象 |\n"


def _bugfix_warnings(context: str) -> int:
    return context.count(f"`{_BUGFIX_SKILL}`を起動しないまま原因分析の記述を実行した")


@pytest.mark.parametrize(
    "payload",
    [
        {"tool_name": "Write", "tool_input": {"file_path": "/tmp/awi.md", "content": _ROOT_CAUSE_BODY}},
        {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/awi.md", "old_string": "x", "new_string": _ROOT_CAUSE_BODY}},
        {
            "tool_name": "MultiEdit",
            "tool_input": {"file_path": "/tmp/awi.md", "edits": [{"old_string": "x", "new_string": _ROOT_CAUSE_BODY}]},
        },
        {
            "tool_name": "apply_patch",
            "tool_input": {"command": "*** Begin Patch\n*** Add File: awi.md\n+# 表題\n+## 原因分析\n*** End Patch\n"},
            "turn_id": "turn-1",
        },
        {"tool_name": "Bash", "tool_input": {"command": f"cat > /tmp/awi.md <<'EOF'\n{_ROOT_CAUSE_BODY}EOF"}},
    ],
    ids=["write", "edit", "multiedit", "apply_patch", "bash"],
)
def test_root_cause_writing_without_bugfix_warns_once_per_context(tmp_path: pathlib.Path, payload: dict) -> None:
    """`agent-toolkit:bugfix`を起動しないまま`## 原因分析`の見出し行を書くと、文脈ごとに1回だけ警告する。

    起草で原因分析を書く時点に未起動を示す手掛かりが無いと、起動を求める条文が文脈にあっても起動されず、
    外部の挙動を一次資料で確かめる初動の基準が適用されないまま原因が確定する。
    """
    env = _plan_file_state_env(tmp_path)
    payload = {**payload, "session_id": "root-cause"}
    first = _run(payload, env)
    assert first.returncode == 0
    assert _bugfix_warnings(_additional_context(first)) == 1
    second = _run(payload, env)
    assert second.returncode == 0
    assert _bugfix_warnings(_additional_context(second)) == 0


@pytest.mark.parametrize("skill", [_BUGFIX_SKILL, "bugfix"])
def test_root_cause_writing_after_bugfix_does_not_warn(tmp_path: pathlib.Path, skill: str) -> None:
    env = _plan_file_state_env(tmp_path)
    _record_skill(env, "root-cause", skill)
    payload = {"tool_name": "Write", "tool_input": {"file_path": "/tmp/awi.md", "content": _ROOT_CAUSE_BODY}}
    result = _run({**payload, "session_id": "root-cause"}, env)
    assert result.returncode == 0
    assert _bugfix_warnings(_additional_context(result)) == 0


@pytest.mark.parametrize(
    "content",
    ["## 原因分析の根拠\n", "### 原因分析\n", "`## 原因分析`を置く\n", "本文だけ\n"],
)
def test_writing_without_root_cause_heading_does_not_warn(tmp_path: pathlib.Path, content: str) -> None:
    payload = {"tool_name": "Write", "tool_input": {"file_path": "/tmp/awi.md", "content": content}, "session_id": "s"}
    result = _run(payload, _plan_file_state_env(tmp_path))
    assert result.returncode == 0
    assert _bugfix_warnings(_additional_context(result)) == 0


_MANAGED_TEMP_SKILL = "agent-toolkit:managed-temp"


def _managed_temp_warnings(context: str) -> int:
    return context.count(f"`{_MANAGED_TEMP_SKILL}`を起動しないまま")


@pytest.mark.parametrize(
    "command",
    [
        "atk managed-temp create --prefix lane-02-grp-a",
        "/home/u/dotfiles/agent-toolkit/bin/atk managed-temp create --prefix x",
        "cd /repo && atk managed-temp create --prefix x",
        "for l in a b; do atk managed-temp create --prefix $l; done",
        "cd /repo && d=$(atk managed-temp create --prefix y)",
    ],
)
def test_managed_temp_create_without_skill_warns_once_per_context(tmp_path: pathlib.Path, command: str) -> None:
    """`agent-toolkit:managed-temp`を起動しないまま個別の領域を作成すると、文脈ごとに1回だけ警告する。

    作成の時点に未起動を示す手掛かりが無いと、置き場所の規定を読まずに参照される保存物を個別の領域へ置き、
    領域の回収で保存物が失われる。
    """
    env = _plan_file_state_env(tmp_path)
    first = _run(_bash(command, "managed-temp"), env)
    assert first.returncode == 0
    assert _managed_temp_warnings(_additional_context(first)) == 1
    second = _run(_bash(command, "managed-temp"), env)
    assert _managed_temp_warnings(_additional_context(second)) == 0


@pytest.mark.parametrize("skill", [_MANAGED_TEMP_SKILL, "managed-temp"])
def test_managed_temp_create_after_skill_does_not_warn(tmp_path: pathlib.Path, skill: str) -> None:
    env = _plan_file_state_env(tmp_path)
    _record_skill(env, "managed-temp", skill)
    result = _run(_bash("atk managed-temp create --prefix x", "managed-temp"), env)
    assert _managed_temp_warnings(_additional_context(result)) == 0


@pytest.mark.parametrize(
    "command",
    [
        "atk managed-temp cleanup --path /tmp/x",
        "atk managed-temp list",
        "git grep -F 'atk managed-temp create'",
        "printf '%s' 'atk managed-temp create'",
        "atk wi list",
    ],
)
def test_other_commands_do_not_warn_managed_temp(tmp_path: pathlib.Path, command: str) -> None:
    result = _run(_bash(command, "s"), _plan_file_state_env(tmp_path))
    assert result.returncode == 0
    assert _managed_temp_warnings(_additional_context(result)) == 0
