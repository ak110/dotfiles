"""課題固有の公開操作と期待値を共通採点処理へ渡す。"""

import pathlib
import runpy
import sys

runner = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[4] / "scripts" / "multi_turn_trials.py"))
runner["grade_fixture"](
    sys.argv[1],
    [
        ("spaces", lambda task: task.render("A  B C"), "A  B C"),
        ("edges", lambda task: task.render("  A B  "), "  A B  "),
        ("normal", lambda task: [task.render("ABC"), task.render("日本語")], ["ABC", "日本語"]),
    ],
)
