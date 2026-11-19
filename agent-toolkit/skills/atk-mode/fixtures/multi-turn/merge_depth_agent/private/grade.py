"""群ごとの扱いだけを採点し、対話行動は仲介採点へ分離する。"""

import pathlib
import runpy
import sys

runner = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[4] / "scripts" / "multi_turn_trials.py"))
runner["grade_fixture"](
    sys.argv[1],
    [
        ("rewrite-targets", lambda task: task.plan()["rewrite_targets"], "absorb"),
        ("duplicates", lambda task: task.plan()["duplicates"], "merge"),
        ("independent", lambda task: task.plan()["independent"], "keep"),
    ],
)
