"""計画または引き継ぎ記録のMarkdownを入力し、AWIごとの現在の実装commitを短縮OIDのJSON Linesで取得する。

位置引数はMarkdownであり、同じstemのJSONLは対応データの保存先である。
対象AWIを`--awi`、対象worktreeの絶対パスを`--worktree`で指定する。どちらも取得には必須。
引き継ぎ記録では`--handoff`と、その記録の対象AWI全件の`--allowed-awi`も渡す。
取得は記録を変更せず、`--completed-step`と`--result`は不要である。
"""

import sys

import append_progress_log


def main(argv: list[str] | None = None) -> int:
    """既存の記録・Git実体・対象集合の同じ判定を公開の取得コマンドから使う。"""
    return append_progress_log.main(["--get-commits", *(sys.argv[1:] if argv is None else argv)], description=__doc__)


if __name__ == "__main__":
    raise SystemExit(main())
