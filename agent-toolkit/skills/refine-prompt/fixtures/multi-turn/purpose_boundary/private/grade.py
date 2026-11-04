"""質問への回答で確定した目的と改善領域を採点する。"""

import pathlib
import runpy
import sys


def _purpose(task):
    value = task.proposal()["purpose"]
    return isinstance(value, str) and bool(value.strip())


def _includes(task, name):
    values = task.proposal()["changes"]
    return isinstance(values, list) and all(isinstance(value, str) for value in values) and name in values


runner = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[4] / "scripts" / "multi_turn_trials.py"))
runner["grade_fixture"](
    sys.argv[1],
    [
        ("purpose", _purpose, True),
        ("discovery", lambda task: _includes(task, "discovery"), True),
        ("strategy", lambda task: _includes(task, "strategy"), True),
        ("coordination", lambda task: _includes(task, "coordination"), True),
        ("verification", lambda task: _includes(task, "verification"), True),
    ],
)
