"""対応を要する機械生成メッセージの理由と次の操作を整形する共通モジュール。

異常、復旧や判断を要する状態へ、理由と具体的な次の操作を組で渡す。
明示オプションどおりの正常な処理選択は呼出元の既知情報であり、この案内の対象から外れる。
正常結果は呼出元の結果出力へまとめ、値や構造化データと同じ内容の案内を重ねない。
本モジュールの例外型と出力関数は理由と次の操作を別々の必須の引数として受け取り、欠いた呼び出しを失敗させる。
層の順序で最も前にある`_common`へ置き、hook、`atk`、agents_server、`wait_ci.py`およびスキル付属スクリプトの
全ての呼び出し元が同じ標識を使い、次の操作を必須とする。文面は各発生源が持つ。
"""

import sys
from typing import TextIO

NEXT_ACTION_PREFIX = "次の操作: "
"""次の操作を表す行の標識。受信側はこの標識で始まる行を次の行動として読む。"""


def recover_from_failure(*, component: str, evidence: str, first_step: str, retry: str) -> str:
    return (
        f"{component}の証拠（{evidence}）を保全し、{first_step}。原因を確かめる。"
        "不具合なら `agent-basics:bugfix` を起動する。認可の範囲で回復する。"
        "配布元の作業ツリーなら配布物の不具合も同じ作業で直し、元の方法で確かめる。"
        "配布先でも認可済みの更新・設定修復・再接続で回復できるなら行う。"
        f"{retry}。"
        "回復結果を伝える。回復できない分だけ、影響・試した手段・再開の条件を委譲元またはユーザーへ返す。"
    )


def next_action_line(next_action: str) -> str:
    if not next_action.strip():
        raise ValueError("次の操作は空文字列以外で指定する必要がある")
    return f"{NEXT_ACTION_PREFIX}{next_action}"


def with_next_action(reason: str, next_action: str) -> str:
    return f"{reason}\n{next_action_line(next_action)}"


class ActionableError(ValueError):
    def __init__(self, reason: str, *, next_action: str) -> None:

        next_action_line(next_action)
        super().__init__(reason)
        self.reason = reason
        self.next_action = next_action

    @property
    def message(self) -> str:
        return with_next_action(self.reason, self.next_action)


def report(reason: str, *, next_action: str, stream: TextIO | None = None) -> None:
    print(with_next_action(reason, next_action), file=stream if stream is not None else sys.stderr)
