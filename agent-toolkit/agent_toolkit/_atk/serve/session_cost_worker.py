"""serveの背景で家族の推定費用を算出する別プロセス（`session_costs.FamilyCostWarmer`が起動する）。

標準入力から1行1件のJSON（engine、記録のパス、各保存先）を受け取り、標準出力へ1行1件の結果を返す。
記録を読めない場合と記録が大きすぎる場合は`error`を返して次の要求を待つ。
serveの要求処理を妨げないよう、起動時に自身の優先度を下げる。
"""

import json
import pathlib
import sys

import psutil

from agent_toolkit._atk.serve import session_costs, sessions


def _lower_priority() -> None:
    process = psutil.Process()
    if sys.platform == "win32":
        process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    else:
        process.nice(10)


def main() -> int:
    """標準入力が閉じるまで1件ずつ算出する。"""
    _lower_priority()
    for line in sys.stdin:
        job = json.loads(line)
        started_ns = session_costs.now_ns()
        try:
            estimated = sessions.estimate_local_family(
                job["engine"],
                job["path"],
                claude_home=pathlib.Path(job["claude_home"]),
                codex_home=pathlib.Path(job["codex_home"]),
                state_dir=pathlib.Path(job["state_dir"]) if job["state_dir"] else None,
            )
        except sessions.SessionNotFoundError as error:
            result = {"path": job["path"], "error": str(error)}
        else:
            result = {
                "path": job["path"],
                "cost": estimated.cost,
                "members": [str(member) for member in estimated.members],
                "complete": estimated.complete,
                "started_ns": started_ns,
            }
        # ASCIIに限定し、Windowsの標準出力の文字コードに左右されないようにする。
        sys.stdout.write(json.dumps(result) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
