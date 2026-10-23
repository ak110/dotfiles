"""engineごとの利用上限で、同じ接続の解除を待つかを決める。"""

from agent_toolkit._atk import config


def should_wait(engine: str, limit_type: str | None) -> bool:
    """利用上限の種類と設定から解除待機の方針を返す。"""
    if engine not in {"claude", "codex"}:
        raise ValueError(f"利用上限の待機方針がないengine: {engine}")
    policy = config.resolve_mutable_setting(f"{engine}_usage_limit_wait")
    return policy == "always" or (policy == "five-hour" and limit_type == "five_hour")
