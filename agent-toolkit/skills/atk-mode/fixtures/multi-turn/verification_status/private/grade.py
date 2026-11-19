"""単独課題の公開操作と期待値を共通採点処理へ渡す。"""

import pathlib
import runpy
import sys


def _unknown(task):
    try:
        task.classify("unknown")
    except ValueError:
        return True
    return False


runner = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[4] / "scripts" / "multi_turn_trials.py"))
runner["grade_fixture"](
    sys.argv[1],
    [
        ("passed", lambda task: task.classify("passed"), "合格"),
        ("failed", lambda task: task.classify("failed"), "不合格"),
        ("skipped", lambda task: task.classify("skipped"), "未実行"),
        ("not-collected", lambda task: task.classify("not-collected"), "未実行"),
        ("xfail", lambda task: task.classify("xfail"), "期待された失敗"),
        ("unknown", _unknown, True),
    ],
)
