import os
import pathlib
import subprocess
import sys
from collections.abc import Callable

import pytest

_SCRIPT = pathlib.Path(__file__).with_name("atk-sandbox.sh")

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="隔離ハーネスはLinuxのprocfsを使う")


@pytest.mark.parametrize(("matching_home", "exit_code"), [(True, 0), (True, 7), (False, 0)])
def test_run_reports_only_matching_home(
    tmp_path: pathlib.Path, matching_home: bool, exit_code: int, host_environ: Callable[[], dict[str, str]]
) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "work").mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (fixture / "evidence-dir").write_text(str(evidence), encoding="utf-8")
    (fixture / "sandbox.env").write_text("PATH=/usr/bin:/bin\n", encoding="utf-8")
    stubs = tmp_path / "bin"
    stubs.mkdir()
    grep = stubs / "grep"
    grep.write_text(
        "#!/bin/bash\n"
        '[[ "$1" == -lzxsF && "$2" == -- && "$3" == "HOME=$MOCK_HOME" ]] || exit 1\n'
        "printf '/proc/9000001/environ\\n/proc/9000002/environ\\n'\n",
        encoding="utf-8",
    )
    grep.chmod(0o755)
    env = host_environ()
    env["PATH"] = str(stubs) + os.pathsep + env["PATH"]
    env["MOCK_HOME"] = str(fixture / ("home" if matching_home else "home-other"))

    result = subprocess.run(
        [
            "bash",
            str(_SCRIPT),
            "run",
            str(fixture),
            "model",
            "/bin/sh",
            "-c",
            f"printf 'model output\\n'; printf 'model error\\n' >&2; exit {exit_code}",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == exit_code
    assert (evidence / "01-model.rc").read_text(encoding="utf-8").strip() == str(exit_code)
    assert (evidence / "01-model.out").read_text(encoding="utf-8") == "model output\n"
    assert (evidence / "01-model.err").read_text(encoding="utf-8") == "model error\n"
    expected_pids = ["残存PID: 9000001", "残存PID: 9000002"] if matching_home else []
    lines = result.stderr.splitlines()
    if matching_home:
        assert lines[0].startswith("注意: 前景コマンドの終了後も隔離HOMEで動くプロセスが残っている。")
    assert lines[1 if matching_home else 0 :] == expected_pids
