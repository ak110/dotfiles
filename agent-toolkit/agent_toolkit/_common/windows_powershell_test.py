"""Windows PowerShellの起動案内と引数の境界を検証する。"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_toolkit._common import plugin_environment, windows_powershell


@pytest.mark.windows
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
@pytest.mark.skipif(os.name != "nt" and not shutil.which("pwsh"), reason="PowerShellが必要")
@pytest.mark.parametrize(
    ("argv", "exit_code"),
    [
        ([], 0),
        (["wi", "grep", "-e", "red|blue", "R&D", "<tag>", "x^2", "two words", "日本語"], 1),
        (["wi", "grep", "-F", "--", "--%"], 1),
        (["wi", "process-loop", "status"], 7),
        (["wi", "process-loop", "status"], 75),
    ],
)
def test_notified_launcher_delivers_argv(
    tmp_path: Path, argv: list[str], exit_code: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """配布スクリプトとネイティブuvを通り、atk.pyで観測したargv、再起動先、終了値を比べる。"""
    source = Path(plugin_environment.__file__).resolve().parents[2] / "scripts"
    root = tmp_path / "plugin root"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(source / "atk.ps1", scripts / "atk.ps1")
    shell = "powershell.exe" if os.name == "nt" else "pwsh"
    if os.name != "nt":
        monkeypatch.setattr(windows_powershell, "_execution_policy", lambda: "RemoteSigned")
        uv = shutil.which("uv")
        assert uv is not None
        (tmp_path / "uv.exe").symlink_to(uv)
        monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
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
            "if sys.argv[1:3] == ['wi', 'process-loop'] and os.environ['PROBE_EXIT'] != '75':\n"
            "    Path(os.environ['AGENT_TOOLKIT_RESTART_SPEC']).write_text("
            "os.environ['RESTART_CONTENT'], encoding='utf-8')\n"
            "    sys.exit(75)\nsys.exit(int(os.environ['PROBE_EXIT']))\n",
            encoding="utf-8",
        )
    restart_argv = ["wi", "grep", "-e", "red|blue", "-e", "two words", "--", "--%"]
    monkeypatch.setenv("RESTART_CONTENT", "\n".join([str(restart / "agent_toolkit/atk.py"), *restart_argv, ""]))
    monkeypatch.setenv("PROBE_EXIT", str(exit_code))
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    monkeypatch.setenv("PATH", str(plugin_environment.base_python().parent) + os.pathsep + os.environ["PATH"])
    command = plugin_environment.cli_instructions(root, windows=True).splitlines()[0].removeprefix("起動部分: ")
    # 記号を含む試験値は、バッチでないことを確かめてから渡す。
    expected = scripts / "atk.ps1"
    assert command == "& '" + str(expected).replace("'", "''") + "'"
    inputs = tmp_path / "search-terms.txt"
    inputs.write_text("".join(value + "\n" for value in argv), encoding="utf-8")
    monkeypatch.setenv("PROBE_ARGV", str(inputs))
    monkeypatch.setenv("PROBE_ENTRY", str(expected))
    invoke = "$values = [IO.File]::ReadAllLines($env:PROBE_ARGV); & $env:PROBE_ENTRY @values; exit $LASTEXITCODE"
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", invoke],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == exit_code, result.stderr
    observed = [json.loads(line) for line in result.stdout.splitlines()]
    assert observed == ([argv, restart_argv] if exit_code == 7 else [argv])
