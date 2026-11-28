from __future__ import annotations

import ast
import functools
import importlib.machinery
import json
import pathlib
import subprocess
import sys

import pytest

from scripts.verification_scan_rules import local_import_exists, scan_files

_ROOT = pathlib.Path(__file__).resolve().parent


@functools.cache
def _installed_search_path() -> tuple[str, ...]:
    result = subprocess.run(
        [sys.executable, "-I", "-c", "import json, sys; print(json.dumps(sys.path))"],
        cwd=_ROOT.anchor,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return tuple(json.loads(result.stdout))


def _ordinary_imports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"insert", "append"}
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "path"
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "sys"
        ):
            return set()
    return names


def _unresolved_imports(path: pathlib.Path, search_path: tuple[str, ...]) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    unresolved = []
    for name in sorted(_ordinary_imports(tree)):
        if local_import_exists(path.parent, name):
            continue
        if name in sys.stdlib_module_names or name in sys.builtin_module_names:
            continue
        if importlib.machinery.PathFinder.find_spec(name, list(search_path)) is None:
            unresolved.append(name)
    return unresolved


def _violations(root: pathlib.Path, tests: list[pathlib.Path], search_path: tuple[str, ...]) -> list[str]:
    return [
        f"{test.relative_to(root).as_posix()}: {name}"
        for test in tests
        if test.is_file()
        for name in _unresolved_imports(test, search_path)
    ]


@pytest.mark.cpu_limit(
    9.5, reason="全追跡試験のASTと導入済み検索パスを照合し、実行組合せ依存のimportを検出する（Linux CI CPU4.422秒）"
)
def test_tracked_tests_resolve_imports_without_run_state() -> None:
    violations = _violations(_ROOT, scan_files(__file__), _installed_search_path())
    assert not violations, (
        "別ディレクトリのモジュールを裸の名前でimportしている。"
        "次の操作: 公開されたCLI（`agent_toolkit._atk.run_script.dispatch`など）経由で呼ぶか、"
        "テスト自身で`sys.path`へ対象ディレクトリを明示的に加える: " + ", ".join(violations)
    )


def test_cross_directory_import_is_reported(tmp_path: pathlib.Path) -> None:
    (tmp_path / "scripts_a").mkdir()
    (tmp_path / "scripts_a" / "helper_only_here.py").write_text("VALUE = 1\n", encoding="utf-8")
    test = tmp_path / "scripts_b" / "sample_test.py"
    test.parent.mkdir()
    test.write_text("import helper_only_here\n", encoding="utf-8")

    assert _violations(tmp_path, [test], _installed_search_path()) == ["scripts_b/sample_test.py: helper_only_here"]


def test_same_directory_and_explicit_path_are_accepted(tmp_path: pathlib.Path) -> None:
    (tmp_path / "helper_same.py").write_text("VALUE = 1\n", encoding="utf-8")
    same = tmp_path / "same_test.py"
    same.write_text("import json\nimport pytest\nimport helper_same\n", encoding="utf-8")
    explicit = tmp_path / "explicit_test.py"
    explicit.write_text(
        "import sys\nsys.path.insert(0, 'elsewhere')\nimport helper_elsewhere\n",
        encoding="utf-8",
    )

    assert not _violations(tmp_path, [same, explicit], _installed_search_path())
