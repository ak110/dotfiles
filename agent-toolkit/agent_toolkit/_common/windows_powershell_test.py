"""Windows PowerShellの起動案内と引数の境界を検証する。"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_toolkit._common import plugin_environment, windows_powershell
from agent_toolkit._testing.isolation import build_host_environ


def _native_uv(environ: dict[str, str]) -> Path | None:
    """uv.exeという別名でも起動できる実体を探す。"""
    path = environ["PATH"]
    mise = shutil.which("mise", path=path)
    shim_target = Path(mise).resolve() if mise else None
    shim: Path | None = None
    for directory in path.split(os.pathsep):
        if not (candidate := shutil.which("uv", path=directory)):
            continue
        resolved = Path(candidate).resolve()
        if resolved != shim_target and resolved.name != "mise":
            return resolved
        shim = resolved
    if shim is not None:
        result = subprocess.run(
            [str(shim), "which", "uv"], env=environ, capture_output=True, check=False, text=True, timeout=10
        )
        if result.returncode == 0 and (candidate := result.stdout.strip()):
            resolved = Path(candidate).resolve()
            if resolved != shim and resolved.name != "mise" and shutil.which(str(resolved)):
                return resolved
    return None


@pytest.mark.skipif(os.name == "nt", reason="Linuxでuvをuv.exeとして起動するための探索")
def test_native_uv_looks_past_mise_shim(tmp_path: Path) -> None:
    shim_dir, bin_dir = tmp_path / "shims", tmp_path / "bin"
    shim_dir.mkdir()
    bin_dir.mkdir()
    mise, uv = tmp_path / "mise", bin_dir / "uv"
    for executable in (mise, uv):
        executable.touch(mode=0o755)
    (shim_dir / "uv").symlink_to(mise)

    assert _native_uv({"PATH": os.pathsep.join(map(str, (shim_dir, bin_dir)))}) == uv


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
    assert ("wi grep -F '--' '--needle'" in note) is (expected == "atk.ps1")
    assert "ExecutionPolicy" not in command + note


@pytest.mark.windows
@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShellの実効ポリシーと照合する")
@pytest.mark.parametrize("restrict_process", [False, True])
def test_policy_matches_windows_powershell(monkeypatch: pytest.MonkeyPatch, restrict_process: bool) -> None:
    if restrict_process:
        monkeypatch.setenv("PSEXECUTIONPOLICYPREFERENCE", "AllSigned")
    shell = Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    # 比較元には5.1自身が起動時に組み立てるモジュール探索先を使わせる。
    reference_env = os.environ.copy()
    reference_env.pop("PSMODULEPATH", None)
    expected = subprocess.run(
        [str(shell), "-NoProfile", "-NonInteractive", "-Command", "Get-ExecutionPolicy"],
        env=reference_env,
        check=True,
        capture_output=True,
        timeout=30,
    )
    policy = expected.stdout.decode("ascii").strip().casefold()
    assert policy
    assert windows_powershell._execution_policy().casefold() == policy  # pylint: disable=protected-access


@pytest.mark.windows
@pytest.mark.skipif(os.name != "nt" and not shutil.which("pwsh"), reason="PowerShellが必要")
@pytest.mark.parametrize(
    ("command_tail", "argv", "exit_code"),
    [
        ("", [], 0),
        (
            "wi grep -e 'red|blue' 'R&D' '<tag>' 'x^2' 'it''s fine' 日本語",
            ["wi", "grep", "-e", "red|blue", "R&D", "<tag>", "x^2", "it's fine", "日本語"],
            1,
        ),
        ("wi grep -F '--' '--needle'", ["wi", "grep", "-F", "--", "--needle"], 0),
        ("wi grep -F '--' '--%'", ["wi", "grep", "-F", "--", "--%"], 1),
        ("wi process-loop status", ["wi", "process-loop", "status"], 7),
        ("wi process-loop status", ["wi", "process-loop", "status"], 75),
    ],
)
def test_notified_launcher_delivers_argv(
    tmp_path: Path, command_tail: str, argv: list[str], exit_code: int, monkeypatch: pytest.MonkeyPatch
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
        # miseのshimは実行名でツールを引くので、uv.exeに改名するとuvを起動できない。
        uv = _native_uv(build_host_environ())
        if uv is None:
            pytest.skip("uv.exeとして起動できるネイティブのuvが見つからない。実体をPATHに加えて試験を再実行する。")
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
    invoke = f"{command} {command_tail}; exit $LASTEXITCODE"
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
