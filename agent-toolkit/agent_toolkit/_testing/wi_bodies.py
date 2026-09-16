"""`atk wi add`の検体が共有するキュー項目の本文。

`source`を持つ通常AWIは投入時に必須H2の全件と規定順序を検査されるため、
投入の成功を前提とする検体は同じ最小本文を共有する。
検体ごとに本文を組み立てると、必須H2を変える改訂のたびに全検体を書き直すことになる。
"""

AGENT_AWI_SECTIONS = (
    "## 反映内容と反映先\n対象と反映先\n\n"
    "## 適用範囲\n誤りの機構が依存する条件\n\n"
    "## 実現性\n対象実装を確認済み\n\n"
    "## メリット\n効果\n\n"
    "## デメリット\nなし\n\n"
    "## 完成条件\n外部可視の終了状態"
)
"""`source`を持つ通常AWIが必須とするH2を規定順序で並べた最小の節群。"""

AGENT_AWI_BODY = f"本文\n\n{AGENT_AWI_SECTIONS}"
"""投入が成立する`source`付き通常AWIの最小本文。"""


def agent_awi_body_without(*headings: str) -> str:
    """指定した必須H2だけを取り除いた`source`付き通常AWIの本文を返す。"""
    kept = [
        section for section in AGENT_AWI_SECTIONS.split("\n\n") if section.splitlines()[0].removeprefix("## ") not in headings
    ]
    return "本文\n\n" + "\n\n".join(kept)


__all__ = ["AGENT_AWI_BODY", "AGENT_AWI_SECTIONS", "agent_awi_body_without"]
