"""agent_toolkitの起動時のモジュール依存を非循環に保つ。"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Iterator
from importlib.util import resolve_name
from pathlib import Path

import pytest

from scripts.verification_scan_rules import scan_files

_PACKAGE = Path(__file__).with_name("agent_toolkit")


_IMPORT_END = re.compile(rb"(?m)^[ \t]*\)[ \t]*(?:#.*)?$")
_ASCII_IDENTIFIER_BYTES = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_"


def _source_tree(source: bytes, filename: str) -> ast.Module:
    raw_last_import = source.rfind(b"import")
    last_import = raw_last_import
    while last_import >= 0:
        after = last_import + len(b"import")
        if (last_import == 0 or source[last_import - 1] not in _ASCII_IDENTIFIER_BYTES) and (
            after == len(source) or source[after] not in _ASCII_IDENTIFIER_BYTES
        ):
            break
        last_import = source.rfind(b"import", 0, last_import)
    if last_import >= 0 and last_import != raw_last_import and b"\r" not in source:
        first_newline = source.find(b"\n")
        second_newline = source.find(b"\n", first_newline + 1) if first_newline >= 0 else -1
        prefix_end = second_newline if second_newline >= 0 else len(source)
        start = source.rfind(b"\n", 0, last_import) + 1
        if b"coding" not in source[:prefix_end] and source.startswith((b"import", b"from", b"lazy"), start):
            tree = _parse_to_import(source, filename, last_import)
            line = source.count(b"\n", 0, last_import) + 1
            if any(
                isinstance(node, (ast.Import, ast.ImportFrom)) and node.lineno <= line <= (node.end_lineno or node.lineno)
                for node in tree.body
            ):
                return tree
    return _parse_to_import(source, filename, raw_last_import)


def _parse_to_import(source: bytes, filename: str, last_import: int) -> ast.Module:
    if last_import < 0:
        return ast.Module(body=[], type_ignores=[])
    end = source.find(b"\n", last_import)
    if end >= 0:
        closing = _IMPORT_END.search(source, end, end + 700)
        if closing:
            end = closing.end()
        for limit in (end, source.find(b"\n", end + 700), source.find(b"\n", end + 3000)):
            if limit < 0:
                break
            try:
                return ast.parse(source[: limit + 1], filename=filename)
            except SyntaxError:
                continue
    return ast.parse(source, filename=filename)


def _is_type_guard(node: ast.expr, bindings: dict[str, str]) -> bool:
    if isinstance(node, ast.Name):
        return bindings.get(node.id) == "TYPE_CHECKING"
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "TYPE_CHECKING"
        and isinstance(node.value, ast.Name)
        and bindings.get(node.value.id) == "typing"
    )


def _rebound_names(node: ast.AST) -> set[str]:

    names: set[str] = set()
    pending = [node]
    while pending:
        current = pending.pop()
        if isinstance(current, ast.Name) and isinstance(current.ctx, (ast.Store, ast.Del)):
            names.add(current.id)
        elif (
            isinstance(current, ast.ExceptHandler)
            and current.name
            or isinstance(current, (ast.MatchAs, ast.MatchStar))
            and current.name
        ):
            names.add(current.name)
        elif isinstance(current, ast.MatchMapping) and current.rest:
            names.add(current.rest)
        nested_scope = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
        if current is not node and isinstance(current, nested_scope):
            continue
        pending.extend(ast.iter_child_nodes(current))
    return names


def _eager_imports(node: ast.AST, bindings: dict[str, str]) -> Iterator[ast.Import | ast.ImportFrom]:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        if not node.is_lazy:
            yield node
        for alias in node.names:
            name = alias.asname or alias.name.partition(".")[0]
            bindings.pop(name, None)
            if isinstance(node, ast.Import) and alias.name == "typing":
                bindings[name] = "typing"
            elif (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module == "typing"
                and alias.name == "TYPE_CHECKING"
            ):
                bindings[name] = "TYPE_CHECKING"
        return
    if isinstance(node, ast.If) and _is_type_guard(node.test, bindings):
        for statement in node.orelse:
            yield from _eager_imports(statement, bindings)
        return
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        if isinstance(node, ast.ClassDef):
            local_bindings = bindings.copy()
            for statement in node.body:
                yield from _eager_imports(statement, local_bindings)
        bindings.pop(node.name, None)
        return
    if isinstance(node, ast.Module):
        for statement in node.body:
            yield from _eager_imports(statement, bindings)
        return
    # 型検査ガードの名前を別の値へ束縛し直しうる文は、その名前をガードとみなさない（安全側に倒す）。
    if bindings:
        for name in _rebound_names(node) & bindings.keys():
            del bindings[name]

    invalidated: set[str] = set()
    for field in node._fields:
        if field not in {"body", "orelse", "finalbody", "handlers", "cases"}:
            continue
        branch: list[ast.AST] = getattr(node, field)
        if branch:
            branch_bindings = bindings.copy()
            for child in branch:
                yield from _eager_imports(child, branch_bindings)
            invalidated.update(name for name, value in bindings.items() if branch_bindings.get(name) != value)
    for name in invalidated:
        del bindings[name]


def _dependencies(root: Path, paths: Iterable[Path]) -> dict[str, set[str]]:
    modules = {}
    root_offset = len(root.parent.parts)
    for path in paths:
        parts = path.parts[root_offset:-1]
        name = ".".join(parts if path.name == "__init__.py" else (*parts, path.stem))
        modules[name] = path

    graph: dict[str, set[str]] = {}
    for name, path in modules.items():
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        targets: set[str] = set()
        for node in _eager_imports(_source_tree(path.read_bytes(), str(path)), {}):
            if isinstance(node, ast.Import):
                targets.update(alias.name for alias in node.names)
            else:
                base = resolve_name("." * node.level + (node.module or ""), package)

                for alias in node.names:
                    submodule = f"{base}.{alias.name}"
                    targets.add(submodule if submodule in modules else base)
        targets = {target for target in targets if target in modules}

        for target in tuple(targets):
            parent = target.rpartition(".")[0]
            while parent:
                if parent in modules and name != parent and not name.startswith(parent + "."):
                    targets.add(parent)
                parent = parent.rpartition(".")[0]
        graph[name] = targets
    return graph


def _cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    visited: set[str] = set()
    active: dict[str, int] = {}
    stack: list[str] = []
    cycles: list[list[str]] = []

    def visit(name: str) -> None:
        active[name] = len(stack)
        stack.append(name)
        for target in sorted(graph[name]):
            if target in active:
                cycles.append([*stack[active[target] :], target])
            elif target not in visited:
                visit(target)
        stack.pop()
        del active[name]
        visited.add(name)

    for name in graph:
        if name not in visited:
            visit(name)
    return cycles


@pytest.mark.cpu_limit(
    0.60,
    reason=(
        "CI実測は0.717〜0.758秒。走査削減後も構文解析が約7割で、検出条件を保つ削減には限界がある。"
        "CI上限0.84秒は観測最大値に約1割の余裕を持たせ、観測範囲の2割超の悪化は検出する。"
    ),
)
def test_eager_imports_are_acyclic() -> None:
    graph = _dependencies(_PACKAGE, scan_files(__file__))
    assert graph, f"検査対象が見つからない。作業ツリーに {_PACKAGE} のPythonファイルがあるか確認する。"
    cycles = _cycles(graph)
    assert not cycles, "起動時に循環するimportがある。共有する値や型を、双方が参照できる下位モジュールへ移す。\n" + "\n".join(
        " -> ".join(cycle) for cycle in cycles
    )


def _sample_dependencies(root: Path) -> dict[str, set[str]]:
    return _dependencies(root, sorted(root.rglob("*.py")))


def test_dependency_resolution(tmp_path: Path) -> None:
    root = tmp_path / "sample"
    root.mkdir()
    (root / "__init__.py").write_text("from .first import VALUE\n", encoding="utf-8")
    (root / "first.py").write_text(
        "from . import second\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from . import first\n"
        "else:\n"
        "    import sample.third\n"
        "lazy from . import first\n"
        "def deferred():\n"
        "    from . import first\n",
        encoding="utf-8",
    )
    (root / "second.py").write_text("", encoding="utf-8")
    (root / "third.py").write_text("", encoding="utf-8")

    graph = _sample_dependencies(root)
    assert graph == {
        "sample": {"sample.first"},
        "sample.first": {"sample.second", "sample.third"},
        "sample.second": set(),
        "sample.third": set(),
    }
    assert not _cycles(graph)
    (root / "third.py").write_text("from . import VALUE\n", encoding="utf-8")
    assert _cycles(_sample_dependencies(root)) == [["sample", "sample.first", "sample.third", "sample"]]


def test_aliased_type_checking_guard(tmp_path: Path) -> None:
    root = tmp_path / "sample"
    root.mkdir()
    source = root / "__init__.py"
    guards = (
        "import typing as t\n"
        "from typing import TYPE_CHECKING as TC\n"
        "if t.TYPE_CHECKING:\n"
        "    import sample\n"
        "if TC:\n"
        "    import sample\n"
    )
    source.write_text(guards, encoding="utf-8")
    assert not _cycles(_sample_dependencies(root))

    rebinds = (
        "TC = True\nif TC:",
        "t = object()\nif t.TYPE_CHECKING:",
        "for TC in (True,):\n    pass\nif TC:",
        "with open(__file__) as TC:\n    pass\nif TC:",
        "(TC := True)\nif TC:",
        "x = (TC := True)\nif TC:",
        "try:\n    pass\nexcept Exception as TC:\n    pass\nif TC:",
    )
    for rebound in rebinds:
        source.write_text(f"{guards}{rebound}\n    import sample\n", encoding="utf-8")
        assert _cycles(_sample_dependencies(root)) == [["sample", "sample"]], rebound

    source.write_text(f"{guards}for TC in (True,):\n    if TC:\n        import sample\n", encoding="utf-8")
    assert _cycles(_sample_dependencies(root)) == [["sample", "sample"]]


def test_cycle_through_parent_initializer(tmp_path: Path) -> None:
    root = tmp_path / "sample"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "first.py").write_text("import sample.nested.leaf\nVALUE = 1\n", encoding="utf-8")
    (nested / "__init__.py").write_text("from ..first import VALUE\n", encoding="utf-8")
    (nested / "leaf.py").write_text("", encoding="utf-8")

    assert _cycles(_sample_dependencies(root)) == [["sample.first", "sample.nested", "sample.first"]]


def test_source_tree_keeps_last_multiline_import() -> None:
    source = b"def deferred():\n    import skip\nfrom package import (\n    member,\n)\nVALUE = 1\nimports_ready = True\n"
    tree = _source_tree(source, "<sample>")
    assert len(tree.body) == 2
    imports = list(_eager_imports(tree, {}))
    assert [node.names[0].name for node in imports] == ["member"]


def test_source_tree_keeps_later_rebinding_in_loop_with_import() -> None:
    source = (
        b"from typing import TYPE_CHECKING\n"
        b"for _ in (0, 1):\n"
        b"    if TYPE_CHECKING:\n"
        b"        import sample\n"
        b"    TYPE_CHECKING = True\n"
        b"    imports_ready = True\n"
    )
    sources = (
        source,
        source.replace(b"\n", b"\r", 3),
        b"# coding: unicode_escape\n" + source.replace(b"\n", b"\\n", 3),
    )
    for sample in sources:
        imports = _eager_imports(_source_tree(sample, "<sample>"), {})
        assert [node.names[0].name for node in imports] == ["TYPE_CHECKING", "sample"]
