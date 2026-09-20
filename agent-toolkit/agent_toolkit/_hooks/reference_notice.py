"""ユーザー入力の解釈規範を再読させる共通通知。"""

from agent_toolkit._hooks import plugin_resources

REFERENCE_NOTICE_TAG = "notice"
REFERENCE_NOTICE_BODY = (
    "当該発話へ応答する前に、"
    f"{plugin_resources.skill_reference('confirmation-and-uwi', 'references/user-utterance.md')}"
    "を全文読み、同書の各項を当該発話へ適用する。"
)
