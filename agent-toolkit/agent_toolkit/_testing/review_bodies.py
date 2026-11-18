"""`atk review-table`のテストが共有する指摘本文と応答本文。

`add`は指摘本文、`respond`は応答本文が必須ラベルを行頭に持つかを判定するため、記録の成功を前提とするテストは
同じ形の本文を共有する。テストごとに本文を組み立てると、必須ラベルを変える改訂のたびに全テストを書き直すことになる。
"""


def issue_body(summary: str, classification: str | None = None) -> str:
    """CLIの`add`が求める指摘本文の必須ラベルを持ち、`summary`を指摘として含む本文を返す。

    2以上のroundへ登録する本文は`classification`へ区分の値を渡す。
    """
    lines = [summary, "類似見直し完了件数: 3"]
    if classification is not None:
        lines.append(f"区分: {classification}")
    return "\n".join(lines)


def response_body(summary: str) -> str:
    """`--response-file`の必須ラベルを全て持ち、`summary`を修正範囲として含む対応内容を返す。"""
    return f"違反を確認した規定: 指摘が示した規定\n類似見直し完了件数: 3\n採用する修正範囲: {summary}\n採用しない修正方針: なし"


def reason_body(summary: str) -> str:
    """`--no-response-reason-file`の必須ラベルを持ち、`summary`を根拠の所在として含む対応不要理由を返す。"""
    return f"根拠の所在: {summary}\n判断主体: メイン"
