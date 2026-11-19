"""超過の判定の機能だけを採点し、対話行動は仲介採点へ分離する。"""

import pathlib
import runpy
import sys

runner = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[4] / "scripts" / "multi_turn_trials.py"))
runner["grade_fixture"](
    sys.argv[1],
    [
        ("cpu-recovered", lambda task: task.judge_cpu([12, 9], 10), "pass"),
        ("cpu-repeated", lambda task: task.judge_cpu([12, 13], 10), "fail"),
        ("cpu-normal", lambda task: task.judge_cpu([9], 10), "pass"),
        ("memory-recovered", lambda task: task.judge_memory([520, 480], 500), "pass"),
        ("memory-repeated", lambda task: task.judge_memory([520, 530], 500), "fail"),
    ],
)
