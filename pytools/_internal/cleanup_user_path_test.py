"""pytools._internal.cleanup_user_path のテスト。

`run()` 経由でプレースホルダー化・重複除外・存在チェック警告・値型昇格・
レジストリ I/O・ブロードキャスト連動・非 Windows 早期 return を検証する。
レジストリ I/O は monkeypatch で `winutils` 関数を差し替え、Linux 上でも実行可能にする。
"""

import pytest

from pytools._internal import cleanup_user_path

# `winreg.REG_EXPAND_SZ` / `REG_SZ` の値。Linux 上では `winreg` をインポートできないため、
# Windows 上の定数値を直接埋め込んでテストの再現性を確保する。
_REG_SZ = 1
_REG_EXPAND_SZ = 2

# テストで固定する %USERPROFILE% / %LOCALAPPDATA% / %APPDATA% 展開値。
_USERPROFILE = r"C:\Users\test"
_LOCALAPPDATA = r"C:\Users\test\AppData\Local"
_APPDATA = r"C:\Users\test\AppData\Roaming"


@pytest.fixture(autouse=True)
def _userprofile(monkeypatch: pytest.MonkeyPatch) -> None:
    """テスト中の `%USERPROFILE%` / `%LOCALAPPDATA%` / `%APPDATA%` 展開を固定値にする。"""
    monkeypatch.setenv("USERPROFILE", _USERPROFILE)
    monkeypatch.setenv("LOCALAPPDATA", _LOCALAPPDATA)
    monkeypatch.setenv("APPDATA", _APPDATA)


class TestFilterUserPath:
    """`run()` 経由でシステム側との重複除外・表記正規化の動作を検証する。

    テストデータは USERPROFILE 配下以外のパスを使い、プレースホルダー化との干渉を避ける。
    """

    def test_separator_direction_is_absorbed(self, monkeypatch: pytest.MonkeyPatch):
        """区切り文字の向き (`\\` vs `/`) は同一視して除外する。"""
        write_calls, _ = _stub_winutils(
            monkeypatch,
            user_value=r"C:/Windows/System32;D:\mybin",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )
        assert cleanup_user_path.run() is True
        assert write_calls == [("Path", r"D:\mybin", _REG_EXPAND_SZ)]

    def test_trailing_separator_is_absorbed(self, monkeypatch: pytest.MonkeyPatch):
        """末尾の区切り文字の有無は同一視して除外する。"""
        write_calls, _ = _stub_winutils(
            monkeypatch,
            user_value=r"C:\Windows\System32\;D:\app",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )
        assert cleanup_user_path.run() is True
        assert write_calls == [("Path", r"D:\app", _REG_EXPAND_SZ)]

    def test_case_difference_is_absorbed(self, monkeypatch: pytest.MonkeyPatch):
        """大文字小文字差は同一視して除外する。"""
        write_calls, _ = _stub_winutils(
            monkeypatch,
            user_value=r"c:\windows\SYSTEM32;D:\app",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )
        assert cleanup_user_path.run() is True
        assert write_calls == [("Path", r"D:\app", _REG_EXPAND_SZ)]

    def test_placeholder_matches_expanded_form(self, monkeypatch: pytest.MonkeyPatch):
        """ユーザー側のプレースホルダー表記がシステム側の展開済み表記と一致する場合は除外する。"""
        write_calls, _ = _stub_winutils(
            monkeypatch,
            user_value=r"%USERPROFILE%\foo;D:\other",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Users\test\foo",
        )
        assert cleanup_user_path.run() is True
        assert write_calls == [("Path", r"D:\other", _REG_EXPAND_SZ)]

    def test_kept_entries_preserve_original_placeholder_string(self, monkeypatch: pytest.MonkeyPatch):
        """残すエントリーはプレースホルダーを含む元の文字列のまま保持する。"""
        write_calls, _ = _stub_winutils(
            monkeypatch,
            user_value=r"%USERPROFILE%\bar;C:\Windows\System32",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )
        # C:\Windows\System32 はシステム側と重複して除外される。
        # %USERPROFILE%\bar は展開されず元の表記のまま残る。
        assert cleanup_user_path.run() is True
        assert write_calls == [("Path", r"%USERPROFILE%\bar", _REG_EXPAND_SZ)]

    def test_no_duplicates_means_no_change(self, monkeypatch: pytest.MonkeyPatch):
        """重複が無ければ書き戻しは行わない。"""
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=r"D:\app;D:\mybin",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )
        assert cleanup_user_path.run() is False
        assert not write_calls
        assert not broadcast_calls

    def test_empty_system_value(self, monkeypatch: pytest.MonkeyPatch):
        """システム側 PATH が空ならユーザー側はそのまま保たれる。"""
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=r"D:\app;D:\mybin",
            user_reg_type=_REG_EXPAND_SZ,
            system_value="",
        )
        assert cleanup_user_path.run() is False
        assert not write_calls
        assert not broadcast_calls


class TestReplacePlaceholders:
    """`run()` 経由でプレースホルダー化ロジックを検証する。

    システム側 PATH と重複しないエントリーを渡し、write_calls の書き戻し値で置換結果を確認する。
    """

    @pytest.mark.parametrize(
        ("entry", "expected"),
        [
            # (a) USERPROFILE 直下の置換。
            (r"C:\Users\test\Documents", r"%USERPROFILE%\Documents"),
            # (b) LOCALAPPDATA 直下は USERPROFILE より優先される (最長一致)。
            (r"C:\Users\test\AppData\Local\Programs", r"%LOCALAPPDATA%\Programs"),
            # (c) APPDATA 直下も USERPROFILE より優先される。
            (r"C:\Users\test\AppData\Roaming\Code", r"%APPDATA%\Code"),
            # (d) 既存プレースホルダー入りエントリーも展開後に再判定して最適化する。
            (r"%USERPROFILE%\AppData\Local\foo", r"%LOCALAPPDATA%\foo"),
            # (e) 末尾完全一致 (区切り無し終端) は環境変数単体に置換する。
            (r"C:\Users\test", r"%USERPROFILE%"),
            # (g) 大文字小文字差は吸収して置換する。
            (r"c:\USERS\Test\Documents", r"%USERPROFILE%\Documents"),
            # (h) 区切り方向差 (`/`) は PureWindowsPath が吸収する。
            (r"C:/Users/test/Documents", r"%USERPROFILE%\Documents"),
        ],
    )
    def test_replaces_with_longest_match(self, monkeypatch: pytest.MonkeyPatch, entry: str, expected: str):
        """境界値・順序優先・原型維持を1ケースずつ確認する。"""
        write_calls, _ = _stub_winutils(
            monkeypatch,
            user_value=entry,
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )
        assert cleanup_user_path.run() is True
        assert write_calls[0][1] == expected

    def test_no_match_entry_is_unchanged(self, monkeypatch: pytest.MonkeyPatch):
        """(j) 置換対象が無いエントリーはそのまま返す（書き戻しなし）。"""
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=r"D:\Tools\bin",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )
        # プレースホルダー置換なし・重複除外なし → 書き戻しなし
        assert cleanup_user_path.run() is False
        assert not write_calls
        assert not broadcast_calls

    def test_skips_undefined_environment_variable(self, monkeypatch: pytest.MonkeyPatch):
        """(i) LOCALAPPDATA 未定義時は USERPROFILE の前方一致にフォールバックする。"""
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        write_calls, _ = _stub_winutils(
            monkeypatch,
            user_value=r"C:\Users\test\AppData\Local\foo",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )
        assert cleanup_user_path.run() is True
        # LOCALAPPDATA が欠落しているため USERPROFILE で前方一致
        assert write_calls[0][1] == r"%USERPROFILE%\AppData\Local\foo"

    def test_empty_env_map_leaves_entry_unchanged(self, monkeypatch: pytest.MonkeyPatch):
        """env_map が空（全環境変数未定義）の場合、エントリーは置換されない。"""
        monkeypatch.delenv("USERPROFILE", raising=False)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.delenv("APPDATA", raising=False)
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=r"C:\Users\test\foo",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )
        # プレースホルダー置換なし・重複除外なし → 書き戻しなし
        assert cleanup_user_path.run() is False
        assert not write_calls
        assert not broadcast_calls


class TestCollectUserprofileEnv:
    """`run()` 経由で環境変数収集の順序と空値スキップを検証する。"""

    def test_returns_longest_first_order(self, monkeypatch: pytest.MonkeyPatch):
        """LOCALAPPDATA が USERPROFILE より優先されてプレースホルダー化される（最長一致）。"""
        write_calls, _ = _stub_winutils(
            monkeypatch,
            user_value=r"C:\Users\test\AppData\Local\Programs",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )
        assert cleanup_user_path.run() is True
        # LOCALAPPDATA が USERPROFILE より前に評価されるため、LOCALAPPDATA に置換される
        assert write_calls[0][1] == r"%LOCALAPPDATA%\Programs"

    def test_skips_empty_or_missing_variables(self, monkeypatch: pytest.MonkeyPatch):
        """空値・未定義の変数はプレースホルダー化に使われない。"""
        monkeypatch.setenv("LOCALAPPDATA", "")  # 空値は除外される
        monkeypatch.delenv("APPDATA", raising=False)
        write_calls, _ = _stub_winutils(
            monkeypatch,
            user_value=r"C:\Users\test\AppData\Local\foo",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )
        assert cleanup_user_path.run() is True
        # LOCALAPPDATA が欠落しているため USERPROFILE で前方一致
        assert write_calls[0][1] == r"%USERPROFILE%\AppData\Local\foo"


class TestFindMissingPaths:
    """`run()` 経由でパス存在チェックの動作を検証する。

    `_stub_winutils` の `stub_find_missing=False` で実コードの存在チェックを有効にして検証する。
    """

    def test_existing_entry_is_not_reported(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        """存在するパスは警告ログを出力しない。"""
        existing = tmp_path / "bin"
        existing.mkdir()
        # プレースホルダー化で変換されないよう Linux パス形式（tmp_path）をそのまま使う。
        # ただし run() は win32 環境を想定するため、存在チェックは path.exists() で通る。
        _stub_winutils(
            monkeypatch,
            user_value=str(existing),
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
            stub_find_missing=False,
        )
        # 存在するパスなので書き戻しなし（プレースホルダー化不要・重複なし）
        assert cleanup_user_path.run() is False

    def test_missing_entry_emits_warning(self, monkeypatch: pytest.MonkeyPatch, tmp_path, caplog: pytest.LogCaptureFixture):
        """存在しないパスは警告ログを出力し、書き戻しは行わない。"""
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        _stub_winutils(
            monkeypatch,
            user_value=r"%USERPROFILE%\missing",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
            stub_find_missing=False,
        )

        with caplog.at_level("WARNING", logger=cleanup_user_path.logger.name):
            assert cleanup_user_path.run() is False
        assert any("ユーザー PATH に存在しないエントリーを検出" in record.getMessage() for record in caplog.records)

    def test_unresolved_placeholder_is_skipped(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
        """展開後に % が残るエントリーは判定不能としてスキップし、警告ログを出力しない。"""
        _stub_winutils(
            monkeypatch,
            user_value=r"%UNDEFINED_VAR%\foo",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
            stub_find_missing=False,
        )

        with caplog.at_level("WARNING", logger=cleanup_user_path.logger.name):
            cleanup_user_path.run()
        # % 残留エントリーはスキップされるため存在チェック警告は出ない
        assert not any("存在しないエントリーを検出" in record.getMessage() for record in caplog.records)


class TestRun:
    """`run()` のシナリオテスト。"""

    def test_non_windows_returns_false(self, monkeypatch: pytest.MonkeyPatch):
        """非 Windows では何もせず False を返す。"""
        monkeypatch.setattr(cleanup_user_path.sys, "platform", "linux")
        # winutils の関数が呼ばれないこと（呼ばれれば AttributeError で気づく）を確認するため
        # 差し替えはせずそのまま実行する。
        assert cleanup_user_path.run() is False

    def test_removes_duplicates_and_broadcasts(self, monkeypatch: pytest.MonkeyPatch):
        """重複ありのケースで書き戻しとブロードキャストを実行する。"""
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=r"%USERPROFILE%\bar;C:\Windows\System32",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )

        assert cleanup_user_path.run() is True
        # プレースホルダー入りの元の文字列がそのまま保たれ、値型も維持される。
        assert write_calls == [("Path", r"%USERPROFILE%\bar", _REG_EXPAND_SZ)]
        assert broadcast_calls == [True]

    def test_no_duplicates_does_not_write(self, monkeypatch: pytest.MonkeyPatch):
        """重複が無いケースでは書き戻し・ブロードキャストを行わない。"""
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=r"%USERPROFILE%\bin",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
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
        """システム側読み込み失敗時は警告ログを表示して False を返し、書き戻しは行わない。"""
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

    def test_placeholder_replacement_and_dedup_together(self, monkeypatch: pytest.MonkeyPatch):
        """プレースホルダー化と重複除外が同時に発火するケース。"""
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=rf"{_USERPROFILE}\foo;C:\Windows\System32",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )

        assert cleanup_user_path.run() is True
        assert write_calls == [("Path", r"%USERPROFILE%\foo", _REG_EXPAND_SZ)]
        assert broadcast_calls == [True]

    def test_placeholder_only_triggers_write(self, monkeypatch: pytest.MonkeyPatch):
        """プレースホルダー化のみで書き戻す (重複・存在しないパスは無し)。"""
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=rf"{_USERPROFILE}\foo",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )

        assert cleanup_user_path.run() is True
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

        assert cleanup_user_path.run() is True
        assert write_calls == [("Path", r"%USERPROFILE%\foo", _REG_EXPAND_SZ)]
        assert broadcast_calls == [True]

    def test_reg_type_is_preserved_without_placeholder(self, monkeypatch: pytest.MonkeyPatch):
        """書き戻し値に % が含まれなければ元の reg_type を維持する。"""
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=r"C:\app;C:\Windows\System32",
            user_reg_type=_REG_SZ,
            system_value=r"C:\Windows\System32",
        )

        assert cleanup_user_path.run() is True
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

        assert cleanup_user_path.run() is True
        # 置換・除外は発生しないが値型昇格のみで書き戻される。
        assert write_calls == [("Path", r"%USERPROFILE%\bin", _REG_EXPAND_SZ)]
        assert broadcast_calls == [True]

    def test_missing_path_emits_warning_without_write(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
        """存在しないパス検出時は警告ログのみで書き戻しは行わない。"""
        write_calls, broadcast_calls = _stub_winutils(
            monkeypatch,
            user_value=r"%USERPROFILE%\missing",
            user_reg_type=_REG_EXPAND_SZ,
            system_value=r"C:\Windows\System32",
        )
        # 既定の Path.exists() は Linux 上で常に False を返すため、ここで明示的に False 固定する。
        monkeypatch.setattr(
            cleanup_user_path,
            "_find_missing_paths",
            lambda entries: [(r"%USERPROFILE%\missing", rf"{_USERPROFILE}\missing")],
        )

        with caplog.at_level("WARNING", logger=cleanup_user_path.logger.name):
            assert cleanup_user_path.run() is False
        assert not write_calls
        assert not broadcast_calls
        assert any("ユーザー PATH に存在しないエントリーを検出" in record.getMessage() for record in caplog.records)


def _stub_winutils(
    monkeypatch: pytest.MonkeyPatch,
    *,
    user_value: str,
    user_reg_type: int,
    system_value: str,
    stub_find_missing: bool = True,
) -> tuple[list[tuple[str, str, int]], list[bool]]:
    """`run()` 用に `winutils` の入出力を差し替える。

    Args:
        stub_find_missing: True の場合、`_find_missing_paths` を「常に空を返す」差し替えに設定する。
            False の場合、実コードの存在チェックをそのまま使う。

    Returns:
        `(write_calls, broadcast_calls)` の参照。テスト本体で副作用を検証する。
    """
    monkeypatch.setattr(cleanup_user_path.sys, "platform", "win32")
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
        # 既定では存在チェック警告を抑止する (テストごとに必要なら明示的に差し替える)。
        monkeypatch.setattr(cleanup_user_path, "_find_missing_paths", lambda entries: [])
    return write_calls, broadcast_calls
