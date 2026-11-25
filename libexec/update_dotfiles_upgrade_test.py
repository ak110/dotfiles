import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest


@pytest.fixture(scope="module", name="update_uv_executable")
def _update_uv_executable() -> pathlib.Path:
    launcher = shutil.which(os.environ.get("UV") or "uv")
    assert launcher is not None
    # pyfltr: allow-comment PATH上のmise shimを解決してもuv本体にはならない。実際に起動したuvから実行パスを得る。
    probe = subprocess.run(
        [
            launcher,
            "run",
            "--no-project",
            "--python",
            sys.executable,
            "python",
            "-X",
            "utf8",
            "-c",
            'import os; print(os.environ["UV"])',
        ],
        capture_output=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
    executable = pathlib.Path(probe.stdout.rstrip("\r\n"))
    assert executable.is_absolute() and executable.is_file(), probe.stdout
    return executable


def _copy_update_sources(checkout: pathlib.Path) -> None:
    source = pathlib.Path(__file__).resolve().parents[1]
    package = pathlib.Path("agent-toolkit/agent_toolkit")
    files = [
        package / "__init__.py",
        package / "_atk/__init__.py",
        package / "_atk/wi/__init__.py",
        package / "_atk/wi/constants.py",
        pathlib.Path("pytools/__init__.py"),
        *(pathlib.Path("pytools/_internal") / f"{name}.py" for name in ("__init__", "log_format", "sync_report", "update_log")),
    ]
    for directory in (pathlib.Path("libexec"), package / "_common", package / "_git", package / "_atk/managed_temp"):
        files.extend(
            path.relative_to(source)
            for path in (source / directory).glob("*.py")
            if not path.stem.endswith(("_test", "_test_helpers")) and path.name != "conftest.py"
        )
    for path in files:
        destination = checkout / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / path, destination)


@pytest.mark.windows
@pytest.mark.cpu_limit(0.69, reason="旧親と新版の子が読む実ファイルをケースごとに隔離し、起動と更新時の置換を試す。")
@pytest.mark.parametrize(
    ("entry", "child_exit", "expected"),
    [
        ("current", 0, 0),
        ("current", 7, 7),
        ("legacy-noninteractive", 0, 0),
        ("legacy-process-loop", 0, 0),
        ("legacy-interactive", 0, 0),
        ("legacy-interactive", 7, 1),
        ("legacy-home-uv", 0, 0),
    ],
)
def test_post_update_imports_only_new_version(
    tmp_path: pathlib.Path, update_uv_executable: pathlib.Path, entry: str, child_exit: int, expected: int
) -> None:
    _copy_update_sources(tmp_path)
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
        elevation.read_text(encoding="utf-8") + "\n"
        "def sweep_interactively(*, no_sudo, logger):\n"
        "    from agent_toolkit._common.next_action import recover_from_failure\n"
        "    assert callable(recover_from_failure) and no_sudo\n"
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
assert 'agent_toolkit._atk.managed_temp' not in sys.modules
update_dotfiles_runtime.LOCK_PATH = pathlib.Path('update.lock')
entry = sys.argv[1]
sys.stdin.isatty = lambda: entry != 'legacy-noninteractive'

def replace_sources(*args, **kwargs):
    os.replace('new_next_action.py', next_action.__file__)
    os.replace("new_constants.py", "agent-toolkit/agent_toolkit/_atk/wi/constants.py")
    os.replace("elevation.py", "agent-toolkit/agent_toolkit/_atk/managed_temp/elevation.py")
    assert not hasattr(next_action, 'recover_from_failure')
    return 0

update_dotfiles_stages.run_stages = replace_sources
if entry.startswith('legacy-'):
    # 旧mainはapply完了後にこの呼び出しを初めて解決し、例外を終了1へ変換する。
    def legacy_post_update(no_sudo):
        from agent_toolkit._atk.managed_temp.elevation import sweep_for_update

        sweep_for_update(no_sudo=no_sudo, logger=update_dotfiles_runtime.logger)
        assert 'agent_toolkit._atk.managed_temp.errors' not in sys.modules
        return 0

    update_dotfiles._run_post_update = legacy_post_update
raise SystemExit(update_dotfiles.main(['--no-sudo']))
"""
    env = {**os.environ, "XDG_STATE_HOME": str(tmp_path / "state"), "LOCALAPPDATA": str(tmp_path / "local")}
    env["WIN_PD_OVERRIDE_LOCAL_APPDATA"] = env["LOCALAPPDATA"]
    env.pop("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", None)
    if entry == "legacy-process-loop":
        env["AGENT_TOOLKIT_PROCESS_LOOP_SESSION"] = "1"
    if entry == "legacy-home-uv":
        env.pop("UV", None)
        home = tmp_path / "home"
        installed_uv = home / ".local/bin" / ("uv.exe" if os.name == "nt" else "uv")
        installed_uv.parent.mkdir(parents=True)
        shutil.copy2(update_uv_executable, installed_uv)
        env.update(HOME=str(home), USERPROFILE=str(home), PATH=str(tmp_path / "empty-bin"), UV_PYTHON=sys.executable)
    else:
        env["UV"] = str(update_uv_executable)
    result = subprocess.run(
        [sys.executable, "-c", driver, entry],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == expected, result.stderr
    report = json.loads((tmp_path / "sync-report.json").read_text(encoding="utf-8"))
    log_root = tmp_path / ("local" if os.name == "nt" else "state")
    log = (log_root / "agent-toolkit" / "update-dotfiles.log").read_text(encoding="utf-8")
    if entry == "current":
        assert "new version reached" in result.stderr
    else:
        assert ("new version reached" in log) == (entry in {"legacy-interactive", "legacy-home-uv"})
    if entry == "current" and child_exit:
        assert report["failed_stage"] == "管理一時資源の回収"
    if child_exit:
        assert report["exit_code"] == expected
        assert "new version reached" in report["stderr_tail"]
    else:
        assert report["status"] == "succeeded"
