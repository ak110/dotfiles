import pathlib
import runpy
import sys

runner = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[4] / "scripts" / "multi_turn_trials.py"))
runner["grade_fixture"](
    sys.argv[1],
    [
        ("both", lambda task: [list(task.chat_options()), list(task.settings_options())], [["標準", "詳細"], ["標準", "詳細"]]),
        ("no-toggle", lambda task: task.choices.__code__.co_argcount + task.choices.__code__.co_kwonlyargcount, 0),
    ],
)
