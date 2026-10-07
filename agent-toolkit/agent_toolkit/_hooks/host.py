"""hookの入力を送ったホスト（Claude CodeかCodexか）を判定する。

各イベントのエントリーポイントは本モジュールの`is_codex_payload`だけでホストを判定し、判定の規則を1か所に保つ。
"""


def is_codex_payload(payload: object) -> bool:
    """hookの入力がCodexから届いたかを返す。

    Codexはターン単位のhook入力へ非空文字列の`turn_id`を付加する。
    CodexのUserPromptSubmitは発話（`prompt`）とともにモデル名（`model`）も渡し、
    Claude CodeのUserPromptSubmitは`model`を渡さない。このため`prompt`と`model`を併せ持つ入力もCodexと判定する。
    ツール名の推測は判定に使わない。
    """
    if not isinstance(payload, dict):
        return False
    turn_id = payload.get("turn_id")
    if isinstance(turn_id, str) and turn_id:
        return True
    return "prompt" in payload and "model" in payload
