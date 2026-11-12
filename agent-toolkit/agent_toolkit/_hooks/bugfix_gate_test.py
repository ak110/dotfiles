"""欠陥指摘のターンで`agent-basics:bugfix`を起動せずに報告する操作を、PreToolUseとStopが同じ判定で遮断することを確かめる。"""

import json
import pathlib

import pytest

from agent_toolkit._hooks import bugfix_gate, stop
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process

_SEND_TO_USER = "mcp__plugin_agent-toolkit_agent-toolkit__send_to_user"


def _human(text: str) -> dict:
    return {"type": "user", "origin": {"kind": "human"}, "message": {"content": text}}


def _tool_use(name: str, tool_input: dict) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": name, "input": tool_input}]}}


_BUGFIX = _tool_use("Skill", {"skill": "agent-basics:bugfix"})


@pytest.fixture(autouse=True)
def _main_session(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AGENT_TOOLKIT_DELEGATED_SESSION", "AGENT_TOOLKIT_OWNER_SESSION", "AGENT_TOOLKIT_HANDOFF_PATH"):
        monkeypatch.delenv(name, raising=False)


def _payload(entries: list[dict], tmp_path: pathlib.Path, **extra: object) -> dict:
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries), encoding="utf-8")
    return {"session_id": "bugfix-gate", "transcript_path": str(path), "cwd": str(tmp_path), **extra}


def _pretooluse_denied(payload: dict) -> bool:
    result = _run_hook_in_process("pretooluse", {**payload, "tool_name": _SEND_TO_USER, "tool_input": {"message": "原因は…"}})
    assert result.returncode == 0 and not result.stderr
    output = json.loads(result.stdout)["hookSpecificOutput"] if result.stdout.strip() else {}
    if output.get("permissionDecision") == "deny":
        assert "agent-basics:bugfix" in output["permissionDecisionReason"]
        return True
    return False


@pytest.mark.parametrize(
    "prompt,expected",
    [
        ("ここの処理がバグっている", True),
        ("この出力は間違ってるよ", True),
        ("the output is broken", True),
        ("進捗を教えて", False),
        ("貼り付けたコード:\n```\n# バグ\n```\n進捗は？", False),
        ("`バグ`という語の意味を教えて", False),
        ("> おかしい\n続けて", False),
    ],
)
def test_defect_report_judgement(prompt: str, expected: bool) -> None:
    """欠陥指摘の語を本文から判定し、貼り付けた素材（コードブロック・インラインコード・引用）の中の語は数えない。"""
    assert bugfix_gate.is_defect_report(prompt) is expected


def test_send_to_user_is_denied_until_bugfix_is_invoked(tmp_path: pathlib.Path) -> None:
    """欠陥指摘の後にbugfixを起動せず`send_to_user`を呼ぶと遮断され、起動後は通る。"""
    assert _pretooluse_denied(_payload([_human("ここがバグっている")], tmp_path))
    assert not _pretooluse_denied(_payload([_human("ここがバグっている"), _BUGFIX], tmp_path))


def test_send_to_user_without_defect_report_or_by_subagent_is_allowed(tmp_path: pathlib.Path) -> None:
    """欠陥指摘ではない発話、前のターンの起動、サブエージェントの呼出しは遮断しない。"""
    assert not _pretooluse_denied(_payload([_human("進捗を教えて")], tmp_path))
    assert _pretooluse_denied(_payload([_BUGFIX, _human("まだバグっている")], tmp_path))
    assert not _pretooluse_denied(_payload([_human("ここがバグっている")], tmp_path, agent_id="sub-1"))


def _stop(entries: list[dict], tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, **extra: object) -> dict:
    monkeypatch.setattr(stop, "CHECK_MODULE_NAMES", ("bugfix_gate",))
    return stop.evaluate(json.dumps(_payload(entries, tmp_path, **extra)))


def test_stop_blocks_direct_answer_without_bugfix_and_respects_recursion_guard(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """応答本文だけで終えるStopも同じ判定で遮断し、bugfix起動後と`stop_hook_active`の再呼出しでは通す。"""
    entries = [_human("ここがバグっている")]
    blocked = _stop(entries, tmp_path, monkeypatch)
    assert blocked["decision"] == "block" and "agent-basics:bugfix" in str(blocked["reason"])
    assert "decision" not in _stop([*entries, _BUGFIX], tmp_path, monkeypatch)
    assert "decision" not in _stop(entries, tmp_path, monkeypatch, stop_hook_active=True)
    assert "decision" not in _stop([_human("進捗を教えて")], tmp_path, monkeypatch)
