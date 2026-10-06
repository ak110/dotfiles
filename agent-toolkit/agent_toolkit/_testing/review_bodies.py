"""`atk review-table respond`のテストが共有する応答本文。

`respond`は応答本文が必須ラベルを行頭に持つかを判定するため、記録の成功を前提とするテストは
同じ形の本文を共有する。テストごとに本文を組み立てると、必須ラベルを変える改訂のたびに全テストを書き直すことになる。
"""


def response_body(summary: str) -> str:
    """`--response-file`の必須ラベルを全て持ち、`summary`を修正範囲として含む対応内容を返す。"""
    return (
        "違反を確認した規定: 指摘が示した規定\n"
        "同じ規定の検索: 変更対象の全体を確認し、指摘箇所以外の該当は無い\n"
        f"採用する修正範囲: {summary}\n"
        "採用しない修正方針: なし"
    )


def reason_body(summary: str) -> str:
    """`--no-response-reason-file`の必須ラベルを持ち、`summary`を根拠の所在として含む対応不要理由を返す。"""
    return f"根拠の所在: {summary}"
