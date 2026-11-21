"""agent_toolkitの起動時のモジュール依存を非循環に保つ。"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from importlib.util import resolve_name
from pathlib import Path

_PACKAGE = Path(__file__).with_name("agent_toolkit")


_IMPORT_END = re.compile(rb"(?m)^[ \t]*\)[ \t]*(?:#.*)?$")


def _source_tree(source: bytes, filename: str) -> ast.Module:
    # 最後の import キーワード以降の関数本体などを解析しない。
    # 改行までの切り出しで複数行文や文字列が途中になる場合は、閉じ括弧または全文まで読む。
    last_import = source.rfind(b"import")
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
    if isinstance(node, (ast.Assign, ast.Delete)):
        targets = node.targets
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = [node.target]
    elif isinstance(node, ast.TypeAlias):
        targets = [node.name]
    else:
        targets = []
    if targets:
        if bindings:
            for target in targets:
                for descendant in ast.walk(target):
                    if isinstance(descendant, ast.Name) and isinstance(descendant.ctx, (ast.Store, ast.Del)):
                        bindings.pop(descendant.id, None)
        return
    if isinstance(node, ast.Module):
        for statement in node.body:
            yield from _eager_imports(statement, bindings)
        return
    # 実行する分岐が分からない場合、分岐内の束縛を外側に持ち出さない。
    invalidated: set[str] = set()
    previous_bindings = bindings.copy()
    for field in ("body", "orelse", "finalbody", "handlers", "cases"):
        branch: list[ast.AST] = getattr(node, field, [])
        if branch:
            branch_bindings = previous_bindings.copy()
            for child in branch:
                yield from _eager_imports(child, branch_bindings)
            invalidated.update(name for name, value in previous_bindings.items() if branch_bindings.get(name) != value)
    for name in invalidated:
        del bindings[name]


def _dependencies(root: Path) -> dict[str, set[str]]:
    modules = {}
    for path in sorted(root.rglob("*.py")):
        if path.name.endswith("_test.py") or path.name == "conftest.py":
            continue
        parts = path.relative_to(root.parent).with_suffix("").parts
        name = ".".join(parts[:-1] if path.name == "__init__.py" else parts)
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
                # サブモジュールの読込と、パッケージ自身が公開する属性の読込を区別する。
                for alias in node.names:
                    submodule = f"{base}.{alias.name}"
                    targets.add(submodule if submodule in modules else base)
        targets.intersection_update(modules)
        # 呼び出し元自身とその親は既に初期化を開始している。再公開だけでは暗黙の逆辺を作らない。
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


def test_eager_imports_are_acyclic() -> None:
    graph = _dependencies(_PACKAGE)
    assert graph, f"検査対象が見つからない。作業ツリーに {_PACKAGE} のPythonファイルがあるか確認する。"
    cycles = _cycles(graph)
    assert not cycles, "起動時に循環するimportがある。共有する値や型を、双方が参照できる下位モジュールへ移す。\n" + "\n".join(
        " -> ".join(cycle) for cycle in cycles
    )


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

    graph = _dependencies(root)
    assert graph == {
        "sample": {"sample.first"},
        "sample.first": {"sample.second", "sample.third"},
        "sample.second": set(),
        "sample.third": set(),
    }
    assert not _cycles(graph)
    (root / "third.py").write_text("from . import VALUE\n", encoding="utf-8")
    assert _cycles(_dependencies(root)) == [["sample", "sample.first", "sample.third", "sample"]]


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
    assert not _cycles(_dependencies(root))

    for rebound in ("TC = True\nif TC:", "t = object()\nif t.TYPE_CHECKING:"):
        source.write_text(f"{guards}{rebound}\n    import sample\n", encoding="utf-8")
        assert _cycles(_dependencies(root)) == [["sample", "sample"]]


def test_cycle_through_parent_initializer(tmp_path: Path) -> None:
    root = tmp_path / "sample"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "first.py").write_text("import sample.nested.leaf\nVALUE = 1\n", encoding="utf-8")
    (nested / "__init__.py").write_text("from ..first import VALUE\n", encoding="utf-8")
    (nested / "leaf.py").write_text("", encoding="utf-8")

    assert _cycles(_dependencies(root)) == [["sample.first", "sample.nested", "sample.first"]]


def test_source_tree_keeps_last_multiline_import() -> None:
    source = b"def deferred():\n    import skip\nfrom package import (\n    member,\n)\nVALUE = 1\n"
    tree = _source_tree(source, "<sample>")
    assert len(tree.body) == 2
    imports = list(_eager_imports(tree, {}))
    assert [node.names[0].name for node in imports] == ["member"]
