"""pytools._internal.cleanup_user_path のテスト。

純粋関数 (`_filter_user_path` / `_normalize_entry` / `_replace_placeholders` /
`_find_missing_paths`) の挙動と、`run()` のレジストリ I/O・ブロードキャスト連動・
非 Windows 早期 return・プレースホルダー化・存在チェック警告・値型昇格を検証する。
レジストリ I/O は monkeypatch で `winutils` 関数を差し替え、Linux 上でも実行可能にする。
"""

# 純粋関数群を直接テストするため protected-access を一括許可する。
# pylint: disable=protected-access

import ntpath

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
        """残すエントリーはプレースホルダーを含む元の文字列のまま保持する。"""
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


# テスト用の標準環境マップ。最長一致優先で LOCALAPPDATA → APPDATA → USERPROFILE の順。
_STD_ENV_MAP: dict[str, str] = {
    "%LOCALAPPDATA%": _LOCALAPPDATA,
    "%APPDATA%": _APPDATA,
    "%USERPROFILE%": _USERPROFILE,
}


class TestReplacePlaceholders:
    """`_replace_placeholders` のプレースホルダー化ロジック。"""

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
            # (f) プレフィックス類似だが境界が異なるパスは非マッチで原型維持。
            (r"C:\Users\testtest\foo", r"C:\Users\testtest\foo"),
            # (g) 大文字小文字差は吸収して置換する。
            (r"c:\USERS\Test\Documents", r"%USERPROFILE%\Documents"),
            # (h) 区切り方向差 (`/`) は PureWindowsPath が吸収する。
            (r"C:/Users/test/Documents", r"%USERPROFILE%\Documents"),
            # (j) 置換対象が無いエントリーはそのまま返す。
            (r"D:\Tools\bin", r"D:\Tools\bin"),
        ],
    )
    def test_replaces_with_longest_match(self, entry: str, expected: str):
        """境界値・順序優先・原型維持を1ケースずつ確認する。"""
        assert cleanup_user_path._replace_placeholders(entry, _STD_ENV_MAP) == expected

    def test_skips_undefined_environment_variable(self):
        """(i) 環境変数未定義時は当該プレースホルダーをスキップする。"""
        # LOCALAPPDATA を欠落させた env_map では C:\Users\test\AppData\Local\foo が
        # USERPROFILE の前方一致にフォールバックする。
        env_map = {
            "%APPDATA%": _APPDATA,
            "%USERPROFILE%": _USERPROFILE,
        }
        assert (
            cleanup_user_path._replace_placeholders(r"C:\Users\test\AppData\Local\foo", env_map)
            == r"%USERPROFILE%\AppData\Local\foo"
        )

    def test_empty_env_map_returns_original(self):
        """env_map が空ならいかなるエントリーも置換されない。"""
        assert cleanup_user_path._replace_placeholders(r"C:\Users\test\foo", {}) == r"C:\Users\test\foo"


class TestCollectUserprofileEnv:
    """`_collect_userprofile_env` の収集順序と空値スキップ。"""

    def test_returns_longest_first_order(self):
        """LOCALAPPDATA → APPDATA → USERPROFILE の順で並ぶ。"""
        result = cleanup_user_path._collect_userprofile_env(
            {
                "USERPROFILE": _USERPROFILE,
                "APPDATA": _APPDATA,
                "LOCALAPPDATA": _LOCALAPPDATA,
            }
        )
        assert list(result.items()) == [
            ("%LOCALAPPDATA%", _LOCALAPPDATA),
            ("%APPDATA%", _APPDATA),
            ("%USERPROFILE%", _USERPROFILE),
        ]

    def test_skips_empty_or_missing_variables(self):
        """空値・未定義の変数は結果に含まれない。"""
        result = cleanup_user_path._collect_userprofile_env(
            {
                "USERPROFILE": _USERPROFILE,
                "LOCALAPPDATA": "",
                # APPDATA は未定義。
            }
        )
        assert result == {"%USERPROFILE%": _USERPROFILE}


class TestFindMissingPaths:
    """`_find_missing_paths` の存在チェック判定。

    tmp_path 上に実ディレクトリを作成して `Path.exists()` 経由で判定する。
    プレースホルダー展開は `monkeypatch.setenv` で tmp_path を `%USERPROFILE%` に
    束縛し、`ntpath.expandvars` 経由でも tmp_path 配下のパスへ解決する。
    """

    def test_existing_entry_is_not_reported(self, tmp_path):
        """(k) 存在するパスは戻り値に含まれない。"""
        existing = tmp_path / "bin"
        existing.mkdir()
        assert not cleanup_user_path._find_missing_paths([str(existing)])

    def test_missing_entry_returns_original_and_expanded(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        """(l) 存在しないパスは (元エントリー, 展開後パス) で返る。"""
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        # missing ディレクトリは作成しない。
        expected_expanded = ntpath.expandvars(r"%USERPROFILE%\missing")
        result = cleanup_user_path._find_missing_paths([r"%USERPROFILE%\missing"])
        assert result == [(r"%USERPROFILE%\missing", expected_expanded)]

    def test_unresolved_placeholder_is_skipped(self):
        """(m) 展開後に % が残るエントリーは判定不能としてスキップする。"""
        assert not cleanup_user_path._find_missing_paths([r"%UNDEFINED_VAR%\foo"])


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
) -> tuple[list[tuple[str, str, int]], list[bool]]:
    """`run()` 用に `winutils` の入出力と存在チェックを既定値で差し替える。

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
    # 既定では存在チェック警告を抑止する (テストごとに必要なら明示的に差し替える)。
    monkeypatch.setattr(cleanup_user_path, "_find_missing_paths", lambda entries: [])
    return write_calls, broadcast_calls
