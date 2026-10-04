"""agent-toolkit直下の実行スクリプト名が公開配置の接頭辞規約に従うことを検証する。"""

from __future__ import annotations

import ast
import pathlib

_EXEMPT_ENTRIES = frozenset({"_managed_temp.py"})


def _has_main_guard(path: pathlib.Path) -> bool:
    """モジュールが`__main__`ガードを持つかを返す。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        and any(isinstance(comparator, ast.Constant) and comparator.value == "__main__" for comparator in node.test.comparators)
        for node in tree.body
    )


def _imported_module_names(paths: list[pathlib.Path]) -> set[str]:
    """対象スクリプト群がimportするモジュール名を返す。"""
    imported: set[str] = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
    return imported


def _standalone_private_prefixed_scripts(paths: list[pathlib.Path]) -> list[str]:
    """対応テスト以外から読み込まれない接頭辞付きの実行ファイルを返す。"""
    imports_by_path = {path: _imported_module_names([path]) for path in paths}
    return [
        path.name
        for path in paths
        if not path.name.endswith("_test.py")
        and _has_main_guard(path)
        and not any(
            path.stem in imported and source.name != f"{path.stem}_test.py" for source, imported in imports_by_path.items()
        )
        and path.stem.startswith("_")
        and path.name not in _EXEMPT_ENTRIES
    ]


def test_standalone_scripts_do_not_use_private_prefix() -> None:
    """単独実行される非テストスクリプトには非公開接頭辞を許さない。"""
    scripts_dir = pathlib.Path(__file__).resolve().parent
    paths = sorted(scripts_dir.glob("*.py"))

    violations = _standalone_private_prefixed_scripts(paths)

    assert violations == []
