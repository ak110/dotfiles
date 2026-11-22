AGENT_AWI_SECTIONS = "## 反映内容と反映先\n対象と反映先"
"""`source`を持つ通常AWIが必須とするH2を規定順序で並べた最小の節群。"""

AGENT_AWI_BODY = f"本文\n\n{AGENT_AWI_SECTIONS}"
"""投入が成立する`source`付き通常AWIの最小本文。"""


__all__ = ["AGENT_AWI_BODY", "AGENT_AWI_SECTIONS"]
