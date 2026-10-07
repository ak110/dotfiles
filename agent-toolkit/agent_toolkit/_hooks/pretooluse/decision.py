"""PreToolUseの判定1件の結果。

各判定は結果をこの値で返し、標準出力・標準エラーと終了コードへの変換はPreToolUseのエントリーポイント（`dispatch.main`）だけが行う。
判定が出力を直接書くと、エントリーポイントが保留している通知（応答言語の警告など）との結合や順序を判定ごとに再現することになる。
"""

import dataclasses


@dataclasses.dataclass(frozen=True)
class Decision:
    """PreToolUseの判定1件の結果。

    `block`は遮断の本文で、エントリーポイントが標準エラーへ書き終了コード2で終える。
    `context`は`hookSpecificOutput.additionalContext`へ渡す本文（判定の警告など）である。
    `notices`はエントリーポイントが保留している通知の末尾へ加える本文で、保留の通知と1つの警告群として結合する。
    `allow`は権限の確認を省いて実行を許可する（`permissionDecision: allow`）ことを示す。
    """

    block: str | None = None
    context: str | None = None
    notices: tuple[str, ...] = ()
    allow: bool = False
