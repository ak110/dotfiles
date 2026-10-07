"""自動生成する本文へXML形式の配送境界を付与する共通モジュール。

本リポジトリが生成してコーディングエージェントの会話文脈またはsystem promptへ入る本文は、
生成主体、種別および配送単位を属性に持つXML要素で囲む。本文に開始タグが現れても、
最初の開始タグと最後の同名終了タグから配送境界を確定できる。

hookの通知、process-loopが渡す追加指示、agent間配送、機械が生成してユーザー入力欄へ入る本文の
いずれもこの関数を経由する。層の順序で最も前にある`_common`へ置くことで、後続の層のどの呼び出し元からも
同じ包装を1つの実装で適用できる。
"""

import re
from collections.abc import Mapping
from xml.sax.saxutils import quoteattr

AUTO_INSERTED_ELEMENT = "atk-auto"
LEGACY_AUTO_INSERTED_ELEMENT = "agent-toolkit-auto-inserted"
LEGACY_HOOK_MESSAGE_ELEMENT = "agent-toolkit-hook-message"
AUTO_ELEMENTS = (AUTO_INSERTED_ELEMENT, LEGACY_AUTO_INSERTED_ELEMENT, LEGACY_HOOK_MESSAGE_ELEMENT)
"""自動挿入の本文として認識する要素名。生成は現行の`atk-auto`だけを使い、旧形式は過去の記録と会話の認識に残す。"""
AUTO_ELEMENT_NAME_PATTERN = "|".join(re.escape(element) for element in AUTO_ELEMENTS)
"""`AUTO_ELEMENTS`のいずれかの要素名に一致する正規表現（グループを持たない選択）。"""
FORWARDED_USER_INPUT_ELEMENT = "forwarded-user-input"
"""自動生成本文の内側で、ユーザー自身が入力した範囲を囲む要素。

受信側はこの要素の内側だけをユーザーの発話として扱う。配送する手段ごとに別の綴りを持つと、
受信側の判定が配送する手段によって分かれるため、要素名を本モジュールで1つに保つ。
"""


def opening_tag(element: str, attributes: Mapping[str, str]) -> str:
    """XML配送境界の開始タグを返す。"""
    serialized = "".join(f" {name}={quoteattr(value)}" for name, value in attributes.items())
    return f"<{element}{serialized}>"


def xml_message(element: str, body: str, attributes: Mapping[str, str]) -> str:
    """自動生成メッセージへXML配送境界を付与する。"""
    return f"{opening_tag(element, attributes)}\n{body}\n</{element}>"


def starts_with_auto_element(text: str) -> bool:
    """本文が先頭の空白を除いて自動挿入の要素（`AUTO_ELEMENTS`）の開始タグで始まるかを返す。"""
    return text.lstrip().startswith(tuple(f"<{element}" for element in AUTO_ELEMENTS))


def auto_message(body: str, *, source: str, kind: str, attributes: Mapping[str, str] | None = None) -> str:
    """自動挿入本文へ共通の境界と出所を付ける。"""
    extras = {name: value for name, value in (attributes or {}).items() if name not in {"source", "kind"}}
    return xml_message(AUTO_INSERTED_ELEMENT, body, {"source": source, "kind": kind, **extras})
