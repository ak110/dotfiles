"""登録済みhookの出力とイベント別JSON Schemaの整合を検証する。"""

from __future__ import annotations

import json
import os
import pathlib
import shlex
import subprocess
import sys
from collections.abc import Callable

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.hook_output_contract import validate_hook_output
from agent_toolkit._testing.pretooluse_support import prepare_read_delivery

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[2]
_HOOK_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "hook.py"
_HOOKS_PATH = _PLUGIN_ROOT / "hooks" / "hooks.json"
# 子プロセスで起動するStopフックへはconftestの待機の差し替えが及ばないため、末尾を`end_turn`で終えるtranscriptを渡し、
# `transcript_scan.wait_for_end_turn`が上限まで待たずに判定へ進むようにする。
_FINISHED_TURN_ENTRIES = [
    {"type": "assistant", "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "完了しました。"}]}}
]

_FIXTURES: dict[tuple[str, str], tuple[str, bool]] = {
    ("SessionStart", "rules_context"): ("rules_context_session_start", True),
    ("SubagentStart", "rules_context"): ("rules_context_subagent_start", True),
    ("PreToolUse", "pretooluse"): ("pretooluse", True),
    ("PostToolUse", "posttooluse"): ("posttooluse", True),
    ("PostToolUseFailure", "posttooluse"): ("empty", False),
    ("PermissionDenied", "posttooluse"): ("empty", False),
    ("Stop", "stop"): ("stop", True),
    ("SubagentStop", "subagent_stop_advisor"): ("subagent_stop_advisor", True),
    ("SessionEnd", "session_end_cleanup"): ("empty", False),
    ("StopFailure", "stopfailure_notifier"): ("empty", False),
    ("PermissionRequest", "permissionrequest"): ("permissionrequest", True),
    ("UserPromptSubmit", "user_prompt_submit"): ("user_prompt_submit", True),
}


def _registered_hooks() -> set[tuple[str, str]]:
    manifest = json.loads(_HOOKS_PATH.read_text(encoding="utf-8"))
    return {
        (event_name, shlex.split(hook["command"].removesuffix(" || exit 1"))[-1])
        for event_name, matcher_groups in manifest["hooks"].items()
        for matcher_group in matcher_groups
        for hook in matcher_group["hooks"]
    }


def _build_fixture(
    event_name: str,
    fixture_name: str,
    tmp_path: pathlib.Path,
) -> tuple[dict[str, object], dict[str, str]]:
    session_id = f"contract-{fixture_name}"
    payload: dict[str, object] = {"hook_event_name": event_name, "session_id": session_id}
    env = os.environ.copy()
    env.update({"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)})

    if fixture_name == "rules_context_session_start":
        payload["source"] = "startup"
        env.pop("AGENT_TOOLKIT_DELEGATED_SESSION", None)
        env.pop("AGENT_TOOLKIT_OWNER_SESSION", None)
    elif fixture_name == "rules_context_subagent_start":
        payload["agent_id"] = "agent-contract"
        payload["agent_type"] = "general-purpose"
    elif fixture_name == "pretooluse":
        assert session_state.update_state(session_id, lambda _previous: {"plan_mode_skill_invoked": False})
        payload.update(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"cwd": str(tmp_path)},
            }
        )
    elif fixture_name == "posttooluse":
        payload.update(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__kill",
                "tool_input": {"session_id": "missing"},
                "tool_response": {},
            }
        )
    elif fixture_name == "stop":
        payload["background_tasks"] = []
        payload["transcript_path"] = str(_write_transcript(tmp_path, _FINISHED_TURN_ENTRIES))
        for name in (
            "AGENT_TOOLKIT_DELEGATED_SESSION",
            "AGENT_TOOLKIT_PROCESS_LOOP_SESSION",
            "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID",
        ):
            env.pop(name, None)
    elif fixture_name == "subagent_stop_advisor":
        payload["last_assistant_message"] = ""
    elif fixture_name == "user_prompt_submit":
        home = tmp_path / "home-prompt"
        plan = home / ".claude" / "plans" / "draft.md"
        plan.parent.mkdir(parents=True)
        plan.write_text("# 計画\n", encoding="utf-8")
        assert session_state.update_state(
            session_id, lambda _previous: {"current_plan_file_path": str(plan), "user_confirmation_skill_pending": True}
        )
        payload["prompt"] = "続行します"
        env.update({"HOME": str(home), "USERPROFILE": str(home)})
    return payload, env


@pytest.mark.parametrize(("event_name", "subcommand"), sorted(_FIXTURES))
def test_registered_hook_output_matches_event_contract(
    event_name: str,
    subcommand: str,
    tmp_path: pathlib.Path,
) -> None:
    fixture_name, expects_output = _FIXTURES[(event_name, subcommand)]
    payload, env = _build_fixture(event_name, fixture_name, tmp_path)
    if subcommand == "pretooluse":
        prepare_read_delivery(payload, env)
    result = subprocess.run(
        [sys.executable, str(_HOOK_SCRIPT), subcommand],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0
    assert bool(result.stdout.strip()) is expects_output
    if expects_output:
        output = json.loads(result.stdout)
        assert validate_hook_output(event_name, output) == []
        if event_name == "UserPromptSubmit":
            assert output["hookSpecificOutput"]["sessionTitle"] == "draft"
            assert not (tmp_path / "claude-agent-toolkit-session-title").exists()


@pytest.mark.parametrize(
    ("event_name", "output"),
    [
        ("PreToolUse", {"hookSpecificOutput": {"additionalContext": "警告"}}),
        (
            "PreToolUse",
            {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "警告"}},
        ),
        ("SessionEnd", {"hookSpecificOutput": {"hookEventName": "SessionEnd"}}),
        (
            "PermissionRequest",
            {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {}}},
        ),
        (
            "PermissionRequest",
            {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {"behavior": "allow", "message": "不可"},
                }
            },
        ),
        (
            "PermissionRequest",
            {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {"behavior": "allow", "interrupt": True},
                }
            },
        ),
        (
            "PermissionRequest",
            {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {"behavior": "deny", "updatedInput": {}},
                }
            },
        ),
        (
            "PermissionRequest",
            {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {"behavior": "deny", "updatedPermissions": []},
                }
            },
        ),
        (
            "PermissionRequest",
            {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": "allow"}},
        ),
        (
            "PermissionRequest",
            {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {"behavior": "allow", "updatedPermissions": [{}]},
                }
            },
        ),
        (
            "PermissionRequest",
            {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {
                        "behavior": "allow",
                        "updatedPermissions": [{"type": "addRules", "rules": [], "behavior": "allow"}],
                    },
                }
            },
        ),
        (
            "PermissionRequest",
            {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {
                        "behavior": "allow",
                        "updatedPermissions": [{"type": "addRules", "behavior": "allow", "destination": "session"}],
                    },
                }
            },
        ),
        (
            "PermissionRequest",
            {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {
                        "behavior": "allow",
                        "updatedPermissions": [{"type": "setMode", "destination": "session"}],
                    },
                }
            },
        ),
        (
            "PermissionRequest",
            {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {
                        "behavior": "allow",
                        "updatedPermissions": [{"type": "addDirectories", "destination": "session"}],
                    },
                }
            },
        ),
        (
            "PermissionRequest",
            {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {
                        "behavior": "allow",
                        "updatedPermissions": [
                            {
                                "type": "setMode",
                                "mode": "plan",
                                "destination": "session",
                                "rules": [],
                            }
                        ],
                    },
                }
            },
        ),
        (
            "PermissionRequest",
            {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {
                        "behavior": "allow",
                        "updatedPermissions": [
                            {
                                "type": "setMode",
                                "mode": "plan",
                                "destination": "unknown",
                            }
                        ],
                    },
                }
            },
        ),
        ("PostToolUse", {"decision": 1}),
        ("Stop", {"decision": "block"}),
        ("PreToolUse", {"unexpected": True}),
    ],
)
def test_invalid_output_reports_schema_violation(event_name: str, output: object) -> None:
    assert validate_hook_output(event_name, output)


@pytest.mark.parametrize(
    "decision",
    [
        {"behavior": "allow"},
        {"behavior": "deny", "message": "拒否", "interrupt": True},
        {
            "behavior": "allow",
            "updatedPermissions": [
                {
                    "type": "addRules",
                    "rules": [{"toolName": "Bash", "ruleContent": "git status"}],
                    "behavior": "allow",
                    "destination": "session",
                }
            ],
        },
        {
            "behavior": "allow",
            "updatedPermissions": [{"type": "setMode", "mode": "plan", "destination": "localSettings"}],
        },
        {
            "behavior": "allow",
            "updatedPermissions": [
                {
                    "type": "addDirectories",
                    "directories": ["/tmp/project"],
                    "destination": "projectSettings",
                }
            ],
        },
    ],
)
def test_permission_request_union_accepts_each_branch(decision: dict[str, object]) -> None:
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": decision,
        }
    }
    assert validate_hook_output("PermissionRequest", output) == []


@pytest.mark.parametrize("output", [{}, {"decision": "block", "reason": "残る報告を発話する"}])
def test_codex_stop_accepts_only_approval_or_continuation(output: dict[str, object]) -> None:
    assert validate_hook_output("Stop", output, host="codex") == []


@pytest.mark.parametrize(
    "output",
    [
        {"decision": "block"},
        {"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": "通知"}},
        {"systemMessage": "通知"},
        {"decision": "approve"},
    ],
)
def test_codex_stop_rejects_claude_only_output(output: dict[str, object]) -> None:
    assert validate_hook_output("Stop", output, host="codex")


def test_codex_stop_public_entry_returns_host_output(tmp_path: pathlib.Path) -> None:
    payload, environment = _build_fixture("Stop", "stop", tmp_path)
    environment.pop("AGENT_TOOLKIT_OWNER_SESSION", None)
    payload.update({"turn_id": "codex-turn", "last_assistant_message": "完了した。"})
    result = subprocess.run(
        [sys.executable, str(_HOOK_SCRIPT), "stop"],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    assert result.returncode == 0, result.stderr
    assert validate_hook_output("Stop", json.loads(result.stdout), host="codex") == []


@pytest.mark.parametrize(
    "specific",
    [
        {},
        {"additionalContext": "通知"},
        {"permissionDecision": "allow", "updatedInput": {}},
        {"permissionDecision": "allow", "updatedInput": {"command": "pwd"}, "additionalContext": "通知"},
        {"permissionDecision": "allow", "updatedInput": {}, "permissionDecisionReason": "書換え"},
        {"permissionDecision": "deny", "permissionDecisionReason": "拒否理由"},
    ],
)
def test_codex_pretooluse_accepts_supported_combinations(specific: dict[str, object]) -> None:
    output = {"hookSpecificOutput": {"hookEventName": "PreToolUse", **specific}}
    assert not validate_hook_output("PreToolUse", output, host="codex")
    assert not validate_hook_output("PreToolUse", {}, host="codex")


@pytest.mark.parametrize(
    "specific",
    [
        {"permissionDecision": "allow"},
        {"permissionDecision": "allow", "additionalContext": "警告"},
        {"permissionDecision": "ask"},
        {"permissionDecision": "defer"},
        {"permissionDecision": "approve"},
        {"permissionDecision": "invalid"},
        {"permissionDecision": "allow", "updatedInput": None},
        {"permissionDecision": "allow", "updatedInput": []},
        {"permissionDecision": "allow", "updatedInput": "入力"},
        {"updatedInput": {}},
        {"permissionDecision": "ask", "updatedInput": {}},
        {"permissionDecision": "deny", "updatedInput": {}, "permissionDecisionReason": "拒否理由"},
        {"permissionDecision": "deny"},
        {"permissionDecision": "deny", "permissionDecisionReason": " \n\t"},
        {"permissionDecisionReason": "決定値なし"},
    ],
)
def test_codex_pretooluse_rejects_unsupported_combinations(specific: dict[str, object]) -> None:
    output = {"hookSpecificOutput": {"hookEventName": "PreToolUse", **specific}}
    assert validate_hook_output("PreToolUse", output, host="codex")


@pytest.mark.parametrize("output", [{"continue": False}, {"stopReason": "停止"}, {"suppressOutput": True}])
def test_codex_pretooluse_rejects_unsupported_universal_fields(output: dict[str, object]) -> None:
    assert validate_hook_output("PreToolUse", output, host="codex")


@pytest.mark.parametrize("decision", ["allow", "deny", "ask", "defer"])
def test_claude_pretooluse_retains_permission_decisions(decision: str) -> None:
    output = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision}}
    assert not validate_hook_output("PreToolUse", output)


@pytest.mark.parametrize("host", ["claude-code", "codex"])
@pytest.mark.parametrize("scenario", ["pass", "warning", "block"])
def test_pretooluse_public_entry_returns_host_output(
    tmp_path: pathlib.Path, host: str, scenario: str, host_environ: Callable[[], dict[str, str]]
) -> None:
    """登録された公開hookで通過・追加文脈・終了2の遮断を確認する。"""
    payload, environment = _build_fixture("PreToolUse", "pretooluse", tmp_path)
    if host == "codex":
        manifest = json.loads((_PLUGIN_ROOT / "hooks/hooks.codex.json").read_text(encoding="utf-8"))
        assert any(
            shlex.split(hook_entry["command"].removesuffix(" || exit 1"))[-1] == "pretooluse"
            for group in manifest["hooks"]["PreToolUse"]
            for hook_entry in group["hooks"]
        )
        payload["turn_id"] = "codex-turn"
    if scenario == "warning":
        # 警告は再編集で是正できる入力として、計画本文の文字化けを使う。
        payload["tool_name"] = "ExitPlanMode"
        payload["tool_input"] = {"plan": "内容を確認" + chr(0xFFFD) + "してください。"}
    elif scenario == "block":
        payload["tool_name"] = "mcp__agents_server__kill"
        payload["tool_input"] = {"session_id": "missing"}
    prepare_read_delivery(payload, {**host_environ(), **environment})
    result = subprocess.run(
        [sys.executable, str(_HOOK_SCRIPT), "pretooluse"],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env={**host_environ(), **environment},
    )
    if scenario == "block":
        assert result.returncode == 2
        assert not result.stdout
        assert "cwd" in result.stderr and "次の操作:" in result.stderr
        return
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    output = json.loads(result.stdout) if result.stdout else {}
    assert not validate_hook_output("PreToolUse", output, host=host)
    if scenario == "warning":
        assert "U+FFFD" in output["hookSpecificOutput"]["additionalContext"]
        assert "permissionDecision" not in output["hookSpecificOutput"]
    elif host == "codex":
        assert "permissionDecision" not in output.get("hookSpecificOutput", {})
    else:
        assert output["hookSpecificOutput"]["permissionDecision"] == "allow"
