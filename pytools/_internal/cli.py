"""pytoolsコマンドライン群で共有するロギング等のCLI補助ユーティリティ。"""

import argparse
import logging


def setup_logging(verbose: bool = False, *, fmt: str = "%(message)s") -> None:
    """pytools配下のCLIエントリで共通利用するロガー設定。

    `verbose=True`のときログレベルを`DEBUG`に、既定では`INFO`にする。
    書式は既定で`"%(message)s"`だが、`fmt`で上書きできる(例: `"%(levelname)s: %(message)s"`)。
    """
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format=fmt)


def enable_completion(parser: argparse.ArgumentParser) -> None:
    """argcompleteによるシェル補完を有効化する。

    対応するコマンドのソース先頭に`# PYTHON_ARGCOMPLETE_OK`マーカーを置き、
    `parser.parse_args()`の直前で呼び出す。
    `_COMPLETE`系の環境変数が設定されているシェル補完起動時のみargcompleteが介入し、
    通常実行ではほぼno-opになる。`argcomplete`未導入環境でもImportErrorを握りつぶしてno-op化し、
    開発時にextrasを欠いたままでも壊れないようにする。
    補完スクリプトの実体は`completions/_pytools.bash`で、将来的に
    `argcomplete`から別実装（shtab等）へ切り替える際は本関数のみ差し替える想定。
    """
    try:
        import argcomplete  # noqa: PLC0415  # 補完起動時のみ必要なので遅延importする。
    except ImportError:
        return
    argcomplete.autocomplete(parser)
