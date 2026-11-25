import pathlib
import runpy
import sys

runner = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[4] / "scripts" / "multi_turn_trials.py"))
runner["grade_fixture"](
    sys.argv[1],
    [
        ("confirm", lambda task: task.recommendation(), "ask"),
        ("retain-until-decision", lambda task: task.resume_default(), "picker"),
    ],
)
