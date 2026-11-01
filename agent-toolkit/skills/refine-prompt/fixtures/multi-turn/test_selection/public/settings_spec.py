"""設定の既定値を確かめる。"""

import spec_support  # ty: ignore[unresolved-import]  # 模擬リポジトリは同じディレクトリのモジュールを実行時のsys.pathで解決する


def test_timeout_default() -> None:
    """設定ファイルの値と補った既定値を読む。"""
    config = spec_support.load_sample_config()
    assert config["timeout"] == 10
    assert config["retries"] == 3
