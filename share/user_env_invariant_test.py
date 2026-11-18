"""`share/user.env`が、bashとpost-applyのPythonの工程が同じ値を得る書式に従うことを確かめる不変条件テスト。

書式は`share/user.env`の冒頭のコメントと`pytools/_internal/setup_user_env.py`が定める。
"""

from pathlib import Path

from pytools._internal import setup_user_env


def test_user_env_follows_shared_format() -> None:
    """現行の`share/user.env`の全行が書式に従う。"""
    text = (Path(__file__).resolve().parent / "user.env").read_text(encoding="utf-8")
    # 有効な行が無い状態も許す。書式に反する行があれば`parse_user_env`が`ValueError`を送出する。
    setup_user_env.parse_user_env(text)
