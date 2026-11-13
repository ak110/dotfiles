"""PS bootstrapのnative境界と、実Windowsランチャーの時間切れを検証する。"""

import contextlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import uuid
from collections.abc import Generator

import psutil
import pytest

pytestmark = pytest.mark.windows

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PS = shutil.which("powershell.exe") or shutil.which("pwsh")

sys.path.insert(0, str(_ROOT / "libexec"))
import update_dotfiles_runtime  # noqa: E402  # pylint: disable=wrong-import-position

# 同じ代用品で、PowerShellの実Process起動・WaitForExit・taskkillまで通す。
_NATIVE_SOURCE = r"""
using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
public class Program {
    public static int Main(string[] args) {
        string stage = "";
        if (args.Length > 0 && args[0] == "--child") {
            File.WriteAllText(Path.Combine(Environment.GetEnvironmentVariable("PIDS"),
                Process.GetCurrentProcess().Id.ToString()), "child");
            Thread.Sleep(60000); return 0;
        }
        if (args.Length > 0 && args[0] == "-NoProfile")
            stage = Array.IndexOf(args, "-Command") >= 0 ? "uv-install" : "uv-self";
        if (args.Length > 1 && args[0] == "python" && args[1] == "install")
            stage = Array.IndexOf(args, "--upgrade") >= 0 ? "python-upgrade" : "python-install";
        File.AppendAllText(Environment.GetEnvironmentVariable("CALLS"), stage + "\n");
        if (args.Length > 1 && args[0] == "python" && args[1] == "dir")
            Console.WriteLine(Environment.GetEnvironmentVariable("PYTHON_ROOT"));
        if (args.Length > 1 && args[0] == "python" && args[1] == "find") {
            if (Environment.GetEnvironmentVariable("INJECT_STAGE") == "python-install") return 2;
            if (Environment.GetEnvironmentVariable("INJECT_STAGE") == "probe") stage = "probe";
            else Console.WriteLine(Environment.GetEnvironmentVariable("PYTHON_PATH"));
        }
        if (stage != Environment.GetEnvironmentVariable("INJECT_STAGE")) return 0;
        if (Environment.GetEnvironmentVariable("INJECT_MODE") == "exit") return 17;
        string self = System.Reflection.Assembly.GetExecutingAssembly().Location;
        using (Process child = Process.Start(new ProcessStartInfo(self, "--child") { UseShellExecute=false })) {
            File.WriteAllText(Path.Combine(Environment.GetEnvironmentVariable("PIDS"),
                Process.GetCurrentProcess().Id.ToString()), "parent");
            Thread.Sleep(60000);
        }
        return 0;
    }
}
"""


@pytest.fixture(name="native_program", scope="module")
def _native_program(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """Windows標準コンパイラーで小さな実Win32代用品を一度だけ構築する。"""
    if os.name != "nt" or _PS is None:
        pytest.skip("Win32 native実行体とWindows PowerShellが必要")
    root = tmp_path_factory.mktemp("bootstrap-native")
    output = root / "stub.exe"
    build = root / "build.ps1"
    build.write_text(
        "Add-Type -OutputAssembly $env:STUB_OUTPUT -OutputType ConsoleApplication -TypeDefinition @'\n"
        + _NATIVE_SOURCE
        + "\n'@\n",
        encoding="utf-8-sig",
    )
    environment = os.environ.copy()
    environment["STUB_OUTPUT"] = str(output)
    result = subprocess.run(
        [str(_PS), "-NoProfile", "-File", str(build)],
        env=environment,
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return output


@contextlib.contextmanager
def _owned_native_pids(directory: pathlib.Path, executable: pathlib.Path) -> Generator[None]:
    """失敗した試験でも、代用品自身が記録したPIDだけを回収する。"""
    try:
        yield
    finally:
        for marker in directory.iterdir():
            with contextlib.suppress(psutil.NoSuchProcess):
                process = psutil.Process(int(marker.name))
                if pathlib.Path(process.exe()).resolve() in {executable.resolve(), directory.parent / "powershell-stub.exe"}:
                    process.kill()
                    process.wait(timeout=10)


def _project(tmp_path: pathlib.Path, native: pathlib.Path, stage: str, mode: str) -> tuple[pathlib.Path, dict[str, str]]:
    root = tmp_path / "project with spaces"
    library = root / "libexec"
    library.mkdir(parents=True)
    native_uv = tmp_path / "home/.local/bin/uv.exe"
    native_uv.parent.mkdir(parents=True)
    shutil.copy2(native, native_uv)
    stub = tmp_path / "powershell-stub.exe"
    shutil.copy2(native, stub)
    bootstrap = (_ROOT / "libexec/update_bootstrap.ps1").read_text(encoding="utf-8-sig")
    # 導入・自己更新の外部作用だけをnative代用品へ向ける。時間切れは実WaitForExitで発生させる。
    bootstrap = bootstrap.replace("'powershell.exe'", "'" + str(stub).replace("'", "''") + "'")
    bootstrap = bootstrap.replace("$Seconds * 1000", "1000")
    (library / "update_bootstrap.ps1").write_text(bootstrap, encoding="utf-8-sig", newline="\r\n")
    shutil.copy2(_ROOT / "libexec/locking_process.ps1", library / "locking_process.ps1")
    pids = tmp_path / "pids"
    pids.mkdir()
    environment = os.environ.copy()
    environment.update(
        INJECT_STAGE=stage,
        INJECT_MODE=mode,
        PIDS=str(pids),
        CALLS=str(tmp_path / "calls"),
        USERPROFILE=str(tmp_path / "home"),
        LOCALAPPDATA=str(tmp_path / "state"),
        PYTHON_ROOT=str(tmp_path / "python"),
        PYTHON_PATH=str(tmp_path / "python/python.exe"),
        AGENT_TOOLKIT_PROCESS_LOOP_SESSION="0",
        UPDATE_DOTFILES_BOOTSTRAP_REPORT=str(tmp_path / "update-bootstrap-1-1.json"),
    )
    environment.pop("CODEX_THREAD_ID", None)
    if stage == "uv-install":
        native_uv.unlink()
    return root, environment


@pytest.mark.parametrize("stage", ["uv-install", "uv-self", "python-upgrade", "python-install"])
@pytest.mark.parametrize("mode", ["exit", "timeout"])
def test_windows_bootstrap_native_failure_boundaries(
    tmp_path: pathlib.Path,
    native_program: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    mode: str,
) -> None:
    """実PS入口の4段で、独立したnative失敗/時間切れと回復・続行を観測する。"""
    root, environment = _project(tmp_path, native_program, stage, mode)
    driver = tmp_path / "driver.ps1"
    script = root / "libexec/update_bootstrap.ps1"
    driver.write_text(
        "function global:Get-CimInstance { param($ClassName,$OperationTimeoutSec) @() }\n"
        f"& '{str(script).replace(chr(39), chr(39) * 2)}' '{environment['USERPROFILE']}\\.local\\bin\\uv.exe' -PrintPython\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8-sig",
    )
    pids = pathlib.Path(environment["PIDS"])
    with _owned_native_pids(pids, pathlib.Path(environment["USERPROFILE"]) / ".local/bin/uv.exe"):
        result = subprocess.run(
            [str(_PS), "-NoProfile", "-File", str(driver)],
            env=environment,
            capture_output=True,
            encoding="utf-8",
            check=False,
            timeout=20,
        )
        calls = pathlib.Path(environment["CALLS"]).read_text(encoding="utf-8").splitlines()
        assert calls.count(stage) == (2 if stage in {"uv-install", "python-install"} else 1)
        assert result.returncode == (1 if stage in {"uv-install", "python-install"} else 0), result.stderr
        assert ("124" if mode == "timeout" else "17") in result.stderr
        if stage in {"uv-self", "python-upgrade"}:
            assert result.stdout.strip() == environment["PYTHON_PATH"]
            failures = json.loads(pathlib.Path(environment["UPDATE_DOTFILES_BOOTSTRAP_REPORT"]).read_text(encoding="utf-8-sig"))
            title = "uvの自己更新" if stage == "uv-self" else "管理Pythonの更新"
            assert any(failure["title"] == title for failure in failures)
            monkeypatch.setenv("UPDATE_DOTFILES_BOOTSTRAP_REPORT", environment["UPDATE_DOTFILES_BOOTSTRAP_REPORT"])
            monkeypatch.setattr(update_dotfiles_runtime, "current_run_id", "bootstrap-test")
            monkeypatch.setattr(update_dotfiles_runtime, "continued_failures", [])
            monkeypatch.setattr(update_dotfiles_runtime, "current_stage_title", None)
            monkeypatch.setattr(update_dotfiles_runtime, "last_stderr_tail", None)
            monkeypatch.setattr(update_dotfiles_runtime, "LOG_PATH", tmp_path / "update.log")
            monkeypatch.setattr(update_dotfiles_runtime.sync_report, "REPORT_PATH", tmp_path / "sync-report.json")
            monkeypatch.setattr(update_dotfiles_runtime.update_dotfiles_report, "report", lambda *_args: None)
            assert update_dotfiles_runtime.finish(0) == 1
            assert not pathlib.Path(environment["UPDATE_DOTFILES_BOOTSTRAP_REPORT"]).exists()
        else:
            assert not result.stdout.strip()
            assert "再実行" in result.stderr
        if mode == "timeout":
            assert any(marker.read_text() == "child" for marker in pids.iterdir())
            assert all(not psutil.pid_exists(int(marker.name)) for marker in pids.iterdir())


def test_windows_cmd_probe_hang_reaps_descendants(tmp_path: pathlib.Path, native_program: pathlib.Path) -> None:
    """実cmd入口から探索を停止させ、実taskkillが親孫を回収しパスを渡さず終了1になる。"""
    root, environment = _project(tmp_path, native_program, "probe", "timeout")
    (root / "bin").mkdir()
    launcher = root / "bin/update-dotfiles.cmd"
    shutil.copy2(_ROOT / "bin/update-dotfiles.cmd", launcher)
    pids = pathlib.Path(environment["PIDS"])
    with _owned_native_pids(pids, pathlib.Path(environment["USERPROFILE"]) / ".local/bin/uv.exe"):
        result = subprocess.run(
            [os.environ["COMSPEC"], "/d", "/c", str(launcher), "job", str(uuid.uuid4())],
            env=environment,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=20,
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert environment["PYTHON_PATH"] not in result.stdout
        assert "124" in result.stderr
        assert any(marker.read_text() == "child" for marker in pids.iterdir())
        assert all(not psutil.pid_exists(int(marker.name)) for marker in pids.iterdir())
