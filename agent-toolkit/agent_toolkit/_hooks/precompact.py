"""組織ガード下でも、原本を参照する短い圧縮要約を依頼する。"""

import json

from agent_toolkit._common.session_record_paths import find_claude_records


def main(payload_text: str) -> int:
    payload = json.loads(payload_text)
    if not isinstance(payload, dict) or payload.get("agent_id") is not None:
        return 0
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return 0
    # 原本の案内はClaude Codeの記録が1件に定まる会話に限るため、他の実行系の記録は探さない。
    records = find_claude_records(session_id)
    if len(records) != 1 or not records[0].is_file():
        return 0
    # PreCompactのstdoutはモデルへの要約指示であり、要約API呼出し自体を省く契約ではない。
    print(
        "圧縮後は原本記録から復元できるため、要約は次の復元案内を中心に短くせよ。"
        "要約にはSessionStartの復元通知に従う旨を残せ。"
        "未保存の判断・要求・承認・未回収の実行識別子がある場合は、その必要情報を省かず残せ。"
    )
    return 0
