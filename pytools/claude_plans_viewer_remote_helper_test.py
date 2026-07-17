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
