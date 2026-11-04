"""注文集計を公開CLIから確認し、既存APIとともに採点する。"""

import json
import pathlib
import runpy
import sys
import tempfile

from agent_toolkit._common import process_tree


def _report(task, records):
    with tempfile.TemporaryDirectory(dir=pathlib.Path(task.__file__).parent) as directory:
        source = pathlib.Path(directory) / "orders.json"
        source.write_text(json.dumps(records), encoding="utf-8")
        result = process_tree.run_with_timeout(
            [sys.executable, "-I", "-B", task.__file__, "report", str(source)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
            check=False,
        )
        if result.returncode:
            raise ValueError(
                f"report終了{result.returncode}: {result.stderr}。"
                "次の操作: 提出したtask.pyのreport診断を確認して修正し、同じtrial・candidateでgradeを再実行する。"
            )
        return json.loads(result.stdout)


runner = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[4] / "scripts" / "multi_turn_trials.py"))
runner["grade_fixture"](
    sys.argv[1],
    [
        ("report", lambda task: _report(task, [{"amount_cents": 120}, {"amount_cents": 80}]), {"count": 2, "total_cents": 200}),
        ("empty", lambda task: _report(task, []), {"count": 0, "total_cents": 0}),
        ("legacy", lambda task: task.legacy_total([2, 3]), 5),
    ],
)
