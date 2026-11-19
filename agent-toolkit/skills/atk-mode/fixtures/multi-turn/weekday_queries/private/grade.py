"""課題固有の公開操作と期待値を共通採点処理へ渡す。"""

import pathlib
import runpy
import sys


def _legacy_function(task):
    return [task.weekday("2026-10-11"), task.weekday("2026-10-12")]


def _legacy_query(task):
    return [task.query("weekday", "2026-10-11"), task.query("weekday", "2026-10-12")]


def _public_iso(task):
    return [task.query("iso-weekday", "2026-10-12"), task.query("iso-weekday", "2026-10-11")]


runner = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[4] / "scripts" / "multi_turn_trials.py"))
runner["grade_fixture"](
    sys.argv[1],
    [
        ("legacy-function", _legacy_function, [1, 2]),
        ("legacy-query", _legacy_query, [1, 2]),
        ("public-iso", _public_iso, [1, 7]),
    ],
)
