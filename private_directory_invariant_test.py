"""所有者だけが使うディレクトリの作成を`agent_toolkit._common.private_directory`へ集めたことを検証する。

Windowsで`mode=0o700`の`mkdir`と`tempfile.mkdtemp`は、OWNER RIGHTSだけに依存する保護付きDACLを付ける。
昇格したプロセスがその配下へ書いたファイルを、同じユーザーの非昇格のプロセスが読めなくなる（2026-11-19）。
"""

from __future__ import annotations

import ast
import pathlib

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


def _production_sources() -> list[pathlib.Path]:
    return sorted(
        path
        for base in _SCANNED
        for path in base.rglob("*.py")
        if not path.name.endswith("_test.py")
        and path.name != "conftest.py"
        and not any(path.is_relative_to(exempt) for exempt in _EXEMPT)
    )


def _os_aliases(tree: ast.Module) -> set[str]:
    """`import os`・`import os as _os`で`os`モジュールを指す名前を返す。"""
    return {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "os"
    }


def _mode_position(function: ast.expr, os_names: set[str]) -> int:
    """`os.mkdir(path, mode)`・`os.makedirs(name, mode)`は2番目、`Path.mkdir(mode)`は1番目にmodeを受け取る。"""
    if isinstance(function, ast.Name):
        return 1
    qualified_by_os = (
        isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name) and function.value.id in os_names
    )
    return 1 if qualified_by_os else 0


def _is_owner_only_mode(mode: ast.expr) -> bool:
    """0o700（448）の定数と`stat.S_IRWXU`を所有者だけのmodeとみなす。"""
    if isinstance(mode, ast.Constant):
        return mode.value == _OWNER_ONLY_MODE
    return isinstance(mode, ast.Attribute) and mode.attr == "S_IRWXU"


def _owner_only_creation_lines(source: str) -> list[int]:
    """`mkdtemp`と`dir`を指定した`TemporaryDirectory`の呼び出し、modeに0o700を渡す`mkdir`・`makedirs`の呼び出しの行番号を返す。

    `dir`を省いた`TemporaryDirectory`はプロセスごとの一時ディレクトリに作成し、状態ディレクトリとして共有しないため対象にしない。
    """
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
    """配布するコードは0o700の`mkdir`と`mkdtemp`を直接呼ばない。"""
    violations: list[str] = []
    for path in _production_sources():
        source = path.read_text(encoding="utf-8")
        # 構文解析は該当し得る記述を含むファイルだけに限り、全ファイルの解析を避ける。
        if any(word in source for word in ("mkdtemp", "TemporaryDirectory", "0o700", "448", "S_IRWXU")):
            violations.extend(f"{path.relative_to(_ROOT)}:{line}" for line in _owner_only_creation_lines(source))
    assert not violations, (
        "Windowsでも非昇格のプロセスが読めるよう、modeは`private_directory.MODE`、"
        f"一時ディレクトリは`private_directory.make_temporary`か`private_directory.temporary`を使う: {violations}"
    )


def test_detector_finds_each_owner_only_creation_form() -> None:
    """既知の書き方をすべて拾い、`private_directory.MODE`とmode省略、`dir`を省いた`TemporaryDirectory`は拾わない。"""
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
