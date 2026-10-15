"""agent-toolkitのuvプロジェクト指定が作業ディレクトリを保持することを検証する。"""

import json
import pathlib
import subprocess
import sys
from collections.abc import Callable

import pytest

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent


@pytest.mark.usefixtures("share_package_caches")
def test_project_launch_preserves_cwd_and_uses_plugin_environment(tmp_path: pathlib.Path) -> None:
    """`--project`は呼出元のcwdを変えず、plugin rootの仮想環境を選ぶ。"""
    result = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(_PROJECT_ROOT),
            "--locked",
            "--no-default-groups",
            "python",
            "-c",
            "import json, os, sys; print(json.dumps({'cwd': os.getcwd(), 'prefix': sys.prefix}))",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    observed = json.loads(result.stdout)
    assert pathlib.Path(observed["cwd"]) == tmp_path
    assert pathlib.Path(observed["prefix"]).resolve().is_relative_to((_PROJECT_ROOT / ".venv").resolve())


def test_public_launcher_preserves_argument_error_exit_status(
    tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]
) -> None:
    """実CLIのargparse終了2をlauncherの終了状態まで伝播する。"""
    env = host_environ()
    env["AGENT_TOOLKIT_RUNTIME_DATA"] = str(tmp_path / "runtime")
    result = subprocess.run(
        [sys.executable, str(_PROJECT_ROOT / "scripts" / "launch.py"), "agent_toolkit/atk.py", "__invalid_launch_test__"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 2
    assert b"__invalid_launch_test__" in result.stderr
