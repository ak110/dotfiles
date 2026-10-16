"""agent-toolkitのuvプロジェクト指定が作業ディレクトリを保持することを検証する。"""

import json
import pathlib
import subprocess
import sys
import venv
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


@pytest.mark.parametrize("project_environment", [None, "", "別の仮想環境"])
def test_public_run_command_restores_callers_project_environment(
    tmp_path: pathlib.Path,
    host_environ: Callable[[], dict[str, str]],
    project_environment: str | None,
) -> None:
    """実launcherとrun-commandで、同期先の分離と子への値・未設定の配送を同時に確認する。"""
    env = host_environ()
    env["AGENT_TOOLKIT_RUNTIME_DATA"] = str(tmp_path / "runtime")
    other = tmp_path / "別の仮想環境"
    venv.EnvBuilder(with_pip=False).create(other)
    marker = other / "package.dist-info" / "RECORD"
    marker.parent.mkdir()
    marker.write_text("呼出元のメタデータ", encoding="utf-8")
    before = {path.relative_to(other): path.read_bytes() for path in other.rglob("*") if path.is_file()}
    env.pop("UV_PROJECT_ENVIRONMENT", None)
    expected = str(other) if project_environment else project_environment
    if expected is not None:
        env["UV_PROJECT_ENVIRONMENT"] = expected
    result = subprocess.run(
        [
            sys.executable,
            str(_PROJECT_ROOT / "scripts" / "launch.py"),
            "agent_toolkit/atk.py",
            "run-command",
            "--cwd",
            str(tmp_path),
            "--timeout",
            "30",
            "--",
            sys.executable,
            "-c",
            "import json, os, sys; print(json.dumps({'project': os.environ.get('UV_PROJECT_ENVIRONMENT'), "
            "'cwd': os.getcwd(), 'argv': sys.argv[1:]})); print('子の診断', file=sys.stderr); sys.exit(7)",
            "空 白",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )
    assert result.returncode == 7, result.stderr
    record = json.loads(result.stdout)
    assert record["child_exit_code"] == record["wrapper_exit_code"] == 7
    observed = json.loads(pathlib.Path(record["stdout_path"]).read_text(encoding="utf-8"))
    assert observed == {"project": expected, "cwd": str(tmp_path), "argv": ["空 白"]}
    assert pathlib.Path(record["stderr_path"]).read_text(encoding="utf-8").strip() == "子の診断"
    assert {path.relative_to(other): path.read_bytes() for path in other.rglob("*") if path.is_file()} == before
