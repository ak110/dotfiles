"""pytools._internal.cleanup_user_path のテスト。

`run()` 経由でプレースホルダー化・システム側およびユーザー側の重複除外・存在チェック警告・値型昇格・
レジストリ I/O・ブロードキャスト連動・非 Windows 早期 return を検証する。
レジストリ I/O は monkeypatch で `winutils` 関数を差し替え、Linux 上でも実行可能にする。
"""

# pylint: disable=protected-access

import itertools
from pathlib import Path, PureWindowsPath

import pytest

from pytools._internal import cleanup_user_path, install_libarchive, setup_bin_path, setup_mise

from ._test_helpers import FakeEnvironmentRegistry

# `winreg.REG_EXPAND_SZ` / `REG_SZ` の値。Linux 上では `winreg` をインポートできないため、
# Windows 上の定数値を直接埋め込んでテストの再現性を確保する。
_REG_SZ = 1
_REG_EXPAND_SZ = 2


_USERPROFILE = r"C:\Users\test"
_LOCALAPPDATA = r"C:\Users\test\AppData\Local"
_APPDATA = r"C:\Users\test\AppData\Roaming"


@pytest.fixture(autouse=True)
def _userprofile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USERPROFILE", _USERPROFILE)
    monkeypatch.setenv("LOCALAPPDATA", _LOCALAPPDATA)
    monkeypatch.setenv("APPDATA", _APPDATA)


def test_missing_known_winget_paths_preserve_existing_and_unreadable_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    packages = PureWindowsPath(_LOCALAPPDATA) / "Microsoft" / "WinGet" / "Packages"
    rg = "BurntSushi.ripgrep.MSVC_Microsoft.Winget.Source_test"
    chezmoi = "twpayne.chezmoi_Microsoft.Winget.Source_test"
    missing = str(packages / rg / "bin")
    unreadable = str(packages / chezmoi)
    existing = str(packages / f"{rg}2" / "bin")
    unrelated = str(packages / "Example.Other_Microsoft.Winget.Source_test")
    links = str(packages.parent / "Links")

    def lstat(path: Path) -> object:
        if str(path) == str(packages / rg):
            raise FileNotFoundError
        if str(path) == unreadable:
            raise PermissionError
        return object()

    monkeypatch.setattr(Path, "lstat", lstat)
    result, removed = cleanup_user_path._filter_retired_winget_paths(
        ";".join([missing, unreadable, existing, unrelated, links])
    )
    assert removed == [missing]
    assert result == ";".join([unreadable, existing, unrelated, links])


def test_current_uninstall_cleans_only_exact_package_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    packages = PureWindowsPath(_LOCALAPPDATA) / "Microsoft" / "WinGet" / "Packages"
    removed = packages / "Example.Other_Microsoft.Winget.Source_test"
    keep = str(packages / "Example.Other_Microsoft.Winget.Source_test2")
    remove = str(removed / "bin")
    written: list[str] = []

    def missing(_path: Path) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(Path, "lstat", missing)
    monkeypatch.setattr(cleanup_user_path.winutils, "read_user_env_var", lambda _key: (f"{keep};{remove}", _REG_EXPAND_SZ))
    monkeypatch.setattr(cleanup_user_path.winutils, "write_user_env_var", lambda _key, value, _kind: written.append(value))
    monkeypatch.setattr(cleanup_user_path.winutils, "broadcast_environment_change", lambda: None)
    assert cleanup_user_path.remove_winget_package_paths([Path(str(removed))])
    assert written == [keep]


class TestFilterUserPath:
    def test_separator_direction_is_absorbed(self, monkeypatch: pytest.MonkeyPatch):
        write_calls, _ = _stub_winutils(
            monkeypatch,
            user_value=r"C:/Windows/System32;D:\mybin",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )
        assert cleanup_user_path.run().changed is True
        assert write_calls == [("Path", r"D:\mybin", _REG_EXPAND_SZ)]

    def test_trailing_separator_is_absorbed(self, monkeypatch: pytest.MonkeyPatch):
        write_calls, _ = _stub_winutils(
            monkeypatch,
            user_value=r"C:\Windows\System32\;D:\app",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )
        assert cleanup_user_path.run().changed is True
        assert write_calls == [("Path", r"D:\app", _REG_EXPAND_SZ)]

    def test_case_difference_is_absorbed(self, monkeypatch: pytest.MonkeyPatch):
        write_calls, _ = _stub_winutils(
            monkeypatch,
            user_value=r"c:\windows\SYSTEM32;D:\app",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )
        assert cleanup_user_path.run().changed is True
        assert write_calls == [("Path", r"D:\app", _REG_EXPAND_SZ)]

    def test_placeholder_matches_expanded_form(self, monkeypatch: pytest.MonkeyPatch):
        write_calls, _ = _stub_winutils(
            monkeypatch,
            user_value=r"%USERPROFILE%\foo;D:\other",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Users\test\foo",
        )
        assert cleanup_user_path.run().changed is True
        assert write_calls == [("Path", r"D:\other", _REG_EXPAND_SZ)]

    def test_kept_entries_preserve_original_placeholder_string(self, monkeypatch: pytest.MonkeyPatch):
        write_calls, _ = _stub_winutils(
            monkeypatch,
            user_value=r"%USERPROFILE%\bar;C:\Windows\System32",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )
        # C:\Windows\System32 はシステム側と重複して除外される。
        # %USERPROFILE%\bar は展開されず元の表記のまま残る。
        assert cleanup_user_path.run().changed is True
        assert write_calls == [("Path", r"%USERPROFILE%\bar", _REG_EXPAND_SZ)]

    def test_no_duplicates_means_no_change(self, monkeypatch: pytest.MonkeyPatch):
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=r"D:\app;D:\mybin",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )
        assert cleanup_user_path.run().changed is False
        assert not write_calls
        assert not broadcast_calls

    def test_empty_system_value(self, monkeypatch: pytest.MonkeyPatch):
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=r"D:\app;D:\mybin",
            user_reg_type=_REG_EXPAND_SZ,
            system_value="",
        )
        assert cleanup_user_path.run().changed is False
        assert not write_calls
        assert not broadcast_calls

    def test_duplicate_user_entries_keep_first(self, monkeypatch: pytest.MonkeyPatch):
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=r"D:\app;D:\tools;D:\app;D:\app",
            user_reg_type=_REG_EXPAND_SZ,
            system_value="",
        )
        assert cleanup_user_path.run().changed is True
        assert write_calls == [("Path", r"D:\app;D:\tools", _REG_EXPAND_SZ)]
        assert broadcast_calls == [True]

    def test_normalized_duplicate_user_entries_keep_first(self, monkeypatch: pytest.MonkeyPatch):
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=r"D:\Apps\bin;d:/apps/bin/;D:\other",
            user_reg_type=_REG_EXPAND_SZ,
            system_value="",
        )
        assert cleanup_user_path.run().changed is True
        assert write_calls == [("Path", r"D:\Apps\bin;D:\other", _REG_EXPAND_SZ)]
        assert broadcast_calls == [True]

    def test_placeholder_and_expanded_user_entries_are_deduplicated(self, monkeypatch: pytest.MonkeyPatch):
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=rf"%USERPROFILE%\bin;{_USERPROFILE}\bin;D:\other",
            user_reg_type=_REG_EXPAND_SZ,
            system_value="",
        )
        assert cleanup_user_path.run().changed is True
        assert write_calls == [("Path", r"%USERPROFILE%\bin;D:\other", _REG_EXPAND_SZ)]
        assert broadcast_calls == [True]


class TestReplacePlaceholders:
    @pytest.mark.parametrize(
        ("entry", "expected"),
        [
            (r"C:\Users\test\Documents", r"%USERPROFILE%\Documents"),
            (r"C:\Users\test\AppData\Local\Programs", r"%LOCALAPPDATA%\Programs"),
            (r"C:\Users\test\AppData\Roaming\Code", r"%APPDATA%\Code"),
            (r"%USERPROFILE%\AppData\Local\foo", r"%LOCALAPPDATA%\foo"),
            # (e) 末尾完全一致 (区切り無し終端) は環境変数単体に置換する。
            (r"C:\Users\test", r"%USERPROFILE%"),
            (r"c:\USERS\Test\Documents", r"%USERPROFILE%\Documents"),
            # (h) 区切り方向差 (`/`) は PureWindowsPath が吸収する。
            (r"C:/Users/test/Documents", r"%USERPROFILE%\Documents"),
        ],
    )
    def test_replaces_with_longest_match(self, monkeypatch: pytest.MonkeyPatch, entry: str, expected: str):
        write_calls, _ = _stub_winutils(
            monkeypatch,
            user_value=entry,
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )
        assert cleanup_user_path.run().changed is True
        assert write_calls[0][1] == expected

    def test_no_match_entry_is_unchanged(self, monkeypatch: pytest.MonkeyPatch):
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=r"D:\Tools\bin",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )

        assert cleanup_user_path.run().changed is False
        assert not write_calls
        assert not broadcast_calls

    def test_skips_undefined_environment_variable(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        write_calls, _ = _stub_winutils(
            monkeypatch,
            user_value=r"C:\Users\test\AppData\Local\foo",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )
        assert cleanup_user_path.run().changed is True

        assert write_calls[0][1] == r"%USERPROFILE%\AppData\Local\foo"

    def test_empty_env_map_leaves_entry_unchanged(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("USERPROFILE", raising=False)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.delenv("APPDATA", raising=False)
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=r"C:\Users\test\foo",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )

        assert cleanup_user_path.run().changed is False
        assert not write_calls
        assert not broadcast_calls


class TestCollectUserprofileEnv:
    def test_returns_longest_first_order(self, monkeypatch: pytest.MonkeyPatch):
        write_calls, _ = _stub_winutils(
            monkeypatch,
            user_value=r"C:\Users\test\AppData\Local\Programs",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )
        assert cleanup_user_path.run().changed is True

        assert write_calls[0][1] == r"%LOCALAPPDATA%\Programs"

    def test_skips_empty_or_missing_variables(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LOCALAPPDATA", "")
        monkeypatch.delenv("APPDATA", raising=False)
        write_calls, _ = _stub_winutils(
            monkeypatch,
            user_value=r"C:\Users\test\AppData\Local\foo",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )
        assert cleanup_user_path.run().changed is True

        assert write_calls[0][1] == r"%USERPROFILE%\AppData\Local\foo"


class TestFindMissingPaths:
    def test_existing_entry_is_not_reported(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        existing = tmp_path / "bin"
        existing.mkdir()
        # プレースホルダー化で変換されないよう Linux パス形式（tmp_path）をそのまま使う。
        # ただし run() は win32 環境を想定するため、存在チェックは path.exists() で成功する。
        _stub_winutils(
            monkeypatch,
            user_value=str(existing),
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
            stub_find_missing=False,
        )

        assert cleanup_user_path.run().changed is False

    def test_missing_entry_emits_warning(self, monkeypatch: pytest.MonkeyPatch, tmp_path, caplog: pytest.LogCaptureFixture):
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        _stub_winutils(
            monkeypatch,
            user_value=r"%USERPROFILE%\missing",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
            stub_find_missing=False,
        )

        with caplog.at_level("WARNING", logger=cleanup_user_path.logger.name):
            assert cleanup_user_path.run().changed is False
        assert any("ユーザー PATH に存在しないエントリーを検出" in record.getMessage() for record in caplog.records)

    def test_os_error_is_reported_and_remaining_entry_is_checked(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        """1件の検査失敗を分離し、後続の不在エントリーを検出する。"""
        failing = str(tmp_path / "failing")
        missing = str(tmp_path / "missing")
        real_exists = Path.exists

        def exists(path: Path) -> bool:
            if str(path) == failing:
                raise OSError("検査失敗")
            return real_exists(path)

        monkeypatch.setattr(Path, "exists", exists)

        absent, unresolved = cleanup_user_path._find_missing_paths((failing, missing))

        assert absent == [(missing, missing)]
        assert len(unresolved) == 1
        assert unresolved[0][:2] == (failing, failing)
        assert str(unresolved[0][2]) == "検査失敗"

    def test_unresolved_placeholder_is_skipped(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
        _stub_winutils(
            monkeypatch,
            user_value=r"%UNDEFINED_VAR%\foo",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
            stub_find_missing=False,
        )

        with caplog.at_level("WARNING", logger=cleanup_user_path.logger.name):
            cleanup_user_path.run()

        assert not any("存在しないエントリーを検出" in record.getMessage() for record in caplog.records)


class TestRun:
    @pytest.mark.parametrize("case", ["missing", "existing", "inaccessible", "different-checkout"])
    def test_only_known_missing_toolkit_bin_is_removed(self, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
        root = Path(r"C:\Users\test\other" if case == "different-checkout" else r"C:\Users\test\dotfiles")
        old = root / "agent-toolkit" / "bin"
        monkeypatch.setattr(cleanup_user_path.common, "find_dotfiles_root", lambda: root)
        original_exists = Path.exists

        def exists(path: Path) -> bool:
            if path != old:
                return original_exists(path)
            if case == "inaccessible":
                raise PermissionError("検査できない")
            return case == "existing"

        monkeypatch.setattr(Path, "exists", exists)
        user_value = r"%USERPROFILE%\dotfiles\agent-toolkit\bin;%USERPROFILE%\missing"
        writes, broadcasts = _stub_winutils(monkeypatch, user_value=user_value, user_reg_type=_REG_EXPAND_SZ, system_value="")
        assert cleanup_user_path.run().changed is (case == "missing")
        assert writes == ([("Path", r"%USERPROFILE%\missing", _REG_EXPAND_SZ)] if case == "missing" else [])
        assert broadcasts == ([True] if case == "missing" else [])

    def test_removes_duplicates_and_broadcasts(self, monkeypatch: pytest.MonkeyPatch):
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=r"%USERPROFILE%\bar;C:\Windows\System32",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )

        assert cleanup_user_path.run().changed is True

        assert write_calls == [("Path", r"%USERPROFILE%\bar", _REG_EXPAND_SZ)]
        assert broadcast_calls == [True]

    def test_no_duplicates_does_not_write(self, monkeypatch: pytest.MonkeyPatch):
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=r"%USERPROFILE%\bin",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )

        assert cleanup_user_path.run().changed is False
        assert not write_calls
        assert not broadcast_calls

    def test_user_path_empty_returns_false(self, monkeypatch: pytest.MonkeyPatch):

        def _fail(name: str) -> tuple[str | None, int]:
            raise AssertionError("system 側読み込みは呼ばれてはならない")

        monkeypatch.setattr(
            cleanup_user_path.winutils,
            "read_user_env_var",
            lambda name: (None, _REG_SZ),
        )
        monkeypatch.setattr(cleanup_user_path.winutils, "read_system_env_var", _fail)
        assert cleanup_user_path.run().changed is False

    def test_system_read_failure_is_reported_as_failure(self, monkeypatch: pytest.MonkeyPatch):
        """システム側読み込み失敗時は書き戻しを行わず、失敗と数える。"""
        write_calls: list[tuple[str, str, int]] = []

        def _raise(name: str) -> tuple[str | None, int]:
            raise OSError("access denied")

        monkeypatch.setattr(
            cleanup_user_path.winutils,
            "read_user_env_var",
            lambda name: (r"C:\Users\test\bin", _REG_EXPAND_SZ),
        )
        monkeypatch.setattr(cleanup_user_path.winutils, "read_system_env_var", _raise)
        monkeypatch.setattr(
            cleanup_user_path.winutils,
            "write_user_env_var",
            lambda name, value, reg_type: write_calls.append((name, value, reg_type)),
        )

        outcome = cleanup_user_path.run()
        assert outcome.changed is False
        assert outcome.failure is not None
        assert "システム側 PATH の読み込みに失敗" in outcome.failure
        assert not write_calls

    def test_placeholder_replacement_and_dedup_together(self, monkeypatch: pytest.MonkeyPatch):
        """プレースホルダー化と重複除外が同時に発火するケース。"""
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=rf"{_USERPROFILE}\foo;C:\Windows\System32",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )

        assert cleanup_user_path.run().changed is True
        assert write_calls == [("Path", r"%USERPROFILE%\foo", _REG_EXPAND_SZ)]
        assert broadcast_calls == [True]

    def test_placeholder_only_triggers_write(self, monkeypatch: pytest.MonkeyPatch):
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=rf"{_USERPROFILE}\foo",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )

        assert cleanup_user_path.run().changed is True
        assert write_calls == [("Path", r"%USERPROFILE%\foo", _REG_EXPAND_SZ)]
        assert broadcast_calls == [True]

    def test_reg_sz_is_promoted_when_value_contains_placeholder(self, monkeypatch: pytest.MonkeyPatch):
        """書き戻し値が % を含み元が REG_SZ なら REG_EXPAND_SZ へ昇格する。"""
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=rf"{_USERPROFILE}\foo",
            user_reg_type=_REG_SZ,
            system_value=r"C:\Windows\System32",
        )

        assert cleanup_user_path.run().changed is True
        assert write_calls == [("Path", r"%USERPROFILE%\foo", _REG_EXPAND_SZ)]
        assert broadcast_calls == [True]

    def test_reg_type_is_preserved_without_placeholder(self, monkeypatch: pytest.MonkeyPatch):
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=r"C:\app;C:\Windows\System32",
            user_reg_type=_REG_SZ,
            system_value=r"C:\Windows\System32",
        )

        assert cleanup_user_path.run().changed is True
        assert write_calls == [("Path", r"C:\app", _REG_SZ)]
        assert broadcast_calls == [True]

    def test_value_type_promotion_only_triggers_write(self, monkeypatch: pytest.MonkeyPatch):
        """既に最適なプレースホルダー表記で保存済みだが REG_SZ のままの値も REG_EXPAND_SZ へ昇格して書き戻す。"""
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=r"%USERPROFILE%\bin",
            user_reg_type=_REG_SZ,
            system_value=r"C:\Windows\System32",
        )

        assert cleanup_user_path.run().changed is True

        assert write_calls == [("Path", r"%USERPROFILE%\bin", _REG_EXPAND_SZ)]
        assert broadcast_calls == [True]

    def test_missing_path_emits_warning_without_write(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=r"%USERPROFILE%\missing",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )
        # 差し替えない Path.exists() は Linux 上で常に False を返すため、ここで明示的に False 固定する。
        monkeypatch.setattr(
            cleanup_user_path,
            "_find_missing_paths",
            lambda entries: ([(r"%USERPROFILE%\missing", rf"{_USERPROFILE}\missing")], []),
        )

        with caplog.at_level("WARNING", logger=cleanup_user_path.logger.name):
            assert cleanup_user_path.run().changed is False
        assert not write_calls
        assert not broadcast_calls
        assert any("ユーザー PATH に存在しないエントリーを検出" in record.getMessage() for record in caplog.records)

    def test_path_check_os_error_warns_and_does_not_prevent_writeback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level("INFO")
        failing = str(tmp_path / "failing")
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=failing + r";C:\Windows\System32",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
            stub_find_missing=False,
        )
        real_exists = Path.exists

        def exists(path: Path) -> bool:
            if str(path) == failing:
                raise OSError("検査失敗")
            return real_exists(path)

        monkeypatch.setattr(Path, "exists", exists)

        with caplog.at_level("INFO", logger=cleanup_user_path.logger.name):
            assert cleanup_user_path.run().changed is True

        assert write_calls == [("Path", failing, _REG_EXPAND_SZ)]
        assert broadcast_calls == [True]
        assert failing in caplog.text
        assert "検査失敗" in caplog.text


def _stub_winutils(
    monkeypatch: pytest.MonkeyPatch,
    *,
    user_value: str,
    user_reg_type: int,
    system_value: str,
    stub_find_missing: bool = True,
) -> tuple[list[tuple[str, str, int]], list[bool]]:
    write_calls: list[tuple[str, str, int]] = []
    broadcast_calls: list[bool] = []
    monkeypatch.setattr(
        cleanup_user_path.winutils,
        "read_user_env_var",
        lambda name: (user_value, user_reg_type),
    )
    monkeypatch.setattr(
        cleanup_user_path.winutils,
        "read_system_env_var",
        lambda name: (system_value, _REG_EXPAND_SZ),
    )
    monkeypatch.setattr(
        cleanup_user_path.winutils,
        "write_user_env_var",
        lambda name, value, reg_type: write_calls.append((name, value, reg_type)),
    )
    monkeypatch.setattr(
        cleanup_user_path.winutils,
        "broadcast_environment_change",
        lambda: broadcast_calls.append(True),
    )
    if stub_find_missing:
        monkeypatch.setattr(cleanup_user_path, "_find_missing_paths", lambda entries: ([], []))
    return write_calls, broadcast_calls


def test_registration_then_cleanup_is_fixed_point(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    local_app_data = tmp_path / "AppData" / "Local"
    (local_app_data / "mise" / "shims").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    registry = FakeEnvironmentRegistry(
        user_path=rf"C:\Windows;{_USERPROFILE}\dotfiles\bin",
        system_path=rf"C:\Windows\System32;{local_app_data}\mise\shims",
    )
    registry.install(monkeypatch)
    monkeypatch.setattr(install_libarchive, "_INSTALL_DIR", PureWindowsPath(_USERPROFILE) / ".local" / "lib" / "libarchive")

    loadable = itertools.cycle([False, True])
    monkeypatch.setattr(install_libarchive, "_is_loadable", lambda: next(loadable))
    monkeypatch.setattr(install_libarchive, "_download_dlls", lambda: True)
    monkeypatch.setattr(install_libarchive, "_persist_libarchive_env_var", lambda: False)

    def register_all() -> None:
        assert setup_bin_path.run().failure is None
        assert install_libarchive.run().failure is None

        setup_mise.ensure_windows_user_path_has_shims()  # noqa: SLF001  # pylint: disable=protected-access

    register_all()
    cleanup_user_path.run()
    registry.writes.clear()

    register_all()
    assert cleanup_user_path.run().changed is False
    assert not registry.writes
