"""課題固有の公開操作と期待値を共通採点処理へ渡す。"""

import pathlib
import runpy
import sys


def _reference(task):
    return task.Attachments("/vault").reference("a.txt")


def _associate(task):
    return task.Attachments("/vault").associate("a.txt", "owner")


def _delete(task):
    store = task.Attachments("/vault")
    store.associate("a.txt", "owner")
    return [store.delete("a.txt"), store.delete("a.txt"), store.links]


def _legacy(task):
    store = task.Attachments("/vault")
    store.reference("b.txt")
    store.associate("b.txt", "owner")
    return store.delete("b.txt")


runner = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[4] / "scripts" / "multi_turn_trials.py"))
runner["grade_fixture"](
    sys.argv[1],
    [
        ("reference", _reference, "/vault/a.txt"),
        ("associate", _associate, "/vault/a.txt"),
        ("delete", _delete, [True, False, {}]),
        ("legacy", _legacy, True),
    ],
)
