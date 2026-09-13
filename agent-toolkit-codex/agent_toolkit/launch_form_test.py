"""agent-toolkitのuvプロジェクト指定が作業ディレクトリを保持することを検証する。"""

import json
import pathlib
import subprocess

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


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
