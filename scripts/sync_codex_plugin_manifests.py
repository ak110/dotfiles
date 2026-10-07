"""Agent Plugins・Codex向け派生JSONを同期または検査する生成器の入口。

`scripts/sync_generated_files.py`がプロジェクト環境で起動する。
生成と検査の処理は`pytools/_internal/codex_plugin_manifests.py`が持ち、本ファイルは引数の解釈と終了コードだけを持つ。
`--check`は派生JSONを変更せず、差が無ければ終了コード0、差があれば差の相対パスと種類を標準エラーへ出力して1を返す。
"""

import argparse
import sys

from pytools._internal import codex_plugin_manifests


def main(argv: list[str] | None = None) -> int:
    """通常同期または非変更検査を実行する。"""
    parser = argparse.ArgumentParser(description="Agent Plugins・Codex向け派生JSONを同期する。")
    parser.add_argument("--check", action="store_true", help="派生JSONを変更せず整合性だけを検査する")
    args = parser.parse_args(argv)
    if args.check:
        diagnostics = codex_plugin_manifests.check_diagnostics(codex_plugin_manifests.REPO_ROOT)
        for diagnostic in diagnostics:
            print(diagnostic, file=sys.stderr)
        return 1 if diagnostics else 0
    codex_plugin_manifests.sync(codex_plugin_manifests.REPO_ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
