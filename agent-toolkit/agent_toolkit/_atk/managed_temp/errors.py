"""managed-tempの操作で、ユーザーが入力または実行環境を修正できる失敗を表す例外。"""

from __future__ import annotations


class ManagedTempError(Exception):
    """ユーザーが入力または実行環境を修正できる検証エラー。

    `str()`は理由だけを返し、`next_action`に次の操作を持つ。送出箇所は原因の分類に応じた次の操作を渡す。
    次の操作を渡さない送出箇所では、原因の除去と報告先を示す共通の案内を使う。
    """

    DEFAULT_NEXT_ACTION = "表示された原因を除去して再実行する。解消しない場合はユーザーへ報告する"
    PERMISSION_NEXT_ACTION = (
        "表示されたパスの所有者が実行中のOSアカウントで、権限がディレクトリは0700であることを確認する。"
        "自分で直せない場合はユーザーへ報告する"
    )
    REPLACED_NEXT_ACTION = "同じ操作を再実行する。繰り返す場合は別の主体が同じパスを操作していないか確認し、ユーザーへ報告する"

    def __init__(self, message: str, *, next_action: str | None = None) -> None:
        super().__init__(message)
        self.next_action = next_action or self.DEFAULT_NEXT_ACTION
