# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""dispatchの契約テスト。"""

import json
import pathlib
import shlex

import pytest

from agent_toolkit._common import read_triggers
from agent_toolkit._hooks.pretooluse import agent_checks
from agent_toolkit._hooks.pretooluse import dispatch as pretooluse
from agent_toolkit._testing import pretooluse_support
from agent_toolkit._testing.dispatch_support import _assert_plain_pass, _original_record
from agent_toolkit._testing.dispatch_support import _existing_work_contract as _existing_work_contract
from agent_toolkit._testing.helpers import auto_message_opening_attributes
from agent_toolkit._testing.hook_output_contract import validate_hook_output
from agent_toolkit._testing.pretooluse_support import _plan_file_state_env, _write_session_state
from agent_toolkit._testing.pretooluse_support import run_after_read_delivery as _run


def test_preparation_scope_refreshes_changed_read_table(tmp_path) -> None:
    """反復準備の共有を終了した後は、資料の参照先変更を新しく解析する。"""
    source = tmp_path / "SKILL.md"
    first, second = tmp_path / "first.md", tmp_path / "second.md"
    for target in (first, second):
        target.write_text("# 判断基準\n本文\n", encoding="utf-8")
    table = (
        "| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n"
        "| --- | --- | --- | --- |\n"
        "| 判断する前 | `{name}` | 判断 | `body-writing` |\n"
    )
    source.write_text(table.format(name=first.name), encoding="utf-8")
    with pretooluse_support.read_delivery_preparation_scope():
        for _ in range(2):
            rows = read_triggers.parse_tables(source, tmp_path, strict=True)
            assert rows[0].documents == (first,)
    source.write_text(table.format(name=second.name), encoding="utf-8")
    with pretooluse_support.read_delivery_preparation_scope():
        rows = read_triggers.parse_tables(source, tmp_path, strict=True)
        assert rows[0].documents == (second,)


@pytest.mark.parametrize("host", ["claude-code", "codex"])
@pytest.mark.parametrize("tool_name", sorted(agent_checks.AGENTS_SERVER_HOOK_TOOL_NAMES))
def test_agents_server_pass_uses_host_output_contract(tmp_path: pathlib.Path, host: str, tool_name: str) -> None:
    """全agents_server通過の生成箇所が、ホスト別の公開hook契約へ到達する。"""
    _write_session_state(
        tmp_path,
        "host-pass",
        {
            "agents_server_cwd_by_session": {"remote": str(tmp_path)},
        },
    )
    result = _run(
        {
            "tool_name": tool_name,
            "tool_input": {"session_id": "remote", "prompt": "続行", "cwd": str(tmp_path)},
            "session_id": "host-pass",
            **({"turn_id": "codex-turn"} if host == "codex" else {}),
        },
        env_overrides=_plan_file_state_env(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    output = json.loads(result.stdout) if result.stdout else {}
    assert not validate_hook_output("PreToolUse", output, host=host)
    if host == "codex":
        assert not output
    else:
        assert output["hookSpecificOutput"]["permissionDecision"] == "allow"


@pytest.mark.parametrize("host", ["claude-code", "codex"])
@pytest.mark.parametrize(
    "tool_input",
    [
        {"session_id": "remote", "prompt": "続行する"},
        {"session_id": "remote", "instruction": "保存開始", "extra_params": {"要求識別子": "r-1"}},
        {"session_id": "remote", "instruction": "再レビューせよ", "extra_params": {"round": "2"}},
    ],
    ids=["free-prompt", "save-start", "rereview"],
)
def test_continuation_inputs_pass_without_input_warning(tmp_path: pathlib.Path, host: str, tool_input: dict) -> None:
    """保存済みcwdを持つ対象への自由本文と定型継続は、本文不足の警告を伴わずに通過する。"""
    _write_session_state(
        tmp_path,
        "host-continuation",
        {
            "agents_server_cwd_by_session": {"remote": str(tmp_path)},
        },
    )
    result = _run(
        {
            "tool_name": "mcp__agents_server__send_message",
            "tool_input": tool_input,
            "session_id": "host-continuation",
            **({"turn_id": "codex-turn"} if host == "codex" else {}),
        },
        env_overrides=_plan_file_state_env(tmp_path),
    )
    _assert_plain_pass(result, host)


@pytest.mark.parametrize("host", ["claude-code", "codex"])
@pytest.mark.parametrize(
    "operation,tool_input",
    [
        ("send_message", {"session_id": "remote"}),
        ("send_message", {"prompt": "続行"}),
        ("kill", {}),
    ],
)
def test_continuation_format_errors_pass_to_tool(tmp_path: pathlib.Path, host: str, operation: str, tool_input: dict) -> None:
    """入力形式の不成立は追加文脈なしで通過し、原因と次の操作をツールの入力検証へ委ねる。"""
    _write_session_state(
        tmp_path,
        "host-format",
        {
            "agents_server_cwd_by_session": {"remote": str(tmp_path)},
        },
    )
    result = _run(
        {
            "tool_name": f"mcp__agents_server__{operation}",
            "tool_input": tool_input,
            "session_id": "host-format",
            **({"turn_id": "codex-turn"} if host == "codex" else {}),
        },
        env_overrides=_plan_file_state_env(tmp_path),
    )
    _assert_plain_pass(result, host)


@pytest.mark.parametrize("root_kind", ["claude", "codex"])
@pytest.mark.parametrize("codex", [False, True], ids=["claude-hook", "codex-hook"])
@pytest.mark.parametrize(
    "operation",
    [
        "read",
        "grep-directory",
        "cat",
        "sed",
        "awk",
        "rg",
        "grep-pattern-option",
        "head",
        "sed-without-terminator",
        "relative",
        "substitution",
        "inline-open",
        "inline-path",
        "inline-variable",
        "script-argument",
    ],
)
def test_original_record_access_is_guided_through_shared_pretooluse(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    root_kind: str,
    codex: bool,
    operation: str,
) -> None:
    """両ホストの各公開ツールから、設定ホームと静的な読取を既存の証拠照会へ接続する。"""
    path = _original_record(tmp_path, root_kind, monkeypatch)
    quoted = shlex.quote(str(path))
    commands = {
        "cat": f"cat -- {quoted} | cat",
        "sed": f"sed -n '1,2p' -- {quoted}",
        "awk": f"awk '{{print}}' {quoted}",
        "rg": f"rg -n -F -- assistant {quoted}",
        "grep-pattern-option": f"grep -e assistant -- {quoted}",
        "head": f"head -n 2 {quoted}",
        "sed-without-terminator": f"sed -n '3,$p' {quoted}",
        "relative": f"cd {shlex.quote(str(path.parent))} && cat {shlex.quote(path.name)}",
        "substitution": f"printf '%s' \"$(cat {quoted})\"",
        "inline-open": "python3 -c " + shlex.quote(f"open({str(path)!r}).read()"),
        "inline-path": "python3 -c " + shlex.quote(f"from pathlib import Path; Path({str(path)!r}).read_text()"),
        "inline-variable": "uv run --frozen python -c " + shlex.quote(f"p={str(path)!r}; open(p).read()"),
        "script-argument": f"python3 analyze.py {quoted}",
    }
    tool_name = "Read" if operation == "read" else "Grep" if operation == "grep-directory" else "Bash"
    tool_input = (
        {"file_path": str(path)}
        if tool_name == "Read"
        else {"pattern": "assistant", "path": str(path.parent)}
        if tool_name == "Grep"
        else {"command": commands[operation]}
    )
    payload = {
        "tool_name": tool_name,
        "tool_input": tool_input,
        "cwd": str(tmp_path),
        **({"turn_id": "codex-turn"} if codex else {}),
    }
    assert pretooluse.main(json.dumps(payload)) == 0
    captured = capsys.readouterr()
    assert not captured.err
    context = json.loads(captured.out)["hookSpecificOutput"]["additionalContext"]
    assert auto_message_opening_attributes(context) == {"source": "pretooluse", "kind": "warn"}
    assert "session-review-evidence" in context and "session-records.md" in context
    assert "--transcript" in context and "session-records-query.md" in context


@pytest.mark.parametrize("codex", [False, True], ids=["claude-hook", "codex-hook"])
@pytest.mark.parametrize(
    "operation",
    [
        "attributes",
        "find",
        "mention",
        "search-pattern",
        "search-option",
        "dedicated",
        "inline-mention",
        "inline-attributes",
        "inline-unexecuted",
        "inline-string-method",
        "script-without-input",
        "ordinary",
        "derived",
        "unknown",
    ],
)
def test_non_original_body_access_does_not_receive_record_warning(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    codex: bool,
    operation: str,
) -> None:
    """属性・言及・正式照会・派生物・一般JSONL・未知の入力を原記録参照へ推定しない。"""
    path = _original_record(tmp_path, "claude", monkeypatch)
    quoted = shlex.quote(str(path))
    commands = {
        "attributes": f"ls {quoted}",
        "find": f"find {shlex.quote(str(path.parent))} -name '*.jsonl'",
        "mention": f"printf '%s' {quoted}",
        "search-pattern": f"rg -F -- {quoted} ordinary.txt",
        "search-option": f"rg --glob {quoted} -- assistant ordinary.txt",
        "dedicated": f"atk run-script session-review-evidence -- --transcript {quoted} --fixed-string assistant",
        "inline-mention": "python3 -c " + shlex.quote(f"print({str(path)!r})"),
        "inline-attributes": "python3 -c " + shlex.quote(f"from pathlib import Path; Path({str(path)!r}).exists()"),
        "inline-unexecuted": "python3 -c " + shlex.quote(f"def example(): open({str(path)!r}).read()"),
        "inline-string-method": "python3 -c " + shlex.quote(f"p={str(path)!r}; p.read_text()"),
        "script-without-input": "python3 analyze.py",
        "ordinary": f"cat {shlex.quote(str(tmp_path / 'ordinary.jsonl'))}",
        "derived": f"cat {shlex.quote(str(tmp_path / 'bundle/conversation.jsonl'))}",
        "unknown": "cat $RECORD",
    }
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": commands[operation]},
        "cwd": str(tmp_path),
        **({"turn_id": "codex-turn"} if codex else {}),
    }
    assert pretooluse.main(json.dumps(payload)) == 0
    assert "session-review-evidence" not in capsys.readouterr().out
