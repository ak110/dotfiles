"""進捗記録からAWIごとの現在の実装commitをJSON Linesで取得する。"""

import sys

import append_progress_log


def main(argv: list[str] | None = None) -> int:
    """既存の記録・Git実体・対象集合の同じ判定を公開の取得コマンドから使う。"""
    return append_progress_log.main(["--get-commits", *(sys.argv[1:] if argv is None else argv)], description=__doc__)


if __name__ == "__main__":
    raise SystemExit(main())
