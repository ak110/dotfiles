"""HTTPの応答を分類し、案内の設定を返す。"""


def classify(status):
    """状態コードを案内の分類名へ対応付ける。"""
    if status in (401, 403):
        return "auth_failed"
    return "ok"


def help_lines():
    """ヘルプに表示する行を返す。"""
    return ["login: 認証情報を更新する"]


def settings():
    """案内の表示箇所、適用範囲、権限照会の1日あたりの上限回数を返す。"""
    return {"place": "inline", "scope": "current", "daily_lookup_limit": 0}
