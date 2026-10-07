"""証拠抽出のテストが共有するfixtureを提供する。"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator

import pytest


@pytest.fixture(name="local_time_jst")
def _local_time_jst() -> Iterator[None]:
    """ローカルタイムゾーンをUTC以外のJST（UTC+9）へ固定し、テスト後に元へ戻す。

    タイムゾーンを省いた時刻の解釈を、テストを実行するホストのタイムゾーン設定に依存させないため。
    """
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "JST-9"
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()
