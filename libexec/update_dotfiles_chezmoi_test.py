# pylint: disable=protected-access

import pathlib
import subprocess
import sys
import typing

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import update_dotfiles  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_process  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_runtime  # noqa: E402  # pylint: disable=wrong-import-position


def _executable(directory: pathlib.Path, name: str) -> pathlib.Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture(name="windows_path")
def _windows_path(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    monkeypatch.setattr(update_dotfiles_runtime, "_WINDOWS", True)
    monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", tmp_path / "dotfiles")
    shims = tmp_path / "shims"
    _executable(shims, "chezmoi.cmd")
    _executable(tmp_path / "mise-bin", "mise.exe")
    monkeypatch.setenv("PATH", f"{shims}{update_dotfiles_runtime.os.pathsep}{tmp_path / 'mise-bin'}")
    monkeypatch.setattr(update_dotfiles_runtime.pathlib.Path, "home", lambda: tmp_path / "home")
    return tmp_path


def _fake_mise(
    monkeypatch: pytest.MonkeyPatch, *, stdout: str, returncode: int = 0
) -> list[tuple[list[str], dict[str, typing.Any]]]:
    calls: list[tuple[list[str], dict[str, typing.Any]]] = []

    def run(argv: list[str], **kwargs: typing.Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(update_dotfiles_runtime.process_tree, "run_with_timeout", run)
    return calls


def test_posix_keeps_bare_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_dotfiles_runtime, "_WINDOWS", False)
    assert update_dotfiles_runtime.chezmoi_executable() == "chezmoi"


def test_windows_prefers_chezmoi_exe_on_path(monkeypatch: pytest.MonkeyPatch, windows_path: pathlib.Path) -> None:
    exe = _executable(windows_path / "chezmoi-bin", "chezmoi.exe")
    monkeypatch.setenv("PATH", f"{update_dotfiles_runtime.os.environ['PATH']}{update_dotfiles_runtime.os.pathsep}{exe.parent}")
    calls = _fake_mise(monkeypatch, stdout="")
    assert update_dotfiles_runtime.chezmoi_executable() == str(exe)
    assert not calls


def test_windows_resolves_mise_shim_to_installed_exe(monkeypatch: pytest.MonkeyPatch, windows_path: pathlib.Path) -> None:
    exe = _executable(windows_path / "installs" / "chezmoi" / "2.70.0", "chezmoi.exe")
    calls = _fake_mise(monkeypatch, stdout=f"{exe}\r\n")
    assert update_dotfiles_runtime.chezmoi_executable() == str(exe)
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == [str(windows_path / "mise-bin" / "mise.exe"), "which", "chezmoi"]
    assert kwargs["cwd"] == update_dotfiles_runtime.DOTFILES_ROOT
    assert kwargs["env"]["MISE_AUTO_INSTALL"] == "0"


@pytest.mark.parametrize(
    ("stdout", "returncode", "expected"),
    [
        ("", 1, "実体を返しません"),
        ("missing/chezmoi.exe", 0, "実在するexeの絶対パスではありません"),
        ("SHIM", 0, "実在するexeの絶対パスではありません"),
    ],
)
def test_windows_rejects_unusable_mise_result(
    monkeypatch: pytest.MonkeyPatch, windows_path: pathlib.Path, stdout: str, returncode: int, expected: str
) -> None:
    if stdout == "SHIM":
        stdout = str(windows_path / "shims" / "chezmoi.cmd")
    _fake_mise(monkeypatch, stdout=stdout, returncode=returncode)
    with pytest.raises(update_dotfiles_runtime.ChezmoiResolveError, match=expected):
        update_dotfiles_runtime.chezmoi_executable()


def test_windows_without_mise_reports_resolution_failure(monkeypatch: pytest.MonkeyPatch, windows_path: pathlib.Path) -> None:
    monkeypatch.setenv("PATH", str(windows_path / "shims"))
    with pytest.raises(update_dotfiles_runtime.ChezmoiResolveError, match="mise.exeも見つかりません"):
        update_dotfiles_runtime.chezmoi_executable()


def _resolution_fails() -> str:
    raise update_dotfiles_runtime.ChezmoiResolveError("解決できない理由")


def test_git_pull_reports_resolution_failure_without_launching(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(update_dotfiles_runtime, "chezmoi_executable", _resolution_fails)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail(f"Popen: {args}"))
    assert update_dotfiles_process._run_git_pull_once(1, 7, timeout=None, plugin=None) == 1
    err = capsys.readouterr().err
    assert "chezmoiの実体を解決できませんでした: 解決できない理由" in err
    assert "--version" not in err


def test_plugin_git_launch_failure_names_git(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path
) -> None:
    monkeypatch.setattr(update_dotfiles_runtime, "chezmoi_executable", lambda: pytest.fail("chezmoiを解決した"))

    def popen(*_args: typing.Any, **_kwargs: typing.Any) -> typing.NoReturn:
        raise FileNotFoundError(2, "見つかりません")

    monkeypatch.setattr(subprocess, "Popen", popen)
    assert update_dotfiles_process._run_git_pull_once(1, 7, timeout=None, plugin=tmp_path / "plugin") == 1
    err = capsys.readouterr().err
    assert "起動した実行ファイル: git" in err
    assert "chezmoi" not in err


def test_run_step_launches_resolved_chezmoi(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[list[str]] = []
    monkeypatch.setattr(update_dotfiles_runtime, "chezmoi_executable", lambda: r"C:\mise\installs\chezmoi\chezmoi.exe")

    def run_with_timeout(argv: list[str], **_kwargs: typing.Any) -> subprocess.CompletedProcess[str]:
        launched.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(update_dotfiles.process_tree, "run_with_timeout", run_with_timeout)
    assert update_dotfiles._run_step(7, 7, "chezmoi apply", ["chezmoi", "apply", "--force"])[0] == 0
    assert launched == [[r"C:\mise\installs\chezmoi\chezmoi.exe", "apply", "--force"]]


def test_run_step_reports_resolution_failure_without_launching(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(update_dotfiles_runtime, "chezmoi_executable", _resolution_fails)
    monkeypatch.setattr(update_dotfiles.process_tree, "run_with_timeout", lambda *args, **kwargs: pytest.fail(f"起動: {args}"))
    assert update_dotfiles._run_step(2, 7, "chezmoi init", ["chezmoi", "init"]) == (1, "")
    assert "chezmoiの実体を解決できませんでした: 解決できない理由" in capsys.readouterr().err
