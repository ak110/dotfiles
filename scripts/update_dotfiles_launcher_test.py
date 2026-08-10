"""`update-dotfiles`ランチャーのuv解決契約を検証する。"""

import os
import pathlib
import shutil
import subprocess
import sys
import tomllib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_LAUNCHER = _ROOT / "bin" / "update-dotfiles"
_WINDOWS_LAUNCHER = _ROOT / "bin" / "update-dotfiles.cmd"
_LINUX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="BashランチャーはLinuxで検証する")
_UPDATE_WARNING = (
    "uvの自己更新に失敗しました。既存のuvでdotfiles更新を実行し、次回のupdate-dotfiles起動時に自己更新を再試行します。"
)


def _write_fake_uv(path: pathlib.Path, name: str) -> None:
    """呼び出しをNUL区切りで記録するfake uvを作成する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""#!/usr/bin/env bash
printf '\\036{name}\\000' >> "$UV_CALL_LOG"
printf '%s\\000' "$@" >> "$UV_CALL_LOG"
if [[ "${{1-}}" == "self" && "${{2-}}" == "update" ]]; then
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
    for command in ("bash", "dirname", "readlink"):
        command_path = shutil.which(command)
        assert command_path is not None
        (path_dir / command).symlink_to(command_path)
    if with_uv:
        _write_fake_uv(path_dir / "uv", "path")
    return path_dir


def _run_launcher(
    tmp_path: pathlib.Path,
    *,
    native_uv: bool,
    path_uv: bool,
    arguments: list[str] | None = None,
    self_update_exit: int = 0,
    run_exit: int = 0,
    process_loop: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    """隔離したHOMEとPATHでLinuxランチャーを実行する。"""
    home = tmp_path / "home"
    home.mkdir()
    if native_uv:
        _write_fake_uv(home / ".local" / "bin" / "uv", "native")
    path_dir = _make_path(tmp_path, with_uv=path_uv)
    call_log = tmp_path / "uv-calls"
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "PATH": str(path_dir),
            "UV_CALL_LOG": str(call_log),
            "UV_SELF_UPDATE_EXIT": str(self_update_exit),
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
            str(_ROOT / "scripts" / "update_dotfiles.py"),
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
            str(_ROOT / "scripts" / "update_dotfiles.py"),
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
            str(_ROOT / "scripts" / "update_dotfiles.py"),
        ]
    ]
    assert result.stderr == "uv run stderr\n"


@_LINUX_ONLY
def test_path_uv_is_not_used_when_native_uv_is_absent(tmp_path: pathlib.Path) -> None:
    """公式パスが存在しない場合はPATH上のuvを選ばず終了する。"""
    result, calls = _run_launcher(tmp_path, native_uv=False, path_uv=True)

    assert result.returncode == 127
    assert not calls
    assert "公式インストーラーでuvを導入してください" in result.stderr


@_LINUX_ONLY
def test_missing_uv_returns_127(tmp_path: pathlib.Path) -> None:
    """公式パスとPATHの双方にuvが無い場合は127を返す。"""
    result, calls = _run_launcher(tmp_path, native_uv=False, path_uv=False)

    assert result.returncode == 127
    assert not calls
    assert "公式インストーラーでuvを導入してください" in result.stderr


def test_windows_launcher_preserves_encoding_and_uv_contract() -> None:
    """Windows側の行末、公式uv必須、自己更新、引数伝播を固定する。"""
    raw = _WINDOWS_LAUNCHER.read_bytes()
    assert b"\n" not in raw.replace(b"\r\n", b"")
    content = raw.decode("cp932")

    native = 'set "UV=%USERPROFILE%\\.local\\bin\\uv.exe"'
    missing = 'if not exist "%UV%" ('
    update_state = 'set "UV_SELF_UPDATE_FAILED=0"'
    process_loop_guard = 'if not "%AGENT_TOOLKIT_PROCESS_LOOP_SESSION%"=="1" ('
    update = '"%UV%" self update'
    update_failure = 'if errorlevel 1 set "UV_SELF_UPDATE_FAILED=1"'
    run = '"%UV%" run --no-project --script "%SCRIPT_DIR%\\scripts\\update_dotfiles.py" %*'
    capture_run_exit = 'set "UPDATE_DOTFILES_EXIT=%ERRORLEVEL%"'
    warning_state = f'set "UV_SELF_UPDATE_WARNING={_UPDATE_WARNING}"'
    warning = 'if "%UV_SELF_UPDATE_FAILED%"=="1" powershell.exe -NoLogo -NoProfile -Command '
    return_run_exit = "exit /b %UPDATE_DOTFILES_EXIT%"
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


def test_root_mise_files_do_not_manage_uv() -> None:
    """bootstrap基盤のuvがルートmise設定とlockへ再混入しない。"""
    with (_ROOT / "mise.toml").open("rb") as file:
        config = tomllib.load(file)
    with (_ROOT / "mise.lock").open("rb") as file:
        lock = tomllib.load(file)

    assert "uv" not in config["tools"]
    assert "uv" not in lock["tools"]
