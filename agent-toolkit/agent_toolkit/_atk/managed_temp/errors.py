"""managed-tempの操作で、ユーザーが入力または実行環境を修正できる失敗を表す例外。"""

from __future__ import annotations


class ManagedTempError(Exception):
    """ユーザーが入力または実行環境を修正できる検証エラー。

    `str()`は理由だけを返し、`next_action`に次の操作を持つ。送出箇所が原因の分類に応じた次の操作を渡し、
    渡さない送出箇所には状態の確認と報告先を示す次の操作を共通の案内として使う。
    """

    DEFAULT_NEXT_ACTION = (
        "`atk managed-temp list`で管理対象の状態を確認し、表示された原因を除去して再実行する。"
        "解消しない場合はユーザーへ報告する"
    )
    PERMISSION_NEXT_ACTION = (
        "表示されたパスの所有者が実行中のOSアカウントで、権限がディレクトリは0700・ファイルは0600であることを確認する。"
        "自分で直せない場合はユーザーへ報告する"
    )
    REPLACED_NEXT_ACTION = "同じ操作を再実行する。繰り返す場合は別の主体が同じパスを操作していないか確認し、ユーザーへ報告する"
    RETRYABLE_NEXT_ACTION = "表示された原因を除去した後に、同じ`atk managed-temp cleanup`を再実行する"
    UNSUPPORTED_NEXT_ACTION = "この実行環境では操作できない。対応するplatformで実行するか、ユーザーへ報告する"

    def __init__(self, message: str, *, next_action: str | None = None) -> None:
        super().__init__(message)
        self.next_action = next_action or self.DEFAULT_NEXT_ACTION
