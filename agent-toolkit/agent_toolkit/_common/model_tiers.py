"""実モデルIDに含まれる系列名から、モデルの段を比較する。"""

MODEL_TIERS = (("haiku", "luna"), ("sonnet", "terra"), ("opus", "sol"), ("fable", "astra"))


def model_tier(model: str) -> int | None:
    """既知の系列を含むモデルの段を返す。未知のモデルは比較しない。"""
    return next((tier for tier, names in enumerate(MODEL_TIERS) if any(name in model.lower() for name in names)), None)


def is_higher_model(parent: str, child: str) -> bool:
    """両方が既知で、委譲元が上の段かを返す。"""
    parent_tier, child_tier = model_tier(parent), model_tier(child)
    return parent_tier is not None and child_tier is not None and parent_tier > child_tier
