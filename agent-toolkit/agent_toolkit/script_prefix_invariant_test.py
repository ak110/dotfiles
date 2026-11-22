from __future__ import annotations

import ast
import pathlib

_EXEMPT_ENTRIES: frozenset[str] = frozenset()


def _has_main_guard(path: pathlib.Path) -> bool:
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
    scripts_dir = pathlib.Path(__file__).resolve().parent
    paths = sorted(scripts_dir.glob("*.py"))

    violations = _standalone_private_prefixed_scripts(paths)

    assert violations == []
