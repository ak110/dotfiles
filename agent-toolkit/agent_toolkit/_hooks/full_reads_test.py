"""スキルの入口の全文を返した読取を、そのスキルの起動として扱うことを公開hookの列で確かめる。

Codex・Antigravityはスキルを入口の読取で始め、Claude Codeでも`Read`で入口を読むことがある。
"""

import json
import pathlib

import pytest

from agent_toolkit._common.plugin_roots import resolve_plugin_root
from agent_toolkit._hooks import bugfix_gate, plugin_resources
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process

_SESSION = "full-reads"


@pytest.fixture(autouse=True)
def _main_session(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AGENT_TOOLKIT_DELEGATED_SESSION", "AGENT_TOOLKIT_OWNER_SESSION", "AGENT_TOOLKIT_HANDOFF_PATH"):
        monkeypatch.delenv(name, raising=False)


def _entry(skill: str) -> pathlib.Path:
    return resolve_plugin_root("agent-basics", plugin_resources.plugin_root()) / "skills" / skill / "SKILL.md"


def _read(kind: str, path: pathlib.Path, cwd: pathlib.Path) -> dict:
    """読取の種類ごとに、ホストが返す形のPostToolUseの入力を作る。"""
    text = path.read_text(encoding="utf-8")
    base = {"session_id": _SESSION, "cwd": str(cwd), "hook_event_name": "PostToolUse", "tool_use_id": f"read-{kind}"}
    if kind == "claude-read":
        response = {"type": "text", "file": {"filePath": str(path), "content": text}}
        return {**base, "tool_name": "Read", "tool_input": {"file_path": str(path)}, "tool_response": response}
    codex = {**base, "turn_id": "turn", "tool_name": "Bash"}
    if kind == "codex-sed":
        return {**codex, "tool_input": {"command": f"sed -n '1,2000p' {path}"}, "tool_response": text}
    if kind == "codex-partial":
        head = "\n".join(text.splitlines()[:3]) + "\n"
        return {**codex, "tool_input": {"command": f"sed -n '1,3p' {path}"}, "tool_response": head}
    assert kind == "codex-redirected"
    return {**codex, "tool_input": {"command": f"cat {path} > copy.md"}, "tool_response": ""}


def _fail_verification(cwd: pathlib.Path) -> None:
    failure = {
        "session_id": _SESSION,
        "cwd": str(cwd),
        "hook_event_name": "PostToolUse",
        "tool_use_id": "failed-1",
        "tool_name": "Bash",
        "tool_input": {"command": "uv run --frozen pytest sample_test.py"},
        "tool_response": {"exit_code": 1},
    }
    assert _run_hook_in_process("posttooluse", failure).returncode == 0


@pytest.mark.parametrize(
    ("kind", "counted"),
    [("claude-read", True), ("codex-sed", True), ("codex-partial", False), ("codex-redirected", False)],
)
def test_full_read_of_bugfix_entry_clears_verification_failure(tmp_path: pathlib.Path, kind: str, counted: bool) -> None:
    """検証の失敗の後、bugfixの入口の全文を返した読取は起動として印を解除し、途中までの読取と転送は解除しない。"""
    _fail_verification(tmp_path)
    assert _run_hook_in_process("posttooluse", _read(kind, _entry("bugfix"), tmp_path)).returncode == 0
    pending = bugfix_gate.read_state(_SESSION)[bugfix_gate.FAILURE_STATE_KEY]["main"]["pending"]
    assert pending is not counted


@pytest.mark.parametrize(("kind", "counted"), [("claude-read", True), ("codex-sed", True), ("codex-partial", False)])
def test_full_read_of_skill_entry_stops_its_notice(tmp_path: pathlib.Path, kind: str, counted: bool) -> None:
    """検索のスキルの入口の全文を返した読取の後は、検索の操作へ起動の案内を出さない。"""
    assert _run_hook_in_process("posttooluse", _read(kind, _entry("search"), tmp_path)).returncode == 0
    search = {"session_id": _SESSION, "cwd": str(tmp_path), "tool_name": "Bash", "tool_input": {"command": "rg needle ."}}
    if kind != "claude-read":
        search["turn_id"] = "turn"
    output = _run_hook_in_process("pretooluse", search).stdout
    context = json.loads(output).get("hookSpecificOutput", {}).get("additionalContext", "") if output else ""
    assert ("agent-basics:search" in context) is not counted
