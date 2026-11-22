"""ssh_unelevatedの試験。

予定タスクを使う接続先の処理はWindowsでしか動かないため、ここでは結果の解釈と、pwshで動かせる部分
（sshの起動部から`remote.ps1`へ引数が届くこと、本文が届かない場合に起動部が上限で終わること、
`runner.ps1`が出力と終了コードを残すこと、予定タスクのジョブの外でスクリプトを起動できない場合に理由を残すこと）を確かめる。
"""

import codecs
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

import psutil
import pytest

from pytools.ssh_unelevated import _cli

_RUN_ID = "0123456789abcdef"
_requires_pwsh = pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh未導入")


@pytest.mark.skipif(not hasattr(os, "WNOWAIT"), reason="waitidのWNOWAITが必要")
@pytest.mark.parametrize(("exit_code", "inherit"), [(0, False), (23, False), (0, True)])
def test_posix_reclaims_helpers_after_ssh_exits(exit_code: int, inherit: bool) -> None:
    helper_stream = "None" if inherit else "subprocess.DEVNULL"
    fake_ssh = (
        "import subprocess, sys;"
        f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
        f"stdout={helper_stream}, stderr={helper_stream});"
        "print(p.pid, flush=True);"
        f"sys.exit({exit_code})"
    )
    with _cli.process_scope(
        [sys.executable, "-c", fake_ssh],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    ) as process:
        started = time.monotonic()
        # pylint: disable-next=protected-access
        stdout, _ = _cli._communicate_posix(process, b"", 15)
        assert time.monotonic() - started < 15
        assert process.returncode == exit_code
        pid = int(stdout)
        deadline = time.monotonic() + 3
        while psutil.pid_exists(pid):
            try:
                if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                    break
            except psutil.NoSuchProcess:
                break
            if time.monotonic() >= deadline:
                pytest.fail("sshの補助プロセスが残った")
            time.sleep(0.02)


@pytest.mark.skipif(not hasattr(os, "WNOWAIT"), reason="waitidのWNOWAITが必要")
def test_posix_drains_large_output_while_sending_input() -> None:
    size = 1024 * 1024
    fake_ssh = (
        "import sys;"
        f"sys.stdout.buffer.write(b'o' * {size});"
        f"sys.stderr.buffer.write(b'e' * {size});"
        "sys.stdout.buffer.write(sys.stdin.buffer.read())"
    )
    with _cli.process_scope(
        [sys.executable, "-c", fake_ssh],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    ) as process:
        # pylint: disable-next=protected-access
        stdout, stderr = _cli._communicate_posix(process, b"i" * size, 15)
        assert process.returncode == 0
    assert stdout == b"o" * size + b"i" * size
    assert stderr == b"e" * size


def _remote_stdout(output: bytes, result: dict[str, object], run_id: str = _RUN_ID) -> bytes:
    return output + f"\nSSH_UNELEVATED_RESULT:{run_id} {json.dumps(result)}\n".encode()


@pytest.mark.parametrize(
    ("result", "exit_code", "diagnostic_parts"),
    [
        ({"status": "completed", "exit_code": 0}, 0, None),
        ({"status": "completed", "exit_code": 7}, 7, None),
        ({"status": "completed", "exit_code": 3221225786}, 1, ["3221225786"]),
        ({"status": "timeout"}, 124, ["60秒"]),
        ({"status": "not_logged_on", "user": "STHENO\\ms"}, 125, ["stheno", "STHENO\\ms", "ログオン"]),
        ({"status": "not_started", "task_result": 0x41303}, 125, ["開始しなかった", "0x00041303"]),
        ({"status": "no_result", "task_result": 0x800710E0}, 125, ["残さずに終わった", "0x800710E0"]),
        ({"status": "error", "message": "Access is denied."}, 125, ["Access is denied."]),
    ],
)
def test_interpret_maps_remote_status(result: dict[str, object], exit_code: int, diagnostic_parts: list[str] | None) -> None:
    """接続先の状態ごとに終了コードを決め、スクリプトの出力はLFの改行で返す。"""
    outcome = _cli.interpret(_remote_stdout("日本語\r\n".encode(), result), 0, run_id=_RUN_ID, host="stheno", timeout=60)
    assert outcome.exit_code == exit_code
    assert outcome.output == "日本語\n"
    if diagnostic_parts is None:
        assert outcome.diagnostic is None
    else:
        assert outcome.diagnostic is not None
        assert all(part in outcome.diagnostic for part in diagnostic_parts), outcome.diagnostic


def test_interpret_keeps_result_lines_of_other_runs_in_output() -> None:
    """スクリプトの中で入れ子に実行した別の実行の結果の行は、出力として残す。"""
    nested = _remote_stdout(b"inner", {"status": "completed", "exit_code": 5}, run_id="ffffffffffffffff")
    outcome = _cli.interpret(
        _remote_stdout(nested, {"status": "completed", "exit_code": 0}), 0, run_id=_RUN_ID, host="stheno", timeout=60
    )
    assert outcome.exit_code == 0
    assert outcome.output == nested.decode()


@pytest.mark.parametrize(("returncode", "exit_code", "part"), [(255, 255, "接続できなかった"), (1, 125, "結果が返らなかった")])
def test_interpret_without_result_line(returncode: int, exit_code: int, part: str) -> None:
    """結果の行が無い場合は、sshの接続失敗とそれ以外を分け、届いた出力は残す。"""
    outcome = _cli.interpret(b"partial", returncode, run_id=_RUN_ID, host="stheno", timeout=60)
    assert outcome.exit_code == exit_code
    assert outcome.output == "partial"
    assert outcome.diagnostic is not None
    assert part in outcome.diagnostic


def test_main_rejects_non_positive_timeout(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        _cli.main(["--timeout", "0", "stheno", "script.ps1"])
    assert excinfo.value.code == 2
    assert "1以上の秒数" in capsys.readouterr().err


def test_main_rejects_non_utf8_script(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = tmp_path / "script.ps1"
    script.write_bytes("日本語".encode("cp932"))
    with pytest.raises(SystemExit) as excinfo:
        _cli.main(["stheno", str(script)])
    assert excinfo.value.code == 2
    assert "UTF-8" in capsys.readouterr().err


@_requires_pwsh
@pytest.mark.skipif(sys.platform == "win32", reason="偽のsshをPOSIXのシェルスクリプトで作る")
def test_main_delivers_large_script_to_remote(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """数十KBのスクリプトでも、起動部がremote.ps1を引数付きで実行し、その結果の行を読み取る。

    Linuxのpwshにはタスクスケジューラーへ接続するCOMが無いため、remote.ps1はその失敗を結果の行で返す。
    引数が届かない場合や起動部が本文を復元できない場合は、結果の行が無い失敗になる。
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_ssh = bin_dir / "ssh"
    # 最後の引数（接続先で実行するコマンド行）を、Windowsのsshdの代わりにpwshで実行する。
    fake_ssh.write_text("#!/bin/sh\nfor command; do :; done\nexec pwsh ${command#powershell }\n", encoding="utf-8")
    fake_ssh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    script = tmp_path / "large.ps1"
    script.write_text("\n".join(f"'{i}-日本語-''quote''-`tick-$notvar'" for i in range(3000)), encoding="utf-8")
    assert script.stat().st_size > 50_000

    with pytest.raises(SystemExit) as excinfo:
        _cli.main(["--timeout", "30", "stheno", str(script)])

    stderr = capsys.readouterr().err
    assert excinfo.value.code == 125
    assert "sthenoでの準備か後始末に失敗した" in stderr
    assert "ComObject" in stderr


@_requires_pwsh
@pytest.mark.skipif(sys.platform == "win32", reason="偽のsshをshebangのPythonスクリプトで作る")
def test_main_reports_stdin_not_arriving(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """標準入力の本文が届かない場合、接続先のPowerShellは上限で自ら終了し、診断付きの終了コード125を返す。

    偽のsshは、起動部のpwshの標準入力を開いたまま何も書かない。
    手元の上限（--timeoutとsshの余裕の和）より先に終わることで、接続先のプロセスが残らないことを確かめる。
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_ssh = bin_dir / "ssh"
    fake_ssh.write_text(
        f"#!{sys.executable}\n"
        "import subprocess, sys\n"
        "process = subprocess.Popen(['pwsh', *sys.argv[-1].split()[1:]], stdin=subprocess.PIPE)\n"
        "sys.exit(process.wait())\n",
        encoding="utf-8",
    )
    fake_ssh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(_cli, "_STDIN_TIMEOUT_SECONDS", 3)
    monkeypatch.setattr(_cli, "_SSH_MARGIN_SECONDS", 60)
    script = tmp_path / "script.ps1"
    script.write_text("Write-Output 'script-ran'", encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        _cli.main(["--timeout", "1", "stheno", str(script)])

    captured = capsys.readouterr()
    assert excinfo.value.code == 125
    assert "script-ran" not in captured.out
    assert "標準入力から本文を3秒以内に読み終えられなかった" in captured.err
    assert "結果が返らなかった" in captured.err


def _run_runner(tmp_path: pathlib.Path, script: str) -> tuple[str, str]:
    """remote.ps1が置く形でrunner.ps1とscript.ps1を置いて実行し、残った出力と終了コードを返す。"""
    runner = tmp_path / "runner.ps1"
    runner.write_bytes(pathlib.Path(_cli.__file__).with_name("runner.ps1").read_bytes())
    (tmp_path / "script.ps1").write_bytes(codecs.BOM_UTF8 + script.encode("utf-8"))
    subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", str(runner), "-RunId", _RUN_ID], check=True, timeout=60)
    output = (tmp_path / "output.txt").read_text(encoding="utf-8-sig")
    assert (tmp_path / "started.txt").read_text(encoding="ascii").strip() == _RUN_ID
    run_id, code = (tmp_path / "exit.txt").read_text(encoding="ascii").split()
    assert run_id == _RUN_ID
    return output, code


@_requires_pwsh
@pytest.mark.parametrize(
    ("script", "expected_output", "expected_exit"),
    [
        ("Write-Output '日本語'\nexit 7", "日本語", "7"),
        ("Write-Output 'before'\nthrow '意図した失敗'", "意図した失敗", "1"),
        ("& (Get-Process -Id $PID).Path -NoProfile -Command 'exit 3'", "", "3"),
        # PowerShellの既定のとおり、エラーで止めず、未定義の変数も参照できる。
        ("Get-Item -LiteralPath '/nonexistent/path'\nWrite-Output \"after=$undefinedVariable.\"", "after=.", "0"),
    ],
)
def test_runner_records_output_and_exit_code(
    tmp_path: pathlib.Path, script: str, expected_output: str, expected_exit: str
) -> None:
    output, exit_code = _run_runner(tmp_path, script)
    assert expected_output in output
    assert exit_code == expected_exit


@_requires_pwsh
def test_runner_reports_failure_to_leave_task_job(tmp_path: pathlib.Path) -> None:
    """ジョブの外で起動し直せない場合は、スクリプトを実行せずに理由を出力へ残し、終了コードを残さない。

    remote.ps1は終了コードが無いことを結果なし（終了コード125）として返し、出力の理由をユーザーへ見せる。
    Linuxのpwshにはプロセスを生成するWMIが無いため、起動し直しが失敗する。
    """
    runner = tmp_path / "runner.ps1"
    runner.write_bytes(pathlib.Path(_cli.__file__).with_name("runner.ps1").read_bytes())
    (tmp_path / "script.ps1").write_bytes(codecs.BOM_UTF8 + b"Write-Output 'script-ran'")

    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(runner), "-LeaveTaskJob", "-RunId", _RUN_ID],
        check=False,
        timeout=60,
    )

    assert completed.returncode == 1
    output = (tmp_path / "output.txt").read_text(encoding="utf-8-sig")
    assert "予定タスクのジョブの外でスクリプトを起動できなかった" in output
    assert "script-ran" not in output
    assert not (tmp_path / "exit.txt").exists()


@pytest.mark.parametrize("result_fragment", [b"", b'{"status":', b'{"status":"completed","exit_code":0}'])
def test_interpret_disconnect_after_start_reports_unknown(result_fragment: bytes) -> None:
    stdout = f"SSH_UNELEVATED_STARTED:{_RUN_ID}\n".encode()
    stdout += _remote_stdout(b"partial", {"status": "completed", "exit_code": 0}, run_id="ffffffffffffffff")
    if result_fragment:
        stdout += f"\nSSH_UNELEVATED_RESULT:{_RUN_ID} ".encode() + result_fragment
    outcome = _cli.interpret(stdout, 255, run_id=_RUN_ID, host="stheno", timeout=60)
    assert outcome.exit_code != 0
    assert outcome.diagnostic is not None and "実行結果は不明" in outcome.diagnostic
    assert "partial" in outcome.output


@_requires_pwsh
def test_cleanup_skips_owned_and_running_requests(tmp_path: pathlib.Path) -> None:
    remote_source = pathlib.Path(_cli.__file__).with_name("remote.ps1")
    test_script = tmp_path / "test-stale.ps1"
    test_script.write_text(
        r"""
param([string] $Remote, [string] $Parent)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$null = $tokens = $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Remote, [ref] $tokens, [ref] $errors)
if ($errors) { throw ($errors | Out-String) }
$definition = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Clear-StaleRun'
}, $false)
. ([scriptblock]::Create($definition.Extent.Text))
$namePattern = '^ssh-unelevated-(\d+)-[0-9a-f]+$'
$now = 2
$taskStateQueued = 2
$taskStateRunning = 4
function Get-ScheduledTask { param($TaskPath, $TaskName, $ErrorAction) }
function Get-RunTask([string] $Name) {
    if ($Name -eq 'ssh-unelevated-1-bbbb') { @{ State = 4 } }
}
function Get-RunProcessId([string] $Dir) {
    if ($Dir.EndsWith('cccc')) { 20 }
}
function Clear-Run([string] $Name) {
    if ($Name -eq 'ssh-unelevated-1-dddd') { throw 'cleanup raced' }
    Write-Output $Name
}
foreach ($id in 'aaaa', 'bbbb', 'cccc', 'dddd', 'eeee') {
    $dir = Join-Path $Parent "ssh-unelevated-1-$id"
    New-Item -ItemType Directory -Path $dir | Out-Null
    New-Item -ItemType File -Path (Join-Path $dir 'owner.lock') | Out-Null
}
$owner = [IO.File]::Open((Join-Path $Parent 'ssh-unelevated-1-aaaa/owner.lock'), 'Open', 'ReadWrite', 'None')
try {
    Clear-StaleRun $Parent
} finally {
    $owner.Dispose()
}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(test_script), str(remote_source), str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.stdout.splitlines() == ["ssh-unelevated-1-eeee"]
    assert "cleanup raced" in result.stderr
