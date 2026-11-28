"""実検証失敗の案内を、報告書式やスキル取得の認定と切り離して確かめる。  pyfltr: allow-comment"""

import json
import pathlib

import pytest

from agent_toolkit._common.session_state import read_state
from agent_toolkit._hooks import bugfix_gate
from agent_toolkit._testing.pretooluse_support import codex_execution_payload, run_hook_with_notice_details


@pytest.mark.parametrize("actor", ["main", "lane"])
def test_verification_failure_is_notified_once_per_failure(tmp_path, actor):
    failure: dict = {
        "session_id": "verification-flow",
        "cwd": str(tmp_path),
        "agent_id": actor,
        "hook_event_name": "PostToolUse",
        "tool_use_id": "failed-1",
        "tool_name": "Bash",
        "tool_input": {"command": "uv run --frozen pytest sample_test.py"},
        "tool_response": {"exit_code": 1},
    }

    def warning(subject=actor):
        return bugfix_gate.verification_notice({**failure, "agent_id": subject}, "Read", {}, failure["session_id"])

    assert not warning()
    bugfix_gate.observe_verification(failure)
    assert not warning("other")
    notice = warning()
    assert "verification-bugfix" in notice
    assert "agent-basics:bugfix" in notice
    assert not warning()
    bugfix_gate.observe_verification(failure)
    assert not warning()
    bugfix_gate.observe_verification({**failure, "tool_use_id": "failed-2"})
    assert warning()


@pytest.mark.parametrize("command", ["rg needle missing.txt", "cat missing.txt"])
def test_nonverification_exit_is_not_a_verification_failure(command):
    bugfix_gate.observe_verification(
        {
            "session_id": "ordinary-operation",
            "tool_use_id": "failed-1",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_response": {"exit_code": 1},
        }
    )
    assert not bugfix_gate.verification_notice({}, "Read", {}, "ordinary-operation")


@pytest.mark.parametrize(
    ("code", "changed_field", "command", "expected"),
    [
        (1, None, "pytest sample_test.py", True),
        (0, None, "pytest sample_test.py", False),
        (None, None, "pytest sample_test.py", False),
        (1, "tool_use_id", "pytest sample_test.py", False),
        (1, "turn_id", "pytest sample_test.py", False),
        (1, "session_id", "pytest sample_test.py", False),
        (1, None, "cat missing.txt", False),
        (1, None, "pytest sample_test.py && cat missing.txt", False),
        (1, None, "cat missing.txt && pytest sample_test.py", False),
        (1, None, "pytest sample_test.py | cat missing.txt", False),
        (1, None, "pytest sample_test.py; cat missing.txt", False),
        (1, None, "! pytest sample_test.py", False),
        (1, None, "bash -c '! pytest sample_test.py'", False),
        (1, None, "pytest sample_test.py &", False),
        (1, None, "pytest sample_test.py 2>&1", True),
        (1, None, "pyfltr run sample_test.py", True),
        (1, None, "pytest sample_test.py > missing-dir/output.log", False),
        (1, None, "pytest sample_test.py < missing-file", False),
        (2, None, "make test artifact", False),
        (1, None, "uv run --frozen pytest sample_test.py", False),
        (1, None, "env pytest sample_test.py", False),
        (127, None, "./missing/pytest sample_test.py", False),
        (126, None, "./not-executable/pytest sample_test.py", False),
        (130, None, "pytest sample_test.py", False),
    ],
)
def test_codex_shell_verification_notifies_only_for_matching_failed_execution(tmp_path, code, changed_field, command, expected):
    post = {
        "session_id": "codex-verification",
        "turn_id": "verification-turn",
        "cwd": str(tmp_path),
        "tool_use_id": "verification-exec",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "hook_event_name": "PostToolUse",
        "tool_response": "exit_code: 1",
    }
    if code is not None:
        post = codex_execution_payload(post, tmp_path, exit_code=code)
    if changed_field is not None:
        post[changed_field] = "different"
    pre = {**post, "hook_event_name": "PreToolUse", "tool_use_id": "next-operation", "tool_input": {"command": "pwd"}}
    del pre["tool_response"]

    for warned in (expected, False):
        assert run_hook_with_notice_details("posttooluse", post).returncode == 0
        result = run_hook_with_notice_details("pretooluse", pre)
        assert result.returncode == 0
        assert ("verification-bugfix" in result.stdout) is warned


@pytest.mark.parametrize(
    ("command", "exit_code", "followed_by_output", "expected"),
    [
        ("pytest sample_test.py", 1, False, True),
        ("pytest sample_test.py", 0, False, False),
        ("pytest sample_test.py", 1, True, True),
        ("atk run-script wait-ci -- --baseline baseline.json", 1, False, True),
        ("atk run-script wait-ci -- --baseline baseline.json", 6, False, True),
        ("atk run-script wait-ci -- --baseline baseline.json", 2, False, False),
    ],
)
def test_verification_failure_uses_complete_codex_record_with_escaped_output(
    tmp_path, command, exit_code, followed_by_output, expected
):
    post = codex_execution_payload(
        {
            "session_id": "codex-long-verification",
            "turn_id": "long-verification-turn",
            "cwd": str(tmp_path),
            "tool_use_id": "long-verification-exec",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "hook_event_name": "PostToolUse",
            "tool_response": "終了状態を含まない出力本文",
        },
        tmp_path,
        exit_code=exit_code,
    )
    path = pathlib.Path(post["transcript_path"])
    lines = path.read_text(encoding="utf-8").splitlines()
    finished = json.loads(lines[-1])
    # pyfltr: allow-comment -- Codex 0.172.0の記録では64KiB以下の出力中のESCもJSONで1文字が6バイトになる。
    finished["payload"]["item"]["aggregated_output"] = "\x1b" * (40_000 if followed_by_output else 64 * 1024)
    lines[-1] = json.dumps(finished)
    if followed_by_output:
        other = finished["payload"]["item"]
        other.update(id="different-exec", exit_code=0, status="completed")
        lines.append(json.dumps(finished))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    pre = {**post, "hook_event_name": "PreToolUse", "tool_use_id": "next-operation", "tool_input": {"command": "pwd"}}
    del pre["tool_response"]
    for warned in (expected, False):
        assert run_hook_with_notice_details("posttooluse", post).returncode == 0
        result = run_hook_with_notice_details("pretooluse", pre)
        assert result.returncode == 0
        assert ("verification-bugfix" in result.stdout) is warned


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("code,pending", [(0, False), (1, True), (2, False), (3, False), (4, False), (6, True)])
def test_ci_failure_is_distinguished_from_waiting_and_missing_runs(tmp_path, code, pending, host):
    """CI失敗1/6だけを保持し、待機の終了を不具合の失敗にしない。"""
    payload = {
        "session_id": "ci-failure",
        "tool_use_id": "ci-1",
        "hook_event_name": "PostToolUseFailure" if code else "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "atk run-script wait-ci -- --baseline baseline.json"},
        "tool_response": {"exit_code": code},
    }
    if host == "codex":
        payload.update(turn_id="ci-turn", tool_response="CIの終了状態を確認した")
        payload = codex_execution_payload(payload, tmp_path, exit_code=code)
    bugfix_gate.observe_verification(payload)
    current = read_state("ci-failure").get(bugfix_gate.FAILURE_STATE_KEY, {}).get("main", {})
    assert bool(current.get("pending")) is pending
    notice = bugfix_gate.verification_notice(payload, "Bash", {"command": "git commit -m fix"}, "ci-failure")
    assert bool(notice) is pending


@pytest.mark.parametrize("field", ["tool_use_id", "turn_id", "session_id"])
def test_ci_failure_uses_only_the_same_native_execution(tmp_path, field):
    """別の実行・ターン・主体の失敗や本文中の終了コードを、現在のCI失敗に流用しない。"""
    payload = codex_execution_payload(
        {
            "session_id": "ci-failure",
            "turn_id": "ci-turn",
            "tool_use_id": "ci-1",
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Bash",
            "tool_input": {"command": "atk run-script wait-ci -- --baseline baseline.json"},
            "tool_response": "exit_code: 1",
        },
        tmp_path,
        exit_code=1,
    )
    payload[field] = "different"
    bugfix_gate.observe_verification(payload)
    assert not read_state(payload["session_id"]).get(bugfix_gate.FAILURE_STATE_KEY)
