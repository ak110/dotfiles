"""登録されたhookと出力スキーマ・テストコードの対応を検証する。"""

import ast
import pathlib

import pytest

from agent_toolkit._hooks import output_contract_test as cases
from agent_toolkit._testing.hook_output_contract import HOOK_OUTPUT_SCHEMAS


def test_fixture_table_covers_every_registered_hook() -> None:
    assert set(cases._FIXTURES) == cases._registered_hooks()  # pylint: disable=protected-access
    assert set(HOOK_OUTPUT_SCHEMAS) == {
        event_name
        for event_name, _ in cases._registered_hooks()  # pylint: disable=protected-access
    }


def _output_offenders(source: str, path: pathlib.Path) -> list[int]:
    """診断の直接出力と、配送欄へ連結した未標識の固定本文を抽出する。"""
    offenders = []
    tree = ast.parse(source)
    permitted: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and (
            path.name == "dispatch.py"
            and node.name in {"_emit", "exit_with"}
            or path.name == "background_tasks.py"
            and node.name == "_emit_debug"
        ):
            permitted.update(id(child) for child in ast.walk(node))
    for node in ast.walk(tree):
        if id(node) in permitted:
            continue
        if isinstance(node, ast.Call):
            direct = (
                any(keyword.arg == "file" and ast.unparse(keyword.value) == "sys.stderr" for keyword in node.keywords)
                or isinstance(node.func, ast.Attribute)
                and ast.unparse(node.func) == "sys.stderr.write"
            )
            if direct:
                offenders.append(node.lineno)
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value in {
                    "additionalContext",
                    "permissionDecisionReason",
                    "ephemeralMessage",
                }:
                    parts = ast.walk(value) if isinstance(value, ast.BinOp) else (value,)
                    if any(
                        isinstance(part, ast.Constant) and isinstance(part.value, str) and part.value.strip() for part in parts
                    ):
                        offenders.append(value.lineno)
    return offenders


def test_production_output_has_no_direct_diagnostics_or_unmarked_literals() -> None:
    root = pathlib.Path(__file__).parent
    paths = [*root.rglob("*.py"), root.parent / "hook.py"]
    failures = {
        str(path): lines
        for path in paths
        if not path.name.endswith("_test.py")
        if (lines := _output_offenders(path.read_text(encoding="utf-8"), path))
    }
    assert not failures


@pytest.mark.parametrize(
    "source",
    [
        'print("診断", file=sys.stderr)',
        'sys.stderr.write("診断")',
        '{"additionalContext": "未標識の本文" + notice}',
        '{"ephemeralMessage": "未標識の本文"}',
    ],
)
def test_output_invariant_rejects_introduced_bad_output(source: str) -> None:
    assert _output_offenders(source, pathlib.Path("posttooluse.py"))
