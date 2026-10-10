"""PreToolUseの全遮断が、呼び出し全体の未実行を同じ要素内で伝える。

入力は既存dispatchテストと同じClaude Code・Codexのhook payload形式を用いる。
Stopの遮断はツールの実行前ではないため、この文を含めない。
"""

import ast
import json
import pathlib
import re

import pytest

from agent_toolkit._hooks import notice
from agent_toolkit._hooks.pretooluse import dispatch

_RESULT = "この呼び出しは全体を実行していない（同じ呼び出しに含まれる他のコマンドや処理を含む）。"


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize(
    "case",
    [
        "atk_pipe",
        "heredoc",
        "process_kill",
        "options",
        "revisions",
        "commit_format",
        "commit_attribution",
        "task_stop",
        "continuation",
        "free_start",
        "agent_prompt",
        "large_read",
    ],
)
def test_block_notices_describe_entire_unexecuted_call(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], codex: bool, case: str
) -> None:
    """原因の後・次の操作の前へ1回だけ結果を出力し、非該当ホストへ遮断を増やさない。"""
    document = tmp_path / "plugin/share/sample.subagent.md"
    document.parent.mkdir(parents=True)
    manifest = document.parent.parent / ".claude-plugin/plugin.json"
    manifest.parent.mkdir()
    manifest.write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    document.write_text("## 入力\n\n```text\n必須入力名: 対象\n```\n", encoding="utf-8")
    large = tmp_path / "large.txt"
    large.write_text("a" * (48 * 1024 + 1), encoding="utf-8")
    commands = {
        "atk_pipe": "cat <<'EOF' > sample.txt\n生成する本文\nEOF\natk --help | cat",
        "heredoc": "cat <<EOF\n`date`\nEOF",
        "process_kill": "pkill unknown",
        "options": "rg -- value --glob '*.md' .",
        "revisions": "git rev-parse --short HEAD HEAD~1",
        "commit_format": "git commit -m 'fix: 件名\n本文'",
        "commit_attribution": "git commit -m 'fix: 件名'",
        "large_read": f"cat -n -- {large}",
    }
    tool, tool_input = "Bash", {"command": commands.get(case, "")}
    if case == "task_stop":
        tool, tool_input = "TaskStop", {"task_id": "unknown-task"}
    elif case == "continuation":
        tool, tool_input = "mcp__agents_server__send_message", {"session_id": "unknown-session", "prompt": "続ける"}
    elif case == "free_start":
        tool, tool_input = "mcp__agents_server__start", {"mode": "explore", "prompt": f"{document}の手順を実行せよ。"}
    elif case == "agent_prompt":
        tool, tool_input = "Agent", {"prompt": f"{document}の手順を実行せよ。\n対象: 値\n余計な手順"}
    payload = {
        "tool_name": tool,
        "tool_input": tool_input,
        "session_id": f"block-{case}-{codex}",
        "cwd": str(tmp_path),
        "model": "test-model",
        "effort": {"level": "medium"},
    }
    if codex:
        payload["turn_id"] = "codex-turn"
    code = dispatch.main(json.dumps(payload, ensure_ascii=False))
    captured = capsys.readouterr()
    if case == "large_read" and not codex:
        assert code == 0
        assert _RESULT not in captured.err
        return
    assert code == 2, captured
    blocks = re.findall(r'<atk-auto source="[^"]+" kind="block">\n(.*?)\n</atk-auto>', captured.err, re.DOTALL)
    assert len(blocks) == 1, captured.err
    body = blocks[0]
    assert body.count(_RESULT) == 1
    assert 0 < body.index(_RESULT) < body.index("次の操作:")


def test_escalated_warning_has_result_but_stop_block_does_not(capsys: pytest.CaptureFixture[str]) -> None:
    """昇格による遮断にも結果を添え、Stopの通常整形へ広げない。"""
    notice.set_warning_session_id("escalated-block")
    warning = notice.warning_formatter("pretooluse")
    for _ in range(2):
        warning(
            "失敗する操作",
            fix="入力を直す",
            cause="case",
            session_id="escalated-block",
            removable_cause=True,
            escalate_on_repeat=True,
        )
    assert dispatch.exit_with(0, []) == 2
    body = capsys.readouterr().err
    assert body.count(_RESULT) == 1
    assert body.index("失敗する操作") < body.index(_RESULT) < body.index("次の操作:")
    assert _RESULT not in notice.block_formatter("stop")("終了工程が残る", fix="工程へ戻る")


def test_pretooluse_block_formatter_is_owned_by_notices() -> None:
    """新しい独立整形が共通の結果文を省く逸脱を検出する。"""
    root = pathlib.Path(dispatch.__file__).parent
    owners = []
    for path in root.glob("*.py"):
        if path.name.endswith("_test.py"):
            continue
        module = ast.parse(path.read_text(encoding="utf-8"))
        aliases = {"block_formatter", "_block_notice_formatter"}
        aliases.update(
            alias.asname or alias.name
            for node in ast.walk(module)
            if isinstance(node, ast.ImportFrom) and node.module == "agent_toolkit._hooks.notice"
            for alias in node.names
            if alias.name == "block_formatter"
        )
        for node in ast.walk(module):
            if isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Name)
                and node.func.id in aliases
                or isinstance(node.func, ast.Attribute)
                and node.func.attr == "block_formatter"
            ):
                owners.append(path.name)
    assert owners == ["notices.py"]
