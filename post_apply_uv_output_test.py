"""展開したpost-applyのuv再導入で、表示、保存先、後続へ渡る結果を両OSとも確認する。"""

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _test_helpers import POSIX_UPDATE_STAGE_JOB

_ROOT = Path(__file__).resolve().parent
_ROUTINE = "Resolved 128 packages in 826ms"
_EXECUTABLES = "Installed 35 executables: test-example, token-inspect"
_CHANGE = " ~ pytools==0.1.0"
_WARNING = "warning: add the tool directory to PATH"
_ACTION = "Run uv tool update-shell to update PATH"
_LAST_WARNING = "warning: last diagnostic before interrupt"
_ERROR = "error: cannot install the tool"


@pytest.mark.windows
@pytest.mark.parametrize("shell", ["bash", "powershell"])
@pytest.mark.parametrize(("code", "save"), [(0, True), (19, True), (0, False), (19, False), (130, True), (130, False)])
def test_uv_reinstall_output(tmp_path: Path, shell: str, code: int, save: bool) -> None:
    """uv・使用中検出・最終CLIは代用品。保存不能でも再実行せず、元の成否でハッシュと後続を決める。"""
    if code == 130 and os.name == "nt":
        pytest.skip("隔離したPOSIX前景プロセス群へのSIGINTを使う")
    powershell = shell == "powershell"
    program = (shutil.which("powershell.exe") or shutil.which("pwsh")) if powershell else shutil.which("bash")
    chezmoi = shutil.which("chezmoi")
    if not program or not chezmoi or (not powershell and sys.platform == "win32"):
        pytest.skip("対象shellとchezmoiが必要")
    home = tmp_path / "home"
    binary = home / ".local/bin"
    binary.mkdir(parents=True)
    root = tmp_path / "project"
    helpers = root / "libexec"
    helpers.mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project.scripts]\nexample = "example:main"\n', encoding="utf-8")
    for name in (
        "update_stage.sh",
        "uv_output.sh",
        "uv_output.ps1",
        "uv_output_patterns.txt",
        "update_stage.ps1",
        "update_stage_job.ps1",
    ):
        shutil.copy2(_ROOT / "libexec" / name, helpers / name)
    (helpers / "locking_process.ps1").write_text(
        "function Get-PytoolsEnvLockingProcess {}\nfunction Invoke-PytoolsMediaRemoteStop { param($Processes) }\n",
        encoding="utf-8-sig",
    )
    (binary / ("example.exe" if powershell else "example")).touch(mode=0o755)
    lines = [_ROUTINE, _EXECUTABLES, "Checking the package cache", _CHANGE, _WARNING, _ACTION]
    if code:
        lines += ["diagnostic token=fixture-value", _ERROR, "hint: retry the installation"]
    interrupt = tmp_path / "interrupt.py"
    if code == 130:
        # 最初の警告がstderrへ届くまでuvを終えない。最後の書込みとSIGINTの間には待ちを入れない。
        interrupt.write_text(
            "import os, pathlib, signal, time\n"
            "screen = pathlib.Path(os.environ['SCREEN'])\n"
            "deadline = time.monotonic() + 5\n"
            f"while {_WARNING.encode()!r} not in screen.read_bytes():\n"
            "    if time.monotonic() >= deadline: raise SystemExit(91)\n"
            "    time.sleep(0.01)\n"
            f"os.write(2, {_LAST_WARNING.encode()!r} + b'\\n')\n"
            "os.killpg(os.getsid(0), signal.SIGINT)\n"
            "time.sleep(5)\n",
            encoding="utf-8",
        )
    if os.name == "nt":
        (binary / "uv.cmd").write_text(
            '@echo off\nif "%2"=="dir" (echo %USERPROFILE%& exit /b 0)\n'
            + "".join(f"echo {line} 1>&2\n" for line in lines)
            + f"exit /b {code}\n",
            encoding="ascii",
        )
    else:
        uv = binary / "uv"
        uv.write_text(
            '#!/bin/sh\nif [ "$2" = dir ]; then echo "$HOME"; exit 0; fi\n'
            + "".join(f"echo '{line}' >&2\n" for line in lines)
            + (f"exec {shlex.quote(sys.executable)} {shlex.quote(str(interrupt))}\n" if code == 130 else f"exit {code}\n"),
            encoding="ascii",
        )
        uv.chmod(0o755)
    suffix = "-windows.ps1" if powershell else ".sh"
    template = _ROOT / f".chezmoi-source/run_after_post-apply{suffix}.tmpl"
    rendered = subprocess.check_output(
        [
            chezmoi,
            "--source",
            str(_ROOT / ".chezmoi-source"),
            "--working-tree",
            str(root),
            "execute-template",
            "--file",
            str(template),
        ],
        encoding="utf-8",
    )
    if powershell:
        if os.name != "nt":
            rendered = POSIX_UPDATE_STAGE_JOB + rendered.removeprefix("\ufeff")
        rendered = rendered.replace(".local\\bin\\dotfiles-post-apply.exe", ".local\\bin\\consumer.ps1")
        (binary / "consumer.ps1").write_text("if ($env:DOTFILES_PYTOOLS_INSTALL_STATE -eq 'failed') { exit 23 }; exit 0\n")
    else:
        consumer = binary / "dotfiles-post-apply"
        consumer.write_text('#!/bin/sh\n[ "$DOTFILES_PYTOOLS_INSTALL_STATE" != failed ] || exit 23\n')
        consumer.chmod(0o755)
    script = tmp_path / ("apply.ps1" if powershell else "apply.sh")
    script.write_text(rendered.removeprefix("\ufeff"), encoding="utf-8-sig" if powershell else "utf-8")
    state = tmp_path / "state"
    log = state / "agent-toolkit" / ("uv-pytools-failed.log" if code else "uv-pytools.log")
    if not save:
        log.mkdir(parents=True)  # 一時ファイルは書けるが、最終ファイルを発行できない。
        if not powershell:
            # 後始末も失敗する場合でも、uvの診断と本来の終了状態は失わない。
            remove = binary / "rm"
            remove.write_text("#!/bin/sh\nexit 1\n")
            remove.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "LOCALAPPDATA": str(state),
        "XDG_STATE_HOME": str(state),
        "XDG_CACHE_HOME": str(state),
        "SCREEN": str(tmp_path / "stderr"),
        "PATH": str(binary) + os.pathsep + os.environ["PATH"],
    }
    command = [program, *(["-NoProfile", "-File"] if powershell else []), str(script)]
    with (tmp_path / "stderr").open("wb") as errors:
        result = subprocess.run(
            command,
            env=env,
            stdout=subprocess.PIPE,
            stderr=errors,
            encoding="utf-8",
            check=False,
            timeout=20,
            start_new_session=code == 130,
        )
    stderr = (tmp_path / "stderr").read_text(encoding="utf-8")
    screen = result.stdout + stderr
    assert "Set-StrictMode:" not in screen
    if code == 130:
        assert result.returncode == 130, screen
        assert stderr.count(_LAST_WARNING) == 1
        lines.append(_LAST_WARNING)
    else:
        assert result.returncode == (23 if code else 0), screen
    assert (state / "dotfiles/pytools.hash").exists() is (not code)
    if save:
        detail = log.read_text(encoding="utf-8")
        assert all(line in detail for line in lines if "token=" not in line)
        assert "fixture-value" not in detail
        assert _ROUTINE not in screen and _EXECUTABLES not in screen and "Checking the package cache" not in screen
        assert all(line in screen for line in (_CHANGE, _WARNING, _ACTION))
        if code:
            assert _ERROR in screen and str(log) in screen
            assert "hint: retry the installation" in screen
    else:
        assert "保存できません" in stderr and str(log) in stderr
        assert all(line in stderr for line in lines)
