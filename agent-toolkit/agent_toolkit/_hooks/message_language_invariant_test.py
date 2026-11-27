"""公開hookが返すコーディングエージェント向け通知の日本語を検証する。"""

import ast
import json
import pathlib
import re
import subprocess

import pytest

from agent_toolkit._hooks import work_contract
from agent_toolkit._testing import fork_runner as _fork_runner
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process as run_hook
from scripts.verification_scan_rules import scan_files

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
    sources = scan_files(__file__)
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
    # `hook.py`の共通障害通知は公開hookの試験で検証し、本走査は`_hooks/`の通知生成元を対象とする。
    # 置換欄だけで構成される本文（`f"{a}\n{b}"`など）は自然言語を持たないため判定対象から外す。
    return [(source, text) for source, text in texts if re.sub(r"[{}\s]", "", text)]


def _is_japanese_notice(text: str) -> bool:
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


@pytest.fixture(scope="module", name="hook_notice_texts")
def _hook_notice_texts() -> list[tuple[str, str]]:
    return _collect_notice_texts()


@pytest.mark.cpu_limit(
    2.0,
    reason="全hookソースの通知をASTから抽出し日本語を検査する。共有準備も当該試験へ計上し、Linux CPU合計0.813秒を基準とする。",
)
def test_hook_source_notice_texts_are_japanese(hook_notice_texts: list[tuple[str, str]]) -> None:
    """hookのソースが持つ通知の本文と解消手段が、自然言語部分を日本語だけで書く契約を満たすことを検証する。"""
    texts = hook_notice_texts

    assert len(texts) >= 40
    failures = [source for source, text in texts if not _is_japanese_notice(text)]
    assert failures == []


@pytest.mark.cpu_limit(
    2.25,
    reason=(
        "全hookソースをASTから抽出し、撤去した操作への案内を検査する。単独起動時の共有準備も含め、"
        "Linux CPU合計0.935秒を基準とする。"
    ),
)
def test_hook_source_does_not_name_removed_agents_server_tools(hook_notice_texts: list[tuple[str, str]]) -> None:
    stale = [source for source, text in hook_notice_texts if "codex_start" in text]
    assert stale == []


def _run(payload: dict) -> subprocess.CompletedProcess[str]:
    return _fork_runner.run_script(
        _SCRIPT,
        argv=("pretooluse",),
        input=json.dumps(payload, ensure_ascii=False),
    )


def test_process_termination_block_is_japanese() -> None:
    command = "p" + "kill -f myserver"
    result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
    assert _is_japanese_notice(_denial_reason(result))


def _denial_reason(result: subprocess.CompletedProcess[str]) -> str:
    assert result.returncode == 0
    assert not result.stderr
    specific = json.loads(result.stdout)["hookSpecificOutput"]
    assert specific["permissionDecision"] == "deny"
    assert specific["permissionDecisionReason"]
    return specific["permissionDecisionReason"]


@pytest.mark.parametrize(
    ("file_name", "commands"),
    [
        (
            "pyproject.toml",
            ("uv add", "uv remove"),
        ),
        (
            "package.json",
            ("pnpm add", "pnpm remove"),
        ),
    ],
)
def test_manifest_edit_warning_uses_confirmed_japanese_notice(
    tmp_path: pathlib.Path,
    file_name: str,
    commands: tuple[str, str],
) -> None:
    result = _run(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": str(tmp_path / file_name), "content": '{"dependencies": {}}'},
        },
    )
    assert result.returncode == 0
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert file_name in context and all(command in context for command in commands)
    assert 'kind="warn"' in context
    assert _is_japanese_notice(context)


@pytest.mark.parametrize("repetitions", [1, 2])
def test_direct_response_language_block_is_japanese(repetitions: int) -> None:
    payload = {
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": [{"question": "Which option should we choose?", "header": "確認", "options": []}]},
        "session_id": "language-block",
    }
    for _ in range(repetitions):
        result = _run(payload)
        reason = _denial_reason(result)
        assert "日本語で書き直" in reason
        assert _is_japanese_notice(reason)


def test_user_prompt_context_notice_is_japanese(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sid = "japanese-context-notice"
    record = tmp_path / "work-contract.md"
    record.write_text("目的: 作業を完了する。\n", encoding="utf-8")
    monkeypatch.setattr(work_contract, "record_path", lambda _session: record)

    result = run_hook("user_prompt_submit", {"session_id": sid, "prompt": "通常のユーザー発話です。"})

    assert result.returncode == 0
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert _is_japanese_notice(context)
