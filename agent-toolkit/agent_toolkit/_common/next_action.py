"""機械生成メッセージの理由と次の操作を1つの形へそろえる共通モジュール。

コーディングエージェントへ届くメッセージが事実だけを返すと、受信側は次の行動を推測で選び、
実行できない操作や同じ失敗を繰り返す。次の操作を任意の本文にするとレビューでも欠落を検出できないため、
本モジュールの例外型と出力関数は理由と次の操作を別々の必須の引数として受け取り、欠いた呼び出しを失敗させる。
層の順序で最も前にある`_common`へ置き、hook、`atk`、agents_server、`wait_ci.py`およびスキル付属スクリプトの
全ての呼び出し元が同じ標識を使い、次の操作を必須とする。文面は各発生源が持つ。
"""

import sys
from typing import TextIO

NEXT_ACTION_PREFIX = "次の操作: "
"""次の操作を表す行の標識。受信側はこの標識で始まる行を次の行動として読む。"""


def next_action_line(next_action: str) -> str:
    """次の操作を標識付きの行へ整形する。空の次の操作は受け付けない。"""
    if not next_action.strip():
        raise ValueError("次の操作は空文字列以外で指定する必要がある")
    return f"{NEXT_ACTION_PREFIX}{next_action}"


def with_next_action(reason: str, next_action: str) -> str:
    """理由の後へ次の操作の行を続けた本文を返す。"""
    return f"{reason}\n{next_action_line(next_action)}"


class ActionableError(ValueError):
    """理由と次の操作を持つ入力・状態のエラー。

    `str()`は理由だけを返し、理由の文字列を使う既存の応答（Web APIの応答本文など）を変えない。
    受信側へ通知する処理は`message`から理由と次の操作の2行を得る。
    """

    def __init__(self, reason: str, *, next_action: str) -> None:
        # 送出の時点で空の次の操作を拒否し、受信側へ届く前に欠落を顕在化させる。
        next_action_line(next_action)
        super().__init__(reason)
        self.reason = reason
        self.next_action = next_action

    @property
    def message(self) -> str:
        """理由と次の操作の2行を返す。"""
        return with_next_action(self.reason, self.next_action)


def report(reason: str, *, next_action: str, stream: TextIO | None = None) -> None:
    """理由と次の操作の行を出力する。出力先を指定しなければ標準エラーへ出力する。"""
    print(with_next_action(reason, next_action), file=stream if stream is not None else sys.stderr)
