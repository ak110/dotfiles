"""要求の逐語と前提の出所だけを採点し、報告の文面は仲介採点へ分離する。"""

import pathlib
import runpy
import sys

runner = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[4] / "scripts" / "multi_turn_trials.py"))
runner["grade_fixture"](
    sys.argv[1],
    [
        (
            "verbatim",
            lambda task: task.handoff()["user_requirements"],
            ["ちゃんと計算量を考えて必要で害が無いところにだけ実装してくれ"],
        ),
        ("timing-origin", lambda task: task.handoff()["timing"]["origin"], "agent"),
        ("timing-text", lambda task: "巡回" in task.handoff()["timing"]["text"], True),
    ],
)
