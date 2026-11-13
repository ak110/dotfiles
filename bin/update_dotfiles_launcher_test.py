"""`update-dotfiles`ランチャーのuv解決契約を検証する。"""

import os
import pathlib
import shutil
import subprocess
import sys
from collections.abc import Callable

import platformdirs
import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_LAUNCHER = _ROOT / "bin" / "update-dotfiles"
_WINDOWS_LAUNCHER = _ROOT / "bin" / "update-dotfiles.cmd"
_LINUX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="BashランチャーはLinuxで検証する")
_UPDATE_WARNING = (
    "uvの自己更新に失敗しました。既存のuvでdotfiles更新を実行し、次回のupdate-dotfiles起動時に自己更新を再試行します。"
    "対応不要（dotfilesの更新処理は継続しました）。"
)

# 実更新中にcmd.exeが読み直す位置を再現する、block化前の公開ランチャー。
_LEGACY_WINDOWS_PREFIX = """@echo off
setlocal
for /f "delims=" %%A in ('cd /d "%~dp0.." ^& cd') do set SCRIPT_DIR=%%A
set "UV=%USERPROFILE%\\.local\\bin\\uv.exe"
if not exist "%UV%" (
    echo uv was not found. Install uv with the official installer. 1>&2
    exit /b 127
)
set "UV_SELF_UPDATE_FAILED=0"
if not "%AGENT_TOOLKIT_PROCESS_LOOP_SESSION%"=="1" (
    "%UV%" self update
    if errorlevel 1 set "UV_SELF_UPDATE_FAILED=1"
)
"%UV%" run --no-project --script "%SCRIPT_DIR%\\libexec\\update_dotfiles.py" %*
"""
_LOGS_WINDOWS_PREFIX = _LEGACY_WINDOWS_PREFIX.replace(
    'if not "%AGENT_TOOLKIT_PROCESS_LOOP_SESSION%"',
    'if not "%~1"=="logs" if not "%AGENT_TOOLKIT_PROCESS_LOOP_SESSION%"',
)


# test-copy-contract: 公開済み旧batchのbyte読取位置で、更新後も終了値取得から再開する互換契約を保つ。
@pytest.mark.parametrize("prefix", [_LEGACY_WINDOWS_PREFIX, _LOGS_WINDOWS_PREFIX], ids=["legacy", "logs"])
def test_windows_replacement_resumes_at_exit_capture(prefix: str) -> None:
    """公開済みbatchの再開位置から更新を重複実行せず終了状態を取得する。"""
    resume = len(prefix.replace("\n", "\r\n").encode("cp932"))
    remainder = _WINDOWS_LAUNCHER.read_bytes()[resume:].decode("cp932")
    commands = [line for line in remainder.splitlines() if line.strip() and not line.startswith("rem ")]

    assert commands[0] == 'call set "UPDATE_DOTFILES_EXIT=%%ERRORLEVEL%%"'


def _write_fake_uv(path: pathlib.Path, name: str) -> None:
    """呼び出しをNUL区切りで記録するfake uvを作成する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""#!/usr/bin/env bash
printf '\\036{name}\\000' >> "$UV_CALL_LOG"
printf '%s\\000' "$@" >> "$UV_CALL_LOG"
if [[ ( "${{STALL_STAGE:-}}" == 'uvの自己更新' && "${{1-}} ${{2-}}" == 'self update' ) ||
      ( "${{STALL_STAGE:-}}" == '管理Pythonの更新' && "${{1-}} ${{2-}} ${{3-}}" == 'python install --upgrade' ) ||
      ( "${{STALL_STAGE:-}}" == '管理Pythonの導入' && "${{1-}} ${{2-}} ${{3-}}" == 'python install 3.15' ) ]]; then sleep 60; fi
if [[ "${{1-}}" == "self" && "${{2-}}" == "update" ]]; then
    [[ -z "${{UV_SELF_UPDATE_MESSAGE-}}" ]] || printf '%s\n' "$UV_SELF_UPDATE_MESSAGE" >&2
    exit "${{UV_SELF_UPDATE_EXIT:-0}}"
fi
if [[ "${{1-}} ${{2-}}" == "python install" ]]; then
    if [[ "${{STALL_UPGRADE:-0}}" == 1 && "${{3-}}" == --upgrade ]]; then while :; do :; done; fi
    exit "${{PYTHON_UPGRADE_EXIT:-0}}"
fi
if [[ "${{1-}} ${{2-}}" == "python find" ]]; then
    exit "${{PYTHON_FIND_EXIT:-0}}"
fi
if [[ "${{1-}}" == "run" ]]; then
    printf 'uv run stderr\n' >&2
    exit "${{UV_RUN_EXIT:-0}}"
fi
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _make_path(tmp_path: pathlib.Path, *, with_uv: bool) -> pathlib.Path:
    """ランチャーが必要とするコマンドだけを持つPATHを作成する。"""
    path_dir = tmp_path / "path"
    path_dir.mkdir()
    for command in ("bash", "sh", "dirname", "readlink", "timeout", "sleep"):
        command_path = shutil.which(command)
        assert command_path is not None
        (path_dir / command).symlink_to(command_path)
    if with_uv:
        _write_fake_uv(path_dir / "uv", "path")
    return path_dir


@pytest.fixture(name="real_uv")
def _real_uv(host_environ: Callable[[], dict[str, str]]) -> str:
    """隔離前のPATHからmise shimを除いて実uvを返す。"""
    environment = host_environ()
    native_path = os.pathsep.join(part for part in environment["PATH"].split(os.pathsep) if pathlib.Path(part).name != "shims")
    uv = shutil.which("uv", path=native_path)
    assert uv is not None
    return uv


def _run_launcher(
    tmp_path: pathlib.Path,
    *,
    native_uv: bool,
    path_uv: bool,
    arguments: list[str] | None = None,
    self_update_exit: int = 0,
    run_exit: int = 0,
    process_loop: bool = False,
    self_update_message: str = "",
    installer_exit: int | None = None,
    python_upgrade_exit: int = 0,
    python_find_exit: int = 0,
    timeout_available: bool = True,
    stall_upgrade: bool = False,
    stall_stage: str = "",
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    """隔離したHOMEとPATHでLinuxランチャーを実行する。"""
    home = tmp_path / "home"
    home.mkdir()
    if native_uv:
        _write_fake_uv(home / ".local" / "bin" / "uv", "native")
        post_apply = home / ".local/bin/dotfiles-post-apply"
        post_apply.write_text('#!/usr/bin/env bash\nprintf "%s\\0" "$@" >> "$POST_APPLY_CALL_LOG"\n', encoding="utf-8")
        post_apply.chmod(0o755)
    path_dir = _make_path(tmp_path, with_uv=path_uv)
    if not timeout_available:
        (path_dir / "timeout").unlink()
    if stall_upgrade or stall_stage:
        timeout = shutil.which("timeout")
        assert timeout is not None
        (path_dir / "timeout").unlink()
        (path_dir / "timeout").write_text(
            '#!/usr/bin/env bash\n[[ "$1" == --kill-after=10 ]] || exit 99\nshift 2\n'
            f'exec "{timeout}" --kill-after=.1 .3 "$@"\n',
            encoding="utf-8",
        )
        (path_dir / "timeout").chmod(0o755)
    if installer_exit is not None:
        installer = path_dir / "curl"
        installer.write_text(
            "#!/usr/bin/env bash\n"
            'if [[ "${STALL_STAGE:-}" == "uvの導入" ]]; then sleep 60; fi\n'
            '[[ "${*: -1}" == https://astral.sh/uv/install.sh ]] || exit 33\n'
            f"[[ {installer_exit} == 0 ]] || exit {installer_exit}\n"
            "cat <<'INSTALLER'\n"
            'test -z "${UV_UNMANAGED_INSTALL-}" || exit 34\n'
            'printf \'\\036installer\\000%s\\000%s\\000\' "$UV_INSTALL_DIR" "$UV_NO_MODIFY_PATH" >> "$UV_CALL_LOG"\n'
            "INSTALLER\n",
            encoding="utf-8",
        )
        installer.chmod(0o755)
        cat = shutil.which("cat")
        assert cat is not None
        (path_dir / "cat").symlink_to(cat)
    call_log = tmp_path / "uv-calls"
    environment = os.environ.copy()
    environment.pop("CODEX_THREAD_ID", None)
    environment.update(
        {
            "HOME": str(home),
            "PATH": str(path_dir),
            "UV_CALL_LOG": str(call_log),
            "UV_SELF_UPDATE_EXIT": str(self_update_exit),
            "UV_SELF_UPDATE_MESSAGE": self_update_message,
            "UV_RUN_EXIT": str(run_exit),
            "PYTHON_UPGRADE_EXIT": str(python_upgrade_exit),
            "PYTHON_FIND_EXIT": str(python_find_exit),
            "STALL_UPGRADE": "1" if stall_upgrade else "0",
            "STALL_STAGE": stall_stage,
            "POST_APPLY_CALL_LOG": str(tmp_path / "post-apply-calls"),
        }
    )
    environment.pop("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", None)
    if process_loop:
        environment["AGENT_TOOLKIT_PROCESS_LOOP_SESSION"] = "1"
    bash = shutil.which("bash")
    assert bash is not None
    result = subprocess.run(  # noqa: S603
        [bash, str(_LAUNCHER), *(arguments or [])],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if not call_log.exists():
        return result, []
    calls = [
        [argument.decode("utf-8") for argument in call.split(b"\0") if argument]
        for call in call_log.read_bytes().split(b"\x1e")
        if call
    ]
    return result, calls


@pytest.mark.parametrize("installer_exit", [0, 22])
@_LINUX_ONLY
def test_receipt_mismatch_reinstalls_at_native_location_and_preserves_run_exit(
    tmp_path: pathlib.Path, installer_exit: int
) -> None:
    """receipt不一致だけを公式再導入へ送り、成功・取得失敗の双方で本処理の終了値を保つ。"""
    message = "The current executable is at native but the standalone installer was used to install uv to old"
    result, calls = _run_launcher(
        tmp_path,
        native_uv=True,
        path_uv=True,
        self_update_exit=1,
        run_exit=23,
        self_update_message=message,
        installer_exit=installer_exit,
    )
    assert result.returncode == 23
    assert calls[0] == ["native", "self", "update"]
    assert calls[-1][0:2] == ["native", "run"]
    repaired = [row for row in calls if row[0] == "installer"]
    assert repaired == ([] if installer_exit else [["installer", str(tmp_path / "home/.local/bin"), "1"]])
    assert (_UPDATE_WARNING in result.stderr) is bool(installer_exit)
    assert (message in result.stderr) is bool(installer_exit)


@_LINUX_ONLY
def test_native_uv_updates_before_run_and_preserves_arguments(tmp_path: pathlib.Path) -> None:
    """公式パスをPATHより優先し、更新後のrunへ全引数を渡す。"""
    arguments = ["--force", "value with spaces"]

    result, calls = _run_launcher(tmp_path, native_uv=True, path_uv=True, arguments=arguments)

    assert result.returncode == 0
    assert calls == [
        ["native", "self", "update"],
        ["native", "python", "install", "--upgrade", "3.15"],
        ["native", "python", "find", "3.15"],
        [
            "native",
            "run",
            "--no-project",
            "--script",
            str(_ROOT / "libexec" / "update_dotfiles.py"),
            *arguments,
        ],
    ]
    assert (tmp_path / "post-apply-calls").read_bytes().split(b"\0") == [
        b"--prune-only-if-minimal",
        *[value.encode() for value in arguments],
        b"",
    ]


@pytest.mark.parametrize("run_exit", [0, 23])
@_LINUX_ONLY
def test_native_uv_update_failure_runs_and_preserves_run_exit(tmp_path: pathlib.Path, run_exit: int) -> None:
    """自己更新が失敗してもrunを実行し、その終了コードと最終案内を保持する。"""
    result, calls = _run_launcher(
        tmp_path,
        native_uv=True,
        path_uv=True,
        self_update_exit=9,
        run_exit=run_exit,
    )

    assert result.returncode == run_exit
    assert calls == [
        ["native", "self", "update"],
        ["native", "python", "install", "--upgrade", "3.15"],
        ["native", "python", "find", "3.15"],
        [
            "native",
            "run",
            "--no-project",
            "--script",
            str(_ROOT / "libexec" / "update_dotfiles.py"),
        ],
    ]
    assert result.stderr.splitlines() == [_UPDATE_WARNING, "uv run stderr"]


@_LINUX_ONLY
def test_python_upgrade_failure_continues_with_existing_python(tmp_path: pathlib.Path) -> None:
    """更新本体まで進み、本体の代用品が返す集約済みの1を伝播する。"""
    result, calls = _run_launcher(tmp_path, native_uv=True, path_uv=False, python_upgrade_exit=17, run_exit=1)
    assert result.returncode == 1, result.stderr
    assert any(call[1:3] == ["python", "find"] for call in calls)
    assert any(call[1] == "run" for call in calls)
    assert not (tmp_path / "post-apply-calls").exists()


@pytest.mark.parametrize("run_exit", [0, 23])
@_LINUX_ONLY
def test_process_loop_skips_self_update_and_preserves_run_exit(tmp_path: pathlib.Path, run_exit: int) -> None:
    """process-loop起動ではuvとPythonの更新を省き、runの終了コードを保持する。"""
    result, calls = _run_launcher(
        tmp_path,
        native_uv=True,
        path_uv=True,
        self_update_exit=9,
        run_exit=run_exit,
        process_loop=True,
    )

    assert result.returncode == run_exit
    assert calls == [
        [
            "native",
            "run",
            "--no-project",
            "--script",
            str(_ROOT / "libexec" / "update_dotfiles.py"),
        ]
    ]
    assert result.stderr == "uv run stderr\n"
    assert (tmp_path / "post-apply-calls").exists() is (run_exit == 0)


@pytest.mark.parametrize("arguments", [["logs"], ["--help"], ["-h", "--prune"]])
@pytest.mark.parametrize("run_exit", [0, 23])
@_LINUX_ONLY
def test_logs_launcher_skips_self_update(tmp_path: pathlib.Path, run_exit: int, arguments: list[str]) -> None:
    """logsとhelpはuv・Pythonの更新と整理を省き、引数と終了コードを共通実装へ渡す。"""
    result, calls = _run_launcher(
        tmp_path, native_uv=True, path_uv=True, arguments=arguments, self_update_exit=9, run_exit=run_exit
    )

    assert result.returncode == run_exit
    assert calls == [["native", "run", "--no-project", "--script", str(_ROOT / "libexec" / "update_dotfiles.py"), *arguments]]
    assert result.stderr == "uv run stderr\n"
    assert not (tmp_path / "post-apply-calls").exists()


@pytest.mark.usefixtures("share_package_caches")
def test_logs_public_launcher_reads_saved_log_without_writing_state(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, real_uv: str
) -> None:
    """両OSの公開ランチャーから実Pythonへ到達し、保存ログだけを読む。"""
    home = tmp_path / "home"
    native_uv = home / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv")
    native_uv.parent.mkdir(parents=True)
    shutil.copy2(real_uv, native_uv)
    for variable in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(variable, str(home))
    for variable in (
        "XDG_STATE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "LOCALAPPDATA",
        "APPDATA",
        "PROGRAMDATA",
    ):
        monkeypatch.setenv(variable, str(tmp_path / variable))
    # WindowsのKnownFolder APIも子へ継承する公開overrideで隔離する。
    monkeypatch.setenv("WIN_PD_OVERRIDE_LOCAL_APPDATA", os.environ["LOCALAPPDATA"])
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    monkeypatch.delenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", raising=False)
    state_dir = pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False))
    assert state_dir.is_relative_to(tmp_path)
    state_dir.mkdir(parents=True)
    expected = "2026-10-01 12:00:00,000 run=100-2 INFO 保存済みの更新\n"
    (state_dir / "update-dotfiles.log").write_text(expected, encoding="utf-8")
    (state_dir / "sync-report.json").write_text('{"status": "failed"}', encoding="utf-8")
    before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in state_dir.iterdir()}
    command = ["cmd.exe", "/d", "/c", str(_WINDOWS_LAUNCHER), "logs"] if os.name == "nt" else [str(_LAUNCHER), "logs"]

    result = subprocess.run(command, check=False, capture_output=True, encoding="utf-8", timeout=90)

    assert result.returncode == 0, result.stderr
    assert result.stdout == expected
    assert {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in state_dir.iterdir()} == before


@pytest.mark.parametrize("run_exit", [0, 23])
@pytest.mark.parametrize(
    "version",
    [
        "current",
        pytest.param("legacy", marks=pytest.mark.skipif(os.name != "nt", reason="旧batchの読取位置はcmd.exeで検証する")),
        pytest.param("logs", marks=pytest.mark.skipif(os.name != "nt", reason="旧batchの読取位置はcmd.exeで検証する")),
    ],
)
def test_launcher_replacement_preserves_update_exit(
    tmp_path: pathlib.Path, version: str, run_exit: int, real_uv: str, host_environ: Callable[[], dict[str, str]]
) -> None:
    """更新中の自身の書換え後も、新旧ランチャーが更新処理の終了コードを返す。"""
    environment = host_environ()
    environment.pop("CODEX_THREAD_ID", None)
    home = tmp_path / "home"
    native_uv = home / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv")
    native_uv.parent.mkdir(parents=True)
    shutil.copy2(real_uv, native_uv)
    launcher = tmp_path / "bin" / ("update-dotfiles.cmd" if os.name == "nt" else "update-dotfiles")
    launcher.parent.mkdir()
    current = (_WINDOWS_LAUNCHER if os.name == "nt" else _LAUNCHER).read_bytes()
    if os.name == "nt":
        endpoint = b'"!USERPROFILE!\\.local\\bin\\dotfiles-post-apply.exe"'
        assert current.count(endpoint) == 1
        current = current.replace(endpoint, b'"!PRUNE_PROBE_PYTHON!" "!PRUNE_PROBE_SCRIPT!"')
    legacy = (_LEGACY_WINDOWS_PREFIX + "exit /b %ERRORLEVEL%\n").replace("\n", "\r\n").encode("cp932")
    logs = (_LOGS_WINDOWS_PREFIX + "exit /b %ERRORLEVEL%\n").replace("\n", "\r\n").encode("cp932")
    launcher.write_bytes({"current": current, "legacy": legacy, "logs": logs}[version])
    launcher.chmod(0o755)
    replacement = current if version != "current" else b"@echo off\r\nexit /b 99\r\n"
    libexec = tmp_path / "libexec"
    libexec.mkdir()
    prune_probe = libexec / "prune.py"
    prune_probe.write_text("raise SystemExit(0)\n", encoding="utf-8")
    if os.name != "nt":
        endpoint = home / ".local/bin/dotfiles-post-apply"
        endpoint.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        endpoint.chmod(0o755)
    calls = tmp_path / "update-calls"
    (libexec / "update_dotfiles.py").write_text(
        "import pathlib\n"
        f"with pathlib.Path({str(calls)!r}).open('a', encoding='utf-8') as stream:\n"
        "    stream.write('update\\n')\n"
        f"pathlib.Path({str(launcher)!r}).write_bytes({replacement!r})\nraise SystemExit({run_exit})\n",
        encoding="utf-8",
    )
    environment.update(
        {"HOME": str(home), "USERPROFILE": str(home), "UV_PYTHON": sys.executable, "AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1"}
    )
    environment.update(PRUNE_PROBE_PYTHON=sys.executable, PRUNE_PROBE_SCRIPT=str(prune_probe))
    command = ["cmd.exe", "/d", "/c", str(launcher)] if os.name == "nt" else [str(launcher)]

    result = subprocess.run(command, env=environment, capture_output=True, encoding="utf-8", timeout=45, check=False)

    assert launcher.read_bytes() == replacement
    assert result.returncode == run_exit, result.stdout + result.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == ["update"]


@_LINUX_ONLY
def test_path_uv_is_not_used_when_native_uv_is_absent(tmp_path: pathlib.Path) -> None:
    """公式パスが存在しない場合はPATH上のuvを選ばず終了する。"""
    result, calls = _run_launcher(tmp_path, native_uv=False, path_uv=True)

    assert result.returncode == 127
    assert not calls
    assert "https://docs.astral.sh/uv/getting-started/installation/" in result.stderr
    assert "update-dotfilesを再実行してください" in result.stderr


@_LINUX_ONLY
def test_missing_uv_returns_127(tmp_path: pathlib.Path) -> None:
    """公式パスとPATHの双方にuvが無い場合は127を返す。"""
    result, calls = _run_launcher(tmp_path, native_uv=False, path_uv=False)

    assert result.returncode == 127
    assert not calls
    assert "https://docs.astral.sh/uv/getting-started/installation/" in result.stderr
    assert "update-dotfilesを再実行してください" in result.stderr


def _windows_warning_script(content: str) -> str:
    """自己更新失敗時の警告を出力するPowerShell本文を一意に取り出す。"""
    (warning_line,) = [
        line
        for line in content.splitlines()
        if line.startswith('if "%UV_SELF_UPDATE_FAILED%"=="1" powershell.exe ') and '-Command "' in line
    ]
    script = warning_line.split('-Command "', maxsplit=1)[1]
    assert script.endswith('"')
    return script.removesuffix('"')


# test-copy-contract: Windowsの配布形式と旧batchが再開する終了処理の構造を検査する。
def test_windows_launcher_preserves_encoding_and_uv_contract() -> None:
    """WindowsはCP932・CRLFと解析済み終了処理を保ち、起動前の更新を補助へ委ねる。"""
    raw = _WINDOWS_LAUNCHER.read_bytes()
    assert b"\n" not in raw.replace(b"\r\n", b"")
    content = raw.decode("cp932")
    assert 'set "UV=%USERPROFILE%\\.local\\bin\\uv.exe"' in content
    assert "update_bootstrap.ps1" in content
    assert "python install --upgrade" not in content
    assert 'call set "UPDATE_DOTFILES_EXIT=%%ERRORLEVEL%%"' in content
    assert "exit /b !UPDATE_DOTFILES_EXIT!" in content
    assert "[Console]::IsErrorRedirected" in _windows_warning_script(content)


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="Windows PowerShellが必要")
def test_windows_warning_redirects_as_utf8_without_bom() -> None:
    """Windows警告のリダイレクト出力がBOMなしUTF-8になる。"""
    content = _WINDOWS_LAUNCHER.read_bytes().decode("cp932")
    script = _windows_warning_script(content)
    environment = os.environ.copy()
    environment["UV_SELF_UPDATE_WARNING"] = _UPDATE_WARNING

    result = subprocess.run(  # noqa: S603
        ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", script],
        env=environment,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert not result.stdout
    assert result.stderr == f"{_UPDATE_WARNING}\r\n".encode()


@pytest.mark.parametrize("run_exit,prune_exit", [(0, 0), (23, 0), (0, 9)])
@_LINUX_ONLY
def test_prune_invokes_post_apply_after_successful_update(tmp_path: pathlib.Path, run_exit: int, prune_exit: int) -> None:
    """--pruneはuv更新の成功後にだけ独立したpost-apply CLIへ渡す。"""
    home = tmp_path / "home"
    _write_fake_uv(home / ".local" / "bin" / "uv", "native")
    post_apply = home / ".local" / "bin" / "dotfiles-post-apply"
    post_apply.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$PRUNE_CALL_LOG"\n' + f"exit {prune_exit}\n", encoding="utf-8"
    )
    post_apply.chmod(0o755)
    path_dir = _make_path(tmp_path, with_uv=False)
    environment = {
        **os.environ,
        "HOME": str(home),
        "PATH": str(path_dir),
        "UV_CALL_LOG": str(tmp_path / "uv-calls"),
        "PRUNE_CALL_LOG": str(tmp_path / "prune-calls"),
        "AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1",
        "UV_RUN_EXIT": str(run_exit),
    }
    environment.pop("CODEX_THREAD_ID", None)
    bash = shutil.which("bash")
    assert bash is not None

    result = subprocess.run(
        [bash, str(_LAUNCHER), "--prune", "--prune"], env=environment, capture_output=True, text=True, check=False, timeout=10
    )

    assert result.returncode == (run_exit or prune_exit), result.stderr
    if run_exit:
        assert not (tmp_path / "prune-calls").exists()
    else:
        assert (tmp_path / "prune-calls").read_text(encoding="utf-8") == "--prune-only-if-minimal --prune --prune\n"


@pytest.mark.parametrize(
    "run_exit,prune_exit,arguments",
    [
        (0, 0, ["--prune", "--prune"]),
        (23, 0, ["--prune"]),
        (0, 9, ["--prune"]),
        (0, 0, []),
        (0, 0, ["--mode", "full"]),
        (0, 0, ["--mode", "minimal"]),
        (0, 0, ["--help", "--prune"]),
        (0, 0, ["--prune", "-h"]),
    ],
)
def test_public_launcher_prune_boundary_with_real_uv(
    tmp_path: pathlib.Path,
    real_uv: str,
    host_environ: Callable[[], dict[str, str]],
    run_exit: int,
    prune_exit: int,
    arguments: list[str],
) -> None:
    """公開入口の制御を実uvで動かし、終了後のprune回数と失敗伝播を検証する。

    削除先CLIだけを無害なPythonへ束縛する。POSIXではuvの共有cache lockも排他取得で確認する。
    Windowsのcmd.exe制御はWindows CIで同じ試験を実行する。
    """
    home = tmp_path / "home with spaces !literal!"
    native_uv = home / ".local/bin" / ("uv.exe" if os.name == "nt" else "uv")
    native_uv.parent.mkdir(parents=True)
    shutil.copy2(real_uv, native_uv)
    libexec = tmp_path / "libexec"
    libexec.mkdir()
    calls = tmp_path / "calls"
    (libexec / "update_dotfiles.py").write_text(
        "import argparse, os, pathlib\n"
        "assert os.environ.get('DOTFILES_PRUNE_OUTSIDE_UV') == '1'\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--prune', action='store_true')\n"
        "parser.add_argument('--mode', choices=['minimal', 'full'])\n"
        "parser.parse_args()\n"
        f"pathlib.Path({str(calls)!r}).write_text('update\\n', encoding='utf-8')\n"
        f"raise SystemExit({run_exit})\n",
        encoding="utf-8",
    )
    probe = tmp_path / "prune.py"
    probe.write_text(
        "import os, pathlib, sys\n"
        f"assert sys.argv[1:] == {['--prune-only-if-minimal', *arguments]!r}\n"
        f"with pathlib.Path({str(calls)!r}).open('a', encoding='utf-8') as stream:\n"
        "    stream.write('post-apply\\n')\n"
        "if '--prune' not in sys.argv and 'minimal' not in sys.argv:\n"
        "    raise SystemExit(0)\n"
        "if os.name != 'nt':\n"
        "    import fcntl\n"
        "    with (pathlib.Path(os.environ['UV_CACHE_DIR']) / '.lock').open('a+b') as lock:\n"
        "        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        f"with pathlib.Path({str(calls)!r}).open('a', encoding='utf-8') as stream:\n"
        "    stream.write('prune\\n')\n"
        f"raise SystemExit({prune_exit})\n",
        encoding="utf-8",
    )
    launcher = tmp_path / "bin" / ("update-dotfiles.cmd" if os.name == "nt" else "update-dotfiles")
    launcher.parent.mkdir()
    if os.name == "nt":
        source = _WINDOWS_LAUNCHER.read_bytes().decode("cp932")
        endpoint = '"!USERPROFILE!\\.local\\bin\\dotfiles-post-apply.exe"'
        assert source.count(endpoint) == 1
        source = source.replace(endpoint, '"!PRUNE_PROBE_PYTHON!" "!PRUNE_PROBE_SCRIPT!"')
        launcher.write_bytes(source.encode("cp932"))
        command = ["cmd.exe", "/d", "/c", str(launcher)]
    else:
        shutil.copy2(_LAUNCHER, launcher)
        endpoint_script = home / ".local/bin/dotfiles-post-apply"
        endpoint_script.write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" "{probe}" "$@"\n', encoding="utf-8")
        endpoint_script.chmod(0o755)
        command = [str(launcher)]
    environment = host_environ()
    environment.pop("CODEX_THREAD_ID", None)
    environment.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "UV_PYTHON": sys.executable,
            "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
            "AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1",
            "PRUNE_PROBE_PYTHON": sys.executable,
            "PRUNE_PROBE_SCRIPT": str(probe),
        }
    )
    command.extend(arguments)

    result = subprocess.run(command, env=environment, capture_output=True, encoding="utf-8", timeout=45, check=False)

    help_requested = "--help" in arguments or "-h" in arguments
    prune = ("--prune" in arguments or "minimal" in arguments) and not help_requested
    assert result.returncode == (run_exit or (prune_exit if prune else 0)), result.stdout + result.stderr
    observed = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    expected = [] if help_requested else ["update"]
    if not help_requested and not run_exit:
        expected.append("post-apply")
        if prune:
            expected.append("prune")
    assert observed == expected


@pytest.mark.parametrize("run_exit", [0, 23])
def test_public_codex_launcher_uses_native_worker_and_saved_job(
    tmp_path: pathlib.Path,
    real_uv: str,
    host_environ: Callable[[], dict[str, str]],
    run_exit: int,
) -> None:
    """両OSの公開ランチャーからnative workerへ接続し、同一jobの結果だけを再取得する。"""
    environment = host_environ()
    home = tmp_path / "home with spaces"
    native = home / ".local/bin" / ("uv.exe" if os.name == "nt" else "uv")
    native.parent.mkdir(parents=True)
    shutil.copy2(real_uv, native)
    libexec = tmp_path / "libexec"
    libexec.mkdir()
    job_source = (_ROOT / "libexec/update_dotfiles_job.py").read_text(encoding="utf-8")
    endpoint = 'str(home_bin / ("dotfiles-post-apply.exe" if sys.platform == "win32" else "dotfiles-post-apply"))'
    assert job_source.count(endpoint) == 1
    job_source = job_source.replace(endpoint, 'sys.executable, str(_ROOT / "libexec/prune.py")')
    (libexec / "update_dotfiles_job.py").write_text(job_source, encoding="utf-8")
    for name in ("update_bootstrap.ps1", "locking_process.ps1"):
        shutil.copy2(_ROOT / "libexec" / name, libexec / name)
    prune_calls = tmp_path / "prune-calls"
    (libexec / "prune.py").write_text(
        "import pathlib, sys\n"
        "assert sys.argv[1:] == ['--prune-only-if-minimal']\n"
        f"with pathlib.Path({str(prune_calls)!r}).open('a', encoding='utf-8') as output:\n"
        "    output.write('post-apply\\n')\n",
        encoding="utf-8",
    )
    counter = tmp_path / "updates"
    (libexec / "update_dotfiles.py").write_text(
        "import pathlib\n"
        f"with pathlib.Path({str(counter)!r}).open('a', encoding='utf-8') as output:\n"
        "    output.write('update\\n')\n"
        "print('worker update', flush=True)\n"
        f"raise SystemExit({run_exit})\n",
        encoding="utf-8",
    )
    launcher = tmp_path / "bin" / ("update-dotfiles.cmd" if os.name == "nt" else "update-dotfiles")
    launcher.parent.mkdir()
    shutil.copy2(_WINDOWS_LAUNCHER if os.name == "nt" else _LAUNCHER, launcher)
    environment.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "CODEX_THREAD_ID": "public-job-thread",
            "AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1",
            "UV_PYTHON": sys.executable,
            "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        }
    )
    for name in (
        "XDG_STATE_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_CONFIG_HOME",
        "LOCALAPPDATA",
        "APPDATA",
        "PROGRAMDATA",
    ):
        environment[name] = str(tmp_path / name)
    environment["WIN_PD_OVERRIDE_LOCAL_APPDATA"] = environment["LOCALAPPDATA"]
    command = ["cmd.exe", "/d", "/c", str(launcher)] if os.name == "nt" else [str(launcher)]
    result = subprocess.run(command, env=environment, capture_output=True, encoding="utf-8", check=False, timeout=90)
    assert result.returncode == run_exit, result.stdout + result.stderr
    assert "worker update" in result.stdout
    job_id = next(line.partition(": ")[2] for line in result.stdout.splitlines() if line.startswith("更新job: "))
    for arguments in (["job", job_id], ["job"]):
        retrieved = subprocess.run(
            [*command, *arguments], env=environment, capture_output=True, encoding="utf-8", check=False, timeout=45
        )
        assert retrieved.returncode == run_exit, retrieved.stdout + retrieved.stderr
        assert f"job {job_id}: 終了 exit={run_exit}" in retrieved.stdout
    assert counter.read_text(encoding="utf-8").splitlines() == ["update"]
    assert (prune_calls.read_text(encoding="utf-8").splitlines() if prune_calls.exists() else []) == (
        [] if run_exit else ["post-apply"]
    )


@_LINUX_ONLY
def test_missing_python_aborts_after_install_recovery(tmp_path: pathlib.Path) -> None:
    """既存3.15が無く、再試行しても導入できない場合は本体へ進まない。"""
    result, calls = _run_launcher(tmp_path, native_uv=True, path_uv=False, python_upgrade_exit=17, python_find_exit=2)
    assert result.returncode == 17
    assert calls.count(["native", "python", "install", "3.15"]) == 2
    assert not any(row[1] == "run" for row in calls)


@_LINUX_ONLY
def test_missing_timeout_never_starts_unbounded_update(tmp_path: pathlib.Path) -> None:
    """時間上限を保証できなければ、外部の更新操作を始めない。"""
    result, calls = _run_launcher(tmp_path, native_uv=True, path_uv=False, timeout_available=False)
    assert result.returncode == 127
    assert not calls
    assert "coreutils" in result.stderr


@_LINUX_ONLY
def test_bootstrap_timeout_continues_with_existing_python(tmp_path: pathlib.Path) -> None:
    """管理Python更新の無期限停止を時間切れに変換し、既存Pythonで本体へ進む。"""
    result, calls = _run_launcher(tmp_path, native_uv=True, path_uv=False, stall_upgrade=True)
    assert result.returncode == 0, result.stderr
    assert "exit=124" in result.stderr
    assert any(row[1] == "run" for row in calls)


@pytest.mark.parametrize("stage", ["uvの導入", "uvの自己更新", "管理Pythonの更新", "管理Pythonの導入"])
@pytest.mark.parametrize("failure_code", [17, 124])
@_LINUX_ONLY
def test_bootstrap_native_failures_from_public_launcher(tmp_path: pathlib.Path, stage: str, failure_code: int) -> None:
    """Python起動前の4段でもnative失敗/timeout結果を分類し、続行段は本体へ進む。"""
    result, calls = _run_launcher(
        tmp_path,
        native_uv=stage != "uvの導入",
        path_uv=False,
        run_exit=1,
        self_update_exit=failure_code if stage == "uvの自己更新" else 0,
        python_upgrade_exit=failure_code if stage in {"管理Pythonの更新", "管理Pythonの導入"} else 0,
        python_find_exit=2 if stage == "管理Pythonの導入" else 0,
        installer_exit=failure_code if stage == "uvの導入" else None,
        stall_stage=stage if failure_code == 124 else "",
    )
    assert stage in result.stderr
    if stage in {"uvの自己更新", "管理Pythonの更新"}:
        assert any(call[1:2] == ["run"] for call in calls)
        assert result.returncode == 1
    else:
        assert not any(call[1:2] == ["run"] for call in calls)
        if stage == "管理Pythonの導入":
            assert sum(call[1:] == ["python", "install", "3.15"] for call in calls) == 2
        assert result.returncode != 0
