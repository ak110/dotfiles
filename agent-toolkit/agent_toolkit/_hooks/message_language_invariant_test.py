"""公開hookが返すコーディングエージェント向け通知の日本語を検証する。"""

import ast
import json
import os
import pathlib
import re
import subprocess
import time

import pytest

from agent_toolkit._testing import fork_runner as _fork_runner
from agent_toolkit._testing.helpers import SESSION_STATE_FILENAME_TEMPLATE

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "hook.py"
_ALLOWED_BARE_IDENTIFIERS = frozenset(
    {
        "WebFetch",
        "PreToolUse",
        "PostToolUse",
        "Stop",
        "SubagentStop",
        "AskUserQuestion",
        "SendMessage",
        "TaskStop",
        "agent-toolkit",
        "Claude",
        "Codex",
        "Git",
        "GitHub",
        "Markdown",
        "JSON",
        "BOM",
        "YAML",
        "SSE",
        "URL",
        "ID",
        "PID",
        "OID",
        "CI",
        "PR",
        "Python",
        "PowerShell",
        "UWI",
        "AWI",
    }
)
_JAPANESE_RE = re.compile(r"[぀-ゟ゠-ヿ一-鿿]")
_LATIN_RE = re.compile(r"[A-Za-z]")

_HOOK_SOURCE_ROOT = pathlib.Path(__file__).resolve().parent
_NOTICE_CALL_NAMES = frozenset({"_llm_notice", "_block_notice", "_notice", "_block", "block", "format_block"})
_NOTICE_CONSTANT_NAME_RE = re.compile(r"(?:^|_)(?:BODY|FIX|NOTICE|MESSAGE)$")


def _literal_text(node: ast.expr, constants: dict[str, str]) -> str | None:
    """文字列リテラル・f-string・連結・モジュール定数参照を、置換欄を`{}`にした本文へ直す。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            text = _literal_text(value, constants) if not isinstance(value, ast.FormattedValue) else "{}"
            parts.append(text or "")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_text(node.left, constants)
        right = _literal_text(node.right, constants)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.Attribute):
        return constants.get(node.attr)
    return None


def _collect_notice_texts() -> list[tuple[str, str]]:
    """hookのソースから通知の本文と解消手段を抽出する。

    写しを持たずソースから直接読むため、文面を変えても新しい文面を対象にでき、旧文面の写しとの乖離が生じない。
    """
    sources = [path for path in sorted(_HOOK_SOURCE_ROOT.rglob("*.py")) if not path.name.endswith("_test.py")]
    trees = {path: ast.parse(path.read_text(encoding="utf-8")) for path in sources}
    constants: dict[str, str] = {}
    for tree in trees.values():
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                text = _literal_text(node.value, constants)
                if text is not None:
                    constants[node.targets[0].id] = text
    texts: list[tuple[str, str]] = []
    for path, tree in trees.items():
        relative = path.relative_to(_HOOK_SOURCE_ROOT.parent)
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and _NOTICE_CONSTANT_NAME_RE.search(node.targets[0].id) is not None
                and node.targets[0].id in constants
            ):
                texts.append((f"{relative}:{node.targets[0].id}", constants[node.targets[0].id]))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name not in _NOTICE_CALL_NAMES:
                continue
            candidates = [("本文", node.args[0])] if node.args else []
            candidates += [(keyword.arg, keyword.value) for keyword in node.keywords if keyword.arg in {"fix", "summary"}]
            for label, value in candidates:
                text = _literal_text(value, constants)
                if text is not None:
                    texts.append((f"{relative}:{node.lineno} {label}", text))
    # `hook.py`の自己障害通知は終了コード0の標準エラーへ出てエージェントへ届かないため、走査対象の`_hooks/`に含めない。
    # 置換欄だけで構成される本文（`f"{a}\n{b}"`など）は自然言語を持たないため判定対象から外す。
    return [(source, text) for source, text in texts if re.sub(r"[{}\s]", "", text)]


def _is_japanese_notice(text: str) -> bool:
    """通知の自然言語部分が日本語だけで構成される場合に真を返す。"""
    body = re.sub(r"</?(?:atk-auto|agent-toolkit-auto-inserted)(?:\s[^>]*)?>", "", text)
    body = re.sub(r"判定対象の冒頭: 「[^\n」]*」", "", body)
    body = re.sub(r"(?m)^\s*(?:warn|warning|block|blocked):\s*", "", body)
    body = re.sub(r"(?m)^\s*(?:Fix|次の操作):\s*", "", body)
    body = re.sub(r"\{[^{}]*\}", "", body)
    body = re.sub(r"`[^`]*`", "", body)
    body = re.sub(r"https?://\S+", "", body)
    body = re.sub(r"\\[ntr]", "", body)
    for identifier in sorted(_ALLOWED_BARE_IDENTIFIERS, key=len, reverse=True):
        body = re.sub(rf"(?<![A-Za-z]){re.escape(identifier)}(?![A-Za-z])", "", body)
    return _LATIN_RE.search(body) is None and _JAPANESE_RE.search(body) is not None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Retry.", False),
        ("blocked: pattern-based process termination is prohibited.", False),
        ("{tool_name}による編集で対象を確認する。対象: {file_path}", True),
        ("{tool_name}による編集で Retry.", False),
        ("`pkill`／`killall`は使わない。", True),
        ("日本語の通知。\n判定対象の冒頭: 「English response.」", True),
        ("日本語の通知。判定対象の冒頭: 「English response.」", True),
        ("日本語。判定対象の冒頭: 「English.」 English notice.", False),
        ("日本語。判定対象の冒頭: 「English.」 English notice.「more」", False),
        ("English notice.\n判定対象の冒頭: 「日本語の応答。」", False),
    ],
)
def test_japanese_notice_judgment(text: str, expected: bool) -> None:
    assert _is_japanese_notice(text) is expected


def test_hook_source_notice_texts_are_japanese() -> None:
    """hookのソースが持つ通知の本文と解消手段が通知言語契約を満たすことを検証する。"""
    texts = _collect_notice_texts()
    # 抽出が機能しない変更（呼び出し名の改名など）で対象の文面が空になり、無条件に合格することを防ぐ。
    assert len(texts) >= 40
    failures = [source for source, text in texts if not _is_japanese_notice(text)]
    assert failures == []


def test_hook_source_does_not_name_removed_agents_server_tools() -> None:
    """通知が廃止済みの旧ツール名を案内しないことを検証する。"""
    stale = [source for source, text in _collect_notice_texts() if "codex_start" in text]
    assert stale == []


def _run(payload: dict, tmp_path: pathlib.Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CLAUDE_AGENT_TOOLKIT_STATE_DIR"] = str(tmp_path / "state")
    return _fork_runner.run_script(
        _SCRIPT,
        argv=("pretooluse",),
        input=json.dumps(payload, ensure_ascii=False),
        env=env,
    )


def _run_user_prompt_submit(payload: dict, tmp_path: pathlib.Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["TMPDIR"] = str(tmp_path)
    env["TEMP"] = str(tmp_path)
    env["TMP"] = str(tmp_path)
    return _fork_runner.run_script(
        _SCRIPT,
        argv=("user_prompt_submit",),
        input=json.dumps(payload, ensure_ascii=False),
        env=env,
    )


def _write_english_transcript(tmp_path: pathlib.Path, message_id: str) -> pathlib.Path:
    transcript = tmp_path / "transcript.jsonl"
    entry = {
        "type": "assistant",
        "message": {
            "id": message_id,
            "role": "assistant",
            "content": [{"type": "text", "text": "This response is written entirely in English for the language checker."}],
            "stop_reason": "end_turn",
        },
    }
    transcript.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")
    return transcript


def test_process_termination_block_is_japanese(tmp_path: pathlib.Path) -> None:
    command = "p" + "kill -f myserver"
    result = _run({"tool_name": "Bash", "tool_input": {"command": command}}, tmp_path)
    assert result.returncode == 2
    assert _is_japanese_notice(result.stderr)


@pytest.mark.parametrize(
    ("file_name", "expected_notice"),
    [
        (
            "pyproject.toml",
            "`Write`で`pyproject.toml`の依存の節を編集しようとしている。\n次の操作: "
            "`[project.dependencies]`・`[project.optional-dependencies]`の編集は、"
            "`uv.lock`を同期させるため`uv add`・`uv remove`を使う。"
            "`[tool.*]`と版数の編集はそのまま進めてよい。",
        ),
        (
            "package.json",
            "`Write`で`package.json`の依存の節を編集しようとしている。\n次の操作: "
            "依存の編集は、`pnpm-lock.yaml`を同期させるため`pnpm add`・`pnpm remove`を使う。"
            "`scripts`とメタデータの編集はそのまま進めてよい。",
        ),
    ],
)
def test_manifest_edit_warning_uses_confirmed_japanese_notice(
    tmp_path: pathlib.Path,
    file_name: str,
    expected_notice: str,
) -> None:
    """manifest編集警告の確定訳を公開されたhookを起動して検証する。"""
    result = _run(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": str(tmp_path / file_name), "content": '{"dependencies": {}}'},
        },
        tmp_path,
    )
    assert result.returncode == 0
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert expected_notice in context
    assert _is_japanese_notice(context)


def test_response_language_warning_is_japanese(tmp_path: pathlib.Path) -> None:
    transcript = _write_english_transcript(tmp_path, "message-1")
    result = _run(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "session_id": "language-warning",
            "transcript_path": str(transcript),
        },
        tmp_path,
    )
    assert result.returncode == 0
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert _is_japanese_notice(context)


def test_response_language_escalated_body_is_japanese(tmp_path: pathlib.Path) -> None:
    transcript = _write_english_transcript(tmp_path, "message-1")
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "session_id": "language-block",
        "transcript_path": str(transcript),
    }
    assert _run(payload, tmp_path).returncode == 0
    _write_english_transcript(tmp_path, "message-2")
    result = _run(payload, tmp_path)
    assert result.returncode == 0
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "この通知は同一セッションで2件目である" in context
    assert _is_japanese_notice(context)


def test_user_prompt_verification_notice_is_japanese(tmp_path: pathlib.Path) -> None:
    sid = "japanese-verification-notice"
    state_path = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)
    state_path.write_text(json.dumps({"last_user_prompt_at": time.time() - 200}), encoding="utf-8")

    result = _run_user_prompt_submit({"session_id": sid, "prompt": "通常のユーザー発話です。"}, tmp_path)

    assert result.returncode == 0
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert _is_japanese_notice(context)
