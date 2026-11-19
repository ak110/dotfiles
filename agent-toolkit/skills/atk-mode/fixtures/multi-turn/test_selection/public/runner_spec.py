"""プラグインの別プロセス実行を確かめる。"""

import runner  # ty: ignore[import-not-found]  # 模擬リポジトリは同じディレクトリのモジュールを実行時のsys.pathで解決する


def test_runner_invokes_plugin() -> None:
    """子プロセスの出力を返す。"""
    assert runner.run_plugin("plugin_csv", "a, b\n") == "a|b\n"
