"""自動生成する本文へXML形式の配送境界を付与する共通モジュール。

本リポジトリが生成してコーディングエージェントの会話文脈又はsystem promptへ入る本文は、
生成主体、種別及び配送単位を属性に持つXML要素で囲む。本文に開始タグが現れても、
nonceと最後の終了タグから配送境界を確定できる。

hookの通知、常駐処理が渡す追加指示、agent間配送、機械が生成してユーザー入力欄へ入る本文の
いずれもこの関数を経由する。層の順序で最も前にある`_common`へ置くことで、後続の層のどの経路からも
同じ包装を1つの実装で適用できる。
"""

import secrets
from collections.abc import Mapping
from xml.sax.saxutils import quoteattr


def xml_message(element: str, body: str, attributes: Mapping[str, str]) -> str:
    """自動生成メッセージへnonce付きXML配送境界を付与する。"""
    nonce = secrets.token_hex(8)
    while nonce in body:
        nonce = secrets.token_hex(8)
    values = {**attributes, "nonce": nonce}
    serialized = "".join(f" {name}={quoteattr(value)}" for name, value in values.items())
    return f"<{element}{serialized}>\n{body}\n</{element}>"
