import json
import pathlib
import sys

import psutil

from agent_toolkit._atk.serve import session_costs, sessions
from agent_toolkit._common import session_cost_slim


def _lower_priority() -> None:
    process = psutil.Process()
    if sys.platform == "win32":
        process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    else:
        process.nice(10)


def main() -> int:
    _lower_priority()
    stores: dict[str | None, session_cost_slim.SlimRecordStore] = {}
    for line in sys.stdin:
        job = json.loads(line)
        directory = job["slim_directory"]
        slim = stores.setdefault(directory, session_cost_slim.SlimRecordStore(pathlib.Path(directory) if directory else None))
        started_ns = session_costs.now_ns()
        try:
            estimated = sessions.estimate_local_family(
                job["engine"],
                job["path"],
                claude_home=pathlib.Path(job["claude_home"]),
                codex_home=pathlib.Path(job["codex_home"]),
                state_dir=pathlib.Path(job["state_dir"]) if job["state_dir"] else None,
                slim=slim,
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
