"""集計結果の辞書を組み立てる。"""


def build_report(rows: list[str]) -> dict:
    """行の一覧から集計結果を返す。"""
    return {"name": "rows", "count": len(rows)}
