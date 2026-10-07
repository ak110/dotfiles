"""`atk run-script session-review-evidence`の`--user-events`を保存した後に、発話ごとの記録位置と冒頭を表示する。

逐語引用の出所として保存ファイルをWI投入担当へ渡す呼び出し元は、引用する発話を選ぶために記録位置と本文の冒頭だけを要する。
保存先を開いて長いJSON行の末尾から`record`と`line`を取り出す追加の呼び出しを不要にするため、保存後の表示でこれらを返す。
"""

import json
import pathlib

_HEAD_LENGTH = 60
"""本文の冒頭として表示する文字数。発話を選ぶ手掛かりに足り、1件1行に収まる長さとする。"""


def summarize_saved_user_events(path: pathlib.Path) -> None:
    """保存ファイルの`kind`が`user`の行ごとに、記録位置と本文の冒頭を保存ファイルと同じ順で1行ずつ表示する。

    記録位置は`record=<record> line=<line>`で示す。`--claude-session-id`で照会した`record`は
    `claude:<セッションID>`のようにコロンを含み、`<record>:<line>`では区切りと区別できないためである。
    `user`の行が無い場合は`発話: 0件`を表示し、保存ファイルを開かずに件数を判別できるようにする。
    """
    lines: list[str] = []
    with path.open(encoding="utf-8", newline="") as stream:
        for raw in stream:
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("kind") != "user":
                continue
            text = event.get("text")
            lines.append(
                f"発話: record={event.get('record')} line={event.get('line')} {_head(text if isinstance(text, str) else '')}"
            )
    if not lines:
        print("発話: 0件")
        return
    for line in lines:
        print(line)


def _head(text: str) -> str:
    """改行を空白へ置き換えた先頭の文字列を返し、切り詰めた場合は末尾へ`…`を付ける。"""
    flattened = " ".join(text.splitlines())
    if len(flattened) <= _HEAD_LENGTH:
        return flattened
    return flattened[:_HEAD_LENGTH] + "…"
