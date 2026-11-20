"""Windows PowerShellの起動案内と引数の境界を検証する。"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_toolkit._common import plugin_environment, windows_powershell


@pytest.mark.parametrize(
    ("policy", "marked", "expected"),
    [
        ("RemoteSigned", False, "atk.ps1"),
        ("Unrestricted", False, "atk.ps1"),
        ("Bypass", False, "atk.ps1"),
        ("RemoteSigned", True, "atk.cmd"),
        ("Bypass", True, "atk.cmd"),
        ("AllSigned", False, "atk.cmd"),
        ("Restricted", False, "atk.cmd"),
        ("", False, "atk.cmd"),
    ],
)
def test_windows_cli_instructions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: str, marked: bool, expected: str
) -> None:
    scripts = tmp_path / "user's plugin" / "scripts"
    scripts.mkdir(parents=True)
    script = scripts / "atk.ps1"
    script.touch()
    if marked:
        Path(f"{script}:Zone.Identifier").write_bytes(b"[ZoneTransfer]\r\nZoneId=3\r\n")
    monkeypatch.setattr(windows_powershell, "_execution_policy", lambda: policy)
    command, note = plugin_environment.cli_instructions(scripts.parent, windows=True).split("\n", 1)
    assert command == "起動部分: & '" + str(scripts / expected).replace("'", "''") + "'"
    assert ("|・&・<・>・^を含む引数は渡さない" in note) is (expected == "atk.cmd")
    assert "ExecutionPolicy" not in command + note


@pytest.mark.windows
@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShellの実効ポリシーと照合する")
@pytest.mark.parametrize("restrict_process", [False, True])
def test_policy_matches_windows_powershell(monkeypatch: pytest.MonkeyPatch, restrict_process: bool) -> None:
    if restrict_process:
        monkeypatch.setenv("PSEXECUTIONPOLICYPREFERENCE", "AllSigned")
    expected = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "Get-ExecutionPolicy"],
        check=True,
        capture_output=True,
        timeout=30,
    )
    assert windows_powershell._execution_policy().casefold() == expected.stdout.decode("ascii").strip().casefold()  # pylint: disable=protected-access


@pytest.mark.windows
@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1とNTFSの配布経路を使う")
@pytest.mark.parametrize(
    ("argv", "exit_code", "marked"),
    [
        ([], 0, False),
        (["wi", "grep", "-e", "red|blue", "R&D", "<tag>", "x^2", "two words"], 1, False),
        (["wi", "process-loop", "status"], 7, False),
        (["info"], 0, True),
    ],
)
def test_notified_launcher_delivers_argv(
    tmp_path: Path, argv: list[str], exit_code: int, marked: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """配布スクリプトとネイティブuvを通り、atk.pyで観測したargv、再起動先、終了値を比べる。"""
    source = Path(plugin_environment.__file__).resolve().parents[2] / "scripts"
    root = tmp_path / "plugin root"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    for name in ("atk.cmd", "atk.ps1"):
        shutil.copyfile(source / name, scripts / name)
    if marked:
        (scripts / "atk.ps1:Zone.Identifier").write_bytes(b"[ZoneTransfer]\r\nZoneId=3\r\n")
    restart = tmp_path / "new root"
    for release in (root, restart):
        (release / "scripts").mkdir(parents=True, exist_ok=True)
        (release / "scripts/launch.py").write_text(
            "import runpy, sys\nfrom pathlib import Path\n"
            "assert sys.argv[1] == '--prepare-if-missing'\n"
            "assert Path(sys.argv[2]).resolve().parents[1] == Path(__file__).resolve().parents[1]\n"
            "sys.argv = sys.argv[2:]\nrunpy.run_path(sys.argv[0], run_name='__main__')\n",
            encoding="utf-8",
        )
        (release / "agent_toolkit").mkdir()
        (release / "agent_toolkit/atk.py").write_text(
            "import json, os, sys\nfrom pathlib import Path\n"
            "print(json.dumps(sys.argv[1:]))\n"
            "if sys.argv[1:3] == ['wi', 'process-loop']:\n"
            "    Path(os.environ['AGENT_TOOLKIT_RESTART_SPEC']).write_text("
            "os.environ['NEXT_SCRIPT'] + '\\nnew|value\\ntwo words\\n', encoding='utf-8')\n"
            "    sys.exit(75)\nsys.exit(int(os.environ['PROBE_EXIT']))\n",
            encoding="utf-8",
        )
    monkeypatch.setenv("NEXT_SCRIPT", str(restart / "agent_toolkit/atk.py"))
    monkeypatch.setenv("PROBE_EXIT", str(exit_code))
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    monkeypatch.setenv("PATH", str(plugin_environment.base_python().parent) + os.pathsep + os.environ["PATH"])
    command = plugin_environment.cli_instructions(root, windows=True).splitlines()[0].removeprefix("起動部分: ")
    # 記号を含む試験値は、バッチでないことを確かめてから渡す。
    expected = scripts / ("atk.cmd" if marked else "atk.ps1")
    assert command == "& '" + str(expected).replace("'", "''") + "'"
    arguments = " ".join("'" + value.replace("'", "''") + "'" for value in argv)
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", f"{command} {arguments}; exit $LASTEXITCODE"],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == exit_code, result.stderr
    observed = [json.loads(line) for line in result.stdout.splitlines()]
    assert observed == ([argv, ["new|value", "two words"]] if argv[:2] == ["wi", "process-loop"] else [argv])
