import ast
import pathlib
import textwrap

import pytest
from agent_toolkit._git import command

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_STREAM_LINE_DECODERS = {
    "agent_toolkit._common.claude_usage_limit": {
        "from_stream_lines",
        "result_from_stream_lines",
        "session_id_from_stream_lines",
    },
    "agent_toolkit._common.codex_usage_limit": {"thread_id_from_stream_lines"},
}


def _name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name(node.value)}.{node.attr}"
    return ""


def _names(node: ast.AST) -> set[str]:
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}


def _split_source(value: ast.AST, sequences: set[str]) -> bool:
    if isinstance(value, ast.Name):
        return value.id in sequences
    if isinstance(value, ast.Subscript) and isinstance(value.slice, ast.Slice):
        return _split_source(value.value, sequences)
    if isinstance(value, ast.Call):
        if isinstance(value.func, ast.Attribute) and value.func.attr == "splitlines":
            return True
        if _name(value.func) in {"enumerate", "reversed", "iter", "list", "tuple"} and value.args:
            return _split_source(value.args[0], sequences)
    return False


def _item_source(value: ast.AST, items: set[str]) -> bool:
    if isinstance(value, ast.Name):
        return value.id in items
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and value.func.attr in {"strip", "lstrip", "rstrip", "decode", "encode"}
        and _item_source(value.func.value, items)
    )


def _violations(source: str, filename: str = "<sample>") -> list[int]:
    tree = ast.parse(source, filename=filename)
    functions = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    decoders = {"json.loads", "json.load", "_json_object"}
    decoders.update(
        node.asname or node.name
        for statement in ast.walk(tree)
        if isinstance(statement, ast.ImportFrom) and statement.module == "json"
        for node in statement.names
        if node.name in {"loads", "load"}
    )
    stream_decoders: set[str] = set()

    for statement in ast.walk(tree):
        if isinstance(statement, ast.Import):
            for imported in statement.names:
                binding = imported.asname or imported.name
                if imported.name == "json":
                    decoders.update(f"{binding}.{name}" for name in ("loads", "load"))
                stream_decoders.update(f"{binding}.{name}" for name in _STREAM_LINE_DECODERS.get(imported.name, set()))
        elif isinstance(statement, ast.ImportFrom):
            for imported in statement.names:
                binding = imported.asname or imported.name
                if imported.name in _STREAM_LINE_DECODERS.get(statement.module or "", set()):
                    stream_decoders.add(binding)
                qualified = f"{statement.module}.{imported.name}"
                stream_decoders.update(f"{binding}.{name}" for name in _STREAM_LINE_DECODERS.get(qualified, set()))
    while True:
        expanded = decoders | {
            function.name
            for function in functions
            if any(isinstance(node, ast.Call) and _name(node.func) in decoders for node in ast.walk(function))
        }
        if expanded == decoders:
            break
        decoders = expanded
    violations: set[int] = set()

    module = ast.Module(
        body=[node for node in tree.body if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    )
    for scope in [module, *functions]:
        nodes = list(ast.walk(scope))
        sequences: set[str] = set()
        items: set[str] = set()

        while True:
            before = sequences.copy(), items.copy()
            for node in nodes:
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    assigned = set().union(*(_names(target) for target in targets))
                    if _split_source(node.value, sequences):
                        sequences.update(assigned)
                    if _item_source(node.value, items):
                        items.update(assigned)
                elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)) and _split_source(node.iter, sequences):
                    items.update(_names(node.target))
            if before == (sequences, items):
                break
        for node in nodes:
            if isinstance(node, ast.Call) and (
                _name(node.func) in decoders
                and any(_names(arg) & items for arg in node.args)
                or _name(node.func) in stream_decoders
                and any(_split_source(arg, sequences) for arg in node.args)
            ):
                violations.add(node.lineno)
    return sorted(violations)


@pytest.mark.cpu_limit(
    6.0,
    reason=("追跡本体Python全量を読み、JSON Lines読取でLF以外を行区切りにする処理を検出する（Linux CI CPU合計最大2.834秒）。"),
)
def test_json_lines_readers_use_lf() -> None:
    result = command.run(["ls-files", "-z", "--", "*.py"], _ROOT, capture_output=True, text=True, check=True)
    paths = [pathlib.Path(name) for name in result.stdout.split("\0") if name]
    assert paths
    violations: list[str] = []
    for path in paths:
        if (
            path.name.endswith("_test.py")
            or path.name.startswith("test_")
            or path.name == "conftest.py"
            or {"_testing", "tests"} & set(path.parts)
        ):
            continue
        source = (_ROOT / path).read_text(encoding="utf-8-sig")
        if "splitlines" not in source:
            continue
        violations.extend(f"{path}:{line}" for line in _violations(source, str(path)))
    assert not violations, "JSON Linesの分割には_common.json_lines.split_linesを使う:\n" + "\n".join(violations)


@pytest.mark.parametrize(
    "source",
    [
        "for line in text.splitlines():\n    json.loads(line)",
        "lines = text.splitlines()\nfor line in lines:\n    stripped = line.strip()\n    json.loads(stripped)",
        "for number, line in enumerate(text.splitlines(), 1):\n    json.loads(line)",
        "rows = [json.loads(line) for line in text.splitlines()]",
        "rows = {number: json.loads(line) for number, line in enumerate(text.splitlines())}",
        "lines = text.splitlines()\nrows = (json.loads(line) for line in lines)",
        "def decode(value):\n    return json.loads(value)\nfor line in text.splitlines():\n    decode(line)",
    ],
)
def test_detects_old_json_lines_splitting(source: str) -> None:
    assert _violations(textwrap.dedent(source))


@pytest.mark.parametrize(
    ("module", "decoder"),
    [
        ("claude_usage_limit", "from_stream_lines"),
        ("claude_usage_limit", "result_from_stream_lines"),
        ("claude_usage_limit", "session_id_from_stream_lines"),
        ("codex_usage_limit", "thread_id_from_stream_lines"),
    ],
)
@pytest.mark.parametrize("style", ["module", "module_alias", "function_alias"])
def test_detects_imported_stream_decoders(module: str, decoder: str, style: str) -> None:
    if style == "module":
        declaration, call = f"import agent_toolkit._common.{module}", f"agent_toolkit._common.{module}.{decoder}"
    elif style == "module_alias":
        declaration, call = f"from agent_toolkit._common import {module} as usage", f"usage.{decoder}"
    else:
        declaration, call = f"from agent_toolkit._common.{module} import {decoder} as decode", "decode"
    assert _violations(declaration + f"\nlines = result.stdout.splitlines()\n{call}(lines)") == [3]
    assert _violations(declaration + f"\n{call}(result.stdout.splitlines())") == [2]
    assert not _violations(declaration + f"\n{call}(split_lines(result.stdout))")


def test_detects_json_module_alias() -> None:
    assert _violations("import json as decoder\nfor line in text.splitlines():\n    decoder.loads(line)") == [3]


def test_unrelated_imported_function_is_not_stream_decoder() -> None:
    assert not _violations("from text_processing import from_stream_lines\nfrom_stream_lines(text.splitlines())")


@pytest.mark.parametrize(
    "source",
    [
        "for line in split_lines(text):\n    json.loads(line)",
        "for line in text.splitlines():\n    print(line)",
        "for line in text.splitlines():\n    json.loads(other)",
        "def first(text):\n    lines = text.splitlines()\n    return lines\n"
        "def second(lines):\n    for line in lines:\n        json.loads(line)",
    ],
)
def test_accepts_lf_and_non_json_processing(source: str) -> None:
    assert not _violations(textwrap.dedent(source))
