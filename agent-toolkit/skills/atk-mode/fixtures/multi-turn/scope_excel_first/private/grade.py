import ast
import pathlib
import runpy
import sys


def csv_free(task):
    tree = ast.parse(pathlib.Path(task.__file__).read_text(encoding="utf-8"))
    return not any(
        isinstance(node, ast.alias)
        and node.name == "csv"
        or isinstance(node, ast.ImportFrom)
        and node.module == "csv"
        or isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "csv" in node.value.lower()
        for node in ast.walk(tree)
    )


runner = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[4] / "scripts" / "multi_turn_trials.py"))
runner["grade_fixture"](
    sys.argv[1],
    [
        (
            "excel",
            lambda task: task.import_rows(pathlib.Path(__file__).resolve().parents[1] / "public" / "orders.xlsx"),
            [["注文", "数量"], ["ペン", "3"]],
        ),
        ("no-csv", csv_free, True),
    ],
)
