"""組織ガード下でも、原本を参照する短い圧縮要約を依頼する。"""

import json

from agent_toolkit._common.session_record_paths import find_session_record


def main(payload_text: str) -> int:
    """原本が一意に読める会話だけへ、要約生成時の指示を出す。"""
    payload = json.loads(payload_text)
    if not isinstance(payload, dict) or payload.get("agent_id") is not None:
        return 0
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return 0
    record = find_session_record(session_id)
    if record is None or record.engine != "claude" or len(record.paths) != 1 or not record.paths[0].is_file():
        return 0
    # PreCompactのstdoutはモデルへの要約指示であり、要約API呼出し自体を省く契約ではない。
    print(
        "圧縮後は原本記録から復元できるため、要約は次の復元案内を中心に短くせよ。"
        "要約にはSessionStartの復元通知に従う旨を残せ。"
        "未保存の判断・要求・承認・未回収の実行識別子がある場合は、その必要情報を省かず残せ。"
    )
    return 0
