"""managed-tempの操作で、ユーザーが入力または実行環境を修正できる失敗を表す例外。"""

from __future__ import annotations


class ManagedTempError(Exception):
    """ユーザーが入力または実行環境を修正できる検証エラー。

    `str()`は理由だけを返し、`next_action`に次の操作を持つ。送出箇所は原因の分類に応じた次の操作を渡す。
    次の操作を渡さない送出箇所では、原因の除去と報告先を示す共通の案内を使う。
    """

    DEFAULT_NEXT_ACTION = "表示された原因を除去して再実行する。解消しない場合はユーザーへ報告する"
    PERMISSION_NEXT_ACTION = (
        "表示されたパスの所有者と権限を確認する。現在のOSアカウントが所有するディレクトリは必要な権限0700を補う。"
        "エージェント由来の残存物は、由来・現在の利用・保存すべき成果・削除対象の同一性を確認する。"
        "所有UIDだけで不要性や削除の認可を判断しない。不要と確定し削除の認可がある対象は、"
        "利用可能なら非対話のsudo -nでエージェントが回収し、不在を確認して同じ回収操作を再実行する。"
        "sudoの未導入、非対話実行の失敗、実行環境の権限拒否、本人にしかできない操作などを実際に確認した場合だけ、"
        "実施不能の理由と必要な入力・認可をユーザーへ報告し、本人の操作が必要な範囲だけ依頼する"
    )
    REPLACED_NEXT_ACTION = "同じ操作を再実行する。繰り返す場合は別の主体が同じパスを操作していないか確認し、ユーザーへ報告する"

    def __init__(self, message: str, *, next_action: str | None = None) -> None:
        super().__init__(message)
        self.next_action = next_action or self.DEFAULT_NEXT_ACTION
