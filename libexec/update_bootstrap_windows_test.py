"""PS bootstrapのnative境界と、実Windowsランチャーの時間切れを検証する。"""

import contextlib
import importlib
import os
import pathlib
import shutil
import subprocess
from collections.abc import Generator

import psutil
import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PS = shutil.which("powershell.exe") or shutil.which("pwsh")

# 同じ代用品で、PowerShellの実Process起動・待機・子孫回収まで通す。
_NATIVE_SOURCE = r"""
using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
public class Program {
    public static int Main(string[] args) {
        string stage = "";
        string self = System.Reflection.Assembly.GetExecutingAssembly().Location;
        if (args.Length > 0 && args[0] == "--middle") {
            using (Process child = Process.Start(new ProcessStartInfo(self, "--child") { UseShellExecute=false })) {
                string marker = Path.Combine(Environment.GetEnvironmentVariable("PIDS"), child.Id.ToString());
                while (!File.Exists(marker)) Thread.Sleep(1);
            }
            return 0;
        }
        if (args.Length > 0 && args[0] == "--child") {
            File.WriteAllText(Path.Combine(Environment.GetEnvironmentVariable("PIDS"),
                Process.GetCurrentProcess().Id.ToString()), "child");
            if (Environment.GetEnvironmentVariable("INJECT_MODE") == "stale") {
                while (!File.Exists(Environment.GetEnvironmentVariable("RELEASE_CHILD"))) Thread.Sleep(1);
                return 0;
            }
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
        if (Environment.GetEnvironmentVariable("INJECT_MODE") == "unlinked") {
            using (Process middle = Process.Start(new ProcessStartInfo(self, "--middle") { UseShellExecute=false })) {
                middle.WaitForExit();
            }
            File.WriteAllText(Path.Combine(Environment.GetEnvironmentVariable("PIDS"),
                Process.GetCurrentProcess().Id.ToString()), "parent");
            File.WriteAllText(Environment.GetEnvironmentVariable("TREE_READY"), "ready");
            Thread.Sleep(60000); return 0;
        }
        using (Process child = Process.Start(new ProcessStartInfo(self, "--child") { UseShellExecute=false })) {
            File.WriteAllText(Path.Combine(Environment.GetEnvironmentVariable("PIDS"),
                Process.GetCurrentProcess().Id.ToString()), "parent");
            if (Environment.GetEnvironmentVariable("INJECT_MODE") == "orphan") {
                string marker = Path.Combine(Environment.GetEnvironmentVariable("PIDS"), child.Id.ToString());
                while (!File.Exists(marker)) Thread.Sleep(1);
                return 17;
            }
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


def _run_stage(tmp_path: pathlib.Path, body: str) -> subprocess.CompletedProcess[str]:
    if _PS is None:
        pytest.skip("PowerShellが必要")
    script = tmp_path / "stage.ps1"
    stage = str(_ROOT / "libexec/update_stage.ps1").replace("'", "''")
    script.write_text(
        f". '{stage}'\n"
        "$script:bootstrapFailures=@(); $script:lastStageOutput=''; "
        "function Write-UpdateError { param($Message) [Console]::Error.WriteLine($Message) }\n" + body,
        encoding="utf-8-sig",
    )
    return subprocess.run(
        [str(_PS), "-NoProfile", "-File", str(script)], capture_output=True, encoding="utf-8", check=False, timeout=30
    )


def test_stage_classification(tmp_path: pathlib.Path) -> None:
    """同じPS起動内で、製品判定のcontinue・abort・Probeの消費結果を確認する。"""
    result = _run_stage(
        tmp_path,
        "$p=New-Object PSObject; $p | Add-Member ScriptMethod Dispose {}; "
        "$cases=@(@('continue',$false,17,$false),@('abort',$false,17,$true),"
        "@('abort',$true,17,$false),@('abort',$false,0,$false)); "
        "foreach ($case in $cases) { "
        "$script:bootstrapFailures=@(); $failed=$false; $code=$null; "
        "try { $code=Invoke-UpdateStage test $case[0] 1 unused @() -Probe:$case[1] "
        "-Start {$p} -Wait {$true} -Stop {} -Output {' probe '} -ExitCode {$case[2]} } "
        "catch { $failed=$true }; "
        "if ($failed -ne $case[3]) {throw 'classification'}; "
        "if (-not $failed -and $code -ne $case[2]) {throw 'code'}; "
        "if ($script:bootstrapFailures.Count -ne [int]($case[2] -ne 0)) {throw 'failures'}; "
        "if ($case[1] -and $script:lastStageOutput -ne 'probe') {throw 'output'} }",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_stage_timeout_and_unknown_ownership(tmp_path: pathlib.Path) -> None:
    """時間切れ124と、continueでも回収不明を伝播する契約を分ける。"""
    result = _run_stage(
        tmp_path,
        "$p=New-Object PSObject; $p | Add-Member ScriptMethod Dispose {}; "
        "$code=Invoke-UpdateStage test continue 1 unused @() -Start { $p } -Wait { $false } -Stop {}; "
        "if ($code -ne 124) { throw 'timeout' }; "
        "try { Invoke-UpdateStage test continue 1 unused @() -Start { $p } -Wait { $true } "
        "-ExitCode {0} -Stop {throw 'unknown'}; throw 'not propagated' } "
        "catch [System.TimeoutException] { exit 0 }",
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["timeout", "orphan", "unlinked"])
def test_stage_native_job_reaps_tree(
    tmp_path: pathlib.Path, native_program: pathlib.Path, mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """停止状態生成した実Jobは短命中間親の末端も回収し、stdioを閉じる。"""
    pids = tmp_path / "pids"
    pids.mkdir()
    for key, value in {
        "PIDS": str(pids),
        "CALLS": str(tmp_path / "calls"),
        "INJECT_STAGE": "uv-self",
        "INJECT_MODE": mode,
        "TREE_READY": str(tmp_path / "ready"),
    }.items():
        monkeypatch.setenv(key, value)
    native = str(native_program).replace("'", "''")
    with _owned_native_pids(pids, native_program):
        result = _run_stage(
            tmp_path,
            f"$code=Invoke-UpdateStage test continue 2 '{native}' @('-NoProfile') -Probe; "
            "if ($code -notin @(17,124)) {throw 'exit'}",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert any(marker.read_text() == "child" for marker in pids.iterdir())
        assert all(not psutil.pid_exists(int(marker.name)) for marker in pids.iterdir())


def test_bootstrap_entry_uses_stage_without_running_native_updates(
    tmp_path: pathlib.Path, native_program: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """入口の最小実境界でProbeのパスをPrintPythonへ渡す。"""
    bootstrap = str(_ROOT / "libexec/update_bootstrap.ps1").replace("'", "''")
    monkeypatch.setenv("CALLS", str(tmp_path / "calls"))
    monkeypatch.setenv("PYTHON_PATH", "managed-python")
    monkeypatch.setenv("INJECT_STAGE", "none")
    result = _run_stage(
        tmp_path,
        f"& '{bootstrap}' '{str(native_program).replace(chr(39), chr(39) * 2)}' -PrepareOnly -PrintPython; exit $LASTEXITCODE",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "managed-python"


def test_stage_continued_failure_reaches_update_result(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """continueの記録は更新結果で失敗として消費され、成功に埋もれない。"""
    monkeypatch.syspath_prepend(str(_ROOT / "libexec"))
    runtime = importlib.import_module("update_dotfiles_runtime")
    report = tmp_path / "update-bootstrap-1-1.json"
    monkeypatch.setenv("UPDATE_DOTFILES_BOOTSTRAP_REPORT", str(report))
    result = _run_stage(
        tmp_path,
        "$p=New-Object PSObject; $p | Add-Member ScriptMethod Dispose {}; "
        "$null=Invoke-UpdateStage test continue 1 unused @() -Start {$p} -Wait {$true} -ExitCode {17} -Stop {}; "
        "ConvertTo-Json -InputObject @($script:bootstrapFailures) -Compress | "
        "Set-Content -LiteralPath $env:UPDATE_DOTFILES_BOOTSTRAP_REPORT -Encoding UTF8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    monkeypatch.setattr(runtime, "current_run_id", "bootstrap-test")
    monkeypatch.setattr(runtime, "continued_failures", [])
    monkeypatch.setattr(runtime, "current_stage_title", None)
    monkeypatch.setattr(runtime, "last_stderr_tail", None)
    monkeypatch.setattr(runtime, "LOG_PATH", tmp_path / "update.log")
    monkeypatch.setattr(runtime.sync_report, "REPORT_PATH", tmp_path / "sync-report.json")
    monkeypatch.setattr(runtime.update_dotfiles_report, "report", lambda *_args: None)
    assert runtime.finish(0) == 1
    assert not report.exists()
