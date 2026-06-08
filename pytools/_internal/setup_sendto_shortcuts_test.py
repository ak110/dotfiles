"""pytools._internal.setup_sendto_shortcuts のテスト。"""

import pathlib
import subprocess
import typing
from collections.abc import Callable

import pytest

from pytools._internal import setup_sendto_shortcuts

_FakeRun = Callable[..., subprocess.CompletedProcess[str] | None]


def _ok(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode=returncode, stdout=stdout, stderr="")


def _make_static_fake(
    calls: list[list[str]],
    response: subprocess.CompletedProcess[str] | None = None,
) -> _FakeRun:
    """全呼び出しに同じレスポンスを返す run_subprocess の差し替え関数。"""
    fixed = response if response is not None else _ok()

    def fake(
        cmd: list[str],
        *,
        timeout: float | None = None,
        cwd: pathlib.Path | None = None,
        tag: str | None = None,
        **kwargs: typing.Any,
    ) -> subprocess.CompletedProcess[str] | None:
        del timeout, cwd, tag, kwargs
        calls.append(list(cmd))
        return fixed

    return fake


def _make_branching_fake(
    calls: list[list[str]],
    create_result: subprocess.CompletedProcess[str],
    read_result: subprocess.CompletedProcess[str],
) -> _FakeRun:
    """script 内容に応じて Save()（生成）と TargetPath 読み取りを切り替える。"""

    def fake(
        cmd: list[str],
        *,
        timeout: float | None = None,
        cwd: pathlib.Path | None = None,
        tag: str | None = None,
        **kwargs: typing.Any,
    ) -> subprocess.CompletedProcess[str] | None:
        del timeout, cwd, tag, kwargs
        calls.append(list(cmd))
        script = " ".join(cmd)
        return create_result if "Save()" in script else read_result

    return fake


@pytest.fixture(name="windows_home")
def _windows_home(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    """Windows 環境を装い、ホームと APPDATA を tmp_path 配下に振り向ける。"""
    monkeypatch.setattr(setup_sendto_shortcuts.sys, "platform", "win32")
    monkeypatch.setattr(setup_sendto_shortcuts.pathlib.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    return tmp_path


class TestRunPlatformGuard:
    """非 Windows での no-op 動作。"""

    def test_non_windows_returns_false(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(setup_sendto_shortcuts.sys, "platform", "linux")
        calls: list[list[str]] = []
        monkeypatch.setattr(
            setup_sendto_shortcuts.claude_common,
            "run_subprocess",
            _make_static_fake(calls),
        )

        assert setup_sendto_shortcuts.run() is False
        assert not calls


class TestRunSendToMissing:
    """SendTo ディレクトリが存在しない場合のスキップ動作。"""

    def test_sendto_dir_missing_returns_false(self, windows_home: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        del windows_home
        calls: list[list[str]] = []
        monkeypatch.setattr(
            setup_sendto_shortcuts.claude_common,
            "run_subprocess",
            _make_static_fake(calls),
        )

        assert setup_sendto_shortcuts.run() is False
        assert not calls


class TestRunTargetMissing:
    """ターゲット exe が未配置の場合のスキップ動作。"""

    def test_target_missing_skips_without_subprocess(self, windows_home: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        sendto = windows_home / "AppData" / "Roaming" / "Microsoft" / "Windows" / "SendTo"
        sendto.mkdir(parents=True)

        calls: list[list[str]] = []
        monkeypatch.setattr(
            setup_sendto_shortcuts.claude_common,
            "run_subprocess",
            _make_static_fake(calls),
        )

        assert setup_sendto_shortcuts.run() is False
        assert not calls
        assert not (sendto / "TouchFile.lnk").exists()


class TestRunShortcutCreation:
    """SendTo + ターゲット exe 配置済みの状態でショートカットを操作する。"""

    @pytest.fixture(name="prepared")
    def _prepared(self, windows_home: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
        sendto = windows_home / "AppData" / "Roaming" / "Microsoft" / "Windows" / "SendTo"
        sendto.mkdir(parents=True)
        target = windows_home / ".local" / "bin" / "touch-file.exe"
        target.parent.mkdir(parents=True)
        target.touch()
        lnk = sendto / "TouchFile.lnk"
        return sendto, target, lnk

    def test_creates_shortcut_when_missing(
        self, prepared: tuple[pathlib.Path, pathlib.Path, pathlib.Path], monkeypatch: pytest.MonkeyPatch
    ):
        _, target, lnk = prepared
        calls: list[list[str]] = []
        monkeypatch.setattr(
            setup_sendto_shortcuts.claude_common,
            "run_subprocess",
            _make_static_fake(calls, _ok()),
        )

        assert setup_sendto_shortcuts.run() is True
        cmd_strings = [" ".join(c) for c in calls]
        # .lnk 不在のため読み取りは行われず、Save() を含む生成コマンドのみ呼ばれる。
        # 既定の `_SHORTCUTS` (TouchFile.lnk) のターゲットと .lnk パスが
        # PowerShell コマンドに正しく組み込まれることもここで担保される。
        assert any("Save()" in s for s in cmd_strings)
        assert any(str(target) in s for s in cmd_strings)
        assert any(str(lnk) in s for s in cmd_strings)

    def test_idempotent_when_target_matches(
        self, prepared: tuple[pathlib.Path, pathlib.Path, pathlib.Path], monkeypatch: pytest.MonkeyPatch
    ):
        _, target, lnk = prepared
        lnk.touch()  # 既存ファイル扱いにする
        calls: list[list[str]] = []
        monkeypatch.setattr(
            setup_sendto_shortcuts.claude_common,
            "run_subprocess",
            _make_branching_fake(calls, _ok(), _ok(stdout=str(target))),
        )

        assert setup_sendto_shortcuts.run() is False
        cmd_strings = [" ".join(c) for c in calls]
        # 読み取りで一致が確認できるため生成は呼ばれない
        assert not any("Save()" in s for s in cmd_strings)

    def test_idempotent_match_is_case_insensitive(
        self, prepared: tuple[pathlib.Path, pathlib.Path, pathlib.Path], monkeypatch: pytest.MonkeyPatch
    ):
        _, target, lnk = prepared
        lnk.touch()
        calls: list[list[str]] = []
        # Windows のファイルシステムは case-insensitive。
        # 大文字小文字が異なるだけのターゲットは一致とみなす。
        monkeypatch.setattr(
            setup_sendto_shortcuts.claude_common,
            "run_subprocess",
            _make_branching_fake(calls, _ok(), _ok(stdout=str(target).upper())),
        )

        assert setup_sendto_shortcuts.run() is False
        cmd_strings = [" ".join(c) for c in calls]
        assert not any("Save()" in s for s in cmd_strings)

    def test_overwrites_when_target_differs(
        self, prepared: tuple[pathlib.Path, pathlib.Path, pathlib.Path], monkeypatch: pytest.MonkeyPatch
    ):
        _, _target, lnk = prepared
        lnk.touch()
        calls: list[list[str]] = []
        monkeypatch.setattr(
            setup_sendto_shortcuts.claude_common,
            "run_subprocess",
            _make_branching_fake(calls, _ok(), _ok(stdout=r"C:\old\touch-file.exe")),
        )

        assert setup_sendto_shortcuts.run() is True
        cmd_strings = [" ".join(c) for c in calls]
        assert any("Save()" in s for s in cmd_strings)

    def test_create_failure_returns_false(
        self, prepared: tuple[pathlib.Path, pathlib.Path, pathlib.Path], monkeypatch: pytest.MonkeyPatch
    ):
        _, _target, _lnk = prepared
        # 生成側が exit 1 で失敗 → run() は False を返し、他の副作用は残さない
        calls: list[list[str]] = []
        monkeypatch.setattr(
            setup_sendto_shortcuts.claude_common,
            "run_subprocess",
            _make_static_fake(calls, _ok(returncode=1)),
        )

        assert setup_sendto_shortcuts.run() is False

    def test_single_quote_in_path_is_escaped_in_powershell_command(
        self, windows_home: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ):
        """PowerShell シングルクオート文字列内のエスケープが実コマンドへ反映される。"""
        sendto = windows_home / "AppData" / "Roaming" / "Microsoft" / "Windows" / "SendTo"
        sendto.mkdir(parents=True)
        odd = windows_home / "O'Dir"
        odd.mkdir()
        target = odd / "tool.exe"
        target.touch()

        monkeypatch.setattr(
            setup_sendto_shortcuts,
            "_SHORTCUTS",
            [
                setup_sendto_shortcuts._Shortcut(  # noqa: SLF001 # pylint: disable=protected-access  # グローバル定数の差し替えに必要
                    lnk_name="O'Tool.lnk",
                    target_relative=pathlib.PurePath("O'Dir") / "tool.exe",
                ),
            ],
        )

        calls: list[list[str]] = []
        monkeypatch.setattr(
            setup_sendto_shortcuts.claude_common,
            "run_subprocess",
            _make_static_fake(calls, _ok()),
        )

        assert setup_sendto_shortcuts.run() is True
        cmd_strings = [" ".join(c) for c in calls]
        # シングルクオートが PowerShell の '' (2連) へエスケープされている
        assert any("O''Dir" in s for s in cmd_strings)
        assert any("O''Tool.lnk" in s for s in cmd_strings)

    def test_processes_all_entries_in_table(self, windows_home: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        """`_SHORTCUTS` の全エントリが順に処理される。"""
        sendto = windows_home / "AppData" / "Roaming" / "Microsoft" / "Windows" / "SendTo"
        sendto.mkdir(parents=True)
        target1 = windows_home / ".local" / "bin" / "tool1.exe"
        target2 = windows_home / ".local" / "bin" / "tool2.exe"
        target1.parent.mkdir(parents=True, exist_ok=True)
        target1.touch()
        target2.touch()

        monkeypatch.setattr(
            setup_sendto_shortcuts,
            "_SHORTCUTS",
            [
                setup_sendto_shortcuts._Shortcut(  # noqa: SLF001 # pylint: disable=protected-access  # グローバル定数の差し替えに必要
                    lnk_name="Tool1.lnk",
                    target_relative=pathlib.PurePath(".local") / "bin" / "tool1.exe",
                ),
                setup_sendto_shortcuts._Shortcut(  # noqa: SLF001 # pylint: disable=protected-access  # グローバル定数の差し替えに必要
                    lnk_name="Tool2.lnk",
                    target_relative=pathlib.PurePath(".local") / "bin" / "tool2.exe",
                ),
            ],
        )

        calls: list[list[str]] = []
        monkeypatch.setattr(
            setup_sendto_shortcuts.claude_common,
            "run_subprocess",
            _make_static_fake(calls, _ok()),
        )

        assert setup_sendto_shortcuts.run() is True
        cmd_strings = [" ".join(c) for c in calls]
        # 両エントリの .lnk とターゲットが PowerShell コマンドへ展開される
        assert any("Tool1.lnk" in s and str(target1) in s for s in cmd_strings)
        assert any("Tool2.lnk" in s and str(target2) in s for s in cmd_strings)
