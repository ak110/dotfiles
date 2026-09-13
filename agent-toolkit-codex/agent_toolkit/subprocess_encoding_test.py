"""本番Pythonコードのテキストsubprocess境界を検査する。"""

import ast
import os
import subprocess
from pathlib import Path

from agent_toolkit._git import command as git_command

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_ROOTS = (_PACKAGE_ROOT / "agent_toolkit", _PACKAGE_ROOT / "scripts", _PACKAGE_ROOT / "skills")
_SUBPROCESS_CALLS = {"run", "Popen", "call", "check_call", "check_output"}


def _literal(node: ast.expr | None) -> object:
    if isinstance(node, ast.Constant):
        return node.value
    return None


def _allows_conditional(node: ast.expr | None, expected: str) -> bool:
    return (
        _literal(node) == expected
        or isinstance(node, ast.IfExp)
        and _literal(node.body) == expected
        and _literal(node.orelse) is None
    )


def test_text_subprocesses_use_utf8_with_replacement() -> None:
    """テキストモードの全呼び出しでUTF-8と置換処理を明示する。"""
    violations: list[str] = []
    paths = sorted(path for root in _SOURCE_ROOTS for path in root.rglob("*.py"))
    for path in paths:
        if path.name.endswith("_test.py") or "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if not (
                isinstance(function, ast.Attribute)
                and isinstance(function.value, ast.Name)
                and function.value.id == "subprocess"
                and function.attr in _SUBPROCESS_CALLS
            ):
                continue
            keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg is not None}
            text_value = _literal(keywords.get("text"))
            universal_value = _literal(keywords.get("universal_newlines"))
            text_mode = "encoding" in keywords or (text_value is not False and "text" in keywords) or universal_value is True
            if not text_mode:
                continue
            if not _allows_conditional(keywords.get("encoding"), "utf-8") or not _allows_conditional(
                keywords.get("errors"), "replace"
            ):
                relative = path.relative_to(_PACKAGE_ROOT.parent)
                violations.append(f"{relative}:{node.lineno}")

    assert not violations


def test_git_text_output_replaces_invalid_utf8(tmp_path: Path, monkeypatch) -> None:
    """UTF-8として不正なbyteを置換文字へ変換する。"""
    git = tmp_path / "git"
    git.write_bytes(b"#!/bin/sh\nprintf '\\377'\n")
    git.chmod(0o755)
    monkeypatch.setenv("PATH", os.fspath(tmp_path))

    result = git_command.run(["ignored"], tmp_path, capture_output=True, text=True)

    assert isinstance(result, subprocess.CompletedProcess)
    assert result.stdout == "\ufffd"
