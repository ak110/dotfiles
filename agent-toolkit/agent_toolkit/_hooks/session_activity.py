"""活動更新だけを通知するホストイベントを受け取る。"""

from agent_toolkit._common.session_activity import main as record_event


def main(payload_text: str) -> int:
    return record_event(payload_text)
