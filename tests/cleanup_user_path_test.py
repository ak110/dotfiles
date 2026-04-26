"""pytools._internal.cleanup_user_path のテスト。

純粋関数 (`_filter_user_path` / `_normalize_entry`) のパス比較ロジックと、
`run()` のレジストリ I/O・ブロードキャスト連動・非 Windows 早期 return を検証する。
レジストリ I/O は monkeypatch で `winutils` 関数を差し替え、Linux 上でも実行可能にする。
"""

# `_filter_user_path` 等の protected 関数を直接テストするため一括で許可する。
# pylint: disable=protected-access

import pytest

from pytools._internal import cleanup_user_path

# `winreg.REG_EXPAND_SZ` / `REG_SZ` の値。Linux 上では `winreg` をインポートできないため、
# Windows 上の定数値を直接埋め込んでテストの再現性を確保する。
_REG_SZ = 1
_REG_EXPAND_SZ = 2


@pytest.fixture(autouse=True)
def _userprofile(monkeypatch: pytest.MonkeyPatch) -> None:
    """テスト中の `%USERPROFILE%` 展開を固定値にする。"""
    monkeypatch.setenv("USERPROFILE", r"C:\Users\test")


class TestFilterUserPath:
    """`_filter_user_path` のパス比較・差分計算ロジック。"""

    def test_separator_direction_is_absorbed(self):
        """区切り文字の向き (`\\` vs `/`) は同一視する。"""
        new_value, removed = cleanup_user_path._filter_user_path(
            user_value=r"C:/Windows/System32;C:\Users\test\bin",
            system_value=r"C:\Windows\System32",
        )
        assert new_value == r"C:\Users\test\bin"
        assert removed == ["C:/Windows/System32"]

    def test_trailing_separator_is_absorbed(self):
        """末尾の区切り文字の有無は同一視する。"""
        new_value, removed = cleanup_user_path._filter_user_path(
            user_value=r"C:\Windows\System32\;C:\app",
            system_value=r"C:\Windows\System32",
        )
        assert new_value == r"C:\app"
        assert removed == ["C:\\Windows\\System32\\"]

    def test_case_difference_is_absorbed(self):
        """大文字小文字差は同一視する。"""
        new_value, removed = cleanup_user_path._filter_user_path(
            user_value=r"c:\windows\SYSTEM32;C:\app",
            system_value=r"C:\Windows\System32",
        )
        assert new_value == r"C:\app"
        assert removed == ["c:\\windows\\SYSTEM32"]

    def test_placeholder_matches_expanded_form(self):
        """ユーザー側のプレースホルダー表記がシステム側の展開済み表記と一致する場合は削除する。"""
        new_value, removed = cleanup_user_path._filter_user_path(
            user_value=r"%USERPROFILE%\foo;C:\other",
            system_value=r"C:\Users\test\foo",
        )
        assert new_value == r"C:\other"
        assert removed == [r"%USERPROFILE%\foo"]

    def test_kept_entries_preserve_original_string(self):
        """残すエントリはプレースホルダーを含む元の文字列のまま保持する。"""
        new_value, removed = cleanup_user_path._filter_user_path(
            user_value=r"%USERPROFILE%\bar;C:\Windows\System32",
            system_value=r"C:\Windows\System32",
        )
        # %USERPROFILE% は展開されず、元の表記のまま残る。
        assert new_value == r"%USERPROFILE%\bar"
        assert removed == [r"C:\Windows\System32"]

    def test_no_duplicates_means_no_change(self):
        """重複が無ければ削除リストは空になる。"""
        new_value, removed = cleanup_user_path._filter_user_path(
            user_value=r"C:\app;C:\Users\test\bin",
            system_value=r"C:\Windows\System32",
        )
        assert new_value == r"C:\app;C:\Users\test\bin"
        assert not removed

    def test_empty_system_value(self):
        """システム側 PATH が空ならユーザー側はそのまま保たれる。"""
        new_value, removed = cleanup_user_path._filter_user_path(
            user_value=r"C:\app;C:\Users\test\bin",
            system_value="",
        )
        assert new_value == r"C:\app;C:\Users\test\bin"
        assert not removed


class TestRun:
    """`run()` のシナリオテスト。"""

    def test_non_windows_returns_false(self, monkeypatch: pytest.MonkeyPatch):
        """非 Windows では何もせず False を返す。"""
        monkeypatch.setattr(cleanup_user_path.sys, "platform", "linux")
        # winutils の関数が呼ばれないこと（呼ばれれば AttributeError で気づく）を確認するため
        # 差し替えはせずそのまま実行する。
        assert cleanup_user_path.run() is False

    def test_removes_duplicates_and_broadcasts(self, monkeypatch: pytest.MonkeyPatch):
        """重複ありのケースで書き戻しとブロードキャストを行う。"""
        monkeypatch.setattr(cleanup_user_path.sys, "platform", "win32")
        write_calls: list[tuple[str, str, int]] = []
        broadcast_calls: list[bool] = []
        monkeypatch.setattr(
            cleanup_user_path.winutils,
            "read_user_env_var",
            lambda name: (r"%USERPROFILE%\bar;C:\Windows\System32", _REG_EXPAND_SZ),
        )
        monkeypatch.setattr(
            cleanup_user_path.winutils,
            "read_system_env_var",
            lambda name: (r"C:\Windows\System32", _REG_EXPAND_SZ),
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

        assert cleanup_user_path.run() is True
        # プレースホルダー入りの元の文字列がそのまま保たれ、値型も維持される。
        assert write_calls == [("Path", r"%USERPROFILE%\bar", _REG_EXPAND_SZ)]
        assert broadcast_calls == [True]

    def test_no_duplicates_does_not_write(self, monkeypatch: pytest.MonkeyPatch):
        """重複が無いケースでは書き戻し・ブロードキャストを行わない。"""
        monkeypatch.setattr(cleanup_user_path.sys, "platform", "win32")
        write_calls: list[tuple[str, str, int]] = []
        broadcast_calls: list[bool] = []
        monkeypatch.setattr(
            cleanup_user_path.winutils,
            "read_user_env_var",
            lambda name: (r"C:\Users\test\bin", _REG_EXPAND_SZ),
        )
        monkeypatch.setattr(
            cleanup_user_path.winutils,
            "read_system_env_var",
            lambda name: (r"C:\Windows\System32", _REG_EXPAND_SZ),
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

        assert cleanup_user_path.run() is False
        assert not write_calls
        assert not broadcast_calls

    def test_user_path_empty_returns_false(self, monkeypatch: pytest.MonkeyPatch):
        """ユーザー側 PATH 自体が空ならシステム側読み込みすら行わず False を返す。"""
        monkeypatch.setattr(cleanup_user_path.sys, "platform", "win32")

        def _fail(name: str) -> tuple[str | None, int]:
            raise AssertionError("system 側読み込みは呼ばれてはならない")

        monkeypatch.setattr(
            cleanup_user_path.winutils,
            "read_user_env_var",
            lambda name: (None, _REG_SZ),
        )
        monkeypatch.setattr(cleanup_user_path.winutils, "read_system_env_var", _fail)
        assert cleanup_user_path.run() is False

    def test_system_read_failure_logs_warning(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
        """システム側読み込み失敗時は警告ログを出して False を返し、書き戻しは行わない。"""
        monkeypatch.setattr(cleanup_user_path.sys, "platform", "win32")
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

        with caplog.at_level("WARNING", logger=cleanup_user_path.logger.name):
            assert cleanup_user_path.run() is False
        assert not write_calls
        assert any("システム側 PATH の読み込みに失敗" in record.getMessage() for record in caplog.records)
