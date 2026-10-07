"""`atk wi`のCLIが受け取る位置引数の解釈のテスト。"""

import pathlib

from agent_toolkit._atk.wi import cli_input as _wi_cli_input


class TestIsExistingDir:
    """長大な文字列候補に対する`is_existing_dir`のOSError耐性を検証する。"""

    def test_returns_false_for_oversized_name_without_raising(self) -> None:
        """OS上限を超える長さの文字列でも`OSError`を送出せずFalseを返す。"""
        oversized = pathlib.Path("x" * 5000)

        assert _wi_cli_input.is_existing_dir(oversized) is False
