from __future__ import annotations

import ast
import pathlib
import unicodedata
from bisect import bisect_left

import pytest
from agent_toolkit._plan.structure import is_agent_doc_target_file

from _test_helpers import _tracked_source_paths


def _path_values(node: ast.AST, values: dict[str, list[pathlib.Path]], source: pathlib.Path) -> list[pathlib.Path]:
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
    paths: list[pathlib.Path] = []
    for candidate in ast.walk(node):
        if (
            isinstance(candidate, ast.Call)
            and isinstance(candidate.func, ast.Attribute)
            and candidate.func.attr in _READ_METHODS
        ):
            paths.extend(_path_values(candidate.func.value, values, source))
    return paths


def _read_name_lines(text: str) -> list[int]:
    lines: list[int] = []
    for line_number, line in enumerate(text.split("\n"), start=1):
        # Pythonの識別子は物理行をまたがず、ASTではNFKCで正規化される。  # pyfltr: allow-comment
        if not line.isascii():
            line = unicodedata.normalize("NFKC", line)
        if any(method in line for method in _READ_METHODS):
            lines.append(line_number)
    return lines


def _function_reads(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    module_values: dict[str, list[pathlib.Path]],
    source: pathlib.Path,
    read_lines: list[int],
) -> list[pathlib.Path]:
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
            if not read_lines:
                continue
            # デコレータはstatementのlinenoより前でも、従来のast.walkは読む。  # pyfltr: allow-comment
            start = statement.lineno
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and statement.decorator_list:
                start = statement.decorator_list[0].lineno
            position = bisect_left(read_lines, start)
            if position < len(read_lines) and (statement.end_lineno is None or read_lines[position] <= statement.end_lineno):
                reads.extend(_read_paths(statement, values, source))

    visit(function.body, dict(module_values))
    return reads


def _agent_doc_reader_violations(repository: pathlib.Path, sources: list[pathlib.Path]) -> list[str]:
    violations: list[str] = []
    for relative_source in sources:
        if relative_source.name.endswith("_invariant_test.py"):
            continue
        source = repository / relative_source
        text = source.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(source))
        read_lines = _read_name_lines(text)
        module_values: dict[str, list[pathlib.Path]] = {}
        for statement in tree.body:
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
                module_values[statement.targets[0].id] = _path_values(statement.value, module_values, source)
        pending: list[ast.AST] = [tree]
        while pending:
            function = pending.pop()

            pending.extend(
                child
                for child in ast.iter_child_nodes(function)
                if isinstance(child, (ast.stmt, ast.ExceptHandler, ast.match_case))
            )
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for path in _function_reads(function, module_values, source, read_lines):
                try:
                    relative = path.resolve().relative_to(repository)
                except ValueError:
                    continue
                if path.is_file() and is_agent_doc_target_file(relative):
                    violations.append(f"{relative_source}::{function.name} -> {relative}")
    return sorted(set(violations))


@pytest.mark.cpu_limit(
    10.73, reason="全追跡試験のASTと読取先を解析する。判定CPU秒はLinux単独2.629〜2.846、4並列3.116〜3.227、CI失敗時10.720"
)
def test_agent_doc_readers_are_in_invariant_tests() -> None:
    repository = pathlib.Path(__file__).resolve().parent
    sources = [path for path in _tracked_source_paths(repository) if path.name.endswith("_test.py")]

    violations = _agent_doc_reader_violations(repository, sources)
    assert not violations, "エージェント向けMarkdownを直接読むテストを同じ領域の*_invariant_test.pyへ移す: " + ", ".join(
        violations
    )


def test_agent_doc_reader_detector_ignores_fixture_paths_and_reports_repository_documents(
    tmp_path: pathlib.Path,
) -> None:
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


def test_agent_doc_reader_detector_keeps_multiline_decorators(tmp_path: pathlib.Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# 規範\n", encoding="utf-8")
    source = tmp_path / "sample_test.py"
    source.write_text(
        "from pathlib import Path\n"
        "def outer():\n"
        "    marker = '\u2028\u0085\v\f'\n"
        "    @(Path(__file__).parent\n"
        "      .joinpath('AGENTS.md').ｒｅａｄ＿ｔｅｘｔ())\n"
        "    def nested():\n"
        "        pass\n",
        encoding="utf-8",
    )

    assert _agent_doc_reader_violations(tmp_path, [source.relative_to(tmp_path)]) == ["sample_test.py::outer -> AGENTS.md"]


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (b"\xff", UnicodeDecodeError),
        (b"def invalid(\n", SyntaxError),
        (b"def invalid():\n    path = Path(__file__).parents[1000]\n", IndexError),
    ],
)
def test_agent_doc_reader_detector_keeps_errors_without_read_names(
    tmp_path: pathlib.Path, data: bytes, error: type[Exception]
) -> None:
    source = tmp_path / "sample_test.py"
    source.write_bytes(data)

    with pytest.raises(error):
        _agent_doc_reader_violations(tmp_path, [source.relative_to(tmp_path)])


_READ_METHODS = {"read_text", "read_bytes"}
