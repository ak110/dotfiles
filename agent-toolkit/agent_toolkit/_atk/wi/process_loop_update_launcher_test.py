"""ランチャーは巡回の引数と終了値を単一のCLIへ渡す。"""

import os
import pathlib
import subprocess

import pytest

_SCRIPTS = pathlib.Path(__file__).resolve().parents[3] / "scripts"


@pytest.mark.skipif(os.name == "nt", reason="POSIXの配布入口を実行する")
def test_posix_launcher_returns_process_loop_exit_without_restarting(tmp_path: pathlib.Path) -> None:
    probe = tmp_path / "uv"
    probe.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\nexit 75\n", encoding="utf-8")
    probe.chmod(0o700)
    result = subprocess.run(
        [str(_SCRIPTS / "atk"), "wi", "process-loop", "--once"],
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 75
    assert result.stdout.splitlines()[-3:] == ["wi", "process-loop", "--once"]
    assert result.stdout.splitlines().count("process-loop") == 1
