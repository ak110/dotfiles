"""混在明細、既存セント、十進金額を固定期待値で採点する。"""

import pathlib
import runpy
import sys

runner = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[4] / "scripts" / "multi_turn_trials.py"))
runner["grade_fixture"](
    sys.argv[1],
    [
        (
            "mixed",
            lambda task: task.total_cents(
                [
                    {"amount": 200, "unit": "cents"},
                    {"amount": "1.25", "unit": "dollars"},
                ]
            ),
            325,
        ),
        (
            "legacy",
            lambda task: task.total_cents(
                [
                    {"amount": 200, "unit": "cents"},
                    {"amount": 50, "unit": "cents"},
                ]
            ),
            250,
        ),
        (
            "decimal",
            lambda task: task.total_cents(
                [
                    {"amount": "0.10", "unit": "dollars"},
                    {"amount": "0.20", "unit": "dollars"},
                ]
            ),
            30,
        ),
    ],
)
