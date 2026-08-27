"""pytools.claude_plans_viewer._remote_helper のテスト。

リモートホスト側ヘルパーはSSH経由でファイル内容ごと転送・実行される運用のため、
既存のリモート連携テスト（`claude_plans_viewer_remote_watcher_test.py`等）は
手組みJSONペイロードの注入で`RemoteWatcher`側の受信処理のみを検証しており、
本ファイル自身の関数`_host_info`を直接importして検証するテストが無かった。
`_host_info`の`root`パス区切り正規化漏れ（Windowsリモートホストで`\\`混入）はこの欠落により
自動テストで検出できなかったため、本ファイルで直接契約を固定する。
`_ctime_epoch`は`os.stat_result`の`st_birthtime`有無というプラットフォーム依存の分岐を持ち、
既存の`claude_plans_viewer_test.py`側でも同様の理由で直接テストせず関数差し替えで扱っているため、
本ファイルでも同じ方針を踏襲し対象外とする。
"""

import os
import pathlib

import pytest

from pytools.claude_plans_viewer import _remote_helper


class TestHostInfo:
    """`_host_info`の戻り値契約を検証する。"""

    def test_root_uses_forward_slash_regardless_of_platform(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`root`は`_local.local_host_info`と同様に`/`区切りへ正規化される。

        Windows実機無しでバックスラッシュ混入経路を再現するため、`ROOT.resolve()`が
        バックスラッシュ区切り文字列を返す状況を`pathlib.PureWindowsPath`スタブで模擬する。
        """

        class _FakeRoot:
            def resolve(self) -> pathlib.PureWindowsPath:
                return pathlib.PureWindowsPath(r"C:\Users\example\.claude\plans")

        monkeypatch.setattr(_remote_helper, "ROOT", _FakeRoot())

        info = _remote_helper._host_info()  # pylint: disable=protected-access  # noqa: SLF001  # モジュール内部契約を直接固定するテストのため

        assert "\\" not in info["root"]
        assert info["root"] == "C:/Users/example/.claude/plans"

    def test_os_type_and_os_name_reflect_current_platform(self) -> None:
        """`os_type`・`os_name`は現在の`os.name`値をそのまま反映する。"""
        info = _remote_helper._host_info()  # pylint: disable=protected-access  # noqa: SLF001  # モジュール内部契約を直接固定するテストのため

        assert info["os_type"] == os.name
        assert info["os_name"] == os.name

    def test_home_matches_pathlib_home_with_forward_slash(self) -> None:
        """`home`は`pathlib.Path.home()`を`/`区切りへ正規化した値と一致する。"""
        info = _remote_helper._host_info()  # pylint: disable=protected-access  # noqa: SLF001  # モジュール内部契約を直接固定するテストのため

        assert "\\" not in info["home"]
        assert info["home"] == str(pathlib.Path.home()).replace("\\", "/")


class TestListedPath:
    """`_is_target_path`・`_is_listed_path`・`_scan_entries`の付属計画除外契約を検証する。"""

    def test_detail_file_target_but_not_listed(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """`.detail.md`・`.bugs.md`は対象だが一覧には載らない。"""
        monkeypatch.setattr(_remote_helper, "ROOT", tmp_path)
        main = tmp_path / "plan.md"
        detail = tmp_path / "plan.detail.md"
        bugs = tmp_path / "plan.bugs.md"
        main.write_text("x", encoding="utf-8")
        detail.write_text("x", encoding="utf-8")
        bugs.write_text("x", encoding="utf-8")

        for attached in (detail, bugs):
            assert _remote_helper._is_target_path(attached)  # pylint: disable=protected-access  # noqa: SLF001  # モジュール内部契約を直接固定するテストのため
            assert not _remote_helper._is_listed_path(attached)  # pylint: disable=protected-access  # noqa: SLF001  # モジュール内部契約を直接固定するテストのため
        assert _remote_helper._is_listed_path(main)  # pylint: disable=protected-access  # noqa: SLF001  # モジュール内部契約を直接固定するテストのため

    def test_scan_entries_excludes_detail_file(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """`_scan_entries`（一覧経路）は付属計画を返さない。"""
        monkeypatch.setattr(_remote_helper, "ROOT", tmp_path)
        monkeypatch.setattr(_remote_helper, "_CREATION_TIME_INDEX_PATH", tmp_path / "creation-times" / "index.json")
        (tmp_path / "plan.md").write_text("x", encoding="utf-8")
        (tmp_path / "plan.detail.md").write_text("x", encoding="utf-8")
        (tmp_path / "plan.bugs.md").write_text("x", encoding="utf-8")

        entries = _remote_helper._scan_entries()  # pylint: disable=protected-access  # noqa: SLF001  # モジュール内部契約を直接固定するテストのため

        assert {entry["path"] for entry in entries} == {"plan.md"}
