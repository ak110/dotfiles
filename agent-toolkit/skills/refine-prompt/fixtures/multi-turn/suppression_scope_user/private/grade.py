"""抑止の範囲の機能だけを採点し、対話行動は仲介採点へ分離する。"""

import pathlib
import runpy
import sys

runner = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[4] / "scripts" / "multi_turn_trials.py"))
runner["grade_fixture"](
    sys.argv[1],
    [
        ("after-submission", lambda task: task.suppressed(True, False), True),
        ("during-submission", lambda task: task.suppressed(True, True), True),
        ("after-conversation", lambda task: task.suppressed(False, False), False),
    ],
)
