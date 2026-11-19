"""集計結果をJSONで標準出力へ書くCLI。"""

import json
import sys

import report  # ty: ignore[import-not-found]  # 模擬リポジトリは同じディレクトリのモジュールを実行時のsys.pathで解決する


def main() -> int:
    """標準入力の各行を集計し、JSONを1行出力する。"""
    print(json.dumps(report.build_report(sys.stdin.read().splitlines())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
