"""課題固有の公開操作と期待値を共通採点処理へ渡す。"""

import pathlib
import runpy
import sys


def _mixed(task):
    return task.batch(
        [
            {"amount": 100, "premium": True, "exempt": False},
            {"amount": 200, "premium": False, "exempt": True},
            {"amount": 300, "premium": True, "exempt": True},
            {"amount": 400, "premium": False, "exempt": False},
        ]
    )


def _order(task):
    return task.batch([{"amount": 400, "premium": False, "exempt": False}, {"amount": 100, "premium": True, "exempt": False}])


runner = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[4] / "scripts" / "multi_turn_trials.py"))
runner["grade_fixture"](
    sys.argv[1],
    [
        ("mixed", _mixed, [110, 200, 270, 420]),
        ("order", _order, [420, 110]),
        ("empty", lambda task: task.batch([]), []),
        ("single", lambda task: task.calculate(100, True, False), 110),
    ],
)
