"""電源操作の公開入力が副作用より前に処理されることを確かめる。"""

import shutil
import subprocess
from pathlib import Path

import pytest

_POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(_POWERSHELL is None, reason="PowerShell実機を要する")


@pytest.mark.parametrize("name", ["keep-awake.ps1", "optimize-power-settings.ps1"])
@pytest.mark.parametrize("args,code", [(["--help"], 0), (["--check"], 2), (["--unknown"], 2), (["extra"], 2)])
def test_public_power_input(name: str, args: list[str], code: int) -> None:
    """原本の起動でヘルプと拒否が完了し、Windows専用の副作用へ進まない。"""
    assert _POWERSHELL is not None
    result = subprocess.run(
        [_POWERSHELL, "-NoProfile", "-File", str(Path(__file__).with_name(name)), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert result.returncode == code, result.stderr
    assert result.stdout if code == 0 else result.stderr


@pytest.mark.parametrize(
    "name,args,elevated",
    [
        ("keep-awake.ps1", [], False),
        ("optimize-power-settings.ps1", [], False),
        ("optimize-power-settings.ps1", ["-AutoElevated"], True),
    ],
)
def test_valid_power_input_reaches_body(tmp_path: Path, name: str, args: list[str], elevated: bool) -> None:
    """入力処理を原本から取得し、最初の副作用以降をマーカーへ置き換えて本体到達を観測する。"""
    assert _POWERSHELL is not None
    source = Path(__file__).with_name(name).read_text(encoding="utf-8-sig")
    boundary = "Add-Type -TypeDefinition" if name == "keep-awake.ps1" else "function Invoke-Native"
    prefix, found, _ = source.partition(boundary)
    assert found
    marker = "Write-Output 'BODY'\n"
    if name == "optimize-power-settings.ps1":
        marker += "Write-Output ([bool]$AutoElevated)\n"
    script = tmp_path / name
    script.write_bytes((prefix + marker).replace("\n", "\r\n").encode("utf-8-sig"))
    result = subprocess.run(
        [_POWERSHELL, "-NoProfile", "-File", str(script), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == (["BODY", str(elevated)] if name == "optimize-power-settings.ps1" else ["BODY"])
