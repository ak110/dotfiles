import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest


@pytest.mark.windows
@pytest.mark.cpu_limit(
    0.69, reason="新旧版の隔離配置を含む。2ケースの判定CPU秒はLinux単独0.254〜0.295、4並列0.272〜0.298、CI失敗時最大0.686973"
)
@pytest.mark.parametrize("child_exit", [0, 7])
def test_post_update_imports_only_new_version(tmp_path: pathlib.Path, child_exit: int) -> None:
    source = pathlib.Path(__file__).resolve().parents[1]
    for directory in ("libexec", "pytools", "agent-toolkit/agent_toolkit"):
        shutil.copytree(source / directory, tmp_path / directory, ignore=shutil.ignore_patterns("__pycache__"))
    package = tmp_path / "agent-toolkit" / "agent_toolkit"
    action = package / "_common" / "next_action.py"
    elevation = package / "_atk" / "managed_temp" / "elevation.py"
    constants = package / "_atk" / "wi" / "constants.py"
    (tmp_path / "new_next_action.py").write_bytes(action.read_bytes())
    (tmp_path / "new_constants.py").write_bytes(constants.read_bytes())
    constants.write_text(
        constants.read_text(encoding="utf-8").replace("recover_from_failure", "old_recovery"),
        encoding="utf-8",
    )
    action.write_text(
        action.read_text(encoding="utf-8").replace("def recover_from_failure(", "def old_recovery("),
        encoding="utf-8",
    )
    (tmp_path / elevation.name).write_text(
        "from agent_toolkit._common.next_action import recover_from_failure\n"
        "import sys\n"
        "def sweep_for_update(*, no_sudo, logger):\n"
        "    assert no_sudo\n"
        "    print('new version reached', file=sys.stderr)\n"
        f"    raise SystemExit({child_exit})\n",
        encoding="utf-8",
    )
    driver = """\
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path('libexec').resolve()))
import update_dotfiles
import update_dotfiles_runtime
import update_dotfiles_stages
from agent_toolkit._common import next_action
from pytools._internal import sync_report

sync_report.REPORT_PATH = pathlib.Path("sync-report.json")
assert not hasattr(next_action, 'recover_from_failure')
update_dotfiles_runtime.LOCK_PATH = pathlib.Path('update.lock')
sys.stdin.isatty = lambda: True

def replace_sources(*args, **kwargs):
    os.replace('new_next_action.py', next_action.__file__)
    os.replace("new_constants.py", "agent-toolkit/agent_toolkit/_atk/wi/constants.py")
    os.replace("elevation.py", "agent-toolkit/agent_toolkit/_atk/managed_temp/elevation.py")
    assert not hasattr(next_action, 'recover_from_failure')
    return 0

update_dotfiles_stages.run_stages = replace_sources
raise SystemExit(update_dotfiles.main(['--no-sudo']))
"""
    env = {**os.environ, "XDG_STATE_HOME": str(tmp_path / "state"), "LOCALAPPDATA": str(tmp_path / "local")}
    env.pop("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", None)
    result = subprocess.run(
        [sys.executable, "-c", driver], cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", check=False
    )
    assert result.returncode == child_exit, result.stderr
    assert "new version reached" in result.stderr
    report = json.loads((tmp_path / "sync-report.json").read_text(encoding="utf-8"))
    if child_exit:
        assert report["failed_stage"] == "管理一時資源の回収"
        assert "new version reached" in report["stderr_tail"]
    else:
        assert report["status"] == "succeeded"
