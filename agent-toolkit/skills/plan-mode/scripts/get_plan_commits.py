"""計画または引き継ぎ記録のMarkdownを入力し、AWIごとの現在の実装commitを短縮OIDのJSON Linesで取得する。

位置引数はMarkdownであり、同じstemのJSONLは対応データの保存先である。
対象worktreeの絶対パスを必須の`--worktree`で指定する。
`--awi`は省略でき、省略時は計画の関連WI全件（引き継ぎ記録では`--allowed-awi`全件）を取得する。
引き継ぎ記録では`--handoff`と、その記録の対象AWI全件の`--allowed-awi`も渡す。
取得は記録を変更せず、`--completed-step`と`--result`は不要である。
"""

import sys

import append_progress_log


def main(argv: list[str] | None = None) -> int:
    """`plan-progress --get-commits`と同じ処理で、既存の記録・Git実体・対象集合を判定して対応を取得する。"""
    return append_progress_log.main(sys.argv[1:] if argv is None else argv, description=__doc__, retrieval=True)


if __name__ == "__main__":
    raise SystemExit(main())
