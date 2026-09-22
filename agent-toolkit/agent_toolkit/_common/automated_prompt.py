"""機械が生成してユーザー入力欄へ入る本文の境界標識。

常駐処理が子セッションの最初の入力として渡す本文は、ホストからはユーザーの発話と同じ
入力欄へ届く。受領したエージェントがユーザー自身の発話と区別できるよう、生成側が本要素で囲み、
消費側は同じ要素の有無だけで判定する。生成側と消費側が別の判定を持つと、両者の集合がずれる。

本要素で囲んだ本文は、ユーザー発話の解釈規範を再読させる注記の対象から外れる。
"""

from agent_toolkit._common import message_format

ELEMENT = "automated-prompt"
SOURCE_PROCESS_LOOP = "agent-toolkit/process-loop"
KIND_GOAL = "goal"
KIND_AVAILABILITY_PROBE = "availability-probe"

_OPENING_TAG = f"<{ELEMENT}"


def wrap(body: str, *, source: str, kind: str) -> str:
    """本文へ境界標識を付ける。"""
    return message_format.xml_message(ELEMENT, body, {"source": source, "kind": kind})


def contains(prompt: str) -> bool:
    """受領した入力が機械生成の本文を含むかを返す。

    スラッシュコマンドはホストが1行目の先頭でだけ解釈するため、包装は引数の位置へ置く。
    このため判定は先頭一致ではなく1行目に開始タグが現れるかで行う。
    """
    return _OPENING_TAG in prompt.split("\n", 1)[0]
