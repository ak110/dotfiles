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
if [[ "${{1-}}" == "self" && "${{2-}}" == "update" ]]; then
    [[ -z "${{UV_SELF_UPDATE_MESSAGE-}}" ]] || printf '%s\n' "$UV_SELF_UPDATE_MESSAGE" >&2
    exit "${{UV_SELF_UPDATE_EXIT:-0}}"
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
    for command in ("bash", "sh", "dirname", "readlink"):
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
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    """隔離したHOMEとPATHでLinuxランチャーを実行する。"""
    home = tmp_path / "home"
    home.mkdir()
    if native_uv:
        _write_fake_uv(home / ".local" / "bin" / "uv", "native")
    path_dir = _make_path(tmp_path, with_uv=path_uv)
    if installer_exit is not None:
        installer = path_dir / "curl"
        installer.write_text(
            "#!/usr/bin/env bash\n"
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
    environment.update(
        {
            "HOME": str(home),
            "PATH": str(path_dir),
            "UV_CALL_LOG": str(call_log),
            "UV_SELF_UPDATE_EXIT": str(self_update_exit),
            "UV_SELF_UPDATE_MESSAGE": self_update_message,
            "UV_RUN_EXIT": str(run_exit),
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
        [
            "native",
            "run",
            "--no-project",
            "--script",
            str(_ROOT / "libexec" / "update_dotfiles.py"),
            *arguments,
        ],
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
        [
            "native",
            "run",
            "--no-project",
            "--script",
            str(_ROOT / "libexec" / "update_dotfiles.py"),
        ],
    ]
    assert result.stderr.splitlines() == ["uv run stderr", _UPDATE_WARNING]


@pytest.mark.parametrize("run_exit", [0, 23])
@_LINUX_ONLY
def test_process_loop_skips_self_update_and_preserves_run_exit(tmp_path: pathlib.Path, run_exit: int) -> None:
    """process-loop起動では自己更新を延期し、runの終了コードを保持する。"""
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


@pytest.mark.parametrize("run_exit", [0, 23])
@_LINUX_ONLY
def test_logs_launcher_skips_self_update(tmp_path: pathlib.Path, run_exit: int) -> None:
    """logsは自己更新を起動せず、引数と表示の終了コードを共通実装へ渡す。"""
    result, calls = _run_launcher(
        tmp_path, native_uv=True, path_uv=True, arguments=["logs"], self_update_exit=9, run_exit=run_exit
    )

    assert result.returncode == run_exit
    assert calls == [["native", "run", "--no-project", "--script", str(_ROOT / "libexec" / "update_dotfiles.py"), "logs"]]
    assert result.stderr == "uv run stderr\n"


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
    home = tmp_path / "home"
    native_uv = home / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv")
    native_uv.parent.mkdir(parents=True)
    shutil.copy2(real_uv, native_uv)
    launcher = tmp_path / "bin" / ("update-dotfiles.cmd" if os.name == "nt" else "update-dotfiles")
    launcher.parent.mkdir()
    current = (_WINDOWS_LAUNCHER if os.name == "nt" else _LAUNCHER).read_bytes()
    legacy = (_LEGACY_WINDOWS_PREFIX + "exit /b %ERRORLEVEL%\n").replace("\n", "\r\n").encode("cp932")
    logs = (_LOGS_WINDOWS_PREFIX + "exit /b %ERRORLEVEL%\n").replace("\n", "\r\n").encode("cp932")
    launcher.write_bytes({"current": current, "legacy": legacy, "logs": logs}[version])
    launcher.chmod(0o755)
    replacement = current if version != "current" else b"@echo off\r\nexit /b 99\r\n"
    libexec = tmp_path / "libexec"
    libexec.mkdir()
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


def test_windows_launcher_preserves_encoding_and_uv_contract() -> None:
    """Windows側の行末、公式uv必須、自己更新、引数伝播を固定する。"""
    raw = _WINDOWS_LAUNCHER.read_bytes()
    assert b"\n" not in raw.replace(b"\r\n", b"")
    content = raw.decode("cp932")

    native = 'set "UV=%USERPROFILE%\\.local\\bin\\uv.exe"'
    missing = 'if not exist "%UV%" goto missing_uv'
    update_state = 'set "UV_SELF_UPDATE_FAILED=0"'
    process_loop_guard = 'if not "%~1"=="logs" if not "%AGENT_TOOLKIT_PROCESS_LOOP_SESSION%"=="1" ('
    update = 'powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%\\libexec\\update_uv.ps1" "%UV%"'
    update_failure = 'if errorlevel 1 set "UV_SELF_UPDATE_FAILED=1"'
    run = '"%UV%" run --no-project --script "%SCRIPT_DIR%\\libexec\\update_dotfiles.py" %*'
    capture_run_exit = 'call set "UPDATE_DOTFILES_EXIT=%%ERRORLEVEL%%"'
    warning_state = f'set "UV_SELF_UPDATE_WARNING={_UPDATE_WARNING}"'
    warning = 'if "%UV_SELF_UPDATE_FAILED%"=="1" powershell.exe -NoLogo -NoProfile -Command '
    return_run_exit = "call exit /b %%UPDATE_DOTFILES_EXIT%%"
    assert native in content
    assert missing in content
    assert update_state in content
    assert process_loop_guard in content
    assert update in content
    assert update_failure in content
    assert 'set "UV=uv"' not in content
    assert run in content
    assert capture_run_exit in content
    assert warning_state in content
    assert warning in content
    assert "[Console]::IsErrorRedirected" in content
    assert "[System.Text.UTF8Encoding]::new($false)" in content
    assert "[Console]::Error.WriteLine($message)" in content
    assert f"echo {_UPDATE_WARNING}" not in content
    assert return_run_exit in content
    assert "self update || exit /b 1" not in content
    assert (
        content.index(native)
        < content.index(missing)
        < content.index(update_state)
        < content.index(process_loop_guard)
        < content.index(update)
        < content.index(update_failure)
        < content.index(run)
        < content.index(capture_run_exit)
        < content.index(warning_state)
        < content.index(warning)
        < content.index(return_run_exit)
    )


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="Windows PowerShellが必要")
def test_windows_warning_redirects_as_utf8_without_bom() -> None:
    """Windows警告のリダイレクト出力がBOMなしUTF-8になる。"""
    content = _WINDOWS_LAUNCHER.read_bytes().decode("cp932")
    warning_line = next(line for line in content.splitlines() if "powershell.exe -NoLogo" in line)
    script = warning_line.split('-Command "', maxsplit=1)[1].removesuffix('"')
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
