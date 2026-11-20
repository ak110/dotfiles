"""展開したpost-applyのuv再導入で、表示、保存先、後続へ渡る結果を両OSとも確認する。"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent
_ROUTINE = "Resolved 128 packages in 826ms"
_EXECUTABLES = "Installed 35 executables: test-example"
_CHANGE = " ~ pytools==0.1.0"
_WARNING = "warning: add the tool directory to PATH"
_ERROR = "error: cannot install the tool"


@pytest.mark.windows
@pytest.mark.parametrize("shell", ["bash", "powershell"])
@pytest.mark.parametrize(("code", "save"), [(0, True), (19, True), (0, False), (19, False)])
def test_uv_reinstall_output(tmp_path: Path, shell: str, code: int, save: bool) -> None:
    """uv・使用中検出・最終CLIは代用品。保存不能でも再実行せず、元の成否でハッシュと後続を決める。"""
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
    for name in ("uv_output.sh", "uv_output.ps1", "uv_output_patterns.txt"):
        shutil.copy2(_ROOT / "libexec" / name, helpers / name)
    (helpers / "locking_process.ps1").write_text(
        "function Get-PytoolsEnvLockingProcess {}\nfunction Invoke-PytoolsMediaRemoteStop { param($Processes) }\n",
        encoding="utf-8-sig",
    )
    (binary / ("example.exe" if powershell else "example")).touch(mode=0o755)
    lines = [_ROUTINE, _EXECUTABLES, _CHANGE, _WARNING]
    if code:
        lines += ["diagnostic token=fixture-value", _ERROR, "hint: retry the installation"]
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
            + f"exit {code}\n",
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
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "LOCALAPPDATA": str(state),
        "XDG_STATE_HOME": str(state),
        "XDG_CACHE_HOME": str(state),
        "PATH": str(binary) + os.pathsep + os.environ["PATH"],
    }
    command = [program, *(["-NoProfile", "-File"] if powershell else []), str(script)]
    result = subprocess.run(command, env=env, capture_output=True, encoding="utf-8", check=False, timeout=20)
    screen = result.stdout + result.stderr
    assert "Set-StrictMode:" not in screen
    assert result.returncode == (23 if code else 0), screen
    assert (state / "dotfiles/pytools.hash").exists() is (not code)
    if save:
        detail = log.read_text(encoding="utf-8")
        assert all(line in detail for line in lines if "token=" not in line)
        assert "fixture-value" not in detail
        assert _ROUTINE not in screen and _EXECUTABLES not in screen
        assert _CHANGE in screen and _WARNING in screen
        if code:
            assert _ERROR in screen and str(log) in screen
            assert "hint: retry the installation" in screen
    else:
        assert "保存できません" in result.stderr and str(log) in result.stderr
        assert all(line in result.stderr for line in lines)
