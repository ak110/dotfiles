"""PS bootstrapのnative境界と、実Windowsランチャーの時間切れを検証する。"""

import contextlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Generator
from typing import NoReturn, TextIO

import psutil
import pytest

pytestmark = pytest.mark.windows

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PS = shutil.which("powershell.exe") or shutil.which("pwsh")

sys.path.insert(0, str(_ROOT / "libexec"))
import update_dotfiles_runtime  # noqa: E402  # pylint: disable=wrong-import-position

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


def _project(
    tmp_path: pathlib.Path, native: pathlib.Path, stage: str, mode: str, wait_ms: str = "1000"
) -> tuple[pathlib.Path, dict[str, str]]:
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
    bootstrap = bootstrap.replace("$Seconds * 1000", wait_ms)
    # 使用中Pythonの探索はこの試験の対象外。回収のCimCmdlets\Get-CimInstanceは実OSへ残す。
    bootstrap = bootstrap.replace(
        "function Invoke-UpdateStage {",
        "function Get-CimInstance { param($ClassName,$OperationTimeoutSec) @() }\nfunction Invoke-UpdateStage {",
    )
    bootstrap = bootstrap.replace(
        "[void]$process.Start()", 'Write-UpdateError ("bootstrap-test: start=$Name"); [void]$process.Start()'
    ).replace(
        "$entries = @(CimCmdlets\\Get-CimInstance Win32_Process -OperationTimeoutSec 10 -ErrorAction Stop)",
        'Write-UpdateError ("bootstrap-test: reclaim-snapshot-start=$Name"); '
        "$entries = @(CimCmdlets\\Get-CimInstance Win32_Process -OperationTimeoutSec 10 -ErrorAction Stop); "
        'Write-UpdateError ("bootstrap-test: reclaim-snapshot-end=$Name")',
    )
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


def _invoke_bootstrap(script: pathlib.Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """残存の負例でも継承されたpipeを待たずに、実PS入口の結果を取得する。"""
    command = [
        str(_PS),
        "-NoProfile",
        "-File",
        str(script),
        str(pathlib.Path(environment["USERPROFILE"]) / ".local/bin/uv.exe"),
        "-PrintPython",
    ]
    output, error = script.with_suffix(".stdout"), script.with_suffix(".stderr")
    with output.open("w", encoding="utf-8") as stdout, error.open("w", encoding="utf-8") as stderr:
        try:
            result = subprocess.run(command, env=environment, stdout=stdout, stderr=stderr, check=False, timeout=30)
        except subprocess.TimeoutExpired as failure:
            stdout.flush()
            stderr.flush()
            calls = pathlib.Path(environment["CALLS"])
            native_calls = calls.read_text(encoding="utf-8") if calls.exists() else "(native未到達)"
            raise AssertionError(
                f"bootstrapが{failure.timeout}秒で時間切れ\nstdout:\n{output.read_text(encoding='utf-8')}"
                f"\nstderr:\n{error.read_text(encoding='utf-8')}\nnative calls:\n{native_calls}"
            ) from failure
    return subprocess.CompletedProcess(
        command, result.returncode, output.read_text(encoding="utf-8"), error.read_text(encoding="utf-8")
    )


def test_bootstrap_timeout_reports_captured_output_and_calls(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """親の時間切れでも、失敗地点を区別する両出力とnative到達記録が診断へ残る。"""
    script = tmp_path / "bootstrap.ps1"
    calls = tmp_path / "calls"
    calls.write_text("uv-self\n", encoding="utf-8")

    def timeout(_command: object, *, stdout: TextIO, stderr: TextIO, **_kwargs: object) -> NoReturn:
        stdout.write("probe-output")
        stderr.write("reclaim-snapshot-start")
        raise subprocess.TimeoutExpired("bootstrap", 30)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(AssertionError) as failure:
        _invoke_bootstrap(script, {"USERPROFILE": str(tmp_path), "CALLS": str(calls)})
    assert "probe-output" in str(failure.value)
    assert "reclaim-snapshot-start" in str(failure.value)
    assert "uv-self" in str(failure.value)


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
    # 終了コードを返す正常な起動には、CLR起動を待てる上限を与える。時間切れは1秒で起こす。
    root, environment = _project(tmp_path, native_program, stage, mode, "1000" if mode == "timeout" else "10000")
    driver = tmp_path / "driver.ps1"
    script = root / "libexec/update_bootstrap.ps1"
    driver.write_text(
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
    """実cmd入口から探索を停止させ、親孫を回収しパスを渡さず終了1になる。"""
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


def test_windows_bootstrap_residual_aborts(tmp_path: pathlib.Path, native_program: pathlib.Path) -> None:
    """終了要求を拒否されて子孫が残るときは本体を起動しない。"""
    root, environment = _project(tmp_path, native_program, "uv-self", "timeout")
    script = root / "libexec/update_bootstrap.ps1"
    bootstrap = script.read_text(encoding="utf-8-sig")
    bootstrap = bootstrap.replace("$target.Kill()", "throw '試験用の終了拒否'")
    script.write_text(bootstrap, encoding="utf-8-sig", newline="\r\n")
    pids = pathlib.Path(environment["PIDS"])
    # 残存させる代用品がpipeを継承するため、試験側のcommunicateを待たせない実ファイルへ出力する。
    with (
        _owned_native_pids(pids, pathlib.Path(environment["USERPROFILE"]) / ".local/bin/uv.exe"),
        (tmp_path / "stdout").open("w", encoding="utf-8") as stdout,
        (tmp_path / "stderr").open("w", encoding="utf-8") as stderr,
    ):
        result = subprocess.run(
            [str(_PS), "-NoProfile", "-File", str(script), str(pathlib.Path(environment["USERPROFILE"]) / ".local/bin/uv.exe")],
            env=environment,
            stdout=stdout,
            stderr=stderr,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        stdout.flush()
        stderr.flush()
        assert result.returncode == 1, (tmp_path / "stderr").read_text(encoding="utf-8")
        assert not (tmp_path / "stdout").read_text(encoding="utf-8").strip()
        assert "回収できません" in (tmp_path / "stderr").read_text(encoding="utf-8")
        assert any(psutil.pid_exists(int(marker.name)) for marker in pids.iterdir())


@pytest.mark.parametrize("mode", ["exit", "orphan"])
def test_windows_bootstrap_exit_at_timeout_reaps_orphans(
    tmp_path: pathlib.Path, native_program: pathlib.Path, mode: str
) -> None:
    """時間切れ観測の直後に親が終了しても、残る子だけを回収して本体へ進む。"""
    root, environment = _project(tmp_path, native_program, "uv-self", mode)
    script = root / "libexec/update_bootstrap.ps1"
    bootstrap = script.read_text(encoding="utf-8-sig")
    # OS負荷に依存せず、待機のfalseと回収の間に自然終了する順序を固定する。
    bootstrap = bootstrap.replace(
        "if (-not $process.WaitForExit(1000)) {",
        "if (-not (& { if ($Name -eq 'uvの自己更新') { "
        "if (-not $process.WaitForExit(10000)) { throw '代用品が終了しません' }; "
        "return $false }; return $process.WaitForExit(1000) })) {",
        1,
    )
    script.write_text(bootstrap, encoding="utf-8-sig", newline="\r\n")
    pids = pathlib.Path(environment["PIDS"])
    with _owned_native_pids(pids, pathlib.Path(environment["USERPROFILE"]) / ".local/bin/uv.exe"):
        result = _invoke_bootstrap(script, environment)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == environment["PYTHON_PATH"]
        assert "124" in result.stderr
        if mode == "orphan":
            assert any(marker.read_text() == "child" for marker in pids.iterdir())
        assert all(not psutil.pid_exists(int(marker.name)) for marker in pids.iterdir())


def test_windows_bootstrap_snapshot_child_exit_continues(tmp_path: pathlib.Path, native_program: pathlib.Path) -> None:
    """実CIM一覧に載った子を自然終了させ、世代取得前の消滅で更新を中断しない。"""
    root, environment = _project(tmp_path, native_program, "uv-self", "stale")
    environment["RELEASE_CHILD"] = str(tmp_path / "release")
    script = root / "libexec/update_bootstrap.ps1"
    bootstrap = script.read_text(encoding="utf-8-sig").replace(
        "CimCmdlets\\Get-CimInstance Win32_Process -OperationTimeoutSec 10 -ErrorAction Stop", "Get-TestSnapshot"
    )
    snapshot = r"""
$script:released = $false
function Get-TestSnapshot {
    if ($script:released) { return @(CimCmdlets\Get-CimInstance Win32_Process -OperationTimeoutSec 10 -ErrorAction Stop) }
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        $markers = @(Get-ChildItem -LiteralPath $env:PIDS | Where-Object { (Get-Content $_.FullName -Raw) -eq 'child' })
        if ([DateTime]::UtcNow -gt $deadline) { throw '子の準備が終わりません' }
        if (-not $markers.Count) { Start-Sleep -Milliseconds 10 }
    } while (-not $markers.Count)
    $child = [System.Diagnostics.Process]::GetProcessById([int]$markers[0].Name)
    try {
        $entries = @(CimCmdlets\Get-CimInstance Win32_Process -OperationTimeoutSec 10 -ErrorAction Stop)
        if (-not @($entries | Where-Object { $_.ProcessId -eq $child.Id }).Count) { throw '実CIM一覧に子がありません' }
        [IO.File]::WriteAllText($env:RELEASE_CHILD, 'exit')
        if (-not $child.WaitForExit(10000)) { throw '子が自然終了しません' }
        $script:released = $true
        return $entries
    } finally { $child.Dispose() }
}
"""
    script.write_text(
        bootstrap.replace("function Invoke-UpdateStage {", snapshot + "\nfunction Invoke-UpdateStage {"),
        encoding="utf-8-sig",
        newline="\r\n",
    )
    pids = pathlib.Path(environment["PIDS"])
    with _owned_native_pids(pids, pathlib.Path(environment["USERPROFILE"]) / ".local/bin/uv.exe"):
        result = _invoke_bootstrap(script, environment)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == environment["PYTHON_PATH"]
        assert "124" in result.stderr
        assert pathlib.Path(environment["RELEASE_CHILD"]).exists()
        assert all(not psutil.pid_exists(int(marker.name)) for marker in pids.iterdir())


def test_windows_bootstrap_exited_intermediate_matches_taskkill_boundary(
    tmp_path: pathlib.Path, native_program: pathlib.Path
) -> None:
    """中間親が先に消えた同じツリーで、旧taskkill方式より回収範囲を悪化させない。"""
    remaining = []
    for boundary in ("taskkill", "bootstrap"):
        root, environment = _project(tmp_path / boundary, native_program, "uv-self", "unlinked")
        ready = tmp_path / boundary / "ready"
        environment["TREE_READY"] = str(ready)
        script = root / "libexec/update_bootstrap.ps1"
        pids = pathlib.Path(environment["PIDS"])
        with _owned_native_pids(pids, pathlib.Path(environment["USERPROFILE"]) / ".local/bin/uv.exe"):
            if boundary == "taskkill":
                process = subprocess.Popen(  # pylint: disable=consider-using-with
                    [str(tmp_path / boundary / "powershell-stub.exe"), "-NoProfile"],
                    env=environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    deadline = time.monotonic() + 10
                    while not ready.exists():
                        assert time.monotonic() < deadline, "中間親の終了待ちが完了しない"
                        time.sleep(0.01)
                    # 26faf04cbの停止境界（/T /Fと直接の子の終了確認）を同じ実Win32ツリーへ適用する。
                    result = subprocess.run(
                        ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False, timeout=10
                    )
                    assert result.returncode == 0, result.stdout + result.stderr
                    process.wait(timeout=10)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=10)
            else:
                bootstrap = script.read_text(encoding="utf-8-sig")
                bootstrap = bootstrap.replace(
                    "$process.WaitForExit(1000)",
                    "(& { if ($Name -eq 'uvの自己更新') { $deadline = [DateTime]::UtcNow.AddSeconds(10); "
                    "while (-not (Test-Path -LiteralPath $env:TREE_READY)) { "
                    "if ([DateTime]::UtcNow -gt $deadline) { throw '中間親の終了待ちが完了しない' }; "
                    "Start-Sleep -Milliseconds 10 } }; "
                    "return $process.WaitForExit(100) })",
                    1,
                )
                script.write_text(bootstrap, encoding="utf-8-sig", newline="\r\n")
                observed = _invoke_bootstrap(script, environment)
                assert observed.returncode == 0, observed.stdout + observed.stderr
                assert "124" in observed.stderr
            remaining.append(
                sum(marker.read_text() == "child" and psutil.pid_exists(int(marker.name)) for marker in pids.iterdir())
            )
    assert remaining[1] <= remaining[0], f"旧taskkillの残存数={remaining[0]}, 修正版の残存数={remaining[1]}"
