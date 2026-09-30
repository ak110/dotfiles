"""pytest conftest: スキル配下のテストへキュー管理リポジトリの隔離を提供する。"""

import pathlib

import pytest


@pytest.fixture(autouse=True)
def _atk_private_notes_env(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`atk wi`・`atk plans`用の管理repo rootをテスト用一時ディレクトリへ差し替える。

    解決処理は`AGENT_TOOLKIT_PRIVATE_NOTES`が未設定だと実行環境の`~/private-notes/`を読むため、
    開発機とCIで実物の有無により結果が変わる。pytestは祖先ディレクトリのconftestだけを読み、
    `agent-toolkit/agent_toolkit/conftest.py`の同名fixtureはスキル配下へ適用されないため、
    本ファイルで同じ隔離を与える。テスト内で環境変数を設定した場合はその値が優先する。
    """
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / "private-notes"))
