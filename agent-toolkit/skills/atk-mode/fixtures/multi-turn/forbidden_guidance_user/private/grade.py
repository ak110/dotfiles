"""403の分類と本人が決めた設定の反映だけを採点し、対話行動は仲介採点へ分離する。"""

import pathlib
import runpy
import sys

runner = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[4] / "scripts" / "multi_turn_trials.py"))
runner["grade_fixture"](
    sys.argv[1],
    [
        ("forbidden", lambda task: task.classify(403), "permission_denied"),
        ("unauthorized", lambda task: task.classify(401), "auth_failed"),
        ("success-status", lambda task: task.classify(200), "ok"),
        ("help", lambda task: task.help_lines(), ["login: 認証情報を更新する"]),
        ("place", lambda task: task.settings()["place"], "banner"),
        ("scope", lambda task: task.settings()["scope"], "all"),
        ("daily-lookup-limit", lambda task: task.settings()["daily_lookup_limit"], 100),
    ],
)
