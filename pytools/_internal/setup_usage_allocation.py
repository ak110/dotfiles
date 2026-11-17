"""dotfiles利用環境の利用枠自動配分を、未設定の場合に有効化する。"""

from agent_toolkit._atk import config_settings

from pytools._internal import post_apply_outcome


def run() -> post_apply_outcome.PostApplyOutcome:
    """明示設定を保持し、利用枠自動配分の未設定キーだけを補う。"""
    # 厳密読込により、破損した設定を空とみなして他用途の値を失うことを防ぐ。
    settings = config_settings.load_config_strict()
    defaults = {"usage_auto_allocation": "true", "usage_activity_mode": "intermittent"}
    missing = {key: value for key, value in defaults.items() if key not in settings}
    if not missing:
        return post_apply_outcome.PostApplyOutcome()
    settings.update(missing)
    config_settings.save_config(settings)
    return post_apply_outcome.PostApplyOutcome(changed=True)
