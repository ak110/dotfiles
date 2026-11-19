"""模擬リポジトリのテストが共有する設定の読み込み。"""

import pathlib
import tomllib


def load_sample_config() -> dict:
    """settings.tomlを読み、既定値を補った設定を返す。"""
    config = tomllib.loads((pathlib.Path(__file__).parent / "settings.toml").read_text(encoding="utf-8"))
    config.setdefault("retries", 3)
    return config
