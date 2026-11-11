"""agent-toolkit参照の主題別不変条件を検証する。"""

from __future__ import annotations

import ast
import pathlib

import pytest
from agent_toolkit._plan.structure import is_agent_doc_target_file

from _test_helpers import _tracked_source_paths


def _path_values(node: ast.AST, values: dict[str, list[pathlib.Path]], source: pathlib.Path) -> list[pathlib.Path]:
    """静的に確定できるpathlib式をパスの一覧へ変換する。"""
    if isinstance(node, ast.Name):
        if node.id == "__file__":
            return [source]
        return values.get(node.id, [])
    if isinstance(node, ast.Call):
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in {"Path", "PurePath"}
            or isinstance(node.func, ast.Attribute)
            and node.func.attr in {"Path", "PurePath"}
        ) and node.args:
            return _path_values(node.args[0], values, source) or (
                [pathlib.Path(node.args[0].value)]
                if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
                else []
            )
        if isinstance(node.func, ast.Attribute):
            bases = _path_values(node.func.value, values, source)
            if node.func.attr == "resolve":
                return [path.resolve() for path in bases]
            if (
                node.func.attr == "with_name"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                return [path.with_name(node.args[0].value) for path in bases]
            if node.func.attr == "joinpath":
                parts = [arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
                return [path.joinpath(*parts) for path in bases] if len(parts) == len(node.args) else []
            if (
                node.func.attr == "glob"
                and len(node.args) == 1
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                return [candidate for path in bases for candidate in path.glob(node.args[0].value)]
        if isinstance(node.func, ast.Name) and node.func.id == "sorted" and node.args:
            return _path_values(node.args[0], values, source)
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        return [path.parent for path in _path_values(node.value, values, source)]
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and node.value.attr == "parents":
        bases = _path_values(node.value.value, values, source)
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int):
            return [path.parents[node.slice.value] for path in bases]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        bases = _path_values(node.left, values, source)
        if isinstance(node.right, ast.Constant) and isinstance(node.right.value, str):
            return [path / node.right.value for path in bases]
    return []


def _read_paths(node: ast.AST, values: dict[str, list[pathlib.Path]], source: pathlib.Path) -> list[pathlib.Path]:
    """構文木の呼び出しから直接読取り対象を返す。"""
    paths: list[pathlib.Path] = []
    for candidate in ast.walk(node):
        if (
            isinstance(candidate, ast.Call)
            and isinstance(candidate.func, ast.Attribute)
            and candidate.func.attr in _READ_METHODS
        ):
            paths.extend(_path_values(candidate.func.value, values, source))
    return paths


def _function_reads(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    module_values: dict[str, list[pathlib.Path]],
    source: pathlib.Path,
) -> list[pathlib.Path]:
    """関数内の代入と反復をたどって直接読取り対象を返す。"""
    reads: list[pathlib.Path] = []

    def visit(statements: list[ast.stmt], values: dict[str, list[pathlib.Path]]) -> None:
        for statement in statements:
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
                values[statement.targets[0].id] = _path_values(statement.value, values, source)
            if isinstance(statement, (ast.For, ast.AsyncFor)) and isinstance(statement.target, ast.Name):
                nested = dict(values)
                nested[statement.target.id] = _path_values(statement.iter, values, source)
                visit(statement.body, nested)
                visit(statement.orelse, dict(values))
                continue
            reads.extend(_read_paths(statement, values, source))

    visit(function.body, dict(module_values))
    return reads


def _agent_doc_reader_violations(repository: pathlib.Path, sources: list[pathlib.Path]) -> list[str]:
    """通常テストから追跡中のエージェント向け文書を読む関数を列挙する。"""
    violations: list[str] = []
    for relative_source in sources:
        if relative_source.name.endswith("_invariant_test.py"):
            continue
        source = repository / relative_source
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        module_values: dict[str, list[pathlib.Path]] = {}
        for statement in tree.body:
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
                module_values[statement.targets[0].id] = _path_values(statement.value, module_values, source)
        pending: list[ast.AST] = [tree]
        while pending:
            function = pending.pop()
            # 関数定義は文の配下にある。巨大なfixture文字列や式は関数探索で走査し直さない。
            pending.extend(
                child
                for child in ast.iter_child_nodes(function)
                if isinstance(child, (ast.stmt, ast.ExceptHandler, ast.match_case))
            )
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for path in _function_reads(function, module_values, source):
                try:
                    relative = path.resolve().relative_to(repository)
                except ValueError:
                    continue
                if path.is_file() and is_agent_doc_target_file(relative):
                    violations.append(f"{relative_source}::{function.name} -> {relative}")
    return sorted(set(violations))


def test_agent_doc_readers_are_in_invariant_tests() -> None:
    """追跡中のエージェント向け文書を読むテストをfast対象へ置く。"""
    repository = pathlib.Path(__file__).resolve().parent
    sources = [path for path in _tracked_source_paths(repository) if path.name.endswith("_test.py")]

    violations = _agent_doc_reader_violations(repository, sources)
    assert not violations, "エージェント向けMarkdownを直接読むテストを同じ領域の*_invariant_test.pyへ移す: " + ", ".join(
        violations
    )


def test_agent_doc_reader_detector_ignores_fixture_paths_and_reports_repository_documents(
    tmp_path: pathlib.Path,
) -> None:
    """一時fixtureを除外し、実リポジトリの文書読取りだけを配置違反にする。"""
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "AGENTS.md").write_text("# 規範\n", encoding="utf-8")
    source = repository / "sample_test.py"
    source.write_text(
        "import pathlib\n"
        "def test_reads(tmp_path):\n"
        "    pathlib.Path(__file__).with_name('AGENTS.md').read_text(encoding='utf-8')\n"
        "    (tmp_path / 'AGENTS.md').read_text(encoding='utf-8')\n"
        "def test_glob():\n"
        "    for path in pathlib.Path(__file__).parent.glob('*.md'):\n"
        "        path.read_bytes()\n",
        encoding="utf-8",
    )

    assert _agent_doc_reader_violations(repository, [source.relative_to(repository)]) == [
        "sample_test.py::test_glob -> AGENTS.md",
        "sample_test.py::test_reads -> AGENTS.md",
    ]


@pytest.mark.parametrize("method", ["read_text", "ｒｅａｄ＿ｔｅｘｔ"])
@pytest.mark.parametrize(
    ("container", "names"),
    [
        ("class Example:\n{function}", ["read"]),
        ("if True:\n{function}", ["read"]),
        ("try:\n    pass\nexcept ValueError:\n{function}", ["read"]),
        ("match 1:\n    case 1:\n{nested_function}", ["read"]),
        ("def outer():\n{function}", ["outer", "read"]),
    ],
)
def test_agent_doc_reader_detector_keeps_nested_definitions(tmp_path, container, names, method):
    """文に入れ子になった関数とUnicode正規化される属性も、式の走査を省いた探索で検出する。"""
    (tmp_path / "AGENTS.md").write_text("# 規範\n", encoding="utf-8")
    function = f"    def read():\n        pathlib.Path(__file__).with_name('AGENTS.md').{method}()\n"
    source = tmp_path / "example_test.py"
    source.write_text(
        "import pathlib\n" + container.format(function=function, nested_function="    " + function.replace("\n", "\n    ")),
        encoding="utf-8",
    )

    assert _agent_doc_reader_violations(tmp_path, [source.relative_to(tmp_path)]) == [
        f"example_test.py::{name} -> AGENTS.md" for name in names
    ]


_READ_METHODS = {"read_text", "read_bytes"}
