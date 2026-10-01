"""スキル配下のテストへキュー管理リポジトリの隔離が適用されることを確かめる。"""

import os
import pathlib


def test_private_notes_env_points_to_tmp_path(tmp_path: pathlib.Path) -> None:
    """テストが設定しなくても、管理repo rootは実物ではなくテスト固有の一時ディレクトリを指す。

    隔離が適用されないと、private-notesを持つ開発機では実物を読み、持たないCIでは指定を省略した場合の位置を読むため、
    同じテストの結果が実行環境で変わる。
    """
    assert os.environ["AGENT_TOOLKIT_PRIVATE_NOTES"] == str(tmp_path / "private-notes")
