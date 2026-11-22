"""所有者だけが使うディレクトリの作成を`agent_toolkit._common.private_directory`へ集めたことを検証する。

Windowsで`mode=0o700`の`mkdir`と`tempfile.mkdtemp`は、OWNER RIGHTSだけに依存する保護付きDACLを付ける。
昇格したプロセスがその配下へ書いたファイルを、同じユーザーの非昇格のプロセスが読めなくなる（2026-11-19）。
"""

from __future__ import annotations

import ast
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parent
_SCANNED = (
    _ROOT / "agent-toolkit" / "agent_toolkit",
    _ROOT / "agent-toolkit" / "scripts",
    _ROOT / "pytools",
    _ROOT / "libexec",
)
_EXEMPT = (
    _ROOT / "agent-toolkit" / "agent_toolkit" / "_common" / "private_directory.py",
    # 試験の隔離環境はプロセスごとに作成して破棄し、昇格の異なるプロセスと共有しない。
    _ROOT / "agent-toolkit" / "agent_toolkit" / "_testing",
)
_OWNER_ONLY_MODE = 0o700
_HOOK_SHIM = _ROOT / "agent-toolkit" / "scripts" / "atk-hook"
_PYTHON_SHEBANG = re.compile(rb"^#!\s*(?:(?:\S*/)?env\s+(?:-S\s+)?)?(?:\S*/)?python(?:\d+(?:\.\d+)*)?(?:\s|$)")


def _is_python_source(path: pathlib.Path) -> bool:
    if path.suffix:
        return path.suffix == ".py"

    if path == _HOOK_SHIM:
        return True
    with path.open("rb") as stream:
        return bool(_PYTHON_SHEBANG.match(stream.readline(256)))


def _production_sources(bases: tuple[pathlib.Path, ...]) -> list[pathlib.Path]:
    return sorted(
        path
        for base in bases
        for path in base.rglob("*")
        if path.is_file()
        and not path.name.endswith("_test.py")
        and path.name != "conftest.py"
        and not any(path.is_relative_to(exempt) for exempt in _EXEMPT)
        and _is_python_source(path)
    )


def _os_aliases(tree: ast.Module) -> set[str]:
    return {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "os"
    }


def _mode_position(function: ast.expr, os_names: set[str]) -> int:
    if isinstance(function, ast.Name):
        return 1
    qualified_by_os = (
        isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name) and function.value.id in os_names
    )
    return 1 if qualified_by_os else 0


def _is_owner_only_mode(mode: ast.expr) -> bool:
    if isinstance(mode, ast.Constant):
        return mode.value == _OWNER_ONLY_MODE
    return isinstance(mode, ast.Attribute) and mode.attr == "S_IRWXU"


def _owner_only_creation_lines(source: str) -> list[int]:
    tree = ast.parse(source)
    os_names = _os_aliases(tree)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = function.attr if isinstance(function, ast.Attribute) else function.id if isinstance(function, ast.Name) else None
        if name == "mkdtemp":
            lines.append(node.lineno)
        elif name == "TemporaryDirectory":
            if len(node.args) >= 3 or any(keyword.arg == "dir" for keyword in node.keywords):
                lines.append(node.lineno)
        elif name in {"mkdir", "makedirs"}:
            position = _mode_position(function, os_names)
            modes = [keyword.value for keyword in node.keywords if keyword.arg == "mode"] + node.args[position : position + 1]
            if any(_is_owner_only_mode(mode) for mode in modes):
                lines.append(node.lineno)
    return sorted(lines)


def test_owner_only_directories_are_created_through_private_directory() -> None:
    violations: list[str] = []
    for path in _production_sources(_SCANNED):
        source = path.read_text(encoding="utf-8")

        if any(word in source for word in ("mkdtemp", "TemporaryDirectory", "0o700", "448", "S_IRWXU")):
            violations.extend(f"{path.relative_to(_ROOT)}:{line}" for line in _owner_only_creation_lines(source))
    assert not violations, (
        "Windowsでも非昇格のプロセスが読めるよう、modeは`private_directory.MODE`、"
        f"一時ディレクトリは`private_directory.make_temporary`か`private_directory.temporary`を使う: {violations}"
    )


def test_extensionless_scripts_are_selected_by_interpreter(tmp_path: pathlib.Path) -> None:
    for name, header in {
        "direct": "#!/usr/bin/python3",
        "env": "#!/usr/bin/env python3",
        "flags": "#!/usr/bin/env -S python3.15 -I",
        "shell": "#!/bin/sh\nexec python3 other.py",
    }.items():
        (tmp_path / name).write_text(header + "\n", encoding="utf-8")
    assert [path.name for path in _production_sources((tmp_path,))] == ["direct", "env", "flags"]


def test_detector_finds_each_owner_only_creation_form() -> None:
    source = (
        "import os, pathlib, stat, tempfile\n"
        "import os as _os\n"
        "pathlib.Path('a').mkdir(mode=0o700)\n"
        "pathlib.Path('a').mkdir(0o700, parents=True)\n"
        "os.mkdir('a', 0o700)\n"
        "os.makedirs('a', 0o700)\n"
        "os.makedirs('a', mode=0o700, exist_ok=True)\n"
        "tempfile.mkdtemp(dir='a')\n"
        "_os.mkdir('a', 448)\n"
        "pathlib.Path('a').mkdir(mode=stat.S_IRWXU)\n"
        "tempfile.TemporaryDirectory(dir='a')\n"
        "tempfile.TemporaryDirectory(None, 'x-', 'a')\n"
        "pathlib.Path('a').mkdir(mode=private_directory.MODE)\n"
        "os.mkdir('a')\n"
        "os.mkdir('a', 0o755)\n"
        "tempfile.TemporaryDirectory(prefix='x-')\n"
    )
    assert _owner_only_creation_lines(source) == [3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
