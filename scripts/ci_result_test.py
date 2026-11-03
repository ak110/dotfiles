"""必須checkの成功・失敗・意図した省略をCLIの終了コードで検証する。"""

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.mark.parametrize("owns", ["true", "false", ""])
@pytest.mark.parametrize("result", ["success", "failure", "skipped", "cancelled"])
def test_required_check_result(
    tmp_path: Path,
    host_environ: Callable[[], dict[str, str]],
    owns: str,
    result: str,
) -> None:
    """所有時は必要なjobが成功した場合だけ成功し、非所有は理由を表示する。"""
    environment = {
        **host_environ(),
        "OWNS": owns,
        "OWNER_REASON": "同期pushの非所有run",
        "NEEDS": json.dumps({"common-ci-owner": {"result": "success"}, "check": {"result": result}}),
        "PYTHONIOENCODING": "utf-8",
    }
    process = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("ci_result.py"))],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=10,
    )
    expected = owns == "false" or (owns == "true" and result == "success")
    assert process.returncode == (0 if expected else 1), process.stderr
    if owns == "false":
        assert environment["OWNER_REASON"] in process.stdout
