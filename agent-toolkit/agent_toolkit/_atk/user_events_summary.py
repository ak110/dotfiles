"""`atk run-script session-review-evidence`の保存後に、発話・一致ごとの記録位置と冒頭を表示する。

逐語引用の出所として保存ファイルをWI投入担当へ渡す呼び出し元は、引用する発話を選ぶために記録位置と本文の冒頭だけを要する。
保存先を開いて長いJSON行の末尾から`record`と`line`を取り出す追加の呼び出しを不要にするため、保存後の表示でこれらを返す。
"""

import collections
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


_MATCH_LIST_LIMIT = 20
"""保存後に1件1行で示す一致の上限。

保存は大きな結果を直接配送しないために行うため、一致の件数に比例する一覧は保存の目的を失わせる。
多数の一致を全件表示した結果、保存した全量と同程度の量を配送した実例がある。
選択の手掛かりには件数の内訳と先頭の一致で足り、残りは保存ファイルから選ぶ。
"""

_RECORD_COUNT_LIMIT = 10
"""記録ごとの件数を件数順に示す記録数の上限。残りの記録は件数の合計だけを示す。"""


def summarize_saved_matches(path: pathlib.Path) -> None:
    """検索一致とツール呼び出しの件数と内訳、保存順の先頭の一致を、保存ファイルから選べる要約にする。"""
    count = 0
    empty = False
    shown: list[str] = []
    kinds: collections.Counter[str] = collections.Counter()
    records: collections.Counter[str] = collections.Counter()
    with path.open(encoding="utf-8", newline="") as stream:
        for raw in stream:
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("kind") in ("summary", "tool-call-summary") and event.get("count") == 0:
                empty = True
            if event.get("kind") not in ("match", "tool-call"):
                continue
            count += 1
            kinds[" ".join(f"{key}={event[key]}" for key in ("kind", "role", "tool") if key in event)] += 1
            records[str(event.get("record"))] += 1
            if len(shown) < _MATCH_LIST_LIMIT:
                fields = "".join(f" {key}={event[key]}" for key in ("role", "tool") if key in event)
                text = event.get("text")
                shown.append(
                    f"一致: record={event.get('record')} line={event.get('line')} kind={event['kind']}{fields} "
                    f"{_head(text if isinstance(text, str) else '', length=160)}"
                )
    if not count:
        if empty:
            print("一致: 0件")
        return
    if count > _MATCH_LIST_LIMIT:
        print(f"一致: 全{count}件（保存順の先頭{len(shown)}件を表示）")
        print("内訳: " + "、".join(f"{key} {value}件" for key, value in kinds.most_common()))
        top = records.most_common(_RECORD_COUNT_LIMIT)
        rest = count - sum(value for _key, value in top)
        others = f"、他{len(records) - len(top)}記録 {rest}件" if rest else ""
        print("記録別: " + "、".join(f"{key} {value}件" for key, value in top) + others)
    for line in shown:
        print(line)
    if count > _MATCH_LIST_LIMIT:
        print(
            f"残り{count - len(shown)}件は保存先のJSONL（1行1イベント）にある。"
            f"次の操作: `jq -c 'select(.record == \"<record>\")' -- {path}`のように保存先から記録・kindを限定して読むか、"
            "照会の条件を狭めて再実行する"
        )


def _head(text: str, *, length: int = _HEAD_LENGTH) -> str:
    """改行を空白へ置き換えた先頭の文字列を返し、切り詰めた場合は末尾へ`…`を付ける。"""
    flattened = " ".join(text.splitlines())
    if len(flattened) <= length:
        return flattened
    return flattened[:length] + "…"
