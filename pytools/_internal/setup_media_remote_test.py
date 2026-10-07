"""pytools._internal.setup_media_remote のテスト。"""

import pathlib

import pytest

from pytools._internal import setup_media_remote
from pytools._internal._test_helpers import make_branching_fake as _make_branching_fake
from pytools._internal._test_helpers import make_static_fake as _make_static_fake
from pytools._internal._test_helpers import ok_result as _ok


def _expected_vbs(exe: pathlib.Path) -> str:
    """テスト用VBS本文（本体の生成ロジックと一致する形式）。"""
    return f'CreateObject("WScript.Shell").Run """{exe}"" serve", 0, False\n'


_REAL_RESTART = setup_media_remote._restart  # pylint: disable=protected-access


@pytest.fixture(name="windows_stheno")
def _windows_stheno(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    # 配置のテストでは実際のプロセスを停止・起動しない。再起動は専用のテストで確かめる。
    monkeypatch.setattr(setup_media_remote, "_restart", lambda _vbs, _exe: None)
    monkeypatch.setattr(setup_media_remote.pathlib.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.setattr(setup_media_remote.socket, "gethostname", lambda: "Stheno")
    exe = tmp_path / ".local" / "bin" / "dotfiles-media-remote.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.touch()
    return tmp_path


@pytest.fixture(name="startup_dir")
def _startup_dir(windows_stheno: pathlib.Path) -> pathlib.Path:
    startup = windows_stheno / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup.mkdir(parents=True)
    return startup


@pytest.fixture(name="vbs_path")
def _vbs_path(windows_stheno: pathlib.Path) -> pathlib.Path:
    return windows_stheno / "AppData" / "Local" / "dotfiles" / "media-remote" / "launch.vbs"


@pytest.fixture(name="exe_path")
def _exe_path(windows_stheno: pathlib.Path) -> pathlib.Path:
    return windows_stheno / ".local" / "bin" / "dotfiles-media-remote.exe"


@pytest.mark.usefixtures("windows_stheno")
def test_startup_dir_missing_returns_false(monkeypatch: pytest.MonkeyPatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_media_remote.claude_common,
        "run_subprocess",
        _make_static_fake(calls),
    )
    assert setup_media_remote.run().changed is False
    assert not calls


@pytest.mark.usefixtures("startup_dir")
def test_exe_missing_skips(exe_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    # uv tool install経由のexeを削除する。フォールバックは存在しないためスキップされる。
    exe_path.unlink()
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_media_remote.claude_common,
        "run_subprocess",
        _make_static_fake(calls),
    )
    assert setup_media_remote.run().changed is False
    assert not calls


@pytest.mark.usefixtures("startup_dir")
def test_creates_shortcut_and_vbs_when_missing(
    exe_path: pathlib.Path,
    vbs_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_media_remote.claude_common,
        "run_subprocess",
        _make_static_fake(calls, _ok()),
    )

    assert setup_media_remote.run().changed is True

    # VBSが配置され、内容は期待形式と一致する。
    assert vbs_path.is_file()
    assert vbs_path.read_text(encoding="utf-8") == _expected_vbs(exe_path)

    # `.lnk`生成PowerShellスクリプト内にwscript.exeターゲットとVBSパスが渡されている。
    cmd_strings = [" ".join(c) for c in calls]
    save_scripts = [s for s in cmd_strings if "Save()" in s]
    assert save_scripts
    assert any(setup_media_remote.LNK_NAME in s for s in save_scripts)
    assert any(setup_media_remote.WSCRIPT_PATH in s for s in save_scripts)
    assert any(str(vbs_path) in s for s in save_scripts)


@pytest.mark.usefixtures("startup_dir")
def test_create_shortcut_failure_returns_false(
    exe_path: pathlib.Path,
    vbs_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """VBS配置済みの状態で`.lnk`生成PowerShellが失敗したとき`run()`は`False`を返す。"""
    vbs_path.parent.mkdir(parents=True, exist_ok=True)
    vbs_path.write_text(_expected_vbs(exe_path), encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_media_remote.claude_common,
        "run_subprocess",
        _make_static_fake(calls, _ok(returncode=1)),
    )
    assert setup_media_remote.run().changed is False
    cmd_strings = [" ".join(c) for c in calls]
    assert any("Save()" in s for s in cmd_strings)


def test_idempotent_when_vbs_and_lnk_match(
    startup_dir: pathlib.Path,
    exe_path: pathlib.Path,
    vbs_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
):
    lnk = startup_dir / setup_media_remote.LNK_NAME
    lnk.touch()
    vbs_path.parent.mkdir(parents=True, exist_ok=True)
    vbs_path.write_text(_expected_vbs(exe_path), encoding="utf-8")
    expected_args = f'"{vbs_path}"'
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_media_remote.claude_common,
        "run_subprocess",
        _make_branching_fake(
            calls,
            _ok(),
            _ok(stdout=f"{setup_media_remote.WSCRIPT_PATH}\t{expected_args}"),
        ),
    )
    assert setup_media_remote.run().changed is False
    cmd_strings = [" ".join(c) for c in calls]
    assert not any("Save()" in s for s in cmd_strings)


def test_existing_pythonw_lnk_is_overwritten(
    startup_dir: pathlib.Path,
    exe_path: pathlib.Path,
    vbs_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """旧仕様（TargetPathが`pythonw.exe`）の`.lnk`は新仕様で上書きされる。"""
    del exe_path  # 既存exe検出のため間接参照
    lnk = startup_dir / setup_media_remote.LNK_NAME
    lnk.touch()
    calls: list[list[str]] = []
    old_target = str(pathlib.Path.home() / ".local" / "share" / "uv" / "tools" / "pytools" / "Scripts" / "pythonw.exe")
    monkeypatch.setattr(
        setup_media_remote.claude_common,
        "run_subprocess",
        _make_branching_fake(
            calls,
            _ok(),
            _ok(stdout=f"{old_target}\t-m pytools.media_remote serve"),
        ),
    )
    assert setup_media_remote.run().changed is True
    assert vbs_path.is_file()
    cmd_strings = [" ".join(c) for c in calls]
    save_scripts = [s for s in cmd_strings if "Save()" in s]
    assert save_scripts
    assert any(setup_media_remote.WSCRIPT_PATH in s for s in save_scripts)


def test_non_stheno_removes_existing_lnk_and_vbs(
    startup_dir: pathlib.Path,
    vbs_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(setup_media_remote.socket, "gethostname", lambda: "other-host")
    lnk = startup_dir / setup_media_remote.LNK_NAME
    lnk.touch()
    vbs_path.parent.mkdir(parents=True, exist_ok=True)
    vbs_path.write_text("dummy", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_media_remote.claude_common,
        "run_subprocess",
        _make_static_fake(calls),
    )
    assert setup_media_remote.run().changed is True
    assert not lnk.is_file()
    assert not vbs_path.is_file()
    assert not calls


@pytest.mark.usefixtures("startup_dir")
def test_non_stheno_without_existing_assets_is_noop(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(setup_media_remote.socket, "gethostname", lambda: "other-host")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_media_remote.claude_common,
        "run_subprocess",
        _make_static_fake(calls),
    )
    assert setup_media_remote.run().changed is False
    assert not calls


class _FakeProcess:
    """`psutil.Process`の代わり。停止の呼び出しを記録する。"""

    def __init__(self, pid: int, exe: str | None, cmdline: list[str], killed: list[int]) -> None:
        self.pid = pid
        self.info = {"pid": pid, "exe": exe, "cmdline": cmdline}
        self._killed = killed

    def kill(self) -> None:
        self._killed.append(self.pid)


@pytest.mark.parametrize(
    ("stopped", "reinstall", "expected_kill", "expected_start"),
    [
        ("1", "1", False, True),
        ("", "1", False, False),
        ("", "", True, True),
    ],
)
@pytest.mark.parametrize("has_vbs", [True, False])
@pytest.mark.usefixtures("startup_dir")
def test_restart_after_reinstall_and_symptomatic_restart(
    monkeypatch: pytest.MonkeyPatch,
    exe_path: pathlib.Path,
    vbs_path: pathlib.Path,
    stopped: str,
    reinstall: str,
    expected_kill: bool,
    expected_start: bool,
    has_vbs: bool,
) -> None:
    """再導入で停止した回は起動だけ、再導入しない回は停止→起動を行い、VBSラッパーが無ければ実行ファイルを直接起動する。"""
    monkeypatch.setattr(setup_media_remote, "_restart", _REAL_RESTART)
    monkeypatch.setenv(setup_media_remote.STOPPED_ENV, stopped)
    monkeypatch.setenv(setup_media_remote.REINSTALL_ENV, reinstall)
    monkeypatch.setattr(setup_media_remote.claude_common, "run_subprocess", _make_static_fake([]))
    monkeypatch.setattr(setup_media_remote, "_is_up_to_date", lambda _lnk, _vbs: True)
    monkeypatch.setattr(setup_media_remote, "_ensure_vbs", lambda _vbs, _exe: False)
    if has_vbs:
        vbs_path.parent.mkdir(parents=True, exist_ok=True)
        vbs_path.write_text("", encoding="utf-8")
    killed: list[int] = []
    processes = [
        _FakeProcess(1, str(exe_path).upper(), [str(exe_path), "serve"], killed),
        _FakeProcess(2, r"C:\Python\python.exe", ["python", "-m", "pytools.media_remote", "serve"], killed),
        _FakeProcess(3, r"C:\other.exe", ["other"], killed),
    ]
    monkeypatch.setattr(setup_media_remote.psutil, "process_iter", lambda _attrs: processes)
    monkeypatch.setattr(setup_media_remote.psutil, "wait_procs", lambda procs, timeout: (procs, []))
    launched: list[list[str]] = []

    def fake_popen(command: list[str], **_kwargs: object) -> None:
        launched.append(command)

    monkeypatch.setattr(setup_media_remote.subprocess, "Popen", fake_popen)

    outcome = setup_media_remote.run()

    assert outcome.failure is None
    assert killed == ([1, 2] if expected_kill else [])
    expected_command = [setup_media_remote.WSCRIPT_PATH, str(vbs_path)] if has_vbs else [str(exe_path), "serve"]
    assert launched == ([expected_command] if expected_start else [])


@pytest.mark.usefixtures("startup_dir")
def test_restart_does_nothing_on_other_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    """stheno以外では停止も起動もしない。"""
    monkeypatch.setattr(setup_media_remote, "_restart", _REAL_RESTART)
    monkeypatch.setattr(setup_media_remote.socket, "gethostname", lambda: "euryale")
    monkeypatch.setenv(setup_media_remote.STOPPED_ENV, "1")
    monkeypatch.setattr(setup_media_remote.psutil, "process_iter", lambda _attrs: pytest.fail("停止しない"))
    monkeypatch.setattr(setup_media_remote.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("起動しない"))

    assert setup_media_remote.run().failure is None
