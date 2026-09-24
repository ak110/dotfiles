"""自動生成する本文へXML形式の配送境界を付与する共通モジュール。

本リポジトリが生成してコーディングエージェントの会話文脈又はsystem promptへ入る本文は、
生成主体、種別及び配送単位を属性に持つXML要素で囲む。本文に開始タグが現れても、
最初の開始タグと最後の同名終了タグから配送境界を確定できる。

hookの通知、常駐処理が渡す追加指示、agent間配送、機械が生成してユーザー入力欄へ入る本文の
いずれもこの関数を経由する。層の順序で最も前にある`_common`へ置くことで、後続の層のどの経路からも
同じ包装を1つの実装で適用できる。
"""

from collections.abc import Mapping
from xml.sax.saxutils import quoteattr

AUTO_INSERTED_ELEMENT = "agent-toolkit-auto-inserted"
FORWARDED_USER_INPUT_ELEMENT = "forwarded-user-input"
"""自動生成本文の内側で、ユーザー自身が入力した範囲を囲む要素。

受信側はこの要素の内側だけをユーザーの発話として扱う。配送経路ごとに別の綴りを持つと、
受信側の判定が経路ごとに分かれるため、要素名を本モジュールで1つに保つ。
"""


def xml_message(element: str, body: str, attributes: Mapping[str, str]) -> str:
    """自動生成メッセージへXML配送境界を付与する。"""
    serialized = "".join(f" {name}={quoteattr(value)}" for name, value in attributes.items())
    return f"<{element}{serialized}>\n{body}\n</{element}>"


def auto_message(body: str, *, source: str, kind: str, attributes: Mapping[str, str] | None = None) -> str:
    """自動挿入本文へ共通の境界と出所を付ける。"""
    return xml_message(AUTO_INSERTED_ELEMENT, body, {**(attributes or {}), "source": source, "kind": kind})
