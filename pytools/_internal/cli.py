"""pytoolsコマンドライン群で共有するロギング等のCLI補助ユーティリティ。"""

import logging


def setup_logging(verbose: bool = False, *, fmt: str = "%(message)s") -> None:
    """pytools配下のCLIエントリで共通利用するロガー設定。

    `verbose=True`のときログレベルを`DEBUG`に、既定では`INFO`にする。
    書式は既定で`"%(message)s"`だが、`fmt`で上書きできる(例: `"%(levelname)s: %(message)s"`)。
    """
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format=fmt)
