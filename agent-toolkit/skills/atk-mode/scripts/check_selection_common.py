"""選定検査で共有する入力エラーと診断定数。"""

from __future__ import annotations

import typing

from agent_toolkit._common import next_action as _next_action
from agent_toolkit._plan import selection as _selection

_LANE_NONE = "なし"


_IMPLEMENTATION_SECONDS_KEY = "実装秒数"


_INTEGRATION_SECONDS_KEY = "統合秒数"


_LEGACY_SECONDS_KEYS = {"implementation_seconds": _IMPLEMENTATION_SECONDS_KEY, "integration_seconds": _INTEGRATION_SECONDS_KEY}


_FIX_PATHS = "位置引数へメインが保存した選定結果YAMLの絶対パスを、`--work-dir`へ対象リポジトリの絶対パスを渡して再実行する"


_FIX_PRIVATE_NOTES = "`atk config get private_notes`が返す場所が実在し読み取れることを確かめてから、同じコマンドを再実行する"


_FIX_CONTENT = "選定結果の該当する欄を`selection-format.md`「選定結果の書式」の欄名と型へ直してから、同じコマンドを再実行する"


class InputError(_next_action.ActionableError):
    """チェックを開始できない入力の問題。送出側が原因に合う次の操作を持つ。"""


# 終了コード1の違反は理由と次の操作の組で生成し、`main`は出力に現れた違反の次の操作だけを1回ずつ出力する。
# 次の操作を違反の生成箇所に持たせると、違反の種類を加えたときも次の操作を欠いた違反を生成できない。
Violation = _next_action.ActionableError


def _is_integrated(decision: dict[str, object], integrated_lanes: set[str]) -> bool:
    """項目の`統合状態`か所属レーンの統合状態から、作業を統合済みの項目かを返す。"""
    return (
        decision.get(_selection.INTEGRATION_STATE_KEY) == _selection.INTEGRATED
        or decision[_selection.LANE_KEY] in integrated_lanes
    )


def _string_list(decision: dict[str, object], key: str) -> list[str]:
    """`load_selection`で型を確かめた項目の列の欄を返す。行の不在は空列として扱う。"""
    return typing.cast(list[str], decision.get(key, []))
