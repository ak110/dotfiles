def issue_body(summary: str, classification: str | None = None) -> str:
    lines = [summary, "類似見直し完了件数: 3", "検収: 修正後の版で該当箇所の契約を確かめる"]
    if classification is not None:
        lines.append(f"区分: {classification}")
    return "\n".join(lines)


def response_body(summary: str, *, acceptance: str | None = None) -> str:
    body = f"違反を確認した規定: 指摘が示した規定\n類似見直し完了件数: 3\n採用する修正範囲: {summary}\n採用しない修正方針: なし"
    return body if acceptance is None else f"{body}\n検収結果: {acceptance}"


def reason_body(summary: str) -> str:
    return f"根拠の所在: {summary}\n判断主体: メイン"
