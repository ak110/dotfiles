import pathlib
import runpy
import sys

runner = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[4] / "scripts" / "multi_turn_trials.py"))
runner["grade_fixture"](
    sys.argv[1],
    [
        (
            "excel",
            lambda task: task.import_rows(pathlib.Path(__file__).resolve().parents[1] / "public" / "orders.xlsx"),
            [["注文", "数量"], ["ペン", "3"]],
        ),
        (
            "legacy-csv",
            lambda task: task.import_rows(pathlib.Path(__file__).resolve().parents[1] / "public" / "orders.csv"),
            [["注文", "数量"], ["ペン", "3"]],
        ),
    ],
)
