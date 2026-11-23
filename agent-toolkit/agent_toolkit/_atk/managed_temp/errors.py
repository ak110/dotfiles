"""managed-tempの操作で、ユーザーが入力または実行環境を修正できる失敗を表す例外。"""

from __future__ import annotations

from agent_toolkit._common.next_action import recover_from_failure


class ManagedTempError(Exception):
    """ユーザーが入力または実行環境を修正できる検証エラー。

    `str()`は理由だけを返し、`next_action`に次の操作を持つ。送出箇所は原因の分類に応じた次の操作を渡す。
    次の操作を渡さない送出箇所では、原因の除去と報告先を示す共通の案内を使う。
    """

    DEFAULT_NEXT_ACTION = recover_from_failure(
        component="一時領域",
        evidence="診断と対象パス",
        first_step="表示された原因とパスの状態を確認する",
        retry="原因を除去し、再実行が安全な場合だけ再試行する",
    )
    PERMISSION_NEXT_ACTION = (
        "表示されたパスの所有者と権限を確認する。現在のOSアカウントが所有するディレクトリは必要な権限0700を補う。"
        "エージェント由来の残存物は、由来・現在の利用・保存すべき成果・削除対象の同一性を確認する。"
        "所有UIDだけで不要性や削除の認可を判断しない。通常の掃引では可能な対象だけを回収する。"
        "アクセスできない領域は、通常権限での確認結果と残った理由をユーザーへ伝える"
    )
    REPLACED_NEXT_ACTION = recover_from_failure(
        component="一時領域の置換",
        evidence="診断と対象パス",
        first_step="別の主体が同じパスを操作していないか確かめる",
        retry="所有者を確認し、別主体の変更と競合しなくなってから再実行する",
    )

    def __init__(self, message: str, *, next_action: str | None = None) -> None:
        super().__init__(message)
        self.next_action = next_action or self.DEFAULT_NEXT_ACTION


class ManagedTempPermissionError(ManagedTempError):
    pass
