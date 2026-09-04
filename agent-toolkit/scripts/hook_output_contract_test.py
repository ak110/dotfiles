"""登録済みhookの出力とイベント別JSON Schemaの整合を検証する。"""

from __future__ import annotations

import json
import os
import pathlib
import shlex
import subprocess
import sys

import _managed_temp
import pytest
from _hook_output_contract import HOOK_OUTPUT_SCHEMAS, validate_hook_output
from _test_helpers import SESSION_STATE_FILENAME_TEMPLATE, _write_transcript

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[1]
_HOOK_SCRIPT = pathlib.Path(__file__).resolve().parent / "hook.py"
_HOOKS_PATH = _PLUGIN_ROOT / "hooks" / "hooks.json"

_FIXTURES: dict[tuple[str, str], tuple[str, bool]] = {
    ("PreToolUse", "pretooluse"): ("pretooluse", True),
    ("PostToolUse", "posttooluse"): ("posttooluse", True),
    ("PostToolUseFailure", "posttooluse"): ("empty", False),
    ("PermissionDenied", "posttooluse"): ("empty", False),
    ("Stop", "stop"): ("stop", True),
    ("SubagentStop", "subagent_stop_advisor"): ("subagent_stop_advisor", True),
    ("SessionEnd", "session_end_cleanup"): ("empty", False),
    ("StopFailure", "stopfailure_notifier"): ("empty", False),
    ("PermissionRequest", "permissionrequest"): ("permissionrequest", True),
    ("PermissionRequest", "permissionrequest_codex"): ("permissionrequest_codex", True),
    ("UserPromptSubmit", "user_prompt_submit"): ("user_prompt_submit", True),
}


def _registered_hooks() -> set[tuple[str, str]]:
    manifest = json.loads(_HOOKS_PATH.read_text(encoding="utf-8"))
    return {
        (event_name, shlex.split(hook["command"])[-1])
        for event_name, matcher_groups in manifest["hooks"].items()
        for matcher_group in matcher_groups
        for hook in matcher_group["hooks"]
    }


def _state_path(tmp_path: pathlib.Path, session_id: str) -> pathlib.Path:
    return tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=session_id)


def _build_fixture(
    event_name: str,
    fixture_name: str,
    tmp_path: pathlib.Path,
) -> tuple[dict[str, object], dict[str, str], pathlib.Path | None]:
    session_id = f"contract-{fixture_name}"
    payload: dict[str, object] = {"hook_event_name": event_name, "session_id": session_id}
    env = os.environ.copy()
    env.update({"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)})

    managed_temp: pathlib.Path | None = None

    if fixture_name == "pretooluse":
        payload.update(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"cwd": str(tmp_path)},
            }
        )
    elif fixture_name == "posttooluse":
        payload["tool_name"] = "mcp__plugin_agent-toolkit_agents_server__wait"
    elif fixture_name == "stop":
        payload["background_tasks"] = []
        payload["transcript_path"] = str(_write_transcript(tmp_path, []))
        for name in (
            "AGENT_TOOLKIT_DELEGATED_SESSION",
            "AGENT_TOOLKIT_PROCESS_LOOP_SESSION",
            "DOTFILES_AUTONOMOUS_EXIT_REQUIRED",
        ):
            env.pop(name, None)
    elif fixture_name == "subagent_stop_advisor":
        payload["last_assistant_message"] = ""
    elif fixture_name == "permissionrequest_codex":
        managed_root = tmp_path / "managed temp"
        managed_root.mkdir()
        if os.name == "nt":
            _managed_temp._windows_secure_path(  # pylint: disable=protected-access
                managed_root,
                directory=True,
            )
        target = _managed_temp.create_managed_temp("hook-test", root=managed_root)
        helper = _PLUGIN_ROOT / "scripts" / "_managed_temp.py"
        command_tokens = [
            "uv",
            "run",
            "--no-project",
            "--script",
            str(helper),
            "cleanup",
            "--path",
            str(target),
        ]
        command = subprocess.list2cmdline(command_tokens) if os.name == "nt" else shlex.join(command_tokens)
        payload.update(
            {
                "tool_name": "Bash",
                "tool_input": {"command": command},
            }
        )
        env["PLUGIN_ROOT"] = str(_PLUGIN_ROOT)

        managed_temp = target
    elif fixture_name == "user_prompt_submit":
        home = tmp_path / "home-prompt"
        plan = home / ".claude" / "plans" / "draft.md"
        plan.parent.mkdir(parents=True)
        plan.write_text("# 計画\n", encoding="utf-8")
        _state_path(tmp_path, session_id).write_text(
            json.dumps({"current_plan_file_path": str(plan)}),
            encoding="utf-8",
        )
        payload["prompt"] = "続行します"
        env.update({"HOME": str(home), "USERPROFILE": str(home)})
    return payload, env, managed_temp


def test_fixture_table_covers_every_registered_hook() -> None:
    assert set(_FIXTURES) == _registered_hooks()
    assert set(HOOK_OUTPUT_SCHEMAS) == {event_name for event_name, _ in _registered_hooks()}


@pytest.mark.parametrize(("event_name", "subcommand"), sorted(_FIXTURES))
def test_registered_hook_output_matches_event_contract(
    event_name: str,
    subcommand: str,
    tmp_path: pathlib.Path,
) -> None:
    fixture_name, expects_output = _FIXTURES[(event_name, subcommand)]
    payload, env, managed_temp = _build_fixture(event_name, fixture_name, tmp_path)
    try:
        result = subprocess.run(
            [sys.executable, str(_HOOK_SCRIPT), subcommand],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
    finally:
        if managed_temp is not None:
            _managed_temp.cleanup_managed_temp(managed_temp)

    assert result.returncode == 0
    assert bool(result.stdout.strip()) is expects_output
    if expects_output:
        output = json.loads(result.stdout)
        assert validate_hook_output(event_name, output) == []


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
