"""テストが裸の名前でimportするモジュールを、実行に含めるファイルの組合せによらず解決できることを検査する。

pytestのprepend import modeとpylintは、同じ実行に含めたファイルのディレクトリを探索パスへ加える。
このため別ディレクトリのスクリプトを裸の名前でimportするテストは、全体の実行では成功し、
対象を限定したpytest・pylintでだけ`ModuleNotFoundError`やE0401で止まる。
テストの依存は、同じディレクトリのファイル、導入済みのパッケージと標準ライブラリ、
またはそのテスト自身が`sys.path`へ明示的に加えるディレクトリのいずれかで解決する。
"""

from __future__ import annotations

import ast
import functools
import importlib.machinery
import json
import pathlib
import subprocess
import sys

_ROOT = pathlib.Path(__file__).resolve().parent


@functools.cache
def _installed_search_path() -> tuple[str, ...]:
    """作業ディレクトリと`PYTHONPATH`を除いた、導入済みのパッケージと標準ライブラリの探索パスを返す。"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", "import json, sys; print(json.dumps(sys.path))"],
        cwd=_ROOT.anchor,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return tuple(json.loads(result.stdout))


def _imported_top_level_names(tree: ast.Module) -> set[str]:
    """絶対importの先頭の名前を返す。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def _extends_sys_path(tree: ast.Module) -> bool:
    """`sys.path.insert`か`sys.path.append`で探索パスを明示的に加えるかを返す。"""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"insert", "append"}
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "path"
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "sys"
        ):
            return True
    return False


def _unresolved_imports(path: pathlib.Path, search_path: tuple[str, ...]) -> list[str]:
    """同じディレクトリと導入済みの探索パスのどちらでも解決できない名前を返す。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    if _extends_sys_path(tree):
        return []
    unresolved = []
    for name in sorted(_imported_top_level_names(tree)):
        if (path.parent / f"{name}.py").is_file() or (path.parent / name).is_dir():
            continue
        if name in sys.stdlib_module_names or name in sys.builtin_module_names:
            continue
        if importlib.machinery.PathFinder.find_spec(name, list(search_path)) is None:
            unresolved.append(name)
    return unresolved


def _tracked_tests(root: pathlib.Path) -> list[pathlib.Path]:
    """Git追跡ファイルのうち`*_test.py`と`conftest.py`を返す。"""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "*_test.py", "*conftest.py"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return sorted(root / raw.decode("utf-8") for raw in result.stdout.split(b"\0") if raw)


def _violations(root: pathlib.Path, tests: list[pathlib.Path], search_path: tuple[str, ...]) -> list[str]:
    return [
        f"{test.relative_to(root).as_posix()}: {name}"
        for test in tests
        if test.is_file()
        for name in _unresolved_imports(test, search_path)
    ]


def test_tracked_tests_resolve_imports_without_run_state() -> None:
    """追跡中のテストの裸の名前のimportが、実行の組合せによらず解決できる。"""
    violations = _violations(_ROOT, _tracked_tests(_ROOT), _installed_search_path())
    assert not violations, (
        "別ディレクトリのモジュールを裸の名前でimportしている。"
        "次の操作: 公開されたCLI（`agent_toolkit._atk.run_script.dispatch`など）経由で呼ぶか、"
        "テスト自身で`sys.path`へ対象ディレクトリを明示的に加える: " + ", ".join(violations)
    )


def test_cross_directory_import_is_reported(tmp_path: pathlib.Path) -> None:
    """別ディレクトリのモジュールを裸の名前でimportするテストを違反として報告する。"""
    (tmp_path / "scripts_a").mkdir()
    (tmp_path / "scripts_a" / "helper_only_here.py").write_text("VALUE = 1\n", encoding="utf-8")
    test = tmp_path / "scripts_b" / "sample_test.py"
    test.parent.mkdir()
    test.write_text("import helper_only_here\n", encoding="utf-8")

    assert _violations(tmp_path, [test], _installed_search_path()) == ["scripts_b/sample_test.py: helper_only_here"]


def test_same_directory_and_explicit_path_are_accepted(tmp_path: pathlib.Path) -> None:
    """同じディレクトリのモジュールと、`sys.path`へ明示的に加えた別ディレクトリのモジュールは受理する。"""
    (tmp_path / "helper_same.py").write_text("VALUE = 1\n", encoding="utf-8")
    same = tmp_path / "same_test.py"
    same.write_text("import json\nimport pytest\nimport helper_same\n", encoding="utf-8")
    explicit = tmp_path / "explicit_test.py"
    explicit.write_text(
        "import sys\nsys.path.insert(0, 'elsewhere')\nimport helper_elsewhere\n",
        encoding="utf-8",
    )

    assert not _violations(tmp_path, [same, explicit], _installed_search_path())
