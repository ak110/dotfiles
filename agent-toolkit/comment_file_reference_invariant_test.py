"""`agent-toolkit/`配下のPythonのコメントとdocstringが挙げるファイル名が、追跡中のファイルとして実在することを確かめる。

ファイルの移動・分割・改名の後にコメントとdocstringへ旧名が残ると、読み手は存在しないファイルを探す。
対象は`<名前>.py`の形の語とし、`*`を含むglobの語と、`<照会モード>_test.py`のように`>`の直後から始まる名前の型の語は対象から外す。
"""

import ast
import io
import pathlib
import re
import subprocess
import tokenize

import pytest

from agent_toolkit._testing.git_repository import commit_all, init_repository

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent
_FILE_NAME_PATTERN = re.compile(r"[A-Za-z0-9_.*/\\-]*?([A-Za-z0-9_*-]+\.py)(?![A-Za-z0-9_])")


def _tracked_names() -> set[str]:
    """リポジトリ全体の追跡中のファイルの名前の集合を返す。"""
    result = subprocess.run(
        ["git", "-C", str(_PLUGIN_ROOT), "ls-files", "-z", "--", ":/"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {pathlib.PurePosixPath(path).name for path in result.stdout.split("\0") if path and (_PLUGIN_ROOT / path).is_file()}


def _plugin_python_files() -> list[pathlib.Path]:
    """`agent-toolkit/`配下の追跡中のPythonファイルを返す。"""
    result = subprocess.run(
        ["git", "-C", str(_PLUGIN_ROOT), "ls-files", "-z", "--", "*.py"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return [_PLUGIN_ROOT / path for path in result.stdout.split("\0") if path and (_PLUGIN_ROOT / path).is_file()]


def _docstring_and_comment_lines(source: str) -> list[tuple[int, str]]:
    """コメントとdocstringの本文を、ファイル内の行番号と組にして返す。"""
    lines: list[tuple[int, str]] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            lines.append((token.start[0], token.string))
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) or not node.body:
            continue
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            lines.extend((first.lineno + offset, text) for offset, text in enumerate(first.value.value.splitlines()))
    return lines


def _missing_references() -> list[str]:
    """追跡中のファイルに無いファイル名を挙げるコメント・docstringの行を返す。"""
    tracked = _tracked_names()
    problems: list[str] = []
    for path in _plugin_python_files():
        for line_number, text in _docstring_and_comment_lines(path.read_text(encoding="utf-8")):
            for match in _FILE_NAME_PATTERN.finditer(text):
                name = match.group(1)
                placeholder = match.start(1) > 0 and text[match.start(1) - 1] == ">"
                if "*" in match.group(0) or placeholder or name in tracked:
                    continue
                relative = path.relative_to(_PLUGIN_ROOT.parent).as_posix()
                problems.append(f"{relative}:{line_number}: {name}: {text.strip()}")
    return problems


def test_comment_and_docstring_file_names_exist() -> None:
    """コメントとdocstringのファイル名が追跡中のファイルとして実在する。"""
    problems = _missing_references()
    assert not problems, (
        "コメント・docstringが追跡中のファイルに無いファイル名を挙げている:\n"
        + "\n".join(problems)
        + "\n次の操作: 現在のファイル名へ改める。移設の経緯だけを書いている場合はファイル名を含まない表現へ改める"
    )


def test_deleted_sources_are_omitted_but_references_to_deleted_or_untracked_files_fail(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """削除済みの走査元を読まず、削除済み・未追跡の参照先は存在として数えない。"""
    repository = init_repository(
        tmp_path / "repo",
        files={
            "agent-toolkit/current.py": '"""retired.pyとuntracked.py、existing.pyとoutside.pyを参照する。"""\n',
            "agent-toolkit/existing.py": '"""plugin内の追跡中の参照先。"""\n',
            "scripts/outside.py": '"""plugin外の追跡中の参照先。"""\n',
            "agent-toolkit/retired.py": '"""撤去予定の走査元。"""\n',
        },
    )
    commit_all(repository, "追跡中の検査対象")
    plugin = repository / "agent-toolkit"
    (plugin / "retired.py").unlink()
    (plugin / "untracked.py").write_text("# 未追跡の参照先\n", encoding="utf-8")
    monkeypatch.setattr(f"{__name__}._PLUGIN_ROOT", plugin)

    problems = _missing_references()

    assert len(problems) == 2
    assert any(": retired.py:" in problem for problem in problems)
    assert any(": untracked.py:" in problem for problem in problems)
