"""pytools CLIエントリ共通のロギング等の補助ユーティリティ。"""

import argparse
import logging


def setup_logging(verbose: bool = False, *, fmt: str = "%(message)s") -> None:
    """pytools配下のCLIエントリ共通のロガー設定。

    `verbose=True`のときログレベルを`DEBUG`に、それ以外は`INFO`にする。
    書式は既定で`"%(message)s"`だが、`fmt`で上書きできる（例: `"%(levelname)s: %(message)s"`）。
    """
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format=fmt)


def enable_completion(parser: argparse.ArgumentParser) -> None:
    """argcompleteによるシェル補完を有効化する。

    対応するコマンドのソース先頭に`# PYTHON_ARGCOMPLETE_OK`マーカーを置き、
    `parser.parse_args()`の直前で呼び出す。
    `_COMPLETE`系の環境変数が設定されているシェル補完起動時のみargcompleteが介入し、
    通常実行ではno-opとなる。`argcomplete`未導入環境でもImportErrorを無視してno-op化するため、
    extrasを欠いた状態でも動作できる。
    補完スクリプトの実体は`completions/_pytools.bash`で、将来的に
    `argcomplete`から別実装（shtab等）へ切り替える際は本関数のみ差し替える。
    """
    try:
        import argcomplete  # noqa: PLC0415  # pylint: disable=import-outside-toplevel  # 補完起動時のみ必要なので遅延importする。
    except ImportError:
        return
    argcomplete.autocomplete(parser)
