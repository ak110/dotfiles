"""`atk wi add`のテストが共有するキュー項目の本文。

`source`を持つ通常AWIは投入時に必須H2の全件が規定順序に並ぶかを判定されるため、
投入の成功を前提とするテストは同じ最小本文を共有する。
テストごとに本文を組み立てると、必須H2を変える改訂のたびに全テストを書き直すことになる。
"""

AGENT_AWI_SECTIONS = "## 反映内容と反映先\n対象と反映先"
"""`source`を持つ通常AWIが必須とするH2を規定順序で並べた最小の節群。"""

AGENT_AWI_BODY = f"本文\n\n{AGENT_AWI_SECTIONS}"
"""投入が成立する`source`付き通常AWIの最小本文。"""


__all__ = ["AGENT_AWI_BODY", "AGENT_AWI_SECTIONS"]
